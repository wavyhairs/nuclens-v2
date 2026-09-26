"""큐레이션 입력에 게재일이 실린다 — '지난 22일'을 풀 기준이다.

2026-09-26 발송분 점검: 배경으로 나온 옛 사건을 새 소식처럼 쓴 제목 17건, 2026-09
아카이브의 event_date 90% null. 입력에 게재일이 없어서 모델은 '지난해 12월 계약'이
얼마나 오래된 일인지 알 수 없었다.
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gemini_client
import news_bot


class _Recorder:
    def __init__(self):
        self.users = []

    def __call__(self, system, user, **kwargs):
        self.users.append(user)
        raise gemini_client.GeminiError("recorded — no network")


class PublishDateLineTests(unittest.TestCase):
    def _record(self, pub):
        article = {"hash": "a" * 16, "title": "하나로 원자로 '자동중지', 작년부터 이상신호",
                   "description": "지난 3월 12일 자동정지", "publisher": "디스커버리뉴스",
                   "link": "https://example.com/a", "pub": pub}
        recorder = _Recorder()
        key = gemini_client.API_KEY
        gemini_client.API_KEY = "offline-replay"
        self.addCleanup(setattr, gemini_client, "API_KEY", key)
        with tempfile.TemporaryDirectory() as tmp:
            news_bot.curate_batch([article], [], client=recorder,
                                  log_path=Path(tmp) / "delivery_log.jsonl")
        return recorder.users[0]

    def test_the_kst_publish_date_is_in_the_block(self):
        pub = datetime(2026, 9, 21, 16, 30, tzinfo=timezone.utc)  # KST 9/22 01:30
        self.assertIn("게재일: 2026-09-22", self._record(pub))

    def test_no_datetime_no_line(self):
        self.assertNotIn("게재일:", self._record("2026-09-21"))

    def test_the_system_prompt_forbids_background_as_news(self):
        prompt = news_bot.CURATION_SYSTEM_PROMPT
        for needle in ("배경 사실을 오늘 사건처럼 올리지 않는다", "주장은 쓴 사람의 것이다",
                       "위험과 영향을 부풀리지 않는다", "성만 나온 인물을 풀어 쓰지 않는다",
                       "게재일:"):
            self.assertIn(needle, prompt)


if __name__ == "__main__":
    unittest.main()
