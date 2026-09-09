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


# ── 전력거래소 fixture (실측 마크업 2026-09-10) ──────────────────────────
#
# 이 게시판만 목록과 상세를 둘 다 받는다. 목록은 게시일만 주고 행사일은 본문에
# 있기 때문이다 — 아래 세 조각이 그 사실을 그대로 담고 있다:
#   77793  본문에 '1. 일 시 :' 가 있는 정상 공지 (행사일·시각·장소 전부 나옴)
#   77367  '일시' 와 '참석 방법 … 까지 제출' 이 같은 본문에 있는 공지
#   78098  본문이 포스터 이미지 한 장뿐인 공개토론회 공지 (행사일이 없다)

KPX_LIST_PAGE = """
<div class="board_list">
<table class="tstyle_list"><tbody>
  <tr>
    <td aria-label="번호">532</td>
    <td class="txt_left" aria-label="제목">
<a href="/board.es?mid=a10501010000&amp;bid=0042&amp;act=view&amp;list_no=78098&amp;tag=&amp;nPage=1" onclick="goView('78098'); return false;">  <i class="xi-new"></i><span class="sr_only">새글</span> 제12차 전기본 공개토론회</a></td>
    <td aria-label="작성자">에너지계획팀</td>
    <td aria-label="등록일">2026/09/09</td>
    <td aria-label="조회수">151</td>
    <td aria-label="첨부파일"></td>
  </tr>
  <tr>
    <td aria-label="번호">531</td>
    <td class="txt_left" aria-label="제목">
<a href="/board.es?mid=a10501010000&amp;bid=0042&amp;act=view&amp;list_no=78092&amp;tag=&amp;nPage=1" onclick="goView('78092'); return false;">   2026년 한국전력거래소 창업·벤처기업 지원사업 참여기업 모집 공고</a></td>
    <td aria-label="작성자">ESG홍보협력팀</td>
    <td aria-label="등록일">2026/09/09</td>
    <td aria-label="조회수">95</td>
    <td aria-label="첨부파일"></td>
  </tr>
  <tr>
    <td aria-label="번호">508</td>
    <td class="txt_left" aria-label="제목">
<a href="/board.es?mid=a10501010000&amp;bid=0042&amp;act=view&amp;list_no=77793&amp;tag=&amp;nPage=2" onclick="goView('77793'); return false;">   2026년 ESS중앙계약시장 사업자 의견수렴 간담회 개최</a></td>
    <td aria-label="작성자">선도시장팀</td>
    <td aria-label="등록일">2026/07/23</td>
    <td aria-label="조회수">4505</td>
    <td aria-label="첨부파일"></td>
  </tr>
  <tr>
    <td aria-label="번호">489</td>
    <td class="txt_left" aria-label="제목">
<a href="/board.es?mid=a10501010000&amp;bid=0042&amp;act=view&amp;list_no=77367&amp;tag=&amp;nPage=4" onclick="goView('77367'); return false;">   VPP사업자 및 재생e 입찰제 참여 발전사업자 대상 제도 개선 간담회 시행</a></td>
    <td aria-label="작성자">실시간시장팀</td>
    <td aria-label="등록일">2026/05/13</td>
    <td aria-label="조회수">2677</td>
    <td aria-label="첨부파일"></td>
  </tr>
  <tr>
    <td aria-label="번호">470</td>
    <td class="txt_left" aria-label="제목">
<a href="/board.es?mid=a10501010000&amp;bid=0042&amp;act=view&amp;list_no=77164&amp;tag=&amp;nPage=5" onclick="goView('77164'); return false;">   [재공모] 한국전력거래소 비상임감사 후보자 모집</a></td>
    <td aria-label="작성자">인사부</td>
    <td aria-label="등록일">2026/04/15</td>
    <td aria-label="조회수">700</td>
    <td aria-label="첨부파일"></td>
  </tr>
</tbody></table>
</div>
"""

# 정상 공지 — 행사일·시각·장소가 본문에 라벨과 함께 있다.
KPX_VIEW_77793 = """
<article class="board_view">
  <div class="title">2026년 ESS중앙계약시장 사업자 의견수렴 간담회 개최</div>
  <ul class="info">
    <li class="date"><strong>작성일</strong><span>2026/07/23 20:11</span></li>
    <li class="name"><strong>작성자</strong>선도시장팀</li>
  </ul>
  <div class="contents">
    안녕하십니까, 전력거래소 선도시장팀에서 안내드립니다.<br />
    &#8216;26년 ESS 중앙계약시장 사업자 의견수렴 간담회를 아래와 같이 개최하오니, 관심있는 사업자분들께서는 참석하여 주시기 바랍니다.<br />
    1. 일 시 : 2026년 7월 29일(수) 14:00 ~ 15:30<br />
    2. 장 소 : 스페이스쉐어 서울중부센터 9층 스카이홀(약 300명 수용 가능)<br />
    3. 주요내용<br />
    ㅇ 지난 사업 주요 성과 및 개선 방향 등<br />
    4. 협조요청사항<br />
    ㅇ 원활한 설명회 진행을 위하여 기관당 2인 이내로 참석하여 주시기 바랍니다.<br />
  </div>
</article>
"""

# 행사일과 신청 마감일이 같은 본문에 있다. 마감일이 칸을 차지하면 안 된다.
KPX_VIEW_77367 = """
<article class="board_view">
  <div class="title">VPP사업자 및 재생e 입찰제 참여 발전사업자 대상 제도 개선 간담회 시행</div>
  <div class="contents">
    안녕하십니까. 전력거래소 실시간시장팀에서 공지드립니다.<br />
    ㅇ 일시 : 5/26(화) 10시~12시<br />
    ㅇ 장소 : 추후 참석자 대상 별도 공지(서울 예정)<br />
    ㅇ 주요 내용 : 제도 개선 의견 수렴 및 논의<br />
    ㅇ 참석 방법 : 5/19(화)까지 참석자 명단(붙임파일 참조)을 제출<br />
    ㅇ 사전 의견 제출 : 5/19(화)까지 제출(양식 무관)<br />
  </div>
</article>
"""

# 본문이 포스터 이미지 한 장뿐인 공지. 행사일을 아는 사람이 아무도 없다.
KPX_VIEW_78098 = """
<article class="board_view">
  <div class="title">제12차 전기본 공개토론회</div>
  <ul class="info">
    <li class="date"><strong>작성일</strong><span>2026/09/09 14:19</span></li>
  </ul>
  <div class="contents">
    <img src="https://www.kpx.or.kr/upload/editor/178893114515800.png" alt="석탄발전 토론회_포스터_게시용" border="0" />
  </div>
</article>
"""

# 날짜를 말하긴 하는데 날짜가 아니다.
KPX_VIEW_VAGUE = """
<article class="board_view">
  <div class="title">VPP용 분산자원연계장치 기술규격 관련 설명회 참석 의향조사</div>
  <div class="contents">
    1. 설명회 개요<br />
    주요 내용 : VPP용 분산자원연계장치 기술규격 개정(안) 안내<br />
    개최 일자 : 10월 말(구체적 날짜는 추후 재 공지)<br />
  </div>
</article>
"""

KPX_BODIES = {
    "78098": KPX_VIEW_78098,
    "77793": KPX_VIEW_77793,
    "77367": KPX_VIEW_77367,
}


class TestKpxList(unittest.TestCase):
    """목록은 게시물만 준다 — 여기서 일정이 만들어지면 게시일이 행사일이 된다."""

    def rows(self):
        return event_sources.parse_kpx_list(KPX_LIST_PAGE)

    def test_the_listing_yields_posts_not_events(self):
        rows = self.rows()
        self.assertEqual(5, len(rows))
        self.assertEqual({"post_id", "title", "posted", "url"}, set(rows[0]))
        # 게시일이지 행사일이 아니다. 이 값이 date 칸에 들어가면 안 된다.
        self.assertEqual(date(2026, 9, 9), rows[0]["posted"])

    def test_the_new_badge_is_not_part_of_the_title(self):
        self.assertEqual("제12차 전기본 공개토론회", self.rows()[0]["title"])

    def test_stripping_the_badge_does_not_eat_english_titles(self):
        """표식을 글자로 지우면 'New Nuclear' 에서 낱말 하나가 함께 사라진다."""
        page = ('<tr><td aria-label="번호">1</td>'
                '<td class="txt_left" aria-label="제목">'
                '<a href="/board.es?mid=a10501010000&amp;bid=0042&amp;act=view'
                '&amp;list_no=99&amp;nPage=1"><i class="xi-new"></i>'
                '<span class="sr_only">새글</span> Roadmaps to New Nuclear 2026'
                ' 국제 포럼</a></td>'
                '<td aria-label="등록일">2026/09/09</td></tr>')
        self.assertEqual("Roadmaps to New Nuclear 2026 국제 포럼",
                         event_sources.parse_kpx_list(page)[0]["title"])

    def test_the_post_url_carries_no_page_number(self):
        """목록 쪽 번호가 URL 에 남으면 새 글이 올라올 때마다 같은 행사가 새 일정이 된다."""
        for row in self.rows():
            self.assertNotIn("nPage", row["url"])
            self.assertIn(f"list_no={row['post_id']}", row["url"])


class TestKpxDetail(unittest.TestCase):
    """행사일은 **본문의 라벨 줄에서만** 읽는다."""

    def test_the_event_date_comes_from_the_body_not_the_posting_date(self):
        text = event_sources.kpx_body_text(KPX_VIEW_77793)
        span, clause = event_sources.kpx_event_span(text, date(2026, 7, 23))
        self.assertEqual((date(2026, 7, 29), date(2026, 7, 29), "point"), span)
        self.assertIn("2026년 7월 29일", clause)

    def test_the_application_deadline_never_becomes_the_event_date(self):
        """같은 본문에 5/26 행사와 5/19 제출 마감이 있다. 칸에 서는 것은 5/26 이다."""
        text = event_sources.kpx_body_text(KPX_VIEW_77367)
        span, _ = event_sources.kpx_event_span(text, date(2026, 5, 13))
        self.assertEqual(date(2026, 5, 26), span[0])

    def test_a_poster_only_notice_has_no_event_date(self):
        text = event_sources.kpx_body_text(KPX_VIEW_78098)
        self.assertEqual((None, ""),
                         event_sources.kpx_event_span(text, date(2026, 9, 9)))

    def test_a_month_without_a_day_is_not_a_date(self):
        """'10월 말(구체적 날짜는 추후 재 공지)' 로 칸을 세우면 그건 지어낸 날이다."""
        text = event_sources.kpx_body_text(KPX_VIEW_VAGUE)
        span, clause = event_sources.kpx_event_span(text, date(2026, 8, 10))
        self.assertIsNone(span)
        self.assertIn("10월 말", clause)      # 라벨은 읽었다 — 날짜가 없을 뿐이다

    def test_a_spaced_label_is_still_a_label(self):
        """관공서는 '일 시'·'장 소' 처럼 자간을 벌려 쓴다."""
        text = event_sources.kpx_body_text(KPX_VIEW_77793)
        self.assertEqual("스페이스쉐어 서울중부센터 9층 스카이홀",
                         event_sources.kpx_place(
                             event_sources._kpx_field(
                                 text, event_sources._KPX_PLACE_RE)))

    def test_a_place_that_says_it_is_unknown_is_not_a_place(self):
        text = event_sources.kpx_body_text(KPX_VIEW_77367)
        self.assertEqual("", event_sources.kpx_place(
            event_sources._kpx_field(text, event_sources._KPX_PLACE_RE)))

    def test_the_hour_is_read_even_without_a_colon(self):
        self.assertEqual("10:00", event_sources.kpx_time("5.26.(화) 10시~12시"))
        self.assertEqual("14:00", event_sources.kpx_time("2026년 7월 29일(수) 14:00 ~ 15:30"))
        self.assertEqual("14:30", event_sources.kpx_time("오후 2시 30분"))
        self.assertEqual("", event_sources.kpx_time("추후 공지"))


class TestKpxEvents(unittest.TestCase):
    """목록 → 판정 → 상세 → 일정. 상세는 판정을 통과한 글만 연다."""

    def build(self, **kwargs):
        rows = event_sources.parse_kpx_list(KPX_LIST_PAGE)
        self.opened = []

        def body(row):
            self.opened.append(row["post_id"])
            return event_sources.kpx_body_text(KPX_BODIES.get(row["post_id"], ""))

        return event_sources.kpx_events(rows, body, **kwargs)

    def kept(self, rows):
        return [row for row in rows if not row.get("_dropped")]

    def test_a_real_notice_becomes_a_dated_event(self):
        by_post = {row["post_id"]: row for row in self.kept(self.build())}
        row = by_post["77793"]
        self.assertEqual("2026-07-29", row["date"])
        self.assertEqual("point", row["kind"])
        self.assertEqual("14:00", row["time"])
        self.assertEqual("스페이스쉐어 서울중부센터 9층 스카이홀", row["place"])
        self.assertEqual("kpx_notice", row["source_id"])
        self.assertEqual("한국전력거래소", row["publisher"])
        # 게시일은 '언제부터 알던 일정인가' 로만 쓴다.
        self.assertEqual("2026-07-23", row["first_seen"])
        self.assertIn("list_no=77793", row["url"])

    def test_the_board_owner_is_display_only_and_never_judged(self):
        """게시판 주인을 판정에 넣으면 '전력거래' 가 걸려 채용 공고까지 통과한다."""
        row = next(r for r in self.kept(self.build()) if r["post_id"] == "77793")
        self.assertEqual("한국전력거래소", row["host"])
        self.assertEqual("", row["organizer"])

    def test_recruitment_and_intake_notices_never_open_a_detail_page(self):
        """제목만으로 확실한 것은 본문을 열지 않는다 — 요청을 아끼는 자리다."""
        self.build()
        self.assertNotIn("78092", self.opened)   # 참여기업 모집 공고
        self.assertNotIn("77164", self.opened)   # 비상임감사 후보자 모집

    def test_a_notice_without_an_event_date_makes_no_event(self):
        rows = self.build()
        self.assertIn("78098", self.opened)      # 열어는 봤다
        self.assertIn("no_event_date", [row.get("_dropped") for row in rows])
        self.assertNotIn("78098", [row["post_id"] for row in self.kept(rows)])

    def test_a_bidding_scheme_is_not_procurement_noise(self):
        """'재생e 입찰제' 의 입찰은 조달이 아니라 전력시장 제도다."""
        posts = [row["post_id"] for row in self.kept(self.build())]
        self.assertIn("77367", posts)

    def test_the_detail_budget_caps_the_requests(self):
        """게시판이 개편돼 판정이 무너지는 날 스무 건을 연달아 받지 않는다."""
        self.build(max_details=1)
        self.assertEqual(1, len(self.opened))

    def test_every_event_keeps_its_post_id(self):
        for row in self.kept(self.build()):
            self.assertTrue(row["post_id"])


class TestKpxRescheduling(unittest.TestCase):
    """연기·장소변경 공지가 새 일정이 아니라 같은 일정의 갱신이 되는가."""

    def event(self, when: str, place: str = "나주 본사"):
        return event_sources.make_event(
            source_id="kpx_notice", publisher="한국전력거래소",
            title="2026년 ESS중앙계약시장 사업자 간담회 개최",
            url="https://new.kpx.or.kr/board.es?mid=a10501010000&bid=0042"
                "&act=view&list_no=77371",
            posted=date(2026, 5, 14), start=date.fromisoformat(when),
            end=date.fromisoformat(when), kind="point", place=place,
            host="한국전력거래소", post_id="77371")

    def test_a_postponed_event_moves_instead_of_doubling(self):
        first = self.event("2026-05-19")
        moved = self.event("2026-05-26", place="서울")
        self.assertNotEqual(first["id"], moved["id"])   # 날짜가 id 의 재료다
        rows, added = event_sources.merge_events([first], [moved])
        self.assertEqual(1, len(rows), "연기된 행사가 두 줄로 섰다")
        self.assertEqual(0, added, "갱신을 신규로 셌다")
        self.assertEqual("2026-05-26", rows[0]["date"])
        self.assertEqual("서울", rows[0]["place"])
        # 최초 확인일은 처음 본 날 그대로다.
        self.assertEqual("2026-05-14", rows[0]["first_seen"])

    def test_posts_without_an_id_keep_the_old_behaviour(self):
        left = event_sources.make_event(
            source_id="kaif_calendar", publisher="한국원자력산업협회",
            title="2026 경남 SMR 국제 콘퍼런스", url="https://example.org/a",
            posted=date(2026, 8, 29), start=date(2026, 11, 16),
            end=date(2026, 11, 18), kind="range")
        right = event_sources.make_event(
            source_id="kaif_calendar", publisher="한국원자력산업협회",
            title="2026 경남 SMR 국제 콘퍼런스", url="https://example.org/b",
            posted=date(2026, 8, 29), start=date(2026, 11, 16),
            end=date(2026, 11, 18), kind="range")
        rows, added = event_sources.merge_events([left], [right])
        self.assertEqual(2, len(rows))
        self.assertEqual(1, added)


class TestKpxInterestVocabulary(unittest.TestCase):
    """전력거래소 공지를 들이면서 넓힌 관심 어휘 — 넓힌 만큼 새는지 본다."""

    def test_the_power_market_events_this_board_actually_posts(self):
        for title in ("2026년 ESS중앙계약시장 사업자 의견수렴 간담회 개최",
                      "전력거버넌스 포럼 개최 공고",
                      "제12차 전기본 공개토론회",
                      "전기국가(4차) 및 수요·재생e 전망(5차) 대국민 정책토론회",
                      "VPP 사업 활성화를 위한 그린e안심결제시스템 설명회(3차) 안내"):
            self.assertTrue(event_relevance.judge(title)["ok"], title)

    def test_the_administrative_notices_this_board_actually_posts(self):
        for title in ("2026년 한국전력거래소 창업·벤처기업 지원사업 참여기업 모집 공고",
                      "[재공모] 한국전력거래소 비상임감사 후보자 모집",
                      "개인정보 처리방침 개정 안내(2026. 8. 1.)",
                      "계통해석프로그램(PSS/E) v36 버전 기준 변경 안내",
                      "'26년도 ESS 중앙계약시장 입찰공고 개설 관련 사업자 의견 수렴 관련 안내사항",
                      "2026년 전력거래소 부패취약 요소 발굴 및 개선과제 외부 공모전",
                      "전력거래시스템(MMS) 신규 오픈 안내",
                      "라오스(비엔티안) 전력기자재 사절단 참가 공고"):
            self.assertFalse(event_relevance.judge(title)["ok"], title)

    def test_a_bare_power_word_is_not_an_interest_match(self):
        """'전력' 한 낱말을 어휘에 넣으면 이 게시판의 행정공지가 전부 통과한다."""
        self.assertFalse(event_relevance.relevance("전력구입비 절감 사례")["ok"])

    def test_ess_is_never_matched_as_a_substring(self):
        """'ess' 를 낱말로 두면 business·process·assessment 가 전부 걸린다."""
        for text in ("Nuclear Business Forum", "IAEA process assessment workshop"):
            self.assertNotIn("renewable_grid", event_relevance.topics(text))


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
