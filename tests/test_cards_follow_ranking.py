"""카드는 사이트 순위 상위 k 그대로다 — 편집 override 로 순위가 바뀌면 다시 굽는다.

2026-09-24: 카드는 07:27 에 구웠고 10:05 에 400GW 숨김·이탈리아 올림이 들어왔다.
사이트는 자원안보·이탈리아·한미일 SMR 을 말하는데 카드는 자원안보·한미일 SMR·폴란드를
들고 있었다. '이미 사이트에 있다' 스킵이 재생성을 막았다.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import make_cards


def _row(issue_id):
    return {"issue_id": issue_id, "title": issue_id,
            "representative_article": {"url": f"https://example.com/{issue_id}", "hash": issue_id}}


class StaleAgainstRanking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        site = Path(self.tmp.name)
        (site / "index.json").write_text(json.dumps({"lines": {"2026-09-24": {
            "story-aad9": "자원안보", "story-831a": "한미일 SMR", "story-53bc": "폴란드"}}}),
            encoding="utf-8")
        self.patch = mock.patch.object(make_cards, "CARDS_SITE_DIR", site)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_override_that_changes_the_top_three_is_stale(self):
        rows = [_row("story-aad9"), _row("story-9280"), _row("story-831a"), _row("story-53bc")]
        stale = make_cards.stale_against_ranking("2026-09-24", rows)
        self.assertIsNotNone(stale)
        self.assertIn("story-9280", stale[1])

    def test_same_three_in_the_same_order_is_not_stale(self):
        rows = [_row("story-aad9"), _row("story-831a"), _row("story-53bc"), _row("x")]
        self.assertIsNone(make_cards.stale_against_ranking("2026-09-24", rows))

    def test_promotion_inside_the_top_three_is_stale(self):
        """카드에 01·02·03 이 박히므로 순서만 바뀌어도 어긋난 것이다."""
        rows = [_row("story-831a"), _row("story-aad9"), _row("story-53bc")]
        self.assertIsNotNone(make_cards.stale_against_ranking("2026-09-24", rows))

    def test_cards_without_a_record_are_not_judged(self):
        self.assertIsNone(make_cards.stale_against_ranking("2026-09-23", [_row("a")]))
        self.assertIsNone(make_cards.stale_against_ranking("2026-09-24", None))


if __name__ == "__main__":
    unittest.main()


class WakeScript(unittest.TestCase):
    def test_crawl_wake_script_reports_through_github_output(self):
        import subprocess
        import sys as _sys
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.txt"
            env = {**__import__("os").environ, "GITHUB_OUTPUT": str(out), "PYTHONIOENCODING": "utf-8"}
            subprocess.run([_sys.executable, str(root / "tools" / "cards_stale.py")],
                           check=True, env=env, capture_output=True)
            self.assertRegex(out.read_text(encoding="utf-8"), r"^stale=(true|false)$")
