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
               now: datetime | None = None):
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
            )

    def test_primary_schedule_runs(self):
        self.assertTrue(self.decide("schedule")[0])

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
