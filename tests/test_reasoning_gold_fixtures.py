import json
import unittest
from pathlib import Path

from tools.generate_reasoning_gold_candidates import (
    ERROR_TYPES,
    _preserve_human_fields,
    build_identity_candidates,
)
from tools.validate_reasoning_gold import REQUIRED_EDGES, audit

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
        self.assertTrue(all(case[side].get("source_hash") and case[side].get("url")
                            and case[side].get("published_at")
                            for case in payload["cases"] for side in ("a", "b")))
        edges = {case["metadata"].get("known_edge_case") for case in payload["cases"]}
        self.assertTrue(REQUIRED_EDGES <= edges)

    def test_candidate_generation_never_promotes_cached_verdict(self):
        cache = {"reviews": {"a--b": {"embedding_similarity": 0.85,
                                        "same_event": True, "left_title": "A",
                                        "right_title": "B", "reason": "old"}}}
        case = build_identity_candidates(cache, per_stratum=1)[0]
        self.assertIsNone(case["human_label"])
        self.assertTrue(case["prior_llm_verdict_not_gold"])

    def test_regeneration_only_preserves_explicit_human_labels(self):
        blank = {"human_label": None, "label_status": "HUMAN_LABEL_REQUIRED"}
        suspicious = {"human_label": "MERGE", "label_status": "HUMAN_LABEL_REQUIRED"}
        labelled = {"human_label": "SEPARATE", "label_status": "HUMAN_LABELLED"}
        self.assertIsNone(_preserve_human_fields(
            dict(blank), suspicious, ("human_label", "label_status"))["human_label"])
        self.assertEqual(_preserve_human_fields(
            dict(blank), labelled, ("human_label", "label_status"))["human_label"],
                         "SEPARATE")

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

    def test_curation_queue_is_broad_and_mostly_unlabelled(self):
        payload = json.loads((FIXTURES / "curation_gold.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(payload["cases"]), 30)
        self.assertLessEqual(len(payload["cases"]), 50)
        pending = [case for case in payload["cases"]
                   if case["label_status"] == "HUMAN_LABEL_REQUIRED"]
        self.assertTrue(all(case["human_label"] is None for case in pending))
        self.assertEqual(len(pending), len(payload["cases"]) - 1)

    def test_semantic_queue_is_balanced_and_covers_taxonomy(self):
        payload = json.loads((FIXTURES / "semantic_gold.json").read_text(encoding="utf-8"))
        cases = payload["cases"]
        self.assertGreaterEqual(len(cases), 50)
        self.assertLessEqual(len(cases), 100)
        for kind in ("source_aligned", "controlled_perturbation", "unsupported_inference"):
            self.assertEqual(sum(case.get("candidate_kind") == kind for case in cases), 24)
        focuses = {case.get("selection_metadata_not_gold", {}).get("review_focus")
                   for case in cases}
        self.assertTrue(set(ERROR_TYPES) <= focuses)
        pending = [case for case in cases
                   if case["label_status"] == "HUMAN_LABEL_REQUIRED"]
        self.assertEqual(len(pending), 72)
        self.assertTrue(all(case["human_label"] is None for case in pending))
        self.assertTrue(all(case["human_error_types"] is None for case in pending))

    def test_candidate_audit_passes_without_warnings(self):
        report = audit(FIXTURES)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["warnings"], [])


if __name__ == "__main__":
    unittest.main()
