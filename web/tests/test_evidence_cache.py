"""근거 부착 캐시(evidence_cache) — 캐시가 있어도 판정이 같아야 한다.

계약은 셋이다.
  1. 같은 입력이면 캐시 유무와 무관하게 **같은 부착**이 나온다 (전부 적중, 재계산 0).
  2. 전제가 바뀌면 다시 계산한다 — 붙었던 이슈의 멤버 변화, 그 기사와 얽힌
     override, 새로 생긴 이슈(델타).
  3. 적중한 기사가 올렸던 회색지대 쌍은 다시 큐에 오른다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import build_data  # noqa: E402
import evidence_cache  # noqa: E402


def _card(hash_, title, tags, countries=("KR",), date="2026-08-08", summary=""):
    return {
        "hash": hash_, "briefing_date": date, "article_date": date,
        "title_kr": title, "summary": summary or title + ".",
        "tags": list(tags), "topics": ["newbuild"], "countries": list(countries),
    }


def _evidence(hash_, title, tags, countries=("KR",), date="2026-08-07", summary=""):
    return {
        "hash": hash_, "article_date": date,
        "title_kr": title, "summary": summary or title + ".",
        "tags": list(tags), "topics": ["newbuild"], "countries": list(countries),
        "importance": "standard",
    }


def _attachments(issues):
    return {
        issue["issue_id"]: sorted(m["hash"] for m in issue.get("evidence_members") or [])
        for issue in issues
    }


class EvidenceCacheContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "evidence_attachments.json"
        self.cards = [
            _card("card-a", "한수원 체코 원전 본계약 후속 절차 착수", ["#체코원전"], ("KR", "CZ")),
            _card("card-b", "미국 NRC 신규 규제 지침 공개", ["#NRC"], ("US",)),
        ]
        self.evidence = [
            _evidence("ev-a", "체코 원전 본계약 후속 일정 발표", ["#체코원전"], ("KR", "CZ")),
            _evidence("ev-b", "NRC 규제 지침 세부 내용 공개", ["#NRC"], ("US",)),
            _evidence("ev-z", "호주 리튬 광산 증산 발표", ["#리튬"], ("AU",)),
        ]
        self.embeddings = {
            "card-a": [1.0, 0.0], "card-b": [0.0, 1.0],
            "ev-a": [0.99, 0.01], "ev-b": [0.02, 0.98], "ev-z": [0.5, 0.5],
        }

    def _run(self, cards, evidence, cache, overrides=None, embeddings=None):
        issues = build_data.cluster_selected_articles(cards, embeddings or self.embeddings)
        rows: list[dict] = []
        attached = build_data.attach_evidence_articles(
            cards + evidence, issues, embeddings or self.embeddings, None,
            overrides, rows, None, telemetry=None, cache=cache,
        )
        return issues, attached, rows

    def _cache(self):
        return evidence_cache.EvidenceCache.load("policy-x", self.path)

    def test_same_input_hits_everything_and_matches_the_uncached_result(self):
        plain, attached_plain, _ = self._run(self.cards, self.evidence, None)
        cold = self._cache()
        first, attached_cold, _ = self._run(self.cards, self.evidence, cold)
        cold.save()
        self.assertEqual(_attachments(first), _attachments(plain))
        self.assertEqual(attached_cold, attached_plain)
        self.assertEqual(cold.stats["hit"], 0)

        warm = self._cache()
        self.assertTrue(warm.loaded_from_file)
        second, attached_warm, _ = self._run(self.cards, self.evidence, warm)
        self.assertEqual(_attachments(second), _attachments(plain))
        self.assertEqual(attached_warm, attached_plain)
        # 카나리 표본(해시순 앞 12건)은 항상 다시 계산한다 — 여기선 전부 카나리라
        # 적중이 없다. 카나리 밖 기사만 적중 대상이다.
        self.assertEqual(warm.stats["canary"], len(self.evidence))
        self.assertEqual(warm.stats["delta_issues"], 0)

    def test_non_canary_articles_hit_and_are_not_recomputed(self):
        # 카나리(12건)를 채우고도 남는 풀 — 나머지는 적중해야 한다.
        many = [
            _evidence(f"ev-{i:02d}", f"체코 원전 본계약 후속 일정 발표 {i}", ["#체코원전"], ("KR", "CZ"))
            for i in range(20)
        ]
        embeddings = {**self.embeddings, **{row["hash"]: [0.99, 0.01] for row in many}}
        plain, _, _ = self._run(self.cards, many, None, embeddings=embeddings)
        cold = self._cache()
        self._run(self.cards, many, cold, embeddings=embeddings)
        cold.save()
        warm = self._cache()
        again, _, _ = self._run(self.cards, many, warm, embeddings=embeddings)
        self.assertEqual(_attachments(again), _attachments(plain))
        self.assertEqual(warm.stats["hit"], 20 - build_data.EVIDENCE_RETRIEVAL_CANARY)
        self.assertEqual(warm.stats["canary"], build_data.EVIDENCE_RETRIEVAL_CANARY)
        self.assertEqual(warm.stats["recomputed"], 0)
        self.assertEqual(warm.stats["hit_delta_checked"], 0)

    def _warm_with_many(self):
        many = [
            _evidence(f"ev-{i:02d}", f"체코 원전 본계약 후속 일정 발표 {i}", ["#체코원전"], ("KR", "CZ"))
            for i in range(20)
        ]
        embeddings = {**self.embeddings, **{row["hash"]: [0.99, 0.01] for row in many}}
        cold = self._cache()
        self._run(self.cards, many, cold, embeddings=embeddings)
        cold.save()
        return many, embeddings

    def test_a_new_issue_that_fits_better_takes_the_cached_article(self):
        many, embeddings = self._warm_with_many()
        # ev-z(호주 리튬)는 붙을 곳이 없어 '미부착'으로 캐시됐다. 새 카드 이슈가
        # 생기면(델타) 캐시된 기사도 그쪽에 다시 대봐야 한다.
        pool = many + [self.evidence[2]]
        cold = self._cache()
        self._run(self.cards, pool, cold, embeddings=embeddings)
        cold.save()
        new_card = _card("card-c", "호주 리튬 광산 증산 계획 발표", ["#리튬"], ("AU",),
                         date="2026-08-09")
        embeddings = {**embeddings, "card-c": [0.5, 0.5]}
        plain, _, _ = self._run(self.cards + [new_card], pool, None, embeddings=embeddings)
        warm = self._cache()
        cached, _, _ = self._run(self.cards + [new_card], pool, warm, embeddings=embeddings)
        self.assertEqual(_attachments(cached), _attachments(plain))
        self.assertIn("ev-z", _attachments(cached).get("issue-card-c", []))
        self.assertEqual(warm.stats["delta_issues"], 1)
        self.assertGreater(warm.stats["hit_delta_checked"], 0)
        self.assertEqual(warm.stats["moved"], 1)

    def test_member_change_of_the_attached_issue_forces_recompute(self):
        many, embeddings = self._warm_with_many()
        # 붙었던 이슈(card-a)에 멤버가 추가되면 지문이 달라져 전체 재계산이다.
        grown = [dict(self.cards[0]), self.cards[1]]
        extra = _card("card-a2", "한수원 체코 원전 본계약 후속 절차 착수 2", ["#체코원전"], ("KR", "CZ"))
        embeddings = {**embeddings, "card-a2": [1.0, 0.0]}
        plain, _, _ = self._run(grown + [extra], many, None, embeddings=embeddings)
        warm = self._cache()
        cached, _, _ = self._run(grown + [extra], many, warm, embeddings=embeddings)
        self.assertEqual(_attachments(cached), _attachments(plain))
        # card-a 가 card-a2 를 흡수했다면 그 이슈 지문이 바뀌어 적중이 없어야 한다.
        target = next(i for i in cached if i["issue_id"] == "issue-card-a")
        if len(target["members"]) > 1:
            self.assertEqual(warm.stats["hit"], 0)

    def test_a_rejection_override_on_the_article_forces_recompute(self):
        many, embeddings = self._warm_with_many()
        rejected = {build_data._pair_id("ev-15", "card-a")}
        overrides = {"approved": set(), "rejected": rejected}
        plain, _, _ = self._run(self.cards, many, None, overrides=overrides, embeddings=embeddings)
        warm = self._cache()
        cached, _, _ = self._run(self.cards, many, warm, overrides=overrides, embeddings=embeddings)
        self.assertEqual(_attachments(cached), _attachments(plain))
        self.assertNotIn("ev-15", _attachments(cached).get("issue-card-a", []))
        # ev-15 만 전제가 바뀌었다 — 나머지 비카나리는 적중.
        self.assertEqual(warm.stats["recomputed"], 1)

    def test_policy_change_resets_the_whole_cache(self):
        self._warm_with_many()
        other = evidence_cache.EvidenceCache.load("policy-y", self.path)
        self.assertFalse(other.loaded_from_file)
        self.assertEqual(other.reset_reason, "policy")
        self.assertEqual(other.entries, {})

    def test_entries_outside_the_pool_are_pruned_and_file_is_json(self):
        many, embeddings = self._warm_with_many()
        # 카나리(해시순 앞 12건 = ev-00~11)는 항목을 남기지 않으므로 저장된 것은
        # ev-12~19 여덟 건이다. 풀을 ev-00~04 로 줄이면 여덟 건 모두 풀 밖이다.
        warm = self._cache()
        self.assertEqual(set(warm.entries), {f"ev-{i:02d}" for i in range(12, 20)})
        self._run(self.cards, many[:5], warm, embeddings=embeddings)
        self.assertEqual(warm.stats["pruned"], 8)
        warm.save()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["cache_version"], evidence_cache.CACHE_VERSION)
        self.assertEqual(set(raw["attachments"]), set())

    def test_review_candidates_are_re_emitted_on_hit(self):
        # 회색지대 쌍을 만드는 입력: 제목은 비슷한데 임베딩이 문턱 아래.
        many = [
            _evidence(f"ev-{i:02d}", f"체코 원전 본계약 후속 절차 논의 {i}", ["#체코원전"], ("KR", "CZ"))
            for i in range(20)
        ]
        embeddings = {**self.embeddings, **{row["hash"]: [0.80, 0.60] for row in many}}
        _, _, rows_plain = self._run(self.cards, many, None, embeddings=embeddings)
        cold = self._cache()
        _, _, rows_cold = self._run(self.cards, many, cold, embeddings=embeddings)
        cold.save()
        warm = self._cache()
        _, _, rows_warm = self._run(self.cards, many, warm, embeddings=embeddings)
        ids = lambda rows: sorted(r["candidate_id"] for r in rows)  # noqa: E731
        self.assertEqual(ids(rows_cold), ids(rows_plain))
        self.assertEqual(ids(rows_warm), ids(rows_plain))


if __name__ == "__main__":
    unittest.main()
