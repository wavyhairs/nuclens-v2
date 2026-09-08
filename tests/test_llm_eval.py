import unittest

from tools import llm_eval


class EvaluatorLockdownTests(unittest.TestCase):
    """평가기가 production 계약 없이 숫자를 만들 수 없음을 고정한다.

    이 클래스가 회귀 방지의 본체다. 과거에 간이 판정 프롬프트로 뽑은 정확도를
    production 성능으로 착각해 며칠을 태운 적이 있고, 그때 실제로 남은 위험은
    "프롬프트가 남아 있다"가 아니라 "그 경로를 실행할 수 있다"였다.
    """

    def test_no_task_is_open_without_a_production_request_builder(self):
        for task in llm_eval.TASKS:
            with self.subTest(task=task):
                with self.assertRaises(llm_eval.ContractNotProven):
                    llm_eval.call_contract(task, {"id": "x"})

    def test_generic_judge_prompts_are_gone(self):
        self.assertFalse(hasattr(llm_eval, "SYSTEMS"))
        self.assertFalse(hasattr(llm_eval, "user_message"))

    def test_unknown_task_is_refused_rather_than_defaulted(self):
        with self.assertRaises(llm_eval.ContractNotProven):
            llm_eval.contract("ANYTHING_ELSE")

    def test_blocked_reason_is_specific_enough_to_act_on(self):
        for task, entry in llm_eval.TASKS.items():
            with self.subTest(task=task):
                self.assertGreater(len(entry.blocked_reason), 20)


class CheckpointNamespaceTests(unittest.TestCase):
    """무효 평가기의 결과가 새 평가를 건너뛰게 만들지 않음을 고정한다."""

    def test_key_requires_an_evaluator_policy(self):
        with self.assertRaises(ValueError):
            llm_eval.result_key("m", "level:high", "f", 2, "")

    def test_key_includes_all_resume_dimensions(self):
        self.assertEqual(
            llm_eval.result_key("m", "level:high", "f", 2, "identity-replay-v1"),
            "identity-replay-v1|m|level:high|f|2")

    def test_legacy_rows_are_preserved_but_never_counted_as_done(self):
        rows = [
            # 옛 평가기가 남긴 행: policy prefix 가 없다. 파일에는 남아야 하지만
            # 새 계약의 조합을 완료로 만들면 안 된다.
            {"key": "m|none|f|0", "status": "ok"},
            {"key": "identity-replay-v1|m|none|f|0", "status": "failed"},
            {"key": "identity-replay-v1|m|none|f|1", "status": "ok"},
        ]
        self.assertEqual(llm_eval.completed_keys(rows, "identity-replay-v1"),
                         {"identity-replay-v1|m|none|f|1"})

    def test_other_policy_rows_do_not_leak_across_namespaces(self):
        rows = [{"key": "semantic-expert-v1|m|none|f|0", "status": "ok"}]
        self.assertEqual(llm_eval.completed_keys(rows, "identity-replay-v1"), set())

    def test_completed_keys_requires_a_policy(self):
        with self.assertRaises(ValueError):
            llm_eval.completed_keys([], "")

    def test_latest_retry_wins_without_dropping_history(self):
        rows = [
            {"key": "p|a", "status": "failed"},
            {"key": "p|a", "status": "ok"},
            {"key": "p|b", "status": "failed"},
        ]
        self.assertEqual({row["key"]: row["status"]
                          for row in llm_eval.latest_results(rows)},
                         {"p|a": "ok", "p|b": "failed"})


class GoldHandlingTests(unittest.TestCase):
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
