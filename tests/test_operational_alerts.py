"""CLI adapter tests use injected senders only; no external notification."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import operational_alerts as cli


NOW = datetime(2026, 8, 17, 4, 0, tzinfo=timezone.utc)


class OperationalAlertsCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sent = Path(self.tmp.name) / "sent.json"
        self.log = Path(self.tmp.name) / "delivery_log.jsonl"

    def write_sent(self, source_yield=None):
        self.sent.write_text(json.dumps({"sent": {}, "source_yield": source_yield or {}},
                                        ensure_ascii=False), encoding="utf-8")

    def test_source_snapshot_is_idempotent_across_workflow_retry(self):
        self.write_sent({
            "at": "run-1", "counts": {"원안위": 0}, "kept": {}, "errors": {"원안위": "500"},
        })
        first = cli.run(sent_path=self.sent, log_path=self.log, now=NOW,
                        expected_sources={"원안위": "official"})
        second = cli.run(sent_path=self.sent, log_path=self.log, now=NOW + timedelta(minutes=5),
                         expected_sources={"원안위": "official"})
        state = json.loads(self.sent.read_text(encoding="utf-8"))
        self.assertTrue(first["source_processed"])
        self.assertFalse(second["source_processed"])
        self.assertEqual(1, state["source_health"]["sources"]["원안위"]["checks"])

    def test_today_events_are_aggregated_and_sent_once(self):
        self.write_sent({"at": "run-2", "counts": {"IAEA": 5}, "kept": {}, "errors": {}})
        rows = [
            {"record_type": "curation_failure", "date": "2026-08-17",
             "generated_at": "2026-08-17T10:00:00+09:00", "lost": 12,
             "reasons": {"quota": 12}},
            {"record_type": "data_quality_gate", "date": "2026-08-17",
             "generated_at": "2026-08-17T11:00:00+09:00",
             "tracking": {"applicable": False},
             "topic_weeks": {"flow_ratio": 2.5, "flow_visible": False,
                             "slope_ratio": 1.1, "slope_visible": True,
                             "totals": [40, 100], "limit": 2.0}},
        ]
        self.log.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
                            encoding="utf-8")
        messages = []
        out = cli.run(sent_path=self.sent, log_path=self.log, notify=True,
                      sender=lambda text: messages.append(text) or {"ok": True},
                      expected_sources={"IAEA": "feed"}, now=NOW)
        self.assertTrue(out["sent"])
        self.assertEqual(1, len(messages))
        self.assertIn("큐레이션 유실", messages[0])
        # Topic-weeks needs a second distinct daily observation, so it is not
        # part of this first notification batch.
        self.assertNotIn("주제 흐름 지표", messages[0])

        again = cli.run(sent_path=self.sent, log_path=self.log, notify=True,
                        sender=lambda text: messages.append(text) or {"ok": True},
                        expected_sources={"IAEA": "feed"}, now=NOW + timedelta(minutes=5))
        self.assertFalse(again["sent"])
        self.assertEqual(1, len(messages))

    def test_failed_notification_is_retried_after_midnight(self):
        self.write_sent({"at": "run-midnight", "counts": {"IAEA": 5},
                         "kept": {}, "errors": {}})
        row = {"record_type": "quality_event", "date": "2026-08-17",
               "generated_at": "2026-08-17T22:00:00+09:00",
               "alert_key": "card-quarantine", "title": "카드 격리",
               "detail": "핵심 사실 충돌", "severity": "critical",
               "min_occurrences": 1}
        self.log.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
        first = cli.run(sent_path=self.sent, log_path=self.log, notify=True,
                        sender=lambda _text: (_ for _ in ()).throw(OSError("down")),
                        expected_sources={"IAEA": "feed"}, now=NOW)
        self.assertFalse(first["sent"])

        messages = []
        next_day = cli.run(
            sent_path=self.sent, log_path=self.log, notify=True,
            sender=lambda text: messages.append(text) or {"ok": True},
            expected_sources={"IAEA": "feed"}, now=NOW + timedelta(days=1))
        self.assertTrue(next_day["sent"])
        self.assertEqual(1, len(messages))
        self.assertIn("카드 격리", messages[0])

    def test_pipeline_failure_is_idempotent_and_resolves_on_success(self):
        self.write_sent()
        failed = {
            "web_build": "success", "data_gate": "failure", "web_deploy": "success",
        }
        messages = []
        first = cli.run(
            sent_path=self.sent, log_path=self.log, notify=True,
            sender=lambda text: messages.append(text) or {"ok": True},
            expected_sources={}, pipeline_outcomes=failed,
            pipeline_observation_id="daily-brief:100", now=NOW)
        self.assertTrue(first["sent"])
        self.assertEqual(1, len(messages))
        self.assertIn("데이터 품질 기록=failure", messages[0])

        retry = cli.run(
            sent_path=self.sent, log_path=self.log, notify=True,
            sender=lambda text: messages.append(text) or {"ok": True},
            expected_sources={}, pipeline_outcomes=failed,
            pipeline_observation_id="daily-brief:100", now=NOW + timedelta(minutes=5))
        self.assertFalse(retry["sent"])
        self.assertEqual(1, len(messages))

        recovered = cli.run(
            sent_path=self.sent, log_path=self.log, notify=True,
            sender=lambda text: messages.append(text) or {"ok": True},
            expected_sources={}, pipeline_outcomes={
                "web_build": "success", "data_gate": "success", "web_deploy": "success",
            }, pipeline_observation_id="daily-brief:101", now=NOW + timedelta(hours=1))
        self.assertFalse(recovered["sent"])
        state = json.loads(self.sent.read_text(encoding="utf-8"))
        self.assertFalse(state["operational_alerts"]["items"]
                         ["quality:web-pipeline-failure"]["active"])

    def test_unsent_pipeline_alert_survives_a_successful_next_run(self):
        self.write_sent()
        failed = {
            "web_build": "failure", "data_gate": "skipped", "web_deploy": "skipped",
        }
        first = cli.run(
            sent_path=self.sent, log_path=self.log, notify=True,
            sender=lambda _text: (_ for _ in ()).throw(OSError("down")),
            expected_sources={}, pipeline_outcomes=failed,
            pipeline_observation_id="daily-brief:200", now=NOW)
        self.assertFalse(first["sent"])

        messages = []
        recovered = cli.run(
            sent_path=self.sent, log_path=self.log, notify=True,
            sender=lambda text: messages.append(text) or {"ok": True},
            expected_sources={}, pipeline_outcomes={
                "web_build": "success", "data_gate": "success", "web_deploy": "success",
            }, pipeline_observation_id="daily-brief:201", now=NOW + timedelta(days=1))
        self.assertTrue(recovered["sent"])
        self.assertEqual(1, len(messages))
        state = json.loads(self.sent.read_text(encoding="utf-8"))
        item = state["operational_alerts"]["items"]["quality:web-pipeline-failure"]
        self.assertFalse(item["active"])
        self.assertFalse(item["pending_notification"])

    def test_collection_crash_alerts_even_without_a_new_source_snapshot(self):
        # The collector died before writing a new snapshot, so the file still
        # contains a prior successful run. It must not be ingested as fresh.
        self.write_sent({"at": "stale-run", "counts": {"IAEA": 5},
                         "kept": {"IAEA": 1}, "errors": {}})
        messages = []
        failed = cli.run(
            sent_path=self.sent, log_path=self.log, notify=True,
            sender=lambda text: messages.append(text) or {"ok": True},
            expected_sources={}, collection_outcome="failure",
            collection_observation_id="crawl:300", now=NOW)
        self.assertTrue(failed["sent"])
        self.assertFalse(failed["source_processed"])
        self.assertIn("뉴스 수집 파이프라인 실행 실패", messages[0])
        state = json.loads(self.sent.read_text(encoding="utf-8"))
        self.assertEqual({}, state["source_health"]["sources"])

        cli.run(
            sent_path=self.sent, log_path=self.log, notify=True,
            sender=lambda text: messages.append(text) or {"ok": True},
            expected_sources={}, collection_outcome="success",
            collection_observation_id="crawl:301", now=NOW + timedelta(hours=3))
        state = json.loads(self.sent.read_text(encoding="utf-8"))
        self.assertFalse(state["operational_alerts"]["items"]
                         ["source:collection-pipeline-failure"]["active"])

    def test_no_sender_logs_and_still_returns_success(self):
        self.write_sent({"at": "run-3", "counts": {"KHNP": 0}, "kept": {},
                         "errors": {"KHNP": "timeout"}})
        # Build a second distinct failed source observation by preloading one.
        state = json.loads(self.sent.read_text(encoding="utf-8"))
        state["source_health"] = {
            "version": 1, "last_snapshot_id": "run-2", "sources": {
                "KHNP": {"name": "KHNP", "kind": "official", "last_status": "failed",
                         "consecutive_failures": 1, "checks": 1, "failures": 1},
            }}
        self.sent.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        with patch.dict("os.environ", {
                "TELEGRAM_BOT_TOKEN": "present-but-no-admin-chat",
                "TELEGRAM_CHAT_ID": "public-channel-must-not-be-used",
        }, clear=True):
            out = cli.run(sent_path=self.sent, log_path=self.log, notify=True, sender=None,
                          expected_sources={"KHNP": "official"}, now=NOW)
        self.assertTrue(out["ok"])
        self.assertFalse(out["sent"])
        persisted = json.loads(self.sent.read_text(encoding="utf-8"))
        self.assertNotIn("last_notified_at",
                         persisted["operational_alerts"]["items"]["source:KHNP:failure"])

    def test_public_chat_id_is_never_an_admin_fallback(self):
        with patch.dict("os.environ", {
                "TELEGRAM_BOT_TOKEN": "token",
                "TELEGRAM_CHAT_ID": "public-channel",
        }, clear=True):
            self.assertIsNone(cli.telegram_sender_from_env())

    def test_official_rss_sources_keep_official_severity(self):
        specs = cli.expected_source_specs()
        self.assertEqual("official", specs["IAEA Top News"])
        self.assertEqual("official", specs["DOE"])
        self.assertEqual("feed", specs["WNN"])

    def test_admin_sender_targets_only_dedicated_chat(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read():
                return b'{"ok": true}'

        with patch.dict("os.environ", {
                "TELEGRAM_BOT_TOKEN": "token",
                "TELEGRAM_ADMIN_CHAT_ID": "admin-channel",
                "TELEGRAM_CHAT_ID": "public-channel",
        }, clear=True), patch.object(cli.urllib.request, "urlopen", return_value=Response()) as open_mock:
            sender = cli.telegram_sender_from_env()
            self.assertIsNotNone(sender)
            self.assertTrue(sender("warning")["ok"])
        request = open_mock.call_args.args[0]
        payload = cli.urllib.parse.parse_qs(request.data.decode("utf-8"))
        self.assertEqual(["admin-channel"], payload["chat_id"])
        self.assertNotIn("public-channel", request.data.decode("utf-8"))

    def test_malformed_sent_file_is_not_overwritten(self):
        self.sent.write_text("{broken", encoding="utf-8")
        before = self.sent.read_bytes()
        out = cli.run(sent_path=self.sent, log_path=self.log, now=NOW,
                      expected_sources={})
        self.assertFalse(out["ok"])
        self.assertEqual(before, self.sent.read_bytes())

    def test_daily_workflow_passes_each_web_step_outcome_to_admin_monitor(self):
        workflow = (ROOT / ".github" / "workflows" / "daily-brief.yml").read_text(
            encoding="utf-8")
        self.assertIn("id: web-build", workflow)
        self.assertIn("id: data-gate", workflow)
        self.assertIn("id: web-deploy", workflow)
        self.assertIn('--web-build-outcome "${{ steps.web-build.outcome }}"', workflow)
        self.assertIn('--data-gate-outcome "${{ steps.data-gate.outcome }}"', workflow)
        self.assertIn('--web-deploy-outcome "${{ steps.web-deploy.outcome }}"', workflow)
        self.assertIn('--pipeline-observation-id "daily-brief:${{ github.run_id }}"', workflow)
        self.assertIn('--collect-outcome "${{ steps.collect.outcome }}"', workflow)
        self.assertIn('--collection-observation-id "daily-brief:${{ github.run_id }}"', workflow)

        crawl = (ROOT / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        self.assertIn("id: collect", crawl)
        self.assertIn("python operational_alerts.py --notify", crawl)
        self.assertIn('--collect-outcome "${{ steps.collect.outcome }}"', crawl)
        self.assertIn('--collection-observation-id "crawl:${{ github.run_id }}"', crawl)
        self.assertIn("TELEGRAM_ADMIN_CHAT_ID", crawl)


if __name__ == "__main__":
    unittest.main()
