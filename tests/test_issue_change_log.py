"""변화 이력 — `issue_continuity` 의 판정을 이슈 안의 짝으로 잇는 게이트.

여기 테스트의 대부분은 **안 싣는 쪽**을 잠근다. 그게 이 모듈의 내용이기 때문이다:
continuity 의 same_issue 는 감점용이라 일부러 넓고(제목 문턱 0.62, 앵커 단독 매칭
허용), 그 판정을 화면의 '변화 이력'으로 그대로 올리면 없는 연결을 주장한다.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import issue_change_log  # noqa: E402


def member(article_hash, briefing_date, title, continuity=None, article_date=None):
    return {
        "hash": article_hash,
        "title_kr": title,
        "briefing_date": briefing_date,
        "article_date": article_date or briefing_date,
        "continuity": continuity or {},
    }


def verdict(prior_hash="", prior_date="", prior_title="", progression="material",
            kind="stage_flip", days=1, similarity=0.7, identity=True):
    return {
        "prior_hash": prior_hash,
        "prior_date": prior_date,
        "prior_title": prior_title,
        "days_ago": days,
        "similarity": similarity,
        "progression": progression,
        "progression_kind": kind,
        "progression_detail": "정지·가동중단",
        "identity_confirmed": identity,
    }


class GateTests(unittest.TestCase):
    """직전 발송분이 **같은 이슈의 멤버**일 때만 싣는다."""

    def test_progression_inside_the_issue_is_kept(self):
        rows = issue_change_log.build([
            member("a1", "2026-09-05", "원안위, 새울 3호기 시운전 시험 재가동 승인"),
            member("a2", "2026-09-06", "원안위, 새울 3호기 재가동 승인…설정값 오류 확인",
                   verdict(prior_hash="a1", prior_date="2026-09-05",
                           prior_title="원안위, 새울 3호기 시운전 시험 재가동 승인")),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "material")
        self.assertEqual(rows[0]["prior_hash"], "a1")
        self.assertEqual(rows[0]["hash"], "a2")

    def test_a_prior_outside_the_issue_is_dropped(self):
        """실측 2026-08-31: 무관한 미국 데이터센터 지연 기사가 볼탈리아 브라질 건에
        `데이터센터`·`전력수요` 앵커만으로 붙어 material 을 받았다. 두 기사는 다른
        이슈로 갈려 있었고, 화면이 그 쌍을 '단계 이동'이라 부르면 없는 연결이 선다.
        """
        rows = issue_change_log.build([
            member("b2", "2026-08-31", "미국 AI 데이터센터, 주민 반발로 지연·취소",
                   verdict(prior_hash="b1", prior_date="2026-08-28",
                           prior_title="프랑스 볼탈리아, 브라질 데이터센터 승인",
                           similarity=0.333)),
        ])
        self.assertEqual(rows, [])

    def test_none_is_not_a_change(self):
        """게이트가 '아무것도 안 움직였다'고 말한 회차다. 변화 이력에 실을 것이 없다."""
        rows = issue_change_log.build([
            member("a1", "2026-08-20", "같은 이야기"),
            member("a2", "2026-08-21", "같은 이야기 재전송",
                   verdict(prior_hash="a1", progression="none", kind="")),
        ])
        self.assertEqual(rows, [])

    def test_minor_is_kept_but_labelled_apart(self):
        rows = issue_change_log.build([
            member("a1", "2026-08-22", "원안위, 새울 1호기 정기검사 완료 및 재가동 승인"),
            member("a2", "2026-08-24", "원안위, 정기검사 마친 새울원전 1호기 재가동 허용",
                   verdict(prior_hash="a1", progression="minor", kind="new_quantities")),
        ])
        self.assertEqual([row["kind"] for row in rows], ["minor"])

    def test_a_member_without_continuity_contributes_nothing(self):
        rows = issue_change_log.build([member("a1", "2026-09-05", "단발 기사")])
        self.assertEqual(rows, [])

    def test_self_reference_is_dropped(self):
        """같은 기사를 가리키는 판정은 변화가 아니다 — 재실행·중복 적재의 잔해다."""
        rows = issue_change_log.build([
            member("a1", "2026-09-05", "같은 기사", verdict(prior_hash="a1")),
        ])
        self.assertEqual(rows, [])


class PriorHashRecoveryTests(unittest.TestCase):
    """`prior_hash` 는 2026-09-12 부터 기록된다. 그 앞 26일치를 되찾는 경로."""

    def index(self, *rows):
        return issue_change_log.prior_hash_index(
            {row["hash"]: row for row in rows})

    def test_old_records_resolve_by_date_and_title(self):
        index = self.index(
            {"hash": "a1", "date": "2026-08-26", "title_kr": "프랑스, 그라벨린 원전 신규 2기 승인"},
            {"hash": "a2", "date": "2026-08-28", "title_kr": "프랑스 그라블린 원전, 해파리로 가동 중단"},
        )
        rows = issue_change_log.build([
            member("a1", "2026-08-26", "프랑스, 그라벨린 원전 신규 2기 승인"),
            member("a2", "2026-08-28", "프랑스 그라블린 원전, 해파리로 가동 중단",
                   verdict(prior_date="2026-08-26",
                           prior_title="프랑스, 그라벨린 원전 신규 2기 승인")),
        ], index)
        self.assertEqual([row["prior_hash"] for row in rows], ["a1"])

    def test_recorded_prior_hash_wins_over_the_title_join(self):
        """제목은 표시용 문자열이라 회차마다 바뀔 수 있다. 해시가 있으면 해시가 이긴다."""
        index = self.index(
            {"hash": "wrong", "date": "2026-09-05", "title_kr": "옛 제목"},
        )
        rows = issue_change_log.build([
            member("a1", "2026-09-05", "새 제목"),
            member("a2", "2026-09-06", "후속",
                   verdict(prior_hash="a1", prior_date="2026-09-05", prior_title="옛 제목")),
        ], index)
        self.assertEqual([row["prior_hash"] for row in rows], ["a1"])

    def test_a_collided_title_resolves_to_nothing(self):
        """같은 날 같은 제목이 둘이면 어느 쪽인지 말할 수 없다 — 반쯤 맞는 연결은 안 잇는다."""
        index = self.index(
            {"hash": "a1", "date": "2026-08-26", "title_kr": "같은 제목"},
            {"hash": "b1", "date": "2026-08-26", "title_kr": "같은 제목"},
        )
        self.assertEqual(index, {})
        rows = issue_change_log.build([
            member("a1", "2026-08-26", "같은 제목"),
            member("a2", "2026-08-28", "후속",
                   verdict(prior_date="2026-08-26", prior_title="같은 제목")),
        ], index)
        self.assertEqual(rows, [])


class PayloadTests(unittest.TestCase):
    def rows(self):
        return issue_change_log.build([
            member("a1", "2026-09-05", "직전 제목"),
            member("a2", "2026-09-06", "현재 제목",
                   verdict(prior_hash="a1", prior_date="2026-09-05",
                           prior_title="발송 당시 제목", days=1)),
        ])

    def test_prior_title_comes_from_the_catalog_not_the_delivery_log(self):
        """같은 기간 살아남은 이슈의 18.8% 가 제목을 바꿨다(issue_ledger). 화면 두 곳이
        같은 기사를 다른 이름으로 부르면 인용이 성립하지 않는다."""
        self.assertEqual(self.rows()[0]["prior_title"], "직전 제목")

    def test_diagnostics_travel_but_the_screen_gets_facts(self):
        row = self.rows()[0]
        # 감사용 — 화면 문장으로 쓰지 않는다(모듈 docstring: 「예타 면제」에
        # '정지·가동중단'이 붙어 있던 실측).
        self.assertEqual(row["reason"], "stage_flip")
        self.assertNotIn("progression_detail", row)
        # 화면이 쓰는 사실들.
        self.assertEqual(
            {"date", "article_date", "hash", "title",
             "prior_date", "prior_article_date", "prior_hash", "prior_title",
             "days", "kind", "reason", "similarity", "identity_confirmed"},
            set(row),
        )

    def test_both_the_briefing_round_and_the_article_day_travel(self):
        """판정은 회차 사이에서 내려지지만 화면 아래 타임라인은 기사일로 선다.
        한 쪽만 실으면 두 블록이 같은 사건을 하루 어긋나게 말한다 —
        실측 그라블린(회차 8/28 · 기사일 8/27)."""
        rows = issue_change_log.build([
            member("a1", "2026-08-26", "그라벨린 신규 2기 승인", article_date="2026-08-25"),
            member("a2", "2026-08-28", "그라블린 해파리 가동 중단",
                   verdict(prior_hash="a1", prior_date="2026-08-26"),
                   article_date="2026-08-27"),
        ])
        self.assertEqual((rows[0]["date"], rows[0]["article_date"]),
                         ("2026-08-28", "2026-08-27"))
        self.assertEqual((rows[0]["prior_date"], rows[0]["prior_article_date"]),
                         ("2026-08-26", "2026-08-25"))

    def test_newest_first_and_stable(self):
        members = [
            member("a1", "2026-09-01", "1"),
            member("a2", "2026-09-02", "2", verdict(prior_hash="a1", prior_date="2026-09-01")),
            member("a3", "2026-09-03", "3", verdict(prior_hash="a2", prior_date="2026-09-02")),
        ]
        rows = issue_change_log.build(members)
        self.assertEqual([row["date"] for row in rows], ["2026-09-03", "2026-09-02"])
        self.assertEqual(rows, issue_change_log.build(list(reversed(members))))

    def test_entries_are_capped(self):
        members = [member("a0", "2026-07-01", "0")]
        for index in range(1, issue_change_log.MAX_ENTRIES + 6):
            members.append(member(
                f"a{index}", f"2026-07-{index + 1:02d}", str(index),
                verdict(prior_hash=f"a{index - 1}")))
        self.assertEqual(len(issue_change_log.build(members)),
                         issue_change_log.MAX_ENTRIES)


if __name__ == "__main__":
    unittest.main()
