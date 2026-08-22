"""weekly_bot / metrics / gemini_client 단위 테스트."""
import copy
import io
import json
import os
import sys
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import gemini_client
import metrics
import weekly_bot

NOW_ISO = "2026-07-12T22:00:00+00:00"


def _update(uid, data="fb:abcd1234:important", from_id=7):
    return {"update_id": uid,
            "callback_query": {"id": f"cq{uid}", "from": {"id": from_id},
                               "data": data}}


class TestWeekly(unittest.TestCase):
    def _curated(self, grade_field):
        now = datetime.now(timezone.utc).isoformat()
        return {
            "h1" * 8: {grade_field: "must_read", "title": "T1", "title_kr": "티1",
                       "link": "https://a.com/1", "domain": "world-nuclear-news.org",
                       "section": "international", "summary": "s", "tags": ["#SMR"],
                       "cached_at": now,
                       "features": {"event_type": "contract_award",
                                    "report_worthiness": 2}},
            "h2" * 8: {grade_field: "noise", "title": "T2", "link": "https://a.com/2",
                       "cached_at": now},
            "h3" * 8: {grade_field: "nice_to_know", "title": "T3", "link": "https://a.com/3",
                       "domain": "yna.co.kr", "section": "khnp", "cached_at": now,
                       "tags": ["#SMR", "#체코수주"]},
        }

    def test_regression_importance_field(self):
        """회귀 수정 검증: 현행 스키마(importance)에서 기사가 잡혀야 함 (기존 0건 버그)."""
        items = weekly_bot.get_week_articles(self._curated("importance"))
        self.assertEqual(len(items), 2)  # noise 제외

    def test_legacy_category_grade_schema(self):
        items = weekly_bot.get_week_articles(self._curated("category"))
        self.assertEqual(len(items), 2)

    def test_old_articles_excluded(self):
        c = self._curated("importance")
        for v in c.values():
            v["cached_at"] = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self.assertEqual(weekly_bot.get_week_articles(c), [])

    def test_actual_publication_time_wins_over_recent_cache_time(self):
        c = self._curated("importance")
        first = next(iter(c.values()))
        first["published_at"] = (
            datetime.now(timezone.utc) - timedelta(days=10)
        ).isoformat()
        items = weekly_bot.get_week_articles(c)
        self.assertNotIn(first["link"], {row["link"] for row in items})

    def test_recent_publication_is_not_dropped_by_old_cache_time(self):
        c = self._curated("importance")
        first = next(iter(c.values()))
        first["cached_at"] = (
            datetime.now(timezone.utc) - timedelta(days=10)
        ).isoformat()
        first["published_at"] = datetime.now(timezone.utc).isoformat()
        items = weekly_bot.get_week_articles(c)
        self.assertIn(first["link"], {row["link"] for row in items})

    def test_unverified_fallback_cannot_reenter_weekly_telegram(self):
        c = self._curated("importance")
        first = next(iter(c.values()))
        first["curation_status"] = "fallback"
        items = weekly_bot.get_week_articles(c)
        self.assertNotIn(first["link"], {row["link"] for row in items})

    def test_aggregates(self):
        items = weekly_bot.get_week_articles(self._curated("importance"))
        agg = weekly_bot.build_aggregates(items)
        self.assertEqual(agg["total"], 2)
        self.assertEqual(agg["must_read"], 1)
        self.assertEqual(agg["event_types"].get("contract_award"), 1)
        self.assertEqual(len(agg["report_candidates"]), 1)

    def test_synthesize_no_key_fallback(self):
        old = os.environ.pop("GEMINI_API_KEY", None)
        try:
            out = weekly_bot.batch_synthesize(
                weekly_bot.get_week_articles(self._curated("importance")), {})
            self.assertEqual(out["policy_shifts"], [])
        finally:
            if old:
                os.environ["GEMINI_API_KEY"] = old

    def test_format_weekly_is_landscape_not_relisting(self):
        """weekly 는 판세 구조 — key_events 5건 제한, 일일 카드 재나열 아님."""
        items = weekly_bot.get_week_articles(self._curated("importance"))
        orig = weekly_bot.batch_synthesize
        weekly_bot.batch_synthesize = lambda i, a: {
            "weekly_intro": "핵심 흐름", "khnp_direct": "직접 영향",
            "policy_shifts": [{"what": "정책A", "so_what": "함의A"}],
            "theme_moves": [{"theme": "SMR", "direction": "강화", "why": "근거"}],
            "watchpoints": ["다음주 포인트"],
            "report_candidates": [{"topic": "보고서감", "basis": "누적"}],
            "key_events": [{"hash": "h" * 8, "headline": f"E{i}", "implication": "x"}
                           for i in range(9)],  # 9건 줘도
        }
        try:
            msg = weekly_bot.format_weekly(items)
        finally:
            weekly_bot.batch_synthesize = orig
        self.assertIn("정책 변화", msg)
        self.assertIn("주제별 강약", msg)
        # 투자 프레이밍은 쓰지 않는다 — 웹과 같은 결정(2026-08-11).
        self.assertNotIn("투자 테마", msg)
        self.assertIn("▲", msg)
        shown = sum(1 for i in range(9) if f"E{i}" in msg)
        self.assertLessEqual(shown, 5)  # key_events 는 최대 5건으로 컷


class TestMetrics(unittest.TestCase):
    def test_insufficient_data(self):
        m = metrics.compute_metrics([], 30)
        self.assertEqual(m["source_diversity"], "insufficient_data")
        self.assertEqual(m["invest_omission_rate"], "insufficient_data")

    def test_computed_when_enough(self):
        delivered = [{"date": "2026-07-10", "hash": f"h{i:02d}" + "x" * 6,
                      "region": "해외", "domain": f"d{i}.com", "theme": "smr",
                      "section": "smr"} for i in range(20)]
        m = metrics.compute_metrics(delivered, 30)
        self.assertEqual(m["invest_omission_rate"], 0.0)
        self.assertIsInstance(m["source_diversity"], float)
        self.assertEqual(m["report_rec_count"], 0)


class TestGeminiSalvage(unittest.TestCase):
    def test_fenced(self):
        self.assertEqual(gemini_client._salvage_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_preamble(self):
        self.assertEqual(gemini_client._salvage_json('결과입니다: {"a": 1} 끝'), {"a": 1})

    def test_raw_newline_in_string(self):
        self.assertEqual(gemini_client._salvage_json('{"a": "줄\n바꿈"}'),
                         {"a": "줄 바꿈"})

    def test_hopeless_raises(self):
        with self.assertRaises(Exception):
            gemini_client._salvage_json("완전 깨진 응답")


class TestGeminiTruncationIsTyped(unittest.TestCase):
    """출력 예산 소진(MAX_TOKENS)은 일반 실패와 대응이 정반대다.

    회귀 방지 (2026-08-03): 2.5-flash 의 thinking 토큰이 maxOutputTokens 를 먹어
    chunk 가 통째로 날아가던 경로. 예전엔 두 모양 다 뭉뚱그린 GeminiError 라
    호출자가 '쪼개서 다시'와 '건드리지 말 것'을 구분할 수 없었다.
    """

    @staticmethod
    def _payload(parts, finish="MAX_TOKENS"):
        content = {"role": "model"}
        if parts is not None:
            content["parts"] = [{"text": parts}]
        return {
            "candidates": [{"content": content, "finishReason": finish}],
            "usageMetadata": {"thoughtsTokenCount": 8192,
                              "candidatesTokenCount": 0, "totalTokenCount": 11592},
        }

    def _drive(self, payload):
        """실제 call_json 을 HTTP 층만 갈아끼워 돌린다. 호출 횟수도 센다."""
        calls = []

        class _Resp(io.BytesIO):
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            return _Resp(json.dumps(payload).encode("utf-8"))

        with patch.object(gemini_client, "API_KEY", "test-key"), \
                patch.object(urllib.request, "urlopen", fake_urlopen), \
                patch.object(gemini_client.time, "sleep", lambda *a, **k: None):
            try:
                return gemini_client.call_json("sys", "user"), None, len(calls)
            except Exception as e:      # noqa: BLE001 — 타입까지 검사 대상
                return None, e, len(calls)

    def test_thinking_ate_budget_raises_truncated(self):
        """parts 자체가 없는 모양 — 생각만 하다 예산이 끝난 경우."""
        _, err, _ = self._drive(self._payload(None))
        self.assertIsInstance(err, gemini_client.GeminiTruncated)
        self.assertIn("MAX_TOKENS", str(err))
        self.assertIn("thoughts=8192", str(err))

    def test_truncated_json_raises_truncated_without_wasting_retries(self):
        """같은 예산으로 3번 더 불러봐야 같은 자리에서 잘린다 — 한도만 태운다."""
        _, err, n_calls = self._drive(self._payload('{"items": [{"idx": 0, "sum'))
        self.assertIsInstance(err, gemini_client.GeminiTruncated)
        self.assertEqual(n_calls, 1)

    def test_truncation_detail_stays_legible_when_log_truncates(self):
        """사유가 앞쪽에 있어야 로그가 잘려도 원인이 남는다."""
        _, err, _ = self._drive(self._payload(None))
        self.assertIn("MAX_TOKENS", str(err)[:80])

    def test_malformed_without_max_tokens_is_still_generic_error(self):
        """잘림이 아닌 구조 이상은 기존대로 — 잘못 분류하면 엉뚱하게 쪼갠다."""
        _, err, _ = self._drive({"candidates": [{"content": {"role": "model"}},
                                                ]})
        self.assertIsInstance(err, gemini_client.GeminiError)
        self.assertNotIsInstance(err, gemini_client.GeminiTruncated)

    def test_parseable_response_still_returns_even_if_max_tokens(self):
        """운 좋게 딱 맞게 끝났으면 통과시킨다 — 항목 결손은 호출자가 idx 로 잡는다."""
        out, err, _ = self._drive(self._payload('{"items": []}'))
        self.assertIsNone(err)
        self.assertEqual(out, {"items": []})


class TestWeeklyReportStore(unittest.TestCase):
    """주간 판세를 웹이 쓸 수 있게 저장한다. Gemini 호출은 늘지 않는다."""

    ITEMS = [{"hash": "aaaaaaaa1111", "title": "체코 두코바니 본계약"},
             {"hash": "bbbbbbbb2222", "title": "체코 두코바니 본계약"},
             {"hash": "cccccccc3333", "title": "미국 NRC 규정 개정"}]

    def _synthesis(self, intro="이번 주 흐름"):
        return {"weekly_intro": intro,
                "policy_shifts": [{"what": "변화", "so_what": "함의",
                                   "evidence_hashes": ["aaaaaaaa"]}],
                "theme_moves": [], "khnp_direct": "", "watchpoints": [],
                "report_candidates": [], "key_events": []}

    def test_kst_iso_week_boundary(self):
        """UTC 로 계산하면 주 경계가 엇갈린다.

        ISO 주차는 월요일에 넘어가므로 위험 구간은 KST 월요일 오전이다.
        KST 2027-01-04(월) 08:00 = UTC 2027-01-03(일) 23:00 → 2027-W01 vs 2026-W53.
        연말까지 걸쳐 있어 년·주 둘 다 어긋나는 최악의 사례다.
        두 값이 실제로 다른지 먼저 확인해 테스트가 우연히 통과하지 않게 한다.
        """
        kst_monday = datetime(2027, 1, 4, 8, 0, tzinfo=weekly_bot.KST)
        utc_year, utc_week, _ = kst_monday.astimezone(timezone.utc).isocalendar()
        self.assertEqual((utc_year, utc_week), (2026, 53))  # UTC 로 재면 전년 53주
        self.assertEqual(weekly_bot.week_id(kst_monday), "2027-W01")

    def test_saves_then_reports_no_change(self):
        """dirty 를 len(reports) 로 판정하면 덮어쓰기가 영영 저장 안 된다."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "weekly_reports.json"
            now = datetime(2026, 8, 3, 17, 0, tzinfo=weekly_bot.KST)
            agg = {"total": 3}
            self.assertTrue(weekly_bot.save_weekly_report(
                self._synthesis(), agg, self.ITEMS, now, path))
            # generated_at 은 매번 달라지므로 비교에서 빼야 한다
            later = now + timedelta(hours=2)
            self.assertFalse(weekly_bot.save_weekly_report(
                self._synthesis(), agg, self.ITEMS, later, path))

    def test_same_week_overwrite_is_detected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "weekly_reports.json"
            now = datetime(2026, 8, 3, 17, 0, tzinfo=weekly_bot.KST)
            weekly_bot.save_weekly_report(self._synthesis(), {"total": 3},
                                          self.ITEMS, now, path)
            self.assertTrue(weekly_bot.save_weekly_report(
                self._synthesis("내용이 바뀌었다"), {"total": 3},
                self.ITEMS, now, path))
            store = weekly_bot.load_weekly_reports(path)
            self.assertEqual(len(store["reports"]), 1)
            self.assertEqual(store["reports"]["2026-W32"]["weekly_intro"], "내용이 바뀌었다")

    def test_new_week_adds_an_entry(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "weekly_reports.json"
            weekly_bot.save_weekly_report(
                self._synthesis(), {"total": 3}, self.ITEMS,
                datetime(2026, 8, 3, 17, 0, tzinfo=weekly_bot.KST), path)
            weekly_bot.save_weekly_report(
                self._synthesis(), {"total": 3}, self.ITEMS,
                datetime(2026, 8, 10, 17, 0, tzinfo=weekly_bot.KST), path)
            self.assertEqual(sorted(weekly_bot.load_weekly_reports(path)["reports"]),
                             ["2026-W32", "2026-W33"])

    def test_source_issue_count_is_not_article_count(self):
        """기사 수를 쓰면 후속 보도가 많은 주가 실제보다 풍성해 보인다."""
        self.assertEqual(weekly_bot.count_unique_issues(self.ITEMS), 2)

    def test_same_event_in_different_wording_counts_once(self):
        """이 픽스처가 진짜 과제다 — 제목 완전일치는 상류가 이미 걷어낸다.

        옛 구현(제목 앞 40자)은 완전일치만 잡아 실질 no-op 였다. 매체마다 같은
        발표를 다르게 쓰는 쪽을 못 잡으면 '후속 보도가 많은 주 = 풍성한 주'
        착시가 그대로 남는다.
        """
        items = [
            {"hash": "aaaaaaaa1111", "title_kr": "한수원, 체코 두코바니 신규 원전 본계약 체결"},
            {"hash": "bbbbbbbb2222", "title_kr": "한수원 체코 두코바니 원전 본계약 체결 완료"},
            {"hash": "cccccccc3333", "title_kr": "미국 NRC, SMR 인허가 규정 개정안 의결"},
        ]
        self.assertEqual(weekly_bot.count_unique_issues(items), 2)

    def test_different_units_of_the_same_plant_stay_separate(self):
        """호기 충돌 거부권 — 서식만 같고 대상이 다른 쌍이 붙으면 안 된다."""
        items = [
            {"hash": "aaaaaaaa1111", "title_kr": "고리2호기 계속운전 심사 착수"},
            {"hash": "bbbbbbbb2222", "title_kr": "한빛1호기 계속운전 심사 결과 발표"},
        ]
        self.assertEqual(weekly_bot.count_unique_issues(items), 2)

    def test_counting_does_not_mutate_the_caller_items(self):
        """세는 행위가 curated 항목에 story_* 메타데이터를 남기면 안 된다."""
        items = [
            {"hash": "aaaaaaaa1111", "title_kr": "한수원, 체코 두코바니 신규 원전 본계약 체결"},
            {"hash": "bbbbbbbb2222", "title_kr": "한수원 체코 두코바니 원전 본계약 체결 완료"},
        ]
        before = copy.deepcopy(items)
        weekly_bot.count_unique_issues(items)
        self.assertEqual(items, before)

    def test_evidence_hashes_pruned_to_known_and_ordered(self):
        synthesis = {"policy_shifts": [{"what": "x", "evidence_hashes": [
            "cccccccc", "deadbeef", "aaaaaaaa", "cccccccc"]}],
            "theme_moves": []}
        weekly_bot.prune_evidence_hashes(synthesis, self.ITEMS)
        # 지어낸 hash 는 화면에서 죽은 칩이 되므로 잘라낸다. 순서는 보존한다
        # (set 으로 걸러 내면 실행마다 순서가 달라져 dirty 가 항상 참이 된다).
        self.assertEqual(synthesis["policy_shifts"][0]["evidence_hashes"],
                         ["cccccccc", "aaaaaaaa"])

    def test_prune_is_deterministic(self):
        first, second = [], []
        for sink in (first, second):
            synthesis = {"policy_shifts": [{"what": "x", "evidence_hashes": [
                "cccccccc", "bbbbbbbb", "aaaaaaaa"]}], "theme_moves": []}
            weekly_bot.prune_evidence_hashes(synthesis, self.ITEMS)
            sink.extend(synthesis["policy_shifts"][0]["evidence_hashes"])
        self.assertEqual(first, second)

    def test_corrupt_store_falls_back_to_empty(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "weekly_reports.json"
            path.write_text("{ 깨진", encoding="utf-8")
            self.assertEqual(weekly_bot.load_weekly_reports(path)["reports"], {})

    def test_format_weekly_accepts_precomputed_synthesis(self):
        """합성을 한 번만 돌려 텔레그램과 웹이 같은 결과를 쓴다 (호출 +0)."""
        items = [{"hash": "aaaaaaaa1111", "title": "T", "title_kr": "제목",
                  "link": "https://example.com/a", "domain": "example.com",
                  "feed": "", "section": "international", "grade": "must_read",
                  "summary": "요약", "tags": [], "features": None,
                  "cached_at": datetime.now(timezone.utc).isoformat()}]
        text = weekly_bot.format_weekly(items, self._synthesis("고정 문구"))
        self.assertIn("고정 문구", text)


class TestWeeklySentenceEvidence(unittest.TestCase):
    """근거 hash 가 진짜라는 것과 그 기사가 그 문장을 뒷받침한다는 것은 다르다."""

    DOOSAN = {
        "hash": "d0000000aaaa", "title": "Doosan signs 345 MW Natrium SMR contract",
        "title_kr": "두산에너빌리티, 테라파워 345MW SMR 기자재 계약 체결",
        "link": "https://example.com/a", "domain": "example.com", "feed": "",
        "section": "international", "grade": "must_read",
        "summary": "두산에너빌리티가 미국 테라파워의 345MW급 SMR 기자재를 공급한다.",
        "tags": [], "features": None, "cached_at": NOW_ISO,
    }
    KHNP = {
        "hash": "k0000000bbbb", "title": "KHNP wins Czech Dukovany contract",
        "title_kr": "한국수력원자력, 체코 두코바니 원전 건설 계약 수주",
        "link": "https://example.com/b", "domain": "example.com", "feed": "",
        "section": "khnp", "grade": "must_read",
        "summary": "한국수력원자력이 체코 두코바니 신규 원전 2기 건설 계약을 따냈다.",
        "tags": [], "features": None, "cached_at": NOW_ISO,
    }

    def setUp(self):
        self.items = [self.DOOSAN, self.KHNP]

    def verify(self, **synthesis):
        base = {"weekly_intro": "", "policy_shifts": [], "theme_moves": [],
                "khnp_direct": "", "watchpoints": [], "report_candidates": [],
                "key_events": []}
        return weekly_bot.verify_synthesis({**base, **synthesis}, self.items)

    def test_faithful_item_survives(self):
        out = self.verify(policy_shifts=[{
            "what": "한국수력원자력이 체코 두코바니 원전 건설 계약을 수주했다.",
            "so_what": "유럽 신규 건설 시장의 진입 사례가 됐다.",
            "evidence_hashes": ["k0000000"]}])
        self.assertEqual(len(out["policy_shifts"]), 1)

    def test_valid_hash_with_a_different_story_is_dropped(self):
        """hash 는 이번 주 기사인데 문장은 그 기사와 다른 사건인 경우."""
        out = self.verify(policy_shifts=[{
            "what": "한국수력원자력이 로사톰과 우라늄 농축 계약을 체결했다.",
            "so_what": "공급망 구조가 바뀐다.",
            "evidence_hashes": ["k0000000"]}])
        self.assertEqual(out["policy_shifts"], [])

    def test_one_cited_article_cannot_carry_another_articles_number(self):
        """여러 기사 중 하나만 지목하면서 다른 기사의 수치를 끼워 넣는 경우."""
        out = self.verify(theme_moves=[{
            "theme": "신규건설", "direction": "강화",
            "why": "한국수력원자력의 체코 두코바니 계약은 345MW 규모다.",
            "evidence_hashes": ["k0000000"]}])
        self.assertEqual(out["theme_moves"], [])

    def test_one_cited_article_cannot_carry_another_institution(self):
        out = self.verify(theme_moves=[{
            "theme": "수출", "direction": "강화",
            "why": "두산에너빌리티도 같은 사업에 기자재를 공급하기로 했다.",
            "evidence_hashes": ["k0000000"]}])
        self.assertEqual(out["theme_moves"], [])

    def test_concrete_item_without_any_evidence_is_dropped(self):
        out = self.verify(theme_moves=[{
            "theme": "핵융합", "direction": "강화",
            "why": "독일이 2040년대 상업로 목표를 발표했다.",
            "evidence_hashes": []}])
        self.assertEqual(out["theme_moves"], [])

    def test_one_bad_item_does_not_take_the_whole_report_down(self):
        """항목 하나가 틀렸다고 그 주 판세 보고를 통째로 버리지 않는다."""
        out = self.verify(
            weekly_intro="이번 주는 신규 건설 계약이 이어졌다.",
            policy_shifts=[
                {"what": "한국수력원자력이 체코 두코바니 계약을 수주했다.",
                 "so_what": "유럽 진입 사례다.", "evidence_hashes": ["k0000000"]},
                {"what": "로사톰이 카자흐스탄 신규 원전을 착공했다.",
                 "so_what": "경쟁 구도가 바뀐다.", "evidence_hashes": ["k0000000"]},
            ],
            key_events=[{"hash": "d0000000",
                         "headline": "두산에너빌리티, 테라파워 345MW SMR 기자재 계약 체결",
                         "implication": "국내 공급망의 수주 사례다."}])
        self.assertEqual(len(out["policy_shifts"]), 1)
        self.assertEqual(len(out["key_events"]), 1)
        self.assertTrue(out["weekly_intro"])

    def test_llm_sentences_are_not_evidence_for_each_other(self):
        """다른 항목이 같은 이름을 썼다고 근거가 되지는 않는다."""
        out = self.verify(
            weekly_intro="로사톰이 이번 주 최대 수주자였다.",
            policy_shifts=[{"what": "로사톰이 신규 원전을 수주했다.",
                            "so_what": "시장이 재편된다.",
                            "evidence_hashes": ["k0000000"]}])
        self.assertEqual(out["policy_shifts"], [])
        self.assertEqual(out["weekly_intro"], "")

    def test_watchpoints_survive_without_per_item_evidence(self):
        """다음 주 관찰 포인트는 사건이 아직 없다 — 근거 기사를 요구하지 않는다."""
        out = self.verify(watchpoints=["체코 두코바니 후속 일정 확인",
                                       "로사톰 카자흐스탄 착공 여부"])
        self.assertEqual(out["watchpoints"], ["체코 두코바니 후속 일정 확인"])

    def test_verification_survives_the_final_format_conversion(self):
        """검증에서 뺀 문장이 텔레그램 렌더링에서 되살아나면 안 된다."""
        out = self.verify(policy_shifts=[
            {"what": "한국수력원자력이 체코 두코바니 계약을 수주했다.",
             "so_what": "유럽 진입 사례다.", "evidence_hashes": ["k0000000"]},
            {"what": "로사톰이 카자흐스탄 신규 원전을 착공했다.",
             "so_what": "경쟁 구도가 바뀐다.", "evidence_hashes": ["k0000000"]},
        ])
        text = weekly_bot.format_weekly(self.items, out)
        self.assertIn("두코바니", text)
        self.assertNotIn("로사톰", text)
        self.assertNotIn("카자흐스탄", text)

    def test_saved_web_report_keeps_the_verified_synthesis(self):
        out = self.verify(policy_shifts=[
            {"what": "로사톰이 카자흐스탄 신규 원전을 착공했다.",
             "so_what": "경쟁 구도가 바뀐다.", "evidence_hashes": ["k0000000"]}])
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "weekly_reports.json"
            weekly_bot.save_weekly_report(
                out, weekly_bot.build_aggregates(self.items), self.items, path=path)
            saved = json.loads(path.read_text(encoding="utf-8"))
        entry = next(iter(saved["reports"].values()))
        self.assertEqual(entry["policy_shifts"], [])

    def test_published_at_window_still_wins_over_cache_time(self):
        """PR #27 의 published_at 기반 주간 창 계산은 그대로 남아 있어야 한다."""
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        curated = {"h" * 16: {"importance": "must_read", "title": "T", "link": "u",
                              "cached_at": datetime.now(timezone.utc).isoformat(),
                              "published_at": old}}
        self.assertEqual(weekly_bot.get_week_articles(curated), [])


class TestWeeklyStoryEvidence(unittest.TestCase):
    """주간의 분석 단위는 기사가 아니라 사건이다.

    같은 발표를 여러 매체가 나눠 쓰면 수치는 A 매체에, 기관명은 B 매체에 있다.
    기사 하나로 좁혀 대조하면 그 주에 분명히 있었던 사실이 '근거 없음'이 되고,
    반대로 사건 수를 세지 않으면 한 발표의 반복 보도가 '강한 흐름'이 된다.
    """

    @staticmethod
    def _article(hash_prefix, title_kr, summary, *, domain="a.com",
                 grade="must_read", section="smr"):
        return {"hash": f"{hash_prefix}{'0' * (12 - len(hash_prefix))}",
                "title": title_kr, "title_kr": title_kr, "link": f"https://{domain}/x",
                "domain": domain, "feed": "", "section": section, "grade": grade,
                "summary": summary, "tags": [], "features": None,
                "curation_status": "reviewed", "cached_at": NOW_ISO,
                "published_at": NOW_ISO}

    def _verify(self, items, **synthesis):
        base = {"weekly_intro": "", "policy_shifts": [], "theme_moves": [],
                "khnp_direct": "", "watchpoints": [], "report_candidates": [],
                "key_events": []}
        return weekly_bot.verify_synthesis({**base, **synthesis}, items)

    # ---- Case 1 · 6 — 서로 다른 사건이 같은 방향이면 종합 판단은 살아야 한다 ----

    SMR_WEEK = None

    def _smr_week(self):
        return [
            self._article("aa1", "정부, 혁신형 SMR 국가전략기술 지정 고시",
                          "정부가 혁신형 SMR 을 국가전략기술로 지정하는 고시를 시행했다.",
                          domain="policy.com"),
            self._article("bb2", "두산에너빌리티, 뉴스케일 SMR 주기기 공급계약 체결",
                          "두산에너빌리티가 뉴스케일파워와 SMR 주기기 공급계약을 체결했다.",
                          domain="ind.com"),
            self._article("cc3", "SK, 테라파워 SMR 사업 지분 투자 결정",
                          "SK 가 테라파워의 SMR 사업에 지분을 투자하기로 결정했다.",
                          domain="fin.com"),
            self._article("dd4", "원안위, 혁신형 SMR 표준설계 인가 심사 착수",
                          "원자력안전위원회가 혁신형 SMR 표준설계 인가 심사에 착수했다.",
                          domain="reg.com"),
        ]

    def test_four_independent_stories_keep_a_strengthening_theme(self):
        """Case 1 — 정책·투자·공급계약·인허가가 각각 다른 사건이면 'SMR 강화'는 정상."""
        items = self._smr_week()
        out = self._verify(items, theme_moves=[{
            "theme": "SMR", "direction": "강화",
            "why": "서로 다른 네 갈래에서 같은 방향의 진전이 이어졌다.",
            "evidence_hashes": [a["hash"][:8] for a in items]}])
        self.assertEqual(len(out["theme_moves"]), 1)

    def test_synthesis_no_single_article_states_is_kept(self):
        """Case 6 — 어느 기사에도 '테마가 강화됐다'는 문장은 없다. 그래도 종합은 판단이다."""
        items = self._smr_week()
        out = self._verify(items, theme_moves=[{
            "theme": "SMR", "direction": "강화",
            "why": "한 주 동안 축적된 사건들이 사업화 단계로의 이동을 가리킨다.",
            "evidence_hashes": [items[0]["hash"][:8], items[1]["hash"][:8],
                                items[2]["hash"][:8]]}])
        self.assertEqual(len(out["theme_moves"]), 1)

    # ---- Case 2 — 같은 사건의 반복 보도는 근거 1건 ----

    def test_one_event_reported_by_eight_outlets_is_not_a_strengthening(self):
        """Case 2 — 기사 8건이라는 이유만으로 '강화'가 되면 안 된다."""
        wordings = [
            "두산에너빌리티, 뉴스케일 SMR 주기기 공급계약 체결",
            "두산에너빌리티 뉴스케일 SMR 주기기 공급계약 체결",
            "두산에너빌리티, 뉴스케일과 SMR 주기기 공급계약 체결",
            "두산에너빌리티, 뉴스케일 SMR 주기기 공급계약을 체결",
            "두산에너빌리티, 뉴스케일 SMR 주기기 공급 계약 체결",
            "두산에너빌리티, 뉴스케일 SMR 주기기 공급계약 체결했다",
            "두산에너빌리티, 뉴스케일 SMR 주기기 공급계약 체결 발표",
            "두산에너빌리티, 뉴스케일 SMR 주기기 공급계약 체결 소식",
        ]
        items = [
            self._article(f"e{i}", text,
                          "두산에너빌리티가 뉴스케일파워와 SMR 주기기 공급계약을 체결했다.",
                          domain=f"news{i}.com")
            for i, text in enumerate(wordings)
        ]
        self.assertEqual(len(weekly_bot.weekly_stories(items)), 1,
                         "같은 발표의 반복 보도는 사건 하나로 묶여야 한다")
        out = self._verify(items, theme_moves=[{
            "theme": "SMR", "direction": "강화",
            "why": "공급계약 체결로 사업이 진전됐다.",
            "evidence_hashes": [a["hash"][:8] for a in items[:5]]}])
        self.assertEqual(out["theme_moves"], [])

    # ---- Case 3 — 실제 진전 없이 전망 기사만 ----

    def test_outlook_only_coverage_cannot_strengthen_a_theme(self):
        """Case 3 — 전망·필요성 기사만 모아 놓고 '강화'라고 쓸 수 없다."""
        items = [
            self._article("f1", "SMR 시장, 2035년까지 확대될 전망",
                          "SMR 시장이 2035년까지 확대될 것으로 전망된다.",
                          domain="v1.com"),
            self._article("f2", "전문가 \"SMR 경쟁력 확보 필요\"",
                          "전문가들은 SMR 경쟁력 확보가 필요하다고 본다.",
                          domain="v2.com"),
        ]
        out = self._verify(items, theme_moves=[{
            "theme": "SMR", "direction": "강화",
            "why": "시장 기대가 커지고 있다.",
            "evidence_hashes": [a["hash"][:8] for a in items]}])
        self.assertEqual(out["theme_moves"], [])

    def test_the_same_outlook_week_can_still_be_reported_as_holding(self):
        """'유지'는 방향을 주장하지 않으므로 같은 근거에서 살아남는다."""
        items = [
            self._article("f1", "SMR 시장, 2035년까지 확대될 전망",
                          "SMR 시장이 2035년까지 확대될 것으로 전망된다.",
                          domain="v1.com"),
            self._article("f2", "전문가 \"SMR 경쟁력 확보 필요\"",
                          "전문가들은 SMR 경쟁력 확보가 필요하다고 본다.",
                          domain="v2.com"),
        ]
        out = self._verify(items, theme_moves=[{
            "theme": "SMR", "direction": "유지",
            "why": "새 사건 없이 논의만 이어졌다.",
            "evidence_hashes": [a["hash"][:8] for a in items]}])
        self.assertEqual(len(out["theme_moves"]), 1)

    # ---- Case 4 · 5 — 수치와 사건 단계는 사건 단위로도 그대로 막는다 ----

    def test_a_number_the_evidence_does_not_carry_is_blocked(self):
        """Case 4 — 근거는 4기인데 문장은 8기."""
        items = [self._article("g1", "정부, 신규 원전 4기 건설 계획 확정",
                               "정부가 신규 원전 4기 건설 계획을 확정했다.",
                               domain="p.com", section="domestic")]
        out = self._verify(items, policy_shifts=[{
            "what": "정부가 신규 원전 8기 건설 계획을 확정했다.",
            "so_what": "설비 계획이 크게 늘어난다.",
            "evidence_hashes": [items[0]["hash"][:8]]}])
        self.assertEqual(out["policy_shifts"], [])

    def test_an_event_stage_the_evidence_does_not_carry_is_blocked(self):
        """Case 5 — 근거는 심사 착수인데 문장은 최종 승인."""
        items = [self._article("h1", "원안위, 고리 2호기 계속운전 심사 착수",
                               "원자력안전위원회가 고리 2호기 계속운전 심사에 착수했다.",
                               domain="p.com", section="domestic")]
        out = self._verify(items, policy_shifts=[{
            "what": "원자력안전위원회가 고리 2호기 계속운전을 최종 승인했다.",
            "so_what": "계속운전 절차가 마무리됐다.",
            "evidence_hashes": [items[0]["hash"][:8]]}])
        self.assertEqual(out["policy_shifts"], [])

    # ---- Case 7 — 주제와 무관한 근거 ----

    def test_evidence_about_another_topic_cannot_carry_a_theme(self):
        """Case 7 — SMR 테마인데 근거가 전력망 기사."""
        items = [
            self._article("i1", "국가기간 전력망 확충 특별법 국회 통과",
                          "국가기간 전력망 확충 특별법이 국회 본회의를 통과했다.",
                          domain="g1.com", section="domestic"),
            self._article("i2", "민간 송전망 건설 참여 허용",
                          "송전망 건설에 민간 참여가 허용된다.",
                          domain="g2.com", section="domestic"),
        ]
        out = self._verify(items, theme_moves=[{
            "theme": "SMR", "direction": "강화",
            "why": "제도적 동력이 확보됐다.",
            "evidence_hashes": [a["hash"][:8] for a in items]}])
        self.assertEqual(out["theme_moves"], [])

    # ---- 사건 단위 근거가 실제로 무엇을 되살리는가 ----

    def test_a_fact_split_across_outlets_of_one_event_is_supported(self):
        """한 발표를 두 매체가 나눠 쓰면 수치는 한쪽에만 있다.

        회귀 (2026-08-21 실측): '원전 19기 분량'이 같은 주 6개 기사에 있는데도,
        지목한 기사 하나에 그 수치가 없다는 이유로 정책 변화가 통째로 삭제됐다.
        """
        items = [
            self._article("j1", "제12차 전기본, 2040년 최대전력 상향",
                          "정부가 2040년 최대전력 전망치를 상향했다.",
                          domain="k1.com", section="domestic"),
            self._article("j2", "제12차 전기본 2040년 최대전력 상향 조정",
                          "상향분은 대형 원전 19기 규모에 해당한다.",
                          domain="k2.com", section="domestic"),
        ]
        self.assertEqual(len(weekly_bot.weekly_stories(items)), 1)
        out = self._verify(items, policy_shifts=[{
            "what": "정부가 2040년 최대전력 전망치를 원전 19기 규모만큼 상향했다.",
            "so_what": "신규 설비 논의가 불가피해진다.",
            "evidence_hashes": [items[0]["hash"][:8]]}])
        self.assertEqual(len(out["policy_shifts"]), 1)

    def test_a_number_no_outlet_of_the_event_carries_is_still_blocked(self):
        """사건으로 넓힌 것이 '아무 수치나 통과'가 되면 안 된다."""
        items = [
            self._article("j1", "제12차 전기본, 2040년 최대전력 상향",
                          "정부가 2040년 최대전력 전망치를 상향했다.",
                          domain="k1.com", section="domestic"),
            self._article("j2", "제12차 전기본 2040년 최대전력 상향 조정",
                          "상향분은 대형 원전 19기 규모에 해당한다.",
                          domain="k2.com", section="domestic"),
        ]
        out = self._verify(items, policy_shifts=[{
            "what": "정부가 2040년 최대전력 전망치를 원전 40기 규모만큼 상향했다.",
            "so_what": "신규 설비 논의가 불가피해진다.",
            "evidence_hashes": [items[0]["hash"][:8]]}])
        self.assertEqual(out["policy_shifts"], [])

    # ---- '진전' 어휘 — 두 번 난 공백이라 세 번째는 테스트가 막는다 ----

    def test_administrative_progress_counts_as_a_development(self):
        """제도·계획이 실제로 움직인 표시는 계약·수주만이 아니다.

        회귀: 2026-08-21 "원자력안전법 개정안 국회 통과", 2026-08-22
        "제12차 전기본 2040년 최대전력 165GW로 상향" 이 각각 '진전'으로 세어지지
        않아 근거 있는 강약 판단이 조용히 삭제됐다.
        """
        for text in (
            "원자력안전법 개정안이 국회 본회의를 통과했다.",
            "정부가 2040년 최대전력 전망치를 165GW로 상향했다.",
            "2040년 최대 전력수요가 165GW로 상향 조정되었다.",
            "제12차 전력수급기본계획 전망치를 재산정했다.",
            "목표 전망치를 하향했다.",
        ):
            with self.subTest(text=text):
                self.assertTrue(weekly_bot.is_development(text))

    def test_an_intended_or_planned_step_is_not_a_development(self):
        """넓힌 어휘가 '아직 안 한 일'을 사건으로 만들면 안 된다.

        W34 실측에서 나온 반례들이다 — 여기 걸리는 표현 때문에 수립·공개·개정·
        개편은 아예 넣지 않았고, 남긴 세 낱말도 꼬리 검사로 한 번 더 거른다.
        """
        for text in (
            "정부가 전망치를 상향할 예정이다.",
            "수요 전망을 재산정하겠다고 밝혔다.",
            "국회 통과 가능성이 거론된다.",
            "전망치 상향이 검토되고 있다.",
            "연내 전망치 상향 계획을 세웠다.",
            "SMR 시장이 2035년까지 확대될 전망이다.",
            "국가 차원의 조달 전략 재수립이 요구된다.",
            "제12차 전력수급기본계획 수립을 위한 정책토론회를 연다.",
            "기후부가 산업용 지역요금제를 내주 공개할 방침이다.",
            "포항시가 원자력 특별회계 조례 재개정을 추진한다.",
        ):
            with self.subTest(text=text):
                self.assertFalse(weekly_bot.is_development(text))

    def test_a_completed_act_is_not_laundered_by_a_later_hedge(self):
        """기존 안전장치 유지 — 문장 뒤쪽 전망 한 마디가 앞의 사실을 세탁하지 못한다."""
        self.assertTrue(weekly_bot.is_development(
            "전력망 3법이 국회를 통과해 민간 참여가 늘어날 전망이다."))
        self.assertTrue(weekly_bot.is_development(
            "정부가 전망치를 상향했으나 추가 조정 가능성도 있다."))

    # ---- 입력·집계 ----

    def test_weekly_input_collapses_repeat_coverage_but_keeps_the_titles(self):
        """대표 하나만 남기고 버리지 않는다 — 접힌 보도의 제목은 입력에 남는다."""
        items = [
            self._article("m1", "두산에너빌리티, 뉴스케일 SMR 주기기 공급계약 체결",
                          "공급계약을 체결했다.", domain="n1.com"),
            self._article("m2", "두산에너빌리티 뉴스케일 SMR 주기기 공급계약 체결",
                          "공급계약을 체결했다.", domain="n2.com"),
        ]
        stories = weekly_bot.weekly_stories(items)
        self.assertEqual(len(stories), 1)
        lines = "\n".join(weekly_bot.story_lines(stories))
        self.assertIn("같은 사건 보도 2건", lines)
        self.assertIn("매체 2곳", lines)

    def test_grouping_does_not_mutate_the_caller_items(self):
        items = self._smr_week()
        before = copy.deepcopy(items)
        weekly_bot.weekly_stories(items)
        weekly_bot.weekly_contracts(items)
        self.assertEqual(items, before)

    def test_theme_signals_count_events_not_articles(self):
        """반복 보도가 주제 신호를 부풀리면 프롬프트가 잘못된 축을 권한다."""
        items = [
            self._article(f"p{i}", f"두산에너빌리티, 뉴스케일 SMR 주기기 공급계약 체결{'.' * i}",
                          "두산에너빌리티가 뉴스케일파워와 SMR 주기기 공급계약을 체결했다.",
                          domain=f"q{i}.com")
            for i in range(6)
        ] + [
            self._article("r1", "원안위, 혁신형 SMR 표준설계 인가 심사 착수",
                          "원자력안전위원회가 혁신형 SMR 표준설계 인가 심사에 착수했다.",
                          domain="reg.com"),
        ]
        signals = {row["theme"]: row for row in
                   weekly_bot.weekly_theme_signals(weekly_bot.weekly_stories(items))}
        self.assertEqual(signals["SMR"]["사건"], 2)

    def test_evidence_hashes_keep_more_than_two_events(self):
        """한 판세 문장이 여러 독립 사건을 종합하는 것이 주간의 일이다."""
        items = self._smr_week()
        synthesis = {"policy_shifts": [], "theme_moves": [{
            "theme": "SMR", "direction": "강화", "why": "진전이 이어졌다.",
            "evidence_hashes": [a["hash"][:8] for a in items]}]}
        weekly_bot.prune_evidence_hashes(synthesis, items)
        self.assertEqual(len(synthesis["theme_moves"][0]["evidence_hashes"]), 4)


class TestWeeklyWorkflow(unittest.TestCase):
    def test_workflow_can_commit_and_rebases_on_conflict(self):
        root = Path(__file__).parent.parent
        yml = (root / ".github" / "workflows" / "weekly.yml").read_text(encoding="utf-8")
        self.assertIn("contents: write", yml)
        # 파일별 가드 — 없는 파일 하나가 스텝 전체를 죽이면 안 된다
        self.assertIn("[ -f weekly_reports.json ]", yml)
        # 단순 push 반복은 다른 워크플로가 먼저 커밋했으면 3번 다 실패한다
        self.assertIn("git rebase origin/main", yml)


if __name__ == "__main__":
    unittest.main()
