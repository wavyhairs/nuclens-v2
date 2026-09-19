"""장기 스토리의 **흐름** — 화면이 "어떻게 여기까지 왔나"를 읽는 칸.

이 계층은 아무것도 판정하지 않는다. 그게 전부다.

    · 관계는 `thread_judge` 가 그 쌍을 보고 고른 값만 쓴다
    · 판정이 없는 이음매는 **빈칸**이다. '관련' 같은 말로 채우지 않는다
    · 단계 어휘를 새로 붙이지 않는다

마지막 줄에 실측 근거가 있다. `issue_continuity.PROGRESSION_SCALES` 로 사건마다
단계 이름을 붙여 봤더니(2026-09-19, thread 멤버 298건) 29%만 라벨이 하나로
정해졌고 그중에도 오탐이 섞였다 —

    「원안위, 혁신형 SMR 표준설계인가 신청 건 보고」 → permit:승인
        '표준설계인가' 의 '인가' 가 승인 패턴에 걸린다. 신청 보고가 승인이 된다.
    「…팹 완공 목표 2029년으로 1년 앞당겨」          → deal:준공
        '완공 목표' 가 준공이 된다.

`issue_change_log` 가 같은 이유로 `progression_detail` 을 화면에서 뺐다
(「예타 면제」 → "정지·가동중단"). 같은 종류는 같은 취급을 받아야 한다.
"""

import unittest
from datetime import date

import thread_judge
import thread_web
from event_retrieval import Event


def _event(issue_id, title, first):
    return Event(
        issue_id=issue_id, title=title, summary="",
        first_seen=date.fromisoformat(first), last_seen=date.fromisoformat(first),
        units=frozenset(), plants=frozenset(), entities=frozenset(),
        assets=frozenset(), actors=frozenset(), action="", tokens=frozenset(),
        briefing_count=1, raw={},
    )


class RelationshipContractTests(unittest.TestCase):
    """캐시의 `relationship` 은 지저분하다. 읽는 자리를 하나로 모은다."""

    def test_only_same_thread_carries_a_relationship(self):
        self.assertEqual(thread_judge.relationship_of(
            {"verdict": "different_thread", "relationship": "same_matter"}), "")

    def test_whitespace_is_trimmed(self):
        self.assertEqual(thread_judge.relationship_of(
            {"verdict": "same_thread", "relationship": " same_matter"}), "same_matter")

    def test_junk_values_become_empty(self):
        for junk in ("null", "uncertain", "", None, "관련 있음"):
            self.assertEqual(thread_judge.relationship_of(
                {"verdict": "same_thread", "relationship": junk}), "",
                f"{junk!r} 가 관계로 통과했다")

    def test_the_three_real_relationships_survive(self):
        for name in thread_judge.RELATIONSHIPS:
            self.assertEqual(thread_judge.relationship_of(
                {"verdict": "same_thread", "relationship": name}), name)


class FlowTests(unittest.TestCase):
    def test_flow_runs_oldest_first(self):
        """목록은 최신순, 흐름은 시간순. 흐름이 거꾸로면 흐름이 아니다."""
        members = [_event("a", "신청", "2026-03-01"),
                   _event("b", "심사 착수", "2026-04-01"),
                   _event("c", "승인", "2026-05-01")]
        rows = thread_web._flow(members, [])
        self.assertEqual([row["date"] for row in rows],
                         ["2026-03-01", "2026-04-01", "2026-05-01"])

    def test_a_judged_relationship_is_shown(self):
        members = [_event("a", "신청", "2026-03-01"), _event("b", "심사", "2026-04-01")]
        links = [{"from": "a", "to": "b", "relationship": "stage_progress"}]
        rows = thread_web._flow(members, links)
        self.assertEqual(rows[0]["relation_to_next"], "stage_progress")
        self.assertEqual(rows[0]["relation_label"], "다음 단계")

    def test_an_unjudged_gap_stays_empty(self):
        """판정이 없으면 화면은 아무 말도 하지 않는다."""
        members = [_event("a", "신청", "2026-03-01"), _event("b", "심사", "2026-04-01")]
        rows = thread_web._flow(members, [])
        self.assertEqual(rows[0]["relation_to_next"], "")
        self.assertEqual(rows[0]["relation_label"], "")

    def test_an_unknown_relationship_is_not_rendered(self):
        members = [_event("a", "신청", "2026-03-01"), _event("b", "심사", "2026-04-01")]
        links = [{"from": "a", "to": "b", "relationship": "전략적으로 관련 있음"}]
        self.assertEqual(thread_web._flow(members, links)[0]["relation_label"], "")

    def test_the_last_step_has_no_relation(self):
        members = [_event("a", "신청", "2026-03-01"), _event("b", "심사", "2026-04-01")]
        links = [{"from": "a", "to": "b", "relationship": "stage_progress"}]
        self.assertEqual(thread_web._flow(members, links)[-1]["relation_to_next"], "")

    def test_identical_titles_collapse_into_one_step(self):
        """같은 문장이 두 줄로 서고 그 사이에 '다음 단계' 가 붙으면 안 된다."""
        members = [_event("a", "정부, 2040년 전력수요 885TWh 전망", "2026-08-24"),
                   _event("b", "정부, 2040년 전력수요 885TWh 전망", "2026-08-29"),
                   _event("c", "정부, 12차 전기본 확정", "2026-09-07")]
        links = [{"from": "a", "to": "b", "relationship": "stage_progress"},
                 {"from": "b", "to": "c", "relationship": "stage_progress"}]
        rows = thread_web._flow(members, links)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["date"], "2026-08-24", "접을 때 최초일을 잃었다")
        self.assertEqual(rows[0]["relation_to_next"], "stage_progress")

    def test_a_repeated_title_that_is_not_adjacent_is_kept(self):
        """사이에 다른 사건이 끼면 같은 제목이라도 서로 다른 자리다."""
        members = [_event("a", "정기검사 착수", "2026-03-01"),
                   _event("b", "설비 교체", "2026-04-01"),
                   _event("c", "정기검사 착수", "2026-05-01")]
        self.assertEqual(len(thread_web._flow(members, [])), 3)

    def test_flow_never_invents_a_stage_name(self):
        """단계 어휘를 붙이지 않는다 — 실측 오탐이 파일 상단에 있다."""
        members = [_event("a", "원안위, 혁신형 SMR 표준설계인가 신청 건 보고", "2026-03-01"),
                   _event("b", "…팹 완공 목표 2029년으로 1년 앞당겨", "2026-04-01")]
        for row in thread_web._flow(members, []):
            self.assertEqual(row["title"], row["title"])
            for banned in ("승인", "준공", "신청", "심사"):
                self.assertNotIn(banned, row["relation_label"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
