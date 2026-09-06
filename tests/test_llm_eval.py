import json
import unittest

from tools import llm_eval


class EvalInfrastructureTests(unittest.TestCase):
    def test_key_includes_all_resume_dimensions(self):
        self.assertEqual(llm_eval.result_key("m", "level:high", "f", 2),
                         "m|level:high|f|2")

    def test_configs_never_silently_downgrade(self):
        self.assertEqual(llm_eval.config_kwargs("none"), {})
        self.assertEqual(llm_eval.config_kwargs("level:high"), {"thinking_level": "high"})
        with self.assertRaises(ValueError):
            llm_eval.config_kwargs("level:weak")

    def test_human_required_cases_are_not_evaluated(self):
        payload = {"cases": [{"id": "x", "human_label": None,
                              "label_status": "HUMAN_LABEL_REQUIRED"},
                             {"id": "y", "human_label": "MERGE",
                              "label_status": "HUMAN_LABELLED"}]}
        self.assertEqual([row["id"] for row in llm_eval.labelled_cases(payload)], ["y"])

    def test_model_expected_verdict_is_never_treated_as_human_gold(self):
        payload = {"cases": [{"id": "x", "expected_verdict": "PASS"},
                             {"id": "y", "human_label": "PASS",
                              "label_status": "USER_SPECIFIED"}]}
        self.assertEqual([row["id"] for row in llm_eval.labelled_cases(payload)], ["y"])

    def test_identity_message_accepts_structured_pair(self):
        message = llm_eval.user_message(
            "IDENTITY_REVIEW", {"id": "x", "a": {"title": "A"},
                                "b": {"title": "B"}})
        self.assertEqual(message, "A: A\nB: B")

    def test_semantic_message_cannot_leak_gold_or_candidate_focus(self):
        message = json.loads(llm_eval.user_message("SEMANTIC", {
            "claim": "claim", "source_evidence": {"facts": ["fact"]},
            "human_label": "BLOCK", "human_error_types": ["FACT_ERROR"],
            "selection_metadata_not_gold": {"review_focus": "FACT_ERROR"},
            "candidate_kind": "controlled_perturbation",
        }))
        self.assertEqual(message, {"claim": "claim",
                                   "source_evidence": {"facts": ["fact"]}})

    def test_only_successful_keys_are_complete_and_latest_retry_wins(self):
        rows = [
            {"key": "a", "status": "failed"},
            {"key": "a", "status": "ok"},
            {"key": "b", "status": "failed"},
        ]
        self.assertEqual(llm_eval.completed_keys(rows), {"a"})
        self.assertEqual({row["key"]: row["status"]
                          for row in llm_eval.latest_results(rows)},
                         {"a": "ok", "b": "failed"})

    def test_identity_costs_are_reported_separately(self):
        rows = [
            {"status": "ok", "model": "m", "config": "none", "fixture_id": "a",
             "gold": "SEPARATE", "prediction": "MERGE", "latency_seconds": 1,
             "thought_tokens": 0},
            {"status": "ok", "model": "m", "config": "none", "fixture_id": "b",
             "gold": "MERGE", "prediction": "SEPARATE", "latency_seconds": 2,
             "thought_tokens": 4},
        ]
        summary = llm_eval.summarize(rows)
        self.assertEqual(summary["false_merge"], 1)
        self.assertEqual(summary["false_split"], 1)
        self.assertEqual(summary["thought_tokens"], 4)


if __name__ == "__main__":
    unittest.main()
