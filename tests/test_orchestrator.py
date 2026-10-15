"""Orchestrator: watcher pipeline, owner approval, session loop, Scout routing. No API calls."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from orchestrator import app as app_module
from orchestrator.cases import RISK_LINE, create_case
from orchestrator.pipeline import Pipeline
from orchestrator.scout_tools import ScoutToolRouter
from orchestrator.sessions import run_session
from orchestrator.store import SQLiteStore


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "t.db", tmp_path / "pdfs")


def add_pack(store, case_id="c_0001", uarn="1001008"):
    store.upsert_candidate({"uarn": uarn, "address": "137 Mare Street", "rv": 30000,
                            "status": "pack_ready"})
    pack = {"case_id": case_id, "uarn": uarn}
    store.insert_pack({"case_id": case_id, "uarn": uarn, "pack": pack, "comparables": [],
                       "proposed_rv": 1, "annual_saving": 1, "argument": "x" * 50,
                       "version": "builder-v1"})


def verdict(store, case_id, v, reasons=None):
    store.insert_verdict({"case_id": case_id, "verdict": v, "version": "advocate-test",
                          "reasons": reasons or [{"reason": "ok", "voa_record": "1001009"}]})


def kinds(store):
    return [e["kind"] for e in store.list_events(200)]


# -- pipeline --------------------------------------------------------------
def test_new_pack_starts_advocate_and_survive_opens_case(store):
    add_pack(store)
    calls = []
    p = Pipeline(store, run_advocate=lambda cid: (calls.append(cid), verdict(store, cid, "survive")),
                 advocate_available=True)
    started = p.tick()
    assert started == ["c_0001"]
    p.judge("c_0001")
    assert calls == ["c_0001"]
    case = store.get_case("c_0001")
    assert case["stage"] == "waiting_owner" and case["owner_approved"] is False
    assert {"type": "risk", "text": RISK_LINE}.items() <= case["history"][1].items()
    assert store.get_candidate("1001008")["status"] == "waiting_owner"
    assert "case_opened" in kinds(store)
    p.apply_verdicts()   # idempotent
    assert kinds(store).count("case_opened") == 1


def test_kill_sets_candidate_killed_with_reasons(store):
    add_pack(store)
    verdict(store, "c_0001", "kill", [
        {"reason": "Comparable 1001011 at £445 was left out", "voa_record": "1001011"}])
    Pipeline(store, advocate_available=True).apply_verdicts()
    assert store.get_candidate("1001008")["status"] == "killed"
    killed = [e for e in store.list_events() if e["kind"] == "killed"]
    assert len(killed) == 1 and "[1001011]" in killed[0]["message"]
    assert store.get_case("c_0001") is None


def test_stub_advocate_leaves_packs_waiting(store):
    add_pack(store)
    p = Pipeline(store, advocate_available=False)
    assert p.tick() == [] and p.tick() == []
    assert kinds(store) == ["advocate_unavailable"]


def test_failing_advocate_retried_three_times(store):
    add_pack(store)

    def boom(cid):
        raise RuntimeError("session failed")

    p = Pipeline(store, run_advocate=boom, advocate_available=True)
    for _ in range(5):
        for cid in p.tick():
            p.judge(cid)
            p.in_flight.discard(cid)
    assert kinds(store).count("advocate_started") == 3
    assert kinds(store).count("advocate_failed") == 3


def test_concurrency_cap(store):
    for i in range(1, 6):
        add_pack(store, f"c_000{i}", f"100100{i}")
    p = Pipeline(store, advocate_available=True, max_concurrent=2)
    assert len(p.tick()) == 2
    assert p.tick() == []          # both still in flight


# -- owner approval --------------------------------------------------------
@pytest.fixture
def api(store, monkeypatch):
    monkeypatch.setenv("WATCH_DISABLED", "1")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "test-token")
    monkeypatch.setattr(app_module, "get_store", lambda: store)
    with TestClient(app_module.app) as client:
        yield client


AUTH = {"Authorization": "Bearer test-token"}


def test_approve_requires_token(api, store):
    add_pack(store)
    create_case(store, "c_0001", "1001008")
    r = api.post("/cases/c_0001/approve", json={"account_reference": "A1",
                                                "facts_confirmed": True})
    assert r.status_code == 401


def test_approve_requires_authority_and_confirmation(api, store):
    add_pack(store)
    create_case(store, "c_0001", "1001008")
    for body in ({"facts_confirmed": True},
                 {"account_reference": "   ", "facts_confirmed": True},
                 {"account_reference": "4471-2290-11"},
                 {"account_reference": "4471-2290-11", "facts_confirmed": False}):
        r = api.post("/cases/c_0001/approve", json=body, headers=AUTH)
        assert r.status_code == 422, body
    assert store.get_case("c_0001")["stage"] == "waiting_owner"


def test_approve_moves_case_to_check_draft(api, store):
    add_pack(store)
    create_case(store, "c_0001", "1001008")
    r = api.post("/cases/c_0001/approve", headers=AUTH,
                 json={"account_reference": "4471-2290-11", "facts_confirmed": True})
    assert r.status_code == 200
    body = r.json()
    assert body["owner_approved"] is True and body["stage"] == "check_draft"
    assert body["risk"] == RISK_LINE
    assert "4471-2290-11" not in str(body) and "ending 0-11" in str(body)
    assert "4471-2290-11" not in str(store.list_events())
    again = api.post("/cases/c_0001/approve", headers=AUTH,
                     json={"account_reference": "4471-2290-11", "facts_confirmed": True})
    assert again.status_code == 409


def test_get_case_shows_risk(api, store):
    add_pack(store)
    create_case(store, "c_0001", "1001008")
    assert api.get("/cases/c_0001", headers=AUTH).json()["risk"] == RISK_LINE
    assert api.get("/cases/c_9999", headers=AUTH).status_code == 404


# -- session loop ----------------------------------------------------------
class FakeClient:
    """Just enough of client.beta.sessions for run_session."""

    def __init__(self, events, usage):
        self.sent = []
        self._events, self._usage = events, usage
        outer = self

        class Events:
            def send(self, session_id, events):
                outer.sent.extend(events)

            @contextmanager
            def stream(self, session_id):
                yield iter(outer._events)

        class Sessions:
            events = Events()

            def create(self, **kw):
                outer.created = kw
                return NS(id="sesn_test")

            def retrieve(self, session_id):
                return NS(usage=outer._usage)

        self.beta = NS(sessions=Sessions())


def idle(reason):
    return NS(type="session.status_idle", stop_reason=NS(type=reason))


def test_run_session_answers_tools_denies_asks_and_logs_spend(store):
    usage = NS(input_tokens=8, output_tokens=339, cache_read_input_tokens=10791,
               cache_creation=NS(ephemeral_5m_input_tokens=734, ephemeral_1h_input_tokens=0),
               list_cost=NS(amount="1", currency="USD"))
    events = [
        NS(type="workflow_run.created", workflow_run_id="wrun_1"),
        NS(type="agent.custom_tool_use", id="sevt_1", name="get_valuation",
           input={"uarn": "1001008"}),
        NS(type="agent.tool_use", id="sevt_2", name="bash", evaluated_permission="ask"),
        idle("end_turn"),                       # a run is open: keep going
        NS(type="workflow_run.status_ended", workflow_run_id="wrun_1"),
        idle("end_turn"),
        NS(type="never.reached"),
    ]
    client = FakeClient(events, usage)
    result = run_session(client, store, agent="scout", agent_id="agent_x", environment_id="env_x",
                         title="scout test", model="claude-sonnet-5-5", message="go",
                         handle=lambda name, args: f"result for {args['uarn']}", verbose=False)
    assert client.created["budget"]["max_list_cost"] == {"amount": "200", "currency": "USD"}
    sent = {e["type"]: e for e in client.sent}
    assert sent["user.custom_tool_result"]["content"][0]["text"] == "result for 1001008"
    assert sent["user.tool_confirmation"]["result"] == "deny"
    assert result.stop_reason == "end_turn" and result.tokens == 11872
    spend = [e for e in store.list_events() if e["kind"] == "session_spend"]
    assert spend[0]["tokens"] == 11872 and "1 cents" in spend[0]["message"]


def test_run_session_stops_at_token_budget(store, monkeypatch):
    monkeypatch.setenv("SESSION_TOKEN_BUDGET", "1000")
    usage = NS(input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_creation=None,
               list_cost=NS(amount="0", currency="USD"))
    big = NS(input_tokens=900, output_tokens=200, cache_read_input_tokens=0,
             cache_creation_input_tokens=0)
    client = FakeClient([NS(type="span.model_request_end", model_usage=big), idle("end_turn")],
                        usage)
    result = run_session(client, store, agent="builder", agent_id="a", environment_id="e",
                         title="t", model="claude-sonnet-5-5", message="go", handle=None,
                         verbose=False)
    assert result.stop_reason.startswith("token budget reached")
    assert {"type": "user.interrupt"} in client.sent
    assert "budget_stop" in kinds(store)


# -- Scout routing ---------------------------------------------------------
def test_scout_router_sends_builder_tools_to_builder(store):
    calls = []
    router = ScoutToolRouter(store, data=None)
    router.builder.handle = lambda name, args: calls.append(name) or "ok"
    router.register("save_candidates", lambda name, args: "saved")
    assert router("submit_pack", {}) == "ok" and calls == ["submit_pack"]
    assert router("save_candidates", {}) == "saved"
    assert router("nope", {}).startswith("ERROR")
    with pytest.raises(ValueError):
        router.register("no_case", lambda n, a: "")
