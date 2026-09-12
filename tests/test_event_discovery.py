"""일정 발견 — 놓쳤던 것을 놓치지 않고, 새로 들인 것이 새 잡음이 되지 않게.

여기 있는 문장도 `test_event_calendar` 와 같이 **실측 원문**이다(archive
2026-07~09). 저 파일이 "날짜는 읽었는데 이름이 틀렸다"를 막는다면, 이 파일은
그 앞 단계 — **날짜를 아예 못 읽던 자리**를 막는다.

두 사례가 계기였다.

    9/18 제12차 전기본 제7차 정책토론회   본문이 "오는 18일" 이라고만 썼다
    WSCE 2026 (9.9~9.11)                "9월 9일부터 11일까지" 의 11일을 잃었다

둘 다 같은 함수에서 죽었다(`article_quality_gate` 의 날짜 추출기). 그리고 둘 다
`dropped` 에 한 줄도 남기지 않았다 — 버린 것이 아니라 **못 본 것**이라 셀 자리가
없었다. 그래서 이 파일은 세 가지를 함께 잰다.

    ① 읽는다        월 없는 날·범위의 꼬리
    ② 지어내지 않는다 보도일·과거·상대 표현
    ③ 센다          못 읽은 자리가 `missed` 에 남는가
"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import article_quality_gate  # noqa: E402
import event_calendar  # noqa: E402
import event_ledger  # noqa: E402
import event_relevance  # noqa: E402

TODAY = date(2026, 9, 10)


def article(**fields) -> dict:
    base = {"hash": "h1", "title_kr": "", "summary": "", "detail": "",
            "article_date": "2026-09-09", "url": "https://example.com/1",
            "publisher": "테스트", "topics": []}
    return {**base, **fields}


def days(text: str, reference: date) -> dict:
    return {day.isoformat(): how
            for day, how in article_quality_gate.scheduled_dates(text, reference).items()}


class TestADayWithoutItsMonth(unittest.TestCase):
    """Case 1·2 — "오는 18일" 은 읽고, "지난 18일" 은 읽지 않는다."""

    def test_an_upcoming_bare_day_resolves_against_the_publication_date(self):
        """계기가 된 사례. 본문은 월을 안 쓰고 독자는 보도일로 그것을 안다."""
        self.assertEqual(
            days("산업통상자원부는 오는 18일 제7차 정책토론회를 개최한다.", date(2026, 9, 9)),
            {"2026-09-18": "inferred"})

    def test_a_past_bare_day_never_becomes_a_future_event(self):
        self.assertEqual(
            days("지난 18일 제7차 정책토론회를 개최했다.", date(2026, 9, 20)), {})

    def test_a_bare_day_rolls_into_next_month_only_when_it_must(self):
        """발행일보다 이른 날은 다음 달이다 — 큐가 그 달을 못박지 않았을 때만."""
        self.assertEqual(days("내달 19일 그란폰도 대회를 개최한다", date(2026, 8, 19)),
                         {"2026-09-19": "inferred"})

    def test_a_month_cue_binds_to_its_own_token(self):
        """Case 13 — 한 절의 두 날짜가 서로 다른 달을 가리킨다.

        실측: "이달 10일부터 다음 달 11일까지". 절 단위로 판정하면 둘이 같은
        달로 접히고 공모 기간이 하루짜리가 된다.
        """
        self.assertEqual(
            days("이번 공모는 이달 10일부터 다음 달 11일까지 진행되며", date(2026, 8, 10)),
            {"2026-08-10": "inferred", "2026-09-11": "inferred"})

    def test_one_clause_can_hold_a_future_date_and_a_reporting_date(self):
        """Case 11 — 절 단위로 미래·과거를 가르면 반드시 한쪽을 틀린다.

        실측 2026-08-09: 12일은 일식이 있는 날이고 8일은 FT 가 보도한 날이다.
        """
        self.assertEqual(
            days("유럽을 덮친 폭염으로 12일 늦은 오후 예정된 일식으로 태양광 발전량이 "
                 "감소하면 부담이 커질 것으로 파이낸셜타임스(FT)가 8일(현지시간) "
                 "보도했습니다", date(2026, 8, 9)),
            {"2026-08-12": "inferred"})

    def test_a_day_just_before_publication_is_the_reporting_day(self):
        """Case 12 — 이 가드가 없으면 어제 일이 한 달 뒤 유령 일정이 된다.

        실측 2026-08-21: "20일 국회에서 간담회를 열고 … 논의했다". 20 은 21 보다
        작아 이번 달로는 과거이므로, 가드가 없으면 9월 20일로 밀린다.
        """
        self.assertEqual(
            days("20일 국회에서 간담회를 열고 공동 대응 방안을 논의했다", date(2026, 8, 21)),
            {})

    def test_last_month_is_not_next_month(self):
        """실측 2026-09-01: "지난달 14일 지원서 접수를 마감하고" 가 9월 14일로 섰다.

        `지난` 뒤에 `달` 이 붙으면 한 글자 뒤보기로는 과거 큐가 안 보인다.
        """
        self.assertEqual(
            days("한전 임추위는 지난달 14일 지원서 접수를 마감하고 심사를 완료했다",
                 date(2026, 9, 1)), {})

    def test_a_length_of_days_is_not_a_day_of_the_month(self):
        """실측 2026-08-28: "8월 31일부터 17일간 개최한다" 의 17일은 열이레다."""
        self.assertEqual(
            days("울산시의회는 정례회를 8월 31일부터 17일간 개최한다", date(2026, 8, 28)),
            {"2026-08-31": "explicit"})

    def test_the_same_number_is_not_read_twice(self):
        """범위의 꼬리로 읽은 숫자를 월 없는 날로 다시 읽지 않는다.

        실측: "10월 25일부터 29일까지 제주 롯데호텔에서 개최된다" 의 29일이
        꼬리로는 10월 29일, 월 없는 날로는 9월 29일이 되어 달력이 이른 쪽에
        칸을 세웠다.
        """
        self.assertEqual(
            days("ICRS15가 10월 25일부터 29일까지 제주 롯데호텔에서 개최된다",
                 date(2026, 9, 7)),
            {"2026-10-25": "explicit", "2026-10-29": "syntactic"})

    def test_a_qualified_date_is_not_read_twice_as_a_bare_day(self):
        """"12월 3일" 의 '3일' 은 월 없는 날이 아니다 — 한 글자 뒤보기로는 안 보인다."""
        self.assertEqual(days("한수원이 12월 3일 착공식을 개최할 예정이다", date(2026, 8, 20)),
                         {"2026-12-03": "explicit"})


class TestARangeKeepsItsTail(unittest.TestCase):
    """Case 3·4 — WSCE 2026. 꼬리를 잃으면 행사가 하루로 접히고 곧 사라진다."""

    WSCE = article(
        title_kr="WSCE 2026, 부산서 세계원전시장 인사이트 콘퍼런스 개최",
        detail="9월 9일부터 11일까지 부산 벡스코에서 원전 수출 전략을 다루는 "
               "WSCE 2026 콘퍼런스가 열린다.",
        article_date="2026-09-08")

    def test_the_tail_inherits_the_month_of_the_head(self):
        self.assertEqual(days("9월 9일부터 11일까지 WSCE 2026이 열린다", date(2026, 9, 8)),
                         {"2026-09-09": "explicit", "2026-09-11": "syntactic"})

    def test_the_tilde_spelling_reads_the_same(self):
        for text in ("9월 9~11일 부산에서 열린다", "2026.9.9~9.11 WSCE 개최"):
            with self.subTest(text=text):
                self.assertEqual(days(text, date(2026, 9, 1)),
                                 {"2026-09-09": "explicit", "2026-09-11": "syntactic"})

    def test_a_decimal_is_not_a_date(self):
        """범위 표기를 열면서 소수점까지 날짜가 되면 안 된다."""
        self.assertEqual(days("전력수요는 9.5GW 늘고 설비는 3.2GW 증가한다",
                              date(2026, 9, 1)), {})

    def test_a_conference_already_under_way_stays_on_the_calendar(self):
        """Case 4 — 오늘이 9월 10일이다. 시작일만 보고 버리면 통째로 사라진다."""
        row = event_calendar.build([self.WSCE], TODAY)["events"][0]
        self.assertEqual((row["date"], row["end_date"]), ("2026-09-09", "2026-09-11"))
        self.assertEqual(row["kind"], "range")

    def test_a_bare_head_before_a_qualified_tail_is_left_alone(self):
        """"11일부터 9월 21일까지" 의 머리는 앞으로 밀지 않는다.

        머리의 월은 꼬리에서 거꾸로 읽어야 하는데 그러면 대개 발행일 이전이다.
        없는 시작일을 지어 만들면 격자를 통째로 물들이는 가짜 기간이 선다.
        """
        self.assertEqual(
            days("해양수산부는 11일부터 9월 21일까지 입법예고하며", date(2026, 8, 20)),
            {"2026-09-21": "explicit"})


class TestNoDateIsStillNoDate(unittest.TestCase):
    """Case 5·6 — 날짜가 없는 미래 신호를 확정 일정으로 만들지 않는다."""

    def test_a_month_only_promise_never_takes_a_day_box(self):
        self.assertEqual(days("9월 중 후속 토론회를 개최할 예정이다", date(2026, 9, 9)), {})

    def test_a_relative_phrase_never_becomes_a_day(self):
        for text in ("다음 달 공청회를 개최할 계획이다", "다음 주 설명회를 연다",
                     "상반기 중 착공할 예정이다", "연내 시행한다"):
            with self.subTest(text=text):
                self.assertEqual(days(text, date(2026, 9, 9)), {})


class TestInferenceCarriesItsProvenance(unittest.TestCase):
    """읽은 방법을 잃지 않는다 — 추론분이 늘면 회차 사이 비교로 보여야 한다."""

    def test_every_event_says_how_its_date_was_read(self):
        payload = event_calendar.build([article(
            title_kr="산업부, 제12차 전기본 제7차 정책토론회 개최",
            detail="산업통상자원부는 오는 18일 전력수급기본계획 제7차 정책토론회를 "
                   "개최한다.")], TODAY)
        self.assertEqual(payload["events"][0]["date_basis"], "inferred")
        self.assertEqual(payload["basis"], {"inferred": 1})

    def test_an_inferred_date_needs_a_real_event_noun_beside_it(self):
        """추론은 미래 표지만으로 서지 못한다 — 그 절이 사건을 말해야 한다."""
        payload = event_calendar.build([article(
            title_kr="원전 가동률 전망",
            detail="원전 이용률은 오는 18일 기준으로 90%를 넘어설 전망이다.")], TODAY)
        self.assertEqual(payload["events"], [])


class TestSilentMissesAreCounted(unittest.TestCase):
    """관측성 — 버린 것과 못 본 것은 다르다.

    두 사례를 재현했을 때 `dropped` 가 통째로 비어 있었다. 조용히 사라지는
    실패는 고칠 수 없다.
    """

    def test_a_date_shaped_clause_the_parser_cannot_read_is_counted(self):
        payload = event_calendar.build([article(
            title_kr="원자력계 행사 안내",
            detail="원자력 세미나는 9월 셋째 주에 개최할 예정이다.")], TODAY)
        self.assertEqual(payload["events"], [])
        self.assertGreaterEqual(payload["missed"].get("no_readable_date", 0), 1)


class TestOnlyOurSubjectReachesTheGrid(unittest.TestCase):
    """기사 경로에도 주제 판정을 둔다 — 발견을 넓힌 만큼 표시를 좁힌다.

    실측 2026-09-10: 이 자리가 비어 있어 달력 기사 경로 48건 중 47건이 주제
    판정을 한 번도 받지 않았다.
    """

    def test_the_noise_that_used_to_reach_the_grid_no_longer_does(self):
        for title, label in (
                ("반도체 소재 기업 큐니티(Qnity), 10월 1일 신임 CFO 선임", "큐니티 선임"),
                ("CME, 10월 5일 엔비디아 GPU 기반 선물계약 2종 출시 예정", "CME 출시"),
                ("전국 자전거 동호인, 영동서 친환경 레이스 페달 밟는다", "자전거 개최"),
                ("한울원자력본부, 지역주민 대상 영화 상영 행사 개최", "한울원자력본부 무비데이"),
                ("현대건설, 하반기 신입 공채…원전·SMR 인력 확충", "현대건설 접수 마감")):
            with self.subTest(title=title):
                self.assertFalse(event_relevance.judge_reported(title, label)["ok"])

    def test_a_policy_milestone_is_an_event_even_without_a_meeting_word(self):
        """`judge` 를 그대로 쓰면 이것들이 전부 not_an_event 로 떨어졌다."""
        for title, label in (
                ("한빛 2호기, 9월 11일 설계수명 만료로 가동 정지", "한빛 2호기 설계수명 만료"),
                ("SMR 상용화 일정 가속화, 9월 특별법 시행에 기대감 고조", "SMR 특별법 시행"),
                ("919 기후정의행진 조직위, 신규 원전 건설 중단 요구", "기후정의행진 집회")):
            with self.subTest(title=title):
                self.assertTrue(event_relevance.judge_reported(title, label)["ok"])

    def test_the_official_path_keeps_its_own_stricter_forms(self):
        """기사 경로에 푼 '공모'·'모집' 이 공식 경로로 새면 안 된다.

        기관 게시판의 공모·모집은 대부분 조달·채용이다.
        """
        self.assertFalse(event_relevance.judge(
            "원자력 분야 신입사원 입문 과정 교육생 모집")["ok"])
        self.assertNotIn("공모", event_relevance.PROCESS_FORMS)


class TestOneEventIsOneRow(unittest.TestCase):
    """Case 7·8·9·14 — 같은 일을 두 줄로 세우지 않는다."""

    def _pair(self, first: dict, second: dict) -> list[dict]:
        return event_calendar.build([first, second], TODAY)["events"]

    def test_one_article_saying_it_twice_is_one_event(self):
        """Case 14 — 실측 55건 중 7쌍이 이 자리에서 갈라졌다.

        같은 기사가 제목에서 '개최' 로, 본문에서 '박람회' 로 불러 이름 명사가
        어긋났다.
        """
        rows = event_calendar.build([article(
            title_kr="전북 새만금 청정에너지 박람회 개최 및 재생에너지 정책 논의",
            detail="전북 새만금 청정에너지 박람회가 9월 17일 개최된다.")], TODAY)["events"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_count"], 1)

    def test_a_postponed_event_moves_instead_of_multiplying(self):
        """Case 7 — 9/18 이 9/25 로 밀리면 한 줄이 옮겨 앉는다."""
        rows = self._pair(
            article(hash="a", story_id="s1",
                    title_kr="한수원, 사용후핵연료 토론회 개최",
                    detail="한수원이 9월 18일 사용후핵연료 토론회를 개최한다."),
            article(hash="b", story_id="s1",
                    title_kr="한수원, 사용후핵연료 토론회 연기",
                    detail="9월 18일 열릴 예정이던 사용후핵연료 토론회가 "
                           "9월 25일로 연기됐다."))
        self.assertEqual([row["date"] for row in rows], ["2026-09-25"])

    def test_a_cancelled_event_leaves_the_grid(self):
        """Case 8 — 취소 기사와 예고 기사는 같은 일정의 두 근거다."""
        rows = self._pair(
            article(hash="a", story_id="s2",
                    title_kr="한수원, 사용후핵연료 토론회 개최",
                    detail="한수원이 9월 25일 사용후핵연료 토론회를 개최한다."),
            article(hash="b", story_id="s2", article_date="2026-09-09",
                    title_kr="한수원, 사용후핵연료 토론회 취소",
                    detail="9월 25일 예정됐던 사용후핵연료 토론회가 취소됐다."))
        self.assertEqual(rows, [])

    def test_a_demand_to_withdraw_something_is_not_a_cancelled_event(self):
        """실측 2026-08-19: 집회의 **요구사항**에 '철회'가 있어 집회가 사라졌다.

        낱말만 찾으면 취소의 대상이 그 행사가 아닌 데도 걸린다. 행사의 취소는
        언제나 그 행사를 주어로 한 서술어로 온다.
        """
        rows = event_calendar.build([article(
            title_kr="919 기후정의행진 조직위, 신규 원전 건설 중단 등 요구",
            detail="919 기후정의행진 조직위가 9월 19일 대규모 집회를 예고하며 "
                   "신규 원전 건설 중단과 노후 원전 수명연장 철회를 촉구했다.",
            article_date="2026-08-19")], TODAY)["events"]
        self.assertEqual([row["date"] for row in rows], ["2026-09-19"])

    def test_the_firmest_evidence_names_the_event(self):
        """추론으로 읽은 줄이 원문에 적힌 줄의 이름을 덮지 않는다.

        실측 2026-09-11: SMR 특별법 시행일을 'SMR 특별법 시행'(원문에 "9월 11일")
        으로 부르던 것이, 건의안 기사에서 "11일 시행되는" 을 추론한 줄에 덮여
        '경남도의회 시행' 이 됐다. 추론은 대개 그 일정을 지나가며 언급한 기사에서
        나온다.
        """
        rows = event_calendar.build([
            article(hash="a", title_kr="경남도의회, SMR 진흥특구 지정 건의안 발의",
                    detail="경남도의회는 11일 시행되는 'SMR 개발 촉진 및 지원에 관한 "
                           "특별법'에 따른 기본계획 반영을 요구했다."),
            article(hash="b", title_kr="SMR 특별법, 9월 11일 시행",
                    detail="SMR 개발 촉진 및 지원에 관한 특별법이 9월 11일 시행된다."),
        ], TODAY)["events"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date_basis"], "explicit")

    def test_two_names_for_one_event_fold_when_the_titles_agree(self):
        """Case 9 — 이름이 달라도 제목이 같은 것을 말하면 한 줄이다."""
        rows = self._pair(
            article(hash="a",
                    title_kr="제12차 전기본 제7차 정책토론회, 9월 18일 개최",
                    detail="제12차 전력수급기본계획 제7차 정책토론회가 9월 18일 열린다."),
            article(hash="b",
                    title_kr="2040 석탄발전 조기폐지 정책토론회, 9월 18일 개최",
                    detail="제12차 전력수급기본계획 제7차 정책토론회가 9월 18일 "
                           "국회에서 열린다."))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_count"], 2)


class TestTheCurationMayShortenTheDate(unittest.TestCase):
    """Case 10 — 큐레이션이 "9월 18일" 을 "18일" 로 줄여도 일정은 남는다.

    원문 본문은 아카이브에 없다(`verified_evidence` 는 해시뿐이다). 그래서
    되살릴 길은 원문을 다시 읽는 것이 아니라 **줄어든 표기를 읽을 줄 아는 것**
    이다. 선언 경로도 같은 추출기로 다시 잰다.
    """

    def test_a_shortened_date_still_stands(self):
        row = event_calendar.build([article(
            title_kr="산업부, 제12차 전기본 제7차 정책토론회 개최",
            summary="산업통상자원부가 오는 18일 제7차 정책토론회를 연다.",
            detail="산업통상자원부는 오는 18일 전력수급기본계획 제7차 정책토론회를 "
                   "개최한다.")], TODAY)["events"][0]
        self.assertEqual(row["date"], "2026-09-18")
        self.assertIn("정책토론회", row["label"])

    def test_a_declared_date_the_gate_cannot_spell_out_is_still_checked(self):
        """선언된 날짜가 줄어든 표기로만 남아 있어도 확인은 한다."""
        self.assertEqual(
            article_quality_gate.date_evidence_problem(
                "2026-09-18", "day", "오는 18일 토론회를 개최한다", "2026-09-09"),
            "source_unsubstantiated")
        self.assertIn(date(2026, 9, 18),
                      article_quality_gate.scheduled_dates(
                          "오는 18일 토론회를 개최한다", "2026-09-09"))


class TestTheLedgerRemembersWhatTheWindowForgets(unittest.TestCase):
    """기사 창(60일)보다 먼 앞날을 기억한다."""

    FAR = article(
        title_kr="제15회 방사선 차폐 국제회의, 제주서 개최",
        detail="2026년 10월 25일부터 29일까지 제주도에서 원자력 방사선 차폐 "
               "국제회의가 개최될 예정입니다.",
        article_date="2026-07-27")

    def test_an_event_beyond_the_calendar_window_is_written_down(self):
        rows = event_ledger.candidates([self.FAR], date(2026, 7, 27))
        self.assertEqual([(row["date"], row["end_date"]) for row in rows],
                         [("2026-10-25", "2026-10-29")])

    def test_what_is_already_on_screen_is_not_written_down_twice(self):
        """달력 창 안의 일정은 기사에서 바로 유도한다 — 원장이 그 경로를 대체하지 않는다."""
        near = article(title_kr="한수원, 원자력 토론회 개최",
                       detail="한수원이 9월 18일 원자력 정책토론회를 개최한다.")
        self.assertEqual(event_ledger.candidates([near], TODAY), [])

    def test_only_dates_read_straight_from_the_source_are_remembered(self):
        """몇 달을 들고 있을 값이라 추론분은 담지 않는다."""
        inferred = article(
            title_kr="한수원, 원자력 토론회 개최",
            detail="한수원이 오는 18일 원자력 정책토론회를 개최한다.",
            article_date="2026-09-09")
        self.assertEqual(event_ledger.candidates([inferred], TODAY), [])

    def test_a_remembered_event_is_re_checked_before_it_stands(self):
        """저장본을 믿지 않는다 — 근거 문장을 다시 읽어 날짜를 확인한다."""
        store = {"events": event_ledger.candidates([self.FAR], date(2026, 7, 27))}
        rows = event_ledger.calendar_rows(store, date(2026, 10, 20))
        payload = event_calendar.build([], date(2026, 10, 20), remembered=rows)
        self.assertEqual([(row["date"], row["end_date"]) for row in payload["events"]],
                         [("2026-10-25", "2026-10-29")])

        tampered = [{**rows[0], "date": "2026-10-26"}]
        blocked = event_calendar.build([], date(2026, 10, 20), remembered=tampered)
        self.assertEqual(blocked["events"], [])
        self.assertEqual(blocked["dropped"],
                         {"remembered_date_not_in_clause": 1})

    def test_the_story_that_moved_replaces_its_own_row(self):
        """원장에서도 연기는 새 줄이 아니라 같은 줄의 이동이다."""
        first = {"id": "a", "date": "2026-11-01", "end_date": "2026-11-01",
                 "kind": "point", "label": "토론회", "clause": "",
                 "story_id": "s9", "first_seen": "2026-08-01"}
        moved = {**first, "id": "b", "date": "2026-11-08",
                 "end_date": "2026-11-08", "first_seen": "2026-09-01"}
        kept, added = event_ledger.merge([first], [moved])
        self.assertEqual(added, 0)
        self.assertEqual([(row["date"], row["first_seen"]) for row in kept],
                         [("2026-11-08", "2026-08-01")])


if __name__ == "__main__":
    unittest.main()
