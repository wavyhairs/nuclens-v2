from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from web import build_data
from tools import (
    check_live_news,
    prune_actions_caches,
    render_static_pages,
    web_deploy_mode,
    web_snapshot,
)


class DeployModeTests(unittest.TestCase):
    def test_ui_only_is_fast(self):
        mode, _ = web_deploy_mode.classify(["web/public/app.js", "functions/admin/api.js"])
        self.assertEqual(mode, "fast")

    def test_test_only_is_skip(self):
        mode, _ = web_deploy_mode.classify(["web/tests/test_prototype.py"])
        self.assertEqual(mode, "skip")

    def test_builder_or_unknown_web_python_is_full(self):
        self.assertEqual(web_deploy_mode.classify(["web/build_data.py"])[0], "full")
        self.assertEqual(web_deploy_mode.classify(["web/new_projection.py"])[0], "full")


class SnapshotTests(unittest.TestCase):
    def _public(self, root: Path) -> Path:
        public = root / "public"
        (public / "data").mkdir(parents=True)
        (public / "admin" / "data").mkdir(parents=True)
        (public / "data" / "meta.json").write_text(
            json.dumps({
                "generation_id": "20260918T000000Z",
                "generated_at": "now",
                "build_mode": "ok",
                "data_contract_version": web_snapshot.DATA_CONTRACT_VERSION,
            }),
            encoding="utf-8",
        )
        (public / "data" / "_pages.json").write_text(
            json.dumps({"schema": "nuclens-render-pages-v1", "issues": [], "briefs": []}),
            encoding="utf-8",
        )
        (public / "admin" / "data" / "config.json").write_text("{}", encoding="utf-8")
        (public / "rss.xml").write_text("<rss/>", encoding="utf-8")
        return public

    def test_create_inspect_restore_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = self._public(root)
            snapshot = root / "snapshot"
            with mock.patch.object(web_snapshot, "builder_fingerprint", return_value="builder"):
                metadata = web_snapshot.create(public, snapshot, "", "abc")
                # create without a live URL is deliberately not production eligible.
                self.assertFalse(metadata["production_verified"])
                compatible, reasons, _ = web_snapshot.inspect(snapshot)
                self.assertFalse(compatible)
                self.assertIn("production 검증 표식 없음", reasons)
                metadata["production_verified"] = True
                (snapshot / web_snapshot.META_NAME).write_text(
                    json.dumps(metadata), encoding="utf-8"
                )
                compatible, reasons, _ = web_snapshot.inspect(snapshot)
                self.assertTrue(compatible, reasons)
                shutil_target = root / "restored"
                shutil_target.mkdir()
                web_snapshot.restore(snapshot, shutil_target)
                self.assertTrue((shutil_target / "data" / "meta.json").is_file())

    def test_live_generation_waits_for_edge_propagation(self):
        with mock.patch.object(
            web_snapshot,
            "_live_meta",
            side_effect=[{"generation_id": "old"}, {"generation_id": "new"}],
        ), mock.patch.object(web_snapshot.time, "sleep") as sleep:
            self.assertEqual(
                web_snapshot._wait_for_live_generation(
                    "https://example.test", "new", attempts=3, delay_seconds=0.01
                ),
                "new",
            )
            sleep.assert_called_once_with(0.01)


class StaticRenderTests(unittest.TestCase):
    def test_renders_current_shell_and_redirect(self):
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp)
            source = web_snapshot.ROOT / "web" / "public" / "index.html"
            (public / "index.html").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            (public / "data").mkdir()
            (public / "data" / "_pages.json").write_text(json.dumps({
                "schema": "nuclens-render-pages-v1",
                "issues": [
                    {"id": "live-1", "kind": "live", "title": "현재 이슈",
                     "description": "설명", "published": "2026-09-01", "modified": "2026-09-18"},
                    {"id": "old-1", "kind": "moved", "title": "옛 이슈", "target": "live-1"},
                ],
                "briefs": [{"date": "2026-09-18", "title": "브리프", "description": "설명"}],
            }, ensure_ascii=False), encoding="utf-8")
            counts = render_static_pages.render(public)
            self.assertEqual(counts, {"issue": 1, "redirect": 1, "brief": 1})
            self.assertIn("현재 이슈", (public / "issue" / "live-1" / "index.html").read_text(encoding="utf-8"))
            self.assertIn("location.replace", (public / "issue" / "old-1" / "index.html").read_text(encoding="utf-8"))


class BrowserContractTests(unittest.TestCase):
    def test_app_rejects_incompatible_data_contract(self):
        script = (web_snapshot.ROOT / "web" / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("const DATA_CONTRACT_VERSION = 1;", script)
        self.assertIn("data contract mismatch", script)
        self.assertIn("async function loadNewsPayload()", script)
        self.assertIn("loadNewsPayload(), loadJSON(\"briefings.json\")", script)


class CachePruningTests(unittest.TestCase):
    def test_only_old_matching_snapshots_are_deleted(self):
        entry = prune_actions_caches.CacheEntry
        rows = [
            entry(1, "Linux-nuclens-web-production-a", "2026-09-16T00:00:00Z"),
            entry(2, "Linux-nuclens-web-production-b", "2026-09-17T00:00:00Z"),
            entry(3, "Linux-nuclens-web-production-c", "2026-09-18T00:00:00Z"),
            entry(4, "Linux-pip-other", "2026-09-01T00:00:00Z"),
        ]
        doomed = prune_actions_caches.deletions(
            rows, "Linux-nuclens-web-production-", keep=2
        )
        self.assertEqual([row.cache_id for row in doomed], [1])


class NewsSmokeTests(unittest.TestCase):
    def test_manifest_and_shard_counts_are_checked(self):
        manifest = {
            "schema": "nuclens-news-shards-v1",
            "count": 3,
            "shards": [
                {"file": "news/000.json", "count": 2},
                {"file": "news/001.json", "count": 1},
            ],
        }
        payloads = {"news/000.json": [{}, {}], "news/001.json": [{}]}
        self.assertEqual(
            check_live_news.validate_manifest(manifest, payloads.__getitem__), 3
        )

    def test_unsafe_shard_path_is_rejected(self):
        manifest = {
            "schema": "nuclens-news-shards-v1",
            "count": 1,
            "shards": [{"file": "../secret.json", "count": 1}],
        }
        with self.assertRaisesRegex(ValueError, "unsafe"):
            check_live_news.validate_manifest(manifest, lambda _: [{}])


class NewsShardTests(unittest.TestCase):
    def test_write_and_load_sharded_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "news.json").write_text("[]", encoding="utf-8")
            rows = [{"hash": str(i), "title": "x" * 600} for i in range(5)]
            manifest = build_data.write_news_payload(rows, data_dir, max_bytes=1024)
            self.assertGreater(len(manifest["shards"]), 1)
            self.assertFalse((data_dir / "news.json").exists())
            self.assertEqual(build_data.load_news_payload(data_dir), rows)
            for descriptor in manifest["shards"]:
                self.assertLessEqual((data_dir / descriptor["file"]).stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()
