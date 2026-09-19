import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import daily_brief_trigger_gate as gate  # noqa: E402


class DailyBriefTriggerGateTests(unittest.TestCase):
    def decide(self, event, *, conclusion="success", now="2026-08-27T21:10:00+00:00",
               outbox=None):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "outbox.json"
            if outbox is not None:
                path.write_text(json.dumps(outbox), encoding="utf-8")
            return gate.decide(
                event_name=event,
                workflow_conclusion=conclusion,
                now=datetime.fromisoformat(now),
                outbox_path=path,
            )

    def test_primary_schedule_runs_while_today_is_unsent(self):
        self.assertTrue(self.decide("schedule")[0])

    # 늦게 도착한 schedule 은 끝난 하루를 다시 만지지 않는다. GitHub cron 은
    # 예약 시각에 안 온다 — 그 사이 crawl 복구가 먼저 보내고 나면, 남은 것은
    # 이미 나간 브리핑 위에 상태를 덧쓰는 일뿐이다.
    def test_late_schedule_stops_once_today_is_sent(self):
        should_run, reason = self.decide("schedule", outbox={
            "date": "2026-08-28", "status": "sent"
        })
        self.assertFalse(should_run)
        self.assertIn("already sent", reason)

    def test_late_schedule_state_names_the_reason(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "outbox.json"
            path.write_text(json.dumps({"date": "2026-08-28", "status": "sent"}),
                            encoding="utf-8")
            state = gate.classify_state(
                event_name="schedule", should_run=False,
                now=datetime.fromisoformat("2026-08-27T21:10:00+00:00"),
                outbox_path=path)
        self.assertEqual(state, "delivery_already_confirmed")

    def test_manual_dispatch_always_runs(self):
        """수동 실행은 오늘 것이 나갔어도 돈다 — 검토용으로 돌리는 자리다."""
        self.assertTrue(self.decide("workflow_dispatch", outbox={
            "date": "2026-08-28", "status": "sent"
        })[0])

    def test_successful_crawl_recovers_missing_morning_brief(self):
        # 21:10 UTC == 06:10 KST
        self.assertTrue(self.decide("workflow_run", outbox={
            "date": "2026-08-27", "status": "sent"
        })[0])

    def test_fallback_skips_when_today_is_already_sent(self):
        self.assertFalse(self.decide("workflow_run", outbox={
            "date": "2026-08-28", "status": "sent"
        })[0])

    def test_fallback_skips_failed_crawl(self):
        self.assertFalse(self.decide("workflow_run", conclusion="failure")[0])

    def test_fallback_skips_outside_morning_window(self):
        # 09:10 UTC == 18:10 KST
        self.assertFalse(self.decide(
            "workflow_run", now="2026-08-28T09:10:00+00:00"
        )[0])


if __name__ == "__main__":
    unittest.main()
