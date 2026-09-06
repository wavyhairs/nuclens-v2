import json
import unittest
from pathlib import Path

from tools.generate_reasoning_gold_candidates import build_identity_candidates

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "gemini_reasoning"


class ReasoningGoldFixtureTests(unittest.TestCase):
    def test_identity_candidates_require_human_labels(self):
        payload = json.loads((FIXTURES / "identity_candidates.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(payload["cases"]), 120)
        self.assertLessEqual(len(payload["cases"]), 160)
        self.assertTrue(all(case["human_label"] is None for case in payload["cases"]))
        self.assertTrue(all(case["label_status"] == "HUMAN_LABEL_REQUIRED"
                            for case in payload["cases"]))

    def test_candidate_generation_never_promotes_cached_verdict(self):
        cache = {"reviews": {"a--b": {"embedding_similarity": 0.85,
                                        "same_event": True, "left_title": "A",
                                        "right_title": "B", "reason": "old"}}}
        case = build_identity_candidates(cache, per_stratum=1)[0]
        self.assertIsNone(case["human_label"])
        self.assertTrue(case["prior_llm_verdict_not_gold"])

    def test_saeul_contract_separates_chronology_and_scope(self):
        payload = json.loads((FIXTURES / "saeul_contract.json").read_text(encoding="utf-8"))
        by_id = {case["id"]: case for case in payload["cases"]}
        self.assertEqual(by_id["saeul-false-causality"]["human_label"], "BLOCK")
        self.assertEqual(set(by_id["saeul-false-causality"]["error_types"]),
                         {"CAUSALITY_ERROR", "TEMPORAL_ERROR"})
        self.assertEqual(by_id["saeul-separate-events"]["human_label"], "PASS")
        self.assertEqual(by_id["saeul-unit-scope-4"]["human_label"], "BLOCK")

    def test_real_curation_regression_is_pinned(self):
        payload = json.loads((FIXTURES / "curation_gold.json").read_text(encoding="utf-8"))
        case = payload["cases"][0]
        self.assertEqual(case["source_hash"], "4da5b7ab6c225c78")
        self.assertIn("자동정지 및 사업기간 연장", case["generated_title"])
        self.assertEqual(case["human_label"], "REPAIR")


if __name__ == "__main__":
    unittest.main()
