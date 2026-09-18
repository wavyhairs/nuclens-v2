"""아침 알림이 읽는 한 장(`data/push.json`) 계약.

왜 따로 굽는가
--------------
발송은 본문 없는 알림을 보낸다 — 본문을 실으려면 구독마다 암호화를 돌려야 하고
(RFC 8291), 그 구현을 검증 없이 배포 경로에 두지 않기로 했다
(`functions/push/send.js` 머리말). 대신 서비스워커가 알림을 받는 순간 이 파일을
읽어 제목을 붙인다.

그래서 이 파일의 성질이 하나 특별하다: **알림이 도착한 순간에 읽힌다.** 오늘
치가 아직 안 올라왔으면 어제 제목이 아침 알림으로 나간다. 그 사고를 막는 곳은
두 군데고(여기 + tools/push_notify.py), 여기서는 **날짜가 실려 있는가**를 본다.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import build_data  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 19, 4, 40, tzinfo=KST)


class PushCardTests(unittest.TestCase):
    def test_the_headline_goes_out_as_written(self):
        """알림이 화면과 다른 말을 하면 누른 뒤에 배신당한 기분이 든다.
        여기서 문장을 새로 짓지 않는 이유다."""
        headline = "한미 원전 노형 배분 등 이견으로 대미 투자 MOU 서명 연기"
        card = build_data.build_push_card([{"date": "2026-09-19", "headline": headline}], NOW)
        self.assertEqual(card["body"], headline)
        self.assertEqual(card["title"], "9월 19일 브리핑")
        self.assertEqual(card["date"], "2026-09-19")
        self.assertEqual(card["url"], "/?src=push")

    def test_a_briefing_without_a_headline_still_sends(self):
        """헤드라인이 비는 회차가 있다(headline_kind 가 안 서는 날).

        그때 알림을 거르면 오는 날과 안 오는 날이 갈리고, 사용자는 그것을
        고장으로 읽는다 — 일반 문구로라도 나가는 쪽이 낫다.
        """
        card = build_data.build_push_card([{"date": "2026-09-19", "headline": "  "}], NOW)
        self.assertEqual(card["body"], build_data.PUSH_FALLBACK_BODY)

    def test_the_date_is_the_briefing_date_not_today(self):
        """발송이 하루 밀리면 그 사실이 알림에 보여야 한다. 그리고 이 날짜가
        `tools/push_notify.py` 의 '오늘 것인가' 판단의 유일한 근거다."""
        card = build_data.build_push_card([{"date": "2026-09-18", "headline": "어제 것"}], NOW)
        self.assertEqual(card["date"], "2026-09-18")
        self.assertEqual(card["title"], "9월 18일 브리핑")

    def test_it_never_raises_on_a_broken_briefing(self):
        """이 한 줄 때문에 빌드 전체가 죽으면 안 된다 — 알림은 부가 기능이다."""
        for briefings in ([], [{}], [{"date": "not-a-date"}], [{"date": None, "headline": None}]):
            card = build_data.build_push_card(briefings, NOW)
            self.assertTrue(card["title"])
            self.assertTrue(card["body"])
            self.assertEqual(card["version"], build_data.PUSH_CARD_VERSION)

    def test_the_tag_is_what_the_push_service_will_accept(self):
        """Topic 헤더는 base64url 32자 이하여야 한다(RFC 8030). 넘으면 푸시
        서비스가 400 을 내고, 증상은 '알림이 안 온다' 하나뿐이다."""
        card = build_data.build_push_card([{"date": "2026-09-19", "headline": "x"}], NOW)
        tag = card["tag"]
        self.assertLessEqual(len(tag), 32)
        self.assertRegex(tag, r"^[A-Za-z0-9_-]+$")

    def test_it_is_shipped_with_the_rest(self):
        """구워도 배포에 안 실리면 서비스워커가 404 를 받고, 알림은 매일
        일반 문구로만 나간다 — 조용한 퇴화다."""
        source = (ROOT / "build_data.py").read_text(encoding="utf-8")
        outputs = source.split("    outputs = (", 1)[1].split("    )", 1)[0]
        self.assertIn('("push.json", build_push_card(briefings, now)),', outputs)


if __name__ == "__main__":
    unittest.main()
