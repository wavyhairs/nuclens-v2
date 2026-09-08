"""profile 별 판정 매핑이 **production 프롬프트와 일치하는가**.

매핑은 사람이 손으로 적은 표다. 손으로 적은 표는 프롬프트가 바뀌면 조용히 틀린다.
그래서 매핑이 인용한 규칙 문장이 실제 프롬프트에 있는지를 테스트가 확인한다 —
프롬프트를 고치면 여기서 먼저 실패하고, 무엇을 다시 읽어야 하는지가 드러난다.
"""

import json
import unittest
from pathlib import Path

import dedup
import issue_review
from tools import identity_contract_map as contract_map

FIXTURE = Path("tests/fixtures/gemini_reasoning/identity_candidates.json")


class ContractSourceTests(unittest.TestCase):
    """매핑의 근거가 지금도 프롬프트에 있는가."""

    def test_issue_review_still_merges_stage_progression(self):
        prompt = issue_review.SYSTEM_PROMPT
        self.assertIn("진행 단계가 바뀐 것은 같은 사건이다", prompt)
        self.assertIn("협상 → 계약 체결", prompt)

    def test_dedup_still_splits_a_new_independent_action(self):
        prompt = dedup.ARTICLE_STORY_PROMPT
        self.assertIn("새로운 독립적 행동/결정/상태전환", prompt)
        self.assertIn("'협상' 이후 '계약 체결'", prompt)

    def test_the_two_contracts_really_do_disagree(self):
        """이 테스트가 통과하는 한 단일 Identity 정답지는 성립하지 않는다.

        두 프롬프트가 같은 예시('협상 → 계약 체결')를 들면서 반대 판정을 요구한다.
        버그가 아니라 서로 다른 질문이다 — issue_review 는 오래 가는 이슈를 잇고,
        dedup 은 그날 브리핑 한 칸의 중복을 없앤다.
        """
        self.assertIn("계약 체결", issue_review.SYSTEM_PROMPT)
        self.assertIn("계약 체결", dedup.ARTICLE_STORY_PROMPT)
        self.assertEqual(contract_map.verdict("different_stage", "issue_review"),
                         contract_map.MERGE)
        self.assertEqual(contract_map.verdict("different_stage", "dedup"),
                         contract_map.SEPARATE)

    def test_every_mapped_row_explains_itself(self):
        for code, row in contract_map.CONTRACT_MAP.items():
            with self.subTest(reason_code=code):
                self.assertGreater(len(str(row["why"])), 15)


class MappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

    def test_every_reason_code_in_the_fixture_is_mapped(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for code in payload["reason_codes"]:
            with self.subTest(reason_code=code):
                self.assertIn(code, contract_map.CONTRACT_MAP)

    def test_unknown_inputs_are_refused_rather_than_defaulted(self):
        with self.assertRaises(ValueError):
            contract_map.verdict("no_such_code", "dedup")
        with self.assertRaises(ValueError):
            contract_map.verdict("same_action", "no_such_profile")

    def test_stored_labels_already_match_the_dedup_contract(self):
        """기존 60건은 dedup 계약에 맞춰져 있었다 — 폐기할 이유가 없다."""
        result = contract_map.apply_to(self.cases, "dedup")
        self.assertEqual(result["label_flipped_by_contract"], 0)

    def test_only_stage_progression_flips_for_issue_review(self):
        result = contract_map.apply_to(self.cases, "issue_review")
        self.assertEqual(result["label_flipped_by_contract"], 6)
        self.assertEqual({row["reason_code"] for row in result["flipped_rows"]},
                         {"different_stage"})
        self.assertTrue(all(row["stored_label"] == "SEPARATE"
                            and row["mapped"] == contract_map.MERGE
                            for row in result["flipped_rows"]))

    def test_undecidable_rows_are_not_counted_as_usable(self):
        # 추측해 채우면 사람이 다시 못 볼 곳에 오답이 박힌다.
        for profile in ("issue_review", "dedup"):
            with self.subTest(profile=profile):
                result = contract_map.apply_to(self.cases, profile)
                self.assertGreater(result["needs_case_review"], 0)
                self.assertEqual(
                    result["usable"] + result["needs_case_review"]
                    + result["excluded"],
                    sum(1 for case in self.cases if case.get("human_label")))
                self.assertNotIn(contract_map.REVIEW, result["verdict_counts"])

    def test_the_contract_mapping_improves_issue_review_class_balance(self):
        """원본 13/47 은 MERGE 가 너무 적어 merge recall 을 잴 수 없었다.

        issue_review 계약으로 옮기면 19/24 가 되어 양쪽 recall 이 의미를 갖는다.
        이것이 기존 Gold 를 버리지 않아도 되는 실질적 이유다.
        """
        raw = [case["human_label"] for case in self.cases if case.get("human_label")]
        self.assertEqual(raw.count("MERGE"), 13)
        mapped = contract_map.apply_to(self.cases, "issue_review")
        self.assertEqual(mapped["verdict_counts"]["MERGE"], 19)

    def test_dedup_merge_class_stays_too_thin_to_rank_configs(self):
        """dedup 쪽은 MERGE 가 8건뿐이다 — 이 사실을 숨기면 안 된다.

        8건으로는 merge recall 의 오차가 config 차이보다 크다. dedup 판정은
        false merge 가 아니라 coverage/붕괴 쪽 안전 지표로 읽어야 한다.
        """
        mapped = contract_map.apply_to(self.cases, "dedup")
        self.assertEqual(mapped["verdict_counts"]["MERGE"], 8)
        self.assertLess(mapped["verdict_counts"]["MERGE"], 15)

    def test_keei_match_follows_the_issue_review_contract(self):
        self.assertEqual(contract_map.PROFILES["keei_match"], "issue_review")
        self.assertEqual(contract_map.verdict("different_stage", "keei_match"),
                         contract_map.MERGE)


if __name__ == "__main__":
    unittest.main()
