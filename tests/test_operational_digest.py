"""하루 운영 요약 — 언제 보내나, 무엇을 사람 말로 옮기나.

기록은 2026-09-26~27 실제 delivery_log 모양을 그대로 쓴다. 운영자가 물었던 것
("근거 없는 문단이 뭔지 설명이 없다", "보류했다가 사라졌다는 게 무슨 말이냐")에
요약이 직접 답하는지를 잠근다.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import operational_alerts as cli  # noqa: E402
import operational_digest as digest  # noqa: E402
import operational_monitoring as monitor  # noqa: E402


KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 27, 6, 30, tzinfo=KST)          # 일요일 아침
SINCE = NOW - timedelta(hours=24)


def records():
    return [
        {"record_type": "selection_stats", "date": "2026-09-27",
         "generated_at": "2026-09-27T06:18:30+09:00", "pipeline_status": "ok",
         "domestic": {"selected_count": 6}, "overseas": {"selected_count": 8}},
        {"record_type": "curation_failure", "date": "2026-09-27",
         "generated_at": "2026-09-27T00:42:00+09:00", "lost": 23,
         "reasons": {"other": 23},
         "items": [{"hash": "c1", "title": "t", "reason": "request:other:HTTP 503: high demand"}]},
        {"record_type": "quality_event", "date": "2026-09-27",
         "generated_at": "2026-09-27T00:42:00+09:00",
         "alert_key": "article-integrity-quarantine", "title": "…", "detail": "…",
         "items": [{"hash": "w1", "title": "ŠKODA lands key R-R SMR contract"},
                   {"hash": "w2", "title": "Nucléaire : la construction des six EPR 2"}]},
        {"record_type": "quality_event", "date": "2026-09-27",
         "generated_at": "2026-09-27T03:42:00+09:00",
         "alert_key": "article-integrity-quarantine", "title": "…", "detail": "…",
         "items": [{"hash": "w1", "title": "ŠKODA lands key R-R SMR contract"}]},
        {"record_type": "quality_event", "date": "2026-09-27",
         "generated_at": "2026-09-27T05:11:00+09:00",
         "alert_key": "article-integrity-quarantine", "title": "…", "detail": "…",
         "items": [{"hash": "w1", "title": "ŠKODA lands key R-R SMR contract"}]},
        {"record_type": "quality_event", "date": "2026-09-27",
         "generated_at": "2026-09-27T03:44:00+09:00",
         "alert_key": "unverified-fallback-held", "title": "…", "detail": "…",
         "items": [{"hash": "w1", "title": "ŠKODA lands key R-R SMR contract", "final": False},
                   {"hash": "h2", "title": "울산시-HD현중, 1조 원대 발전엔진·SMR 건립 협약",
                    "final": True}]},
        {"record_type": "quality_event", "date": "2026-09-27",
         "generated_at": "2026-09-27T06:25:21+09:00",
         "alert_key": "audio-script-claim-removed",
         "title": "전문가 브리핑 대본에서 근거 없는 문단을 뺐습니다", "detail": "…",
         "items": [{"variant": "expert", "date": "2026-09-27",
                    "line": "트럼프 행정부는 2026년 9월 29일 정부 서비스 효율화를 위한 웹사이트 "
                            "America.gov를 공식 출범했습니다.",
                    "dates": ["2026-09-29"]}]},
        # 창 밖 — 요약에 나오면 안 된다.
        {"record_type": "quality_event", "date": "2026-09-25",
         "generated_at": "2026-09-25T06:00:00+09:00", "alert_key": "old-thing",
         "title": "옛 이벤트", "detail": "창 밖"},
    ]


def alert_state():
    iso = lambda value: value.astimezone(timezone.utc).isoformat()  # noqa: E731
    return {"items": {
        "source:FT 원자력:empty": {
            "scope": "source", "active": True, "level": "attention",
            "title": "FT 원자력 수집 결과가 계속 0건입니다",
            "action": "해당 사이트에 새 글이 있는지 확인해 주세요.",
            "last_seen_at": iso(NOW - timedelta(hours=1)), "delivery": "digest"},
        "issue-candidate:topn-retention": {
            "scope": "data_gate", "active": True, "level": "attention",
            "title": "이슈 묶음 정확도 점검 항목이 있습니다",
            "detail": "기사당 Top-12 이 실제 병합을 놓친다",
            "first_seen_at": iso(NOW - timedelta(days=9)),
            "last_seen_at": iso(NOW - timedelta(hours=1)), "delivery": "dev"},
    }}


def crawl_slots():
    base = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)
    return {f"s{index}": {"slot": (base + timedelta(hours=3 * index)).isoformat(),
                          "status": "success_with_articles", "new_article_count": 20}
            for index in range(7)}


class DigestTimingTests(unittest.TestCase):
    def test_once_a_day(self):
        state = {"last_sent_date": "2026-09-27"}
        self.assertFalse(digest.digest_due(state, NOW, requested=True))
        self.assertTrue(digest.digest_due({"last_sent_date": "2026-09-26"}, NOW, requested=True))

    def test_fallback_hour_covers_a_missed_morning_run(self):
        early = datetime(2026, 9, 27, 9, 50, tzinfo=KST)
        late = datetime(2026, 9, 27, 12, 11, tzinfo=KST)
        self.assertFalse(digest.digest_due({}, early, requested=False))
        self.assertTrue(digest.digest_due({}, late, requested=False))
        self.assertFalse(digest.digest_due({}, late, requested=False, fallback=False))

    def test_window_starts_at_the_previous_digest(self):
        previous = (NOW - timedelta(hours=23)).astimezone(timezone.utc)
        self.assertEqual(digest.window_start({"last_sent_at": previous.isoformat()}, NOW),
                         previous)
        self.assertEqual(digest.window_start({}, NOW),
                         NOW.astimezone(timezone.utc) - timedelta(hours=24))


class DigestContentTests(unittest.TestCase):
    def build(self, **kwargs):
        return digest.build_digest(records=records(), alert_state=alert_state(),
                                   crawl_slots=crawl_slots(), since=SINCE, now=NOW, **kwargs)

    def test_first_lines_are_the_verdict(self):
        text = self.build()
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("📋 뉴클렌스 하루 점검 · 9월 27일(일)"))
        self.assertEqual(lines[1], "✅ 할 일 없음")
        self.assertIn("아침 브리핑: 정상 발송(06:18) · 기사 14건", text)
        self.assertIn("수집: 7회 모두 정상 · 새 기사 140건", text)

    def test_removed_script_sentence_is_shown_with_what_was_not_found(self):
        """'근거 없는 문단을 뺐다'만으로는 무엇을 뺐는지 알 수 없었다."""
        text = self.build()
        self.assertIn("트럼프 행정부는 2026년 9월 29일", text)
        self.assertIn("기사에 없던 것: 날짜 9월 29일", text)

    def test_held_and_excluded_articles_are_named_and_not_double_counted(self):
        text = self.build()
        self.assertIn("원문과 맞지 않아 이번 수집에서 뺀 기사 2건", text)
        self.assertIn("ŠKODA lands key R-R SMR contract", text)
        self.assertIn("3회째 같은 이유로 빠지고", text)
        # 같은 기사(w1)가 보류 쪽에도 있었다 — 한 번만 센다.
        self.assertIn("잠시 뺀 기사 1건", text)
        self.assertIn("최종 제외 1건: 「울산시-HD현중, 1조 원대 발전엔진·SMR 건립 협약」", text)

    def test_ai_failure_says_the_cause_in_words(self):
        self.assertIn("늦춰진 기사 23건 (구글 AI 서버 혼잡 1회)", self.build())

    def test_sources_get_their_own_section(self):
        text = self.build()
        self.assertIn("\n출처\n• FT 원자력 수집 결과가 계속 0건입니다", text)

    def test_dev_items_only_on_monday(self):
        self.assertNotIn("개발 점검", self.build())
        monday = self.build(include_dev=True)
        self.assertIn("개발 점검", monday)
        self.assertIn("기사당 Top-12 이 실제 병합을 놓친다 (9/18부터)", monday)

    def test_out_of_window_records_are_ignored(self):
        self.assertNotIn("옛 이벤트", self.build())

    def test_no_developer_tokens_in_the_summary(self):
        text = self.build(include_dev=True)
        for token in ("consecutive_", "observation_id", "quarantined=", "bozo", "::"):
            self.assertNotIn(token, text)

    def test_missing_brief_is_a_todo(self):
        rows = [row for row in records() if row["record_type"] != "selection_stats"]
        text = digest.build_digest(records=rows, alert_state={}, crawl_slots=crawl_slots(),
                                   since=SINCE, now=NOW)
        self.assertIn("🔧 할 일 1건", text)
        self.assertIn("Daily Brief 워크플로를 확인해 주세요", text)


class DeliveryRoutingTests(unittest.TestCase):
    """즉시 갈래만 울리고, 요약 갈래는 '해결됨'도 만들지 않는다."""

    T0 = datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)

    def test_digest_items_are_never_due(self):
        signal = monitor.AlertSignal(
            key="quality-event:held", scope="quality_event", title="보류", detail="d",
            observation_id="o1", min_occurrences=1, delivery=monitor.DELIVERY_DIGEST)
        state, due = monitor.evaluate_alerts([signal], None, evaluated_scopes={"quality_event"},
                                             now=self.T0, immediate_only=True)
        self.assertEqual(due, [])
        self.assertTrue(state["items"]["quality-event:held"]["active"])
        self.assertEqual(state["items"]["quality-event:held"]["delivery"], "digest")

    def test_old_pending_digest_rows_are_not_resent(self):
        """옛 규칙에서 미발송으로 남은 요약 갈래 행이 새 규칙에서 튀어나오면 안 된다."""
        previous = {"items": {"quality:archive-integrity": {
            "scope": "data_gate", "active": True, "level": "attention",
            "title": "옛 경보", "pending_notification": True,
            "last_notified_at": "2026-09-26T21:31:30+00:00",
            "first_seen_at": "2026-08-17T19:52:52+00:00"}}}
        state, due = monitor.evaluate_alerts([], previous, evaluated_scopes={"data_gate"},
                                             now=self.T0, immediate_only=True)
        self.assertEqual(due, [])
        self.assertFalse(state["items"]["quality:archive-integrity"]["pending_notification"])

    def test_immediate_alert_resolves_silently(self):
        signal = monitor.AlertSignal(
            key="quality:brief-partial", scope="selection", title="일부 브리핑이 발송되지 못했습니다",
            detail="d", severity="critical", level=monitor.LEVEL_ACTION,
            observation_id="o1", min_occurrences=1)
        state, due = monitor.evaluate_alerts([signal], None, evaluated_scopes={"selection"},
                                             now=self.T0, immediate_only=True)
        self.assertEqual([row.key for row in due], ["quality:brief-partial"])
        state = monitor.mark_notified(state, due, self.T0)
        state, due = monitor.evaluate_alerts([], state, evaluated_scopes={"selection"},
                                             now=self.T0 + timedelta(hours=25),
                                             immediate_only=True)
        calls = []

        def sender(message, *, silent=False):
            calls.append((message, silent))
            return {"ok": True}

        monitor.notify_alerts(state, due, sender, now=self.T0 + timedelta(hours=25))
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1], "좋은 소식으로 새벽에 깨우지 않는다")
        self.assertIn("지난 24시간 동안 다시 생기지 않았습니다", calls[0][0])

    def test_telegram_sender_mutes_on_request(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read():
                return b'{"ok": true}'

        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token",
                                       "TELEGRAM_ADMIN_CHAT_ID": "admin"}, clear=True), \
                patch.object(cli.urllib.request, "urlopen", return_value=Response()) as opened:
            sender = cli.telegram_sender_from_env()
            sender("요약", silent=True)
            muted = cli.urllib.parse.parse_qs(opened.call_args.args[0].data.decode("utf-8"))
            sender("경보")
            loud = cli.urllib.parse.parse_qs(opened.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(muted.get("disable_notification"), ["true"])
        self.assertNotIn("disable_notification", loud)


if __name__ == "__main__":
    unittest.main()
