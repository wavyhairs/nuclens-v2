"""배포가 얻은 LLM 판정을 main 캐시에 채워 넣는 병합(tools/merge_llm_caches.py).

지키는 것: main 이 쓴 답은 절대 덮지 않는다, 배포가 새로 얻은 답만 넣는다,
옛 프롬프트의 답은 넣지 않는다, 형식은 각 모듈이 쓰는 그대로다.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import llm_cache
from tools import merge_llm_caches as merge

ROOT = Path(__file__).resolve().parents[1]


def entry(verdict, pv=2):
    return {"prompt_version": pv, "same_event": verdict}


def write(path, entries, *, pv=2, key="reviews", sort_keys=True):
    llm_cache.save(entries, path, key=key, prompt_version=pv, comment="판정 캐시",
                   sort_keys=sort_keys, swallow_errors=False)


class MergeEntriesTests(unittest.TestCase):
    def run_merge(self, base, ours, theirs, pv=2):
        result = merge.Result("x")
        return merge.merge_entries(base, ours, theirs, pv, result), result

    def test_new_answer_from_the_deploy_is_added(self):
        merged, result = self.run_merge({}, {"a": entry(True)}, {})
        self.assertEqual(merged, {"a": entry(True)})
        self.assertEqual(result.added, 1)

    def test_untouched_entries_are_ignored(self):
        merged, result = self.run_merge({"a": entry(True)}, {"a": entry(True)}, {})
        self.assertEqual(merged, {}, "main 이 지운 것을 되살리면 안 된다")
        self.assertFalse(result.changed)

    def test_main_wins_when_both_answered(self):
        merged, result = self.run_merge({}, {"a": entry(True)}, {"a": entry(False)})
        self.assertEqual(merged["a"], entry(False))
        self.assertEqual(result.main_wins, 1)

    def test_requery_by_the_deploy_updates_an_unchanged_main_entry(self):
        merged, result = self.run_merge({"a": entry(True)}, {"a": entry(False)},
                                        {"a": entry(True)})
        self.assertEqual(merged["a"], entry(False))
        self.assertEqual(result.updated, 1)

    def test_requery_does_not_override_a_newer_main_answer(self):
        merged, result = self.run_merge({"a": entry(True, pv=1)}, {"a": entry(False)},
                                        {"a": {"prompt_version": 2, "same_event": True}})
        self.assertEqual(merged["a"]["same_event"], True)
        self.assertEqual(result.main_wins, 1)

    def test_deleted_on_main_is_not_revived(self):
        merged, result = self.run_merge({"a": entry(True)}, {"a": entry(False)}, {})
        self.assertNotIn("a", merged)
        self.assertFalse(result.changed)

    def test_old_prompt_answers_are_not_added(self):
        """배포가 main 보다 옛 코드로 돌았다 — 읽는 쪽이 어차피 버린다."""
        merged, result = self.run_merge({}, {"a": entry(True, pv=1)}, {}, pv=2)
        self.assertEqual(merged, {})
        self.assertEqual(result.stale_prompt, 1)


class MergeFileTests(unittest.TestCase):
    """실제 git 저장소에서 base-ref 를 읽고 main 워크트리에 쓴다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "deploy"
        self.target = Path(self.tmp.name) / "main"
        self.source.mkdir()
        self.target.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        for name, (key, sort_keys) in merge.CACHES.items():
            write(self.source / name, {"old": entry(True)}, key=key, sort_keys=sort_keys)
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.source, check=True, capture_output=True)

    def test_only_new_answers_reach_main_and_format_is_the_modules(self):
        name = "issue_llm_reviews.json"
        # 배포 빌드가 한 쌍을 새로 물었다.
        write(self.source / name, {"old": entry(True), "new": entry(False)})
        # 그 사이 main 은 크롤이 다른 쌍을 적어 올렸다.
        write(self.target / name, {"old": entry(True), "crawl": entry(True)})
        result = merge.merge_file(name, source=self.source, target=self.target, base_ref="HEAD")
        self.assertEqual((result.added, result.updated), (1, 0))
        expected = Path(self.tmp.name) / "expected.json"
        write(expected, {"old": entry(True), "crawl": entry(True), "new": entry(False)})
        self.assertEqual((self.target / name).read_text(encoding="utf-8"),
                         expected.read_text(encoding="utf-8"))

    def test_unsorted_cache_keeps_main_order_and_appends(self):
        name = "issue_insights.json"
        write(self.source / name, {"old": entry(True), "new": entry(False)},
              key="insights", sort_keys=False)
        write(self.target / name, {"z": entry(True), "old": entry(True)},
              key="insights", sort_keys=False)
        merge.merge_file(name, source=self.source, target=self.target, base_ref="HEAD")
        doc = json.loads((self.target / name).read_text(encoding="utf-8"))
        self.assertEqual(list(doc["insights"]), ["z", "old", "new"])

    def test_no_new_answers_leaves_main_untouched(self):
        name = "keei_llm_matches.json"
        write(self.target / name, {"old": entry(True)}, key="matches")
        before = (self.target / name).stat().st_mtime_ns
        result = merge.merge_file(name, source=self.source, target=self.target, base_ref="HEAD")
        self.assertFalse(result.changed)
        self.assertEqual((self.target / name).stat().st_mtime_ns, before)

    def test_unknown_envelope_field_is_not_rewritten(self):
        name = "issue_headlines.json"
        write(self.source / name, {"old": entry(True), "new": entry(False)}, key="headlines")
        doc = {"_comment": "c", "prompt_version": 2, "headlines": {}, "extra": 1}
        (self.target / name).write_text(json.dumps(doc), encoding="utf-8")
        result = merge.merge_file(name, source=self.source, target=self.target, base_ref="HEAD")
        self.assertIn("모르는 칸", result.skipped)
        self.assertEqual(json.loads((self.target / name).read_text(encoding="utf-8")), doc)


class WorkflowWiringTests(unittest.TestCase):
    DEPLOY = ROOT / ".github" / "workflows" / "deploy-web.yml"
    CRAWL = ROOT / ".github" / "workflows" / "crawl.yml"

    def test_deploy_returns_its_answers_to_main(self):
        yml = self.DEPLOY.read_text(encoding="utf-8")
        self.assertIn("python tools/merge_llm_caches.py", yml)
        self.assertIn("contents: write", yml)
        step = yml[yml.index("python tools/merge_llm_caches.py") - 3000:]
        self.assertIn("continue-on-error: true", step, "캐시를 못 돌려놓아도 배포는 산다")

    def test_merged_files_are_the_crawls_llm_caches(self):
        """크롤이 커밋하는 판정 캐시와 같은 목록이어야 한다 — 하나 빠지면 그 캐시만 또 버려진다."""
        crawl = self.CRAWL.read_text(encoding="utf-8")
        line = next(row for row in crawl.splitlines()
                    if "for f in issue_llm_reviews.json" in row)
        for name in merge.CACHES:
            self.assertIn(name, line)
        deploy = self.DEPLOY.read_text(encoding="utf-8")
        for name in merge.CACHES:
            self.assertIn(name, deploy)


if __name__ == "__main__":
    unittest.main()
