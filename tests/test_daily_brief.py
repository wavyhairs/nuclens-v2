"""daily_brief.py 단위 테스트 — 분류·투자 구조화·보고서 게이트·outbox 원자성."""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

# telegram_send 는 토큰 없으면 import 시 sys.exit → 테스트에선 공용 fake 주입
import _fake_tg  # noqa: E402
fake_tg = _fake_tg.installed

import channel_queue  # noqa: E402
import daily_brief as db  # noqa: E402
import issue_continuity  # noqa: E402
import ranking  # noqa: E402

NOW = datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc)


def qitem(h="h1", importance="nice_to_know", section="international",
          domain="world-nuclear-news.org", title="Title", features=None, **kw):
    d = {"hash": h, "importance": importance, "section": section, "domain": domain,
         "title": title, "title_kr": kw.pop("title_kr", title),
         "link": f"https://{domain}/x/{h}",
         "summary": kw.pop("summary", "요약입니다"),
         "implication": kw.pop("implication", "시사점입니다"),
         "queued_at": kw.pop("queued_at", NOW.isoformat()),
         "related_reports": [], "tags": []}
    if features is not None:
        d["features"] = features
    d.update(kw)
    return d


class TestRegion(unittest.TestCase):
    def test_khnp_section_domestic_even_foreign_source(self):
        self.assertEqual(db.region({"section": "khnp", "domain": "reuters.com"}), "국내")

    def test_us_article_misclassified_domestic_corrected(self):
        self.assertEqual(db.region({"section": "domestic", "domain": "ans.org"}), "해외")

    def test_international(self):
        self.assertEqual(db.region({"section": "international", "domain": "unknown.io"}), "해외")

    def test_google_kr_feed(self):
        self.assertEqual(db.region({"section": "domestic", "domain": "news.google.co.kr"}), "국내")

    # --- scope (LLM 직접 판정) 최우선 ---

    def test_scope_overrides_domain(self):
        # 한국 매체 도메인이지만 주제가 해외 → 해외
        self.assertEqual(
            db.region({"scope": "overseas", "section": "domestic", "domain": "yna.co.kr"}), "해외")

    def test_scope_kr_overrides_foreign_domain(self):
        # 해외 매체가 보도한 한국 주제(체코 수주 등) → 국내
        self.assertEqual(
            db.region({"scope": "kr", "section": "international", "domain": "reuters.com"}), "국내")

    # --- scope 없는 과거 큐 항목: section·도메인·제목 언어 휴리스틱 ---

    def test_korean_media_foreign_topic_goes_overseas(self):
        # 국내 매체의 해외 기사 — section=international 이면 매체 국적과 무관하게 해외
        self.assertEqual(
            db.region({"section": "international", "domain": "news.google.co.kr"}), "해외")

    def test_foreign_smr_not_domestic(self):
        # 지역 신호 없는 section='smr' + 외국 도메인 + 영문 제목 → 해외
        # (기존 기본값이 '국내'여서 미국 SMR 기사가 국내 브리핑에 섞이던 버그)
        self.assertEqual(db.region(
            {"section": "smr", "domain": "terrapower.com",
             "title": "TerraPower announces reactor milestone"}), "해외")
        self.assertEqual(db.region(
            {"section": "smr", "domain": "prnewswire.com",
             "title": "Blue Energy secures strategic investment"}), "해외")

    def test_korean_smr_domestic(self):
        self.assertEqual(db.region(
            {"section": "smr", "domain": "news.google.co.kr",
             "title": "두산에너빌리티, i-SMR 주기기 수주"}), "국내")

    def test_unknown_domain_defaults_overseas(self):
        self.assertEqual(db.region({"section": "", "domain": "county17.com",
                                    "title": "BWXT plans fuel hub"}), "해외")


class TestInvestmentSurfaceIsGone(unittest.TestCase):
    """텔레그램 카드에서 투자 관점이 다시 생기지 않는지 지킨다.

    예전에는 선별 뒤에 투자 분석 LLM 을 한 번 더 불러(`enrich_investment`)
    `💰 투자 관점` 줄을 붙였다. 이 브리핑의 독자는 투자자가 아니라 정책·산업
    실무자다 — 카드는 무슨 일이 있었고 무엇이 달라졌는지만 말한다.
    """

    def test_investment_helpers_are_removed(self):
        for name in ("enrich_investment", "render_investment", "_sanitize_invest",
                     "INVEST_SYSTEM_PROMPT", "INVEST_THEMES"):
            self.assertFalse(hasattr(db, name), f"{name} 이 되살아났다")

    def test_card_has_no_investment_field(self):
        card = db.item_to_card(qitem())
        self.assertNotIn("investment", card)

    def test_rendered_card_has_no_investment_expression(self):
        from synthesize import format_cards_message
        # 카드에 investment 키를 억지로 넣어도 렌더러가 그 줄을 만들지 않는다.
        card = db.item_to_card(qitem())
        card["investment"] = "SMR 밸류체인 수혜 / 가스 피크발전 부담 (SMR·중기)"
        message = format_cards_message([card], header="국내")
        for banned in ("투자 관점", "💰", "수혜", "부담", "밸류체인"):
            self.assertNotIn(banned, message, f"발송문에 {banned} 이 남아 있다")


class TestWhyImportantDropsCliches(unittest.TestCase):
    """`왜 중요` 는 의미 부여가 아니라 상태 변화를 말해야 한다."""

    def test_cliche_ending_drops_the_line(self):
        for cliche in ("원자력 산업에 중요하다.",
                       "관련 산업의 관심이 필요하다.",
                       "향후 시장 확대가 전망된다.",
                       "정책적 의지를 보여준다."):
            self.assertIsNone(db._change_or_none(cliche), cliche)

    def test_change_sentence_survives(self):
        text = "정부 검토 단계였던 신규 원전 지원이 실제 예산 프로그램으로 전환됐다."
        self.assertEqual(db._change_or_none(text), text)

    def test_quantity_rescues_a_soft_ending(self):
        # 상투적 어미라도 '언제·얼마'가 실려 있으면 정보다 — 기존 게이트의 판단.
        text = "2028년 착공을 목표로 인허가 전 단계가 열릴 전망이다."
        self.assertEqual(db._change_or_none(text), text)

    def test_empty_stays_empty(self):
        self.assertIsNone(db._change_or_none(""))
        self.assertIsNone(db._change_or_none(None))

    def test_card_without_why_still_renders(self):
        from synthesize import format_cards_message
        art = qitem()
        art["why_important"] = "원자력 산업에 중요하다."
        art["implication"] = ""           # 이 줄이 대신 채우지 않도록 비운다
        card = db.item_to_card(art)
        self.assertIsNone(card["why"])
        message = format_cards_message([card], header="국내")
        self.assertNotIn("왜 중요", message)
        self.assertIn("무슨 일", message)


class TestTakeawayLabelFollowsContent(unittest.TestCase):
    """`implication` 은 한수원 접점이 있을 때만 한수원 라벨을 단다.

    이 값을 만드는 수집 프롬프트에는 한수원이라는 말이 없다(원인·다음 절차·수치·
    영향 대상을 요구한다). 그래서 라벨을 무조건 붙이면 한수원이 한 글자도 없는
    문장이 `🇰🇷 한수원 시사점` 으로 나간다 — 2026-09-19 실측 6건 중 4건이 그랬다.
    """

    def _render(self, **over):
        from synthesize import format_cards_message
        art = qitem(**over)
        return format_cards_message([db.item_to_card(art)], header="국내")

    def test_direct_khnp_article_keeps_the_khnp_label(self):
        msg = self._render(implication="한수원의 재생에너지 사업 다각화 사례이다.",
                           implication_requirement="required")
        self.assertIn("🇰🇷 한수원 시사점", msg)
        self.assertNotIn("왜 중요", msg)

    def test_indirect_article_renders_it_as_why(self):
        msg = self._render(implication="AI 전력수요가 원자로 조달 방식을 제품 반복 생산으로 옮기고 있다.",
                           implication_requirement="expected")
        self.assertIn("왜 중요", msg)
        self.assertNotIn("한수원 시사점", msg)

    def test_the_axis_is_not_written_twice(self):
        # why_important 가 이미 `왜 중요` 를 채웠으면 간접 해석은 버린다.
        msg = self._render(
            why_important="정부 검토 단계였던 지원이 예산 프로그램으로 전환됐다.",
            implication="중복으로 실리면 안 되는 두 번째 해석 문장이다.",
            implication_requirement="optional")
        self.assertEqual(msg.count("왜 중요"), 1)
        self.assertIn("예산 프로그램으로 전환", msg)
        self.assertNotIn("두 번째 해석", msg)

    def test_social_cards_keep_the_khnp_label(self):
        # 소셜 합성 프롬프트는 이 칸에 한수원 관점을 직접 요구한다.
        from synthesize import format_cards_message
        card = {"headline": "제목", "what": "무슨 일.", "why": None,
                "kr_takeaway": "한국이 참고할 대목이다.", "khnp_direct": True,
                "cluster": {"url": "https://x.example/a", "sources": ["x"]}, "cred": {}}
        self.assertIn("🇰🇷 한수원 시사점", format_cards_message([card], header="소셜"))


class TestUnfoundedKoreanBeneficiaryIsDropped(unittest.TestCase):
    """기사에 없는 한국 수혜 주체를 만들어 낸 해석은 텔레그램에서 뺀다.

    세 조건이 모두 맞을 때만 버린다 — 낱말 하나로 지우면 정상 문장이 함께 날아간다.
    실측 1629건 중 걸리는 것은 2건이다.
    """

    def _drop(self, implication, **art):
        return db._invents_korean_beneficiary(implication, {"implication": implication, **art})

    def test_invented_beneficiary_in_a_foreign_event_is_dropped(self):
        # 튀르키예 재생에너지 기사 — 원문 어디에도 한국 기업이 없다.
        self.assertTrue(self._drop(
            "대규모 송전망 확충 정책은 초고압 전력기기 기술력을 보유한 한국 기업들에 "
            "새로운 해외 시장 진출 기회가 될 수 있다.",
            title_kr="튀르키예, 2035년까지 1080억 달러 투입해 재생에너지 120GW 구축",
            summary="튀르키예 정부가 발전 설비와 송전망에 1080억 달러를 투자한다.",
            source_excerpt="튀르키예 정부가 2035년까지 1080억 달러를 투입한다.",
            scope="overseas", section="international"))

    def test_the_articles_own_korean_subject_is_kept(self):
        # '국내 조선 3사' 기사 — 그 주체는 해석이 데려온 것이 아니라 기사의 주어다.
        self.assertFalse(self._drop(
            "국내 조선업계가 단순 선박 건조를 넘어 해양 에너지 인프라 솔루션 시장으로 "
            "사업 영역을 확장하고 있다.",
            title_kr="국내 조선 3사, 가스텍 2026서 차세대 해양 에너지 솔루션 기술 인증 획득",
            summary="HD현대·삼성중공업·한화오션이 선급 기본인증을 획득했다.",
            scope="kr", section="domestic"))

    def test_a_korean_event_grounds_its_own_beneficiary(self):
        # 표지 목록은 기업 이름을 모른다 — 사건이 한국 것이면 지우지 않는다.
        self.assertFalse(self._drop(
            "국내 기업의 수주 확대로 이어지고 있다.",
            title_kr="효성중공업, 초고압변압기 수주", summary="수주했다.",
            scope="kr", section="domestic"))

    def test_a_factual_sentence_is_not_inference(self):
        # 수혜·기회 표현이 없으면 한국 주체가 있어도 건드리지 않는다.
        self.assertFalse(self._drop(
            "국내 기업 3곳이 이번 입찰에 참여한다고 공시했다.",
            title_kr="해외 입찰 공고", summary="입찰이 공고됐다.", scope="overseas"))

    def test_inference_without_a_korean_subject_is_not_this_guard(self):
        # 이 guard 는 '한국 수혜 주체'만 본다. 다른 빈껍데기는 다른 게이트 몫이다.
        self.assertFalse(self._drop(
            "유럽 전력기기 공급사의 수주 확대로 이어질 수 있다.",
            title_kr="유럽 송전망 확충", summary="확충한다.", scope="overseas"))

    def test_the_card_omits_the_line_entirely(self):
        from synthesize import format_cards_message
        art = qitem(implication="한국 기업들에 새로운 시장 진출 기회가 될 수 있다.",
                    title_kr="튀르키예, 재생에너지 120GW 구축",
                    summary="튀르키예가 투자한다.", source_excerpt="튀르키예가 투자한다.",
                    scope="overseas", section="international")
        card = db.item_to_card(art)
        self.assertIsNone(card["kr_takeaway"])
        msg = format_cards_message([card], header="해외")
        self.assertNotIn("진출 기회", msg)
        self.assertIn("무슨 일", msg)


class TestKhnpLabelNeedsKoreanContact(unittest.TestCase):
    """한수원 라벨은 등급만으로 붙지 않는다 — 한국 접점이 함께 있어야 한다."""

    def _direct(self, **art):
        return db._khnp_direct({"implication_requirement": "required", **art})

    def test_topic_only_foreign_article_is_not_khnp(self):
        # SMR·AI 전력수요는 국적이 없는 축이라 해외 기사도 required 가 된다.
        self.assertFalse(self._direct(
            title_kr="오펜하이머 CEO, 원전 반복 생산 강조", summary="주장했다.",
            scope="overseas", section="international"))

    def test_domestic_article_is_khnp(self):
        self.assertTrue(self._direct(
            title_kr="산업부, 원전 수출 지원 확대", summary="확대한다.",
            scope="kr", section="domestic"))

    def test_khnp_export_deal_keeps_the_label_without_repeating_the_name(self):
        # '한수원 체코 두코바니 계약' — 해석 문장에 한수원이 다시 없어도 유지된다.
        self.assertTrue(self._direct(
            title_kr="한수원, 체코 두코바니 신규원전 본계약 체결",
            summary="본계약을 체결했다.", implication="후속 호기 협상 시점이 앞당겨진다.",
            section="khnp"))

    def test_foreign_article_naming_korea_keeps_the_label(self):
        self.assertTrue(self._direct(
            title_kr="미국, 한국산 원전 기자재 인증 절차 간소화",
            summary="간소화한다.", scope="overseas", section="international"))

    def test_below_required_is_never_khnp(self):
        self.assertFalse(db._khnp_direct({
            "implication_requirement": "expected", "title_kr": "한수원 소식",
            "scope": "kr", "section": "khnp"}))


class TestOfficialBadge(unittest.TestCase):
    """✅ 는 '신뢰하는 매체'가 아니라 '기관이 낸 원문'에만 붙는다.

    2026-09-19 실측: 국내·해외 17건에 배지가 7개 붙었고 **7개가 전부 오판**이었다.
    ✅ 에너지신문·✅ KBS 뉴스(일반 매체), ✅ ANS·✅ NEI(전문지), 그리고 IAEA 를
    인용한 Reuters 기사의 ✅ IAEA(제목에서 기관명을 주워 온 것).
    """

    def _badge(self, url, title=""):
        from sources import credibility
        from synthesize import official_badge
        return official_badge({"cred": credibility({"url": url, "title": title, "meta": ""})}).strip()

    def test_official_domain_keeps_the_badge(self):
        self.assertEqual(self._badge("https://www.iaea.org/newscenter/x"), "✅ IAEA")
        self.assertEqual(self._badge("https://www.nssc.go.kr/board/x"), "✅ 원자력안전위원회")
        self.assertEqual(self._badge("https://www.khnp.co.kr/board/x"), "✅ 한국수력원자력")

    def test_trusted_media_do_not_get_an_official_badge(self):
        # tier1·tier2 에 들어 있어도 기관 발표가 아니면 공식출처가 아니다.
        for url in ("https://www.reuters.com/world/x",
                    "https://www.yna.co.kr/view/x",
                    "https://www.world-nuclear-news.org/articles/x",
                    "https://www.ans.org/news/x"):
            self.assertEqual(self._badge(url), "", url)

    def test_quoting_an_agency_is_not_being_one(self):
        # URL 로 확인 못 하면 제목에서 기관명을 주워 배지를 달지 않는다.
        self.assertEqual(
            self._badge("https://news.google.com/rss/articles/CBMi",
                        "IAEA, 러시아 쿠르스크 원전 냉각탑 드론 피격 확인"), "")
        self.assertEqual(
            self._badge("https://www.reuters.com/world/x",
                        "IAEA says Kursk cooling tower was hit"), "")


class TestRequiredFieldBackfill(unittest.TestCase):
    def _run_with_response(self, response):
        item = qitem(implication="", detail="근거가 있는 기사 본문")
        orig_call = db.call_json
        orig_avail = db.is_available
        orig_requirement = db.khnp_relevance.implication_requirement
        db.is_available = lambda: True
        db.call_json = lambda *a, **k: response
        db.khnp_relevance.implication_requirement = lambda _item: {
            "level": "required",
            "regenerate": True,
            "current": "",
            "reasons": [],
        }
        try:
            return item, db.complete_required_fields([item])
        finally:
            db.call_json = orig_call
            db.is_available = orig_avail
            db.khnp_relevance.implication_requirement = orig_requirement

    def test_top_level_list_response_is_nonfatal(self):
        """실운영 회귀: Gemini가 객체 대신 배열을 반환해도 Plan은 계속한다."""
        item, diag = self._run_with_response([{"idx": 0, "implication": "문장"}])
        self.assertEqual(diag["skipped"], "invalid_response")
        self.assertEqual(item["implication"], "")

    def test_non_list_items_response_is_nonfatal(self):
        item, diag = self._run_with_response({"items": {"idx": 0}})
        self.assertEqual(diag["skipped"], "invalid_response")
        self.assertEqual(item["implication"], "")


class TestReportGate(unittest.TestCase):
    def feat(self, **kw):
        base = {"event_type": "other", "korea_relevance": 0, "market_materiality": 0,
                "policy_materiality": 0, "novelty": 0, "evidence_strength": 0,
                "report_worthiness": 0}
        base.update(kw)
        return base

    def test_zero_candidates_no_llm(self):
        items = [qitem(h="a", features=self.feat(report_worthiness=1)),
                 qitem(h="b", features=self.feat())]
        called = []
        orig = db.call_json
        db.call_json = lambda *a, **k: called.append(1) or {"reports": []}
        try:
            msg, diag = db.build_report_recs(items)
        finally:
            db.call_json = orig
        self.assertEqual(msg, "")
        self.assertEqual(diag["candidates"], [])
        self.assertEqual(called, [])  # 후보 0건이면 Gemini 호출 자체가 없다

    def test_malformed_response_is_nonfatal(self):
        item = qitem(
            importance="must_read",
            features=self.feat(report_worthiness=3, policy_materiality=3),
        )
        orig_call, orig_avail = db.call_json, db.is_available
        db.is_available = lambda: True
        db.call_json = lambda *a, **k: [{"idx": 0, "topic": "잘못된 최상위 배열"}]
        try:
            message, diag = db.build_report_recs([item])
        finally:
            db.call_json, db.is_available = orig_call, orig_avail
        self.assertEqual(message, "")
        self.assertEqual(diag["skipped"], "invalid_response")

    def test_gate_requires_strong_signal(self):
        weak = qitem(h="a", features=self.feat(report_worthiness=3))  # 이벤트·정책·등급 없음
        strong = qitem(h="b", importance="must_read",
                       features=self.feat(report_worthiness=2))
        out = db.gate_report_candidates([weak, strong], negatives=[])
        self.assertEqual([a["hash"] for a in out], ["b"])

    def test_legacy_item_must_read_passes(self):
        legacy = qitem(h="c", importance="must_read")  # features 없음 (옛 스키마)
        out = db.gate_report_candidates([legacy], negatives=[])
        self.assertEqual(len(out), 1)

    def test_negative_example_blocks(self):
        a = qitem(h="a", importance="must_read",
                  title_kr="체코 언론, 두코바니 일정 지연 가능성 보도")
        negs = ["체코 언론, 두코바니 일정 지연 가능성 보도 — 전망성 반복"]
        self.assertEqual(db.gate_report_candidates([a], negatives=negs), [])

    def test_cap_two_and_invalid_idx(self):
        cands = [qitem(h=f"c{i}", importance="must_read",
                       features=self.feat(report_worthiness=3, policy_materiality=3))
                 for i in range(4)]
        orig_call, orig_avail = db.call_json, db.is_available
        db.is_available = lambda: True
        db.call_json = lambda *a, **k: {"reports": [
            {"idx": 0, "topic": "T0", "why": "w"},
            {"idx": 99, "topic": "무효 idx"},         # 필터돼야 함
            {"idx": 1, "topic": "T1"},
            {"idx": 2, "topic": "T2 — 3건째, 컷"},
        ]}
        try:
            msg, diag = db.build_report_recs(cands)
        finally:
            db.call_json, db.is_available = orig_call, orig_avail
        self.assertEqual(len(diag["recommended"]), 2)  # 하루 최대 2건
        self.assertIn("T0", msg)
        self.assertIn("T1", msg)
        self.assertNotIn("T2", msg)
        self.assertNotIn("무효", msg)

    def test_recommended_hash_is_not_truncated(self):
        """웹이 기사에 배지를 다는 조인 키다 — 8자로 자르면 delivery_log 와 안 붙는다."""
        full = "538c53ac9b1d2e4f"
        cands = [qitem(h=full, importance="must_read",
                       features=self.feat(report_worthiness=3, policy_materiality=3))]
        orig_call, orig_avail = db.call_json, db.is_available
        db.is_available = lambda: True
        db.call_json = lambda *a, **k: {"reports": [{"idx": 0, "topic": "T", "why": "w"}]}
        try:
            _, diag = db.build_report_recs(cands)
        finally:
            db.call_json, db.is_available = orig_call, orig_avail
        self.assertEqual(diag["recommended"][0]["hash"], full)


class OutboxBase(unittest.TestCase):
    """tmpdir 로 상태 파일 경로를 돌려서 실제 파일 흐름 검증."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        p = Path(self.tmp.name)
        self._orig = (db.QUEUE_FILE, db.OUTBOX_FILE, db.OUTBOX_RESULT_FILE,
                      db.DELIVERY_LOG_FILE, ranking.DELIVERY_LOG_FILE,
                      issue_continuity.DELIVERY_LOG_FILE,
                      channel_queue.QUEUE_FILE)
        db.QUEUE_FILE = p / "digest_queue.json"
        db.OUTBOX_FILE = p / "outbox.json"
        db.OUTBOX_RESULT_FILE = p / "outbox_result.json"
        db.DELIVERY_LOG_FILE = p / "delivery_log.jsonl"
        ranking.DELIVERY_LOG_FILE = p / "delivery_log.jsonl"
        # 연속일 게이트도 같은 tmpdir 을 봐야 한다. 안 돌리면 저장소 루트의 **진짜**
        # 발송 이력을 읽어서, 픽스처 제목이 우연히 지난 회차와 닮은 날 테스트가
        # 조용히 달라진다(이력은 계속 늘어난다).
        issue_continuity.DELIVERY_LOG_FILE = p / "delivery_log.jsonl"
        # cmd_plan 이 구독 채널 배치를 적재한다 — 돌리지 않으면 테스트 픽스처가
        # 저장소 루트의 진짜 channel_outbox.json 에 쌓인다.
        channel_queue.QUEUE_FILE = p / "channel_outbox.json"
        # Gemini 차단 (호출 0)
        self._avail = db.is_available
        db.is_available = lambda: False
        fake_tg.sent_messages = []
        fake_tg.fail_next = False

    def tearDown(self):
        (db.QUEUE_FILE, db.OUTBOX_FILE, db.OUTBOX_RESULT_FILE,
         db.DELIVERY_LOG_FILE, ranking.DELIVERY_LOG_FILE,
         issue_continuity.DELIVERY_LOG_FILE,
         channel_queue.QUEUE_FILE) = self._orig
        db.is_available = self._avail
        self.tmp.cleanup()

    def seed_queue(self, items):
        db.save_queue(items)


class TestOutboxFlow(OutboxBase):
    def _queue(self):
        return [
            qitem(h="d1", section="khnp", domain="khnp.co.kr", importance="must_read",
                  title="한수원 체코 본계약"),
            qitem(h="f1", section="international", title="NRC approves NuScale design"),
            qitem(h="f2", section="international", title="NRC approves the NuScale design today"),  # f1 후속
            qitem(h="n1", importance="noise", title="채용 공고"),
            qitem(h="m1", importance="market", title="테마주 급등"),
        ]

    def test_plan_prunes_selected_dups_junk_keeps_rest(self):
        many = self._queue() + [
            qitem(h=f"f{i}", section="international", title=f"별개 해외뉴스 {i} 완전다름")
            for i in range(3, 11)]
        self.seed_queue(many)
        self.assertEqual(db.cmd_plan(), 0)
        outbox = db.load_outbox()
        self.assertEqual(outbox["status"], "pending")
        queue_after = db.load_queue()
        left = {a["hash"] for a in queue_after}
        # noise/market/선별/후속 은 제거, 미선별 해외는 잔류 (다음날 재경쟁)
        self.assertNotIn("n1", left)
        self.assertNotIn("m1", left)
        self.assertNotIn("d1", left)
        self.assertNotIn("f2", left)  # f1(선별)의 중복이므로 함께 제거
        selected = {i["hash"] for i in outbox["items"]}
        self.assertNotIn("f2", selected)
        self.assertEqual(len(left), len(many) - len(outbox["prune_hashes"]))

    def test_empty_queue_plan(self):
        self.seed_queue([])
        db.cmd_plan()
        outbox = db.load_outbox()
        self.assertEqual(outbox["status"], "empty")
        self.assertEqual(outbox["quality_gate_version"], db.QUALITY_GATE_VERSION)
        self.assertRegex(outbox["quality_payload_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(db.cmd_send(), 0)  # empty → 발송 스킵, 에러 아님
        self.assertEqual(fake_tg.sent_messages, [])

    def test_legacy_pending_outbox_without_quality_version_is_not_sent(self):
        """강화 전 저장된 pending claim 이 최종 품질 게이트를 우회하면 안 된다."""
        self.seed_queue(self._queue())
        db.cmd_plan()
        outbox = db.load_outbox()
        self.assertEqual(outbox["quality_gate_version"], db.QUALITY_GATE_VERSION)
        outbox.pop("quality_gate_version")
        db.save_outbox(outbox)

        self.assertEqual(db.cmd_send(), 1)
        self.assertEqual(fake_tg.sent_messages, [])
        blocked = db.load_outbox()
        self.assertEqual(blocked["quality_gate_error"]["code"],
                         "quality_gate_version_mismatch")
        self.assertTrue(all(b["status"] == "failed" for b in blocked["briefs"]))
        self.assertTrue(all(b["failure_reason"] == "quality_gate_version_mismatch"
                            for b in blocked["briefs"]))
        results = json.loads(db.OUTBOX_RESULT_FILE.read_text(encoding="utf-8"))
        self.assertTrue(all(r["status"] == "failed" for r in results))

    def test_mismatched_or_non_integer_quality_version_is_not_sent(self):
        template = {
            "schema_version": 1,
            "date": "2026-07-12",
            "created_at": NOW.isoformat(),
            "status": "pending",
            "briefs": [{"name": "국내", "text": "x", "keyboard": None,
                        "status": "pending"}],
            "items": [],
            "prune_hashes": [],
        }
        for found in (0, db.QUALITY_GATE_VERSION + 1, str(db.QUALITY_GATE_VERSION), True):
            with self.subTest(found=found):
                outbox = json.loads(json.dumps(template))
                outbox["quality_gate_version"] = found
                results = db.send_outbox(outbox, now=NOW)
                self.assertEqual(fake_tg.sent_messages, [])
                self.assertEqual(results[0]["status"], "failed")
                self.assertEqual(results[0]["failure_reason"],
                                 "quality_gate_version_mismatch")
                self.assertEqual(outbox["briefs"][0]["status"], "failed")

    def test_missing_or_malformed_quality_digest_is_not_sent(self):
        template = db._seal_quality_payload({
            "schema_version": 1,
            "quality_gate_version": db.QUALITY_GATE_VERSION,
            "date": "2026-07-12",
            "created_at": NOW.isoformat(),
            "status": "pending",
            "briefs": [{"name": "국내", "text": "x", "status": "pending"}],
            "items": [],
            "quality_diag": {},
        })
        cases = (("missing", None), ("bool", True), ("number", 1),
                 ("short", "0" * 63), ("uppercase", "A" * 64))
        for label, found in cases:
            with self.subTest(label=label):
                outbox = json.loads(json.dumps(template))
                if label == "missing":
                    outbox.pop("quality_payload_digest")
                else:
                    outbox["quality_payload_digest"] = found
                results = db.send_outbox(outbox, now=NOW)
                self.assertEqual(fake_tg.sent_messages, [])
                self.assertEqual(results[0]["status"], "failed")
                self.assertIn(outbox["quality_gate_error"]["code"], {
                    "quality_payload_digest_missing", "quality_payload_digest_invalid"})

    def test_tampered_brief_or_validation_metadata_is_not_sent(self):
        template = db._seal_quality_payload({
            "schema_version": 1,
            "quality_gate_version": db.QUALITY_GATE_VERSION,
            "date": "2026-07-12",
            "created_at": NOW.isoformat(),
            "status": "pending",
            "briefs": [{"name": "국내", "text": "검증된 본문", "status": "pending"}],
            "items": [{"hash": "h1", "title_kr": "검증된 제목"}],
            "quality_diag": {"final_cards": [{"hash": "h1", "action": "allow"}]},
            "field_diag": {"attempted": 0},
        })
        mutations = {
            "text": lambda outbox: outbox["briefs"][0].update(text="변조된 본문"),
            "name": lambda outbox: outbox["briefs"][0].update(name="변조된 섹션"),
            "item": lambda outbox: outbox["items"][0].update(title_kr="변조된 제목"),
            "card_audit": lambda outbox: outbox["quality_diag"]["final_cards"][0].update(
                action="quarantine"),
            "field_audit": lambda outbox: outbox["field_diag"].update(attempted=1),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                outbox = json.loads(json.dumps(template))
                mutate(outbox)
                results = db.send_outbox(outbox, now=NOW)
                self.assertEqual(fake_tg.sent_messages, [])
                self.assertEqual(results[0]["failure_reason"],
                                 "quality_payload_digest_mismatch")
                self.assertEqual(outbox["status"], "quality_rejected")

    def test_blocked_outbox_reaches_the_admin_as_a_quality_event(self):
        """발송이 막힌 사실이 로그 한 줄로 끝나면 그날 브리핑이 조용히 빠진다."""
        outbox = db._seal_quality_payload({
            "schema_version": 1,
            "quality_gate_version": db.QUALITY_GATE_VERSION,
            "date": "2026-07-12",
            "created_at": NOW.isoformat(),
            "status": "pending",
            "briefs": [{"name": "국내", "text": "검증된 본문", "status": "pending"}],
            "items": [], "quality_diag": {},
        })
        outbox["briefs"][0]["text"] = "변조된 본문"
        db.send_outbox(outbox, now=NOW)
        self.assertEqual(fake_tg.sent_messages, [])

        log = db.ROOT / "quality_event_blocked.jsonl"
        self.addCleanup(log.unlink, True)
        added = db.append_quality_audit(outbox, path=log, now=NOW)
        self.assertEqual(added, 1)
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(row["record_type"], "quality_event")
        self.assertEqual(row["severity"], "critical")
        self.assertEqual(row["min_occurrences"], 1)  # 재시도로 회복되지 않는다
        self.assertIn("quality_payload_digest_mismatch", row["alert_key"])
        self.assertEqual(row["items"][0]["blocked_briefs"], ["국내"])

    def test_dedup_failure_reaches_the_admin(self):
        """중복 판정 없이 나간 날이 로그 한 줄로 끝나면 안 된다(2026-09-25 editorial_final 503)."""
        outbox = {"date": "2026-07-12", "created_at": NOW.isoformat(),
                  "quality_diag": {"dedup_failures": [
                      {"stage": "editorial_final", "error": "HTTP 503"},
                      {"stage": "cross_day", "error": "HTTP 503"}]}}
        log = db.ROOT / "quality_event_dedup.jsonl"
        self.addCleanup(log.unlink, True)
        self.assertEqual(db.append_quality_audit(outbox, path=log, now=NOW), 1)
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(row["alert_key"], "dedup-review-failed")
        self.assertEqual(row["min_occurrences"], 1)
        self.assertIn("cross_day", row["detail"])

    def test_quality_event_is_not_emitted_when_the_outbox_is_intact(self):
        self.seed_queue(self._queue())
        db.cmd_plan()
        outbox = db.load_outbox()
        db.send_outbox(outbox, now=NOW)
        self.assertNotIn("quality_gate_error", outbox)
        log = db.ROOT / "quality_event_intact.jsonl"
        self.addCleanup(log.unlink, True)
        db.append_quality_audit(outbox, path=log, now=NOW)
        rows = [json.loads(line) for line in
                log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        self.assertEqual(
            [r for r in rows if "outbox-quality-claim" in str(r.get("alert_key"))], [])

    def test_report_recommendation_naming_an_absent_company_is_dropped(self):
        """브리핑 맨 위에 붙고 부서가 실제로 착수하는 근거가 되는 문구다."""
        candidates = [{
            "hash": "h1",
            "title": "KHNP wins Czech Dukovany reactor construction contract",
            "title_kr": "한국수력원자력, 체코 두코바니 원전 건설 계약 수주",
            "summary": "한국수력원자력이 체코 두코바니 신규 원전 건설 계약을 따냈다.",
        }]
        good = (0, {"topic": "체코 두코바니 수주의 국내 공급망 함의",
                    "why": "한국수력원자력의 유럽 신규 건설 진입 사례다.",
                    "angles": ["공급망 준비도"]})
        bad = (0, {"topic": "웨스팅하우스 견제 전략 재점검",
                   "why": "웨스팅하우스가 같은 사업에 참여했다.", "angles": []})
        kept, dropped = db.verify_report_recs([good, bad], candidates)
        self.assertEqual([r for _i, r in kept], [good[1]])
        self.assertEqual(dropped[0]["entities"], ["westinghouse"])

    def test_report_recommendation_inventing_a_number_is_dropped(self):
        candidates = [{"hash": "h1", "title": "KHNP wins Czech Dukovany contract",
                       "title_kr": "한국수력원자력, 체코 두코바니 원전 건설 계약 수주",
                       "summary": "한국수력원자력이 체코 두코바니 계약을 따냈다."}]
        kept, dropped = db.verify_report_recs(
            [(0, {"topic": "24조원 규모 수주의 재무 영향", "angles": []})], candidates)
        self.assertEqual(kept, [])
        self.assertIn("24조원", dropped[0]["claims"])

    def test_report_recommendation_may_span_several_candidates(self):
        """추천은 여러 후보를 묶어 한 주제를 말할 수 있다 — 묶었다고 막지 않는다."""
        candidates = [
            {"hash": "h1", "title_kr": "한국수력원자력, 체코 두코바니 계약 수주",
             "summary": "체코 신규 원전 계약을 따냈다."},
            {"hash": "h2", "title_kr": "두산에너빌리티, 테라파워 기자재 계약",
             "summary": "두산에너빌리티가 테라파워에 기자재를 공급한다."},
        ]
        kept, dropped = db.verify_report_recs(
            [(0, {"topic": "한국수력원자력·두산에너빌리티 동시 수주의 공급망 함의",
                  "why": "테라파워와 체코 사업이 같은 주에 겹쳤다.", "angles": []})],
            candidates)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])

    def test_social_cards_use_deterministic_final_fact_gate(self):
        cluster = {
            "title": "Canada opens new uranium mine",
            "fulltext": "Canada opened a new uranium mine with annual output of 500 tonnes.",
            "url": "https://example.com/canada-mine",
        }
        wrong_core = {
            "headline": "스페인 원전 수명연장 승인",
            "what": "스페인 규제기관이 원전 수명연장을 승인했다.",
            "cluster": cluster,
        }
        safe, audits = db.verify_social_cards([wrong_core])
        self.assertEqual(safe, [])
        self.assertEqual(audits[0]["action"], "quarantine")

        unsupported_optional = {
            "headline": "캐나다 신규 우라늄 광산 개장",
            "what": "캐나다가 신규 우라늄 광산을 개장했다.",
            "kr_takeaway": "한국수력원자력이 345MW 원자로 공급 계약을 체결했다.",
            "cluster": cluster,
        }
        safe, audits = db.verify_social_cards([unsupported_optional])
        self.assertEqual(len(safe), 1)
        self.assertIsNone(safe[0]["kr_takeaway"])
        self.assertEqual(audits[0]["removed_fields"], ["kr_takeaway"])

    def test_manual_research_path_uses_the_same_card_gate(self):
        """send_research 는 daily_brief 를 거치지 않는다 — 같은 카드가 한쪽에서만
        검증되면 우회 경로가 그대로 남는다."""
        import send_research
        import synthesize

        self.assertIs(send_research.verify_cards, synthesize.verify_cards)
        cluster = {
            "title": "Canada opens new uranium mine",
            "fulltext": "Canada opened a new uranium mine with annual output of 500 tonnes.",
            "url": "https://example.com/canada-mine",
        }
        safe, audits = synthesize.verify_cards([{
            "headline": "스페인 원전 수명연장 승인",
            "what": "스페인 규제기관이 원전 수명연장을 승인했다.",
            "cluster": cluster,
        }])
        self.assertEqual(safe, [])
        self.assertEqual(audits[0]["action"], "quarantine")

    def test_final_card_sanitization_is_written_back_to_article(self):
        article = qitem(
            summary="검증된 사실입니다.",
            why_important="근거 없는 중요성입니다.",
            implication="근거 없는 시사점입니다.",
        )
        cleaned = db.item_to_card(article)
        cleaned.update({"why": None, "kr_takeaway": None})
        result = db.article_quality_gate.GateResult(
            cleaned, "sanitize", ("why", "kr_takeaway"), ()
        )
        original = db.article_quality_gate.validate_final_card
        db.article_quality_gate.validate_final_card = lambda *args, **kwargs: result
        try:
            safe, cards, _ = db.verify_final_cards([article])
        finally:
            db.article_quality_gate.validate_final_card = original

        self.assertEqual(len(safe), 1)
        self.assertEqual(cards[0]["what"], "검증된 사실입니다.")
        self.assertEqual(article["why_important"], "")
        self.assertEqual(article["implication"], "")

    def test_incompatible_pending_outbox_is_replanned_instead_of_deadlocking(self):
        """구버전 claim 때문에 36시간 동안 새 계획까지 막히면 안 된다."""
        legacy = {
            "schema_version": 1,
            "date": datetime.now(db.KST).date().isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "briefs": [{"name": "국내", "text": "검증 전 문구", "keyboard": None,
                        "status": "pending"}],
            "items": [],
            "prune_hashes": [],
        }
        db.save_outbox(legacy)
        self.seed_queue(self._queue())

        self.assertEqual(db.cmd_plan(), 0)
        replacement = db.load_outbox()
        self.assertEqual(replacement["quality_gate_version"], db.QUALITY_GATE_VERSION)
        self.assertNotEqual(replacement["status"], "quality_rejected")
        self.assertNotIn("검증 전 문구", [row["text"] for row in replacement["briefs"]])

    def test_send_then_rerun_no_duplicates(self):
        self.seed_queue(self._queue())
        db.cmd_plan()
        self.assertEqual(db.cmd_send(), 0)
        n_first = len(fake_tg.sent_messages)
        self.assertGreater(n_first, 0)
        # 같은 날 재실행: plan 재사용 + send 는 sent 스킵 → 재발송 0
        self.assertEqual(db.cmd_plan(), 0)
        self.assertEqual(db.cmd_send(), 0)
        self.assertEqual(len(fake_tg.sent_messages), n_first)

    def test_claim_then_send_failure_marks_failed(self):
        """claim(plan) 후 텔레그램 발송 실패 → failed 기록, 재시도로 회복."""
        self.seed_queue(self._queue())
        db.cmd_plan()
        fake_tg.fail_next = True
        rc = db.cmd_send()
        self.assertEqual(rc, 1)
        outbox = db.load_outbox()
        statuses = [b["status"] for b in outbox["briefs"]]
        self.assertIn("failed", statuses)
        # 재실행 → failed 만 다시 발송
        n = len(fake_tg.sent_messages)
        self.assertEqual(db.cmd_send(), 0)
        self.assertGreater(len(fake_tg.sent_messages), n)
        self.assertEqual(db.load_outbox()["status"], "sent")

    def test_send_success_but_state_lost_recovered_by_result_file(self):
        """발송 성공 후 outbox 저장분 유실(git reset 모의) → outbox_result 로 복구."""
        self.seed_queue(self._queue())
        db.cmd_plan()
        claim_snapshot = db.OUTBOX_FILE.read_text(encoding="utf-8")  # push 된 claim 상태
        db.cmd_send()
        # git reset --hard 모의: outbox 가 claim(pending) 상태로 되돌아감
        db.OUTBOX_FILE.write_text(claim_snapshot, encoding="utf-8")
        self.assertEqual(db.cmd_confirm(), 0)
        self.assertEqual(db.load_outbox()["status"], "sent")  # 멱등 병합으로 복원

    def test_stale_outbox_not_resent(self):
        self.seed_queue(self._queue())
        db.cmd_plan()
        outbox = db.load_outbox()
        outbox["created_at"] = (NOW - timedelta(hours=48)).isoformat()
        db.save_outbox(outbox)
        db.send_outbox(outbox, now=NOW)
        self.assertEqual(fake_tg.sent_messages, [])
        self.assertTrue(all(b["status"] == "stale_skipped" for b in outbox["briefs"]))

    def test_yesterday_pending_blocks_new_plan(self):
        """직전 outbox 미발송(<36h) → 새 계획 생략 (재발송 우선)."""
        yesterday = {"schema_version": 1, "date": "2026-07-11",
                     "quality_gate_version": db.QUALITY_GATE_VERSION,
                     "created_at": (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat(),
                     "status": "pending",
                     "briefs": [{"name": "국내", "text": "x", "keyboard": None,
                                 "status": "pending"}],
                     "items": [], "prune_hashes": []}
        db._seal_quality_payload(yesterday)
        db.save_outbox(yesterday)
        self.seed_queue(self._queue())
        db.cmd_plan()
        self.assertEqual(db.load_outbox()["date"], "2026-07-11")  # 덮어쓰지 않음

    def _log_rows(self, record_type=None):
        rows = [json.loads(line) for line
                in db.DELIVERY_LOG_FILE.read_text(encoding="utf-8").splitlines()]
        return [r for r in rows if r.get("record_type") == record_type]

    def test_delivery_log_idempotent(self):
        self.seed_queue(self._queue())
        db.cmd_plan()
        db.cmd_send()
        self.assertEqual(db.cmd_confirm(), 0)
        n1 = len(self._log_rows())
        self.assertEqual(db.cmd_confirm(), 0)  # 두 번 confirm(재시도 모의)
        n2 = len(self._log_rows())
        self.assertEqual(n1, n2)
        rec = self._log_rows()[0]
        self.assertIn("breakdown", rec)  # 점수 내역이 남는다

    def test_selection_stats_appended_and_deduped_by_reader(self):
        """통계 레코드는 hash 가 없어 append 로 쌓인다 — 읽는 쪽이 하나를 고른다."""
        self.seed_queue(self._queue())
        db.cmd_plan()
        db.cmd_send()
        db.cmd_confirm()
        db.cmd_confirm()  # 재실행 → 통계 줄이 늘어난다(설계상 정상)
        stats = self._log_rows("selection_stats")
        self.assertEqual(len(stats), 2)
        for row in stats:
            self.assertIn("generated_at", row)
            self.assertEqual(row["pipeline_status"], "ok")
            self.assertIn("candidate_count", row["domestic"])
        # 기사 레코드는 통계에 오염되지 않는다
        for row in self._log_rows():
            self.assertNotIn("record_type", row)

    def test_old_queue_schema_loads_and_plans(self):
        """features/why_important 없는 기존 큐 JSON — 그대로 계획·발송 가능해야 함."""
        old_item = {"hash": "old1", "title": "Old article", "title_kr": "옛 기사",
                    "link": "https://world-nuclear-news.org/x", "domain": "world-nuclear-news.org",
                    "feed": "정책", "matched": "kw", "importance": "must_read",
                    "section": "international", "category": "정책", "summary": "요약",
                    "implication": "시사점", "watch_next": "", "tags": [],
                    "related_reports": [], "queued_at": datetime.now(timezone.utc).isoformat()}
        self.seed_queue([old_item])
        self.assertEqual(db.cmd_plan(), 0)
        outbox = db.load_outbox()
        self.assertEqual(outbox["status"], "pending")
        self.assertEqual(outbox["items"][0]["hash"], "old1")
        self.assertEqual(db.cmd_send(), 0)


class TestReportPickReachesTheWeb(OutboxBase):
    """보고서 검토 추천이 outbox 텍스트에서 끝나면 웹은 그걸 알 수 없다.

    outbox.json 은 매일 덮어쓰고, 웹 빌드는 나중에 따로 돈다. 추천을 화면까지
    옮기는 유일한 경로는 **커밋되는** delivery_log 의 기사 메타다
    (docs/2026-08-04-gap-review.md P1).
    """

    PICKED = "538c53ac9b1d2e4f"

    def _plan_with_recommendation(self):
        db.is_available = lambda: True
        orig_call = db.call_json
        # 추천 문구는 후보 기사에 실제로 있는 것만 말해야 한다 — 그렇지 않으면
        # verify_report_recs 가 뺀다. 픽스처도 그 계약을 따른다.
        db.call_json = lambda *a, **k: {"reports": [
            {"idx": 0, "topic": "한수원 체코 본계약의 국내 공급망 함의", "why": "w",
             "angles": ["기술 자립도", "수출 경쟁력"]}]}
        try:
            self.seed_queue([
                qitem(h=self.PICKED, section="khnp", domain="khnp.co.kr",
                      importance="must_read", title="한수원 체코 본계약"),
                qitem(h="f1", section="international", title="NRC approves NuScale design"),
            ])
            self.assertEqual(db.cmd_plan(), 0)
            return db.load_outbox()
        finally:
            db.call_json = orig_call

    def test_picked_article_carries_the_topic_and_others_stay_clean(self):
        outbox = self._plan_with_recommendation()
        self.assertEqual(outbox["briefs"][0]["name"], "보고서추천")
        picked = [item for item in outbox["items"] if item.get("report_pick")]
        self.assertEqual([item["hash"] for item in picked], [self.PICKED])
        self.assertEqual(picked[0]["report_pick"], "한수원 체코 본계약의 국내 공급망 함의")
        # 하루 0~2건짜리 표식이다. 나머지 전 줄에 빈 값이 붙으면 로그가 그만큼
        # 읽기 어려워진다 — 키 자체가 없어야 한다.
        others = [item for item in outbox["items"] if item["hash"] != self.PICKED]
        self.assertTrue(others)
        for item in others:
            self.assertNotIn("report_pick", item)

    def test_topic_survives_the_send_confirm_round_trip(self):
        """웹이 실제로 읽는 것은 outbox 가 아니라 delivery_log 다."""
        self._plan_with_recommendation()
        self.assertEqual(db.cmd_send(), 0)
        self.assertEqual(db.cmd_confirm(), 0)
        logged = [json.loads(line) for line
                  in db.DELIVERY_LOG_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
        picked = [row for row in logged if row.get("report_pick")]
        self.assertEqual([row["hash"] for row in picked], [self.PICKED])


class TestContinuityStats(unittest.TestCase):
    """연속일 판정은 두 번 돈다 — 통계가 둘을 섞으면 회차 비교가 끊긴다."""

    DIAG = {"candidate_count": 302, "dropped_below_floor": [],
            "dropped_repeat": [{"hash": "r1"}]}

    def test_pool_numbers_survive_the_recheck(self):
        cont = {
            "checked": 302, "matched": 76,
            "verdicts": [{"progression": "minor"}, {"progression": "none"}],
            "recheck": {
                "checked": 24, "matched": 3,
                "verdicts": [
                    {"progression": "none", "evidence_shared": 12,
                     "evidence_confirmed": True},
                    # 한 건 겹쳤을 뿐 문턱을 못 넘었다 — 판정에 아무 영향이 없다.
                    {"progression": "none", "evidence_shared": 1,
                     "evidence_confirmed": False},
                    {"progression": "material", "evidence_shared": 0,
                     "evidence_confirmed": False}],
            },
        }
        stats = db.region_stats(self.DIAG, [], [], cont)["continuity"]
        # 풀 전체 기준의 사실은 그대로 남는다.
        self.assertEqual((stats["checked"], stats["matched"]), (302, 76))
        self.assertEqual(stats["dropped"], 1)
        # 삭제를 실제로 정한 것은 접힌 뒤의 재판정이다.
        self.assertEqual(stats["recheck"]["checked"], 24)
        self.assertEqual(stats["recheck"]["by_progression"],
                         {"material": 1, "minor": 0, "none": 2})
        # 겹친 것은 둘, 문턱을 넘은 것은 하나. 둘을 한 숫자로 섞으면 게이트가
        # 실제보다 활발해 보이고, 문턱 조정의 근거가 사라진다.
        self.assertEqual(stats["recheck"]["evidence_confirmed"], 1)
        self.assertEqual(stats["recheck"]["evidence_overlapping"], 2)

    def test_missing_recheck_keeps_the_old_shape(self):
        cont = {"checked": 10, "matched": 1, "verdicts": []}
        stats = db.region_stats(self.DIAG, [], [], cont)["continuity"]
        self.assertNotIn("recheck", stats)


if __name__ == "__main__":
    unittest.main()
