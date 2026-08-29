"""앞으로 30일 달력 — 실제로 났던 날짜 오류를 하나씩 못박는다.

여기 있는 문장은 전부 **실측 원문**이다(2026-08-20~25 수집분). 지난 '예정'
코너가 이 문장들에서 틀린 날짜·틀린 이름을 냈고, 그래서 코너가 꺼졌다
(`weekly_sections.SHOW_WEEKLY_UPCOMING`). 같은 문장으로 다시 재보는 것이
이 파일의 존재 이유다 — 회귀가 나면 그 자리에서 걸린다.
"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import event_calendar  # noqa: E402

TODAY = date(2026, 8, 29)


def article(**fields) -> dict:
    """달력이 보는 최소한의 기사. 없는 필드는 화면이 빈 값으로 다룬다."""
    base = {"hash": "h1", "title_kr": "", "summary": "", "detail": "",
            "article_date": "2026-08-20", "url": "https://example.com/1",
            "publisher": "테스트", "topics": []}
    return {**base, **fields}


def labels(payload: dict) -> list[tuple[str, str, str]]:
    return [(row["date"], row["end_date"], row["label"]) for row in payload["events"]]


class TestLabelComesFromTheDatedClause(unittest.TestCase):
    """①번 오류 — 날짜는 절에서, 이름은 제목에서 따로 오던 것."""

    HANBIT = article(
        title_kr="한빛원전, 25일부터 사용후핵연료 건식저장시설 주민 설명회",
        detail="25일 홍농읍 설명회를 시작으로 지역별 설명회와 9월 1일 토론회를 "
               "거쳐 2028년 6월 착공, 2030년 7월 운영을 목표로 한다.")

    def test_the_chip_names_what_happens_on_that_day(self):
        """9월 1일 칸에 서는 것은 '토론회'다 — 제목의 '설명회'는 8월 25일이다.

        지난 코너는 이 기사에서 날짜(9/1)와 제목(설명회)을 따로 가져와 붙였다.
        화면에는 "9월 1일 · 한빛원전 …주민 설명회"가 떴고, 그 설명회는 실제로는
        일주일 전 일이었다.
        """
        payload = event_calendar.build([self.HANBIT], TODAY)
        self.assertEqual(len(payload["events"]), 1)
        row = payload["events"][0]
        self.assertEqual(row["date"], "2026-09-01")
        self.assertIn("토론회", row["label"])
        self.assertNotIn("설명회", row["label"])

    def test_the_clause_travels_with_the_chip(self):
        """근거는 언제나 그 문장이다 — 짧은 이름은 길잡이일 뿐이다."""
        row = event_calendar.build([self.HANBIT], TODAY)["events"][0]
        self.assertIn("9월 1일 토론회", row["clause"])
        self.assertEqual(row["title"], self.HANBIT["title_kr"])

    def test_a_label_word_that_is_not_in_the_source_is_refused(self):
        """이름의 모든 낱말은 원문에 있어야 한다. 지어낸 이름은 게이트가 막는다."""
        row = event_calendar.build([self.HANBIT], TODAY)["events"][0]
        forged = {**row, "label": "한빛원전 준공식"}
        self.assertEqual(
            event_calendar.verify(forged, self.HANBIT, TODAY, date(2026, 9, 28)),
            "label_not_in_source")


class TestTheNounMustBeTheEventOnThatDay(unittest.TestCase):
    """이름을 고르다 틀린 자리들. 전부 실제 빌드 입력(news.json)에서 났다."""

    def test_a_finished_event_is_not_the_name_of_a_future_date(self):
        """과거형 꼬리가 붙은 명사는 앞날의 일이 아니다.

        실측: "산업안전보건 강조기간이 종료되었음에도 … 8월 31일까지 연장
        운영"에서 8/31 칩 이름이 '강조기간 종료'로 나갔다. 그 종료는 지나간
        일이고 8/31 은 연장 운영의 끝이다.
        """
        rows = event_calendar.build([article(
            title_kr="한수원 김회천 사장, 고리원전 폭염 현장 점검",
            detail="한수원은 정부의 산업안전보건 강조기간이 종료되었음에도 자체 "
                   "폭염 대응기간을 8월 31일까지 연장 운영하며 현장 안전관리를 "
                   "이어갈 계획이다.")], TODAY)["events"]
        for row in rows:
            self.assertNotIn("종료", row["label"])

    def test_the_head_of_a_longer_word_is_not_an_event(self):
        """'시행령'의 '시행'은 사건이 아니라 문서 이름의 일부다.

        실측: 9/21 칩이 '정부 특별법 시행 마감'으로 나갔다 — 그 날짜는 입법예고
        마감일이고 시행일은 12월 17일이었다. 한 화면에서 두 날짜가 뒤바뀌었다.
        """
        row = event_calendar.build([article(
            title_kr="정부, 북극항로 특별법 시행령 마련",
            detail="해양수산부는 11일부터 9월 21일까지 북극항로 특별법 시행령 "
                   "제정안을 입법예고하며, 오는 12월 17일 시행되는 특별법의 "
                   "위임사항을 구체화한다.")], TODAY)["events"][0]
        self.assertIn("입법예고", row["label"])
        self.assertEqual(row["date"], "2026-09-21")

    def test_the_nearest_noun_wins_even_when_it_comes_before_the_date(self):
        """날짜가 사건 바로 뒤에 붙는 꼴 — "임기 만료(9월 19일)에 따라".

        뒤쪽만 보면 9/19 가 임원추천위원회의 날짜가 된다. 그 날짜는 만료일이다.
        """
        row = event_calendar.build([article(
            title_kr="한국전력공사, 김동철 사장 임기 만료에 따라 신임 사장 공모 착수",
            detail="한국전력공사가 김동철 현 사장의 임기 만료(9월 19일)에 따라 "
                   "임원추천위원회를 통해 신임 사장 공모 절차를 시작했다.")],
            TODAY)["events"][0]
        self.assertIn("만료", row["label"])
        self.assertNotIn("위원회", row["label"])

    def test_stripping_a_particle_never_invents_a_word(self):
        """'표준설계인가'의 '가'는 조사가 아니다 — 떼면 원문에 없는 말이 된다."""
        row = event_calendar.build([article(
            title_kr="KINS, 선진원자로 안전기술 심포지엄 개최",
            detail="9월 2일에는 혁신형 SMR의 표준설계인가 심사 방향에 대한 "
                   "발표와 토론이 진행될 예정이다.")], TODAY)["events"][0]
        self.assertIn("표준설계인가", row["label"])

    def test_a_connective_is_not_a_modifier(self):
        """'따라'·'통해' 같은 연결형은 이름이 아니다."""
        self.assertEqual(event_calendar._strip_particle("국회에"), "국회")
        self.assertEqual(event_calendar._strip_particle("마을"), "마을")


class TestRangesAreNotPoints(unittest.TestCase):
    """②번 오류 — "A부터 B까지"를 점 하나로 접던 것."""

    YEONGDEOK = article(
        title_kr="영덕군, 신규 원전 명칭 공모…10월 최종 결과 발표",
        detail="영덕군이 신규 원전 명칭을 8월 27일부터 9월 10일까지 공모하며, "
               "10월 중 최종 후보 2개 안을 선정해 발표할 예정이다.",
        article_date="2026-08-25")

    POHANG = article(
        title_kr="포항 장기면 주민들, 원자력 지역자원시설세 운용 원상회복 요구 집회 예고",
        detail="포항시 장기면 주민들이 원자력발전 지역자원시설세 특별회계 운용 "
               "조례 개정에 반발하며 8월 23일부터 9월 20일까지 집회를 예고했다.",
        article_date="2026-08-21")

    def test_a_period_keeps_both_ends(self):
        """공모 기간은 8/27~9/10 이다. 끝만 찍으면 그날 시작하는 행사로 읽힌다."""
        row = event_calendar.build([self.YEONGDEOK], TODAY)["events"][0]
        self.assertEqual((row["date"], row["end_date"]), ("2026-08-27", "2026-09-10"))
        self.assertEqual(row["kind"], "range")

    def test_a_period_already_under_way_survives(self):
        """시작이 지났다고 버리지 않는다 — 진행 중인 일정이 통째로 사라진다.

        같은 집회가 W34 저장본에서는 8/23, 다시 계산하면 9/20 이었다. 어느
        쪽도 틀리지 않았고 둘 다 반쪽이었다 — 그것은 기간이다.
        """
        row = event_calendar.build([self.POHANG], TODAY)["events"][0]
        self.assertEqual((row["date"], row["end_date"]), ("2026-08-23", "2026-09-20"))
        self.assertEqual(row["kind"], "range")

    def test_two_dates_without_a_period_marker_are_not_a_period(self):
        """표지 없이 두 날짜가 있다고 기간이 아니다 — 발표일과 시행일은 다른 일이다."""
        row = event_calendar.build([article(
            title_kr="정부, 제도 개편",
            detail="8월 20일 발표한 계획에 따라 9월 3일 국회에 제출될 예정이다.",
        )], TODAY)["events"][0]
        self.assertEqual((row["date"], row["end_date"]), ("2026-09-03", "2026-09-03"))
        self.assertEqual(row["kind"], "point")


class TestMonthPrecisionNeverTakesADayBox(unittest.TestCase):
    """③번 오류 — '9월 중'을 9월 1일 칸에 못박던 것.

    실측 2026-08-29: 이 규칙이 없을 때 9월 1일 한 칸에 5건이 몰렸고 그중
    어느 것도 9월 1일 일정이 아니었다.
    """

    MONTHLY = article(
        title_kr="정부, 9월 대미 투자 1호 프로젝트 발표 예정",
        detail="정부가 9월 중 대미 투자 1호 프로젝트를 발표할 예정이다.",
        event_date="2026-09-01", event_date_type="scheduled",
        event_date_precision="month", event_date_source="article_text")

    def test_it_goes_to_the_month_strip_not_the_grid(self):
        payload = event_calendar.build([self.MONTHLY], TODAY)
        self.assertEqual(payload["events"], [])
        self.assertEqual([row["month"] for row in payload["month_notes"]], ["2026-09"])

    def test_the_same_month_plan_from_two_outlets_is_one_line(self):
        """이름만 비교하면 안 접힌다 — 한쪽은 제목으로, 다른 쪽은 명사구로 남는다.

        실측 2026-08-29: '영덕 신규원전 건설사업 전략환경영향평가'와 '영덕
        신규원전 2기 건설 본궤도…9월 전략환경영향평가 초안 제출'이 두 줄로 섰다.
        """
        rows = [
            article(hash="a", title_kr="영덕 신규원전 건설사업, 환경영향평가 착수",
                    detail="영덕 신규원전 전략환경영향평가가 9월 중 착수될 예정이다.",
                    event_date="2026-09-01", event_date_type="scheduled",
                    event_date_precision="month", event_date_source="article_text"),
            article(hash="b", title_kr="영덕 신규원전 2기 건설 본궤도…전략환경영향평가 초안 제출",
                    detail="영덕 신규원전 전략환경영향평가 초안이 9월 중 제출될 예정이다.",
                    event_date="2026-09-01", event_date_type="scheduled",
                    event_date_precision="month", event_date_source="article_text"),
        ]
        self.assertEqual(len(event_calendar.build(rows, TODAY)["month_notes"]), 1)

    def test_a_month_outside_the_window_is_not_shown(self):
        """창이 8/29~9/28 이면 10월은 이 화면이 말할 몫이 아니다."""
        payload = event_calendar.build([article(
            title_kr="영덕군, 10월 최종 결과 발표",
            detail="영덕군이 10월 중 최종 후보를 발표할 예정이다.",
            event_date="2026-10-01", event_date_type="scheduled",
            event_date_precision="month", event_date_source="article_text",
        )], TODAY)
        self.assertEqual(payload["month_notes"], [])


class TestTheSameEventIsOneChip(unittest.TestCase):
    """한 사건이 기사마다 다른 주체로 서면 같은 칸에 두 번 뜬다."""

    def test_two_outlets_on_the_same_forum_fold_into_one(self):
        rows = [
            article(hash="a", title_kr="한수원, 한빛원전 사용후핵연료 건식저장시설 주민 설명회 개최",
                    detail="9월 1일 토론회를 열 예정이다.", article_date="2026-08-21"),
            article(hash="b", title_kr="한빛원전, 25일부터 사용후핵연료 건식저장시설 주민 설명회",
                    detail="25일 홍농읍 설명회를 시작으로 9월 1일 토론회를 거친다.",
                    article_date="2026-08-20"),
        ]
        payload = event_calendar.build(rows, TODAY)
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["source_count"], 2)

    def test_one_article_is_one_source_even_when_it_says_it_twice(self):
        """제목 절과 본문 절이 같은 일정을 말하면 기사 하나다.

        실측: 영덕 공모 상세에 같은 국민일보 기사가 두 줄로 섰고 '보도 2건'이
        됐다 — 두 곳이 보도한 것처럼 읽힌다.
        """
        row = event_calendar.build([article(
            title_kr="영덕군, 신규 원전 명칭 공모…8월 27일부터 9월 10일까지",
            detail="영덕군이 신규 원전 명칭을 8월 27일부터 9월 10일까지 공모한다.",
            article_date="2026-08-25")], TODAY)["events"][0]
        self.assertEqual(row["source_count"], 1)
        self.assertEqual(len(row["sources"]), 1)

    def test_different_events_on_the_same_day_stay_apart(self):
        """같은 날이라고 접지 않는다 — 다른 일은 다른 줄이다."""
        rows = [
            article(hash="a", title_kr="국회물포럼, 산업용수 토론회 개최",
                    detail="국회물포럼이 8월 31일 산업용수 확보 방안 토론회를 개최한다."),
            article(hash="b", title_kr="월성원자력본부, 2027년 지원사업 공모",
                    detail="월성원자력본부가 2027년도 지원사업 공모를 8월 31일까지 진행한다."),
        ]
        self.assertEqual(len(event_calendar.build(rows, TODAY)["events"]), 2)


class TestTheWindowSlidesEveryBuild(unittest.TestCase):
    """상태 파일이 없다 — 창이 미끄러지면 지난 일정은 저절로 빠진다."""

    PAST = article(title_kr="한수원, 설명회 개최",
                   detail="한수원이 8월 20일 설명회를 개최했다.",
                   article_date="2026-08-15")
    FAR = article(title_kr="한수원, 착공식 개최",
                  detail="한수원이 12월 3일 착공식을 개최할 예정이다.")

    def test_yesterday_is_gone_and_far_future_has_not_arrived(self):
        payload = event_calendar.build([self.PAST, self.FAR], TODAY)
        self.assertEqual(payload["events"], [])
        self.assertEqual((payload["start"], payload["end"]),
                         ("2026-08-29", "2026-09-28"))

    def test_an_article_older_than_the_lookback_is_not_read(self):
        """두 달 전 기사가 말한 일정은 더 싣지 않는다 — 취소돼도 알 길이 없다."""
        stale = article(title_kr="한수원, 토론회 개최",
                        detail="한수원이 9월 1일 토론회를 개최할 예정이다.",
                        article_date="2026-06-01")
        self.assertEqual(event_calendar.build([stale], TODAY)["events"], [])


class TestVerifyIsTheContract(unittest.TestCase):
    """게이트가 실제로 막는지 본다. 만드는 코드와 재는 코드가 같은 값을 쓰면
    검사가 통과해도 아무 말이 아니므로, 여기서는 **틀린 값을 직접 넣어** 잰다."""

    SOURCE = article(title_kr="한수원, 토론회 개최",
                     detail="한수원이 9월 1일 토론회를 개최할 예정이다.")
    HORIZON = date(2026, 9, 28)

    def _verify(self, **overrides) -> str:
        base = {"date": "2026-09-01", "end_date": "2026-09-01", "kind": "point",
                "label": "한수원 토론회", "clause": self.SOURCE["detail"],
                "origin": "clause"}
        return event_calendar.verify({**base, **overrides}, self.SOURCE,
                                     TODAY, self.HORIZON)

    def test_a_clean_event_passes(self):
        self.assertEqual(self._verify(), "")

    def test_a_date_that_is_not_in_the_clause_is_refused(self):
        self.assertEqual(self._verify(date="2026-09-02", end_date="2026-09-02"),
                         "date_not_in_clause")

    def test_a_clause_without_a_schedule_marker_is_refused(self):
        """날짜가 적혀 있다고 다 일정이 아니다 — 앞날을 말하는 표지가 있어야 한다."""
        self.assertEqual(self._verify(clause="한수원이 9월 1일 결과를 통보했다"),
                         "no_schedule_marker")

    def test_a_date_outside_the_window_is_refused(self):
        self.assertEqual(self._verify(date="2026-10-05", end_date="2026-10-05"),
                         "out_of_window")

    def test_a_backwards_span_is_refused(self):
        self.assertEqual(self._verify(date="2026-09-05", end_date="2026-09-01"),
                         "span_invalid")


class TestPayloadShape(unittest.TestCase):
    """화면이 기대는 모양. 필드 이름이 바뀌면 칸이 조용히 빈다."""

    def test_every_event_carries_what_the_chip_and_dialog_need(self):
        payload = event_calendar.build([article(
            title_kr="한수원, 토론회 개최",
            detail="한수원이 9월 1일 토론회를 개최할 예정이다.")], TODAY)
        row = payload["events"][0]
        for field in ("id", "date", "end_date", "kind", "label", "clause",
                      "title", "url", "publisher", "sources", "source_count",
                      "first_seen"):
            self.assertIn(field, row)
        self.assertTrue(row["id"].startswith("ev-"))
        self.assertEqual(payload["days"], event_calendar.HORIZON_DAYS)

    def test_an_empty_window_is_a_valid_payload(self):
        """재료가 없어도 화면은 달력을 그린다 — 빈 칸이 정상이다."""
        payload = event_calendar.build([], TODAY)
        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["month_notes"], [])
        self.assertEqual(payload["start"], "2026-08-29")


# ── 공식 일정원과의 통합 ─────────────────────────────────────────────────
#
# 아래 값은 전부 2026-08-29 실측이다. 협회 일정표·협회 공지·학회 공지에서
# 실제로 받은 행사이고, 기사 쪽 문장도 그 행사를 다룬 실제 어법을 따른다.

def official(**fields) -> dict:
    """`event_sources` 가 저장하는 공식 일정 한 줄의 최소 모양."""
    base = {"id": "of-abc123", "date": "2026-09-09", "end_date": "2026-09-09",
            "kind": "point", "time": "14:00",
            "label": "AI 시대 국가경쟁력을 위한 전원믹스와 시장제도 심포지움",
            "notice_title": "AI 시대 국가경쟁력을 위한 전원믹스와 시장제도 "
                            "심포지움 개최(9.9(수) 14:00, 대한상공회의소)",
            "host": "한국원자력학회", "organizer": "",
            "place": "대한상공회의소",
            "url": "https://www.kns.org/boards/chk_view/notice/103327",
            "source_id": "kns_notice", "publisher": "한국원자력학회",
            "first_seen": "2026-08-14", "topics": ["power_market"]}
    return {**base, **fields}


class TestOfficialEventsStandOnTheSameGrid(unittest.TestCase):
    """기관이 공지한 일정이 기사 일정과 한 달력에 선다."""

    def test_an_official_notice_becomes_a_chip(self):
        payload = event_calendar.build([], TODAY, official=[official()])
        self.assertEqual(len(payload["events"]), 1)
        row = payload["events"][0]
        self.assertEqual(row["date"], "2026-09-09")
        self.assertEqual(row["origin"], "official")
        self.assertEqual(row["kind"], "point")

    def test_what_the_source_gave_us_is_preserved(self):
        """사용자가 요구한 칸 — 날짜·시간·행사명·주최·장소·출처 URL·최초 확인일."""
        row = event_calendar.build([], TODAY, official=[official()])["events"][0]
        self.assertEqual(row["time"], "14:00")
        self.assertEqual(row["host"], "한국원자력학회")
        self.assertEqual(row["place"], "대한상공회의소")
        self.assertEqual(row["first_seen"], "2026-08-14")
        self.assertEqual(row["sources"][0]["url"],
                         "https://www.kns.org/boards/chk_view/notice/103327")
        self.assertEqual(row["sources"][0]["source_kind"], "official")

    def test_the_notice_travels_as_the_evidence(self):
        """근거는 기관이 실제로 올린 공지 제목이다 — 기사 경로의 '절'과 같은 자리."""
        row = event_calendar.build([], TODAY, official=[official()])["events"][0]
        self.assertIn("9.9(수) 14:00", row["clause"])

    def test_both_paths_fill_one_calendar(self):
        payload = event_calendar.build([article(
            title_kr="한수원, 토론회 개최",
            detail="한수원이 9월 1일 토론회를 개최할 예정이다.")],
            TODAY, official=[official()])
        self.assertEqual([row["date"] for row in payload["events"]],
                         ["2026-09-01", "2026-09-09"])


class TestOfficialBeatsReporting(unittest.TestCase):
    """같은 일정이 양쪽에 있으면 공식이 근거의 앞자리에 선다."""

    REPORT = article(
        title_kr="한국원자력학회, 전원믹스와 시장제도 심포지움 연다",
        detail="한국원자력학회는 9월 9일 대한상공회의소에서 심포지움을 개최한다.",
        hash="news-1", publisher="전기신문",
        url="https://example.com/report")

    def test_one_chip_not_two(self):
        payload = event_calendar.build([self.REPORT], TODAY,
                                       official=[official()])
        self.assertEqual(len(payload["events"]), 1)

    def test_the_official_row_owns_the_chip(self):
        row = event_calendar.build([self.REPORT], TODAY,
                                   official=[official()])["events"][0]
        self.assertEqual(row["origin"], "official")
        self.assertEqual(row["host"], "한국원자력학회")
        self.assertEqual(row["place"], "대한상공회의소")

    def test_the_article_is_kept_as_further_evidence(self):
        """진 쪽은 사라지지 않는다 — 몇 건이 이 일정을 다뤘는지가 값이다."""
        row = event_calendar.build([self.REPORT], TODAY,
                                   official=[official()])["events"][0]
        self.assertEqual(row["source_count"], 2)
        self.assertEqual([source["source_kind"] for source in row["sources"]],
                         ["official", "news"])

    def test_a_reported_range_cannot_stretch_an_official_date(self):
        """기관이 하루로 공지한 행사를 기사의 기간이 늘리면 안 된다.

        '기간이 점을 이긴다'는 규칙은 기사끼리의 규칙이다. 공식 날짜에까지
        적용하면 공식을 앞세운 이유가 통째로 무너진다.
        """
        stretched = article(
            title_kr="한국원자력학회, 전원믹스와 시장제도 심포지움 연다",
            detail="한국원자력학회는 9월 9일부터 9월 11일까지 심포지움을 개최한다.",
            hash="news-2", url="https://example.com/2")
        row = event_calendar.build([stretched], TODAY,
                                   official=[official()])["events"][0]
        self.assertEqual((row["date"], row["end_date"]),
                         ("2026-09-09", "2026-09-09"))
        self.assertEqual(row["kind"], "point")

    def test_the_earliest_sighting_wins(self):
        """최초 확인일은 낮은 쪽이다 — 학회 공지가 기사보다 먼저 알렸다."""
        row = event_calendar.build([self.REPORT], TODAY,
                                   official=[official()])["events"][0]
        self.assertEqual(row["first_seen"], "2026-08-14")


class TestTwoOfficialSourcesForOneEvent(unittest.TestCase):
    """협회 일정표와 협회 공지가 같은 행사를 다르게 적는다(실측 9/4 조찬강연회)."""

    LISTING = official(
        id="of-cal", date="2026-09-04", end_date="2026-09-04", time="",
        label="제226회 원자력계 조찬강연회",
        notice_title="제226회 원자력계 조찬강연회 (세미나)",
        host="", place="웨스틴조선 서울", source_id="kaif_calendar",
        publisher="한국원자력산업협회", first_seen="2026-08-29", topics=[],
        url="https://www.kaif.or.kr/ko/?c=240")
    NOTICE = official(
        id="of-not", date="2026-09-04", end_date="2026-09-04", time="",
        label="제226회 원자력계 조찬강연회 개최 및 참가등록 안내 (9. 4. (금) 개최)",
        notice_title="제226회 원자력계 조찬강연회 개최 및 참가등록 안내 (9. 4. (금) 개최)",
        host="한국원자력산업협회", place="", source_id="kaif_notice",
        publisher="한국원자력산업협회", first_seen="2026-07-29", topics=[],
        url="https://www.kaif.or.kr/ko/?c=193&ix=30040")

    def test_they_fold_into_one(self):
        """'강연회'는 기사용 사건 명사표에 없다 — 이름으로 재야 접힌다."""
        payload = event_calendar.build([], TODAY,
                                       official=[self.LISTING, self.NOTICE])
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["source_count"], 2)

    def test_the_gaps_are_filled_from_the_other_source(self):
        """한쪽은 장소를 주고 주최를 안 주며, 다른 쪽은 그 반대다."""
        row = event_calendar.build([], TODAY,
                                   official=[self.LISTING, self.NOTICE])["events"][0]
        self.assertEqual(row["place"], "웨스틴조선 서울")
        self.assertEqual(row["host"], "한국원자력산업협회")
        self.assertEqual(row["first_seen"], "2026-07-29")

    def test_a_different_event_on_the_same_day_stays_separate(self):
        """같은 날이라고 묶으면 안 된다 — 9/4 에 다른 행사가 또 있을 수 있다."""
        other = official(id="of-x", date="2026-09-04", end_date="2026-09-04",
                         label="사용후핵연료 관리정책 공청회",
                         notice_title="사용후핵연료 관리정책 공청회 개최 안내",
                         host="산업통상부", place="코엑스", time="",
                         url="https://example.go.kr/1", topics=["waste"])
        payload = event_calendar.build([], TODAY,
                                       official=[self.LISTING, other])
        self.assertEqual(len(payload["events"]), 2)


class TestTheGateRunsAgainAtBuildTime(unittest.TestCase):
    """저장본을 믿지 않는다. 판정을 고치면 다시 걷지 않아도 반영된다."""

    def test_an_off_topic_row_in_the_store_never_reaches_the_grid(self):
        payload = event_calendar.build([], TODAY, official=[official(
            label="2026년 제65차 대한핵의학회 추계 학술대회",
            notice_title="2026년 제65차 대한핵의학회 추계 학술대회 (세미나)",
            topics=[])])
        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["dropped"], {"official_off_topic": 1})

    def test_a_row_without_a_source_url_is_refused(self):
        """확인할 수 없는 일정은 세우지 않는다."""
        payload = event_calendar.build([], TODAY, official=[official(url="")])
        self.assertEqual(payload["dropped"], {"official_no_source_url": 1})

    def test_the_category_column_is_part_of_the_evidence(self):
        """협회 일정표는 구분 칸('(세미나)')으로 형식을 말한다.

        그 칸은 notice_title 에만 있고 label 에는 없다. 재판정이 label 만 보면
        'Roadmaps to New Nuclear 2026' 이 '일정이 아니다'로 버려진다(실측).
        """
        payload = event_calendar.build([], TODAY, official=[official(
            date="2026-09-18", end_date="2026-09-18", time="", host="", place="Paris",
            label="Roadmaps to New Nuclear 2026",
            notice_title="Roadmaps to New Nuclear 2026 (세미나)",
            url="https://www.oecd-nea.org/jcms/pl_119678", topics=["newbuild"])])
        self.assertEqual(len(payload["events"]), 1)

    def test_a_far_future_event_waits_outside_the_window(self):
        """협회 일정표는 몇 달 앞을 준다. 창에 들어오기 전에는 세우지 않는다."""
        payload = event_calendar.build([], TODAY, official=[official(
            date="2026-11-16", end_date="2026-11-18", kind="range",
            label="2026 경남 SMR 국제 콘퍼런스",
            notice_title="2026 경남 SMR 국제 콘퍼런스 (세미나)")])
        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["dropped"], {"official_out_of_window": 1})

    def test_no_official_material_changes_nothing(self):
        """새 경로가 죽어도 기존 달력은 그대로 선다."""
        news = [article(title_kr="한수원, 토론회 개최",
                        detail="한수원이 9월 1일 토론회를 개최할 예정이다.")]
        self.assertEqual(event_calendar.build(news, TODAY)["events"],
                         event_calendar.build(news, TODAY, official=[])["events"])


if __name__ == "__main__":
    unittest.main()
