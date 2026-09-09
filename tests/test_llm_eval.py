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



class JobOrderTests(unittest.TestCase):
    """호출 **순서**가 곧 측정 조건이다.

    config 를 바깥 루프에 두면 뒤에 도는 arm 일수록 페이싱과 누적 quota 압력을 더
    받는다. 늘 마지막에 도는 high 가 체계적으로 느리게 나오는데, 그건 reasoning 의
    성질이 아니라 자리의 성질이다. 그 편향을 없애는 것이 이 테스트의 대상이다.
    """

    CASES = [{"id": f"case-{index}"} for index in range(6)]
    CONFIGS = ["none", "level:low", "level:medium", "level:high"]

    def _jobs(self, repeat=1):
        return llm_eval.plan_jobs(["m"], self.CONFIGS, self.CASES, repeat, "p1")

    def test_every_planned_combination_is_present_exactly_once(self):
        jobs = self._jobs(repeat=2)
        keys = [job[4] for job in jobs]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), len(self.CASES) * len(self.CONFIGS) * 2)

    def test_configs_are_interleaved_not_run_in_blocks(self):
        # config-major 였다면 같은 config 가 연속으로 6번씩 나온다.
        order = [job[1] for job in self._jobs()]
        longest = best = 1
        for previous, current in zip(order, order[1:]):
            best = best + 1 if current == previous else 1
            longest = max(longest, best)
        self.assertLessEqual(longest, 2, f"config 가 뭉쳐 있다: {order}")

    def test_no_config_is_systematically_last(self):
        # 어떤 arm 도 늘 꼴찌면 그 arm 만 페이싱을 더 받는다.
        per_case = {}
        for job in self._jobs():
            per_case.setdefault(job[2]["id"], []).append(job[1])
        last_places = [configs[-1] for configs in per_case.values()]
        self.assertGreater(len(set(last_places)), 1,
                           f"한 config 가 늘 마지막이다: {last_places}")

    def test_order_is_deterministic_across_runs(self):
        # 재개해도 같은 순서여야 하고, 결과를 본 뒤 순서를 바꾸는 길도 막힌다.
        self.assertEqual([job[4] for job in self._jobs()],
                         [job[4] for job in self._jobs()])

    def test_repeats_of_one_case_do_not_share_an_order(self):
        jobs = self._jobs(repeat=2)
        by_repeat = {}
        for job in jobs:
            if job[2]["id"] == "case-0":
                by_repeat.setdefault(job[3], []).append(job[1])
        self.assertNotEqual(by_repeat[0], by_repeat[1])


class ProvenanceGateTests(unittest.TestCase):
    """AI 보조 라벨이 조용히 평가 Gold 가 되지 않는가."""

    PAYLOAD = {"cases": [
        {"id": "a", "human_label": "PASS", "label_status": "USER_SPECIFIED"},
        {"id": "b", "human_label": "BLOCK",
         "label_status": "HUMAN_REVIEWED_AI_ASSISTED"},
        {"id": "c", "human_label": None, "label_status": "HUMAN_LABEL_REQUIRED"},
    ]}

    def test_ai_assisted_labels_are_excluded_from_evaluation(self):
        self.assertEqual([case["id"] for case in llm_eval.labelled_cases(self.PAYLOAD)],
                         ["a"])

    def test_the_exclusion_is_counted_so_it_can_be_reported(self):
        # 조용히 빠지면 "Gold 가 적다"는 사실이 보고서에서 사라진다.
        self.assertEqual(llm_eval.excluded_by_provenance(self.PAYLOAD), 1)

    def test_ai_assisted_status_is_not_in_the_eligible_set(self):
        self.assertNotIn(llm_eval.AI_ASSISTED_STATUS, llm_eval.EVALUATION_STATUSES)


class RedundantArmTests(unittest.TestCase):
    """thought 토큰이 0 인 arm 은 baseline 과 구별되지 않는다 — 돌릴 이유가 없다."""

    def test_a_zero_thought_arm_is_flagged_as_redundant(self):
        rows = [{"status": "ok", "model": "m35", "config": "level:low",
                 "thought_tokens": 0} for _ in range(3)]
        self.assertEqual(llm_eval.redundant_arms(rows), {"m35": ["level:low"]})

    def test_a_thinking_arm_is_kept(self):
        rows = [{"status": "ok", "model": "m35", "config": "level:medium",
                 "thought_tokens": 325} for _ in range(3)]
        self.assertEqual(llm_eval.redundant_arms(rows), {})

    def test_baseline_is_never_called_redundant(self):
        rows = [{"status": "ok", "model": "m", "config": "none",
                 "thought_tokens": 0} for _ in range(3)]
        self.assertEqual(llm_eval.redundant_arms(rows), {})

    def test_a_single_observation_is_not_enough_to_prune(self):
        # 한 건뿐이면 "0 이었다"가 아니라 "아직 모른다"다.
        rows = [{"status": "ok", "model": "m", "config": "level:low",
                 "thought_tokens": 0}]
        self.assertEqual(llm_eval.redundant_arms(rows), {})

    def test_failed_rows_do_not_count_as_evidence_of_zero_thinking(self):
        rows = [{"status": "failed", "model": "m", "config": "level:low"}
                for _ in range(3)]
        self.assertEqual(llm_eval.redundant_arms(rows), {})

    def test_pruning_is_per_model_because_the_limit_is_model_specific(self):
        rows = ([{"status": "ok", "model": "m35", "config": "level:low",
                  "thought_tokens": 0} for _ in range(2)]
                + [{"status": "ok", "model": "m31", "config": "level:low",
                    "thought_tokens": 133} for _ in range(2)])
        self.assertEqual(llm_eval.redundant_arms(rows), {"m35": ["level:low"]})


class LatencyDecompositionTests(unittest.TestCase):
    def test_overhead_is_summarized_apart_from_api_latency(self):
        rows = [{"status": "ok", "model": "m", "config": "none", "fixture_id": "a",
                 "gold": "PASS", "prediction": "PASS", "latency_seconds": 1.0,
                 "overhead_seconds": 4.0, "thought_tokens": 0},
                {"status": "ok", "model": "m", "config": "none", "fixture_id": "b",
                 "gold": "PASS", "prediction": "PASS", "latency_seconds": 2.0,
                 "overhead_seconds": 0.0, "thought_tokens": 0}]
        summary = llm_eval.summarize(rows)
        # p50 은 순수 API 시간에서 나와야 한다 — 페이싱이 섞이면 4.0 쪽으로 끌린다.
        self.assertEqual(summary["latency_p50"], 1.5)
        self.assertEqual(summary["overhead_p50"], 2.0)
        self.assertEqual(summary["overhead_total"], 4.0)

    def test_rows_without_overhead_do_not_break_the_summary(self):
        rows = [{"status": "ok", "model": "m", "config": "none", "fixture_id": "a",
                 "gold": "PASS", "prediction": "PASS", "latency_seconds": 1.0,
                 "thought_tokens": 0}]
        summary = llm_eval.summarize(rows)
        self.assertIsNone(summary["overhead_p50"])

if __name__ == "__main__":
    unittest.main()
