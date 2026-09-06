import json
import tempfile
import unittest
from pathlib import Path

from tools import sol_provisional_gold


class SolProvisionalGoldTests(unittest.TestCase):
    def test_packages_include_only_pending_cases_and_no_model_answer(self):
        with tempfile.TemporaryDirectory() as temp:
            curation = sol_provisional_gold.prepare("curation", Path(temp))
            self.assertEqual(curation["case_count"], 39)
            rows = sol_provisional_gold.read_jsonl(Path(curation["input"]))
            self.assertEqual(len(rows), 39)
            self.assertNotIn("human_label", rows[0])
            self.assertIn("nuclens_generated", rows[0])

    def test_strict_positive_contract_requires_none_only(self):
        base = {"case_id": "x", "provisional_label": "SUPPORTED",
                "error_types": ["none"], "confidence": "high",
                "reason": "근거와 주장이 일치한다.",
                "evidence_reference": "source title"}
        clean = sol_provisional_gold.validate_row("semantic", base, {"x"})
        self.assertEqual(clean["provisional_label"], "SUPPORTED")
        with self.assertRaises(ValueError):
            sol_provisional_gold.validate_row(
                "semantic", {**base, "error_types": ["contradiction"]}, {"x"})

    def test_import_marks_every_row_not_human_reviewed_and_prioritizes_hard(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sol.jsonl"
            target = Path(temp) / "sidecar.json"
            fixture = json.loads((sol_provisional_gold.FIXTURES /
                                  "semantic_gold.json").read_text("utf-8"))
            case_id = next(case["id"] for case in fixture["cases"]
                           if case["label_status"] == "HUMAN_LABEL_REQUIRED")
            row = {"case_id": case_id, "provisional_label": "AMBIGUOUS",
                   "error_types": ["causality"], "confidence": "low",
                   "reason": "관계 근거가 부족하다.",
                   "evidence_reference": "verified evidence claims"}
            source.write_text(json.dumps(row, ensure_ascii=False) + "\n", "utf-8")
            report = sol_provisional_gold.import_provisional(
                "semantic", source, target)
            stored = json.loads(target.read_text("utf-8"))
            self.assertFalse(stored["labels"][case_id]["human_reviewed"])
            self.assertGreaterEqual(stored["labels"][case_id]["review_priority"], 60)
            self.assertEqual(report["hard_cases"], 1)

    def test_unknown_duplicate_and_extra_fields_fail_closed(self):
        row = {"case_id": "unknown", "provisional_label": "CORRECT",
               "error_types": ["none"], "confidence": "high",
               "reason": "문제가 없다.", "evidence_reference": "source",
               "human_reviewed": True}
        with self.assertRaises(ValueError):
            sol_provisional_gold.validate_row("curation", row, {"known"})


if __name__ == "__main__":
    unittest.main()
