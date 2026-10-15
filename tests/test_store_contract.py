"""The same Store behaviour on every backend.

SQLite always runs. The Supabase run writes rows marked TEST- and a test PDF to
the database it points at, then deletes them, so it is opt-in: it runs only when
SUPABASE_URL and the service role key are set AND SUPABASE_TESTS=1.
"""

from __future__ import annotations

import os
import uuid

import pytest

from orchestrator.config import load_env
from orchestrator.store import SQLiteStore

load_env()
RUN_SUPABASE = bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY")
                    and os.getenv("SUPABASE_TESTS") == "1")


@pytest.fixture(params=["sqlite", pytest.param("supabase", marks=pytest.mark.skipif(
    not RUN_SUPABASE, reason="writes to Supabase: needs SUPABASE_URL, the service role key "
                             "and SUPABASE_TESTS=1"))])
def env(request, tmp_path):
    tag = uuid.uuid4().hex[:8]
    ids = {"uarn": f"TEST-{tag}", "case_id": f"c_{9_000_000 + int(tag[:5], 16) % 999_999}",
           "kind": f"test_{tag}"}
    if request.param == "sqlite":
        yield SQLiteStore(tmp_path / "t.db", tmp_path / "pdfs"), ids
        return
    from orchestrator.supabase_store import PDF_BUCKET, SupabaseStore
    store = SupabaseStore()
    try:
        yield store, ids
    finally:   # children first, for the foreign keys
        sb = store.sb
        sb.table("events").delete().eq("kind", ids["kind"]).execute()
        sb.table("cases").delete().eq("case_id", ids["case_id"]).execute()
        sb.table("verdicts").delete().eq("case_id", ids["case_id"]).execute()
        sb.table("packs").delete().eq("case_id", ids["case_id"]).execute()
        sb.table("candidates").delete().eq("uarn", ids["uarn"]).execute()
        sb.storage.from_(PDF_BUCKET).remove([f"{ids['case_id']}.pdf"])


def seed(store, ids):
    store.upsert_candidate({"uarn": ids["uarn"], "address": "TEST address", "rv": 30000, "status": "pack_ready",
                            "flags": ["zone_a_above_street"]})
    store.insert_pack({"case_id": ids["case_id"], "uarn": ids["uarn"],
                       "pack": {"case_id": ids["case_id"]}, "comparables": [],
                       "proposed_rv": 1, "annual_saving": 1, "argument": "x" * 50,
                       "version": "test"})


def test_candidate_round_trip(env):
    store, ids = env
    seed(store, ids)
    store.set_candidate_status(ids["uarn"], "dismissed")
    c = store.get_candidate(ids["uarn"])
    assert c["status"] == "dismissed" and c["flags"] == ["zone_a_above_street"]
    with pytest.raises(ValueError):
        store.set_candidate_status(ids["uarn"], "bogus")


def test_pack_verdict_case_round_trip(env):
    store, ids = env
    seed(store, ids)
    cid = ids["case_id"]
    assert store.get_pack(cid)["pack"] == {"case_id": cid}
    assert cid in store.list_case_ids()
    assert store.latest_verdict(cid) is None
    # Live: one verdict per (pack, advocate_version), so a rerun needs a new version.
    store.insert_verdict({"case_id": cid, "verdict": "kill", "version": "t1",
                          "reasons": [{"reason": "a", "voa_record": "1"}]})
    with pytest.raises(Exception):
        store.insert_verdict({"case_id": cid, "verdict": "survive", "version": "t1",
                              "reasons": [{"reason": "b", "voa_record": "2"}]})
    store.insert_verdict({"case_id": cid, "verdict": "survive", "version": "t2",
                          "reasons": [{"reason": "b", "voa_record": "2"}]})
    assert store.latest_verdict(cid)["verdict"] == "survive"
    assert [v["version"] for v in store.list_verdicts(cid)] == ["t1", "t2"]

    store.insert_case({"case_id": cid, "uarn": ids["uarn"], "stage": "waiting_owner",
                       "owner_approved": False, "history": [{"type": "risk", "text": "r"}]})
    store.update_case(cid, {"stage": "check_draft", "owner_approved": True,
                            "owner_approved_at": "2026-10-15T12:00:00+00:00",
                            "next_deadline": "2026-10-20T09:00:00+00:00"})
    case = store.get_case(cid)
    assert case["stage"] == "check_draft" and case["owner_approved"] is True
    assert case["history"] == [{"type": "risk", "text": "r"}]
    assert cid in [c["case_id"] for c in store.list_cases(("check_draft",))]
    with pytest.raises(ValueError):
        store.update_case(cid, {"uarn": "x"})


def test_events(env):
    store, ids = env
    k, cid = ids["kind"], ids["case_id"]
    seed(store, ids)          # events link to a case by its uuid, so the pack must exist
    assert store.last_event(k) is None
    store.add_event("orchestrator", k, "one", case_id=cid, tokens=5)
    store.add_event("orchestrator", k, "two", case_id=cid)
    assert store.count_events(k, cid) == 2
    assert store.last_event(k, cid)["message"] == "two"
    first = store.events_since(k, None)
    assert [e["message"] for e in first] == ["one", "two"]
    assert first[0]["case_id"] == cid            # read back as the readable ref
    assert [e["message"] for e in store.events_since(k, first[1]["ts"])] == ["two"]


def test_pdf_saved(env):
    store, ids = env
    url = store.save_pdf(ids["case_id"], b"%PDF-1.4 test")
    assert ids["case_id"] in url


def test_next_case_id_sqlite(tmp_path):
    store = SQLiteStore(tmp_path / "t.db", tmp_path / "pdfs")
    assert store.next_case_id() == "c_0001"
    seed(store, {"uarn": "U1", "case_id": "c_0041"})
    assert store.next_case_id() == "c_0042"
