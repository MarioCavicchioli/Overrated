"""One retry on a dropped Supabase connection. A fake client, no network."""

from __future__ import annotations

import httpx
import pytest
from postgrest.exceptions import APIError

from orchestrator.supabase_store import SupabaseStore

DROP = httpx.RemoteProtocolError("Server disconnected")
DUPLICATE = APIError({"code": "23505", "message": "duplicate key value", "details": None,
                      "hint": None})
OTHER = APIError({"code": "23514", "message": "check constraint", "details": None, "hint": None})


class FakeQuery:
    """Any chain of PostgREST builder calls; execute() plays the next scripted outcome."""

    def __init__(self, script: list, calls: list):
        self.script, self.calls = script, calls

    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        self.calls.append(1)
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return type("Resp", (), {"data": outcome, "count": len(outcome)})()


def store(script: list) -> tuple[SupabaseStore, list]:
    calls: list = []
    client = type("Client", (), {"table": lambda self, name: FakeQuery(script, calls)})()
    return SupabaseStore(client=client), calls


def test_select_retried_once_after_a_drop():
    s, calls = store([DROP, [{"uarn": "1"}]])
    assert s._select("candidates", [("uarn", "eq", "1")]) == [{"uarn": "1"}]
    assert len(calls) == 2


def test_update_and_count_retried():
    s, calls = store([DROP, [], DROP, [{"x": 1}, {"x": 2}]])
    s._update("packs", {"status": "killed"}, [("pack_id", "eq", "p")])
    assert s._count("events", [("kind", "eq", "k")]) == 2
    assert len(calls) == 4


def test_insert_that_landed_before_the_drop_is_not_an_error():
    """The retry hits our own UUID key: the first attempt succeeded."""
    s, calls = store([DROP, DUPLICATE])
    s._insert("events", {"event_id": "e1", "kind": "k"})
    assert len(calls) == 2


def test_insert_retry_with_another_error_raises():
    s, _ = store([DROP, OTHER])
    with pytest.raises(APIError):
        s._insert("events", {"event_id": "e1"})


def test_only_one_retry():
    s, calls = store([DROP, DROP, [{"uarn": "1"}]])
    with pytest.raises(httpx.RemoteProtocolError):
        s._select("candidates")
    assert len(calls) == 2


def test_other_errors_are_not_retried():
    s, calls = store([OTHER, [{"uarn": "1"}]])
    with pytest.raises(APIError):
        s._select("candidates")
    s2, calls2 = store([DUPLICATE])           # a duplicate on the FIRST try is a real error
    with pytest.raises(APIError):
        s2._insert("events", {"event_id": "e1"})
    assert len(calls) == 1 and len(calls2) == 1
