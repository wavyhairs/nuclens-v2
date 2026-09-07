import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import audio_brief
import expert_audio_brief
import gemini_client
import semantic_verifier


class SemanticVerifierContractTests(unittest.TestCase):
    def test_compact_pass_and_block_contract(self):
        passed = semantic_verifier.normalize_report(
            {"verdict": "PASS", "passed": True, "findings": []})
        self.assertTrue(passed["passed"])
        blocked = semantic_verifier.normalize_report({
            "verdict": "BLOCK", "passed": False,
            "findings": [{"type": "CAUSALITY_ERROR", "line": "A 때문에 B",
                          "why": "인과 근거 없음"}],
        })
        self.assertFalse(blocked["passed"])

    def test_unknown_error_type_fails_closed(self):
        with self.assertRaises(gemini_client.GeminiError):
            semantic_verifier.normalize_report({
                "verdict": "BLOCK", "findings": [{"type": "MAYBE"}]})

    def test_missing_compact_verdict_fields_fail_closed(self):
        for report in ({"passed": True, "findings": []},
                       {"verdict": "PASS", "findings": []},
                       {"verdict": "PASS", "passed": True}):
            with self.subTest(report=report), self.assertRaises(gemini_client.GeminiError):
                semantic_verifier.normalize_report(report)

    def test_non_boolean_passed_fails_closed(self):
        with self.assertRaises(gemini_client.GeminiError):
            semantic_verifier.normalize_report(
                {"verdict": "PASS", "passed": "true", "findings": []})

    def test_verify_sends_the_machine_readable_schema(self):
        class Client:
            def __init__(self):
                self.kwargs = None

            def call_json(self, _system, _message, **kwargs):
                self.kwargs = kwargs
                return {"verdict": "PASS", "passed": True, "findings": []}

        client = Client()
        semantic_verifier.verify([], "HOST: claim", client=client)
        self.assertEqual(client.kwargs["response_json_schema"],
                         semantic_verifier.OUTPUT_JSON_SCHEMA)

    def test_later_cause_cannot_explain_earlier_effect(self):
        finding = semantic_verifier.chronology_finding(
            date(2026, 8, 11), date(2026, 7, 31))
        self.assertEqual(finding["type"], "TEMPORAL_ERROR")
        self.assertIsNone(semantic_verifier.chronology_finding(None, date(2026, 7, 31)))
        self.assertIsNone(semantic_verifier.chronology_finding(
            date(2026, 7, 1), date(2026, 7, 31)))

    def test_prompt_marks_generated_context_as_non_evidence(self):
        prompt = semantic_verifier.verification_prompt(
            {"facts": ["source"]}, "HOST: claim", context={"dossier": "generated"})
        self.assertIn("[Source Evidence]", prompt)
        self.assertIn("[Non-evidence Context]", prompt)
        self.assertIn("generated", prompt)

    def test_expert_prompt_separates_source_evidence_from_dossiers(self):
        prompt = expert_audio_brief.verification_prompt(
            {"date": "2026-08-14"}, [{"confirmed_facts": ["generated"]}],
            "HOST: claim", source_evidence=[{"text": "source"}])
        self.assertIn("[Source Evidence]", prompt)
        self.assertIn("[Non-evidence Context: Dossiers]", prompt)
        self.assertLess(prompt.index('"source"'), prompt.index('"generated"'))

    def test_digest_is_stable(self):
        report = {"verdict": "PASS", "passed": True, "findings": []}
        self.assertEqual(semantic_verifier.verdict_digest(report),
                         semantic_verifier.verdict_digest(dict(report)))

    def test_fast_gate_is_present_but_not_production_activated(self):
        self.assertFalse(semantic_verifier.FAST_SEMANTIC_GATE_ENABLED)

    def test_fast_repair_chain_reaudits_and_reverifies(self):
        class Client:
            def __init__(self):
                self.calls = []
                self.responses = [
                    {"verdict": "REPAIR", "passed": False,
                     "findings": [{"type": "CAUSALITY_ERROR", "line": "A 때문에 B",
                                   "why": "unsupported", "repair": "병렬 기술"}]},
                    {"script": "HOST: 수정된 문장입니다."},
                    {"verdict": "PASS", "passed": True, "findings": []},
                ]

            def call_json(self, system, message, **kwargs):
                self.calls.append(kwargs)
                return self.responses.pop(0)

        client = Client()
        audit = SimpleNamespace(ok=True, script="HOST: 수정된 문장입니다.")
        with patch.object(audio_brief, "validate_script",
                          return_value=(audit.script, 100)), \
                patch.object(audio_brief, "verify_script", return_value=audit):
            script, report = audio_brief.run_fast_semantic_gate(
                "HOST: A 때문에 B입니다.", [], {"date": "2026-08-14"}, client=client)
        self.assertEqual(script, audit.script)
        self.assertTrue(report["passed"])
        self.assertEqual([row["label"] for row in client.calls],
                         ["fast_verify", "fast_semantic_repair", "fast_verify"])

    def test_block_and_repair_both_require_intervention_in_fast_and_expert(self):
        for verdict in ("BLOCK", "REPAIR"):
            with self.subTest(verdict=verdict):
                report = {
                    "verdict": verdict, "passed": False,
                    "findings": [{"type": "FACT_ERROR", "line": "x",
                                  "why": "unsupported", "repair": "remove"}],
                    "coverage_score": 100, "factual_support_score": 100,
                    "stage_precision_score": 100, "expert_depth_score": 100,
                    "single_speaker_score": 100,
                    "unsupported_critical_claims": [],
                }
                self.assertFalse(semantic_verifier.normalize_report(report)["passed"])
                self.assertFalse(expert_audio_brief.verification_passed(report))

    def test_generated_latest_change_is_not_evidence(self):
        contracts = audio_brief.evidence_contracts(
            {"date": "2026-08-14"},
            [{"issue_id": "i", "title": "검증 제목", "summary": "검증 요약",
              "detail": "검증 상세", "latest_change": "모델이 만든 새로운 인과",
              "related_articles": []}],
        )
        flattened = " ".join(contract.text for contract in contracts)
        self.assertIn("검증 상세", flattened)
        self.assertNotIn("모델이 만든 새로운 인과", flattened)

    def test_explicit_expert_verifier_failure_has_no_model_fallback(self):
        calls = []

        def reject(*args, **kwargs):
            calls.append(kwargs)
            raise gemini_client.GeminiConfigError("high rejected")

        with patch.object(expert_audio_brief, "call_json", side_effect=reject), \
                self.assertRaises(gemini_client.GeminiConfigError):
            expert_audio_brief._call_structured(
                "system", "message", label="expert_verify_domestic")
        self.assertEqual(len(calls), 1)

    def test_text_send_precedes_nonfatal_audio_workflow(self):
        workflow = (audio_brief.BASE / ".github" / "workflows" /
                    "daily-brief.yml").read_text(encoding="utf-8")
        self.assertLess(workflow.index("- name: Send (텔레그램 발송)"),
                        workflow.index("- name: Generate audio briefings"))
        audio_section = workflow[workflow.index("- name: Generate audio briefings"):]
        self.assertIn("실패해도 배포·채널 공개는 계속한다", audio_section)


if __name__ == "__main__":
    unittest.main()
