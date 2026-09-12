"""이슈 신원 원장 — 한 번 나간 주소는 죽지 않는다.

2026-09-12 실측이 이 파일의 존재 이유다. 9/1 빌드의 이슈 447건을 11일 뒤
라이브와 대조하니 75건(16.8%)이 사라졌고, 표본 5건 전부 HTTP 404 였다.
사라진 것들의 `first_seen` 은 08-26~08-31 로 **창 한복판**이었다 — 노화가
아니라 재클러스터링 이동이다. 같은 날 `rss.xml` 의 고유 이슈 링크 185건 중
33건(17.8%)이 이미 깨져 있었고, RSS 항목은 구독자 리더에 영구히 남는다.

원칙은 이 저장소의 다른 거부권과 같다: **놓치는 쪽이 잘못 넘기는 쪽보다 낫다.**
이동 판정은 어휘가 아니라 기사 해시로만 한다 — 제목 유사도로 이동을 판정하면
이미 여러 번 겪은 오병합을 신원 기록에까지 들인다.
"""
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import build_data  # noqa: E402
import issue_ledger  # noqa: E402


def _issue(issue_id, hashes, *, title="제목", first="2026-09-01",
           last="2026-09-01", briefings=1):
    return {
        "issue_id": issue_id, "title": title, "summary": "요약",
        "region": "국내", "first_seen": first, "last_seen": last,
        "article_count": len(hashes), "briefing_count": briefings,
        "topics": [], "related_articles": [{"hash": h} for h in hashes],
    }


class LedgerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "issue_ledger.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_absorbed_issue_points_at_its_new_home(self):
        """흡수된 이슈의 옛 주소는 현재 주소로 넘어간다."""
        issue_ledger.run([_issue("issue-A", ["h1", "h2"]), _issue("issue-B", ["h3"])],
                         today="2026-09-01", path=self.path)
        store = issue_ledger.run([_issue("issue-A", ["h1", "h2", "h3"])],
                                 today="2026-09-02", path=self.path)["store"]
        self.assertEqual(store["issues"]["issue-B"]["moved_to"], "issue-A")
        self.assertEqual(issue_ledger.redirects(store, {"issue-A"}), {"issue-B": "issue-A"})

    def test_an_issue_that_merely_aged_out_keeps_its_own_page(self):
        """창 밖으로 나간 것은 이동이 아니다 — 넘기지 않고 보관 페이지를 세운다."""
        issue_ledger.run([_issue("issue-A", ["h1"])], today="2026-09-01", path=self.path)
        store = issue_ledger.run([], today="2026-11-01", path=self.path)["store"]
        self.assertEqual(store["issues"]["issue-A"]["moved_to"], "")
        self.assertEqual([row["issue_id"] for row in issue_ledger.archived(store, set())],
                         ["issue-A"])

    def test_an_unrelated_catalog_never_captures_a_retired_issue(self):
        """기사를 하나도 공유하지 않는 이슈로는 절대 넘기지 않는다.

        이동 판정의 유일한 재료가 해시라는 것을 잠근다. 여기가 뚫리면 원장이
        서로 다른 사건을 같은 주소로 잇는다.
        """
        issue_ledger.run([_issue("issue-A", ["h1"])], today="2026-09-01", path=self.path)
        store = issue_ledger.run([_issue("issue-Z", ["z9"])],
                                 today="2026-09-02", path=self.path)["store"]
        self.assertEqual(store["issues"]["issue-A"]["moved_to"], "")

    def test_every_retired_address_still_resolves_somewhere(self):
        """흡수된 뒤 그 대상마저 창 밖으로 나가도 옛 주소는 살아 있어야 한다.

        리다이렉트 도착지를 '살아 있는 이슈'로만 두면 여기서 구멍이 났다:
        A 는 보관 페이지를 얻는데 B 는 도착지를 잃고 다시 404 가 된다.
        흡수됐다는 이유로 더 일찍 사라지는 셈이라 원장의 존재 이유와 어긋난다.
        """
        issue_ledger.run([_issue("issue-A", ["h1"]), _issue("issue-B", ["h2"])],
                         today="2026-09-01", path=self.path)
        issue_ledger.run([_issue("issue-A", ["h1", "h2"])],
                         today="2026-09-02", path=self.path)
        store = issue_ledger.run([], today="2026-11-01", path=self.path)["store"]
        served = ({row["issue_id"] for row in issue_ledger.archived(store, set())}
                  | set(issue_ledger.redirects(store, set())))
        self.assertEqual(served, {"issue-A", "issue-B"})

    def test_title_changes_are_kept_so_a_citation_can_be_restored(self):
        """살아남은 이슈의 18.8%가 11일 만에 제목이 바뀐다(실측). 보고서 각주가
        가리키는 주소가 다른 말을 하기 시작하면 인용이 성립하지 않는다."""
        issue_ledger.run([_issue("issue-A", ["h1"], title="처음 제목")],
                         today="2026-09-01", path=self.path)
        store = issue_ledger.run([_issue("issue-A", ["h1"], title="바뀐 제목")],
                                 today="2026-09-02", path=self.path)["store"]
        self.assertEqual([row["title"] for row in store["issues"]["issue-A"]["revisions"]],
                         ["처음 제목", "바뀐 제목"])

    def test_a_returning_issue_stops_redirecting(self):
        """이동했다가 같은 id 로 되살아나면 이동 표시를 지운다 — 안 지우면
        살아 있는 주소가 남의 주소로 넘긴다."""
        issue_ledger.run([_issue("issue-A", ["h1"]), _issue("issue-B", ["h2"])],
                         today="2026-09-01", path=self.path)
        issue_ledger.run([_issue("issue-A", ["h1", "h2"])],
                         today="2026-09-02", path=self.path)
        store = issue_ledger.run([_issue("issue-A", ["h1"]), _issue("issue-B", ["h2"])],
                                 today="2026-09-03", path=self.path)["store"]
        self.assertEqual(store["issues"]["issue-B"]["moved_to"], "")
        self.assertEqual(issue_ledger.redirects(store, {"issue-A", "issue-B"}), {})

    def test_the_ledger_is_idempotent(self):
        """같은 카탈로그를 두 번 태워도 원장이 자라지 않는다.

        빌드는 하루에 여러 번 돌고 `deploy-web.yml` 은 커밋하지 않으므로
        같은 입력이 반복해서 들어온다.
        """
        catalog = [_issue("issue-A", ["h1"])]
        first = issue_ledger.run(catalog, today="2026-09-01", path=self.path)["store"]
        second = issue_ledger.run(catalog, today="2026-09-01", path=self.path)["store"]
        self.assertEqual(first["issues"], second["issues"])

    def test_alias_chain_survives_a_cycle(self):
        """원장이 자기를 가리키는 고리를 만나도 멈춘다."""
        store = {"issues": {
            "a": {"issue_id": "a", "moved_to": "b"},
            "b": {"issue_id": "b", "moved_to": "a"},
        }}
        self.assertIn(issue_ledger.alias_target(store, "a"), {"", "a", "b"})

    def test_backfill_recovers_addresses_from_delivery_history(self):
        """원장을 오늘부터 시작하면 이미 깨진 주소는 영영 404 다. 발송 이력에
        story_id 와 대표 해시가 남아 있어 그 신원은 되살릴 수 있다."""
        rows = issue_ledger.backfill_rows([
            {"hash": "h1", "story_id": "story-x", "title_kr": "옛 제목",
             "date": "2026-07-14", "region": "국내"},
            {"hash": "h2", "story_id": "story-x", "title_kr": "새 제목",
             "date": "2026-07-20", "region": "국내"},
            {"record_type": "alert", "hash": "h9", "story_id": "story-y"},
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["issue_id"], "story-x")
        self.assertEqual(rows[0]["first_seen"], "2026-07-14")
        self.assertEqual(rows[0]["last_seen"], "2026-07-20")
        self.assertEqual(sorted(rows[0]["hashes"]), ["h1", "h2"])


class IssuePagePersistenceTests(unittest.TestCase):
    """페이지 생성이 카탈로그가 아니라 원장을 본다.

    `build_issue_pages` 는 `SITE_DIR/public/issue` 를 **통째로 지우고** 다시
    만든다. 그래서 이 테스트는 SITE_DIR 까지 임시 디렉터리로 돌려야 한다 —
    안 그러면 빌드 산출물을 지워 놓고 그것을 읽는 다른 테스트를 깨뜨린다
    (실제로 한 번 그랬다).
    """

    def _sandbox(self, tmp: Path) -> None:
        (tmp / "public").mkdir(parents=True, exist_ok=True)
        template = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        (tmp / "public" / "index.html").write_text(template, encoding="utf-8")

    def test_build_issue_pages_serves_live_archived_and_moved(self):
        tmp = Path(tempfile.mkdtemp())
        self._sandbox(tmp)
        catalog = [_issue("issue-live", ["h1"], title="살아 있는 이슈")]
        ledger = {"generated_at": "", "issues": {
            "issue-live": {"issue_id": "issue-live", "title": "살아 있는 이슈",
                           "summary": "요약", "first_seen": "2026-09-01",
                           "last_seen": "2026-09-02", "article_count": 1,
                           "briefing_count": 1, "topics": [], "hashes": ["h1"],
                           "revisions": [], "moved_to": ""},
            "issue-old": {"issue_id": "issue-old", "title": "보관된 이슈",
                          "summary": "옛 요약", "first_seen": "2026-06-01",
                          "last_seen": "2026-06-02", "article_count": 1,
                          "briefing_count": 1, "topics": [], "hashes": ["h8"],
                          "revisions": [], "moved_to": ""},
            "issue-gone": {"issue_id": "issue-gone", "title": "흡수된 이슈",
                           "summary": "", "first_seen": "2026-09-01",
                           "last_seen": "2026-09-01", "article_count": 1,
                           "briefing_count": 1, "topics": [], "hashes": ["h1"],
                           "revisions": [], "moved_to": "issue-live"},
        }}
        original_out, original_site = build_data.OUT_DIR, build_data.SITE_DIR
        build_data.OUT_DIR = tmp / "data"
        build_data.SITE_DIR = tmp
        try:
            counts = build_data.build_issue_pages(catalog, ledger)
            self.assertEqual(counts, {"live": 1, "archived": 1, "moved": 1})
            pages = tmp / "public" / "issue"
            self.assertTrue((pages / "issue-live" / "index.html").exists())
            self.assertTrue((pages / "issue-old" / "index.html").exists())
            moved = (pages / "issue-gone" / "index.html").read_text(encoding="utf-8")
            self.assertIn("/issue/issue-live/", moved)
            snapshot = json.loads(
                (tmp / "data" / "issue" / "issue-old.json").read_text(encoding="utf-8"))
            self.assertTrue(snapshot["archived"])
            self.assertEqual(snapshot["title"], "보관된 이슈")
            self.assertNotIn("related_articles", snapshot)
        finally:
            build_data.OUT_DIR, build_data.SITE_DIR = original_out, original_site
            shutil.rmtree(tmp, ignore_errors=True)

    def test_pages_still_build_without_a_ledger(self):
        """원장이 아직 없는 환경(첫 실행·테스트)에서도 예전과 같이 동작한다."""
        tmp = Path(tempfile.mkdtemp())
        self._sandbox(tmp)
        original_out, original_site = build_data.OUT_DIR, build_data.SITE_DIR
        build_data.OUT_DIR = tmp / "data"
        build_data.SITE_DIR = tmp
        try:
            counts = build_data.build_issue_pages([_issue("issue-live", ["h1"])], None)
            self.assertEqual(counts["live"], 1)
            self.assertEqual(counts["archived"], 0)
        finally:
            build_data.OUT_DIR, build_data.SITE_DIR = original_out, original_site
            shutil.rmtree(tmp, ignore_errors=True)


class RssGuidStabilityTests(unittest.TestCase):
    """GUID 가 issue_id 를 타면 재클러스터링마다 같은 사건이 새 항목으로 재발행된다."""

    def test_guid_follows_the_delivered_article_not_the_cluster(self):
        card = {"title": "제목", "summary": "요약",
                "representative_article": {"hash": "abc123"}}
        before = {"date": "2026-09-01", "issues": [{**card, "issue_id": "issue-first"}]}
        after = {"date": "2026-09-01",
                 "issues": [{**card, "issue_id": "issue-second-after-recluster"}]}
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        first = build_data.build_rss([before], now).decode("utf-8")
        second = build_data.build_rss([after], now).decode("utf-8")
        self.assertIn("abc123:2026-09-01", first)
        self.assertIn("abc123:2026-09-01", second)
        self.assertNotIn("issue-first:2026-09-01", first)

    def test_guid_falls_back_when_an_old_row_has_no_article_hash(self):
        briefing = {"date": "2026-09-01",
                    "issues": [{"issue_id": "issue-x", "title": "제목", "summary": ""}]}
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.assertIn("issue-x:2026-09-01",
                      build_data.build_rss([briefing], now).decode("utf-8"))


class StoryScopeTests(unittest.TestCase):
    """스토리 목록의 자격 바 — 카탈로그 전체를 스토리라고 부르지 않는다.

    라이브 실측 2026-09-12: 525건 중 421건(80.2%)이 단 한 회차에만 나타났고
    268건(51.0%)은 기사가 1건이다. 그런 이슈의 상세에는 타임라인도 변화도 설
    자리가 없다. 바를 넘은 174건(33.1%)은 성질이 다르다 — 기사 2건 이상 100%,
    서로 다른 날짜 3건 이상 83.9%, 검증 97.1%, 기사 수 중앙값 5.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")

    def test_scope_switch_defaults_to_stories(self):
        self.assertIn('id="archiveScope"', self.html)
        self.assertIn('data-scope="stories"', self.html)
        self.assertIn('data-scope="all"', self.html)
        self.assertIn('archiveScope: "stories"', self.script)

    def test_eligibility_uses_both_briefings_and_distinct_dates(self):
        """회차는 '우리가 며칠에 걸쳐 다뤘나'이고 날짜는 '사건이 며칠에 걸쳐
        움직였나'다. 하나만 쓰면 한쪽 종류의 스토리가 통째로 빠진다."""
        self.assertIn("function storyEligible(", self.script)
        self.assertIn("STORY_MIN_BRIEFINGS = 2", self.script)
        self.assertIn("STORY_MIN_DATES = 3", self.script)

    def test_empty_stories_offer_the_full_catalog(self):
        """스토리 범위에서 0건이면 원인이 필터가 아니라 범위일 수 있다.
        필터 해제만 안내하면 막다른 길이 된다."""
        self.assertIn('data-archive-scope="all"', self.script)

    def test_scope_is_only_written_to_the_url_when_it_is_not_the_default(self):
        """공유된 주소가 기본값을 들고 다니면 나중에 기본이 바뀔 때 옛 링크가
        옛 화면을 고집한다."""
        self.assertIn('if (state.archiveScope !== "stories") params.set("as"', self.script)

    def test_archived_issue_detail_is_rendered_not_silently_dropped(self):
        """카탈로그에 없는 이슈를 열면 예전에는 조용히 아무 일도 안 했다
        (`if (!issue) return`). 공유된 주소를 연 사람에게는 빈 화면이다."""
        self.assertIn("function openArchivedIssueDialog(", self.script)
        self.assertIn("function loadArchivedIssue(", self.script)
        self.assertIn("/issue/${encodeURIComponent(issueId)}.json", self.script)

    def test_the_story_tab_replaced_explore_rather_than_adding_a_fifth(self):
        """탭을 하나 더 만들면 모바일 하단바가 다섯이 되고, 같은 데이터의
        목록이 둘이 된다."""
        self.assertEqual(self.html.count('data-view="search"'), 2)
        self.assertIn(">스토리</button>", self.html)
        self.assertNotIn(">탐색</button>", self.html)


if __name__ == "__main__":
    unittest.main()
