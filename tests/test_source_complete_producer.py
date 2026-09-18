import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import news_bot
from tools import source_complete_evidence as evidence
from tools import source_complete_producer as producer


SOURCE_HASH = "a" * 64
BODY = "정부는 2026년 신규 원전 계약 계획을 공식 발표했다."


def article(description="정부가 2026년 신규 원전 계약 계획을 발표했다."):
    return {
        "hash": SOURCE_HASH,
        "title": "정부, 신규 원전 계약 계획 발표",
        "description": description,
        "link": "https://example.com/nuclear-plan",
        "pub": "2026-09-19T00:00:00Z",
        "domain": "example.com",
        "publisher": "Example News",
        "feed": "rss",
    }


def response_item():
    return {
        "idx": 0,
        "id": SOURCE_HASH[:8],
        "importance": "nice_to_know",
        "section": "international",
        "scope": "overseas",
        "category": "정책",
        "title_kr": "정부, 신규 원전 계약 계획 발표",
        "summary": "정부가 신규 원전 계약 계획을 발표했다.",
        "detail": "정부 발표에는 신규 원전 계약 추진 일정이 포함됐다.",
        "implication": "",
        "why_important": "",
        "tags": [],
        "topics": ["newbuild"],
        "countries": ["US"],
        "article_type": "policy",
        "event_date": "2026-09-19",
        "event_date_type": "announcement",
        "event_date_precision": "day",
        "event_date_source": "description",
        "related_reports": [],
        "features": {},
    }


def fake_transport(system_prompt, user_message, *, trace_sink, **kwargs):
    parsed = {"items": [response_item()]}
    raw = json.dumps(parsed, ensure_ascii=False)
    request = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        "generationConfig": {
            "temperature": kwargs.get("temperature"),
            "maxOutputTokens": kwargs.get("max_output_tokens"),
            "responseMimeType": "application/json",
        },
    }
    provider_response = {
        "candidates": [{
            "content": {"parts": [{"text": raw}]},
            "finishReason": "STOP",
        }],
        "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 10},
    }
    trace_sink({
        "schema_version": 1,
        "captured_at_epoch": 1_789_761_600.0,
        "endpoint": "https://example.invalid/generateContent",
        "request_payload": request,
        "provider_response": provider_response,
        "raw_model_output": raw,
        "parsed_output": parsed,
        "parser_result": {"status": "PASS", "mode": "json"},
        "detail": {"task": kwargs.get("label"), "retry_count": 0},
        "request_options": {},
    })
    return parsed


def body_provenance():
    return {
        SOURCE_HASH: {
            "body_origin": "offline-production-equivalent-fixture",
            "body_extraction_method": "fixture-exact-text",
            "body_extraction_version": "fixture-v1",
        }
    }


class ProducerEndToEndTests(unittest.TestCase):
    def run_curation(self, store, *, bodies=None, description=None):
        row = article() if description is None else article(description)
        prod = producer.SourceCompleteProducer(
            store, target=1, body_provenance=body_provenance())
        client = prod.traced_client(fake_transport)
        with patch.object(news_bot, "gemini_rest_available", return_value=True):
            output = news_bot.curate_batch(
                [row], [], bodies if bodies is not None else {SOURCE_HASH: BODY},
                client=client, evidence_sink=prod.record_curation_event)
        return prod, output

    def test_one_real_curation_path_promotes_one_source_complete_case(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            store = Path(temp_dir) / "store"
            prod, output = self.run_curation(store)
            report = prod.finalize()

            self.assertIn(SOURCE_HASH, output)
            self.assertEqual(report["eligible_after"], 1)
            self.assertEqual(report["status"], "TARGET_REACHED")
            self.assertEqual(report["observed_production_curation_calls"], 1)
            self.assertEqual(report["incremental_api_calls"], {"gemini": 0, "openai": 0})
            case_path = next((store / "cases").glob("*.json"))
            document = json.loads(case_path.read_text(encoding="utf-8"))
            validation = evidence.validate_case_document(document, store)
            self.assertTrue(validation.source_complete, validation.as_dict())
            self.assertEqual(document["production_input"]["article"], article())
            self.assertEqual(document["production_input"]["batch"]["position"], 0)
            self.assertEqual(document["production_output"]["parser_result"]["matched_by"], "id")

    def test_missing_body_fails_closed_without_writing_candidate_or_blobs(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            store = Path(temp_dir) / "store"
            prod, output = self.run_curation(store, bodies={})
            report = prod.finalize()

            self.assertIn(SOURCE_HASH, output, "service output must not depend on evidence eligibility")
            self.assertEqual(report["eligible_after"], 0)
            self.assertEqual(report["complete_candidates_in_memory"], 0)
            self.assertGreater(report["rejected_candidates"], 0)
            self.assertFalse((store / "cases").exists())
            self.assertFalse((store / "blobs").exists())

    def test_secret_is_rejected_before_any_raw_candidate_is_written(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            store = Path(temp_dir) / "store"
            prod, _output = self.run_curation(
                store, description="Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
            report = prod.finalize()
            self.assertEqual(report["eligible_after"], 0)
            self.assertTrue(any(
                "secret" in error
                for row in report["rejections"] for error in row.get("errors", [])))
            self.assertFalse(store.exists())

    def test_missing_transport_trace_cannot_be_reconstructed_afterward(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            prod = producer.SourceCompleteProducer(
                Path(temp_dir) / "store", target=1,
                body_provenance=body_provenance())
            event = {
                "transport_trace": None,
                "normalized_outputs": {SOURCE_HASH: {"summary": "valid"}},
            }
            prod.record_curation_event(event)
            report = prod.finalize()
            self.assertEqual(report["eligible_after"], 0)
            self.assertEqual(report["rejections"][0]["reason"], "missing_transport_trace")

    def test_validated_temporary_store_consolidates_without_api_calls(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            root = Path(temp_dir)
            temporary = root / "temporary"
            destination = root / "permanent"
            prod, _output = self.run_curation(temporary)
            self.assertEqual(prod.finalize()["eligible_after"], 1)

            report = producer.consolidate_stores(
                [temporary], destination, target=1)
            self.assertEqual(report["eligible_after"], 1)
            self.assertEqual(report["api_calls"], {"gemini": 0, "openai": 0})
            document = json.loads(next((destination / "cases").glob("*.json")).read_text(
                encoding="utf-8"))
            self.assertTrue(evidence.validate_case_document(
                document, destination).source_complete)


class ProducerActivationTests(unittest.TestCase):
    def test_capture_is_off_unless_explicit_flag_is_on(self):
        with patch.dict(os.environ, {
            producer.STORE_ENV: ".eval/gemini-reasoning-v2/source-complete",
        }, clear=False):
            os.environ.pop(producer.CAPTURE_FLAG, None)
            self.assertIsNone(producer.producer_from_environment([article()], {SOURCE_HASH: BODY}))

    def test_capture_store_is_restricted_to_eval_directory(self):
        with patch.dict(os.environ, {
            producer.CAPTURE_FLAG: "on",
            producer.STORE_ENV: "curated-source-complete",
            producer.TARGET_ENV: "30",
        }, clear=False):
            with self.assertRaises(producer.ProducerConfigurationError):
                producer.producer_from_environment([article()], {SOURCE_HASH: BODY})

    def test_plan_is_zero_call_and_reports_bounded_accumulation(self):
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            report = producer.collection_plan(Path(temp_dir) / "store", 30)
        self.assertEqual(report["live_api_calls_this_command"], {"gemini": 0, "openai": 0})
        self.assertEqual(report["incremental_api_calls"], {"gemini": 0, "openai": 0})
        self.assertEqual(report["remaining"], 30)
        self.assertEqual(report["minimum_future_successful_batches"], 2)
        self.assertIn("Gold judgment", report["forbidden_in_this_stage"])

    def test_public_workflows_do_not_upload_source_bodies(self):
        for path in Path(".github/workflows").glob("*.yml"):
            with self.subTest(path=path):
                self.assertNotIn(
                    "NUCLENS_SOURCE_COMPLETE", path.read_text(encoding="utf-8"),
                    "source-complete bodies require approved private/encrypted storage")


class BalancedAccumulationTests(unittest.TestCase):
    def test_existing_coverage_guides_the_next_selection(self):
        rows = [
            {"case_id": "event", "risk_buckets": ["event_boundary"]},
            {"case_id": "scope", "risk_buckets": ["scope"]},
        ]
        selected = evidence.select_balanced_candidates(
            rows, target=1, initial_counts={"event_boundary": 5})
        self.assertEqual(selected[0]["case_id"], "scope")


if __name__ == "__main__":
    unittest.main()
