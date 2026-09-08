"""production 이 실제로 보내는 reasoning baseline 을 고정한다.

`llm_policy` 만 읽으면 모든 profile 이 `thinking_level=None` 이라 baseline 이 전부
"unspecified" 로 보인다. 실제로는 두 갈래가 더 있다.

- `expert_audio_brief` / `audio_brief` 가 `thinking_budget=0` 을 따로 얹는다.
- `gemini_client` 는 `gemini-3.5-flash-lite` 가 그 값을 거부하므로 그 모델에서만
  필드를 생략한다.

그래서 **같은 코드가 모델에 따라 explicit OFF 도 되고 필드 없음도 된다.** 이 구분을
놓치면 reasoning 비교의 baseline arm 이 production 에 존재하지 않는 설정이 되고,
그 위에서 나온 숫자는 아무것도 말해 주지 않는다.

이 테스트는 문서가 아니라 **직렬화된 본문**을 근거로 그 구분을 붙잡는다.
"""

import json
import unittest
from pathlib import Path

import llm_policy
from tools import observed_baseline

FIXTURE = Path("tests/fixtures/gemini_reasoning/observed_baseline.json")

# gemini-3.1-flash-lite 는 thinkingBudget:0 을 받으므로 thinking 이 **명시적으로
# 꺼진 채** 돈다. 3.5-flash-lite 는 그 값을 거부해 필드가 빠지므로 unspecified 다.
EXPLICIT_OFF = {"expert_dossiers", "expert_verify"}


class ObservedBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["callsites"]

    def test_every_callsite_actually_serializes_a_request(self):
        # "요청이 안 만들어졌다"를 조용히 넘기면 그 callsite 만 검증 없이 빠진다.
        for name, row in self.expected.items():
            with self.subTest(callsite=name):
                self.assertNotIn("error", row)

    def test_live_probe_still_matches_the_frozen_baseline(self):
        observed = observed_baseline.audit()["callsites"]
        self.assertEqual(observed, self.expected)

    def test_explicit_thinking_off_is_not_mistaken_for_unspecified(self):
        for name in EXPLICIT_OFF:
            with self.subTest(callsite=name):
                row = self.expected[name]
                self.assertEqual(row["observed_baseline_thinking"], "budget:0")
                self.assertEqual(row["thinking_config"], {"thinkingBudget": 0})
                self.assertEqual(row["model"], "gemini-3.1-flash-lite")

    def test_model_constraint_turns_the_same_code_into_unspecified(self):
        # 같은 `_call_structured` 인데 사다리 첫 단이 3.5-flash-lite 라 필드가 빠진다.
        for name in ("expert_plan", "expert_script", "expert_repair"):
            with self.subTest(callsite=name):
                row = self.expected[name]
                self.assertEqual(row["model"], "gemini-3.5-flash-lite")
                self.assertEqual(row["observed_baseline_thinking"], "absent")
                self.assertIsNone(row["thinking_config"])

    def test_evaluation_scope_callsites_have_no_thinking_field(self):
        for name in ("curation", "dedup", "dedup_final", "issue_review", "keei_match"):
            with self.subTest(callsite=name):
                self.assertEqual(
                    self.expected[name]["observed_baseline_thinking"], "absent")

    def test_no_callsite_is_already_running_an_explicit_reasoning_level(self):
        # 활성화 전이므로 어떤 callsite 도 thinkingLevel 을 보내면 안 된다.
        for name, row in self.expected.items():
            with self.subTest(callsite=name):
                self.assertFalse(
                    row["observed_baseline_thinking"].startswith("level:"))


class ContractFingerprintTests(unittest.TestCase):
    """reasoning 결정이 어떤 계약 위에서 검증됐는지를 지문이 실제로 붙잡는가."""

    BASE = {
        "profile": "curation", "resolved_model": "gemini-3.1-flash-lite",
        "observed_baseline_thinking": "absent", "system_prompt_sha": "a",
        "user_builder_sha": "b", "response_schema_sha": None, "temperature": 0.2,
        "max_output_tokens": 32768, "timeout": 150.0, "retries": 3,
        "batch_size": 15, "split_budget": 6, "parser_sha": "c", "normalizer_sha": "d",
    }

    def test_partial_contracts_are_refused(self):
        for field in llm_policy.CONTRACT_FIELDS:
            with self.subTest(missing=field):
                partial = {k: v for k, v in self.BASE.items() if k != field}
                with self.assertRaises(KeyError):
                    llm_policy.production_contract_fingerprint(partial)

    def test_abstract_baseline_names_are_refused(self):
        for name in ("unspecified", "none", ""):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    llm_policy.production_contract_fingerprint(
                        {**self.BASE, "observed_baseline_thinking": name})

    def test_every_contract_axis_changes_the_fingerprint(self):
        base = llm_policy.production_contract_fingerprint(self.BASE)
        for field in llm_policy.CONTRACT_FIELDS:
            with self.subTest(field=field):
                changed = llm_policy.production_contract_fingerprint(
                    {**self.BASE, field: "CHANGED"})
                self.assertNotEqual(base, changed)

    def test_explicit_off_and_absent_are_not_the_same_contract(self):
        # 이 구분이 무너지면 3.1 expert 경로의 검증이 3.5 경로로 새어 나간다.
        self.assertNotEqual(
            llm_policy.production_contract_fingerprint(
                {**self.BASE, "observed_baseline_thinking": "absent"}),
            llm_policy.production_contract_fingerprint(
                {**self.BASE, "observed_baseline_thinking": "budget:0"}))


if __name__ == "__main__":
    unittest.main()
