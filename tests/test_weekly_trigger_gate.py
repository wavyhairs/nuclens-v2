import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import weekly_trigger_gate as gate  # noqa: E402


class WeeklyTriggerGateTests(unittest.TestCase):
    NOW = datetime.fromisoformat("2026-08-28T09:10:00+00:00")  # Friday 18:10 KST

    def decide(self, event: str, *, conclusion: str = "success",
               report_status: str | None = None, channel_status: str | None = None,
               now: datetime | None = None, trigger_source: str = ""):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            if report_status:
                (base / "weekly_reports.json").write_text(json.dumps({
                    "reports": {"2026-W35": {
                        "week_id": "2026-W35",
                        "_automation": {"telegram": {"status": report_status}},
                    }}
                }), encoding="utf-8")
            if channel_status:
                (base / "channel_outbox.json").write_text(json.dumps({
                    "batches": [{"id": "weekly-2026-W35", "status": channel_status}]
                }), encoding="utf-8")
            return gate.decide(
                event_name=event, workflow_conclusion=conclusion,
                now=now or self.NOW,
                reports_path=base / "weekly_reports.json",
                channel_path=base / "channel_outbox.json",
                channel_required=True,
                trigger_source=trigger_source,
            )

    def test_primary_schedule_runs(self):
        self.assertTrue(self.decide("schedule")[0])

    # ── 늦은 schedule 도 같은 문 앞에 선다 ────────────────────────────────
    #
    # 이 저장소의 금요일 cron 은 제시간에 오지 않는다. 2026-09-04 와 09-11 은
    # 예약 08:07Z 가 12:41Z·12:47Z 에 떴고(4시간 반), 그 사이 저녁 crawl
    # recovery 가 이미 발송을 끝낸 뒤였다. 그런데도 게이트는 schedule 이라는
    # 이유만으로 통과시켰다.
    def test_late_schedule_does_not_rerun_after_confirmation(self):
        should_run, state, _ = self.decide(
            "schedule", report_status="sent", channel_status="sent")
        self.assertFalse(should_run)
        self.assertEqual(state, "delivery_already_confirmed")

    def test_schedule_still_runs_while_the_week_is_unsent(self):
        should_run, state, _ = self.decide("schedule", report_status="failed")
        self.assertTrue(should_run)
        self.assertEqual(state, "schedule_trigger_created")

    # ── 사람과 Worker 를 가르는 것은 이벤트 이름이 아니라 trigger_source 다 ──
    def test_manual_dispatch_runs_even_after_confirmation(self):
        """수동 실행의 목적이 그것이다 — 질문 없이 다시 돌려 보는 것."""
        should_run, state, _ = self.decide(
            "workflow_dispatch", report_status="sent", channel_status="sent")
        self.assertTrue(should_run)
        self.assertEqual(state, "manual_trigger")

    def test_backup_watchdog_dispatch_stops_at_the_same_door(self):
        should_run, state, _ = self.decide(
            "workflow_dispatch", report_status="sent", channel_status="sent",
            trigger_source="backup_watchdog")
        self.assertFalse(should_run)
        self.assertEqual(state, "delivery_already_confirmed")

    def test_backup_watchdog_dispatch_recovers_an_unsent_week(self):
        should_run, state, _ = self.decide(
            "workflow_dispatch", trigger_source="backup_watchdog")
        self.assertTrue(should_run)
        self.assertEqual(state, "schedule_missing_recovery")

    def test_backup_watchdog_dispatch_obeys_the_recovery_window(self):
        monday = datetime.fromisoformat("2026-08-31T09:10:00+00:00")
        should_run, state, _ = self.decide(
            "workflow_dispatch", trigger_source="backup_watchdog", now=monday)
        self.assertFalse(should_run)
        self.assertEqual(state, "outside_recovery_window")

    def test_missing_schedule_is_recovered_by_friday_crawl(self):
        should_run, state, _ = self.decide("workflow_run")
        self.assertTrue(should_run)
        self.assertEqual(state, "schedule_missing_recovery")

    def test_schedule_and_backup_do_not_run_twice_after_confirmation(self):
        should_run, state, _ = self.decide(
            "workflow_run", report_status="sent", channel_status="sent")
        self.assertFalse(should_run)
        self.assertEqual(state, "delivery_already_confirmed")

    def test_send_failure_is_retried(self):
        self.assertTrue(self.decide(
            "workflow_run", report_status="failed", channel_status="sent")[0])

    def test_failed_crawl_does_not_recover(self):
        self.assertFalse(self.decide("workflow_run", conclusion="failure")[0])

    def test_old_recovery_is_blocked(self):
        monday = datetime.fromisoformat("2026-08-31T09:10:00+00:00")
        self.assertFalse(self.decide("workflow_run", now=monday)[0])


if __name__ == "__main__":
    unittest.main()
