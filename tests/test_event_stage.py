"""사건 중심 파이프라인 회귀 — 2026-08-16.

이 저장소의 V2 철학은 '상태 변화는 별도 사건'이다. 그런데 두 곳이 그 철학과
어긋나 있었다.

  ① 수집 단계가 중복을 **삭제**했다. story 가 만들어질 무렵에는 매체 수·근거 수가
     이미 실제보다 작았고, 그래서 story_outlet_count 로 '복수 출처 확인'을
     말할 수 없었다.
  ② V1 에서 가져온 빠른 제목 중복 알고리즘은 단계 개념이 없다. '심사 착수'와
     '최종 승인'처럼 제목이 닮은 단계 전환이 AI story 판정 전에 접혀 사라졌다 —
     하필 가장 중요한 뉴스가.

여기 있는 테스트는 그 둘이 되돌아오는 것을 막는다.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_k, "test-dummy")

import dedup  # noqa: E402
import event_stage  # noqa: E402
import news_bot as nb  # noqa: E402
import ranking  # noqa: E402
import story_cluster  # noqa: E402

NOW = datetime(2026, 8, 16, tzinfo=timezone.utc)


def item(h, title, **kw):
    base = {
        "hash": h,
        "title": title,
        "title_kr": title,
        "link": f"https://example.com/{h}",
        "domain": "example.com",
        "publisher": "예시일보",
        "importance": "nice_to_know",
        "section": "domestic",
        "tags": ["#계속운전"],
        "queued_at": NOW.isoformat(),
        "summary": "",
        "detail": "",
    }
    base.update(kw)
    return base


def candidate(title, *, link, score=10, publisher="예시일보", domain="example.com"):
    """news_bot 수집 후보 모양 (큐레이션 전이라 title/link/score 만 있다)."""
    return {"title": title, "link": link, "score": score, "publisher": publisher,
            "domain": domain, "feed": "테스트", "matched": [], "pub": "2026-08-16"}


# ---------------------------------------------------------------------------
# ① 단계 판정 자체
# ---------------------------------------------------------------------------

class TestStageDetection(unittest.TestCase):
    def test_review_and_approval_are_different_stages(self):
        review = event_stage.detect_stages("원안위, 고리2호기 계속운전 심사 착수")
        approval = event_stage.detect_stages("원안위, 고리2호기 계속운전 최종 승인")
        self.assertIn("review", review)
        self.assertIn("approval", approval)
        self.assertTrue(event_stage.stage_conflict(review, approval))

    def test_shutdown_and_restart_are_different_stages(self):
        stop = event_stage.detect_stages("한빛 3호기 가동 중단")
        back = event_stage.detect_stages("한빛 3호기 재가동")
        self.assertTrue(event_stage.stage_conflict(stop, back))

    def test_spacing_variants_are_the_same_stage(self):
        self.assertEqual(event_stage.detect_stages("가동 중단"),
                         event_stage.detect_stages("가동중단"))

    def test_missing_marker_never_vetoes(self):
        """한쪽이 단계를 말하지 않으면 판정하지 않는다 — 기존 동작 유지."""
        stop = event_stage.detect_stages("프랑스 EDF 원전 6기, 폭염으로 가동 중단")
        none = event_stage.detect_stages("장기 폭염·고수온·유량 감소가 프랑스 원전 운영 위협")
        self.assertFalse(none)
        self.assertFalse(event_stage.stage_conflict(stop, none))

    def test_two_stage_phrase_intersects_both(self):
        """'심사 결과 승인'처럼 두 단계에 걸친 표현은 어느 쪽과도 충돌하지 않는다.

        어휘를 넓게 잡는 것이 안전한 방향이라는 설계 전제 — 집합이 커지면 교집합이
        생겨 거부권이 덜 발동한다.
        """
        both = event_stage.detect_stages("원안위, 고리2호기 계속운전 심사 결과 승인")
        self.assertFalse(event_stage.stage_conflict(
            both, event_stage.detect_stages("고리2호기 계속운전 심사 착수")))
        self.assertFalse(event_stage.stage_conflict(
            both, event_stage.detect_stages("고리2호기 계속운전 승인")))

    def test_english_patterns_need_word_boundaries(self):
        self.assertIn("shutdown", event_stage.detect_stages("EDF units taken offline"))
        self.assertIn("approval", event_stage.detect_stages("NRC approved the design"))
        # 'restart' 가 다른 단어 속에 묻혀 있으면 잡지 않는다.
        self.assertNotIn("restart", event_stage.detect_stages("prerestarted nonsense"))


# ---------------------------------------------------------------------------
# ② ranking.cluster_duplicates — 단계가 다르면 중복 처리 금지
# ---------------------------------------------------------------------------

class TestClusterDuplicatesStageVeto(unittest.TestCase):
    def test_review_to_approval_is_not_folded(self):
        """핵심 회귀: 제목이 닮아도 심사→승인은 별도 사건이다."""
        a = item("a", "원안위, 고리2호기 계속운전 심사 착수 결정")
        b = item("b", "원안위, 고리2호기 계속운전 승인 의결")
        # 전제: 제목 유사도만 보면 같은 사건으로 붙는다
        self.assertTrue(ranking._same_event(
            ranking._norm_title(a), ranking._title_tokens(a),
            ranking._norm_title(b), ranking._title_tokens(b), 0.82)
            or ranking._same_facility_event(
                ranking._title_facilities(a), ranking._norm_tags(a),
                ranking._title_facilities(b), ranking._norm_tags(b)))
        vetoes: list[dict] = []
        kept, dropped = ranking.cluster_duplicates([a, b], {"a": 9, "b": 8}, vetoes=vetoes)
        self.assertEqual({x["hash"] for x in kept}, {"a", "b"})
        self.assertEqual(dropped, [])
        self.assertEqual(len(vetoes), 1)
        self.assertEqual(vetoes[0]["kind"], "event_stage")
        self.assertIn("심사", vetoes[0]["explanation"])

    def test_shutdown_to_restart_is_not_folded(self):
        a = item("a", "한빛 3호기 계획예방정비로 가동 중단", tags=["#가동"])
        b = item("b", "한빛 3호기 계획예방정비 마치고 재가동", tags=["#가동"])
        kept, _ = ranking.cluster_duplicates([a, b], {"a": 9, "b": 8})
        self.assertEqual(len(kept), 2)

    def test_same_stage_still_folds(self):
        """거부권은 단계가 **갈릴 때만** 선다 — 같은 단계 중복은 예전처럼 접힌다."""
        a = item("a", "한수원, 체코 두코바니 원전 본계약 체결")
        b = item("b", "한수원 체코 두코바니 원전 본계약을 체결했다")
        kept, dropped = ranking.cluster_duplicates([a, b], {"a": 9, "b": 8})
        self.assertEqual([x["hash"] for x in kept], ["a"])
        self.assertEqual(dropped[0]["dup_of"], "a")

    def test_veto_does_not_block_a_valid_later_match(self):
        """단계가 갈린 대표를 만나도 스캔을 멈추지 않는다.

        예전 코드는 첫 후보에서 break 했다. 거부권을 continue 로 처리하지 않으면
        '승인 기사 뒤에 온 심사 중복'이 아무 데도 안 붙어 두 칸을 먹는다.
        """
        approval = item("a", "원안위, 고리2호기 계속운전 승인 의결")
        review = item("b", "원안위, 고리2호기 계속운전 심사 착수 결정")
        review_dup = item("c", "원안위 고리2호기 계속운전 심사 착수를 결정했다")
        kept, dropped = ranking.cluster_duplicates(
            [approval, review, review_dup], {"a": 9, "b": 8, "c": 7})
        self.assertEqual({x["hash"] for x in kept}, {"a", "b"})
        self.assertEqual(dropped[0]["dup_of"], "b")


class TestDedupStageBackstop(unittest.TestCase):
    """LLM 이 '단순 재전재'라면서 단계를 넘겨 묶으면 되돌린다."""

    def test_duplicate_relation_cannot_cross_a_stage(self):
        a = item("a", "고리2호기 계속운전 심사 착수")
        b = item("b", "고리2호기 계속운전 승인")
        groups = {"groups": [{"indices": [0, 1], "relation": "duplicate",
                              "reason": "같은 기사", "fingerprint": {}}]}
        with patch.object(dedup, "is_available", return_value=True), \
                patch.object(dedup, "call_json", return_value=groups):
            kept, dropped = dedup.dedup_articles([a, b], {"a": 9, "b": 8})
        self.assertEqual({x["hash"] for x in kept}, {"a", "b"})
        self.assertEqual(dropped, [])
        self.assertEqual(kept[0]["story_stage_vetoes"][0]["kind"], "event_stage")

    def test_merge_relation_is_left_to_the_model(self):
        """relation='merge'(보완 분석)는 건드리지 않는다 — 제목 밖 맥락은 모델이 낫다."""
        a = item("a", "프랑스 EDF 원전 6기 폭염으로 가동 중단")
        b = item("b", "장기 폭염·고수온이 프랑스 원전 운영을 위협한다")
        groups = {"groups": [{"indices": [0, 1], "relation": "merge",
                              "reason": "같은 EDF 폭염 이슈", "fingerprint": {}}]}
        with patch.object(dedup, "is_available", return_value=True), \
                patch.object(dedup, "call_json", return_value=groups):
            kept, dropped = dedup.dedup_articles([a, b], {"a": 9, "b": 8})
        self.assertEqual([x["hash"] for x in kept], ["a"])
        self.assertEqual(len(dropped), 1)


# ---------------------------------------------------------------------------
# ③ 수집 단계 — 지우지 않고 raw_sources 로 넘긴다
# ---------------------------------------------------------------------------

class TestCollectionKeepsEvidence(unittest.TestCase):
    def test_exact_title_duplicate_becomes_a_raw_source(self):
        rows = nb.dedup_exact_candidates([
            candidate("한수원, 체코 두코바니 본계약 체결", link="https://a.example/1",
                      score=20, publisher="A일보", domain="a.example"),
            candidate("한수원, 체코 두코바니 본계약 체결", link="https://b.example/1",
                      score=10, publisher="B신문", domain="b.example"),
        ])
        self.assertEqual(len(rows), 1)
        raws = story_cluster.raw_sources_of(rows[0])
        self.assertEqual(len(raws), 1)
        self.assertEqual(raws[0]["publisher"], "B신문")
        self.assertEqual(raws[0]["fold_stage"], "collect_title")

    def test_same_url_does_not_inflate_outlet_count(self):
        """같은 URL 을 두 경로로 받은 것은 두 매체 보도가 아니다."""
        rows = nb.dedup_exact_candidates([
            candidate("같은 기사", link="https://a.example/1?utm_source=x", score=20),
            candidate("같은 기사", link="https://a.example/1", score=10),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(story_cluster.raw_sources_of(rows[0]), [])

    def test_fuzzy_fold_preserves_the_other_outlet(self):
        rep = candidate("한수원, 체코 두코바니 원전 본계약 체결", link="https://a.example/1",
                        publisher="A일보", domain="a.example")
        rep["hash"] = "a"
        other = candidate("한수원 체코 두코바니 원전 본계약을 체결했다",
                          link="https://b.example/1", publisher="B신문", domain="b.example")
        other["hash"] = "b"
        story_cluster.attach_raw_source(rep, other, stage="collect_fuzzy_title",
                                        reason="제목 유사")
        story_cluster.consolidate_story_metadata(rep, [rep], relation="collected",
                                                 stage="collect_fold")
        self.assertEqual(rep["story_outlet_count"], 2)
        self.assertEqual(rep["story_article_count"], 2)
        self.assertIn("한수원 체코 두코바니 원전 본계약을 체결했다",
                      rep["story_related_titles"])

    def test_folded_evidence_survives_a_later_story_merge(self):
        """수집 단계 근거가 선정 단계 병합을 통과해 대표까지 간다.

        이 사슬이 끊어지면 story_outlet_count 는 다시 '선정 단계에서 본 매체 수'로
        줄고, 복수 출처 확인 지표로 쓸 수 없게 된다.
        """
        a = item("a", "한수원, 체코 두코바니 원전 본계약 체결",
                 publisher="A일보", domain="a.example")
        b = item("b", "한수원 체코 두코바니 원전 본계약을 체결했다",
                 publisher="B신문", domain="b.example")
        story_cluster.attach_raw_source(
            a, item("a2", "두코바니 본계약 체결", publisher="C데일리", domain="c.example"),
            stage="collect_fuzzy_title")
        story_cluster.attach_raw_source(
            b, item("b2", "두코바니 본계약 소식", publisher="D뉴스", domain="d.example"),
            stage="collect_fuzzy_title")
        kept, _ = ranking.cluster_duplicates([a, b], {"a": 9, "b": 8})
        self.assertEqual(len(kept), 1)
        rep = kept[0]
        outlets = {s["publisher"] for s in rep["story_sources"]}
        self.assertEqual(outlets, {"A일보", "B신문", "C데일리", "D뉴스"})
        self.assertEqual(rep["story_outlet_count"], 4)

    def test_raw_sources_are_capped_but_counted(self):
        rep = item("rep", "대표 기사")
        for i in range(story_cluster.RAW_SOURCE_LIMIT + 3):
            story_cluster.attach_raw_source(
                rep, item(f"x{i}", f"중복 {i}", domain=f"d{i}.example",
                          publisher=f"매체{i}"),
                stage="collect_fuzzy_title")
        self.assertEqual(len(rep["raw_sources"]), story_cluster.RAW_SOURCE_LIMIT)
        self.assertEqual(rep["raw_sources_truncated"], 3)


class TestSemanticDedupStageVeto(unittest.TestCase):
    def test_embedding_similarity_cannot_cross_a_stage(self):
        """임베딩은 어휘가 겹치면 높게 나온다 — 단계 전환은 바로 그 위에서 일어난다."""
        a = candidate("고리2호기 계속운전 심사 착수", link="https://a.example/1", score=20)
        a["hash"] = "a"
        b = candidate("고리2호기 계속운전 승인", link="https://b.example/1", score=10)
        b["hash"] = "b"
        vetoes: list[dict] = []
        with patch.object(nb, "get_or_compute_embedding",
                                        side_effect=lambda *_a, **_k: [1.0, 0.0]), \
                patch.object(nb.time, "sleep", lambda *_a: None):
            kept = nb.semantic_dedup([a, b], {}, vetoes=vetoes)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(vetoes), 1)

    def test_same_stage_is_folded_not_deleted(self):
        a = candidate("체코 두코바니 본계약 체결", link="https://a.example/1", score=20,
                      publisher="A일보", domain="a.example")
        a["hash"] = "a"
        b = candidate("두코바니 원전 본계약 체결 소식", link="https://b.example/1", score=10,
                      publisher="B신문", domain="b.example")
        b["hash"] = "b"
        with patch.object(nb, "get_or_compute_embedding",
                                        side_effect=lambda *_a, **_k: [1.0, 0.0]), \
                patch.object(nb.time, "sleep", lambda *_a: None):
            kept = nb.semantic_dedup([a, b], {})
        self.assertEqual(len(kept), 1)
        self.assertEqual(story_cluster.raw_sources_of(kept[0])[0]["publisher"], "B신문")


# ---------------------------------------------------------------------------
# ④ 화면용 대표는 story 가 완성된 뒤에 고른다
# ---------------------------------------------------------------------------

class TestDisplayRepresentative(unittest.TestCase):
    def test_tie_keeps_the_incumbent(self):
        """동점이면 유지한다 — 아무것도 더 낫지 않은데 대표가 바뀌면 안 된다."""
        a = item("a", "같은 사건", summary="요약")
        b = item("b", "같은 사건 후속", summary="요약")
        winner, reason = story_cluster.choose_display_representative(
            [a, b], {"a": 5.0, "b": 5.0}, current=a)
        self.assertIs(winner, a)
        self.assertEqual(reason, "keep")

    def test_article_with_body_wins_within_the_score_band(self):
        thin = item("a", "같은 사건", summary="한 줄")
        thick = item("b", "같은 사건 상세", summary="한 줄",
                     detail="본문 요지가 충분히 길게 들어 있는 기사 " * 3)
        winner, reason = story_cluster.choose_display_representative(
            [thin, thick], {"a": 6.0, "b": 5.0}, current=thin)
        self.assertIs(winner, thick)
        self.assertIn("본문", reason)

    def test_score_gap_blocks_the_swap(self):
        thin = item("a", "같은 사건", summary="한 줄")
        thick = item("b", "같은 사건 상세", detail="본문 " * 40)
        winner, reason = story_cluster.choose_display_representative(
            [thin, thick], {"a": 20.0, "b": 5.0}, current=thin)
        self.assertIs(winner, thin)
        self.assertEqual(reason, "keep_score_gap")

    def test_promotion_moves_the_story_evidence_and_dup_pointers(self):
        thin = item("a", "한수원 체코 두코바니 본계약 체결", summary="한 줄",
                    publisher="A일보", domain="a.example")
        thick = item("b", "한수원 체코 두코바니 원전 본계약을 체결했다",
                     detail="계약 규모와 일정, 후속 절차를 상세히 적은 본문 " * 3,
                     publisher="B신문", domain="b.example")
        scores = {"a": 6.0, "b": 5.5}
        kept, dropped = ranking.cluster_duplicates([thin, thick], scores)
        self.assertEqual([x["hash"] for x in kept], ["a"])
        out, promotions = ranking._pick_display_representatives(
            kept, [thin, thick], scores, dropped)
        self.assertEqual([x["hash"] for x in out], ["b"])
        self.assertEqual(promotions[0]["from_hash"], "a")
        # story 근거가 통째로 새 대표에게 넘어간다 (매체 수가 줄면 안 된다)
        self.assertEqual(out[0]["story_outlet_count"], 2)
        self.assertEqual(out[0]["story_article_count"], 2)
        # 물러난 대표와 접힌 기사 모두 새 대표를 가리켜야 큐 정리가 동작한다
        self.assertEqual({d["dup_of"] for d in dropped}, {"b"})
        self.assertIn("a", {d["hash"] for d in dropped})


if __name__ == "__main__":
    unittest.main()
