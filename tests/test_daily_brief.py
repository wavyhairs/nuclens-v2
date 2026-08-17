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


class TestInvestment(unittest.TestCase):
    def test_weak_evidence_omitted(self):
        # confidence 0 / theme none / mechanism 없음 → 전부 생략
        self.assertIsNone(db.render_investment(None))
        self.assertIsNone(db.render_investment(db._sanitize_invest(
            {"theme": "none", "mechanism": "뭔가", "confidence": 2})))
        self.assertIsNone(db.render_investment(db._sanitize_invest(
            {"theme": "smr", "mechanism": "", "confidence": 2})))
        self.assertIsNone(db.render_investment(db._sanitize_invest(
            {"theme": "smr", "mechanism": "근거 약함", "confidence": 0})))

    def test_render_full(self):
        txt = db.render_investment(db._sanitize_invest({
            "theme": "grid_demand", "mechanism": "데이터센터 PPA로 재가동 원전의 장기 판매가가 고정된다",
            "beneficiary_type": "utility", "risk_side": "가스 피크발전",
            "time_horizon": "mid", "confidence": 2}))
        self.assertIn("발전사업자 수혜", txt)
        self.assertIn("가스 피크발전 부담", txt)
        self.assertIn("전력수요", txt)
        self.assertNotIn("확신 낮음", txt)

    def test_low_confidence_hedged(self):
        txt = db.render_investment(db._sanitize_invest({
            "theme": "uranium", "mechanism": "감산이 이어지면 현물가 상방",
            "beneficiary_type": "uranium_miner", "time_horizon": "near",
            "confidence": 1}))
        self.assertIn("확신 낮음", txt)

    def test_sanitize_bad_values(self):
        s = db._sanitize_invest({"theme": "meme_stocks", "mechanism": "x" * 500,
                                 "beneficiary_type": "tesla", "time_horizon": "tomorrow",
                                 "confidence": "high"})
        self.assertEqual(s["theme"], "none")
        self.assertEqual(s["beneficiary_type"], "none")
        self.assertEqual(s["time_horizon"], "mid")
        self.assertEqual(s["confidence"], 0)
        self.assertLessEqual(len(s["mechanism"]), 180)

    def test_enrich_gemini_error_returns_empty(self):
        orig_call, orig_avail = db.call_json, db.is_available
        db.is_available = lambda: True

        def boom(*a, **k):
            raise db.GeminiError("429 (모의)")
        db.call_json = boom
        try:
            self.assertEqual(db.enrich_investment([qitem()]), {})
        finally:
            db.call_json, db.is_available = orig_call, orig_avail


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
                      channel_queue.QUEUE_FILE)
        db.QUEUE_FILE = p / "digest_queue.json"
        db.OUTBOX_FILE = p / "outbox.json"
        db.OUTBOX_RESULT_FILE = p / "outbox_result.json"
        db.DELIVERY_LOG_FILE = p / "delivery_log.jsonl"
        ranking.DELIVERY_LOG_FILE = p / "delivery_log.jsonl"
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
            investment_struct={
                "theme": "smr",
                "mechanism": "근거 없는 투자 문장",
                "beneficiary_type": "none",
                "risk_side": "",
                "time_horizon": "mid",
                "confidence": 1,
            },
        )
        cleaned = db.item_to_card(article, "근거 없는 투자 문장")
        cleaned.update({"why": None, "investment": None, "kr_takeaway": None})
        result = db.article_quality_gate.GateResult(
            cleaned, "sanitize", ("why", "investment", "kr_takeaway"), ()
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
        self.assertIsNone(article["investment_struct"])

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


if __name__ == "__main__":
    unittest.main()
