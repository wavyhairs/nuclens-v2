"""아침 알림을 **언제 보내지 않는가**.

이 스크립트가 하는 판단은 둘뿐이고, 둘 다 "보내지 않는다" 쪽이다. 발송 자체는
엣지가 한다(`functions/push/send.js`). 그래서 여기서 검사할 것도 그 둘이다.

왜 이 둘이 중요한가
-------------------
GitHub cron 은 예약대로 뜨지 않는다(이 저장소 실측: 대기 15~66분). 그리고 아침
빌드가 늦거나 실패하는 날이 있다. 그 둘이 겹치면 **점심에 도착한 어제 브리핑
알림**이 나간다 — 알림이 아니라 방해고, 한 번 나가면 되돌릴 수 없다.

늦는 것이 틀린 것보다 낫다. 둘 다 조용히 넘어간다(종료 코드 0) — 알림을 못 보낸
것은 사고가 아니라 판단이고, 워크플로를 빨갛게 만들면 진짜 고장과 구분되지 않는다.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import push_notify  # noqa: E402

KST = timezone(timedelta(hours=9))


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 19, hour, minute, tzinfo=KST)


class SendWindowTests(unittest.TestCase):
    def test_seven_in_the_morning_is_the_point(self):
        self.assertEqual(push_notify.SEND_AT.hour, 7)
        self.assertEqual(push_notify.SEND_AT.minute, 0)
        self.assertTrue(push_notify.within_window(at(7, 0)))

    def test_the_cron_delay_fits_inside_the_window(self):
        """예약은 07:00 KST 인데 실제 도착은 늦는다. 실측 최대 지연(66분)이
        창 안에 들어와야 '늦게 떴다'가 곧 '알림 없음'이 되지 않는다."""
        self.assertTrue(push_notify.within_window(at(8, 6)),
                        "실측 최대 지연에서도 알림이 나가야 한다")

    def test_it_does_not_arrive_before_the_hour(self):
        """일찍 뜬 실행은 기다리지 않고 건너뛴다. 러너를 재워 두는 것보다
        다음 날 정시에 오는 쪽이 낫다."""
        self.assertFalse(push_notify.within_window(at(6, 59)))
        self.assertFalse(push_notify.within_window(at(3, 0)))

    def test_lunchtime_is_not_a_morning_alert(self):
        self.assertFalse(push_notify.within_window(at(9, 31)))
        self.assertFalse(push_notify.within_window(at(12, 0)))
        self.assertFalse(push_notify.within_window(at(23, 0)))

    def test_the_window_is_read_in_kst_whatever_the_runner_thinks(self):
        """러너는 UTC 다. 여기서 KST 로 안 옮기면 알림이 16시에 간다."""
        utc = datetime(2026, 9, 18, 22, 10, tzinfo=timezone.utc)   # = 07:10 KST
        self.assertTrue(push_notify.within_window(utc))
        self.assertFalse(push_notify.within_window(
            datetime(2026, 9, 19, 7, 10, tzinfo=timezone.utc)))     # = 16:10 KST


class FreshnessTests(unittest.TestCase):
    def test_todays_card_goes_out(self):
        self.assertTrue(push_notify.card_is_todays({"date": "2026-09-19"}, at(7, 5)))

    def test_yesterdays_card_does_not(self):
        """아침 빌드가 늦거나 실패한 날이다. 어제 헤드라인을 오늘 아침 알림으로
        보내면, 그 알림을 받은 사람은 우리가 그걸 오늘 것이라 믿는다고 읽는다."""
        self.assertFalse(push_notify.card_is_todays({"date": "2026-09-18"}, at(7, 5)))

    def test_a_card_without_a_date_does_not(self):
        for card in ({}, {"date": ""}, {"date": None}, {"date": "not-a-date"}):
            self.assertFalse(push_notify.card_is_todays(card, at(7, 5)))

    def test_the_date_is_compared_in_kst(self):
        """UTC 로 비교하면 07:00 KST 는 아직 '어제'다 — 매일 걸러진다."""
        utc = datetime(2026, 9, 18, 22, 0, tzinfo=timezone.utc)     # = 09-19 07:00 KST
        self.assertTrue(push_notify.card_is_todays({"date": "2026-09-19"}, utc))


if __name__ == "__main__":
    unittest.main()
