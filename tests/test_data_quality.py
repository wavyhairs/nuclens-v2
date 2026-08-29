"""Nuclens 공통 데이터 품질 계약 회귀 테스트. 외부 호출 0."""

import unittest

import data_quality

from data_quality import (
    DETAIL_LIMIT,
    curation_errors,
    invalid_url_reason,
    sanitize_detail,
    is_complete_sentence,
    legacy_url_hash,
    normalize_event_date_fields,
    normalize_url,
    source_profile,
    split_title_publisher,
    title_key,
    url_hash,
)


class UrlQualityTests(unittest.TestCase):
    def test_double_slash_and_tracking_params_are_normalized(self):
        left = "https://www.example.com//articles/story/?utm_source=x&id=7#top"
        right = "https://www.example.com/articles/story?id=7"
        self.assertEqual(normalize_url(left), right)
        self.assertEqual(url_hash(left), url_hash(right))

    def test_article_identity_query_is_preserved(self):
        self.assertNotEqual(
            normalize_url("https://example.com/read?id=7"),
            normalize_url("https://example.com/read?id=8"),
        )

    def test_legacy_hash_remains_available_for_state_transition(self):
        raw = "https://example.com/story?utm_source=old"
        self.assertNotEqual(legacy_url_hash(raw), url_hash(raw))

    def test_error_path_is_rejected_case_insensitively(self):
        self.assertEqual(invalid_url_reason("https://example.com/Error/please-try-again"), "error_path")
        self.assertEqual(invalid_url_reason("javascript:alert(1)"), "invalid_url")
        self.assertEqual(invalid_url_reason("https://example.com/news/error-budget"), "")

    def test_exact_title_key_only_normalizes_spacing_and_case(self):
        self.assertEqual(title_key("  NRC   Approves Licence  "), title_key("nrc approves licence"))
        self.assertNotEqual(title_key("NRC approves licence"), title_key("NRC licence approved today"))


class PublisherTests(unittest.TestCase):
    def test_explicit_rss_publisher_wins_and_repeated_suffix_is_removed(self):
        title, publisher = split_title_publisher(
            "원전 계속운전 심사 - 전기신문 - 전기신문", "전기신문"
        )
        self.assertEqual(title, "원전 계속운전 심사")
        self.assertEqual(publisher, "전기신문")

    def test_title_suffix_is_fallback_when_source_element_is_missing(self):
        self.assertEqual(
            split_title_publisher("원안위가 심사 결과를 발표했다 - KBS 뉴스"),
            ("원안위가 심사 결과를 발표했다", "KBS 뉴스"),
        )


class SourceModelTests(unittest.TestCase):
    def test_official_and_specialist_sources_are_not_conflated(self):
        official = source_profile("energy.gov", "US DOE")
        specialist = source_profile("world-nuclear-news.org", "World Nuclear News")
        self.assertEqual((official["source_type"], official["evidence_role"]), ("official", "primary"))
        self.assertEqual(
            (specialist["source_type"], specialist["evidence_role"]),
            ("specialist_media", "independent"),
        )

    def test_press_release_distribution_is_explicit(self):
        profile = source_profile("globenewswire.com", "GlobeNewswire")
        self.assertEqual(profile["source_type"], "press_release")
        self.assertEqual(profile["evidence_role"], "distributed_claim")

    def test_unknown_domain_still_has_non_null_rank_tier(self):
        profile = source_profile("regional-news.example", "지역매체")
        self.assertEqual(profile["source_tier"], 3)
        self.assertEqual(profile["publisher"], "지역매체")


class TextAndEventDateTests(unittest.TestCase):
    def test_complete_sentence_gate(self):
        self.assertTrue(is_complete_sentence("원안위가 운영 변경을 승인했다."))
        self.assertTrue(is_complete_sentence("상업운전은 2027년 시작될 예정이다"))
        self.assertFalse(is_complete_sentence("운영 변경 승인"))
        self.assertFalse(is_complete_sentence("규제 심사가 강화될 것으로 예상되"))

    def test_curation_limits_do_not_allow_mid_sentence_slicing(self):
        self.assertEqual(curation_errors({"summary": "정부가 계획을 발표했다."}), [])
        # 한도는 80 → 100 (2026-08-07). 원문 본문을 프롬프트에 넣자 요약에 실리는
        # 사실이 늘어 길이가 함께 올라갔고, 80 을 그대로 두면 **내용이 좋아진
        # 기사부터** 격리된다. 이 게이트가 막는 것은 길이가 아니라 잘림이다.
        self.assertIn(
            "summary:incomplete_or_over_100",
            curation_errors({"summary": "정부가 계획을 발표"}),
        )
        self.assertEqual(curation_errors({"summary": "가" * 97 + "다."}), [])
        self.assertIn(
            "summary:incomplete_or_over_100",
            curation_errors({"summary": "가" * 120 + "다."}),
        )
        # 한도는 60 → 90 (2026-08-05). 원인·다음 절차·수치를 담으라고 프롬프트를
        # 바꿨는데 60자로는 그게 안 들어간다. 잘림 검사 자체는 그대로다.
        self.assertIn(
            "implication:incomplete_or_over_90",
            curation_errors({"summary": "정부가 계획을 발표했다.", "implication": "시장 영향 확대 가능"}),
        )

    def test_event_date_requires_explicit_iso_date_and_metadata(self):
        valid = normalize_event_date_fields({
            "event_date": "2026-08-01",
            "event_date_type": "announcement",
            "event_date_precision": "day",
            "event_date_source": "description",
        })
        self.assertEqual(valid["event_date"], "2026-08-01")
        invalid = normalize_event_date_fields({"event_date": "2026년 8월"})
        self.assertEqual(invalid, {
            "event_date": None,
            "event_date_type": "unknown",
            "event_date_precision": "unknown",
            "event_date_source": "unknown",
        })


class FeaturesGateTests(unittest.TestCase):
    """features 결손을 '게시 자격'과 '큐레이션 완결성'으로 분리해 다룬다.

    분리가 없던 동안 결손 항목이 재큐레이션 대상에서 영구히 빠졌다.
    근거: docs/score_distribution.md §4.
    """

    BASE = {
        "summary": "한빛 1·2호기 계속운전 심사가 재개됐다.",
        "importance": "must_read",
        "section": "khnp",
        "category": "정책",
    }

    def test_publishing_gate_ignores_features_by_default(self):
        # 아카이브 적재·배포 게이트는 features 가 없어도 통과해야 한다.
        # 제목·요약·링크는 멀쩡하므로 내보낼 수 있고, 여기서 막으면
        # 트렌드·prior_coverage 재료가 통째로 사라진다.
        self.assertEqual(curation_errors(dict(self.BASE)), [])
        self.assertEqual(curation_errors({**self.BASE, "features": None}), [])

    def test_curation_completeness_flags_missing_features(self):
        self.assertEqual(
            curation_errors(dict(self.BASE), require_features=True),
            ["features:missing"],
        )

    def test_non_dict_features_are_treated_as_missing(self):
        for bad in (None, [], "", 0, "policy_decision"):
            with self.subTest(features=bad):
                self.assertIn(
                    "features:missing",
                    curation_errors({**self.BASE, "features": bad},
                                    require_features=True),
                )

    def test_present_features_pass(self):
        payload = {**self.BASE, "features": {"event_type": "policy_decision",
                                             "korea_relevance": 3}}
        self.assertEqual(curation_errors(payload, require_features=True), [])

    def test_features_error_does_not_mask_other_errors(self):
        payload = {"summary": "", "features": None}
        errors = curation_errors(payload, require_features=True)
        self.assertIn("summary:missing", errors)
        self.assertIn("features:missing", errors)


class TestHollowImplication(unittest.TestCase):
    """정보량 0인 AI 해석을 걸러낸다.

    사용자 지적(2026-08-05): "AI 헝가리 정부의 원전 운영에 대한 긍정적 입장을
    시사한다 >> 이거 보면 내용이 너무 없어." 카드 두 번째 줄이 제목을 바꿔 말하기만
    하면 읽는 사람이 얻는 게 없다.

    실측 라이브 issues.json: implication 이 있는 64건 중 40건(62%)이 상투적
    종결부로 끝났다. **재생성 사유로 쓰지 않는다** — curation_errors 에 넣으면
    재생성 1회 뒤에도 남을 때 기사가 격리돼 영문 제목 폴백으로 떨어진다.
    """

    def test_the_reported_sentence_is_caught(self):
        self.assertTrue(data_quality.implication_is_hollow(
            "헝가리 정부의 원자력 발전 운영에 대한 긍정적 입장을 시사한다."))

    def test_common_hollow_shapes(self):
        for text in (
            "미국 SMR 상용화 및 기술 실증 가속화에 기여할 것입니다.",
            "선진원자로 기술의 상업화 및 운영 표준화에 중요한 이정표가 될 것으로 예상됩니다.",
            "정부의 탈원전 정책 전환 기조가 언론에서도 주요하게 다뤄지고 있음을 보여줍니다.",
            "향후 원자력 안전 관련 법규 및 행정 절차 변경 가능성을 주시할 필요가 있습니다.",
        ):
            self.assertTrue(data_quality.implication_is_hollow(text), text)

    def test_concrete_causes_survive(self):
        """원인·다음 절차가 들어 있으면 살린다 — 이것이 우리가 원하는 문장이다."""
        for text in (
            "다뉴브강 수위가 회복되며 냉각수 취수 제한이 풀린 결과로, 앞서 예고된 전면 정지는 피했다.",
            "미국 내 신규 원전 건설 가능성을 열어두는 전략적 움직임입니다.",
            "EIB의 대출 승인은 루마니아 원전 개보수 사업의 재정적 안정성을 확보한다.",
        ):
            self.assertFalse(data_quality.implication_is_hollow(text), text)

    def test_quantities_and_dates_rescue_a_cliche_ending(self):
        """'언제·얼마'가 실려 있으면 어미가 상투적이어도 정보는 있다."""
        self.assertFalse(data_quality.implication_is_hollow(
            "2028년 착공을 목표로 인허가 절차가 이어질 전망이다."))
        self.assertFalse(data_quality.implication_is_hollow(
            "설비용량 1,400MW 증설분이 계통에 들어올 것으로 예상됩니다."))

    def test_model_numbers_do_not_rescue(self):
        """'AP1000'·'3·4호기'의 숫자를 수량으로 세면 게이트가 사문화된다(실측 오탐)."""
        self.assertTrue(data_quality.implication_is_hollow(
            "주요 원전 공급사 간 협력으로 AP1000 및 AP300 SMR의 시장 확산 가속화를 시사합니다."))

    def test_empty_is_not_hollow(self):
        """빈 값은 '빈껍데기'가 아니다 — 폐기 집계를 부풀리면 안 된다."""
        self.assertFalse(data_quality.implication_is_hollow(""))
        self.assertFalse(data_quality.implication_is_hollow(None))

    def test_hollow_is_not_a_regeneration_error(self):
        """curation_errors 에 들어가면 문체 위반으로 기사가 격리된다."""
        payload = {"summary": "헝가리 총리가 팍스 원전 가동 상황을 발표했다.",
                   "implication": "헝가리 정부의 긍정적 입장을 시사한다."}
        self.assertEqual(data_quality.curation_errors(payload), [])

    def test_implication_limit_allows_a_cause_clause(self):
        """60자로는 원인·다음 절차를 못 담는다 — 한도를 90자로 올렸다."""
        self.assertEqual(data_quality.IMPLICATION_LIMIT, 90)
        payload = {"summary": "요약 문장이다.",
                   "implication": "다뉴브강 수위가 회복되며 냉각수 취수 제한이 풀린 결과로, "
                                  "앞서 예고된 전면 정지는 피했다."}
        self.assertEqual(data_quality.curation_errors(payload), [])


if __name__ == "__main__":
    unittest.main()


class TestPortalRelaysAreNotIndependent(unittest.TestCase):
    """포털 중계는 독립 출처가 아니다.

    원 매체 기사를 그대로 실어 나르므로, 원문과 포털 사본이 같은 이슈 클러스터에
    들어가면 build_data._is_independent_source 가 둘 다 세어 '복수 출처 확인'
    배지를 위조한다. 실측 2026-08-06: 아카이브 970건 중 23건이 포털 중계였고
    **전부 independent 로 잡혀 있었다**(네이트 12 · v.daum.net 6 · MSN 5).

    대부분 Google News 피드의 <source> 가 포털로 찍혀 들어오므로 domain 은
    news.google.co.kr 이다 — 별칭(publisher) 매칭이 살아 있어야 잡힌다.
    """

    PORTALS = ("네이트", "v.daum.net", "MSN", "다음", "네이버")

    def test_portal_publisher_is_a_relay(self):
        for publisher in self.PORTALS:
            with self.subTest(publisher=publisher):
                profile = source_profile("news.google.co.kr", publisher)
                self.assertEqual("distributed_claim", profile["evidence_role"])

    def test_portal_domain_is_a_relay(self):
        for domain in ("daum.net", "v.daum.net", "n.news.naver.com",
                       "news.nate.com", "msn.com"):
            with self.subTest(domain=domain):
                self.assertEqual("distributed_claim",
                                 source_profile(domain, "")["evidence_role"])

    def test_real_outlets_stay_independent(self):
        # 포털을 거른다고 진짜 매체까지 걸리면 검증이 통째로 죽는다.
        for domain, publisher in (("yna.co.kr", "연합뉴스"),
                                  ("news.google.co.kr", "전기신문"),
                                  ("chosun.com", "조선일보"),
                                  ("kbs.co.kr", "KBS 뉴스")):
            with self.subTest(publisher=publisher):
                self.assertEqual("independent",
                                 source_profile(domain, publisher)["evidence_role"])

    def test_official_sources_are_unaffected(self):
        self.assertEqual("primary", source_profile("nssc.go.kr", "")["evidence_role"])

    def test_ranking_tier_is_unchanged(self):
        # 랭킹 점수는 건드리지 않는다 — 등록 전에도 tier3 기본값이었다.
        self.assertEqual(3, source_profile("news.google.co.kr", "네이트")["source_tier"])


class DetailSanitizerTests(unittest.TestCase):
    """기사 요지(detail) — 원문 대신 읽는 문단.

    사용자 요구(2026-08-07): "실제 기사들이 영문으로 되어있는 경우가 많아서 실제를
    들어가서 보기 어려운 경우가 많거든." summary(카드 한 줄)로는 그 요구를 못 받는다.
    """

    def test_complete_sentences_are_kept(self):
        text = ("헝가리 팍스 원전 4기 중 3기가 8월 6일 가동을 멈췄다. "
                "다뉴브강 수위가 취수 기준선 아래로 내려가 냉각수 확보가 불가능해졌다. "
                "나머지 1기도 출력을 절반으로 낮춰 운전 중이다.")
        self.assertEqual(sanitize_detail(text), text)

    def test_trailing_incomplete_sentence_is_dropped_not_kept(self):
        """잘린 마지막 절을 남기면 모델이 아니라 우리가 만든 오정보가 된다."""
        good = ("헝가리 팍스 원전 4기 중 3기가 8월 6일 가동을 멈췄다. "
                "다뉴브강 수위가 취수 기준선 아래로 내려가 냉각수 확보가 불가능해졌다. "
                "나머지 1기도 출력을 절반으로 낮춰 운전 중이다.")
        self.assertEqual(sanitize_detail(good + " 헝가리 정부는 전력 수급 대"), good)

    def test_short_output_is_treated_as_absent(self):
        # 본문을 못 받아온 기사에서 모델이 제목을 늘려 쓴 한 줄. 요지가 아니다.
        self.assertEqual(sanitize_detail("가동이 중단됐다."), "")
        self.assertEqual(sanitize_detail(""), "")
        self.assertEqual(sanitize_detail(None), "")

    def test_length_is_capped_at_a_sentence_boundary(self):
        sentence = "다뉴브강 수위가 취수 기준선 아래로 내려가 냉각수 확보가 불가능해졌다. "
        capped = sanitize_detail(sentence * 20)
        self.assertLessEqual(len(capped), DETAIL_LIMIT)
        self.assertTrue(capped.endswith("다."), capped[-20:])


class EmailRedactionTests(unittest.TestCase):
    """기사에서 딸려온 이메일 주소는 저장 전에 지운다.

    한국 기사는 바이라인에 기자 메일을 그대로 적는다. 그 줄이 RSS description 을
    타고 `source_excerpt` 에 저장돼 있었다(실측 2026-08-29, digest_queue.json 1건).
    우리 자격증명이 아니라 **제3자의 개인정보**라, 저장소를 공개로 돌리면 신문
    지면에 한 번 실린 것이 아카이브에 영구히 색인되는 것으로 성격이 바뀐다.

    여기서 잠그는 것은 둘이다 — 지우는가, 그리고 **그것 말고는 안 건드리는가**.
    """

    def test_a_byline_address_is_removed(self):
        self.assertEqual(
            data_quality.strip_emails(
                "정아람 기자 areum@example.co.kr [한국정경신문=정아람 기자]"),
            "정아람 기자 [한국정경신문=정아람 기자]")

    def test_the_wrapper_left_behind_is_cleaned_up(self):
        """"(<메일>)" 이 "()" 로 남으면 그것대로 원문에 없던 모양이다."""
        self.assertEqual(data_quality.strip_emails("문의 (hong@example.net)"), "문의")
        self.assertEqual(data_quality.strip_emails("메일 <hong@example.co.kr>"), "메일")

    def test_mailto_prefix_goes_with_it(self):
        self.assertEqual(
            data_quality.strip_emails("mailto:someone@example.com 참고"), "참고")

    def test_text_without_an_address_is_returned_untouched(self):
        """뜻을 바꾸지 않는 것이 조건이다 — 큐레이션·랭킹이 이 텍스트를 읽는다."""
        for text in ("이메일 없는 평범한 문장입니다.",
                     "한수원, 신한울 3·4호기 착공 — 2026년 목표",
                     "SMR 100@사업",          # @ 가 주소가 아닌 경우
                     "가격은 1,200원@2026년 기준"):
            self.assertEqual(data_quality.strip_emails(text), text)

    def test_non_strings_do_not_explode(self):
        for value in (None, 123, [], {}):
            self.assertEqual(data_quality.strip_emails(value), "")

    def test_it_does_not_touch_urls_through_clean_text(self):
        """`clean_text` 는 180곳에서 쓰이고 `normalize_url` 도 그중 하나다.

        이메일 규칙이 거기 섞이면 URL·식별자까지 조용히 바뀐다. 지우는 일은
        부르는 쪽이 명시적으로 하고, `clean_text` 는 예전 그대로여야 한다.
        """
        url = "https://example.com/news?author=kim@example.com"
        self.assertIn("kim@example.com", data_quality.clean_text(url))
        # normalize_url 은 쿼리를 퍼센트 인코딩한다(원래 동작). 여기서 잠그는 것은
        # 그 값이 **지워지지 않는다**는 것이지 표기가 그대로라는 것이 아니다.
        self.assertIn("kim%40example.com", data_quality.normalize_url(url))

    def test_ranking_and_dedup_keys_are_unaffected(self):
        """제목에 주소가 없으면 중복판정 열쇠는 한 글자도 안 바뀐다."""
        title = "한수원, 원전 열 활용 청정수소 생산 효율 높인다"
        self.assertEqual(data_quality.title_key(title),
                         data_quality.title_key(data_quality.strip_emails(title)))


class EmailRedactionAtCaptureTests(unittest.TestCase):
    """지우는 자리가 실제로 수집 경로 위에 있는가.

    함수만 맞고 부르는 자리가 없으면 다음 기사가 그대로 또 저장된다.
    """

    def test_the_body_extractor_strips_before_it_returns(self):
        import article_body
        paragraphs = "".join(
            f"<p>한국수력원자력이 원전에서 나오는 열을 활용해 청정수소 생산 효율을 "
            f"높인다고 {i}일 밝혔다. 관련 사업은 대전에서 진행된다.</p>"
            for i in range(6))
        # 짧은 바이라인 줄은 기존 `_usable` 이 이미 버린다. 여기서 잠그는 것은
        # **긴 줄 안에 섞인** 주소다 — 그것이 지금까지 빠져나가던 모양이다.
        tail = ("<p>자세한 내용은 한국정경신문 정아람 기자 areum@example.co.kr 으로 "
                "문의하거나 공식 홈페이지를 참고하시기 바랍니다. 추가 안내가 이어집니다.</p>")
        body = article_body.extract_text(f"<article>{paragraphs}{tail}</article>")
        self.assertTrue(body, "본문 추출 자체가 실패하면 이 검사는 아무것도 못 잠근다")
        self.assertNotIn("areum@example.co.kr", body)
        self.assertIn("한국정경신문 정아람 기자", body)

    def test_the_feed_parser_strips_the_description(self):
        import news_bot
        rows = news_bot.parse_kaeri_board(
            '<li class="item"><a href="/board/view?id=1"><strong>원자력연구원 성과</strong></a>'
            '<span class="desc">문의 press@example.re.kr 로 연락바랍니다</span>'
            "<dd>2026-08-20</dd></li>")
        self.assertEqual(len(rows), 1)
        self.assertNotIn("press@example.re.kr", rows[0]["description"])
        self.assertIn("문의", rows[0]["description"])
