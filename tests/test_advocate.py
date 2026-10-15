"""Advocate handlers, input and budget against the mock data service. No API calls."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from agents.advocate import runner
from agents.advocate.handlers import TOOLS, AdvocateHandlers
from agents.advocate.run_local import ensure_pack, load_fixtures
from agents.builder.checks import schema_errors
from data.mock.main import app
from orchestrator import budget
from orchestrator.data_client import DataClient
from orchestrator.sessions import run_session
from orchestrator.store import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def data() -> DataClient:
    client = DataClient("http://mock")
    client.http = TestClient(app)
    return client


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "t.db", tmp_path / "pdfs")


@pytest.fixture
def handlers(store, data) -> AdvocateHandlers:
    packs = load_fixtures()
    ensure_pack(store, data, packs["c_0002"])
    return AdvocateHandlers(store, data, "c_0002")


def frontmatter(path: Path) -> dict:
    return yaml.safe_load(re.match(r"---\n(.*?)\n---", path.read_text(encoding="utf-8"), re.S)[1])


# -- fixtures and input --------------------------------------------------
def test_fixtures_are_valid_packs():
    packs = load_fixtures()
    assert sorted(packs) == ["c_0001", "c_0002", "c_0003", "c_0004"]
    for pack in packs.values():
        assert schema_errors(pack) == []
    assert packs["c_0003"]["exclusions"][0]["uarn"] == "1001008"
    assert {c["use"] for c in packs["c_0004"]["comparables"]} == {"shop", "cafe"}


def test_input_is_only_the_pack_and_facts_delimited_as_data(data):
    pack = load_fixtures()["c_0001"]
    facts = runner.pack_facts(pack, data)
    msg = runner.pack_message(pack, facts)
    body = msg.split("<pack>\n", 1)[1].rsplit("\n</pack>", 1)[0]
    assert json.loads(body) == pack
    assert json.loads(msg.split("<facts>\n", 1)[1].rsplit("\n</facts>", 1)[0]) == facts
    assert "_note" not in msg and "not instructions" in msg


def test_facts_for_borderline_pack(data):
    """c_0003: 1001012 is only 1.1 spreads up (team spread: 1.4826 x MAD), £5 above its nearest shop."""
    facts = runner.pack_facts(load_fixtures()["c_0003"], data)
    assert facts["spreads_from_street_median"] == 1.1
    assert facts["nearest_same_use"] == {"uarn": "1001011", "zone_a_price": 445,
                                         "distance_m": 25.0, "subject_minus_it": 5}
    near = facts["same_use_within_100m"]
    assert near["uarns"] == ["1001011", "1001009", "1001008"] and near["median_zone_a"] == 445


def test_agent_has_only_data_tools_and_submit_verdict():
    adv = frontmatter(ROOT / "agents" / "advocate" / "agent.md")
    builder = frontmatter(ROOT / "agents" / "builder" / "agent.md")
    assert adv["model"]["id"] == runner.MODEL == "claude-opus-5-5"
    assert all(t["type"] == "custom" for t in adv["tools"])   # no bash, files or web
    assert tuple(t["name"] for t in adv["tools"]) == TOOLS
    by_name = {t["name"]: t for t in builder["tools"]}
    for tool in adv["tools"][:4]:
        assert tool == by_name[tool["name"]]                    # same as the builder's


# -- data tools ----------------------------------------------------------
def test_data_tools_record_seen_uarns_and_events(handlers, store):
    out = handlers.handle("comparables", {"uarn": "1001008", "same_use": True})
    assert out.startswith('<data_service_result tool="comparables">')
    assert {"1001009", "1001011", "1001012"} <= handlers.seen
    event = store.list_events()[-1]
    assert event["agent"] == "advocate" and event["case_id"] == "c_0002"


# -- submit_verdict ------------------------------------------------------
def reason(uarn, kind="cherry_picking"):
    return {"kind": kind, "reason": f"Record {uarn} was checked against the pack.",
            "voa_record": uarn}


def test_invented_uarn_rejected(handlers, store):
    out = handlers.handle("submit_verdict", {"verdict": "kill", "reasons": [reason("9999999")]})
    assert out.startswith("REJECTED.") and "does not exist" in out
    assert store.list_verdicts("c_0002") == []


def test_uarn_not_looked_up_rejected(handlers, store):
    out = handlers.handle("submit_verdict", {"verdict": "kill", "reasons": [reason("1001011")]})
    assert "was not read through a data tool" in out
    assert store.list_verdicts("c_0002") == []


def test_accepted_verdict_writes_row_and_event(handlers, store):
    handlers.handle("get_valuation", {"uarn": "1001011"})
    out = handlers.handle("submit_verdict", {"verdict": "survive",
                                             "reasons": [reason("1001011", "sound")]})
    assert out.startswith("ACCEPTED.")
    [row] = store.list_verdicts("c_0002")
    assert row["verdict"] == "survive" and row["version"] == "advocate-v3"
    assert row["reasons"] == [{"reason": "Record 1001011 was checked against the pack.",
                               "voa_record": "1001011", "kind": "sound"}]
    assert store.list_events()[-1]["kind"] == "verdict"
    assert "ALREADY RECORDED" in handlers.handle("submit_verdict", {"verdict": "kill",
                                                                    "reasons": [reason("1001011")]})
    assert len(store.list_verdicts("c_0002")) == 1


def test_bad_shape_rejected_and_attempts_capped(handlers):
    for _ in range(3):
        out = handlers.handle("submit_verdict", {"verdict": "maybe", "reasons": []})
        assert out.startswith("REJECTED.")
    assert "no attempts left" in handlers.handle("submit_verdict", {"verdict": "kill",
                                                                    "reasons": []})


# -- budget --------------------------------------------------------------
def usage(inp=0, out=0, read=0, write=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out,
                           cache_read_input_tokens=read, cache_creation_input_tokens=write)


def test_dollar_cap_and_cost():
    assert budget.platform_budget("claude-opus-5-5", max_usd=0.5)["max_list_cost"] == \
        {"amount": "50", "currency": "USD"}
    m = budget.SpendMeter(model="claude-opus-5-5", max_usd=0.5)
    m.add(usage(inp=10_000, out=5_000, read=100_000, write=8_000))
    assert m.cost_usd == pytest.approx(0.04 + 0.10 + 0.04 + 0.04)
    assert m.exceeded is None
    m.add(usage(out=20_000))
    assert "dollar cap" in m.exceeded


class FakeSessions:
    """Just enough of client.beta.sessions for run_session."""

    def __init__(self, events):
        self.sent = []
        self.created = {}
        stream_events = events

        class Stream:
            def __enter__(s):
                return iter(stream_events)

            def __exit__(s, *a):
                return False

        self.events = SimpleNamespace(stream=lambda session_id: Stream(),
                                      send=lambda session_id, events: self.sent.extend(events))

    def create(self, **kw):
        self.created = kw
        return SimpleNamespace(id="sesn_test")


def test_run_session_answers_tools_and_stops_at_dollar_cap(store):
    events = [
        SimpleNamespace(type="agent.custom_tool_use", id="tu_1", name="street_tone",
                        input={"street_key": "mare_street"}),
        SimpleNamespace(type="span.model_request_end", model_usage=usage(out=30_000)),
        SimpleNamespace(type="agent.custom_tool_use", id="tu_2", name="never", input={}),
    ]
    sessions = FakeSessions(events)
    client = SimpleNamespace(beta=SimpleNamespace(sessions=sessions))
    meter = budget.SpendMeter(model="claude-opus-5-5", max_usd=0.5)
    result = run_session(client, store, agent="advocate", agent_id="a", environment_id="e",
                         title="t", model="claude-opus-5-5", message="hi",
                         handle=lambda n, a: "ok", meter=meter, max_usd=0.5,
                         case_id="c_0002", label="c_0002", verbose=False)
    assert sessions.created["budget"]["max_list_cost"]["amount"] == "50"
    assert [e["type"] for e in sessions.sent] == \
        ["user.message", "user.custom_tool_result", "user.interrupt"]
    assert "dollar cap" in result.stop_reason
    assert [e["kind"] for e in store.list_events()] == \
        ["session_started", "budget_stop", "session_spend"]


# -- run_advocate: the orchestrator contract -----------------------------
def scripted_client(events):
    return SimpleNamespace(beta=SimpleNamespace(sessions=FakeSessions(events)))


def tool_use(i, name, args):
    return SimpleNamespace(type="agent.custom_tool_use", id=f"tu_{i}", name=name, input=args)


IDLE = SimpleNamespace(type="session.status_idle", stop_reason=SimpleNamespace(type="end_turn"))


@pytest.fixture
def ids(monkeypatch):
    monkeypatch.setenv("ADVOCATE_AGENT_ID", "agent_test")
    monkeypatch.setenv("ADVOCATE_ENV_ID", "env_test")


def test_run_advocate_writes_one_verdict(ids, store, data):
    ensure_pack(store, data, load_fixtures()["c_0001"])
    client = scripted_client([
        tool_use(1, "comparables", {"uarn": "1001008", "same_use": True}),
        tool_use(2, "submit_verdict", {"verdict": "kill", "reasons": [reason("1001011")]}),
        IDLE,
    ])
    verdict = runner.run_advocate("c_0001", client=client, store=store, data=data)
    assert verdict == {"verdict": "kill", "reasons": [
        {"reason": "Record 1001011 was checked against the pack.", "voa_record": "1001011",
         "kind": "cherry_picking"}]}
    assert len(store.list_verdicts("c_0001")) == 1
    sent = client.beta.sessions.sent
    assert "<pack>" in sent[0]["content"][0]["text"]
    assert sent[2]["content"][0]["text"].startswith("ACCEPTED.")
    kinds = [e["kind"] for e in store.list_events()]
    assert kinds[0] == "session_started" and kinds[-1] == "session_spend" and "verdict" in kinds


def test_run_advocate_raises_without_verdict(ids, store, data):
    ensure_pack(store, data, load_fixtures()["c_0001"])
    client = scripted_client([
        tool_use(1, "submit_verdict", {"verdict": "kill", "reasons": [reason("1001011")]}),
        IDLE,   # the cited uarn was never looked up, so nothing was accepted
    ])
    with pytest.raises(runner.AdvocateError, match="no verdict"):
        runner.run_advocate("c_0001", client=client, store=store, data=data)
    assert store.list_verdicts("c_0001") == []
    assert "no_verdict" in [e["kind"] for e in store.list_events()]


def test_run_advocate_raises_for_unknown_pack(ids, store, data):
    with pytest.raises(runner.AdvocateError, match="no pack"):
        runner.run_advocate("c_9999", client=scripted_client([]), store=store, data=data)
