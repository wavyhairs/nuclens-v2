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
