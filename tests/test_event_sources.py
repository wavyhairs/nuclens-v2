"""공식 일정 수집 — 실제 게시판 마크업과 실제 공지 제목으로 못박는다.

여기 있는 HTML 조각과 제목은 전부 **2026-08-29 에 그 사이트에서 받은 것**이다.
게시판이 개편되면 이 fixture 가 먼저 깨지고, 그때 파서를 고치면 된다. 개편을
운영 중에 알아채는 유일한 다른 방법은 '어느 날부터 0건'인데, 그것은 조용한
기관과 구분되지 않는다(`event_sources` 머리말).
"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import event_relevance  # noqa: E402
import event_sources  # noqa: E402

TODAY = date(2026, 8, 29)


class TestNoticeDates(unittest.TestCase):
    """공지 제목의 줄인 날짜. `explicit_dates` 가 못 읽던 자리다."""

    def test_a_short_date_with_a_weekday_is_a_date(self):
        """"9.9(수) 14:00" — 학회 공지가 실제로 쓰는 꼴."""
        title = ("AI 시대 국가경쟁력을 위한 전원믹스와 시장제도 심포지움 "
                 "개최(9.9(수) 14:00, 대한상공회의소)")
        self.assertEqual(event_sources.notice_dates(title, date(2026, 8, 14)),
                         [date(2026, 9, 9)])
        self.assertEqual(event_sources.notice_time(title), "14:00")

    def test_a_tilde_makes_it_a_deadline(self):
        """"(~9. 10.)" 은 그날 행사가 아니라 그날까지다."""
        span = event_sources.notice_span("2026 원전해체 비즈니스 포럼 참가자 모집(~9. 10.)",
                                         date(2026, 8, 20))
        self.assertEqual(span, (date(2026, 9, 10), date(2026, 9, 10), "deadline"))

    def test_a_bare_ordinal_is_not_a_date(self):
        """'Part 3. 인력' 의 '3.' 을 날짜로 읽으면 없는 일정이 선다.

        국회 행사알림에 실제로 있던 제목이다. 요일·시각·물결표 같은 근거가
        하나도 없으므로 줄인 M.D 를 아예 보지 않는다.
        """
        self.assertEqual(
            event_sources.notice_dates("대한민국 반도체 미래지도와 인프라 전략 Part 3. 인력",
                                       TODAY), [])

    def test_a_full_date_is_not_read_twice(self):
        """"2026.11.16" 의 뒤쪽 '11.16' 이 줄인 날짜로 한 번 더 잡히면 안 된다."""
        self.assertEqual(
            event_sources.notice_dates("2026.11.16 ~ 2026.11.18 개최", TODAY),
            [date(2026, 11, 16), date(2026, 11, 18)])

    def test_a_year_end_notice_rolls_into_next_year(self):
        """12월에 올라온 '1. 15.' 공지는 내년 1월이다."""
        self.assertEqual(
            event_sources.notice_dates("신년인사회 개최(1. 15.)", date(2026, 12, 20)),
            [date(2027, 1, 15)])

    def test_a_just_passed_date_stays_in_this_year(self):
        """8/24 에 올라온 '8. 31.' 은 올해 8월이지 내년이 아니다."""
        self.assertEqual(
            event_sources.notice_dates("접수 마감 (~ 8. 31.)", date(2026, 8, 24)),
            [date(2026, 8, 31)])

    def test_no_date_means_no_event(self):
        """날짜가 없으면 일정이 아니다 — 칸을 채우려고 게시일을 쓰지 않는다."""
        self.assertIsNone(
            event_sources.notice_span("사무국 환경 개선 공사 안내", TODAY))


# ── 게시판 fixture (실측 마크업) ─────────────────────────────────────────

KNS_PAGE = """
<div class="board-box line">
  <ul class="board-list">
    <li class="li01">1099</li>
    <li class="li02"><a href="/boards/chk_view/notice/103327">
      AI 시대 국가경쟁력을 위한 전원믹스와 시장제도 심포지움 개최(9.9(수) 14:00, 대한상공회의소)
    </a></li>
    <li class="li03"><span class="down">한국원자력학회</span> |
      <span class="date">2026-08-14<br></span> | <span class="view">42</span></li>
  </ul>
  <ul class="board-list">
    <li class="li01">1095</li>
    <li class="li02"><a href="/boards/chk_view/notice/103304">사무국 환경 개선 공사 안내</a></li>
    <li class="li03"><span class="down">한국원자력학회</span> |
      <span class="date">2026-07-22<br></span> | <span class="view">17</span></li>
  </ul>
</div>
"""

KAIF_NOTICE_PAGE = """
<table class="bbs-standard"><tbody>
  <tr>
    <td class="col-num">1</td>
    <td class="col-tit"><a href="?c=193&amp;s=&amp;gp=1&amp;gbn=view&amp;ix=30028">2026 원전해체 비즈니스 포럼 참가자 모집(~9. 10.)</a></td>
    <td class="col-writer">평생교육원</td>
    <td class="col-date">2026.08.20</td>
    <td class="col-hit">560</td>
  </tr>
  <tr>
    <td class="col-num">2</td>
    <td class="col-tit"><a href="?c=193&amp;s=&amp;gp=1&amp;gbn=view&amp;ix=30035">2026년 원전기업 신입사원 입문 과정 (2차) 교육생 모집 (~ 8. 31.)</a></td>
    <td class="col-writer">평생교육원</td>
    <td class="col-date">2026.08.24</td>
    <td class="col-hit">162</td>
  </tr>
</tbody></table>
"""

KAIF_CALENDAR_PAGE = """
<table class="bbs-standard thk-bbs"><tbody>
  <tr>
    <td class="col-num">576</td><td class="">세미나</td>
    <td class="">2026 경남 SMR 국제 콘퍼런스</td>
    <td class="no640">2026.11.16 ~ 2026.11.18</td>
    <td class="no768">창원컨벤션센터</td>
    <td class="no640"></td>
  </tr>
  <tr>
    <td class="col-num">573</td><td class="">세미나</td>
    <td class="">2026년 제65차 대한핵의학회 추계 학술대회</td>
    <td class="no640">2026.11.06 ~ 2026.11.07</td>
    <td class="no768">세종대 컨벤션센터</td>
    <td class="no640"><a href="www.ksnm.or.kr" target='_blank'><span>home</span></a></td>
  </tr>
  <tr>
    <td class="col-num">559</td><td class="">세미나</td>
    <td class="">제226회 원자력계 조찬강연회</td>
    <td class="no640">2026.09.04</td>
    <td class="no768">웨스틴조선 서울</td>
    <td class="no640"></td>
  </tr>
</tbody></table>
"""


class TestBoardParsers(unittest.TestCase):

    def test_kns_reads_the_symposium_and_skips_the_notice_without_a_date(self):
        rows = event_sources.parse_kns_notice(KNS_PAGE)
        kept = [row for row in rows if not row.get("_dropped")]
        self.assertEqual(len(kept), 1)
        row = kept[0]
        self.assertEqual(row["date"], "2026-09-09")
        self.assertEqual(row["time"], "14:00")
        self.assertEqual(row["place"], "대한상공회의소")
        self.assertEqual(row["host"], "한국원자력학회")
        self.assertEqual(row["first_seen"], "2026-08-14")
        self.assertTrue(row["url"].endswith("/boards/chk_view/notice/103327"))
        self.assertIn("power_market", row["topics"])

    def test_kaif_notice_keeps_the_forum_and_drops_the_training_intake(self):
        """행사 모집은 마감으로 서고, 신입사원 교육생 모집은 중요도에서 걸린다."""
        rows = event_sources.parse_kaif_notice(KAIF_NOTICE_PAGE)
        kept = [row for row in rows if not row.get("_dropped")]
        dropped = [row["_dropped"] for row in rows if row.get("_dropped")]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["kind"], "deadline")
        self.assertEqual(kept[0]["date"], "2026-09-10")
        self.assertEqual(dropped, ["low_significance"])

    def test_the_calendar_table_carries_place_and_span(self):
        rows = event_sources.parse_kaif_calendar(KAIF_CALENDAR_PAGE, today=TODAY)
        kept = [row for row in rows if not row.get("_dropped")]
        by_label = {row["label"]: row for row in kept}
        smr = by_label["2026 경남 SMR 국제 콘퍼런스"]
        self.assertEqual((smr["date"], smr["end_date"]),
                         ("2026-11-16", "2026-11-18"))
        self.assertEqual(smr["kind"], "range")
        self.assertEqual(smr["place"], "창원컨벤션센터")
        self.assertIn("smr", smr["topics"])
        # 하루짜리는 점이다.
        self.assertEqual(by_label["제226회 원자력계 조찬강연회"]["kind"], "point")

    def test_nuclear_medicine_never_reaches_the_calendar(self):
        """원자력 기관의 일정표에 실렸어도 핵의학은 이 달력의 주제가 아니다."""
        rows = event_sources.parse_kaif_calendar(KAIF_CALENDAR_PAGE, today=TODAY)
        self.assertIn("off_topic",
                      [row.get("_dropped") for row in rows])
        self.assertNotIn("대한핵의학회",
                         " ".join(row.get("label", "") for row in rows
                                  if not row.get("_dropped")))

    def test_the_listing_body_is_not_recorded_as_the_host(self):
        """협회 일정표는 남의 행사를 실어 나른다 — 주최를 협회로 적으면 거짓이다."""
        rows = event_sources.parse_kaif_calendar(KAIF_CALENDAR_PAGE, today=TODAY)
        smr = next(row for row in rows
                   if row.get("label") == "2026 경남 SMR 국제 콘퍼런스")
        self.assertEqual(smr["host"], "")
        self.assertEqual(smr["publisher"], "한국원자력산업협회")


ASSEMBLY_ROWS = [
    {"eventDivCd": "MEMNA", "eventDivNm": "의원실행사",
     "title": "국가전력망 민간참여 본격화, 공공성 훼손 우려와 대응과제: 전력산업 공공성 강화를 위한 정책 연속세미나",
     "eventDate": "2026-09-03 14:00", "eventTime": "14:00",
     "placeNm": "의원회관 제7간담회의실(210호)",
     "orgNm": "김주영 의원실, 혁신더하기연구소, 전기신문",
     "linkUrl": "https://ampos.nanet.go.kr:7443/seminarList.do"},
    {"eventDivCd": "MEMNA", "eventDivNm": "의원실행사",
     "title": "초고령사회 고령층 인플루엔자 예방 정책 혁신 방안 정책토론회",
     "eventDate": "2026-09-03 14:00~16:00", "eventTime": "14:00~16:00",
     "placeNm": "의원회관 제8간담회의실(211호)",
     "orgNm": "서미화 의원실, 대한감염학회",
     "linkUrl": "https://ampos.nanet.go.kr:7443/seminarList.do"},
    {"eventDivCd": "ARTCL", "eventDivNm": "문화행사",
     "title": "국회 개방 정오의 콘서트 <9월 국회 버스킹>",
     "eventDate": "2026-09-03 12:10 ~ 13:00", "eventTime": "12:10 ~ 13:00",
     "placeNm": "국회 중앙잔디광장", "orgNm": None, "linkUrl": ""},
]


class TestAssembly(unittest.TestCase):
    """국회 행사알림 — 하루치에서 이 달력이 볼 것은 대개 한 건이다."""

    def test_only_the_power_policy_seminar_survives(self):
        rows = event_sources.parse_assembly_day(ASSEMBLY_ROWS, day=TODAY)
        kept = [row for row in rows if not row.get("_dropped")]
        self.assertEqual(len(kept), 1)
        row = kept[0]
        self.assertEqual(row["date"], "2026-09-03")
        self.assertEqual(row["time"], "14:00")
        self.assertEqual(row["place"], "의원회관 제7간담회의실(210호)")
        self.assertEqual(row["host"], "김주영 의원실, 혁신더하기연구소, 전기신문")
        self.assertIn("power_market", row["topics"])

    def test_a_culture_event_is_not_even_a_candidate(self):
        """문화행사는 구분에서 걸러진다 — 판정까지 갈 것도 없다."""
        rows = event_sources.parse_assembly_day(ASSEMBLY_ROWS, day=TODAY)
        self.assertNotIn("버스킹", " ".join(str(row) for row in rows))

    def test_the_room_name_cannot_pass_the_significance_gate(self):
        """'제8간담회의실' 의 '간담회' 가 행사 형식으로 읽히면 안 된다.

        국회 회의실 이름에는 죄다 '간담회의실' 이 들어 있다. 장소를 중요도
        판정에 넣으면 주제만 맞으면 무엇이든 행사가 된다.
        """
        verdict = event_relevance.judge("원자력 관련 자료 안내", "",
                                        "의원회관 제8간담회의실(211호)")
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], "not_an_event")


class TestStore(unittest.TestCase):

    def _row(self, **over):
        base = {"id": "of-1", "date": "2026-09-04", "end_date": "2026-09-04",
                "kind": "point", "label": "제226회 원자력계 조찬강연회",
                "first_seen": "2026-08-29", "url": "https://example.org/1",
                "host": "", "place": "웨스틴조선 서울"}
        return {**base, **over}

    def test_the_first_sighting_is_never_pushed_forward(self):
        """협회 일정표에는 게시일이 없다. 매번 오늘로 덮으면 그 값이 무의미해진다."""
        kept, _ = event_sources.merge_events(
            [self._row(first_seen="2026-07-29")],
            [self._row(first_seen="2026-08-29", place="웨스틴조선 서울 오키드룸")])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["first_seen"], "2026-07-29")
        # 나머지 칸은 새 값이 이긴다 — 기관이 장소를 고치면 그것이 사실이다.
        self.assertEqual(kept[0]["place"], "웨스틴조선 서울 오키드룸")

    def test_a_new_row_counts_as_new(self):
        kept, added = event_sources.merge_events([], [self._row()])
        self.assertEqual((len(kept), added), (1, 1))
        kept, added = event_sources.merge_events(kept, [self._row()])
        self.assertEqual((len(kept), added), (1, 0))

    def test_pruning_keeps_the_far_future_and_drops_the_far_past(self):
        """협회 일정표는 몇 달 앞을 준다. 창에 들어올 때까지 들고 있어야 한다."""
        rows = event_sources.prune([
            self._row(id="of-old", date="2020-01-01", end_date="2020-01-01"),
            self._row(id="of-far", date="2026-11-16", end_date="2026-11-18"),
        ], TODAY)
        self.assertEqual([row["id"] for row in rows], ["of-far"])


class TestSourceIsolation(unittest.TestCase):

    def test_one_dead_board_does_not_stop_the_others(self):
        """게시판 개편·403 은 언제든 온다. 한 곳이 죽어도 나머지는 걷는다."""
        def boom(_today):
            raise RuntimeError("게시판 개편")

        def fine(_today):
            return [{"id": "of-ok", "date": "2026-09-04",
                     "end_date": "2026-09-04", "kind": "point",
                     "label": "무사한 일정", "first_seen": "2026-08-29",
                     "url": "https://example.org/ok"}]

        original = event_sources.OUT_FILE
        event_sources.OUT_FILE = original.with_name("event_schedule.test.json")
        try:
            event_sources.run([{"id": "dead", "name": "죽은 게시판", "fetch": boom},
                               {"id": "alive", "name": "산 게시판", "fetch": fine}],
                              today=TODAY)
            store = event_sources.load_store()
            self.assertFalse(store["last_checked"]["dead"]["ok"])
            self.assertIn("RuntimeError", store["last_checked"]["dead"]["error"])
            self.assertTrue(store["last_checked"]["alive"]["ok"])
            self.assertEqual([row["id"] for row in store["events"]], ["of-ok"])
        finally:
            event_sources.OUT_FILE.unlink(missing_ok=True)
            event_sources.OUT_FILE = original


if __name__ == "__main__":
    unittest.main()
