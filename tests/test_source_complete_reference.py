import json
import tempfile
import unittest
from pathlib import Path

from tests.test_source_complete_evidence import valid_payload
from tools import curation_p4_judge as judge
from tools import source_complete_evidence as evidence
from tools import source_complete_reference as reference


class SourceCompleteReferenceTests(unittest.TestCase):
    def _store(self, root: Path) -> Path:
        store = root / "store"
        result = evidence.promote_case(valid_payload(), store)
        self.assertTrue(result["source_complete"])
        return store

    def _answer(self, packet: dict, *, label: str = "PASS") -> dict:
        case = packet["cases"][0]
        return {
            "policy": reference.POLICY,
            "reviewer": {"type": "codex_agent", "display": "Codex source review"},
            "judgments": [{
                "case_id": case["case_id"],
                "case_fingerprint": case["case_fingerprint"],
                "label": label,
                "error_dimensions": [] if label == "PASS" else ["stage"],
                "rationale": "The output matches the supplied source." if label == "PASS"
                             else "The output overstates the project stage.",
                "required_repair": None if label == "PASS" else "Use the planned stage.",
            }],
        }

    def test_round_trip_creates_source_complete_calibration_gold(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            root = Path(temp_dir)
            store = self._store(root)
            packet_path = root / "packet.json"
            reference.export_packet(store, packet_path)
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            answer_path = root / "answer.json"
            answer_path.write_text(json.dumps(self._answer(packet)), encoding="utf-8")
            gold_path = root / "gold.json"
            result = reference.import_answer(store, answer_path, gold_path)
            gold = json.loads(gold_path.read_text(encoding="utf-8"))
            self.assertEqual(result["label_counts"], {"PASS": 1})
            self.assertEqual(gold["reviewer"]["type"], "codex_agent")
            self.assertEqual(len(judge.calibration_requests(gold)), 3)

    def test_fingerprint_drift_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            root = Path(temp_dir)
            store = self._store(root)
            packet_path = root / "packet.json"
            reference.export_packet(store, packet_path)
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            answer = self._answer(packet)
            answer["judgments"][0]["case_fingerprint"] = "0" * 64
            answer_path = root / "answer.json"
            answer_path.write_text(json.dumps(answer), encoding="utf-8")
            with self.assertRaisesRegex(reference.ReferenceValidationError, "fingerprint"):
                reference.import_answer(store, answer_path, root / "gold.json")

    def test_pass_with_error_dimension_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            root = Path(temp_dir)
            store = self._store(root)
            packet_path = root / "packet.json"
            reference.export_packet(store, packet_path)
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            answer = self._answer(packet)
            answer["judgments"][0]["error_dimensions"] = ["scope"]
            answer_path = root / "answer.json"
            answer_path.write_text(json.dumps(answer), encoding="utf-8")
            with self.assertRaisesRegex(reference.ReferenceValidationError, "PASS"):
                reference.import_answer(store, answer_path, root / "gold.json")


if __name__ == "__main__":
    unittest.main()
