"""Central LLM policy is deterministic and initially request-body neutral."""

import os
import unittest
from unittest.mock import patch

import llm_policy


class TaskPolicyTests(unittest.TestCase):
    def test_all_production_profiles_start_unspecified(self):
        for name, item in llm_policy.production_policy_snapshot().items():
            with self.subTest(name=name):
                self.assertIsNone(item["thinking"])
                self.assertEqual(item["sampling_mode"], "explicit")
                self.assertEqual(item["activation"], "IMPLEMENTED_BUT_NOT_ACTIVATED")

    def test_reasoning_kwargs_are_empty_for_noop_policy(self):
        self.assertEqual(llm_policy.profile("issue_review").reasoning_kwargs(), {})

    def test_model_resolvers_preserve_current_buckets(self):
        env = {
            "GEMINI_REVIEW_MODEL": "review-x",
            "GEMINI_INSIGHT_MODEL": "insight-x",
            "GEMINI_SYNTHESIS_MODEL": "synth-x",
            "GEMINI_SCRIPT_MODEL": "script-x",
            "GEMINI_VERIFY_MODEL": "verify-x",
        }
        with patch.dict(os.environ, env):
            self.assertEqual(llm_policy.profile("issue_review").model(), "review-x")
            self.assertEqual(llm_policy.profile("issue_insight").model(), "insight-x")
            self.assertEqual(llm_policy.profile("weekly_bot").model(), "synth-x")
            self.assertEqual(llm_policy.profile("audio_brief").model(), "script-x")
            self.assertEqual(llm_policy.profile("expert_verify_1").model(), "verify-x")

    def test_card_profiles_resolve_in_the_documented_order(self):
        """카드는 자리마다 해석 순서가 다르다 — 편집 데스크와 카피라이터는
        같은 과제가 아니다.

            card_editorial_narrator  CARD_EDITORIAL_NARRATOR_MODEL
                                     → GEMINI_SYNTHESIS_MODEL → 기본
            card_writer              CARD_WRITER_MODEL
                                     → CARDS_GEMINI_MODEL → GEMINI_MODEL → 기본
        """
        with patch.dict(os.environ, {"GEMINI_SYNTHESIS_MODEL": "synth-x"}, clear=False):
            self.assertEqual(
                llm_policy.profile("card_editorial_narrator").model(), "synth-x")
        with patch.dict(os.environ, {"CARD_EDITORIAL_NARRATOR_MODEL": "narrator-x",
                                     "GEMINI_SYNTHESIS_MODEL": "synth-x"}, clear=False):
            self.assertEqual(
                llm_policy.profile("card_editorial_narrator").model(), "narrator-x")
        with patch.dict(os.environ, {"CARDS_GEMINI_MODEL": "cards-x"}, clear=False):
            for task in ("card_writer", "card_daily_writer", "card_writer_repair"):
                with self.subTest(task=task):
                    self.assertEqual(llm_policy.profile(task).model(), "cards-x")
        with patch.dict(os.environ, {"CARD_WRITER_MODEL": "writer-x",
                                     "CARDS_GEMINI_MODEL": "cards-x"}, clear=False):
            self.assertEqual(llm_policy.profile("card_writer").model(), "writer-x")

    def test_no_card_profile_defaults_to_a_preview_model(self):
        """스토리 카드의 `gemini-3-flash-preview` 기본 의존을 걷었다.

        그 값의 근거는 "로컬에서 flash-lite 가 표지 제목 길이로 세 번 걸렸다"
        한 줄뿐이었다. 길이는 모델을 올려 푸는 문제가 아니라 재시도에 실패
        사유를 돌려주면 되는 문제다.
        """
        snapshot = llm_policy.production_policy_snapshot()
        for name, row in snapshot.items():
            if name.startswith("card_"):
                with self.subTest(task=name):
                    self.assertNotIn("preview", row["model"])

    def test_verify_is_strict_but_not_activated(self):
        item = llm_policy.profile("expert_verify_after_repair_1")
        self.assertTrue(item.strict_reasoning)
        self.assertIsNone(item.thinking_level)

    def test_fingerprint_is_stable_and_policy_sensitive(self):
        item = llm_policy.profile("issue_review")
        first = llm_policy.generation_policy_fingerprint(item, 2)
        self.assertEqual(first, llm_policy.generation_policy_fingerprint(item, 2))
        self.assertNotEqual(first, llm_policy.generation_policy_fingerprint(item, 3))

    def test_unknown_profile_fails_closed(self):
        with self.assertRaises(KeyError):
            llm_policy.profile("typo_task")


if __name__ == "__main__":
    unittest.main()
