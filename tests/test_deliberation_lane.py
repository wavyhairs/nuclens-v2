"""신규원전 공론화 레인 — 판정 모듈(deliberation_lane)과 수집 회차(deliberation_pipeline).

이 레인의 규칙은 해외 AI 레인과 **반대 방향**이 셋 있다. 테스트로 잠근다.
  1. 확신이 없으면 포함(검토 표시). AI 레인은 제외.
  2. 접힌 기사(raw_sources)까지 받는다 — 대표가 포함이면 호출 없이 판정을 상속한다.
  3. 판정 한도로 밀린 후보는 버리지 않고 이월한다.
"""
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import deliberation_lane as lane  # noqa: E402
import deliberation_pipeline as pipe  # noqa: E402

NOW = datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc)
_HEAD = re.compile(r"^\[(\d+)\] (.*)$")


def main_record(digest, title, *, summary="", publisher="연합뉴스", archived_at=None):
    return {"hash": digest, "title": title, "title_kr": title, "summary": summary, "importance": "nice_to_know",
            "scope": "kr", "url": f"https://news.test/{digest}", "domain": "yna.co.kr", "publisher": publisher,
            "archived_at": archived_at or NOW.isoformat(), "pub": "2026-10-15T01:00:00+00:00",
            "article_type": "news"}


def fake_judge(in_scope=("공론화",), *, key="원전 공론화 의제 확정", calls=None, fail=False):
    """제목에 in_scope 단어가 있으면 포함, 아니면 고준위 제외. calls 에 받은 제목을 적는다."""
    def call(system, user):
        if fail:
            raise RuntimeError("503")
        items = []
        for block in user.split("\n\n"):
            m = _HEAD.match(block.split("\n", 1)[0])
            if not m:
                continue
            idx, title = int(m.group(1)), m.group(2)
            if calls is not None:
                calls.append(title)
            if any(word in title for word in in_scope):
                items.append({"idx": idx, "in_scope": True, "exclude_reason": "none", "stage": "agenda_design",
                              "actor_type": "committee", "actor_names": ["원전 공론화위원회"], "stance": "neutral",
                              "article_type": "report", "significance": 3, "event_key": key,
                              "event_date": "2026-10-14", "confidence": "high", "reason": "r",
                              "title_kr": "", "summary": "모델 요약", "why_important": "왜", "implication": "시사"})
            else:
                items.append({"idx": idx, "in_scope": False, "exclude_reason": "hlw_deliberation"})
        return {"items": items}
    return call


class PrefilterTests(unittest.TestCase):
    def test_needs_both_deliberation_and_nuclear_terms(self):
        self.assertTrue(lane.prefilter({"title": "정부, 원전 공론화 의제 확정"})["passed"])
        self.assertTrue(lane.prefilter({"title": "시민참여단 300명 구성", "summary": "전력수급기본계획 숙의"})["passed"])
        # '공론화' 단독은 올리지 않는다 — 교정시설·공항·약국 공론화가 섞인다(0단계 실측).
        self.assertFalse(lane.prefilter({"title": "영덕군, 교정시설 유치 공론화 착수"})["passed"])
        self.assertFalse(lane.prefilter({"title": "원전 계속운전 심사 착수"})["passed"])

    def test_naver_description_counts(self):
        self.assertTrue(lane.prefilter({"title": "영덕군 입장 발표", "description": "신규 원전 <b>공론화</b> 결과"})["passed"])

    def test_homonym_is_not_nuclear(self):
        self.assertFalse(lane.prefilter({"title": "기원전 그리스 민회의 숙의 전통"})["passed"])

    def test_hlw_is_a_hint_not_a_drop(self):
        # 0단계: 고준위 표시로 결정적 제외를 하면 포함 기사 2건을 잃었다. 판정이 가른다.
        verdict = lane.prefilter({"title": "원전 경제성 재평가…방폐장 비용 반영", "summary": "공론화 거쳐 전기본 반영"})
        self.assertTrue(verdict["passed"])
        self.assertIn("hlw", verdict["hints"])

    def test_gold_has_no_prefilter_miss(self):
        gold = [json.loads(line) for line in (ROOT / "eval_artifacts/deliberation/gold.jsonl")
                .read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(gold), 174)
        self.assertEqual(sum(1 for g in gold if g["in_scope"]), 140)
        self.assertTrue(all(g.get("event_key") for g in gold if g["in_scope"]))

    def test_editorial_title(self):
        self.assertTrue(lane.is_editorial_title("[사설] 원전 공론화, 숫자 놀음 안 된다"))
        self.assertTrue(lane.is_editorial_title("【기고】 숙의의 조건"))
        self.assertFalse(lane.is_editorial_title("정부, 원전 공론화 의제 확정"))


class JudgmentTests(unittest.TestCase):
    def test_conflicting_fields_become_low_confidence_inclusion(self):
        # AI 레인과 반대: 두 칸이 엇갈리면 포함·검토.
        both = lane.normalize_judgment({"in_scope": True, "exclude_reason": "background_mention"})
        self.assertTrue(both["in_scope"])
        self.assertEqual(both["confidence"], "low")
        silent = lane.normalize_judgment({"in_scope": False, "exclude_reason": "none"})
        self.assertTrue(silent["in_scope"])
        self.assertEqual(silent["confidence"], "low")

    def test_clear_exclusion_stays_excluded(self):
        verdict = lane.normalize_judgment({"in_scope": False, "exclude_reason": "hlw_deliberation"})
        self.assertFalse(verdict["in_scope"])
        self.assertEqual(verdict["exclude_reason"], "hlw_deliberation")

    def test_values_outside_vocabulary_fall_back(self):
        verdict = lane.normalize_judgment({"in_scope": True, "exclude_reason": "none", "stage": "x",
                                           "significance": "9", "event_date": "10월 14일"})
        self.assertEqual(verdict["stage"], "reaction")
        self.assertEqual(verdict["significance"], 3)
        self.assertEqual(verdict["event_date"], "")

    def test_request_carries_known_event_keys(self):
        _, user = lane.build_request([{"title": "기사", "description": "<b>설명</b>"}], ["원전 공론화위원회 출범"])
        self.assertIn("[최근 사건]", user)
        self.assertIn("- 원전 공론화위원회 출범", user)
        self.assertIn("본문 발췌: 설명", user)

    def test_failed_chunk_is_none_not_excluded(self):
        self.assertEqual(lane.judge([{"title": "원전 공론화"}], call=fake_judge(fail=True)), [None])

    def test_new_event_key_is_offered_to_the_next_batch(self):
        seen_users = []

        def call(system, user):
            seen_users.append(user)
            return fake_judge(key="공론화위 2차 회의")(system, user)
        lane.judge([{"title": f"원전 공론화 {i}"} for i in range(9)], call=call)
        self.assertNotIn("공론화위 2차 회의", seen_users[0])
        self.assertIn("- 공론화위 2차 회의", seen_users[1])

    def test_event_key_norm_ignores_spacing_and_punctuation(self):
        self.assertEqual(lane.event_key_norm("원전 공론화위원회 출범"), lane.event_key_norm("원전공론화위원회·출범"))


def naver_page(items, total=None):
    return {"total": total if total is not None else len(items), "items": items}


def naver_item(i, hours_ago, title="원전 공론화 소식"):
    pub = (NOW - timedelta(hours=hours_ago)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return {"title": f"<b>{title}</b> {i}", "originallink": f"https://local.test/n/{i}",
            "link": f"https://n.news.naver.com/{i}", "description": "설명", "pubDate": pub}


class NaverTests(unittest.TestCase):
    def test_pages_until_the_window_is_covered(self):
        requests = []

        def search(query, display, start):
            requests.append(start)
            if start == 1:
                return naver_page([naver_item(i, 1) for i in range(100)], total=250)
            return naver_page([naver_item(100 + i, 100) for i in range(100)], total=250)
        items, counts = pipe.collect_naver(search, lambda url: url[-6:], now=NOW, queries=["원전 공론화"])
        self.assertEqual(requests, [1, 101])  # 두 번째 페이지에서 창(3일)을 벗어나 멈춘다
        self.assertEqual(len(items), 100)
        stat = counts["네이버 「원전 공론화」"]
        self.assertEqual((stat["total"], stat["pages"], stat["covered"]), (250, 2, True))
        self.assertEqual(items[0]["title"], "원전 공론화 소식 0")
        self.assertEqual(items[0]["url"], "https://local.test/n/0")

    def test_hitting_the_api_ceiling_is_reported_as_a_miss(self):
        def search(query, display, start):
            return naver_page([naver_item(start + i, 1) for i in range(100)], total=5000)
        _, counts = pipe.collect_naver(search, lambda url: url, now=NOW, queries=["원전 공론화"])
        self.assertFalse(counts["네이버 「원전 공론화」"]["covered"])
        self.assertIn("✗창 미달", pipe.format_stats({"feeds": counts}))

    def test_one_failing_query_does_not_stop_the_rest(self):
        def search(query, display, start):
            if query == "나쁜":
                raise RuntimeError("401")
            return naver_page([naver_item(1, 1)])
        items, counts = pipe.collect_naver(search, lambda url: url, now=NOW, queries=["나쁜", "좋은"])
        self.assertEqual(len(items), 1)
        self.assertEqual(counts["네이버 「나쁜」"]["failed"], 1)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.state_file = base / "deliberation_state.json"
        self.archive_dir = base / "deliberation_archive"

    def tearDown(self):
        self.tmp.cleanup()

    def run_lane(self, records, call, **kw):
        return pipe.run(records, judge_call=call, now=kw.pop("now", NOW), state_file=self.state_file,
                        archive_dir=self.archive_dir, **kw)

    def archived(self):
        return pipe.shared.load_archive(self.archive_dir)

    def state(self):
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def test_shared_record_keeps_main_facts_and_lane_interpretation(self):
        stats = self.run_lane([main_record("a1", "정부, 원전 공론화 의제 확정", summary="메인 요약")], fake_judge())
        self.assertEqual(stats["included"], 1)
        row = self.archived()[0]
        self.assertEqual((row["lane"], row["summary"], row["why_important"]), ("deliberation", "메인 요약", "왜"))
        self.assertEqual((row["stage"], row["event_key"], row["significance"]), ("agenda_design", "원전 공론화 의제 확정", 3))

    def test_folded_articles_inherit_without_a_call(self):
        calls = []
        rep = {"hash": "a1", "title": "정부, 원전 공론화 의제 확정", "link": "https://yna.test/a1",
               "domain": "yna.co.kr", "publisher": "연합뉴스", "pub": NOW,
               "raw_sources": [{"hash": f"f{i}", "title": f"원전 공론화위, 계속운전 제외…의제 정했다 {i}",
                                "link": f"https://local{i}.test/x", "publisher": f"지역지{i}",
                                "domain": f"local{i}.test"} for i in range(5)]}
        stats = self.run_lane([main_record("a1", rep["title"])], fake_judge(calls=calls), collected=[rep])
        # 대표 1건만 판정기에 갔다. 제목이 공론화 기사로 보이는 접힌 5건은 호출 없이 상속으로 들어온다
        # (제목이 프리필터를 못 넘는 접힌 기사는 스스로 판정한다 — 아래 별도 검사).
        self.assertEqual(calls, ["정부, 원전 공론화 의제 확정"])
        self.assertEqual((stats["included"], stats["inherited"]), (6, 5))
        folded = [r for r in self.archived() if r["origin"] == "folded"]
        self.assertTrue(all(r["judge"] == "inherited" and r["folded_into"] == "a1" for r in folded))
        self.assertEqual({r["event_key"] for r in folded}, {"원전 공론화 의제 확정"})

    def test_folded_article_without_deliberation_title_is_judged_not_inherited(self):
        # crawl #681 실측: 「의제 확정」 대표 밑에 「고리 3·4호기 계속운전 심의」가 접혀 왔다.
        calls = []
        rep = {"hash": "a1", "title": "정부, 원전 공론화 의제 확정", "link": "https://yna.test/a1", "pub": NOW,
               "raw_sources": [{"hash": "f1", "title": "원전 공론화위, 계속운전 논의서 제외", "link": "https://b.test/f1"},
                               {"hash": "f2", "title": "고리 3·4호기 계속운전 허가안 원안위 상정", "link": "https://c.test/f2"}]}
        stats = self.run_lane([main_record("a1", rep["title"])], fake_judge(calls=calls), collected=[rep])
        self.assertEqual(calls, ["정부, 원전 공론화 의제 확정", "고리 3·4호기 계속운전 허가안 원안위 상정"])
        self.assertEqual((stats["inherited"], stats["included"], stats["excluded"]), (1, 2, 1))
        self.assertEqual({r["hash"] for r in self.archived()}, {"a1", "f1"})

    def test_folded_article_of_an_excluded_rep_is_judged_on_its_own(self):
        rep = {"hash": "a1", "title": "울진 원전 고준위 방폐장 공론화", "link": "https://x.test/a1", "pub": NOW,
               "raw_sources": [{"hash": "f1", "title": "신규원전 공론화위 출범도 함께 거론", "link": "https://y.test/f1"}]}
        calls = []
        stats = self.run_lane([], fake_judge(in_scope=("신규원전",), calls=calls), collected=[rep])
        self.assertEqual(calls, ["울진 원전 고준위 방폐장 공론화", "신규원전 공론화위 출범도 함께 거론"])
        self.assertEqual(stats["included"], 1)
        self.assertEqual(self.archived()[0]["hash"], "f1")

    def test_over_the_limit_candidates_carry_over_to_the_next_run(self):
        records = [main_record(f"a{i}", f"원전 공론화 기사 {i}", publisher=f"매체{i}") for i in range(5)]
        old = pipe.MAX_JUDGE_PER_RUN
        pipe.MAX_JUDGE_PER_RUN = 2
        try:
            first = self.run_lane(records, fake_judge())
            self.assertEqual((first["judged"], first["deferred"]), (2, 3))
            self.assertEqual(len(self.state()["backlog"]), 3)
            # 다음 회차에는 메인 풀이 비어도(3일 창을 벗어나도) 이월분이 온다.
            second = self.run_lane([], fake_judge(), now=NOW + timedelta(hours=3))
            self.assertEqual((second["judged"], second["deferred"]), (2, 1))
            third = self.run_lane([], fake_judge(), now=NOW + timedelta(hours=6))
            self.assertEqual((third["judged"], third["deferred"]), (1, 0))
        finally:
            pipe.MAX_JUDGE_PER_RUN = old
        self.assertEqual(len(self.archived()), 5)

    def test_backlog_expires_after_three_days(self):
        old = pipe.MAX_JUDGE_PER_RUN
        pipe.MAX_JUDGE_PER_RUN = 0
        try:
            self.run_lane([main_record("a1", "원전 공론화 기사")], fake_judge())
            self.run_lane([], fake_judge(), now=NOW + timedelta(days=3, hours=1))
        finally:
            pipe.MAX_JUDGE_PER_RUN = old
        self.assertEqual(self.state()["backlog"], {})

    def test_failure_is_retried_then_given_up(self):
        record = main_record("a1", "원전 공론화 기사")
        for hours in (0, 3, 6):
            stats = self.run_lane([record], fake_judge(fail=True), now=NOW + timedelta(hours=hours))
            self.assertEqual(stats["failed"], 1)
        state = self.state()
        self.assertEqual(state["judged"]["a1"]["reason"], "judge_failed")
        self.assertNotIn("a1", state["backlog"])

    def test_editorial_background_mention_is_kept_for_review(self):
        def call(system, user):
            return {"items": [{"idx": 0, "in_scope": False, "exclude_reason": "background_mention"}]}
        stats = self.run_lane([main_record("e1", "[사설] 전력 정책, 원전 공론화만으론 부족하다")], call)
        self.assertEqual((stats["included"], stats["guarded"]), (1, 1))
        row = self.archived()[0]
        self.assertEqual((row["article_type"], row["confidence"]), ("editorial", "low"))

    def test_judged_articles_are_not_asked_again(self):
        calls = []
        record = main_record("a1", "원전 공론화 기사")
        self.run_lane([record], fake_judge(calls=calls))
        self.run_lane([record], fake_judge(calls=calls), now=NOW + timedelta(hours=3))
        self.assertEqual(len(calls), 1)

    def test_same_title_from_the_same_outlet_is_one_article(self):
        a = main_record("a1", "원전 공론화위 출범", publisher="경북매일")
        b = {**main_record("a2", "원전 공론화위 출범", publisher="경북매일"), "url": "https://other.test/a2"}
        c = main_record("a3", "원전 공론화위 출범", publisher="영남일보")  # 다른 매체 — 근거 한 건 더
        stats = self.run_lane([a, b, c], fake_judge())
        self.assertEqual((stats["duplicate"], stats["included"]), (1, 2))

    def test_known_event_keys_reach_the_prompt(self):
        self.run_lane([main_record("a1", "원전 공론화 의제 확정")], fake_judge())
        users = []

        def call(system, user):
            users.append(user)
            return fake_judge()(system, user)
        self.run_lane([main_record("a2", "원전 공론화 후속")], call, now=NOW + timedelta(hours=3))
        self.assertIn("- 원전 공론화 의제 확정", users[0])

    def test_dedicated_naver_items_enter_the_pool(self):
        def search(query, display, start):
            return naver_page([naver_item(1, 1, title="삼척 원전 공론화 주민 반응")] if query == "삼척 공론화" else [])
        stats = self.run_lane([], fake_judge(), naver=search, hasher=lambda url: "h" + url[-3:])
        self.assertEqual(stats["included"], 1)
        row = self.archived()[0]
        self.assertEqual((row["origin"], row["feed"]), ("dedicated", "네이버 「삼척 공론화」"))
        self.assertIn("[공론화 피드] 네이버 「삼척 공론화」: 받음 1", pipe.format_stats(stats))


class ResearchBackfillTests(unittest.TestCase):
    """기간 백필(since) — 원천 재검색. 정기 회차와 같은 판정, 다른 창·시각."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.state_file = base / "deliberation_state.json"
        self.archive_dir = base / "deliberation_archive"

    def tearDown(self):
        self.tmp.cleanup()

    def test_window_reaches_since_and_rounds_follow_publication(self):
        def search(query, display, start):
            if query != "원전 공론화":
                return naver_page([])
            return naver_page([naver_item(1, 24 * 40, title="9월 원전 공론화 착수"),
                               naver_item(2, 24 * 60, title="8월 원전 공론화 예고")])
        since = NOW - timedelta(days=44)
        stats = pipe.run([], naver=search, hasher=lambda url: "h" + url[-2:], judge_call=fake_judge(),
                         now=NOW, state_file=self.state_file, archive_dir=self.archive_dir, since=since)
        self.assertEqual(stats["included"], 1)          # 60일 전 기사는 since 밖
        row = pipe.shared.load_archive(self.archive_dir)[0]
        self.assertEqual(row["collected_at"][:10], (NOW - timedelta(days=40)).date().isoformat())

    def test_judge_limit_carries_the_rest_over(self):
        def search(query, display, start):
            if query != "원전 공론화":
                return naver_page([])
            return naver_page([naver_item(i, 24 * (i + 1), title=f"원전 공론화 기사 {i}") for i in range(5)])
        stats = pipe.run([], naver=search, hasher=lambda url: "h" + url[-2:], judge_call=fake_judge(),
                         now=NOW, state_file=self.state_file, archive_dir=self.archive_dir,
                         since=NOW - timedelta(days=30), max_judge=2)
        self.assertEqual((stats["judged"], stats["deferred"]), (2, 3))

    def test_google_queries_are_cut_by_date(self):
        sources = pipe.gnews_range_sources(datetime(2026, 9, 1, tzinfo=timezone.utc),
                                           datetime(2026, 10, 15, 10, tzinfo=timezone.utc))
        from urllib.parse import parse_qs, urlparse
        queries = [parse_qs(urlparse(src["url"]).query)["q"][0] for src in sources]
        self.assertTrue(all("after:" in q and "before:" in q and "when:" not in q for q in queries))
        self.assertTrue(all(len(q) < 200 for q in queries))
        self.assertIn("after:2026-09-01 before:2026-09-04", queries[0])
        self.assertTrue(any("after:2026-10-13" in q for q in queries))

    def test_workflow_runs_the_tool_with_inputs_through_env(self):
        text = (ROOT / ".github/workflows/deliberation-backfill.yml").read_text(encoding="utf-8")
        self.assertIn('python tools/deliberation_backfill.py --research --since "$SINCE" --max-judge "$MAX_JUDGE"', text)
        self.assertIn("group: nuclens-state", text)
        self.assertIn("gh workflow run deploy-web.yml", text)


class WiringTests(unittest.TestCase):
    def test_news_bot_runs_the_lane_with_folded_candidates(self):
        src = (ROOT / "news_bot.py").read_text(encoding="utf-8")
        self.assertIn("deliberation_pipeline.run(", src)
        self.assertIn("collected=semantically_unique, naver=search_naver_page", src)
        # 메인 수집 함수는 그대로다 — 레인은 별도 함수로 페이지를 넘긴다.
        self.assertIn('params = {"query": query, "display": display, "sort": "date"}', src)

    def test_workflows_commit_the_lane_state(self):
        for name in ("crawl.yml", "daily-brief.yml"):
            text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            self.assertIn("git add deliberation_archive/", text, name)
            self.assertIn("deliberation_state.json", text, name)
        self.assertIn("deliberation_archive/*.jsonl merge=union", (ROOT / ".gitattributes").read_text(encoding="utf-8"))

    def test_policy_profile_exists(self):
        import llm_policy
        self.assertTrue(llm_policy.profile("deliberation_judge").model())

    def test_gnews_queries_stay_short(self):
        # AI 레인 실측: 200자가 넘는 질의는 when: 창이 무시됐다.
        from urllib.parse import parse_qs, urlparse
        for src in pipe.GNEWS_SOURCES:
            query = parse_qs(urlparse(src["url"]).query)["q"][0]
            self.assertLess(len(query), 200, src["name"])
            self.assertIn("when:", query)


if __name__ == "__main__":
    unittest.main()
