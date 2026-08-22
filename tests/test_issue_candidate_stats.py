"""issue_candidate_stats.py — 후보가 어디서 몇 개 나는지 세는 계측과 그 감시.

이 파일이 잠그는 것 두 가지:
  ① 집계가 산수로 맞는가 (밴드 합 = 전수, 경로 합 = 전수, Top-N 단조성)
  ② **감시가 평소엔 조용한가.** 매 회차 우는 알림은 아무도 안 본다 — 표본 하나가
     튀었을 때(max)와 실제로 병합을 놓쳤을 때(beyond_cut)를 가르는 것이 요점이다.
"""

import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import issue_candidate_stats as stats


def candidate(score, *, article="a1", role=None, tag_shared=1, topic_shared=1,
              title=0.5, token=0.3, contested=(), shared=(), compared=0,
              facilities=()):
    row = {
        "candidate_id": f"{article}--{score}",
        "right_hash": article,
        "candidate_score": score,
        "shared_facility_entities": list(facilities),
        "diagnostics": {
            "embedding_similarity": score,
            "tag_shared": tag_shared,
            "topic_shared": topic_shared,
            "title_ratio": title,
            "token_ratio": token,
            "story_fingerprint_contested": list(contested),
            "story_fingerprint_shared": list(shared),
            "story_fingerprint_compared": compared,
        },
    }
    if role:
        row["member_role"] = role
    return row


def merge(score, *, article="a1", method="llm_approved", role="card", **kwargs):
    row = candidate(score, article=article, **kwargs)["diagnostics"]
    return {"hash": article, "method": method, "member_role": role, **row,
            "shared_facility_entities": []}


class BandTableTests(unittest.TestCase):
    def test_bands_and_origins_add_up_to_the_total(self):
        """합이 안 맞으면 표가 아니라 거짓말이다 — 어떤 구간에도 안 들어가는
        후보가 조용히 사라지면 '후보가 줄었다'로 잘못 읽힌다."""
        rows = [candidate(0.71), candidate(0.78, role="evidence"),
                candidate(0.83), candidate(0.86, role="evidence"),
                candidate(0.90), candidate(None)]
        table = stats.band_table(rows)
        self.assertEqual(table["total"], len(rows))
        self.assertEqual(sum(row["count"] for row in table["by_band"]), len(rows))
        self.assertEqual(sum(table["by_origin"].values()), len(rows))
        for row in table["by_band"]:
            self.assertEqual(row["evidence"] + row["card"], row["count"], row["band"])

    def test_the_requested_bands_are_always_present_even_when_empty(self):
        """0건도 칸으로 남아야 한다. 칸이 사라지면 '그 구간이 비었다'와
        '그 구간을 안 쟀다'를 구분할 수 없다."""
        names = [row["band"] for row in stats.band_table([candidate(0.9)])["by_band"]]
        for low, high in stats.BANDS:
            self.assertIn(f"{low:.2f}-{high:.2f}", names)

    def test_only_the_review_band_counts_as_reviewed(self):
        """0.84 미만은 LLM 도 콘솔도 집어 들지 않는다 — 그 사실이 숫자로 남아야
        '후보가 많다'와 '검수할 것이 많다'를 섞어 말하지 않게 된다."""
        rows = [candidate(0.71), candidate(0.83), candidate(0.84), candidate(0.9195)]
        table = stats.band_table(rows)
        self.assertEqual(table["review_band_count"], 2)
        self.assertEqual(table["review_band"], [0.84, 0.92])


class TopNRetentionTests(unittest.TestCase):
    def test_the_measured_levels_cover_the_gap_between_ten_and_twenty(self):
        """"승인 병합 100% 를 지키는 가장 작은 N" 을 고르려면 그 사이가 있어야 한다 —
        러너 실측에서 Top-10 이 165건 중 1건을 놓쳤으니 답은 10 과 20 사이다."""
        self.assertEqual(stats.TOP_N_CHOICES, (3, 5, 10, 12, 15, 20))

    def test_retention_never_falls_as_n_grows(self):
        rows = [candidate(0.9 - i / 100, article="a1") for i in range(12)]
        rows += [candidate(0.86, article="a2")]
        levels = stats.topn_retention(rows, [])["levels"]
        shares = [level["review_band_share"] for level in levels]
        self.assertEqual(shares, sorted(shares))
        kept = [level["candidates_kept"] for level in levels]
        self.assertEqual(kept, sorted(kept))

    def test_only_llm_approved_merges_are_at_risk(self):
        """Top-N 은 후보 목록만 줄인다. 제목·태그·임베딩 병합은 그 목록을 거치지
        않으므로 위험 계산에 넣으면 안 된다 — 넣으면 위험이 과장돼 컷을 못 건다."""
        merges = [merge(0.95, method="tags"), merge(0.95, method="embedding"),
                  merge(0.86, method="llm_approved")]
        retention = stats.topn_retention([candidate(0.9)], merges)
        self.assertEqual(retention["llm_approved_total"], 1)

    def test_a_merge_behind_many_higher_candidates_is_reported_lost(self):
        rows = [candidate(0.90 + i / 1000, article="a1") for i in range(6)]
        merges = [merge(0.85, article="a1")]
        levels = {level["n"]: level for level in stats.topn_retention(rows, merges)["levels"]}
        self.assertEqual(levels[3]["llm_approved_kept"], 0)
        self.assertEqual(levels[10]["llm_approved_kept"], 1)


class WithinArticleTopNTests(unittest.TestCase):
    """실제로 거는 상한. 계측이 잰 그 순위를 그대로 집행해야 한다."""

    def test_the_cap_is_the_measured_hundred_percent_line(self):
        """12 는 감이 아니라 실측 최소값이다 — 가드도 같은 값에서 운다."""
        self.assertEqual(stats.ISSUE_CANDIDATE_TOP_N, 12)
        self.assertEqual(stats.GUARD_LIMITS["top_n"], 12)
        self.assertEqual(stats.GUARD_LIMITS["top_n_min_retention"], 1.0)

    def test_twelve_or_fewer_candidates_pass_through_untouched(self):
        rows = [candidate(0.9 - i / 100, article="a1") for i in range(12)]
        kept = stats.within_article_top_n(rows)
        self.assertEqual(len(kept), 12)
        self.assertEqual([r["candidate_id"] for r in kept],
                         [r["candidate_id"] for r in rows])

    def test_only_the_top_twelve_go_on(self):
        rows = [candidate(0.99 - i / 100, article="a1") for i in range(20)]
        kept = stats.within_article_top_n(rows)
        self.assertEqual(len(kept), 12)
        # 남은 것은 점수 상위 12개다.
        self.assertEqual({r["candidate_score"] for r in kept},
                         {r["candidate_score"] for r in rows[:12]})

    def test_the_cap_is_per_article_not_global(self):
        rows = ([candidate(0.99 - i / 100, article="a1") for i in range(20)]
                + [candidate(0.5 - i / 100, article="a2") for i in range(3)])
        kept = stats.within_article_top_n(rows)
        by_article = Counter(r["right_hash"] for r in kept)
        self.assertEqual(by_article, Counter({"a1": 12, "a2": 3}))

    def test_ties_do_not_depend_on_input_order(self):
        """동점이 경계에 걸려도 결과가 목록 순서로 흔들리면 안 된다."""
        rows = ([candidate(0.9, article="a1")] * 0
                + [candidate(0.9 - i / 100, article="a1") for i in range(11)]
                + [candidate(0.5, article="a1"), candidate(0.5, article="a1")])
        forward = {id(r) for r in stats.within_article_top_n(rows)}
        backward = {id(r) for r in stats.within_article_top_n(list(reversed(rows)))}
        self.assertEqual(forward, backward)
        # 동점은 관대하게 남긴다 — 계측이 보장한 보존율을 그대로 옮기기 위해서다.
        self.assertEqual(len(forward), 13)

    def test_enforcement_agrees_with_the_retention_measurement(self):
        """집행과 계측이 어긋나면 100% 라고 재 놓고 실제로는 병합을 잃는다."""
        rows = [candidate(0.99 - i / 100, article="a1") for i in range(20)]
        rows += [candidate(0.88 - i / 100, article="a2") for i in range(5)]
        kept = {r["candidate_id"] for r in stats.within_article_top_n(rows)}
        scores = {}
        for row in rows:
            scores.setdefault(row["right_hash"], []).append(row["candidate_score"])
        measured = {
            row["candidate_id"] for row in rows
            if sum(1 for o in scores[row["right_hash"]]
                   if o > row["candidate_score"]) < stats.ISSUE_CANDIDATE_TOP_N
        }
        self.assertEqual(kept, measured)

    def test_zero_disables_the_cap(self):
        rows = [candidate(0.99 - i / 100, article="a1") for i in range(20)]
        self.assertEqual(len(stats.within_article_top_n(rows, 0)), 20)

    def test_the_measured_levels_still_span_past_the_cap(self):
        """15·20 은 여유 확인용으로 남는다 — 데이터가 변하면 여기서 먼저 보인다."""
        self.assertIn(stats.ISSUE_CANDIDATE_TOP_N, stats.TOP_N_CHOICES)
        self.assertTrue([n for n in stats.TOP_N_CHOICES
                         if n > stats.ISSUE_CANDIDATE_TOP_N])


class TopNGuardTests(unittest.TestCase):
    """상한이 승인 병합을 놓치면 그 회차에 바로 울어야 한다."""

    def retention(self, kept, total=170, n=12):
        return {"top_n_retention": {
            "llm_approved_total": total,
            "levels": [{"n": n, "llm_approved_kept": kept,
                        "llm_approved_share": kept / total}],
        }}

    def test_full_retention_is_silent(self):
        self.assertEqual(stats.guardrails(self.retention(170)), [])

    def test_losing_one_approved_merge_warns(self):
        found = stats.guardrails(self.retention(169))
        self.assertEqual([row["id"] for row in found],
                         ["issue-candidate:topn-retention"])
        self.assertIn("Top-12", found[0]["title"])

    def test_the_guard_now_watches_twelve_not_ten(self):
        """운영 상한이 12 이므로 10 에서 울면 적용하지 않는 컷을 두고 우는 것이다."""
        at_ten = {"top_n_retention": {
            "llm_approved_total": 170,
            "levels": [{"n": 10, "llm_approved_kept": 169, "llm_approved_share": 0.9941},
                       {"n": 12, "llm_approved_kept": 170, "llm_approved_share": 1.0}],
        }}
        self.assertEqual(stats.guardrails(at_ten), [])


class PrefilterShadowTests(unittest.TestCase):
    def test_a_filter_reports_what_it_would_have_killed(self):
        """줄어드는 수만 세면 그 표는 대가를 숨긴다. 실제 병합 손실이 같이
        나와야 '많이 줄었다'가 '많이 잃었다'를 가리지 못한다."""
        rows = [candidate(0.86, tag_shared=0, facilities=())]
        merges = [merge(0.90, tag_shared=0), merge(0.93, tag_shared=2)]
        shadow = stats.prefilter_shadow(rows, merges)
        by_id = {row["id"]: row for row in shadow["filters"]}
        no_tag = by_id["no_concrete_tag_or_facility"]
        self.assertEqual(no_tag["candidates"], 1)
        self.assertEqual(no_tag["merges_lost"], 1)
        self.assertEqual(no_tag["merges_lost_by_method"], {"llm_approved": 1})

    def test_a_shared_facility_entity_rescues_a_tagless_pair(self):
        rows = [candidate(0.86, tag_shared=0, facilities=("plant:hanbit",))]
        by_id = {row["id"]: row
                 for row in stats.prefilter_shadow(rows, [])["filters"]}
        self.assertEqual(by_id["no_concrete_tag_or_facility"]["candidates"], 0)


class PreselectRankTests(unittest.TestCase):
    def test_beyond_cut_counts_only_what_the_cut_would_lose(self):
        ranks = Counter({0: 90, 3: 8, 25: 2})
        summary = stats.preselect_rank_summary(ranks, Counter({"unlanded": 5}), cut=20)
        self.assertEqual(summary["landed"], 100)
        self.assertEqual(summary["beyond_cut"], 2)
        self.assertEqual(summary["beyond_cut_share"], 0.02)
        self.assertEqual(summary["max"], 25)
        self.assertEqual(summary["median"], 0)


class GuardrailTests(unittest.TestCase):
    """평소엔 빈 리스트가 정상이다. 여기서 잠그는 것은 '언제 우는가'다."""

    def test_only_the_paths_we_plan_to_cut_are_guarded(self):
        """적용하지도 않을 컷을 두고 우는 알림은 배경 소음이 된다.

        2026-08-21 러너 첫 회차에서 실제로 그랬다 — card 경로가 컷 20 에서
        1.6% 를 잃는다고 경고했는데, 계획은 evidence 경로만 자르는 것이다.
        값은 계속 재되(`preselect_rank` 에 남는다) 알림은 안 건다.
        """
        breach = stats.preselect_rank_summary(Counter({0: 100, 25: 40}), Counter())
        diag = {
            "search_space": [{"path": "card", "preselect_rank": breach},
                             {"path": "evidence", "preselect_rank": breach}],
            "top_n_retention": {"llm_approved_total": 0, "levels": []},
        }
        found = stats.guardrails(diag)
        self.assertEqual([row["id"] for row in found],
                         ["issue-candidate:preselect-headroom"])
        self.assertIn("(evidence)", found[0]["title"])
        self.assertNotIn("card", found[0]["title"])

    def test_desync_is_reported_on_any_path(self):
        """계측 무결성은 컷 계획과 무관하다 — 어느 경로든 수치를 못 믿게 만든다."""
        broken = stats.preselect_rank_summary(Counter({0: 100}), Counter())
        broken["not_in_table"] = 3
        found = stats.guardrails({
            "search_space": [{"path": "card", "preselect_rank": broken}],
            "top_n_retention": {"llm_approved_total": 0, "levels": []},
        })
        self.assertEqual([row["id"] for row in found],
                         ["issue-candidate:telemetry-desync"])

    @staticmethod
    def diagnostics(ranks, *, path="evidence", landed_extra=None, **rest):
        summary = stats.preselect_rank_summary(ranks, Counter())
        if landed_extra:
            summary.update(landed_extra)
        base = {
            "search_space": [{"path": path, "preselect_rank": summary}],
            "top_n_retention": {"llm_approved_total": 0, "levels": []},
        }
        base.update(rest)
        return base

    def test_a_healthy_run_says_nothing(self):
        healthy = self.diagnostics(Counter({0: 300, 1: 80, 4: 20}))
        self.assertEqual(stats.guardrails(healthy), [])

    def test_one_outlier_does_not_page(self):
        """`max` 하나로 걸면 매 회차 운다. 컷 밖 1건 / 400건(0.25%)은 침묵해야 한다 —
        이 테스트가 깨지면 알림이 배경 소음이 되고, 그러면 진짜일 때도 안 읽힌다."""
        noisy = self.diagnostics(Counter({0: 350, 2: 49, 28: 1}))
        self.assertEqual(stats.guardrails(noisy), [])

    def test_real_loss_pages(self):
        broken = self.diagnostics(Counter({0: 300, 2: 60, 25: 40}))
        found = stats.guardrails(broken)
        self.assertEqual([row["id"] for row in found],
                         ["issue-candidate:preselect-headroom"])
        self.assertEqual(found[0]["severity"], "critical")
        self.assertIn("40건", found[0]["detail"])

    def test_a_small_sample_is_not_judged(self):
        """뉴스가 한산한 날 분모가 10건이면 1건만 밀려도 10% 다. 추적률 게이트가
        하루치 분모로 겪은 것과 같은 함정이라 표본 하한을 둔다."""
        tiny = self.diagnostics(Counter({0: 8, 30: 2}))
        self.assertEqual(stats.guardrails(tiny), [])

    def test_p99_warns_before_the_cut_is_crossed(self):
        """컷을 넘긴 뒤 알리면 이미 병합을 놓친 회차다. 그 앞에서 한 번 말한다."""
        tight = self.diagnostics(Counter({0: 200, 15: 90, 18: 10}))
        found = stats.guardrails(tight)
        self.assertEqual([row["severity"] for row in found], ["warning"])
        self.assertIn("여유가 줄었다", found[0]["title"])

    def test_topn_retention_below_one_pages(self):
        # 감시하는 N 은 **실제로 거는 상한**이다(2026-08-22 부터 12). 여기 숫자를
        # 손으로 적으면 상한을 옮길 때 이 테스트만 조용히 어긋난다.
        diag = {
            "search_space": [],
            "top_n_retention": {
                "llm_approved_total": 132,
                "levels": [{"n": stats.ISSUE_CANDIDATE_TOP_N,
                            "llm_approved_kept": 129, "llm_approved_share": 0.977}],
            },
        }
        found = stats.guardrails(diag)
        self.assertEqual([row["id"] for row in found], ["issue-candidate:topn-retention"])
        self.assertIn("3건", found[0]["detail"])

    def test_drift_needs_a_baseline_and_keeps_its_sign(self):
        diag = self.diagnostics(Counter({0: 300}), merge_rate=0.09)
        self.assertEqual(stats.guardrails(diag), [], "기준선이 없으면 표류를 말할 수 없다")
        found = stats.guardrails(diag, baseline={"merge_rate": 0.17})
        self.assertEqual([row["id"] for row in found], ["issue-candidate:merge-rate-drift"])
        # 줄었는데 '+' 로 적히면 알림을 읽고도 어느 쪽인지 다시 열어 봐야 한다.
        self.assertIn("-47%", found[0]["detail"])

    def test_desync_is_reported_even_with_a_tiny_sample(self):
        """계측이 루프와 어긋나면 이 회차 수치 전체를 못 믿는다 — 표본 하한과 무관하다."""
        broken = self.diagnostics(Counter({0: 3}),
                                  landed_extra={"not_in_table": 4})
        found = stats.guardrails(broken)
        self.assertEqual([row["id"] for row in found], ["issue-candidate:telemetry-desync"])
        self.assertEqual(found[0]["severity"], "critical")

    def test_alert_keys_are_stable(self):
        """`id` 는 운영 알림의 중복 억제 키다. 바꾸면 쿨다운이 끊겨 같은 사고가
        매 회차 새 알림으로 온다. 이름을 바꿀 때는 그 사실을 알고 바꿀 것."""
        broken = self.diagnostics(Counter({0: 100, 25: 40}))
        self.assertTrue(all(row["id"].startswith("issue-candidate:")
                            for row in stats.guardrails(broken)))


class TelemetryTests(unittest.TestCase):
    def test_settle_resets_the_table_between_articles(self):
        """안 비우면 다음 기사의 예선 표에 앞 기사 점수가 섞여 순위가 부풀려진다."""
        telemetry = stats.SearchTelemetry("card")
        telemetry.pair("a1", "candidate", "i1", 0.9)
        telemetry.pair("a1", "candidate", "i2", 0.5)
        telemetry.settle("i1")
        telemetry.pair("a2", "candidate", "i3", 0.1)
        telemetry.settle("i3")
        summary = telemetry.summary()["preselect_rank"]
        self.assertEqual(summary["landed"], 2)
        self.assertEqual(summary["max"], 0, "두 기사 모두 1위여야 한다")

    def test_an_unlanded_article_is_counted_not_ranked(self):
        telemetry = stats.SearchTelemetry("evidence")
        telemetry.pair("a1", "no_context", "i1", 0.2)
        telemetry.settle(None)
        summary = telemetry.summary()["preselect_rank"]
        self.assertEqual((summary["landed"], summary["unlanded"]), (0, 1))

    def test_pairs_scored_equals_the_sum_of_outcomes(self):
        telemetry = stats.SearchTelemetry("card")
        for outcome in ("candidate", "no_context", "veto", "matched:tags"):
            telemetry.pair("a1", outcome, "i1", 0.3)
        summary = telemetry.summary()
        self.assertEqual(summary["pairs_scored"], 4)
        self.assertEqual(sum(summary["pair_outcomes"].values()), 4)


if __name__ == "__main__":
    unittest.main()
