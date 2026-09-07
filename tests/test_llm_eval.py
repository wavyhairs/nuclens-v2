import unittest

from tools import llm_eval


class EvalInfrastructureTests(unittest.TestCase):
    def test_key_includes_all_resume_dimensions(self):
        self.assertEqual(llm_eval.result_key("m", "level:high", "f", 2),
                         "m|level:high|f|2")
        self.assertEqual(llm_eval.result_key(
            "m", "level:high", "f", 2, "schema-v2"),
            "schema-v2|m|level:high|f|2")

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

    def test_explicit_checkpoint_case_selection_is_ordered_and_strict(self):
        cases = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(llm_eval.select_cases(cases, ["b", "a"]),
                         [{"id": "b"}, {"id": "a"}])
        with self.assertRaises(ValueError):
            llm_eval.select_cases(cases, ["a", "a"])
        with self.assertRaises(ValueError):
            llm_eval.select_cases(cases, ["missing"])

    def test_identity_message_accepts_structured_pair(self):
        message = llm_eval.user_message(
            "IDENTITY_REVIEW", {"id": "x", "a": {"title": "A"},
                                "b": {"title": "B"}})
        self.assertEqual(message, "A: A\nB: B")

    def test_semantic_message_cannot_leak_gold_or_candidate_focus(self):
        message = llm_eval.user_message("SEMANTIC", {
            "claim": "claim", "source_evidence": {"facts": ["fact"]},
            "generated_context_not_source_evidence": {"summary": "context"},
            "human_label": "BLOCK", "human_error_types": ["FACT_ERROR"],
            "selection_metadata_not_gold": {"review_focus": "FACT_ERROR"},
            "candidate_kind": "controlled_perturbation",
        })
        self.assertIn('[Source Evidence]', message)
        self.assertIn('"facts": [', message)
        self.assertIn('[Non-evidence Context]', message)
        self.assertIn('"summary": "context"', message)
        self.assertIn('[Script]\nclaim', message)
        self.assertNotIn('human_label', message)
        self.assertNotIn('review_focus', message)

    def test_semantic_call_uses_production_verifier_contract(self):
        system, message, options = llm_eval.call_contract("SEMANTIC", {
            "claim": "claim", "source_evidence": {"facts": ["fact"]},
        })
        self.assertEqual(system, llm_eval.semantic_verifier.SYSTEM_PROMPT)
        self.assertEqual(message, llm_eval.semantic_verifier.verification_prompt(
            {"facts": ["fact"]}, "claim", context=None))
        self.assertEqual(options, {
            "max_output_tokens": 6000, "timeout": 150, "retries": 2,
            "response_json_schema": llm_eval.semantic_verifier.OUTPUT_JSON_SCHEMA,
            "capture_response_text": True,
        })

    def test_curation_short_judge_contract_is_disabled(self):
        with self.assertRaisesRegex(ValueError, "Human labels"):
            llm_eval.call_contract("CURATION", {"id": "x"})

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

    def test_identity_summary_includes_class_balanced_config_metrics(self):
        rows = [
            {"status": "ok", "model": "m", "config": "unspecified",
             "fixture_id": "a", "gold": "MERGE", "prediction": "MERGE",
             "latency_seconds": 1, "thought_tokens": 0, "repeat": 0},
            {"status": "ok", "model": "m", "config": "unspecified",
             "fixture_id": "b", "gold": "SEPARATE", "prediction": "MERGE",
             "latency_seconds": 3, "thought_tokens": 0, "repeat": 0},
        ]
        metrics = llm_eval.summarize(rows)["configs"]["unspecified"]
        self.assertEqual(metrics["accuracy"], .5)
        self.assertEqual(metrics["balanced_accuracy"], .5)
        self.assertEqual(metrics["merge_recall"], 1.0)
        self.assertEqual(metrics["separate_recall"], 0.0)
        self.assertEqual(metrics["prediction_counts"], {"MERGE": 2})
        self.assertEqual(metrics["per_label_precision"],
                         {"MERGE": .5, "SEPARATE": None})
        self.assertIsNone(metrics["retries"])

    def test_local_socket_block_is_not_reported_as_api_failure(self):
        summary = llm_eval.summarize([{
            "key": "x", "status": "failed", "failure_type": "api",
            "error": "URLError: <urlopen error [WinError 10013] forbidden by its access permissions>",
            "config": "unspecified",
        }])
        self.assertEqual(summary["environment_failure"], 1)
        self.assertEqual(summary["api_failure"], 0)

    def test_result_schema_is_enforced(self):
        self.assertEqual(
            llm_eval.validate_result("IDENTITY_REVIEW", {"verdict": "MERGE"}),
            ("MERGE", []))
        with self.assertRaises(ValueError):
            llm_eval.validate_result("IDENTITY_REVIEW", {"verdict": "PASS"})
        with self.assertRaises(ValueError):
            llm_eval.validate_result(
                "SEMANTIC", {"verdict": "PASS", "passed": True,
                             "findings": "FACT_ERROR"})
        with self.assertRaises(ValueError):
            llm_eval.validate_result(
                "SEMANTIC", {"verdict": "BLOCK", "passed": False,
                             "findings": [{"type": "MADE_UP"}]})
        self.assertEqual(llm_eval.validate_result(
            "SEMANTIC", {"verdict": "BLOCK", "passed": False,
                         "findings": [{"type": "FACT_ERROR", "line": "x",
                                       "why": "y", "repair": "z"}]}),
            ("BLOCK", ["FACT_ERROR"]))

    def test_semantic_safety_and_severity_metrics_are_separate(self):
        rows = [
            {"status": "ok", "model": "m", "config": "none", "fixture_id": "a",
             "gold": "BLOCK", "prediction": "REPAIR", "latency_seconds": 1},
            {"status": "ok", "model": "m", "config": "none", "fixture_id": "b",
             "gold": "BLOCK", "prediction": "PASS", "latency_seconds": 1},
            {"status": "ok", "model": "m", "config": "none", "fixture_id": "c",
             "gold": "REPAIR", "prediction": "BLOCK", "latency_seconds": 1},
            {"status": "ok", "model": "m", "config": "none", "fixture_id": "d",
             "gold": "PASS", "prediction": "REPAIR", "latency_seconds": 1},
        ]
        metrics = llm_eval.summarize(rows, "SEMANTIC")
        self.assertEqual(metrics["unsafe_pass"], 1)
        self.assertAlmostEqual(metrics["safe_intervention_rate"], 2 / 3)
        self.assertEqual(metrics["block_recall"], 0.0)
        self.assertEqual(metrics["block_as_repair"], 1)
        self.assertEqual(metrics["repair_as_block"], 1)
        self.assertEqual(metrics["pass_false_repair"], 1)


if __name__ == "__main__":
    unittest.main()
