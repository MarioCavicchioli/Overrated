import unittest
from unittest.mock import patch

from agents.caseworker.caseworker import (
    _is_simulated,
    _validate_model_output,
    process_reply_event,
)


def make_result(stage):
    return {
        "draft_reply": "Draft for human review",
        "recommended_stage": stage,
        "next_action": "Review the response",
        "next_deadline": None,
        "rationale": "Based on the supplied evidence",
    }


class Response:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.name = db, table
        self.filters, self.operation, self.row = {}, "select", None

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def insert(self, row):
        self.operation, self.row = "insert", row
        return self

    def execute(self):
        if self.operation == "insert":
            saved = dict(self.row, event_id="output-event")
            self.db.events.append(saved)
            return Response([saved])
        if self.name == "events":
            matches = [row for row in self.db.events if all(row.get(k) == v for k, v in self.filters.items())]
            return Response(matches[:1])
        if self.name == "cases":
            return Response([self.db.case] if all(self.db.case.get(k) == v for k, v in self.filters.items()) else [])
        if self.name == "packs":
            return Response([self.db.pack] if all(self.db.pack.get(k) == v for k, v in self.filters.items()) else [])
        raise AssertionError(self.name)


class FakeDB:
    def __init__(self):
        self.case = {
            "case_id": "case-1", "uarn": "real-uarn", "stage": "waiting_check_decision",
            "owner_approved": True, "history": [], "next_deadline": None,
        }
        self.pack = {"case_id": "case-1", "pack": {"case_ref": "c_0001"}}
        self.events = [{
            "event_id": "input-event", "kind": "fake_voa_reply", "case_id": "case-1",
            "message": "test reply", "payload": {"simulated": True}, "ts": "2026-10-15T10:00:00Z",
        }]

    def table(self, name):
        return FakeQuery(self, name)


class CaseworkerSafetyTests(unittest.TestCase):
    def test_fake_event_is_always_simulated(self):
        self.assertTrue(_is_simulated("fake_voa_reply", {}))

    def test_invalid_reply_transition_escalates(self):
        parsed = _validate_model_output(make_result("resolved"), "waiting_check_decision", "reply")
        self.assertEqual(parsed["recommended_stage"], "needs_human")

    def test_deadline_cannot_advance_stage(self):
        parsed = _validate_model_output(make_result("challenge_draft"), "waiting_check_decision", "deadline")
        self.assertEqual(parsed["recommended_stage"], "needs_human")

    def test_deadline_review_accepts_active_submitted_stage(self):
        parsed = _validate_model_output(make_result("check_submitted"), "check_submitted", "deadline")
        self.assertEqual(parsed["recommended_stage"], "check_submitted")

    def test_naive_deadline_is_rejected(self):
        bad = make_result("waiting_check_decision")
        bad["next_deadline"] = "2026-10-16T09:00:00"
        with self.assertRaisesRegex(RuntimeError, "timezone"):
            _validate_model_output(bad, "waiting_check_decision", "reply")

    def test_fake_reply_emits_simulation_without_changing_case(self):
        db = FakeDB()
        model_output = (make_result("challenge_draft"), 20, "0.001000")
        with patch("agents.caseworker.caseworker._clients", return_value=(object(), db)):
            with patch("agents.caseworker.caseworker._agent_json", return_value=model_output):
                result = process_reply_event("input-event")
        self.assertEqual(result["status"], "simulated")
        self.assertEqual(db.case["stage"], "waiting_check_decision")
        self.assertEqual(db.case["history"], [])
        self.assertEqual(result["event"]["kind"], "caseworker_simulation_completed")
        self.assertFalse(result["event"]["payload"]["changes_case"])


if __name__ == "__main__":
    unittest.main()

