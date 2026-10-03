"""사실검사 모순 판정 → 재생성 연결의 계약.

2026-10-03: 열흘치 검사 기록 1,610건에서 모순 6%·추가 13% 가 나왔는데 판정이
어디로도 가지 않았다. 여기서 잠그는 것: 모순만 넘긴다, 1건씩 부른다, 원문 인용이
메모로 들어간다, 등급이 바뀌면 버린다, curated 와 큐 양쪽에 적는다, 다시 검사해
after_repair 표식으로 남긴다, 라벨·모델 버킷이 따로 센다.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import llm_policy
import news_bot as nb
import summary_verify

ROOT = Path(__file__).resolve().parents[1]


def _row(hash_="h1", verdict="contradiction", **over):
    row = {"hash": hash_, "called": True, "verdict": verdict, "field": "summary",
           "claim": "유치 확정", "source_quote": "유치를 추진하며", "reason": "단계가 다름",
           "rule": []}
    row.update(over)
    return row


def _article(hash_="h1", title="합천군 양수발전소 유치 추진"):
    return {"hash": hash_, "title": title, "link": f"https://x.test/{hash_}",
            "description": "군은 오도산 양수발전소 유치를 추진한다.", "domain": "x.test",
            "publisher": "X", "feed": "f", "matched": "k"}


def _item(summary, importance="nice_to_know", **over):
    item = {"idx": 0, "id": "h1", "importance": importance, "section": "domestic",
            "scope": "kr", "category": "정책", "title_kr": "합천군, 오도산 양수발전소 유치 추진",
            "summary": summary, "detail": "", "implication": "", "why_important": "",
            "why_short": "", "tags": [], "topics": ["pumped"], "countries": ["KR"],
            "article_type": "policy", "event_date": None, "event_date_type": "unknown",
            "event_date_precision": "unknown", "event_date_source": "unknown",
            "related_reports": [],
            "features": {"event_type": "plan", "korea_relevance": 3, "market_materiality": 1,
                         "policy_materiality": 2, "report_worthiness": 1}}
    item.update(over)
    return item


class RepairNotesTests(unittest.TestCase):
    def test_only_contradictions_and_rules_become_notes(self):
        rows = [_row("a"), _row("b", verdict="unsupported"), _row("c", verdict="ok"),
                _row("d", verdict="ok", rule=["최종안←정부안"]),
                _row("e", called=False, verdict="")]
        notes = summary_verify.repair_notes(rows)
        self.assertEqual(set(notes), {"a", "d"})
        self.assertIn("유치를 추진하며", notes["a"][0])   # 원문 인용이 메모에 실린다
        self.assertIn("유치 확정", notes["a"][0])
        self.assertIn("최종안←정부안", notes["d"][0])


class CurateBatchRepairSeamTests(unittest.TestCase):
    def test_notes_go_into_first_call_one_article_per_call_with_own_label_and_model(self):
        arts = [_article("h1"), _article("h2", title="두 번째 기사")]
        notes = {"h1": ["사실검사 모순[summary]: 요약이 '확정' 이라고 썼으나 원문은 '추진'"]}
        responses = [{"items": [_item("군이 유치를 추진한다.", id="h1")]},
                     {"items": [_item("두 번째 기사 요약이다.", id="h2", title_kr="두 번째 기사")]}]
        with patch.object(nb, "gemini_rest_available", return_value=True), \
             patch.object(nb, "gemini_call_json", side_effect=responses) as call, \
             patch.dict(os.environ, {"GEMINI_CURATION_REPAIR_MODEL": "repair-x"}, clear=False):
            out = nb.curate_batch(arts, [], {"h1": "본문 " * 60, "h2": "본문 " * 60},
                                  error_notes=notes, chunk_size=1,
                                  label="curation:검사재생성", profile="curation_verify_repair")
        self.assertEqual(call.call_count, 2)                       # 1건씩
        first_system, first_user = call.call_args_list[0].args[:2]
        self.assertIn("[재생성]", first_system)                     # 첫 호출부터 재생성 모드
        self.assertIn("이전 출력 오류: 사실검사 모순", first_user)
        self.assertEqual(call.call_args_list[0].kwargs["label"], "curation:검사재생성")
        self.assertEqual(call.call_args_list[0].kwargs["model"], "repair-x")
        second_user = call.call_args_list[1].args[1]
        self.assertNotIn("이전 출력 오류", second_user)              # 메모 없는 기사엔 안 붙는다
        self.assertEqual(set(out), {"h1", "h2"})

    def test_defaults_are_unchanged(self):
        with patch.object(nb, "gemini_rest_available", return_value=True), \
             patch.object(nb, "gemini_call_json",
                          side_effect=[{"items": [_item("군이 유치를 추진한다.", id="h1")]}]) as call:
            nb.curate_batch([_article()], [])
        self.assertEqual(call.call_args.kwargs["label"], "curation")
        self.assertEqual(call.call_args.kwargs["model"], llm_policy.profile("curation").model())
        self.assertNotIn("[재생성]", call.call_args.args[0])


class RepairContradictedSummariesTests(unittest.TestCase):
    def setUp(self):
        self.article = _article()
        self.curated = {"h1": {"importance": "nice_to_know",
                               "title_kr": "합천군, 양수발전소 유치 확정",
                               "summary": "합천군이 오도산 양수발전소 유치를 확정했다.",
                               "detail": "", "implication": "확정이라 중요", "why_important": "",
                               "why_short": "", "section": "domestic",
                               "features": {"event_type": "plan"}}}
        self.queue = [{"hash": "h1", "summary": "합천군이 오도산 양수발전소 유치를 확정했다.",
                       "title_kr": "합천군, 양수발전소 유치 확정", "importance": "nice_to_know"},
                      {"hash": "other", "summary": "그대로"}]
        self.targets = [{"hash": "h1", "title": self.article["title"], "body": "군은 유치를 추진하며",
                         "title_kr": self.curated["h1"]["title_kr"],
                         "summary": self.curated["h1"]["summary"], "detail": "",
                         "published": "2026-10-02"}]
        self.rows = [_row("h1")]

    def _run(self, fresh, verify_rows=None):
        calls = {}

        def curate(articles, reports_kb, bodies, **kw):
            calls["curate"] = {"articles": articles, "bodies": bodies, **kw}
            return fresh

        def verify(targets, **kw):
            calls["verify"] = {"targets": targets, **kw}
            return (verify_rows or []), {"checked": len(targets),
                                         "contradiction": len(verify_rows or []),
                                         "unsupported": 0, "failed": 0}
        stats = nb.repair_contradicted_summaries(
            self.rows, self.targets, [self.article], self.curated, self.queue, [],
            now_iso="2026-10-03T00:00:00+00:00", curate=curate, verify=verify)
        return stats, calls

    def test_repaired_text_lands_in_curated_and_queue_then_rechecked(self):
        fresh = {"h1": _item("합천군이 오도산 양수발전소 유치를 추진한다.",
                             title_kr="합천군, 오도산 양수발전소 유치 추진", implication="추진 단계")}
        stats, calls = self._run(fresh)
        self.assertEqual(stats["repaired"], 1)
        self.assertEqual(calls["curate"]["chunk_size"], 1)
        self.assertEqual(calls["curate"]["profile"], "curation_verify_repair")
        self.assertEqual(calls["curate"]["label"], "curation:검사재생성")
        self.assertIn("유치를 추진하며", calls["curate"]["error_notes"]["h1"][0])
        self.assertEqual(calls["curate"]["bodies"], {"h1": "군은 유치를 추진하며"})
        cur = self.curated["h1"]
        self.assertEqual(cur["summary"], "합천군이 오도산 양수발전소 유치를 추진한다.")
        self.assertEqual(cur["title_kr"], "합천군, 오도산 양수발전소 유치 추진")
        self.assertEqual(cur["implication"], "추진 단계")
        self.assertEqual(cur["importance"], "nice_to_know")            # 등급은 원래 값
        self.assertEqual(cur["features"], {"event_type": "plan"})      # 지표도 원래 값
        self.assertEqual(cur["summary_repair"]["before"]["summary"],
                         "합천군이 오도산 양수발전소 유치를 확정했다.")
        self.assertEqual(self.queue[0]["summary"], cur["summary"])     # 큐 사본도 고친다
        self.assertEqual(self.queue[0]["title_kr"], cur["title_kr"])
        self.assertEqual(self.queue[1]["summary"], "그대로")
        self.assertEqual(calls["verify"]["row_extra"], {"stage": "after_repair"})
        self.assertEqual(calls["verify"]["targets"][0]["summary"], cur["summary"])
        self.assertEqual(calls["verify"]["targets"][0]["body"], "군은 유치를 추진하며")
        self.assertEqual(stats["recheck"]["checked"], 1)

    def test_grade_change_keeps_the_grade_but_takes_the_text(self):
        """처음엔 등급이 달라진 답을 통째로 버렸다. 그러면 모순이 확인된 요약이
        그대로 남는다(2026-10-03 지적). 등급은 두고 문장만 받는다."""
        fresh = {"h1": _item("합천군이 유치를 추진한다.", importance="noise", implication="")}
        stats, calls = self._run(fresh)
        self.assertEqual(stats["grade_changed"], 1)
        self.assertEqual(stats["repaired"], 1)
        cur = self.curated["h1"]
        self.assertEqual(cur["summary"], "합천군이 유치를 추진한다.")
        self.assertEqual(cur["importance"], "nice_to_know")           # 등급은 원래 값
        self.assertEqual(cur["implication"], "확정이라 중요")           # 비어 온 칸은 안 덮는다
        self.assertEqual(self.queue[0]["summary"], "합천군이 유치를 추진한다.")
        self.assertIn("verify", calls)

    def test_empty_regenerated_field_never_blanks_an_existing_one(self):
        fresh = {"h1": _item("합천군이 유치를 추진한다.", implication="", why_important="")}
        self.curated["h1"]["why_important"] = "지역 전력망에 영향"
        self._run(fresh)
        cur = self.curated["h1"]
        self.assertEqual(cur["summary"], "합천군이 유치를 추진한다.")
        self.assertEqual(cur["implication"], "확정이라 중요")
        self.assertEqual(cur["why_important"], "지역 전력망에 영향")

    def test_failed_regeneration_keeps_original(self):
        stats, calls = self._run({})
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(self.curated["h1"]["summary"], "합천군이 오도산 양수발전소 유치를 확정했다.")
        self.assertNotIn("summary_repair", self.curated["h1"])

    def test_nothing_flagged_makes_no_call(self):
        self.rows = [_row("h1", verdict="unsupported")]
        stats, calls = self._run({"h1": _item("x")})
        self.assertEqual(stats, {"flagged": 0, "targets": 0, "repaired": 0, "grade_changed": 0,
                                 "failed": 0, "recheck": {}})
        self.assertEqual(calls, {})

    def test_backlog_article_without_source_is_skipped(self):
        """밀린 기사는 기사 원본이 손에 없어 재생성하지 않는다."""
        self.rows = [_row("h1"), _row("old")]
        self.targets.append({"hash": "old", "title": "옛 기사", "body": "본문", "title_kr": "옛",
                             "summary": "요약", "detail": ""})
        stats, calls = self._run({"h1": _item("합천군이 오도산 양수발전소 유치를 추진한다.")})
        self.assertEqual(stats["flagged"], 2)
        self.assertEqual(stats["targets"], 1)
        self.assertEqual([a["hash"] for a in calls["curate"]["articles"]], ["h1"])


class PolicyTests(unittest.TestCase):
    def test_repair_profile_has_its_own_bucket(self):
        with patch.dict(os.environ, {"GEMINI_CURATION_REPAIR_MODEL": ""}, clear=False):
            self.assertEqual(llm_policy.profile("curation_verify_repair").model(),
                             "gemini-3.5-flash-lite")
            self.assertEqual(llm_policy.profile("curation_verify_repair").task,
                             llm_policy.BULK_CURATION)

    def test_workflows_pass_repair_variables(self):
        for name in ("crawl.yml", "daily-brief.yml"):
            yml = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn("GEMINI_CURATION_REPAIR_MODEL: ${{ vars.GEMINI_CURATION_REPAIR_MODEL }}", yml)
            self.assertIn("SUMMARY_REPAIR: ${{ vars.SUMMARY_REPAIR }}", yml)

    def test_repair_switch(self):
        with patch.dict(os.environ, {"SUMMARY_REPAIR": "off"}, clear=False):
            self.assertFalse(nb.summary_repair_enabled())
        with patch.dict(os.environ, {"SUMMARY_REPAIR": ""}, clear=False):
            self.assertTrue(nb.summary_repair_enabled())


if __name__ == "__main__":
    unittest.main()
