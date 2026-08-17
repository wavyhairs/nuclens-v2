"""Source health persistence and non-blocking admin alert planning."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import operational_monitoring as monitor


T0 = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)


class SourceHealthTests(unittest.TestCase):
    def test_snapshot_distinguishes_failure_empty_and_missing(self):
        observations = monitor.source_observations(
            {
                "counts": {"IAEA": 12, "NSSC": 0, "WNN": 0},
                "kept": {"IAEA": 2},
                "errors": {"WNN": "HTTP 503"},
            },
            {"IAEA": "feed", "NSSC": "official", "WNN": "feed", "KHNP": "official"},
        )
        rows = {row["name"]: row for row in observations}
        self.assertEqual("ok", rows["IAEA"]["status"])
        self.assertEqual("empty", rows["NSSC"]["status"])
        self.assertEqual("failed", rows["WNN"]["status"])
        self.assertEqual("failed", rows["KHNP"]["status"])
        self.assertIn("not observed", rows["KHNP"]["error"])

    def test_health_persists_streaks_and_recovers(self):
        health = monitor.update_source_health(None, [{
            "name": "KHNP", "kind": "official", "status": "ok", "count": 10, "kept": 1,
        }], T0)
        row = health["sources"]["KHNP"]
        first_success = row["last_success_at"]
        self.assertEqual(0, row["consecutive_failures"])
        self.assertEqual(10, row["last_count"])

        for hours in (1, 2):
            health = monitor.update_source_health(health, [{
                "name": "KHNP", "kind": "official", "status": "failed",
                "count": 0, "kept": 0, "error": "timeout",
            }], T0 + timedelta(hours=hours))
        row = health["sources"]["KHNP"]
        self.assertEqual(2, row["consecutive_failures"])
        self.assertEqual(first_success, row["last_success_at"])
        self.assertEqual("timeout", row["last_error"])

        health = monitor.update_source_health(health, [{
            "name": "KHNP", "kind": "official", "status": "empty", "count": 0, "kept": 0,
        }], T0 + timedelta(hours=3))
        row = health["sources"]["KHNP"]
        self.assertEqual(0, row["consecutive_failures"])
        self.assertEqual(1, row["consecutive_empty"])
        self.assertEqual("", row["last_error"])

    def test_unobserved_sources_are_preserved(self):
        health = monitor.update_source_health(None, [
            {"name": "A", "status": "ok", "count": 1},
            {"name": "B", "status": "ok", "count": 2},
        ], T0)
        health = monitor.update_source_health(health, [
            {"name": "A", "status": "failed", "error": "x"},
        ], T0 + timedelta(hours=1))
        self.assertEqual(1, health["sources"]["B"]["checks"])
        self.assertEqual("ok", health["sources"]["B"]["last_status"])

    def test_alerts_use_different_failure_and_empty_thresholds(self):
        health = {"sources": {
            "NSSC": {"kind": "official", "consecutive_failures": 2,
                     "last_checked_at": "run-2", "last_error": "HTTP 500"},
            "IAEA": {"kind": "feed", "consecutive_empty": 3,
                     "last_checked_at": "run-3"},
            "WNN": {"kind": "feed", "consecutive_empty": 2,
                    "last_checked_at": "run-2"},
        }}
        signals = monitor.source_health_signals(health)
        self.assertEqual({"source:NSSC:failure", "source:IAEA:empty"},
                         {signal.key for signal in signals})


class QualitySignalTests(unittest.TestCase):
    def test_data_gate_builds_actionable_signals(self):
        record = {
            "record_type": "data_quality_gate",
            "generated_at": "2026-08-17T09:00:00+09:00",
            "tracking": {"applicable": True, "below_target": True, "rate": 0.1,
                         "target": 0.2, "window_briefings": 7},
            "topic_weeks": {"flow_ratio": 2.5, "flow_visible": False,
                            "slope_ratio": 1.2, "slope_visible": True,
                            "totals": [40, 100], "limit": 2.0},
        }
        signals = monitor.data_gate_signals(record)
        self.assertEqual({"quality:tracking-rate", "quality:topic-weeks"},
                         {signal.key for signal in signals})
        self.assertTrue(all(signal.min_occurrences == 2 for signal in signals))

    def test_archive_integrity_quarantine_alerts_immediately(self):
        signals = monitor.data_gate_signals({
            "generated_at": "2026-08-17T08:00:00+09:00",
            "archive_quality": {
                "quarantined": 2,
                "quarantine_samples": [{"hash": "bad-title-1"}, {"hash": "bad-title-2"}],
            },
            "tracking": {"applicable": False},
            "topic_weeks": {},
        })

        self.assertEqual(1, len(signals))
        self.assertEqual("quality:archive-integrity", signals[0].key)
        self.assertEqual("critical", signals[0].severity)
        self.assertEqual(1, signals[0].min_occurrences)

    def test_archive_integrity_sanitize_is_an_immediate_stable_signal(self):
        signals = monitor.data_gate_signals({
            "generated_at": "2026-08-17T08:00:00+09:00",
            "observation_id": "github-run:123",
            "archive_quality": {
                "quarantined": 0,
                "sanitized": 1,
                "sanitize_samples": [{"hash": "bad-date-1",
                                      "codes": ["invalid_event_date"]}],
            },
            "tracking": {"applicable": False},
            "topic_weeks": {},
        })

        self.assertEqual(1, len(signals))
        self.assertEqual("quality:archive-integrity", signals[0].key)
        self.assertEqual("warning", signals[0].severity)
        self.assertEqual("github-run:123", signals[0].observation_id)
        self.assertEqual(1, signals[0].min_occurrences)
        self.assertIn("정제 1건", signals[0].detail)

    def test_archive_quarantine_and_sanitize_share_one_escalating_signal(self):
        signals = monitor.data_gate_signals({
            "observation_id": "github-run:124",
            "archive_quality": {"quarantined": 2, "sanitized": 3},
            "tracking": {"applicable": False}, "topic_weeks": {},
        })
        self.assertEqual(1, len(signals))
        self.assertEqual("critical", signals[0].severity)
        self.assertIn("격리 2건", signals[0].detail)
        self.assertIn("정제 3건", signals[0].detail)

    def test_insufficient_topic_window_does_not_alert(self):
        signals = monitor.data_gate_signals({
            "generated_at": "x", "tracking": {"applicable": False},
            "topic_weeks": {"flow_ratio": None, "flow_visible": False,
                            "slope_ratio": None, "slope_visible": False},
        })
        self.assertEqual([], signals)

    def test_latest_record_ignores_unrelated_log_rows(self):
        rows = [
            {"record_type": "data_quality_gate", "generated_at": "2026-08-16"},
            {"record_type": "curation_failure", "generated_at": "2026-08-18"},
            {"record_type": "data_quality_gate", "generated_at": "2026-08-17"},
        ]
        self.assertEqual("2026-08-17", monitor.latest_data_gate_record(rows)["generated_at"])


class WebPipelineSignalTests(unittest.TestCase):
    def test_build_failure_is_reported_without_downstream_noise(self):
        signals = monitor.web_pipeline_signals({
            "web_build": "failure", "data_gate": "skipped", "web_deploy": "skipped",
        }, observation_id="daily-brief:1")
        self.assertEqual(1, len(signals))
        self.assertEqual("quality:web-pipeline-failure", signals[0].key)
        self.assertEqual("daily-brief:1", signals[0].observation_id)
        self.assertIn("웹 데이터 빌드=failure", signals[0].detail)
        self.assertNotIn("Cloudflare", signals[0].detail)

    def test_metrics_and_deploy_failures_can_be_reported_together(self):
        signals = monitor.web_pipeline_signals({
            "web_build": "success", "data_gate": "failure", "web_deploy": "failure",
        }, observation_id="daily-brief:2")
        self.assertEqual(1, len(signals))
        self.assertIn("데이터 품질 기록=failure", signals[0].detail)
        self.assertIn("Cloudflare 배포·스모크=failure", signals[0].detail)

    def test_all_success_has_no_failure_signal(self):
        self.assertEqual([], monitor.web_pipeline_signals({
            "web_build": "success", "data_gate": "success", "web_deploy": "success",
        }, observation_id="daily-brief:3"))

    def test_collection_crash_is_visible_without_a_source_snapshot(self):
        signals = monitor.collection_pipeline_signals(
            "failure", observation_id="crawl:42")
        self.assertEqual(1, len(signals))
        self.assertEqual("source:collection-pipeline-failure", signals[0].key)
        self.assertEqual("critical", signals[0].severity)
        self.assertEqual("crawl:42", signals[0].observation_id)

    def test_successful_collection_has_no_failure_signal(self):
        self.assertEqual([], monitor.collection_pipeline_signals(
            "success", observation_id="crawl:43"))


class AlertLifecycleTests(unittest.TestCase):
    def signal(self, observation: str, *, severity: str = "warning") -> monitor.AlertSignal:
        return monitor.AlertSignal(
            key="quality:test", scope="quality", title="테스트 경고", detail="상세",
            severity=severity, observation_id=observation, min_occurrences=2,
        )

    def test_requires_distinct_consecutive_observations(self):
        state, due = monitor.evaluate_alerts([self.signal("run-1")], None,
                                             evaluated_scopes={"quality"}, now=T0)
        self.assertEqual([], due)
        # Workflow retry: the same metrics record must not count twice.
        state, due = monitor.evaluate_alerts([self.signal("run-1")], state,
                                             evaluated_scopes={"quality"}, now=T0 + timedelta(minutes=5))
        self.assertEqual([], due)
        self.assertEqual(1, state["items"]["quality:test"]["consecutive"])

        state, due = monitor.evaluate_alerts([self.signal("run-2")], state,
                                             evaluated_scopes={"quality"}, now=T0 + timedelta(days=1))
        self.assertEqual(["quality:test"], [signal.key for signal in due])

    def test_notification_cooldown_recovery_and_recurrence(self):
        state, _ = monitor.evaluate_alerts([self.signal("r1")], None,
                                           evaluated_scopes={"quality"}, now=T0)
        state, due = monitor.evaluate_alerts([self.signal("r2")], state,
                                             evaluated_scopes={"quality"}, now=T0 + timedelta(hours=1))
        state = monitor.mark_notified(state, due, T0 + timedelta(hours=1))

        state, due = monitor.evaluate_alerts([self.signal("r3")], state,
                                             evaluated_scopes={"quality"}, now=T0 + timedelta(hours=2))
        self.assertEqual([], due)
        state, due = monitor.evaluate_alerts([self.signal("r4")], state,
                                             evaluated_scopes={"quality"}, now=T0 + timedelta(hours=26))
        self.assertEqual(1, len(due))
        state = monitor.mark_notified(state, due, T0 + timedelta(hours=26))

        state, due = monitor.evaluate_alerts([], state, evaluated_scopes={"quality"},
                                             now=T0 + timedelta(hours=27))
        self.assertEqual([], due)
        self.assertFalse(state["items"]["quality:test"]["active"])
        state, due = monitor.evaluate_alerts([self.signal("r5")], state,
                                             evaluated_scopes={"quality"}, now=T0 + timedelta(hours=28))
        self.assertEqual([], due)
        self.assertEqual(1, state["items"]["quality:test"]["consecutive"])

    def test_severity_escalation_bypasses_cooldown(self):
        first = monitor.AlertSignal("source:x", "source", "x", "d", "warning", "r1", 1)
        state, due = monitor.evaluate_alerts([first], None, evaluated_scopes={"source"}, now=T0)
        state = monitor.mark_notified(state, due, T0)
        critical = monitor.AlertSignal("source:x", "source", "x", "worse", "critical", "r2", 1)
        state, due = monitor.evaluate_alerts([critical], state, evaluated_scopes={"source"},
                                             now=T0 + timedelta(hours=1))
        self.assertEqual(1, len(due))

    def test_source_evaluation_does_not_resolve_quality_scope(self):
        state = {"items": {"quality:x": {"scope": "quality", "active": True,
                                          "consecutive": 3}}}
        state, _ = monitor.evaluate_alerts([], state, evaluated_scopes={"source"}, now=T0)
        self.assertTrue(state["items"]["quality:x"]["active"])

    def test_sender_failure_is_non_blocking_and_retryable(self):
        signal = monitor.AlertSignal("x", "quality", "title", "detail", min_occurrences=1)
        state, due = monitor.evaluate_alerts([signal], None,
                                             evaluated_scopes={"quality"}, now=T0)

        def fail(_message):
            raise OSError("network down")

        unchanged, result = monitor.notify_alerts(state, due, fail, now=T0)
        self.assertFalse(result["sent"])
        self.assertNotIn("last_notified_at", unchanged["items"]["x"])

        messages = []
        sent, result = monitor.notify_alerts(unchanged, due,
                                             lambda message: messages.append(message) or {"ok": True},
                                             now=T0)
        self.assertTrue(result["sent"])
        self.assertIn("last_notified_at", sent["items"]["x"])
        self.assertIn("Nuclens+ 운영 품질 알림", messages[0])

    def test_unsent_alert_survives_recovery_and_next_day(self):
        signal = monitor.AlertSignal(
            "source:x", "source", "수집 장애", "연속 실패", observation_id="run-2",
            min_occurrences=1,
        )
        state, due = monitor.evaluate_alerts(
            [signal], None, evaluated_scopes={"source"}, now=T0)
        self.assertEqual(1, len(due))
        self.assertTrue(state["items"]["source:x"]["pending_notification"])

        # The source recovers before an admin sender is available. The incident
        # is resolved operationally but its unsent notification remains due.
        state, due = monitor.evaluate_alerts(
            [], state, evaluated_scopes={"source"}, now=T0 + timedelta(days=1))
        self.assertFalse(state["items"]["source:x"]["active"])
        self.assertEqual(["source:x"], [row.key for row in due])

        state = monitor.mark_notified(state, due, T0 + timedelta(days=1))
        state, due = monitor.evaluate_alerts(
            [], state, evaluated_scopes=set(), now=T0 + timedelta(days=2))
        self.assertEqual([], due)
        self.assertFalse(state["items"]["source:x"]["pending_notification"])


if __name__ == "__main__":
    unittest.main()
