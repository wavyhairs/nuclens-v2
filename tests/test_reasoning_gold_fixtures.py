import json
import unittest
from collections import Counter
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
    def test_identity_candidates_keep_human_gold_separate_from_pending_cases(self):
        payload = json.loads((FIXTURES / "identity_candidates.json").read_text(encoding="utf-8"))
        cases = payload["cases"]
        self.assertGreaterEqual(len(cases), 120)
        self.assertLessEqual(len(cases), 160)
        labelled = [case for case in cases if case["label_status"] == "HUMAN_LABELLED"]
        pending = [case for case in cases if case["label_status"] == "HUMAN_LABEL_REQUIRED"]
        self.assertEqual(labelled, cases[:60])
        self.assertEqual(pending, cases[60:])
        self.assertTrue(all(case["human_label"] in payload["label_contract"]
                            and case["reason_code"] in payload["reason_codes"]
                            for case in labelled))
        self.assertTrue(all(case["human_label"] is None and case["reason_code"] is None
                            for case in pending))
        self.assertTrue(all(case[side].get("source_hash") and case[side].get("url")
                            and case[side].get("published_at")
                            for case in cases for side in ("a", "b")))
        edges = {case["metadata"].get("known_edge_case") for case in cases}
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

    def test_curation_labels_carry_their_provenance(self):
        payload = json.loads((FIXTURES / "curation_gold.json").read_text(encoding="utf-8"))
        cases = payload["cases"]
        self.assertGreaterEqual(len(cases), 30)
        self.assertLessEqual(len(cases), 50)
        pending = [case for case in cases
                   if case["label_status"] == "HUMAN_LABEL_REQUIRED"]
        self.assertTrue(all(case["human_label"] is None for case in pending))
        self.assertEqual(len(pending), 15)
        self.assertEqual(
            Counter(case["label_status"] for case in cases),
            {"HUMAN_REVIEWED_AI_ASSISTED": 24, "HUMAN_LABEL_REQUIRED": 15,
             "USER_SPECIFIED": 1})

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
        self.assertEqual(len(pending), 31)
        self.assertEqual(
            Counter(case["label_status"] for case in cases),
            {"HUMAN_REVIEWED_AI_ASSISTED": 41, "HUMAN_LABEL_REQUIRED": 31,
             "USER_SPECIFIED": 5})
        self.assertTrue(all(case["human_label"] is None for case in pending))
        self.assertTrue(all(case["human_error_types"] is None for case in pending))

    def test_ambiguous_human_labelled_status_never_returns_to_curation_or_semantic(self):
        """`HUMAN_LABELLED` 는 이 두 fixture 에서 출처를 말해 주지 않는다.

        이 라벨들은 Sol 의 판정·확신도·근거를 케이스와 **동시에** 보여 주고 한 키로
        승인하게 한 UI 를 거쳤다. 그것을 독립 판정과 같은 이름으로 부르면 reasoning
        비교가 실제로는 "Gemini 가 Sol 과 얼마나 같은가"를 재게 된다.
        """
        for name in ("curation_gold.json", "semantic_gold.json"):
            with self.subTest(fixture=name):
                cases = json.loads(
                    (FIXTURES / name).read_text(encoding="utf-8"))["cases"]
                self.assertNotIn("HUMAN_LABELLED",
                                 {case["label_status"] for case in cases})

    def test_identity_labels_stay_independent(self):
        """Identity 라벨러는 모델 판정을 기본 숨김으로 두고 케이스마다 되접는다.

        Curation/Semantic 리뷰 UI 와 질이 다르므로 같이 강등하지 않는다. 다만
        참고 패널 자체는 존재하므로 '열어 볼 수 있었다'는 여지는 남는다.
        """
        cases = json.loads((FIXTURES / "identity_candidates.json")
                           .read_text(encoding="utf-8"))["cases"]
        labelled = {case["label_status"] for case in cases
                    if case["human_label"] is not None}
        self.assertEqual(labelled, {"HUMAN_LABELLED"})

    def test_candidate_audit_passes_without_warnings(self):
        report = audit(FIXTURES)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["warnings"], [])


if __name__ == "__main__":
    unittest.main()
