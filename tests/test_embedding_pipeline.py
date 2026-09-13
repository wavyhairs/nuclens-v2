import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import embedding_pipeline as ep


class EmbeddingPipelineTests(unittest.TestCase):
    def test_current_cache_requires_model_and_fingerprint(self):
        article = {"title_kr": "동일 사건 후속 보도", "summary": "후속 사실을 확인했습니다."}
        text = ep.embedding_text(article)
        entry = {
            "vec": [1.0, 0.0],
            "model": ep.EMBEDDING_MODEL,
            "text_fingerprint": ep.text_fingerprint(text),
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        self.assertTrue(ep.cache_entry_is_current(entry, text))
        self.assertIsNone(ep.cached_vector({**entry, "model": "text-embedding-004"}))
        self.assertFalse(ep.cache_entry_is_current(entry, text + "변경"))

    def test_selected_articles_uses_latest_21_day_delivery_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "archive").mkdir()
            deliveries = [
                {"date": "2026-07-01", "hash": "old"},
                {"date": "2026-07-20", "hash": "kept"},
                {"date": "2026-08-01", "hash": "latest"},
            ]
            (root / "delivery_log.jsonl").write_text(
                "\n".join(json.dumps(row) for row in deliveries), encoding="utf-8"
            )
            archive = [
                {"hash": "old", "title_kr": "과거"},
                {"hash": "kept", "title_kr": "최근"},
                {"hash": "latest", "title_kr": "최신"},
            ]
            (root / "archive" / "2026-08.jsonl").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in archive),
                encoding="utf-8",
            )
            selected = ep.selected_articles(root, window_days=21)
            self.assertEqual({row["hash"] for row in selected}, {"kept", "latest"})

    def test_refresh_writes_model_aware_cache(self):
        articles = [{"hash": "a", "title_kr": "후속 보도", "summary": "사실을 확인했습니다."}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "embeddings.json"
            vector = [0.0] * ep.EMBEDDING_DIMENSION
            vector[0] = 1.0
            with patch.object(ep, "embed_one", return_value=vector):
                stats = ep.refresh_embeddings(
                    articles, {}, object(), sleep_seconds=0, cache_path=path
                )
            cache = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stats["current"], 1)
            self.assertEqual(cache["a"]["model"], ep.EMBEDDING_MODEL)
            self.assertEqual(cache["a"]["dimension"], ep.EMBEDDING_DIMENSION)


if __name__ == "__main__":
    unittest.main()


class RetentionFollowsIssueWindowTests(unittest.TestCase):
    """보존 기간이 이슈 창을 따라가는지.

    예전에는 `EMBEDDING_RETENTION_DAYS = 35 # 21일 이슈 창 + 여유` 였다. 결합이
    주석에만 있으면 창을 넓히는 순간 조용히 깨지고, 벡터가 없는 쌍은
    `issue_review.in_review_band` 가 False 를 돌려주어 **회색지대에 들어가지도
    못한다.** 그러면 창 확대의 효과가 아니라 보존이 끊긴 효과를 재게 된다.
    """

    def _reload(self, env: dict) -> object:
        import importlib
        with patch.dict("os.environ", env, clear=False):
            return importlib.reload(ep)

    def tearDown(self):
        import importlib
        with patch.dict("os.environ", {}, clear=False):
            for name in ("NUCLENS_ISSUE_WINDOW_DAYS", "EMBEDDING_RETENTION_DAYS"):
                __import__("os").environ.pop(name, None)
            importlib.reload(ep)

    def test_default_is_unchanged(self):
        module = self._reload({"NUCLENS_ISSUE_WINDOW_DAYS": "", "EMBEDDING_RETENTION_DAYS": ""})
        self.assertEqual(module.ISSUE_WINDOW_DAYS, 21)
        self.assertEqual(module.EMBEDDING_RETENTION_DAYS, 35)

    def test_widening_the_issue_window_widens_retention(self):
        module = self._reload({"NUCLENS_ISSUE_WINDOW_DAYS": "42", "EMBEDDING_RETENTION_DAYS": ""})
        self.assertEqual(module.EMBEDDING_RETENTION_DAYS, 56)
        self.assertGreater(module.EMBEDDING_RETENTION_DAYS, module.ISSUE_WINDOW_DAYS)

    def test_backfill_window_follows_the_issue_window(self):
        """백필 창이 뒤처지면 창 안에 있는데 벡터가 없는 기사가 생긴다."""
        module = self._reload({"NUCLENS_ISSUE_WINDOW_DAYS": "35", "EMBEDDING_RETENTION_DAYS": ""})
        parser_default = module.ISSUE_WINDOW_DAYS
        self.assertEqual(parser_default, 35)

    def test_explicit_override_still_wins(self):
        module = self._reload({"NUCLENS_ISSUE_WINDOW_DAYS": "42", "EMBEDDING_RETENTION_DAYS": "90"})
        self.assertEqual(module.EMBEDDING_RETENTION_DAYS, 90)

    def test_garbage_falls_back_instead_of_crashing_the_crawl(self):
        module = self._reload({"NUCLENS_ISSUE_WINDOW_DAYS": "three weeks", "EMBEDDING_RETENTION_DAYS": ""})
        self.assertEqual(module.ISSUE_WINDOW_DAYS, 21)
        self.assertEqual(module.EMBEDDING_RETENTION_DAYS, 35)
