"""Caseworker wake-ups and the nightly playbook trigger. No API calls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from orchestrator import agent_ports
from orchestrator.schedules import Scheduler
from orchestrator.store import SQLiteStore

NOON = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(tmp_path / "t.db", tmp_path / "pdfs")


def open_case(store, case_id, stage="check", deadline=None):
    uarn = f"U{case_id}"
    store.upsert_candidate({"uarn": uarn, "address": "a", "status": "survived"})
    store.insert_pack({"case_id": case_id, "uarn": uarn, "pack": {}, "comparables": [],
                       "proposed_rv": 1, "annual_saving": 1, "argument": "x" * 50,
                       "version": "t"})
    store.insert_case({"case_id": case_id, "uarn": uarn, "stage": stage,
                       "owner_approved": stage != "awaiting_owner", "history": [],
                       "next_deadline": deadline})


def sched(store, clock, calls, nightly_hour=24, **kw):
    """nightly_hour=24 keeps the playbook trigger out of the Caseworker tests."""
    return Scheduler(store, run_caseworker=calls.append,
                     run_playbook_keeper=lambda: calls.append("playbook"),
                     now=lambda: clock[0], daily_hour=7, nightly_hour=nightly_hour, **kw)


def run(jobs):
    for job in jobs:
        job()


def wakes(store):
    return [(e["kind"], e["case_id"]) for e in store.list_events(500)
            if e["kind"].startswith("caseworker_wake")]


def test_voa_reply_wakes_its_case_once(store):
    open_case(store, "c_0001")
    open_case(store, "c_0002")
    clock, calls = [NOON.replace(hour=1)], []          # before the daily sweep
    s = sched(store, clock, calls)
    store.add_event("voa", "voa_reply", "VOA asked for floor plans", case_id="c_0001")
    run(s.tick())
    assert calls == ["c_0001"]
    run(s.tick())
    assert calls == ["c_0001"]                         # not again
    restarted = sched(store, clock, calls)              # cursor read back from events
    run(restarted.tick())
    assert calls == ["c_0001"]


def test_reply_for_closed_or_unknown_case_is_skipped(store):
    open_case(store, "c_0001", stage="closed")
    clock, calls = [NOON.replace(hour=1)], []
    s = sched(store, clock, calls)
    store.add_event("voa", "voa_reply", "late reply", case_id="c_0001")
    store.add_event("voa", "voa_reply", "no case", case_id="c_9999")
    run(s.tick())
    assert calls == [] and s.cursor == store.last_event("voa_reply")["id"]


def test_deadline_within_three_days_wakes_once_a_day(store):
    soon = (NOON + timedelta(days=2)).isoformat()
    later = (NOON + timedelta(days=10)).isoformat()
    open_case(store, "c_0001", deadline=soon)
    open_case(store, "c_0002", deadline=later)
    clock, calls = [NOON.replace(hour=1)], []
    s = sched(store, clock, calls)
    run(s.tick())
    assert calls == ["c_0001"]
    clock[0] += timedelta(hours=5)                    # 06:00: still before the sweep
    run(s.tick())
    assert calls == ["c_0001"]
    clock[0] += timedelta(days=1)
    run(s.tick())
    assert calls.count("c_0001") == 2


def test_daily_sweep_wakes_open_cases_once(store):
    open_case(store, "c_0001")
    open_case(store, "c_0002", stage="awaiting_owner")   # not approved: not swept
    open_case(store, "c_0003", stage="won")
    clock, calls = [NOON.replace(hour=6)], []
    s = sched(store, clock, calls)
    run(s.tick())
    assert calls == []                                 # before 07:00
    clock[0] = NOON.replace(hour=7)
    run(s.tick())
    run(s.tick())
    assert calls == ["c_0001"]
    assert wakes(store) == [("caseworker_wake_daily", "c_0001")]


def test_reply_during_a_running_session_reruns_it(store):
    open_case(store, "c_0001")
    clock, calls = [NOON.replace(hour=1)], []
    s = sched(store, clock, calls)
    store.add_event("voa", "voa_reply", "first", case_id="c_0001")
    jobs = s.tick()                                     # session "running"
    store.add_event("voa", "voa_reply", "second", case_id="c_0001")
    assert s.tick() == []                               # recorded, not started twice
    run(jobs)
    assert calls == ["c_0001", "c_0001"]                # once more for the second reply
    assert len(wakes(store)) == 2 and not s.in_flight


def test_concurrency_cap_queues_wakes(store):
    for i in range(1, 4):
        open_case(store, f"c_000{i}")
    clock, calls = [NOON], []
    s = sched(store, clock, calls, max_concurrent=2)
    first = s.tick()
    assert len(first) == 2 and len(wakes(store)) == 3   # all recorded, two started
    run(first)
    run(s.tick())
    assert sorted(calls) == ["c_0001", "c_0002", "c_0003"]


def test_stub_caseworker_logs_unavailable(store):
    open_case(store, "c_0001")
    s = Scheduler(store, now=lambda: NOON, daily_hour=7, nightly_hour=2,
                  run_caseworker=agent_ports.run_caseworker if not agent_ports.CASEWORKER_AVAILABLE
                  else lambda cid: (_ for _ in ()).throw(agent_ports.AgentUnavailable()),
                  run_playbook_keeper=lambda: None)
    run(s.tick())
    kinds = [e["kind"] for e in store.list_events()]
    assert "caseworker_wake_daily" in kinds and "caseworker_unavailable" in kinds


def test_nightly_playbook_once_per_night(store):
    clock, calls = [NOON.replace(hour=1)], []
    s = sched(store, clock, calls, nightly_hour=2)
    run(s.tick())
    assert calls == []                                  # before 02:00
    clock[0] = NOON.replace(hour=2)
    run(s.tick())
    run(s.tick())
    assert calls == ["playbook"]
    clock[0] += timedelta(days=1)
    run(s.tick())
    assert calls == ["playbook", "playbook"]
