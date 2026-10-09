"""발송 해시가 아카이브에 없을 때(고아) 같은 기사의 레코드로 잇는다 — resolve_delivery_orphans.

왜 잠그는가
-----------
웹은 발송 기록을 아카이브와 해시로만 조인했다. 같은 기사가 두 경로로 들어와 해시가
갈리면(아카이브는 첫 사본, 발송은 두 번째 사본) 그 기사는 발송됐는데도 카드가 되지
못하고 검색에도 없었다 (2026-10-09 월성 2~4호기 계속운전, 9/29 이후 7건).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_data as bd  # noqa: E402

TITLE = "월성 2~4호기, 계속운전 신청 임박…2호기는 내달 정지"


def _record(h, title=TITLE):
    return {"hash": h, "title": title, "url": f"https://example.com/{h}"}


def _delivery(h, date, title=TITLE, **extra):
    return {"hash": h, "date": date, "title_kr": "한수원, 월성 2~4호기 계속운전 연내 신청 추진",
            "title": title, "brief_rank": 2, **extra}


class ResolveDeliveryOrphansTests(unittest.TestCase):
    def test_title_match_attaches_delivery_to_archive_hash(self):
        deliveries = {"ca6e1b2e": _delivery("ca6e1b2e", "2026-10-09")}
        ranks = {"ca6e1b2e": 2}
        stats = bd.resolve_delivery_orphans(deliveries, [_record("5468adce")], {}, ranks)
        self.assertEqual((stats["orphans"], stats["by_title"], stats["unresolved"]), (1, 1, 0))
        attached = deliveries["5468adce"]
        self.assertEqual(attached["date"], "2026-10-09")
        self.assertEqual(attached["hash"], "5468adce")
        self.assertEqual(attached["delivery_hash_alias"], "ca6e1b2e")
        self.assertEqual(ranks["5468adce"], 2)          # 카드 번호도 따라온다
        self.assertIn("ca6e1b2e", deliveries)            # 고아 키는 그대로(무해)

    def test_alias_file_wins_over_title(self):
        deliveries = {"orphan": _delivery("orphan", "2026-10-05", title="제목이 달라졌다")}
        stats = bd.resolve_delivery_orphans(
            deliveries, [_record("arch")], {"orphan": "arch"}, {})
        self.assertEqual(stats["by_alias"], 1)
        self.assertEqual(deliveries["arch"]["delivery_hash_alias"], "orphan")

    def test_existing_newer_delivery_is_not_overwritten(self):
        deliveries = {"arch": _delivery("arch", "2026-10-10"),
                      "orphan": _delivery("orphan", "2026-10-09")}
        stats = bd.resolve_delivery_orphans(deliveries, [_record("arch")], {}, {})
        self.assertEqual(stats["by_title"], 1)
        self.assertEqual(deliveries["arch"]["date"], "2026-10-10")
        self.assertNotIn("delivery_hash_alias", deliveries["arch"])

    def test_unresolved_orphan_is_counted_with_sample(self):
        deliveries = {"orphan": _delivery("orphan", "2026-10-09", title="")}
        stats = bd.resolve_delivery_orphans(deliveries, [_record("arch", "다른 기사")], {}, {})
        self.assertEqual((stats["orphans"], stats["unresolved"]), (1, 1))
        self.assertTrue(stats["unresolved_samples"][0].startswith("orphan"))
        self.assertNotIn("arch", deliveries)

    def test_non_orphans_untouched(self):
        deliveries = {"arch": _delivery("arch", "2026-10-09")}
        stats = bd.resolve_delivery_orphans(deliveries, [_record("arch")], {}, {})
        self.assertEqual(stats["orphans"], 0)
        self.assertEqual(list(deliveries), ["arch"])


class LoadDeliveryAliasesTests(unittest.TestCase):
    def test_reads_alias_map_and_tolerates_missing_or_broken(self):
        original = bd.BOT_DIR
        with TemporaryDirectory() as tmp:
            bd.BOT_DIR = Path(tmp)
            try:
                self.assertEqual(bd.load_delivery_aliases(), {})
                (Path(tmp) / "delivery_hash_aliases.json").write_text(
                    json.dumps({"aliases": {"a": "b", "": "x"}}), encoding="utf-8")
                self.assertEqual(bd.load_delivery_aliases(), {"a": "b"})
                (Path(tmp) / "delivery_hash_aliases.json").write_text("{broken", encoding="utf-8")
                self.assertEqual(bd.load_delivery_aliases(), {})
            finally:
                bd.BOT_DIR = original


if __name__ == "__main__":
    unittest.main()
