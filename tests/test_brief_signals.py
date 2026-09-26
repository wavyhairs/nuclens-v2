"""LLM 없는 일일 품질 신호(brief_signals.py) — 게이트가 아니라 추세다.

기준선은 2026-09-13~26 발송 243건: 게재 2일+ 32 · 스토리 2일+ 70 · 7일 내 닮은 제목 28.
"""
import unittest

import brief_signals as bs

TODAY = "2026-09-26"


class ComputeTests(unittest.TestCase):
    def test_each_signal_counts_its_own_case(self):
        dates = {"m-old": {"published_at": "2026-09-22T10:00:00+09:00", "title": "첫 보도"}}
        selected = [
            {"hash": "a", "title_kr": "한수원 체코 원전 주기기 계약", "published_at": "2026-09-23T09:00:00+09:00"},
            {"hash": "b", "title_kr": "정부 대미투자 첫 사업 확정 정리", "published_at": "2026-09-25T21:00:00+09:00",
             "story_members": [{"hash": "m-old"}]},
            {"hash": "c", "title_kr": "울진 신한울 3호기 원자로 설치 착수", "published_at": "2026-09-25T22:00:00+09:00"},
            {"hash": "d", "title_kr": "IAEA 사무총장 방한 일정 발표", "published_at": "2026-09-25T23:00:00+09:00"},
        ]
        sent = [
            {"date": "2026-09-24", "title_kr": "울진 신한울 3호기 원자로 설치 착수했다"},
            {"date": "2026-09-18", "title_kr": "IAEA 사무총장 방한 일정 발표"},   # 8일 전 — 창 밖
            {"date": TODAY, "title_kr": "IAEA 사무총장 방한 일정 발표"},         # 오늘 — 대조 안 함
        ]
        result = bs.compute(selected, TODAY, dates, sent)
        self.assertEqual((result["sent"], result["article_older_2d"], result["story_older_2d"],
                          result["similar_7d"]), (4, 1, 2, 1))
        flagged = {row["hash"]: row for row in result["samples"]}
        self.assertEqual(flagged["b"]["first_seen"], "2026-09-22")
        self.assertEqual(flagged["c"]["similar_to"]["date"], "2026-09-24")
        self.assertNotIn("d", flagged)

    def test_a_midnight_utc_stamp_is_read_in_kst(self):
        # 9/23 15:30 UTC = 9/24 00:30 KST — 2일 선(9/24)에 걸린다.
        item = {"hash": "x", "title_kr": "제목", "published_at": "2026-09-23T15:30:00+00:00"}
        self.assertEqual(bs.compute([item], TODAY, {}, [])["article_older_2d"], 1)

    def test_nothing_selected_is_zero(self):
        self.assertEqual(bs.compute([], TODAY, {}, [])["sent"], 0)


if __name__ == "__main__":
    unittest.main()
