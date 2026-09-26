"""지난 브리핑은 발송 당시 모양으로 남는다 — 사건을 나눈 뒤에도.

2026-09-24 결정 A: 콘솔에서 6260(전기본 비대 이슈)을 나누는 순간 8월 브리핑
화면이 지금 묶음으로 다시 그려진다. 그날 독자가 본 카드는 사라지고 그날 없던
카드 구성이 그 날짜 이름으로 선다. 원장이 그 날의 카드를 붙잡고, 바뀐 카드에만
'이후 분류 정정됨'을 단다.
"""
import tempfile
import unittest
from pathlib import Path

import briefing_snapshot as bs

DAY = "2026-08-06"


def _article(hash_, day=DAY, role="card"):
    return {"hash": hash_, "briefing_date": day, "member_role": role}


def _row(issue_id, hashes, title, *, evidence=(), older=()):
    arts = [_article(h) for h in hashes]
    arts += [_article(h, role="evidence") for h in evidence]
    arts += [_article(h, day="2026-08-01") for h in older]
    return {"issue_id": issue_id, "title": title, "headline_display": title,
            "summary": f"{title} 요약", "related_articles": arts,
            "representative_article": {"hash": hashes[0]}, "verification": {"status": "single"}}


def _briefing(rows, day=DAY):
    return {"date": day, "issues": rows, "issue_count": len(rows)}


class FreezeTests(unittest.TestCase):
    def test_first_sight_freezes_only_that_days_card_members(self):
        store = {"dates": {}}
        rows = [_row("issue-6260", ["a", "b"], "전기본 비대 이슈", evidence=["e"], older=["o"])]
        stats = bs.apply([_briefing(rows)], store, "2026-09-24T00:00:00+09:00")
        self.assertEqual(stats["frozen_new"], 1)
        card = store["dates"][DAY]["cards"][0]
        self.assertEqual(card["hashes"], ["a", "b"], "근거·다른 날 기사가 얼린 묶음에 섞였다")
        self.assertEqual(card["fields"]["title"], "전기본 비대 이슈")
        self.assertNotIn("related_articles", card["fields"], "무거운 칸까지 얼렸다")

    def test_frozen_date_is_never_rewritten(self):
        store = {"dates": {}}
        bs.apply([_briefing([_row("issue-x", ["a"], "처음")])], store, "t1")
        bs.apply([_briefing([_row("issue-x", ["a"], "나중")])], store, "t2")
        self.assertEqual(store["dates"][DAY]["cards"][0]["fields"]["title"], "처음")
        self.assertEqual(store["dates"][DAY]["frozen_at"], "t1")


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.store = {"dates": {}}
        sent = [_row("issue-6260", ["a", "b"], "전기본 비대 이슈"),
                _row("issue-italy", ["c"], "이탈리아 원전법")]
        bs.apply([_briefing(sent)], self.store, "2026-09-24T00:00:00+09:00")

    def test_unchanged_grouping_uses_the_current_row(self):
        now = [_row("issue-6260", ["a", "b"], "새 표시 제목"),
               _row("issue-italy", ["c"], "이탈리아 원전법")]
        briefing = _briefing(now)
        stats = bs.apply([briefing], self.store, "t")
        self.assertEqual(stats["cards_corrected"], 0)
        self.assertIs(briefing["issues"][0], now[0])
        self.assertNotIn("classification_note", briefing["issues"][0])

    def test_new_route_with_same_articles_is_not_a_correction(self):
        """주소만 바뀐 것(재발급·흡수)은 분류가 바뀐 게 아니다."""
        now = [_row("story-new", ["a", "b"], "전기본 비대 이슈"),
               _row("issue-italy", ["c"], "이탈리아 원전법")]
        briefing = _briefing(now)
        self.assertEqual(bs.apply([briefing], self.store, "t")["cards_corrected"], 0)
        self.assertEqual([r["issue_id"] for r in briefing["issues"]], ["story-new", "issue-italy"])

    def test_split_keeps_the_sent_card_and_points_to_both_halves(self):
        now = [_row("issue-6260", ["a"], "전기본 확정"),
               _row("issue-tariff", ["b"], "지역별 요금제"),
               _row("issue-italy", ["c"], "이탈리아 원전법")]
        briefing = _briefing(now)
        stats = bs.apply([briefing], self.store, "t")
        self.assertEqual(stats["cards_corrected"], 1)
        rows = briefing["issues"]
        self.assertEqual(len(rows), 2, "나뉜 두 행이 발송 당시 카드 옆에 또 섰다")
        sent = rows[0]
        self.assertEqual(sent["title"], "전기본 비대 이슈", "발송 당시 문장이 지워졌다")
        self.assertEqual(sent["issue_id"], "issue-6260")
        note = sent["classification_note"]
        self.assertEqual(note["status"], "corrected")
        self.assertEqual({row["issue_id"] for row in note["now"]}, {"issue-6260", "issue-tariff"})
        # 무거운 칸은 대표 기사를 지금 들고 있는 행에서 빌린다.
        self.assertEqual(sent["verification"], {"status": "single"})
        self.assertEqual(sent["current_article_count"], 2)
        self.assertEqual(briefing["issue_count"], 2)

    def test_merge_keeps_both_sent_cards(self):
        now = [_row("issue-6260", ["a", "b", "c"], "합쳐진 이슈")]
        briefing = _briefing(now)
        stats = bs.apply([briefing], self.store, "t")
        rows = briefing["issues"]
        self.assertEqual([r["title"] for r in rows], ["전기본 비대 이슈", "이탈리아 원전법"])
        self.assertEqual(stats["cards_corrected"], 2)
        self.assertEqual(len({r["issue_id"] for r in rows}), 2, "한 날짜에 같은 주소가 두 번 섰다")

    def test_withdrawn_articles_are_not_resurrected(self):
        """편집 숨김·격리로 그날 기사가 전부 빠진 카드는 되살리지 않는다."""
        now = [_row("issue-6260", ["a", "b"], "전기본 비대 이슈")]
        briefing = _briefing(now)
        bs.apply([briefing], self.store, "t")
        self.assertEqual([r["issue_id"] for r in briefing["issues"]], ["issue-6260"])

    def test_late_article_row_is_kept_after_the_frozen_cards(self):
        now = [_row("issue-late", ["z"], "늦게 복원된 기사"),
               _row("issue-6260", ["a", "b"], "전기본 비대 이슈"),
               _row("issue-italy", ["c"], "이탈리아 원전법")]
        briefing = _briefing(now)
        bs.apply([briefing], self.store, "t")
        self.assertEqual([r["issue_id"] for r in briefing["issues"]],
                         ["issue-6260", "issue-italy", "issue-late"],
                         "발송 당시 순서가 지켜지지 않았다")


class MigrationV2Tests(unittest.TestCase):
    """v1 원장의 소급 날짜만 표시 제목을 그날 제목으로 되돌린다 — 한 번만, 그 칸만."""

    def _v1_store(self, frozen_at):
        return {"version": 1, "dates": {DAY: {"frozen_at": frozen_at, "cards": [{
            "issue_id": "issue-x", "hashes": ["a"], "representative_hash": "a",
            "fields": {"title": "그날 제목", "headline_display": "9/24 의 이슈 표시 제목",
                       "summary": "그날 요약"}}]}}}

    def test_backfilled_date_gets_its_own_title(self):
        store = self._v1_store("2026-09-24T18:50:55.342384+09:00")
        stats = bs.apply([], store, "2026-09-26T06:00:00+09:00")
        fields = store["dates"][DAY]["cards"][0]["fields"]
        self.assertEqual(fields["headline_display"], "그날 제목")
        self.assertEqual(fields["summary"], "그날 요약", "표시 제목 말고 다른 칸까지 건드렸다")
        self.assertEqual(stats["migrated_headlines"], 1)
        self.assertEqual(store["version"], bs.VERSION)

    def test_date_frozen_on_its_own_day_is_left_alone(self):
        store = self._v1_store(f"{DAY}T06:10:00+09:00")
        bs.apply([], store, "2026-09-26T06:00:00+09:00")
        self.assertEqual(store["dates"][DAY]["cards"][0]["fields"]["headline_display"],
                         "9/24 의 이슈 표시 제목")

    def test_migration_runs_once(self):
        store = self._v1_store("2026-09-24T18:50:55+09:00")
        store["version"] = 2
        stats = bs.apply([], store, "2026-09-26T06:00:00+09:00")
        self.assertEqual(stats["migrated_headlines"], 0)
        self.assertEqual(store["dates"][DAY]["cards"][0]["fields"]["headline_display"],
                         "9/24 의 이슈 표시 제목")


class RepairTests(unittest.TestCase):
    """나중에 찾은 제목 오류(archive_repairs.json)는 얼린 카드를 세울 때 얹는다 — 원장은 그대로.

    2026-09-26: 정정 55건 중 23장이 원장에 옛 제목("한덕수 국무총리, 빌 게이츠와…")으로
    얼려 있었다. 기사 묶음이 그대로면 지금 행(정정된 제목)이 서지만, 묶음이 바뀌는
    순간 얼린 문장으로 돌아가 옛 오류가 다시 보인다.
    """

    REPAIRS = {"a": {"title_kr": "한성숙 국무총리, 빌 게이츠 면담",
                     "summary": "고친 요약", "detail": "고친 상세"}}

    def setUp(self):
        self.store = {"dates": {}}
        sent = [_row("issue-gates", ["a", "b"], "한덕수 국무총리, 빌 게이츠 면담")]
        bs.apply([_briefing(sent)], self.store, "2026-09-24T00:00:00+09:00")
        self.split_now = [_row("issue-gates", ["a"], "한성숙 국무총리, 빌 게이츠 면담"),
                          _row("issue-smr", ["b"], "테라파워 공급망")]

    def test_a_frozen_card_shows_the_repaired_text(self):
        briefing = _briefing(self.split_now)
        bs.apply([briefing], self.store, "t", repairs=self.REPAIRS)
        card = briefing["issues"][0]
        self.assertIn("classification_note", card, "얼린 카드 경로를 타지 않았다")
        self.assertEqual((card["title"], card["headline_display"], card["summary"], card["detail"]),
                         ("한성숙 국무총리, 빌 게이츠 면담", "한성숙 국무총리, 빌 게이츠 면담",
                          "고친 요약", "고친 상세"))

    def test_the_ledger_keeps_what_was_frozen(self):
        bs.apply([_briefing(self.split_now)], self.store, "t", repairs=self.REPAIRS)
        fields = self.store["dates"][DAY]["cards"][0]["fields"]
        self.assertEqual(fields["title"], "한덕수 국무총리, 빌 게이츠 면담",
                         "정정이 원장(write-once)에 스며들었다")

    def test_without_a_repair_the_frozen_text_stands(self):
        briefing = _briefing(self.split_now)
        bs.apply([briefing], self.store, "t", repairs={"zz": {"title_kr": "다른 기사"}})
        self.assertEqual(briefing["issues"][0]["title"], "한덕수 국무총리, 빌 게이츠 면담")


class FileTests(unittest.TestCase):
    def test_round_trip_and_one_line_per_date(self):
        store = {"dates": {}}
        bs.apply([_briefing([_row("issue-x", ["a"], "제목")]),
                  _briefing([_row("issue-y", ["b"], "제목2")], day="2026-08-07")], store, "t")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "briefing_snapshots.json"
            bs.save(path, store)
            self.assertEqual(bs.load(path)["dates"], store["dates"])
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(sum(1 for line in lines if line.startswith('"2026-08-0')), 2)

    def test_missing_or_broken_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            self.assertEqual(bs.load(path)["dates"], {})
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(bs.load(path)["dates"], {})


if __name__ == "__main__":
    unittest.main()
