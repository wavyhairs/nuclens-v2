"""회차 간 같은-제목 접기(news_bot.fold_cross_run_duplicates) 테스트. 외부 호출 0.

회귀 방지 대상 (2026-10-09):
- 같은 기사가 네이버·Google News 경로로 3시간 간격으로 들어와 두 해시로 두 번
  큐레이션되고, 발송된 해시가 아카이브에 없어 웹에서 사라지던 문제
  (월성 2~4호기 계속운전, 10/9 국내 2위).
"""

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent))

for _k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_k, "test-dummy")
import news_archive  # noqa: E402
import news_bot as nb  # noqa: E402

TITLE = "월성 2~4호기, 계속운전 신청 임박…2호기는 내달 정지"
GNEWS = "https://news.google.com/rss/articles/CBMieEFVX3lx?oc=5"
DIRECT = "https://www.newsis.com/view/NISX20261007_0003817907"
DESC = "한수원이 월성 2~4호기 계속운전 신청을 연내 마무리할 계획이나, 월성 2호기는 내달 1일 정지된다."


def _article(hash_value, link, description=""):
    return {"hash": hash_value, "title": TITLE, "link": link, "description": description,
            "feed": "정책", "domain": "newsis.com", "matched": "x", "pub": "2026-10-08"}


class TestCopyRichness(unittest.TestCase):
    def test_google_copy_with_empty_summary_is_zero(self):
        self.assertEqual(nb.copy_richness(GNEWS, "", TITLE), 0)

    def test_direct_copy_with_informative_summary_is_two(self):
        self.assertEqual(nb.copy_richness(DIRECT, DESC, TITLE), 2)

    def test_summary_echoing_title_does_not_count(self):
        self.assertEqual(nb.copy_richness(DIRECT, TITLE, TITLE), 1)
        self.assertEqual(nb.copy_richness(DIRECT, "짧다", TITLE), 1)


class TestFoldCrossRunDuplicates(unittest.TestCase):
    def _fold(self, articles, curated, queue, archive_titles=None):
        state = {"sent": {}}
        kept, rejudge, stats = nb.fold_cross_run_duplicates(
            articles, curated, queue, state, "2026-10-08T00:39:20+00:00",
            archive_titles=archive_titles)
        return kept, rejudge, stats, state

    def test_poorer_second_copy_is_folded_and_marked_seen(self):
        # 월성 실사례: 네이버 사본(직접 링크·요약 있음)이 먼저 큐에 있고 Google 사본이 뒤에 온다.
        curated = {"5468adce": {"title": TITLE, "link": DIRECT, "importance": "nice_to_know"}}
        queue = [{"hash": "5468adce", "source_excerpt": DESC}]
        kept, rejudge, stats, state = self._fold([_article("ca6e1b2e", GNEWS)], curated, queue)
        self.assertEqual(kept, [])
        self.assertEqual(rejudge, set())
        self.assertEqual(stats["folded"], 1)
        self.assertEqual(stats["reasons"], {"poorer_or_equal": 1})
        self.assertIn("ca6e1b2e", state["sent"])            # 다음 회차에 다시 안 온다
        self.assertEqual([e["hash"] for e in queue], ["5468adce"])  # 큐는 그대로

    def test_equal_copies_fold_to_first(self):
        curated = {"first": {"title": TITLE, "link": DIRECT}}
        queue = [{"hash": "first", "source_excerpt": DESC}]
        kept, rejudge, stats, _ = self._fold(
            [_article("second", "https://other.example/x", DESC)], curated, queue)
        self.assertEqual(kept, [])
        self.assertEqual(stats["reasons"], {"poorer_or_equal": 1})

    def test_richer_second_copy_rejudges_under_existing_hash(self):
        # 드문 반대 순서: Google 사본이 먼저 왔고(요약 공란) 아직 발송 전, 네이버 사본이 뒤에 온다.
        curated = {"gfirst": {"title": TITLE, "link": GNEWS, "importance": "nice_to_know"}}
        queue = [{"hash": "gfirst", "source_excerpt": ""}, {"hash": "other"}]
        kept, rejudge, stats, state = self._fold(
            [_article("nsecond", DIRECT, DESC)], curated, queue)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["hash"], "gfirst")      # 신원은 첫 해시
        self.assertEqual(kept[0]["link"], DIRECT)         # 재료는 충실한 사본
        self.assertEqual(rejudge, {"gfirst"})
        self.assertEqual([e["hash"] for e in queue], ["other"])  # 옛 큐 항목은 빠진다
        self.assertIn("nsecond", state["sent"])
        self.assertEqual(stats["rejudged"], 1)
        self.assertEqual(stats["folded"], 0)

    def test_richer_copy_after_delivery_is_still_folded(self):
        curated = {"gfirst": {"title": TITLE, "link": GNEWS}}
        kept, rejudge, stats, _ = self._fold([_article("nsecond", DIRECT, DESC)], curated, [])
        self.assertEqual(kept, [])
        self.assertEqual(rejudge, set())
        self.assertEqual(stats["reasons"], {"already_sent": 1})

    def test_archive_only_identity_folds_without_judgement(self):
        # 캐시(14일)는 만료됐지만 아카이브가 그 제목을 안다 — 적재가 거부할 기사다.
        kept, _, stats, state = self._fold(
            [_article("late", DIRECT, DESC)], {}, [], archive_titles={nb.title_key(TITLE): "old"})
        self.assertEqual(kept, [])
        self.assertEqual(stats["reasons"], {"archive_only": 1})
        self.assertIn("late", state["sent"])

    def test_archive_identity_wins_over_cache_identity(self):
        # 캐시에 두 사본이 다 있으면 웹이 아는 쪽(아카이브 해시)으로 접는다.
        curated = {"cacheonly": {"title": TITLE, "link": GNEWS},
                   "archived": {"title": TITLE, "link": DIRECT}}
        queue = [{"hash": "archived", "source_excerpt": DESC}]
        _, _, stats, _ = self._fold(
            [_article("third", GNEWS)], curated, queue,
            archive_titles={nb.title_key(TITLE): "archived"})
        self.assertEqual(stats["samples"][0].split(" ← ")[1][:8], "archived")

    def test_unrelated_and_cached_articles_pass_through(self):
        curated = {"known": {"title": TITLE, "link": DIRECT}}
        other = {**_article("new", "https://x.example/y", "다른 기사 요약"), "title": "전혀 다른 제목"}
        same_hash = _article("known", DIRECT, DESC)
        kept, rejudge, stats, _ = self._fold([other, same_hash], curated, [])
        self.assertEqual([a["hash"] for a in kept], ["new", "known"])
        self.assertEqual(stats["folded"], 0)
        self.assertEqual(rejudge, set())


class TestArchiveTitleHash(unittest.TestCase):
    def test_identities_map_title_to_first_hash(self):
        with TemporaryDirectory() as tmp:
            original = news_archive.ARCHIVE_DIR
            news_archive.ARCHIVE_DIR = Path(tmp)
            try:
                path = Path(tmp) / "2026-10.jsonl"
                rows = [{"hash": "a1", "url": "https://a.example/1", "title": TITLE},
                        {"hash": "a2", "url": "https://a.example/2", "title": TITLE}]
                path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                encoding="utf-8")
                identities = news_archive.load_recent_identities()
            finally:
                news_archive.ARCHIVE_DIR = original
        self.assertEqual(identities["title_hash"], {nb.title_key(TITLE): "a1"})
        self.assertEqual(identities["hashes"], {"a1", "a2"})


if __name__ == "__main__":
    unittest.main()
