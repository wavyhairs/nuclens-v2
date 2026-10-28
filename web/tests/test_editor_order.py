"""발송 순서를 얼린 뒤에도 편집 순위(promote·demote_only)를 지킨다 — `apply_editor_order`.

2026-10-29: 국내 2·3번 카드를 promote 했는데, 빌드가 정렬한 순서를 briefing_snapshot 이
발송 당시 순서로 되돌려 체코 기사가 화면·카드 1번에 그대로 남았다. 숨김은 기사가
빠지므로 원래도 지켜졌다.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import build_data  # noqa: E402

DAY = "2026-10-29"


def _row(h, region):
    return {"issue_id": f"issue-{h}", "title": h, "region": region,
            "related_articles": [{"hash": h * 2, "briefing_date": DAY}]}


def _briefing():
    # 얼린 순서 그대로: 국내·해외 맞물림
    rows = [_row("czech000", "국내"), _row("over0001", "해외"), _row("grid0000", "국내"),
            _row("over0002", "해외"), _row("plan0000", "국내"), _row("over0003", "해외")]
    return {"date": DAY, "issues": rows, "highlights": [r["title"] for r in rows[:3]]}


def _overrides(promote=(), demote=()):
    return {"promote": {(h, DAY): "r" for h in promote},
            "demote": {(h, DAY): action for h, action in demote}, "matched": set()}


class EditorOrderTests(unittest.TestCase):
    def titles(self, briefing):
        return [row["title"] for row in briefing["issues"]]

    def test_promoted_cards_take_the_first_domestic_slots(self):
        briefing = _briefing()
        changed = build_data.apply_editor_order(
            [briefing], _overrides(promote=("grid0000", "plan0000")))
        self.assertEqual(changed, 1)
        self.assertEqual(self.titles(briefing),
                         ["grid0000", "over0001", "plan0000", "over0002", "czech000", "over0003"])
        self.assertEqual(briefing["highlights"], ["grid0000", "over0001", "plan0000"])

    def test_demote_only_goes_to_the_back_of_its_region(self):
        briefing = _briefing()
        build_data.apply_editor_order([briefing], _overrides(demote=(("over0001", "demote_only"),)))
        self.assertEqual(self.titles(briefing)[1::2], ["over0002", "over0003", "over0001"])

    def test_hide_and_other_dates_leave_the_order_alone(self):
        briefing = _briefing()
        before = self.titles(briefing)
        other_day = {"promote": {("grid0000", "2026-10-28"): "r"},
                     "demote": {("czech000", DAY): "hide_from_today"}, "matched": set()}
        self.assertEqual(build_data.apply_editor_order([briefing], other_day), 0)
        self.assertEqual(self.titles(briefing), before)


if __name__ == "__main__":
    unittest.main()
