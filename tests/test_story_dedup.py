"""Story-level dedup/coverage regression tests added in 2026-08-14 redesign."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import dedup
import ranking
import story_cluster

NOW = datetime(2026, 8, 14, 0, 0, tzinfo=timezone.utc)
CFG = ranking.load_config()


def feat(event_type="other", **kw):
    base = {"event_type": event_type, "korea_relevance": 0, "market_materiality": 0,
            "policy_materiality": 0, "novelty": 0, "evidence_strength": 0,
            "report_worthiness": 0}
    base.update(kw)
    return base


def item(h, title, *, publisher="Le Monde", domain="lemonde.fr", source_tier=1,
         summary="", detail="", score_event="other"):
    return {
        "hash": h,
        "title": title,
        "title_kr": title,
        "publisher": publisher,
        "domain": domain,
        "source_tier": source_tier,
        "link": f"https://{domain}/{h}",
        "importance": "nice_to_know",
        "section": "international",
        "scope": "overseas",
        "summary": summary,
        "detail": detail,
        "features": feat(score_event, policy_materiality=1),
        "queued_at": NOW.isoformat(),
        "related_reports": [],
        "tags": ["#프랑스", "#원전"],
        "event_date": "2026-08-13",
    }


class TestStoryContextPrompt(unittest.TestCase):
    def test_payload_uses_summary_and_detail_not_title_only(self):
        a = item("a", "프랑스 EDF 원전 6기, 폭염으로 가동중단",
                 summary="폭염으로 EDF 원전 6기가 가동 차질을 겪었다.",
                 detail="고수온과 냉각수 제약이 원전 가용성에 영향을 줬다.")
        text = dedup._article_block(0, a)
        self.assertIn("SUMMARY: 폭염으로 EDF 원전 6기가", text)
        self.assertIn("DETAIL: 고수온과 냉각수 제약", text)
        self.assertIn("EVENT:", text)
        self.assertIn("EVENT_DATE", dedup.ARTICLE_STORY_PROMPT)

    def test_edf_heatwave_pair_is_consolidated_when_story_model_groups_it(self):
        a = item(
            "a", "르몽드 프랑스 EDF 원전 6기, 폭염으로 가동중단",
            summary="프랑스 폭염으로 EDF 원전 6기가 가동 중단 또는 출력 제약을 받았다.",
            detail="높은 기온과 냉각수 온도 상승으로 원전 운영에 제약이 발생했다.")
        b = item(
            "b", "프랑스 원전 가동 중단: 장기폭염, 고수온, 유량 감소가 원전 운영 위협 - 르몽드",
            summary="장기 폭염과 고수온, 하천 유량 감소가 프랑스 원전 운영을 위협하고 있다.",
            detail="EDF 원전의 냉각수 조건 악화가 같은 폭염 상황에서 가동 차질을 키웠다.")
        c = item("c", "캐나다 다링턴 SMR 공사 새 단계 진입", publisher="WNN",
                 domain="world-nuclear-news.org", summary="별개의 캐나다 SMR 프로젝트 뉴스")

        old_avail, old_call = dedup.is_available, dedup.call_json
        dedup.is_available = lambda: True
        dedup.call_json = lambda *args, **kwargs: {
            "groups": [
                {"indices": [0, 1], "relation": "merge",
                 "reason": "같은 EDF 폭염·고수온 원전 가동제약 story",
                 "fingerprint": {"countries": ["France"], "actors": ["EDF"],
                                 "event_family": "operational_constraint",
                                 "drivers": ["heatwave", "high water temperature"],
                                 "event_date": "2026-08-13"}},
                {"indices": [2], "relation": "single", "reason": "", "fingerprint": {}},
            ]
        }
        try:
            kept, dropped = dedup.dedup_articles([a, b, c], {"a": 20, "b": 18, "c": 15})
        finally:
            dedup.is_available, dedup.call_json = old_avail, old_call

        self.assertEqual([x["hash"] for x in kept], ["a", "c"])
        self.assertEqual(dropped[0]["hash"], "b")
        self.assertEqual(dropped[0]["dup_of"], "a")
        self.assertEqual(a["story_article_count"], 2)
        # Same Le Monde outlet should count once, exactly like Daily News's per-outlet rule.
        self.assertEqual(a["story_outlet_count"], 1)
        self.assertEqual(len(a["story_related_titles"]), 2)
        self.assertEqual(a["story_fingerprint"]["actors"], ["EDF"])


class TestCoverageSignal(unittest.TestCase):
    def test_multiple_independent_outlets_are_bonus_not_gate(self):
        # Locally similar titles are merged before Gemini.  Two independent tier1 outlets should
        # add +0.4 outlet bonus and +0.8 multi-tier1 bonus, but a single-source story gets no penalty.
        a = item("a", "EDF 프랑스 원전 폭염 가동중단 발표", publisher="Reuters",
                 domain="reuters.com", source_tier=1)
        b = item("b", "EDF 프랑스 원전 폭염 가동중단 발표", publisher="World Nuclear News",
                 domain="world-nuclear-news.org", source_tier=1)
        single = item("s", "독립적인 단독 공식 원전 정책 발표", publisher="IAEA",
                      domain="iaea.org", source_tier=1)

        selected, diag = ranking.rank_and_select([a, b, single], 3, CFG, NOW)
        self.assertEqual(a["story_outlet_count"], 2)
        self.assertAlmostEqual(diag["breakdowns"]["a"]["coverage:outlets"], 0.4)
        self.assertAlmostEqual(diag["breakdowns"]["a"]["coverage:multi_tier1"], 0.8)
        self.assertNotIn("coverage:outlets", diag["breakdowns"]["s"])
        self.assertIn("s", {x["hash"] for x in selected})  # single-source official item is not gated out


class TestEditorialBackfill(unittest.TestCase):
    def test_final_duplicate_removal_backfills_next_story(self):
        rows = [
            item("a", "프랑스 폭염 원전 운영 제약"),
            item("b", "EDF 냉각수 고수온으로 원전 출력 제한"),
            item("c", "캐나다 SMR 규제 승인"),
            item("d", "미국 농축시설 투자 결정"),
        ]

        def semantic_noop(arts, scores):
            return list(arts), []

        def editorial(arts, scores):
            # Simulate the final editor recognizing b as the same briefing story as a.
            keep = [x for x in arts if x["hash"] != "b"]
            gone = [dict(x, dup_of="a", dup_reason="merge") for x in arts if x["hash"] == "b"]
            return keep, gone

        selected, diag = ranking.rank_and_select(
            rows, 3, CFG, NOW, semantic_dedup=semantic_noop, editorial_dedup=editorial)
        hashes = [x["hash"] for x in selected]
        self.assertEqual(len(hashes), 3)
        self.assertNotIn("b", hashes)
        self.assertIn("d", hashes)  # the duplicate did not consume a final briefing slot
        self.assertIn("b", {x["hash"] for x in diag["dropped_duplicates"]})


class TestRepresentativeOwnership(unittest.TestCase):
    def test_one_article_cannot_represent_two_surviving_stories(self):
        shared = item("shared", "공유된 고품질 대표 기사", publisher="Reuters",
                      domain="reuters.com", source_tier=1)
        left = item("left", "서로 다른 사건 A", source_tier=3)
        right = item("right", "서로 다른 사건 B", source_tier=3)
        left["story_article_hashes"] = ["left", "shared"]
        right["story_article_hashes"] = ["right", "shared"]
        left["story_members"] = [{"hash": "left"}, {"hash": "shared"}]
        right["story_members"] = [{"hash": "right"}, {"hash": "shared"}]

        conflicts = []
        picked, _ = ranking._pick_display_representatives(
            [left, right], [left, right, shared],
            {"left": 10.0, "right": 9.0, "shared": 20.0}, [], conflicts)

        self.assertEqual(len({row["hash"] for row in picked}), 2)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["article_hash"], "shared")
        owners = [row for row in (left, right)
                  if "shared" in (row.get("story_article_hashes") or [])]
        self.assertEqual(len(owners), 1)

    def test_duplicate_story_id_is_rejected_and_next_story_backfills(self):
        rows = [
            item("a", "사건 A"),
            item("b", "사건 A의 다른 대표"),
            item("c", "사건 C"),
        ]
        rows[0]["story_id"] = rows[1]["story_id"] = "story-one"
        rows[2]["story_id"] = "story-three"
        selected, violations = ranking._select_with_identity_invariants(
            rows, {"a": 30.0, "b": 20.0, "c": 10.0}, 2, CFG)
        self.assertEqual([row["hash"] for row in selected], ["a", "c"])
        self.assertEqual(violations[0]["kind"], "story_id")
        self.assertEqual(violations[0]["resolution"], "backfill_next_distinct_story")


def _story(own, member_hashes, *, article_hashes=None):
    """story 메타를 단 기사 하나. hash 만 중요하다."""
    row = {"hash": own, "title_kr": own, "story_members": [
        {"hash": h, "title": h, "publisher": "x", "fold_stage": "collect_fold"}
        for h in member_hashes]}
    if article_hashes is not None:
        row["story_article_hashes"] = list(article_hashes)
    return row


class TestEvidenceOverlap(unittest.TestCase):
    """근거 교집합 — 어휘를 안 타는 '같은 사건' 판정 재료."""

    def test_member_hashes_do_not_rely_on_story_article_hashes(self):
        """실측 2026-08-22 — 두 목록이 어긋났고 빠진 쪽에 전날 카드가 있었다.

        `_article_hashes()` 는 이미 `story_article_hashes` 를 가진 멤버의 자기
        hash 를 다시 넣지 않는다. 그래서 그날 8/22 카드의 story_article_hashes 는
        12건, story_members 는 14건이었고 **빠진 2건 중 하나가 전날 카드**였다.
        근거를 세는 곳은 members 여야 한다.
        """
        row = _story("today", ["today", "yesterday", "e1"], article_hashes=["today", "e1"])
        self.assertIn("yesterday", story_cluster.member_hashes(row))

    def test_old_records_without_members_still_count(self):
        row = {"hash": "a", "story_article_hashes": ["a", "e1", "e2"]}
        self.assertEqual(story_cluster.member_hashes(row), frozenset({"a", "e1", "e2"}))

    def test_ratio_separates_a_repeat_from_a_follow_up(self):
        """실측에서 갈린 자리 — 0.188(진짜 후속) 과 0.857(같은 사건) 사이가 비어 있다."""
        prior = _story("p", ["p"] + [f"e{i}" for i in range(1, 12)]
                       + ["y1", "y2", "y3", "y4"])
        repeat = _story("c", ["c", "p"] + [f"e{i}" for i in range(1, 13)])
        follow_up = _story("f", ["f", "p", "e1", "e2"] + [f"z{i}" for i in range(1, 13)])

        wide = story_cluster.evidence_overlap(repeat, prior)
        self.assertEqual(wide.shared, 12)
        self.assertEqual(wide.candidate_total, 14)
        self.assertGreaterEqual(wide.ratio, 0.85)

        thin = story_cluster.evidence_overlap(follow_up, prior)
        self.assertEqual(thin.shared, 3)
        self.assertLessEqual(thin.ratio, 0.2)
        # 둘 다 상대 카드를 근거로 들고 있다 — cross_cited 단독으로는 못 가른다.
        self.assertTrue(wide.cross_cited)
        self.assertTrue(thin.cross_cited)

    def test_ratio_is_measured_against_todays_evidence(self):
        """분모는 오늘 근거 수다. 어제 story 가 컸다고 재탕이 덜 재탕이 되지 않는다."""
        prior = _story("p", ["p"] + [f"e{i}" for i in range(1, 40)])
        today = _story("c", ["c", "e1", "e2", "e3"])
        self.assertEqual(story_cluster.evidence_overlap(today, prior).ratio, 0.75)

    def test_empty_story_is_not_an_overlap(self):
        self.assertEqual(
            story_cluster.evidence_overlap({}, _story("p", ["p", "e1"])).shared, 0)


if __name__ == "__main__":
    unittest.main()
