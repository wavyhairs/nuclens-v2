"""요약 사실검증(경고 모드)의 계약.

측정(2026-09-24)에서 정한 것들을 잠근다: 1건씩 묻는다, 추론을 켜지 않는다,
오늘 날짜와 '원문이 유일한 진실'을 준다, 인용이 원문에 없으면 모순을 믿지 않는다,
한도·결제 오류를 보면 멈춘다, 요약은 바꾸지 않는다.
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import summary_verify

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 24, 3, 0, tzinfo=timezone.utc)  # 12:00 KST


def target(hash_="a" * 16, **overrides):
    row = {
        "hash": hash_,
        "title": "정부, 3개월간 원전 공론화",
        "body": "12차 전력수급기본계획은 당초 10월에 정부안이 나올 예정이었으나, 공론화 과정을 담아 12월에 나온다",
        "title_kr": "정부, 12차 전기본 수립 위해 3개월간 원전 공론화 추진",
        "summary": "정부가 3개월간 원전 공론화를 진행하며, 최종안은 12월에 발표될 예정임.",
        "detail": "",
    }
    row.update(overrides)
    return row


class FakeClient:
    def __init__(self, answers=None, error=None, available=True):
        self.answers = list(answers or [])
        self.error = error
        self.available = available
        self.calls = []

    def is_available(self):
        return self.available

    def call_json(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, **kwargs})
        if self.error is not None:
            raise self.error
        answer = self.answers.pop(0) if self.answers else {"verdict": "ok"}
        return {"items": [answer]}


class StageWordRuleTests(unittest.TestCase):
    """LLM 이 어떤 추론 레벨에서도 못 잡은 유형 — 규칙이 맡는다."""

    def test_final_term_against_draft_source_is_flagged(self):
        t = target()
        found = summary_verify.stage_word_findings(t["summary"], t["body"])
        self.assertEqual(found, ["최종안←정부안"])

    def test_source_that_also_says_final_is_not_flagged(self):
        self.assertEqual(summary_verify.stage_word_findings(
            "최종안은 2월", "정부안은 12월, 최종안은 내년 2월"), [])

    def test_no_draft_term_in_source_means_no_finding(self):
        self.assertEqual(summary_verify.stage_word_findings("최종안 발표", "계획을 발표했다"), [])


class ClassifyTests(unittest.TestCase):
    SOURCE = "원문 제목\n양산 목표 시점은 2028년이다. 시험생산은 2027년 1분기다."

    def test_contradiction_with_quote_in_source_stands(self):
        verdict, quoted = summary_verify.classify(
            {"verdict": "contradiction", "source_quote": "양산 목표 시점은 2028년이다",
             "reason": "원문은 2028년"}, self.SOURCE)
        self.assertEqual((verdict, quoted), ("contradiction", True))

    def test_quote_from_elsewhere_is_not_trusted(self):
        """묶음 측정에서 옆 기사 원문을 인용한 사고가 있었다."""
        verdict, _ = summary_verify.classify(
            {"verdict": "contradiction", "source_quote": "최종 확정은 내년 2월을 목표로 한다",
             "reason": "다름"}, self.SOURCE)
        self.assertEqual(verdict, "quote_unverified")

    def test_not_mentioned_reason_downgrades_to_unsupported(self):
        verdict, _ = summary_verify.classify(
            {"verdict": "contradiction", "source_quote": "양산 목표 시점은 2028년이다",
             "reason": "원문에는 해당 내용이 언급되지 않음"}, self.SOURCE)
        self.assertEqual(verdict, "unsupported")

    def test_ellipsis_quote_checks_each_piece(self):
        self.assertTrue(summary_verify.quote_in_source(
            "양산 목표 시점은... 시험생산은 2027년 1분기다", self.SOURCE))

    def test_unknown_verdict_is_invalid(self):
        self.assertEqual(summary_verify.classify({"verdict": "maybe"}, self.SOURCE)[0], "invalid")


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "checks.jsonl"

    def run_verify(self, targets, client, **kwargs):
        kwargs.setdefault("day_cap", 250)
        kwargs.setdefault("per_run_cap", 80)
        return summary_verify.verify(targets, client=client, now=NOW, path=self.path, **kwargs)

    def test_one_call_per_article_without_reasoning(self):
        client = FakeClient()
        self.run_verify([target("a" * 16), target("b" * 16)], client)
        self.assertEqual(len(client.calls), 2)
        for call in client.calls:
            self.assertEqual(call["user"].count("### id="), 1, "묶어 물으면 옆 기사 원문이 섞인다")
            self.assertNotIn("thinking_level", call)
            self.assertNotIn("thinking_budget", call)
            self.assertEqual(call["temperature"], 0.0)
            self.assertEqual(call["label"], "summary_verify")

    def test_prompt_carries_today_and_source_only_rule(self):
        client = FakeClient()
        self.run_verify([target()], client)
        system = client.calls[0]["system"]
        self.assertIn("2026년 9월 24일", system)
        self.assertIn("원문이 유일한 진실", system)
        # 추론 없는 작은 모델은 '내년'을 제 기준으로 풀었다 — 코드가 미리 계산해 준다.
        self.assertIn("내년은 2027년", system)

    def test_each_article_carries_its_published_date(self):
        client = FakeClient()
        self.run_verify([target(published="2026-09-22T06:20:00+00:00")], client)
        self.assertIn("발행일: 2026-09-22", client.calls[0]["user"])

    def test_results_are_appended_not_rewritten(self):
        self.path.write_text('{"hash": "old", "called": true}\n', encoding="utf-8")
        self.run_verify([target()], FakeClient([{"verdict": "ok"}]))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["hash"], "old")
        self.assertEqual(len(lines), 2)

    def test_same_summary_is_not_asked_twice(self):
        self.run_verify([target()], FakeClient())
        client = FakeClient()
        _, stats = self.run_verify([target()], client)
        self.assertEqual(client.calls, [])
        self.assertEqual(stats["cached"], 1)

    def test_changed_summary_is_asked_again(self):
        self.run_verify([target()], FakeClient())
        client = FakeClient()
        self.run_verify([target(summary="다른 요약")], client)
        self.assertEqual(len(client.calls), 1)

    def test_daily_cap_counts_todays_calls_from_the_log(self):
        rows = [{"hash": f"h{i}", "called": True, "quota_day": "2026-09-23"} for i in range(3)]
        self.path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        client = FakeClient()
        _, stats = self.run_verify([target("a" * 16), target("b" * 16)], client, day_cap=4)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(stats["skipped_cap"], 1)

    def test_quota_day_resets_at_16_kst(self):
        self.assertEqual(summary_verify.quota_day(datetime(2026, 9, 24, 6, 59, tzinfo=timezone.utc)),
                         "2026-09-23")
        self.assertEqual(summary_verify.quota_day(datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc)),
                         "2026-09-24")

    def test_quota_or_payment_error_stops_the_run(self):
        for message in ("HTTP 429: quota", "HTTP 402: Your prepayment credits are depleted"):
            with self.subTest(message=message):
                client = FakeClient(error=RuntimeError(message))
                _, stats = summary_verify.verify(
                    [target("a" * 16), target("b" * 16)], client=client, now=NOW,
                    path=Path(self.tmp.name) / f"{message[5:8]}.jsonl", day_cap=250, per_run_cap=80)
                self.assertEqual(len(client.calls), 1)
                self.assertTrue(stats["stopped"])

    def test_other_errors_do_not_stop_the_run(self):
        client = FakeClient(error=RuntimeError("timeout"))
        self.run_verify([target("a" * 16), target("b" * 16)], client)
        self.assertEqual(len(client.calls), 2)

    def test_no_key_means_no_calls_but_rule_still_recorded(self):
        client = FakeClient(available=False)
        rows, stats = self.run_verify([target()], client)
        self.assertEqual(client.calls, [])
        self.assertEqual(stats["status"], "no_api_key")
        self.assertEqual(rows[0]["rule"], ["최종안←정부안"])
        self.assertFalse(rows[0]["called"])

    def test_contradiction_row_is_recorded_with_evidence(self):
        answer = {"verdict": "contradiction", "field": "summary", "claim": "최종안은 12월",
                  "source_quote": "당초 10월에 정부안이 나올 예정이었으나", "reason": "정부안"}
        rows, stats = self.run_verify([target()], FakeClient([answer]))
        self.assertEqual(stats["contradiction"], 1)
        self.assertEqual(rows[0]["verdict"], "contradiction")
        self.assertTrue(rows[0]["quote_in_source"])


class TargetSelectionTests(unittest.TestCase):
    def test_only_new_kept_articles_with_body(self):
        articles = [{"hash": h, "title": h} for h in ("new", "old", "noise", "nobody", "fb")]
        curated = {h: {"importance": "must_read", "summary": "s"} for h in ("new", "old", "nobody", "fb")}
        curated["noise"] = {"importance": "noise"}
        curated["fb"]["curation_status"] = "fallback"
        bodies = {h: "본문" for h in ("new", "old", "noise", "fb")}
        attempted = {"new", "noise", "nobody", "fb"}
        out = summary_verify.targets_from_curation(
            articles, curated, bodies, attempted,
            lambda cur: cur.get("curation_status") == "fallback")
        self.assertEqual([row["hash"] for row in out], ["new"])

    def test_published_date_comes_from_the_normalized_record(self):
        out = summary_verify.targets_from_curation(
            [{"hash": "h", "title": "t", "pub": "Tue, 22 Sep 2026 06:20:00 GMT"}],
            {"h": {"importance": "must_read", "published_at": "2026-09-22T06:20:00+00:00"}},
            {"h": "본문"}, {"h"}, lambda cur: False)
        self.assertEqual(out[0]["published"], "2026-09-22")


class WiringTests(unittest.TestCase):
    def test_news_bot_runs_it_warning_only_after_curation(self):
        source = (ROOT / "news_bot.py").read_text(encoding="utf-8")
        start = source.index("# ---- 요약 사실검증")
        block = source[start:source.index("# ---- 영구 아카이브 적재", start)]
        self.assertIn("not (QUOTA_EXHAUSTED or CONFIG_ERROR)", block)
        self.assertIn("except Exception", block)
        self.assertNotIn("curated[", block, "경고 모드는 요약을 바꾸지 않는다")

    def test_log_is_committed_and_union_merged(self):
        for name in ("crawl.yml", "daily-brief.yml"):
            yml = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn("summary_checks.jsonl", yml, f"{name} 에 커밋이 빠졌다")
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("summary_checks.jsonl merge=union", attributes)

    def test_policy_uses_the_curation_bucket_without_reasoning(self):
        import llm_policy
        profile = llm_policy.profile("summary_verify")
        self.assertEqual(profile.model(), llm_policy.profile("curation").model())
        self.assertEqual(profile.reasoning_kwargs(), {})


if __name__ == "__main__":
    unittest.main()
