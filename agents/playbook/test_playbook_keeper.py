import unittest

from agents.playbook.playbook_keeper import _proposal_identity, validate_proposal


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


if __name__ == "__main__":
    unittest.main()
