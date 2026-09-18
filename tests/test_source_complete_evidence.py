import ast
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gemini_client
from tools import source_complete_evidence as evidence


def valid_payload(case_id="case-a", source_hash="a" * 16, body="full source body"):
    title = "원전 계약 계획 발표"
    description = "정부가 원전 계약 계획을 발표했다."
    item = {"id": source_hash[:8], "idx": 0, "summary": "계약 계획 발표"}
    raw = json.dumps({"items": [item]}, ensure_ascii=False)
    request = {
        "system_instruction": {"parts": [{"text": "system"}]},
        "contents": [{"parts": [{"text": (
            f"[0|{source_hash[:8]}] {title}\n"
            f"요약: {description}\n출처: example.com\n본문: {body}"
        )}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 32768},
    }
    return {
        "case_id": case_id,
        "source": {
            "source_hash": source_hash,
            "canonical_url": f"https://example.com/{case_id}",
            "publisher": "Example News", "domain": "example.com",
            "published_at": "2026-09-19T00:00:00Z",
            "captured_at": "2026-09-19T01:00:00Z",
            "source_type": "article",
            "provenance_version": evidence.PROVENANCE_VERSION,
            "title": title, "description": description, "body": body,
            "body_origin": "production_article_text",
            "body_extraction_method": "readability-clean-text",
            "body_extraction_version": "v1",
        },
        "production": {
            "article": {
                "hash": source_hash, "title": title, "description": description,
                "publisher": "Example News", "domain": "example.com",
                "link": f"https://example.com/{case_id}",
            },
            "reports_context": [],
            "batch": {"article_hashes": [source_hash], "position": 0},
            "request_builder_fingerprint": "builder-v1-deadbeef",
        },
        "request_payload": request,
        "output": {
            "provider_response": {
                "candidates": [{"content": {"parts": [{"text": raw}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            },
            "raw_model_output": raw,
            "parsed_items": [item],
            "normalized_output": {"summary": "계약 계획 발표", "curation_status": "reviewed"},
            "parser_result": {"status": "PASS", "matched_by": "id"},
            "lifecycle": {
                "regenerated": False, "split": False,
                "quarantined": False, "lost": False,
            },
            "validation": {"status": "PASS", "errors": []},
        },
        "evaluation_evidence": {"included_fields": list(evidence.EVALUATION_FIELDS)},
        "selection_metadata": {
            "risk_buckets": ["stage", "historical_error_prone"],
            "selection_policy": "risk-balanced-v1",
        },
    }


class CompletenessGateTests(unittest.TestCase):
    def test_complete_case_is_eligible(self):
        result = evidence.completeness_gate(valid_payload())
        self.assertTrue(result.source_complete)
        self.assertEqual(result.status, evidence.SOURCE_COMPLETE_ELIGIBLE)

    def test_required_evidence_missing_fails_closed(self):
        payload = valid_payload()
        del payload["production"]["reports_context"]
        result = evidence.completeness_gate(payload)
        self.assertFalse(result.source_complete)
        self.assertEqual(result.status, evidence.UNSCORABLE)
        self.assertIn("$.production.reports_context", result.missing)

    def test_title_only_case_without_body_is_unscorable(self):
        payload = valid_payload(body="")
        result = evidence.completeness_gate(payload)
        self.assertFalse(result.source_complete)
        self.assertIn("$.source.body", result.missing)

    def test_secret_material_is_rejected_before_any_case_is_written(self):
        payload = valid_payload()
        payload["request_payload"]["api_key"] = "AIza" + "x" * 28
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            result = evidence.promote_case(payload, Path(temp_dir))
            self.assertFalse(result["source_complete"])
            self.assertFalse((Path(temp_dir) / "cases").exists())
        self.assertTrue(any("secret" in error for error in result["errors"]))

    def test_article_batch_and_serialized_request_must_correspond(self):
        for mutation in ("article", "batch", "request"):
            with self.subTest(mutation=mutation):
                payload = valid_payload()
                if mutation == "article":
                    payload["production"]["article"]["hash"] = "b" * 16
                elif mutation == "batch":
                    payload["production"]["batch"]["article_hashes"] = ["b" * 16]
                else:
                    payload["request_payload"]["contents"][0]["parts"][0]["text"] = "different"
                self.assertFalse(evidence.completeness_gate(payload).source_complete)

    def test_raw_output_must_equal_parsed_items(self):
        payload = valid_payload()
        payload["output"]["parsed_items"][0]["summary"] = "changed"
        result = evidence.completeness_gate(payload)
        self.assertFalse(result.source_complete)
        self.assertIn("raw_model_output items do not equal parsed_items", result.errors)

    def test_provider_response_must_contain_exact_raw_output(self):
        payload = valid_payload()
        payload["output"]["provider_response"]["candidates"][0]["content"]["parts"][0]["text"] = "{}"
        result = evidence.completeness_gate(payload)
        self.assertFalse(result.source_complete)
        self.assertIn("provider response does not contain the exact raw_model_output", result.errors)

    def test_evaluation_evidence_subset_is_explicit_and_fixed(self):
        payload = valid_payload()
        payload["evaluation_evidence"]["included_fields"].append("production.secret_context")
        result = evidence.completeness_gate(payload)
        self.assertFalse(result.source_complete)
        self.assertIn("evaluation evidence subset is not the fixed explicit field list", result.errors)

    def test_selection_metadata_cannot_contain_gold_answer(self):
        payload = valid_payload()
        payload["selection_metadata"]["verdict"] = "PASS"
        self.assertFalse(evidence.completeness_gate(payload).source_complete)


class ContentAddressedStoreTests(unittest.TestCase):
    def test_body_hash_and_request_fingerprint_are_stable(self):
        self.assertEqual(evidence.body_fingerprint("same"), evidence.body_fingerprint("same"))
        self.assertNotEqual(evidence.body_fingerprint("same\n"), evidence.body_fingerprint("same"))
        self.assertEqual(
            evidence.request_fingerprint({"b": 2, "a": 1}),
            evidence.request_fingerprint({"a": 1, "b": 2}),
        )

    def test_identical_body_is_stored_once_without_changing_case_identity(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            store = Path(temp_dir)
            first = evidence.promote_case(valid_payload("case-a", "a" * 16), store)
            second = evidence.promote_case(valid_payload("case-b", "b" * 16), store)
            self.assertTrue(first["source_complete"] and second["source_complete"])
            self.assertEqual(len(list((store / "blobs" / "body").glob("*.txt"))), 1)
            self.assertEqual(len(list((store / "cases").glob("*.json"))), 2)

    def test_tampered_blob_invalidates_persisted_case(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            store = Path(temp_dir)
            result = evidence.promote_case(valid_payload(), store)
            document = json.loads(Path(result["case_path"]).read_text(encoding="utf-8"))
            body_path = store / document["source_content"]["body_ref"]["path"]
            body_path.write_text("tampered", encoding="utf-8")
            self.assertFalse(evidence.validate_case_document(document, store).source_complete)

    def test_tampered_case_manifest_invalidates_fingerprint(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            store = Path(temp_dir)
            result = evidence.promote_case(valid_payload(), store)
            document = json.loads(Path(result["case_path"]).read_text(encoding="utf-8"))
            document["source_provenance"]["publisher"] = "Changed"
            validation = evidence.validate_case_document(document, store)
            self.assertFalse(validation.source_complete)
            self.assertIn("case fingerprint mismatch", validation.errors)

    def test_promotion_does_not_mutate_production_or_make_api_calls(self):
        tracked = [Path("curated.json"), Path("digest_queue.json"), Path("delivery_log.jsonl"),
                   Path("tests/fixtures/gemini_reasoning/frozen_requests.json")]
        before = {path: path.read_bytes() for path in tracked}
        with tempfile.TemporaryDirectory(dir=".") as temp_dir, \
                patch.object(gemini_client, "call_json") as gemini:
            result = evidence.promote_case(valid_payload(), Path(temp_dir))
            self.assertTrue(result["source_complete"])
            gemini.assert_not_called()
        after = {path: path.read_bytes() for path in tracked}
        self.assertEqual(before, after)

    def test_evidence_module_has_no_openai_or_network_transport(self):
        tree = ast.parse(Path(evidence.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("openai", imported)
        self.assertNotIn("urllib", imported)
        self.assertNotIn("requests", imported)


class HistoricalAndSelectionTests(unittest.TestCase):
    def test_historical_incomplete_gold_never_enters_source_complete_pool(self):
        gold = json.loads(Path(
            "tests/fixtures/gemini_reasoning/curation_gold.json"
        ).read_text(encoding="utf-8"))
        registry = evidence.historical_registry(gold)
        frozen = json.loads(Path(
            "tests/fixtures/gemini_reasoning/source_complete/historical_registry.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(registry, frozen)
        self.assertEqual(len(registry["cases"]), 20)
        self.assertTrue(all(row["history_status"] == evidence.HISTORICAL_ONLY
                            for row in registry["cases"]))
        self.assertTrue(all(row["source_complete_status"] == evidence.UNSCORABLE
                            and not row["calibration_eligible"]
                            for row in registry["cases"]))
        by_id = {row["case_id"]: row for row in registry["cases"]}
        for case in gold["cases"]:
            if case["id"] in by_id:
                self.assertEqual(by_id[case["id"]]["preserved_human_label"],
                                 case["human_label"])

    def test_balanced_selection_is_deterministic_and_answer_free(self):
        candidates = [
            {"case_id": f"case-{index}", "risk_buckets": [bucket], "selection_note": "not Gold"}
            for index, bucket in enumerate(evidence.RISK_BUCKETS)
        ]
        first = evidence.select_balanced_candidates(candidates, target=6)
        second = evidence.select_balanced_candidates(reversed(candidates), target=6)
        self.assertEqual([row["case_id"] for row in first], [row["case_id"] for row in second])
        self.assertTrue(all(not set(row).intersection({"gold", "verdict", "answer"}) for row in first))


class StorageEstimateTests(unittest.TestCase):
    def test_capture_estimate_reports_required_projections_and_dedup(self):
        payload = valid_payload()
        record = {
            "detail": {"task": "curation", "retry_count": 0},
            "request_body": payload["request_payload"],
            "response": payload["output"]["provider_response"],
        }
        report = evidence.estimate_capture_storage([record, copy.deepcopy(record)])
        self.assertEqual(report["cases"], 2)
        self.assertEqual(set(report["projected_mib_before_dedup"]), {"20", "30", "100"})
        self.assertEqual(set(report["projected_mib_after_dedup"]), {"20", "30", "100"})
        self.assertGreater(report["body_share_after_dedup"], 0)
        self.assertGreaterEqual(report["measured_mib_before_dedup"],
                                report["measured_mib_after_dedup"])
        self.assertEqual(report["measurement_basis"],
                         "historical_capture_size_only_not_eligible")


if __name__ == "__main__":
    unittest.main()
