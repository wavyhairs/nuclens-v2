"""Gold 의 출처 재분류가 **라벨을 건드리지 않는지**, 표본이 결과와 무관한지.

절대 원칙 하나가 여기 걸려 있다 — Human Gold 는 모델 결과에 맞춰 수정하지 않는다.
출처를 바로잡는 일이 라벨을 바꾸는 일로 새면 그 원칙이 이 커밋에서 깨진다.
"""

import copy
import hashlib
import json
import unittest
from pathlib import Path

from tools import gold_provenance

FIXTURES = Path("tests/fixtures/gemini_reasoning")


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ReclassifyTests(unittest.TestCase):
    def test_labels_are_never_touched(self):
        for name in ("curation_gold.json", "semantic_gold.json"):
            with self.subTest(fixture=name):
                before = _load(name)
                after, _changed = gold_provenance.reclassify(copy.deepcopy(before))
                self.assertEqual(
                    [c.get("human_label") for c in before["cases"]],
                    [c.get("human_label") for c in after["cases"]])

    def test_only_ui_reviewed_rows_are_downgraded(self):
        payload = {"cases": [
            {"id": "a", "human_label": "PASS", "label_status": "HUMAN_LABELLED"},
            {"id": "b", "human_label": "BLOCK", "label_status": "USER_SPECIFIED"},
            {"id": "c", "human_label": None, "label_status": "HUMAN_LABEL_REQUIRED"},
        ]}
        after, changed = gold_provenance.reclassify(payload)
        self.assertEqual(changed, 1)
        self.assertEqual([c["label_status"] for c in after["cases"]],
                         [gold_provenance.AI_ASSISTED, "USER_SPECIFIED",
                          "HUMAN_LABEL_REQUIRED"])

    def test_reclassified_payload_says_why(self):
        # 상태값만 바뀌면 나중에 왜 강등했는지 알 수 없다.
        after, _ = gold_provenance.reclassify(
            {"cases": [{"id": "a", "human_label": "PASS",
                        "label_status": "HUMAN_LABELLED"}]})
        self.assertIn("anchoring", after["provenance_note"])

    def test_reclassify_is_idempotent(self):
        payload = {"cases": [{"id": "a", "human_label": "PASS",
                              "label_status": "HUMAN_LABELLED"}]}
        first, changed_a = gold_provenance.reclassify(copy.deepcopy(payload))
        _second, changed_b = gold_provenance.reclassify(first)
        self.assertEqual((changed_a, changed_b), (1, 0))


class BlindSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payloads = {"curation": _load("curation_gold.json"),
                        "semantic": _load("semantic_gold.json")}

    def test_sample_covers_the_boundary_strata_at_the_planned_size(self):
        sample = gold_provenance.select_blind_sample(self.payloads)
        self.assertEqual(len(sample), 15)
        self.assertEqual(
            {(row["task"], row["stratum"]) for row in sample},
            {("semantic", "controlled_perturbation"), ("curation", "REPAIR"),
             ("curation", "PASS")})

    def test_selection_is_deterministic_and_independent_of_stored_labels(self):
        first = gold_provenance.select_blind_sample(self.payloads)
        # 라벨을 흔들어도 같은 표본이 나와야 한다 — 결과를 보고 표본을 바꿀 수 없게.
        shaken = copy.deepcopy(self.payloads)
        for payload in shaken.values():
            for case in payload["cases"]:
                if case.get("human_label") == "PASS":
                    case["human_notes"] = "노트를 바꿔도 선택은 그대로여야 한다"
        self.assertEqual(first, gold_provenance.select_blind_sample(shaken))

    def test_selection_uses_a_stable_hash_not_file_order(self):
        reordered = copy.deepcopy(self.payloads)
        for payload in reordered.values():
            payload["cases"] = list(reversed(payload["cases"]))
        self.assertEqual(gold_provenance.select_blind_sample(self.payloads),
                         gold_provenance.select_blind_sample(reordered))

    def test_only_anchored_or_already_blind_rows_are_eligible(self):
        """사용자가 직접 지정한 라벨에는 anchoring 이 없다 — 예산을 쓰면 낭비다.

        이미 blind 를 거친 행은 계속 포함한다. 표본은 **고정 집합**이어야 승격 뒤에도
        같은 패킷이 나오고 대조를 다시 돌릴 수 있다.
        """
        sample = gold_provenance.select_blind_sample(self.payloads)
        ids = {row["case_id"] for row in sample}
        for payload in self.payloads.values():
            for case in payload["cases"]:
                if case["id"] in ids:
                    self.assertIn(case["label_status"], gold_provenance.BLIND_ELIGIBLE)

    def test_the_sample_is_stable_across_promotion(self):
        # 승격은 상태값을 바꾼다. 그때 표본이 움직이면 대조가 재현되지 않는다.
        before = gold_provenance.select_blind_sample(self.payloads)
        promoted = copy.deepcopy(self.payloads)
        for payload in promoted.values():
            for case in payload["cases"]:
                if case["label_status"] == gold_provenance.AI_ASSISTED:
                    case["label_status"] = gold_provenance.BLIND_CONFIRMED
        self.assertEqual(before, gold_provenance.select_blind_sample(promoted))

    def test_a_thin_stratum_fails_loudly_instead_of_shrinking(self):
        thin = copy.deepcopy(self.payloads)
        for case in thin["curation"]["cases"]:
            if (case.get("superseded_label") or case.get("human_label")) == "PASS":
                case["label_status"] = "HUMAN_LABEL_REQUIRED"
        with self.assertRaises(ValueError):
            gold_provenance.select_blind_sample(thin)


class DirectionTests(unittest.TestCase):
    """일치율이 아니라 불일치의 **방향**이 anchoring 을 가른다."""

    PAYLOADS = {"curation": {"cases": [
        {"id": "a", "human_label": "PASS"}, {"id": "b", "human_label": "PASS"},
        {"id": "c", "human_label": "REPAIR"}]}}
    SAMPLE = [{"task": "curation", "stratum": "PASS", "case_id": cid}
              for cid in ("a", "b", "c")]

    def test_one_way_disagreement_is_read_as_anchoring(self):
        # blind 가 전부 더 강하게 개입 = 보관 라벨이 일관되게 관대했다.
        result = gold_provenance.compare(
            {"a": "REPAIR", "b": "BLOCK", "c": "REPAIR"}, self.PAYLOADS, self.SAMPLE)
        self.assertEqual(result["directions"]["stored_more_lenient"], 2)
        self.assertIn("anchoring 신호", result["reading"])

    def test_two_way_disagreement_is_read_as_difficulty(self):
        result = gold_provenance.compare(
            {"a": "REPAIR", "b": "PASS", "c": "PASS"}, self.PAYLOADS, self.SAMPLE)
        self.assertEqual(result["directions"]["stored_more_lenient"], 1)
        self.assertEqual(result["directions"]["stored_more_strict"], 1)
        self.assertIn("판정 난이도", result["reading"])

    def test_full_agreement_is_not_called_anchoring(self):
        result = gold_provenance.compare(
            {"a": "PASS", "b": "PASS", "c": "REPAIR"}, self.PAYLOADS, self.SAMPLE)
        self.assertEqual(result["agreement"], 1.0)
        self.assertIn("판정 난이도", result["reading"])

    def test_unlabelled_blind_rows_are_skipped_not_counted_as_agreement(self):
        result = gold_provenance.compare({"a": "PASS"}, self.PAYLOADS, self.SAMPLE)
        self.assertEqual(result["compared"], 1)


if __name__ == "__main__":
    unittest.main()
