import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# VS Code's Run Python File starts with this test directory on sys.path.
# Add the repository root so direct execution and module execution both work.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.playbook.playbook_keeper import (
    _proposal_identity, _rules_diff, propose_candidate, validate_proposal,
)


class PlaybookKeeperTests(unittest.TestCase):
    def test_validates_candidate_payload(self):
        proposal = validate_proposal({
            "rules": {"minimum_z_score": 1.5},
            "diff": {"minimum_z_score": {"from": 2.0, "to": 1.5}},
            "rationale": "Held-out recall improved in supplied runs.",
        })
        self.assertEqual(proposal["rules"]["minimum_z_score"], 1.5)

    def test_allows_no_change_proposal_for_no_candidate_path(self):
        proposal = validate_proposal({"rules": {"x": 1}, "diff": {}, "rationale": "No evidence supports a change"})
        self.assertEqual(proposal["diff"], {})

    def test_rejects_non_object_rules(self):
        with self.assertRaisesRegex(RuntimeError, "rules"):
            validate_proposal({"rules": [], "diff": {"x": 1}, "rationale": "Change"})

    def test_candidate_identity_is_stable_and_base_scoped(self):
        version, key = _proposal_identity("v1", ["eval-b", "eval-a"])
        self.assertEqual((version, key), _proposal_identity("v1", ["eval-a", "eval-b"]))
        self.assertNotEqual(version, _proposal_identity("v2", ["eval-a", "eval-b"])[0])


class Response:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.table_name = db, table
        self.filters = {}
        self.row = None
        self.operation = "select"
        self.limit_count = None

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        self.limit_count = count
        return self

    def insert(self, row):
        self.operation, self.row = "insert", row
        return self

    def execute(self):
        rows = self.db.tables[self.table_name]
        if self.operation == "insert":
            if self.table_name == "playbook" and any(r["version"] == self.row["version"] for r in rows):
                raise RuntimeError("duplicate playbook version")
            if self.table_name == "events" and any(
                r.get("idempotency_key") == self.row.get("idempotency_key") for r in rows
            ):
                raise RuntimeError("duplicate idempotency key")
            saved = dict(self.row)
            saved["playbook_id" if self.table_name == "playbook" else "event_id"] = f"{self.table_name}-{len(rows) + 1}"
            rows.append(saved)
            return Response([saved])
        matched = [row for row in rows if all(row.get(key) == value for key, value in self.filters.items())]
        return Response(matched[:self.limit_count] if self.limit_count is not None else matched)


class FakeDB:
    def __init__(self):
        self.tables = {
            "playbook": [{
                "playbook_id": "live-1", "version": "v1", "status": "live",
                "rules": {"minimum_z_score": 2.0, "filters": {"min_rv": 1000}},
            }],
            "eval_runs": [{
                "eval_run_id": "eval-1", "version": "v1", "dataset_snapshot": "real-voa-2026-a",
                "split": "heldout", "status": "completed", "lift_at_50": 0.2,
                "pack_pass_rate": 0.8, "advocate_accuracy": 0.75,
                "metrics": {"n": 25}, "property_records": [{"uarn": "must-not-be-sent"}],
            }],
            "events": [],
        }

    def table(self, name):
        return FakeQuery(self, name)


class PlaybookKeeperFlowTests(unittest.TestCase):
    def test_diff_is_derived_from_rules(self):
        self.assertEqual(
            _rules_diff({"a": 1, "nested": {"x": 2}}, {"a": 1.5, "nested": {"x": 2}, "new": True}),
            {"a": {"from": 1, "to": 1.5}, "new": {"to": True}},
        )
        self.assertIsNone(_rules_diff({"a": 1}, {"a": 1}))

    def test_persists_only_candidate_and_aggregate_evidence(self):
        db = FakeDB()
        captured = {}
        proposal = {
            "rules": {"minimum_z_score": 1.8, "filters": {"min_rv": 1000}},
            "diff": {"fabricated": "model claim"},
            "rationale": "Held-out aggregate metrics support a small threshold adjustment.",
        }

        def model(_anthropic, context):
            captured.update(context)
            return proposal, 35, "0.001000"

        with patch("agents.playbook.playbook_keeper._clients", return_value=(object(), db)):
            with patch("agents.playbook.playbook_keeper._model_proposal", side_effect=model):
                result = propose_candidate()

        self.assertEqual(result["status"], "candidate_created")
        self.assertEqual(db.tables["playbook"][-1]["status"], "candidate")
        self.assertEqual(db.tables["playbook"][-1]["eval_run_id"], None)
        self.assertEqual(db.tables["playbook"][-1]["diff"], {"minimum_z_score": {"from": 2.0, "to": 1.8}})
        self.assertEqual(db.tables["events"][-1]["payload"]["evaluation_ids"], ["eval-1"])
        self.assertNotIn("property_records", str(captured))

    def test_retry_returns_prior_event_without_calling_model(self):
        db = FakeDB()
        proposal = {
            "rules": {"minimum_z_score": 1.8, "filters": {"min_rv": 1000}},
            "diff": {}, "rationale": "Supported by aggregate held-out metrics.",
        }
        with patch("agents.playbook.playbook_keeper._clients", return_value=(object(), db)):
            with patch("agents.playbook.playbook_keeper._model_proposal", return_value=(proposal, 1, None)):
                first = propose_candidate()
            with patch("agents.playbook.playbook_keeper._model_proposal") as model:
                second = propose_candidate()
        self.assertEqual(first["status"], "candidate_created")
        self.assertEqual(second["status"], "already_proposed")
        model.assert_not_called()

    def test_no_change_rules_do_not_create_candidate(self):
        db = FakeDB()
        proposal = {
            "rules": db.tables["playbook"][0]["rules"],
            "diff": {"minimum_z_score": {"from": 2.0, "to": 1.0}},
            "rationale": "No actual rule change.",
        }
        with patch("agents.playbook.playbook_keeper._clients", return_value=(object(), db)):
            with patch("agents.playbook.playbook_keeper._model_proposal", return_value=(proposal, 1, None)):
                result = propose_candidate()
        self.assertEqual(result["status"], "no_candidate")
        self.assertEqual(len(db.tables["playbook"]), 1)
        self.assertEqual(db.tables["events"], [])


if __name__ == "__main__":
    unittest.main()
