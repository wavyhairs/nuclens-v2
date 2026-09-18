import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import curation_p4
from tools import curation_p4_judge as judge


def valid_judgment(aliases=("A", "B")):
    return {
        "candidates": [{
            "candidate": alias,
            "dimensions": [{"name": name, "status": "PASS", "severity": "NONE",
                            "reason": "supported"} for name in judge.DIMENSIONS],
            "final_verdict": "PASS", "summary": "publishable",
        } for alias in aliases],
        "pairwise": [{"left": left, "right": right, "preferred": "TIE", "reason": "same"}
                     for index, left in enumerate(aliases) for right in aliases[index + 1:]],
    }


class BlindPacketTests(unittest.TestCase):
    def setUp(self):
        self.source = {"title": "source", "body": "evidence"}
        self.candidates = {
            "current": {"summary": "one"},
            "level:low": {"summary": "two"},
            "level:medium": {"summary": "three"},
            "level:high": {"summary": "four"},
        }

    def test_alias_order_is_deterministic_and_repeat_versioned(self):
        first = judge.build_blind_request("case", self.source, self.candidates, 0)
        again = judge.build_blind_request("case", self.source, self.candidates, 0)
        orders = [tuple(judge.build_blind_request(
            "case", self.source, self.candidates, repeat).aliases.values())
                  for repeat in range(6)]
        self.assertEqual(first.aliases, again.aliases)
        self.assertGreater(len(set(orders)), 1)

    def test_wire_payload_is_anonymous_and_has_all_pairs(self):
        request = judge.build_blind_request("case", self.source, self.candidates, 0)
        wire = request.body["input"]
        for secret in self.candidates:
            self.assertNotIn(secret, wire)
        payload = json.loads(wire)
        self.assertEqual({row["candidate"] for row in payload["candidates"]},
                         {"A", "B", "C", "D"})
        self.assertEqual(len(payload["required_pairs"]), 6)

    def test_candidate_metadata_leak_is_rejected(self):
        with self.assertRaises(ValueError):
            judge.build_blind_request(
                "case", self.source,
                {"x": {"summary": "baseline config"}, "y": {"summary": "clean"}}, 0)

    def test_policy_namespaces_results_and_legacy_rows_do_not_resume(self):
        current = judge.result_key("case", 2)
        rows = [{"key": "old|case|2", "status": "ok"},
                {"key": current, "status": "ok"}]
        self.assertEqual(judge.completed_keys(rows), {current})


class JudgeFailClosedTests(unittest.TestCase):
    def test_complete_structured_result_is_accepted(self):
        value = valid_judgment()
        self.assertIs(judge.validate_judgment(value, ("A", "B")), value)

    def test_missing_dimension_fails_closed(self):
        value = valid_judgment()
        value["candidates"][0]["dimensions"].pop()
        with self.assertRaises(judge.JudgeValidationError):
            judge.validate_judgment(value, ("A", "B"))

    def test_missing_pair_fails_closed(self):
        value = valid_judgment()
        value["pairwise"] = []
        with self.assertRaises(judge.JudgeValidationError):
            judge.validate_judgment(value, ("A", "B"))

    def test_pass_with_error_fails_closed(self):
        value = valid_judgment()
        value["candidates"][0]["dimensions"][0].update(
            status="ERROR", severity="MAJOR")
        with self.assertRaises(judge.JudgeValidationError):
            judge.validate_judgment(value, ("A", "B"))

    def test_block_is_allowed_only_for_complete_evidence_abstention(self):
        value = valid_judgment()
        value["candidates"][0]["final_verdict"] = "BLOCK"
        for dimension in value["candidates"][0]["dimensions"]:
            dimension.update(status="NOT_EVALUABLE", severity="NONE")
        self.assertIs(judge.validate_judgment(value, ("A", "B")), value)
        value["candidates"][0]["dimensions"][0]["status"] = "PASS"
        with self.assertRaises(judge.JudgeValidationError):
            judge.validate_judgment(value, ("A", "B"))

    def test_judge_contract_has_no_api_transport(self):
        self.assertFalse(hasattr(judge, "call_judge"))


class ProductionRunnerTests(unittest.TestCase):
    def test_reasoning_wrapper_changes_only_reasoning_kwarg(self):
        original = {"temperature": 0.2, "max_output_tokens": 32768,
                    "timeout": 150.0, "model": "gemini-3.1-flash-lite", "label": "curation"}
        with patch.object(curation_p4.gemini_client, "call_json", return_value={"items": []}) as call:
            client = curation_p4.ReasoningClient("level:medium", 1)
            client("system", "user", **copy.deepcopy(original))
        call.assert_called_once_with("system", "user", **original, thinking_level="medium")

    def test_current_wrapper_preserves_request_options(self):
        original = {"temperature": 0.2, "max_output_tokens": 32768,
                    "timeout": 150.0, "model": "gemini-3.1-flash-lite", "label": "curation"}
        with patch.object(curation_p4.gemini_client, "call_json", return_value={"items": []}) as call:
            curation_p4.ReasoningClient("current", 1)("system", "user", **copy.deepcopy(original))
        call.assert_called_once_with("system", "user", **original)

    def test_future_production_reasoning_is_not_overridden(self):
        client = curation_p4.ReasoningClient("level:high", 1)
        with self.assertRaises(RuntimeError):
            client("system", "user", thinking_level="low")

    def test_hard_gate_and_judge_are_separate(self):
        articles = [{"hash": "h", "title": "t"}]
        gate = curation_p4.hard_gate(articles, {}, [], [])
        self.assertEqual(gate["status"], "BLOCK")
        self.assertFalse(hasattr(curation_p4.judge, "call_judge"))

    def test_preflight_is_zero_call_and_does_not_touch_production_data(self):
        tracked = [Path("curated.json"), Path("digest_queue.json"), Path("delivery_log.jsonl")]
        before = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in tracked}
        with tempfile.TemporaryDirectory(dir=".") as temp_dir, \
                patch.object(curation_p4.gemini_client, "call_json") as gemini, \
                patch("sys.argv", ["curation_p4.py", "--out", temp_dir]):
            self.assertEqual(curation_p4.main(), 0)
            report = json.loads((Path(temp_dir) / "preflight.json").read_text(encoding="utf-8"))
        self.assertEqual(report["live_calls_made"], {"openai": 0, "gemini": 0})
        gemini.assert_not_called()
        after = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in tracked}
        self.assertEqual(before, after)

    def test_not_proven_calibration_blocks_gemini_canary(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            out = Path(temp_dir)
            (out / "calibration-summary.json").write_text(
                json.dumps({"status": "NOT_PROVEN"}), encoding="utf-8")
            with patch.object(curation_p4, "run_gemini_canary") as run, \
                    patch("sys.argv", ["curation_p4.py", "--out", temp_dir,
                                       "--phase", "canary", "--approve-live-calls",
                                       "--max-gemini-calls-per-arm", "1"]):
                with self.assertRaises(SystemExit):
                    curation_p4.main()
            run.assert_not_called()


class CalibrationContractTests(unittest.TestCase):
    def test_real_gold_yields_twenty_cases_three_repeats(self):
        gold = json.loads(curation_p4.GOLD_PATH.read_text(encoding="utf-8"))
        requests = judge.calibration_requests(gold)
        self.assertEqual(len(requests), 60)
        self.assertEqual(len({row.case_id for row in requests}), 20)

    def test_thresholds_are_fixed_before_results(self):
        self.assertEqual(judge.CALIBRATION_THRESHOLDS["false_pass_max"], 0)
        self.assertEqual(judge.CALIBRATION_THRESHOLDS["unsafe_pass_max"], 0)
        self.assertEqual(judge.CALIBRATION_THRESHOLDS["identical_pair_position_bias_max"], 0.05)
        self.assertEqual(judge.CALIBRATION_THRESHOLDS["repeats"], 3)

    def test_manual_packets_export_without_internal_candidate_names(self):
        gold = json.loads(curation_p4.GOLD_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            exported = judge.export_manual_calibration_packets(gold, temp_dir)
            self.assertEqual(len(exported), 3)
            text = Path(exported[0]["path"]).read_text(encoding="utf-8")
            self.assertNotIn('"primary"', text)
            self.assertNotIn('"identical_control"', text)
            self.assertIn('"packet_id": "calibration-repeat-0"', text)

    def test_manual_answer_import_is_atomic_and_schema_checked(self):
        gold = json.loads(curation_p4.GOLD_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            judge.export_manual_calibration_packets(gold, temp_dir)
            manifest_path = Path(temp_dir) / "calibration-manifest.private.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            packet = manifest["packets"][0]
            answer = {
                "policy": judge.EVALUATOR_POLICY,
                "packet_id": packet["packet_id"],
                "judge_model_display": "latest reasoning model",
                "judgments": [],
            }
            for request in packet["requests"]:
                value = valid_judgment(tuple(request["aliases"]))
                answer["judgments"].append({"case_id": request["case_id"], **value})
            answer_path = Path(temp_dir) / "answer.json"
            answer_path.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
            rows = judge.import_manual_calibration_answer(answer_path, manifest_path)
            self.assertEqual(len(rows), 20)
            self.assertTrue(all(row["provenance"]["mode"] == "manual_chatgpt"
                                for row in rows))
            answer["judgments"][0]["candidates"][0]["dimensions"].pop()
            answer_path.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(judge.JudgeValidationError):
                judge.import_manual_calibration_answer(answer_path, manifest_path)


if __name__ == "__main__":
    unittest.main()
