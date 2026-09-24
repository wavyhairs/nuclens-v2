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


class AudioRecoveryGateTests(unittest.TestCase):
    """오늘 브리핑은 나갔는데 라이브에 오늘 오디오가 없으면 crawl 완료가 복구한다.

    2026-09-25 전문가 브리핑이 Gemini 503 과부하로 죽었다. 과부하는 몇 시간이면
    풀리는데 그 몇 시간 뒤를 부를 경로가 없었다.
    """

    TODAY = "2026-09-25"
    NOW = "2026-09-24T21:35:00+00:00"   # 06:35 KST

    def decide(self, manifest, *, event="workflow_run", now=None, conclusion="success"):
        calls = []

        def loader():
            calls.append(1)
            return manifest

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "outbox.json"
            path.write_text(json.dumps({"date": self.TODAY, "status": "sent"}),
                            encoding="utf-8")
            now_dt = datetime.fromisoformat(now or self.NOW)
            should_run, reason = gate.decide(
                event_name=event, workflow_conclusion=conclusion, now=now_dt,
                outbox_path=path, audio_manifest_loader=loader)
            state = gate.classify_state(event_name=event, should_run=should_run,
                                        now=now_dt, outbox_path=path)
        return should_run, reason, state, len(calls)

    def manifest(self, *, expert=True, expert_state="delivered"):
        variants = {"fast": {"date": self.TODAY, "delivery": {"state": "delivered"}}}
        if expert:
            variants["expert"] = {"date": self.TODAY, "delivery": {"state": expert_state}}
        return {"date": self.TODAY, "variants": variants}

    def test_missing_expert_triggers_audio_recovery(self):
        should_run, reason, state, _ = self.decide(self.manifest(expert=False))
        self.assertTrue(should_run)
        self.assertIn("expert", reason)
        self.assertEqual(state, gate.AUDIO_RECOVERY_STATE)

    def test_failed_delivery_triggers_audio_recovery(self):
        should_run, *_ = self.decide(self.manifest(expert_state="telegram_failed"))
        self.assertTrue(should_run)

    def test_complete_audio_does_not_run(self):
        should_run, reason, state, _ = self.decide(self.manifest())
        self.assertFalse(should_run)
        self.assertIn("already sent", reason)
        self.assertEqual(state, "recovery_not_needed")

    def test_yesterdays_expert_counts_as_missing(self):
        manifest = self.manifest()
        manifest["variants"]["expert"]["date"] = "2026-09-24"
        self.assertTrue(self.decide(manifest)[0])

    def test_unreadable_live_manifest_does_not_run(self):
        """판정 불가로 무거운 잡을 반복하지 않는다."""
        self.assertFalse(self.decide(None)[0])

    def test_outside_morning_window_does_not_even_ask(self):
        # 03:35 UTC == 12:35 KST
        should_run, _r, _s, calls = self.decide(
            self.manifest(expert=False), now="2026-09-25T03:35:00+00:00")
        self.assertFalse(should_run)
        self.assertEqual(calls, 0)

    def test_failed_crawl_and_schedule_do_not_recover_audio(self):
        self.assertFalse(self.decide(self.manifest(expert=False), conclusion="failure")[0])
        self.assertFalse(self.decide(self.manifest(expert=False), event="schedule")[0])

    def test_delivery_failure_states_match_audio_brief(self):
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import audio_brief
        self.assertEqual(gate.AUDIO_DELIVERY_FAILED, frozenset({
            audio_brief.DELIVERY_TELEGRAM_FAILED, audio_brief.DELIVERY_NO_FILE_ID,
            audio_brief.DELIVERY_QUEUE_FAILED}))


if __name__ == "__main__":
    unittest.main()
