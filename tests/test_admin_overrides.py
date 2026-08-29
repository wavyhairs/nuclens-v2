"""운영 콘솔 판정이 수집·선정에 실제로 얹히는가.

이 기능의 실패는 조용하다. 관리자가 콘솔에서 키워드를 지우고 기사를 갈라 놓아도
파이프라인이 그것을 안 읽으면 화면만 바뀌고 검색은 그대로 돈다 — 그리고 그 사실은
며칠 뒤 "왜 아직도 이 기사가 오지?"로만 드러난다. 그래서 여기서 보는 것은 파일
파싱이 아니라 **덧칠이 실제 호출 지점에 닿는가**다.

또 하나: 이 모듈은 어떤 이유로도 예외를 올리면 안 된다. 올리면 수집이 통째로 선다.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import admin_overrides  # noqa: E402


_REAL_OVERRIDES_FILE = admin_overrides.OVERRIDES_FILE


def write_overrides(entries: list[dict]) -> Path:
    """임시 판정 파일을 만들고 **모듈 기본 경로도** 그리로 돌린다.

    기본 경로까지 바꾸는 이유: 파이프라인 호출 지점(ranking·news_bot·dedup)은
    경로를 넘기지 않고 `merge_blocked(a, b)` 만 부른다. 인자로만 갈아 끼우면
    모듈 단위 테스트는 통과하면서 실제 경로는 저장소 파일을 계속 읽는다 —
    이 파일이 제일 잡고 싶은 실패가 바로 그것이다.
    """
    path = Path(tempfile.mkdtemp()) / "admin_overrides.json"
    path.write_text(json.dumps({"version": 1, "entries": entries}, ensure_ascii=False),
                    encoding="utf-8")
    admin_overrides.OVERRIDES_FILE = path
    admin_overrides.reload()
    return path


def restore_overrides() -> None:
    admin_overrides.OVERRIDES_FILE = _REAL_OVERRIDES_FILE
    admin_overrides.reload()


class OverlayNeverBreaksTheRunTests(unittest.TestCase):
    """덧칠이 죽으면 수집이 통째로 선다 — 어떤 입력에도 예외가 나가면 안 된다."""

    def tearDown(self):
        restore_overrides()

    def test_missing_file_means_no_overlay(self):
        missing = Path(tempfile.mkdtemp()) / "nope.json"
        self.assertEqual(admin_overrides.load(missing)["entries"], [])
        self.assertEqual(admin_overrides.blocked_pairs(missing), set())

    def test_broken_json_falls_back_to_base_behaviour(self):
        path = Path(tempfile.mkdtemp()) / "broken.json"
        path.write_text("{ 이건 JSON 이 아니다", encoding="utf-8")
        admin_overrides.reload()
        base = {"정책": {"keywords": ["원전"], "anchors": [], "negative_terms": ""}}
        self.assertEqual(admin_overrides.keywords_config(base, path), base)
        self.assertIsNone(admin_overrides.merge_blocked({"hash": "a"}, {"hash": "b"}, path))

    def test_unknown_kinds_are_ignored_not_fatal(self):
        """콘솔이 이 배포보다 새 종류를 쓸 수 있다. 모르면 무시하고 지나간다."""
        path = write_overrides([
            {"id": "x1", "kind": "미래에_생길_종류", "value": "무엇"},
            {"id": "x2", "kind": "anti_add", "value": "체육대회"},
        ])
        self.assertEqual(admin_overrides.anti_keywords(["기존"], path), ["기존", "체육대회"])

    def test_entries_without_an_id_are_dropped(self):
        """id 가 없으면 지울 수 없다 — 지울 수 없는 판정은 남기지 않는다."""
        path = write_overrides([{"kind": "anti_add", "value": "이름표없음"}])
        self.assertEqual(admin_overrides.anti_keywords(["기존"], path), ["기존"])


class KeywordOverlayTests(unittest.TestCase):
    """검색 키워드 — 콘솔 편집이 기본 파일을 덮어쓰지 않는다."""

    def setUp(self):
        self.base = {
            "정책": {
                "keywords": ["원자력 정책", "원전 수출"],
                "anchors": ["원자력"],
                "negative_terms": "-주가 -채용",
            },
        }

    def tearDown(self):
        restore_overrides()

    def test_add_and_remove_leave_the_base_dict_untouched(self):
        """덧칠이 원본을 건드리면 저장소 손편집과 콘솔 편집이 서로를 지운다."""
        path = write_overrides([
            {"id": "k1", "kind": "keyword_add", "group": "정책", "value": "SMR 표준설계"},
            {"id": "k2", "kind": "keyword_remove", "group": "정책", "value": "원전 수출"},
        ])
        merged = admin_overrides.keywords_config(self.base, path)
        self.assertIn("SMR 표준설계", merged["정책"]["keywords"])
        self.assertNotIn("원전 수출", merged["정책"]["keywords"])
        # 원본은 그대로다.
        self.assertEqual(self.base["정책"]["keywords"], ["원자력 정책", "원전 수출"])

    def test_negative_terms_round_trip_through_the_query_string(self):
        """화면은 칩으로 보여 주고 저장은 `-말` 문자열이다 — 왕복이 안 맞으면 샌다."""
        path = write_overrides([
            {"id": "n1", "kind": "negative_add", "group": "정책", "value": "부고"},
            {"id": "n2", "kind": "negative_remove", "group": "정책", "value": "주가"},
        ])
        merged = admin_overrides.keywords_config(self.base, path)
        terms = admin_overrides.negative_terms_list(merged["정책"]["negative_terms"])
        self.assertEqual(terms, ["채용", "부고"])
        # 앞의 '-' 를 붙여 저장한다 — news_bot.parse_negative_terms 가 그 형식을 읽는다.
        self.assertEqual(merged["정책"]["negative_terms"], "-채용 -부고")

    def test_negative_add_accepts_the_leading_dash_people_type(self):
        path = write_overrides([
            {"id": "n3", "kind": "negative_add", "group": "정책", "value": "-공모주"},
        ])
        merged = admin_overrides.keywords_config(self.base, path)
        self.assertIn("공모주", admin_overrides.negative_terms_list(merged["정책"]["negative_terms"]))

    def test_edits_to_a_group_that_no_longer_exists_are_ignored(self):
        """저장소에서 그룹을 지웠는데 콘솔 판정이 남아 있어도 새 그룹을 만들지 않는다.

        그룹은 키워드·앵커·제외어 한 벌이다. 앵커 없는 그룹이 생기면 원자력 문맥
        확인 없이 검색이 나가고, 그건 잡음을 그대로 통과시킨다.
        """
        path = write_overrides([
            {"id": "k9", "kind": "keyword_add", "group": "없어진그룹", "value": "무엇"},
        ])
        merged = admin_overrides.keywords_config(self.base, path)
        self.assertEqual(list(merged), ["정책"])

    def test_anti_keywords_add_and_remove(self):
        path = write_overrides([
            {"id": "a1", "kind": "anti_add", "value": "체육대회"},
            {"id": "a2", "kind": "anti_remove", "value": "동호회"},
        ])
        merged = admin_overrides.anti_keywords(["동호회", "인사발령"], path)
        self.assertEqual(merged, ["인사발령", "체육대회"])


class FeedOverlayTests(unittest.TestCase):
    def tearDown(self):
        restore_overrides()

    def test_added_feed_carries_the_fields_news_bot_reads(self):
        path = write_overrides([{
            "id": "f1", "kind": "feed_add", "url": "https://example.com/feed",
            "name": "예시", "domain_label": "example.com",
            "require_keywords": ["Nuclear", "원전"],
        }])
        feeds = admin_overrides.rss_sources([{"url": "https://a/feed", "name": "A"}], path)
        added = feeds[-1]
        self.assertEqual(added["name"], "예시")
        self.assertEqual(added["domain_label"], "example.com")
        # news_bot.passes_source_keyword_gate 는 소문자 부분일치로 본다.
        self.assertEqual(added["require_keywords"], ("nuclear", "원전"))

    def test_non_http_feed_urls_are_refused(self):
        """javascript:·file: 은 수집기가 열 주소가 아니다."""
        path = write_overrides([
            {"id": "f2", "kind": "feed_add", "url": "file:///etc/passwd", "name": "나쁨"},
        ])
        self.assertEqual(admin_overrides.rss_sources([], path), [])

    def test_disable_removes_a_built_in_feed_by_url(self):
        base = [{"url": "https://a/feed", "name": "A"}, {"url": "https://b/feed", "name": "B"}]
        path = write_overrides([
            {"id": "f3", "kind": "feed_disable", "target": "https://a/feed"},
        ])
        self.assertEqual([row["name"] for row in admin_overrides.rss_sources(base, path)], ["B"])

    def test_a_console_add_that_repeats_a_built_in_feed_is_dropped(self):
        """콘솔에서 이미 있는 피드를 다시 넣어도 두 줄이 되지 않는다.

        인코딩만 다른 같은 주소가 실제로 그렇게 섰다 — 코드는 quote_plus 라
        공백이 `+`, 콘솔에 붙여 넣은 것은 `%20` 이었고, 둘이 따로 서서 서울대
        NIFTEP 피드를 매 수집마다 두 번 걸었다(2026-08-29).
        """
        base = [{"url": "https://news.google.com/rss/search?q=site%3Ax.kr+when%3A3d",
                 "name": "엑스", "domain_label": "x.kr"}]
        path = write_overrides([{
            "id": "f9", "kind": "feed_add",
            "url": "https://news.google.com/rss/search?q=site%3Ax.kr%20when%3A3d",
            "name": "엑스(콘솔)", "domain_label": "x.kr",
        }])
        feeds = admin_overrides.rss_sources(base, path)
        self.assertEqual([row["name"] for row in feeds], ["엑스"])

    def test_the_same_domain_may_carry_two_different_feeds(self):
        """중복 판정은 도메인이 아니라 **주소**로 한다.

        energy.gov 에는 DOE 뉴스룸과 DOE 원자력국이 서로 다른 RSS 로 있다.
        도메인으로 묶으면 멀쩡한 둘째 피드가 막힌다.
        """
        base = [{"url": "https://www.energy.gov/newsroom/rss.xml",
                 "name": "DOE", "domain_label": "energy.gov"}]
        path = write_overrides([{
            "id": "f10", "kind": "feed_add", "url": "https://www.energy.gov/ne/rss.xml",
            "name": "DOE 원자력국", "domain_label": "energy.gov",
        }])
        feeds = admin_overrides.rss_sources(base, path)
        self.assertEqual([row["name"] for row in feeds], ["DOE", "DOE 원자력국"])

    def test_two_identical_console_adds_land_once(self):
        """같은 판단을 두 번 눌러도 수집이 두 배가 되지 않는다."""
        path = write_overrides([
            {"id": "f11", "kind": "feed_add", "url": "https://ex.com/feed?a=1&b=2",
             "name": "첫 번째", "domain_label": "ex.com"},
            # 질의 순서만 다른 같은 요청.
            {"id": "f12", "kind": "feed_add", "url": "https://ex.com/feed?b=2&a=1",
             "name": "두 번째", "domain_label": "ex.com"},
        ])
        feeds = admin_overrides.rss_sources([], path)
        self.assertEqual([row["name"] for row in feeds], ["첫 번째"])

    def test_a_broken_url_still_does_not_raise(self):
        """이 모듈은 예외를 올리지 않는다 — 죽으면 수집이 통째로 선다."""
        path = write_overrides([{
            "id": "f13", "kind": "feed_add", "url": "https://[oops/feed", "name": "깨짐",
        }])
        self.assertIsInstance(admin_overrides.rss_sources([], path), list)

    def test_official_sources_can_only_be_disabled(self):
        """기관 게시판은 전용 파서가 코드에 있어야 읽힌다 — 화면 추가를 허용하지 않는다."""
        base = [{"url": "https://khnp/x", "name": "한수원", "kind": "khnp_html"}]
        path = write_overrides([
            {"id": "o1", "kind": "official_disable", "target": "https://khnp/x"},
            # 추가 종류 자체가 없으므로 아래는 아무 일도 하지 않는다.
            {"id": "o2", "kind": "feed_add", "url": "https://x/y", "name": "새 기관"},
        ])
        self.assertEqual(admin_overrides.official_sources(base, path), [])


class SourceTierOverlayTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "tier1_bonus": 40,
            "tier1": [{"domain": "iaea.org", "name": "IAEA", "aliases": ["IAEA"],
                       "source_type": "official", "evidence_role": "primary", "rank_tier": 1}],
            "tier2": [],
            "tier3": [],
        }

    def tearDown(self):
        restore_overrides()

    def test_moving_a_domain_removes_it_from_the_old_tier(self):
        """두 등급에 동시에 있으면 sources.credibility 가 먼저 만난 쪽을 쓴다 —
        그러면 등급이 파일 순서에 따라 달라진다."""
        path = write_overrides([{
            "id": "t1", "kind": "tier_upsert", "domain": "iaea.org", "tier": 2,
            "name": "IAEA", "evidence_role": "independent",
        }])
        merged = admin_overrides.sources_config(self.base, path)
        self.assertEqual(merged["tier1"], [])
        self.assertEqual(len(merged["tier2"]), 1)
        row = merged["tier2"][0]
        self.assertEqual(row["rank_tier"], 2)
        self.assertEqual(row["evidence_role"], "independent")
        # 손대지 않은 필드는 원래 값을 유지한다.
        self.assertEqual(row["source_type"], "official")
        self.assertEqual(row["aliases"], ["IAEA"])

    def test_remove_drops_the_domain_from_every_tier(self):
        path = write_overrides([
            {"id": "t2", "kind": "tier_remove", "domain": "iaea.org"},
        ])
        merged = admin_overrides.sources_config(self.base, path)
        self.assertEqual(merged["tier1"] + merged["tier2"] + merged["tier3"], [])

    def test_a_new_domain_gets_sane_defaults(self):
        path = write_overrides([{
            "id": "t3", "kind": "tier_upsert", "domain": "neimagazine.com", "tier": 2,
            "name": "NEI",
        }])
        merged = admin_overrides.sources_config(self.base, path)
        row = merged["tier2"][0]
        self.assertEqual(row["source_type"], "unknown")
        self.assertEqual(row["evidence_role"], "unknown")
        self.assertEqual(row["aliases"], ["NEI"])

    def test_the_two_loaders_see_the_same_overlay(self):
        """sources.py 와 data_quality.py 가 각자 파일을 읽는다 — 덧칠이 한쪽에만
        걸리면 '선정 점수는 tier1 인데 화면 배지는 tier3' 이 된다."""
        source = (ROOT / "sources.py").read_text(encoding="utf-8")
        quality = (ROOT / "data_quality.py").read_text(encoding="utf-8")
        for name, text in (("sources.py", source), ("data_quality.py", quality)):
            self.assertIn("admin_overrides.sources_config", text, name)


class MergeVetoTests(unittest.TestCase):
    """분리 판정과 학습된 판별축.

    발동 조건은 event_stage.stage_conflict 와 같은 보수성이다 — **양쪽 다 말했고
    겹치는 축이 하나도 없을 때만.** 한쪽이 침묵하면 판정하지 않는다. 사람이 규칙
    하나를 잘못 배워도 무관한 기사를 갈라 놓지 않게 하는 것이 여기서 제일 중요하다.
    """

    RULE = {
        "id": "lr-1", "kind": "learned_rule", "label": "고리2 ↔ 한빛3",
        "left_terms": ["고리 2호기"], "right_terms": ["한빛 3호기"],
    }

    def tearDown(self):
        restore_overrides()

    def article(self, hash_: str, title: str) -> dict:
        return {"hash": hash_, "title_kr": title, "title": ""}

    def test_an_explicit_pair_is_blocked_in_both_directions(self):
        path = write_overrides([
            {"id": "s1", "kind": "story_split", "left_hash": "aaa", "right_hash": "bbb"},
        ])
        left, right = self.article("aaa", "한쪽"), self.article("bbb", "다른 쪽")
        for first, second in ((left, right), (right, left)):
            veto = admin_overrides.merge_blocked(first, second, path)
            self.assertIsNotNone(veto)
            self.assertEqual(veto["kind"], "admin_split")

    def test_an_unrelated_pair_is_not_blocked(self):
        path = write_overrides([
            {"id": "s2", "kind": "story_split", "left_hash": "aaa", "right_hash": "bbb"},
        ])
        self.assertIsNone(admin_overrides.merge_blocked(
            self.article("aaa", "한쪽"), self.article("ccc", "무관"), path))

    def test_a_learned_axis_applies_to_articles_it_never_saw(self):
        """이것이 '학습'의 본체다 — 원래 쌍이 아닌 새 조합에도 적용된다."""
        path = write_overrides([self.RULE])
        veto = admin_overrides.merge_blocked(
            self.article("new1", "고리 2호기 계속운전 심사 착수"),
            self.article("new2", "한빛 3호기 계속운전 심사 착수"), path)
        self.assertIsNotNone(veto)
        self.assertEqual(veto["kind"], "learned_rule")
        self.assertEqual(veto["rule_id"], "lr-1")

    def test_spacing_does_not_change_the_verdict(self):
        """'고리2호기' 와 '고리 2호기' 는 같은 말이다 — 띄어쓰기로 규칙이 빠져나가면
        관리자는 같은 판정을 여러 번 저장하게 된다."""
        path = write_overrides([self.RULE])
        self.assertIsNotNone(admin_overrides.merge_blocked(
            self.article("n1", "고리2호기 재가동"),
            self.article("n2", "한빛3호기 재가동"), path))

    def test_silence_on_one_side_means_no_verdict(self):
        """표식이 없다는 것은 '다르다'가 아니라 '못 읽었다'이다."""
        path = write_overrides([self.RULE])
        self.assertIsNone(admin_overrides.merge_blocked(
            self.article("n1", "고리 2호기 재가동"),
            self.article("n2", "국내 원전 재가동 현황"), path))

    def test_an_article_naming_both_sides_is_not_split(self):
        """두 축을 다 말하는 기사는 어느 쪽도 아니다 — 겹치면 판정하지 않는다."""
        path = write_overrides([self.RULE])
        self.assertIsNone(admin_overrides.merge_blocked(
            self.article("n1", "고리 2호기와 한빛 3호기 동시 점검"),
            self.article("n2", "한빛 3호기 점검"), path))

    def test_a_rule_missing_one_side_is_dropped(self):
        """한쪽 축만 있는 규칙은 아무것도 가르지 못한다."""
        path = write_overrides([
            {"id": "lr-2", "kind": "learned_rule", "left_terms": ["고리"], "right_terms": []},
        ])
        self.assertEqual(admin_overrides.learned_rules(path), [])

    def test_a_disabled_entry_stops_applying(self):
        path = write_overrides([{**self.RULE, "enabled": False}])
        self.assertIsNone(admin_overrides.merge_blocked(
            self.article("n1", "고리 2호기 재가동"),
            self.article("n2", "한빛 3호기 재가동"), path))

    def test_issue_pairs_come_out_in_the_repository_file_shape(self):
        """build_data 가 저장소 파일과 콘솔 판정을 구분하지 않고 합칠 수 있어야 한다."""
        path = write_overrides([
            {"id": "i1", "kind": "issue_join", "left_hash": "a", "right_hash": "b",
             "created_at": "2026-08-16T01:00:00Z", "note": "같은 사건"},
            {"id": "i2", "kind": "issue_split", "left_hash": "c", "right_hash": "d"},
        ])
        pairs = admin_overrides.issue_pair_overrides(path)
        self.assertEqual(len(pairs["approved"]), 1)
        self.assertEqual(len(pairs["rejected"]), 1)
        self.assertEqual(pairs["approved"][0]["left_hash"], "a")
        self.assertEqual(pairs["approved"][0]["reviewed_at"], "2026-08-16")
        self.assertEqual(pairs["approved"][0]["origin"], "admin_console")


class GroupSplitTests(unittest.TestCase):
    """"이 묶음은 사실 두 사건이다" — 쌍이 아니라 선으로 저장되는 판정.

    왜 선인가는 `admin_overrides.group_splits` 의 주석과
    `web/tests/test_prototype.py` 의 재현 테스트에 있다. 요지는 쌍 하나로는
    갈라지지 않는다는 것이다 — 막히지 않은 다른 멤버를 통해 도로 합쳐진다.
    여기서 보는 것은 항목 하나가 **선을 가로지르는 쌍 전부**로 펼쳐지는가다.
    """

    SPLIT = {
        "id": "g-1", "kind": "issue_group_split", "issue_id": "issue-k1",
        "left_hashes": ["k1", "k2", "k3", "k4"], "right_hashes": ["m1", "m2"],
        "note": "한쪽은 계속운전 심사, 다른 쪽은 수출 업무협약",
        "created_at": "2026-08-16T02:00:00Z",
    }

    def tearDown(self):
        restore_overrides()

    def test_one_entry_becomes_every_pair_that_crosses_the_line(self):
        path = write_overrides([self.SPLIT])
        rejected = admin_overrides.issue_pair_overrides(path)["rejected"]
        self.assertEqual(len(rejected), 8)
        self.assertEqual(
            {(row["left_hash"], row["right_hash"]) for row in rejected},
            {(left, right) for left in ("k1", "k2", "k3", "k4") for right in ("m1", "m2")})
        # 같은 쪽끼리는 갈라지지 않는다 — 선 안쪽은 여전히 한 사건이다.
        self.assertNotIn(("k1", "k2"), {(r["left_hash"], r["right_hash"]) for r in rejected})
        self.assertEqual(rejected[0]["origin"], "admin_console")
        self.assertEqual(rejected[0]["entry_id"], "g-1", "지울 때 되짚을 항목 id 가 없다")
        self.assertEqual(rejected[0]["reviewed_at"], "2026-08-16")

    def test_the_line_also_stops_the_same_day_fold(self):
        """다른 사건이면 같은 날 한 카드로 접혀서도 안 된다.

        관리자가 말한 것은 "이 넷과 저 둘은 다른 사건"이지 "날짜를 넘는 연결만
        끊어라"가 아니다. 이슈 계층에서만 막으면 같은 날 나란히 실린 두 건이
        `ranking.cluster_duplicates` 에서 그대로 접힌다.
        """
        path = write_overrides([self.SPLIT])
        pairs = admin_overrides.blocked_pairs(path)
        self.assertIn(frozenset(("k1", "m1")), pairs)
        self.assertNotIn(frozenset(("k1", "k2")), pairs)
        veto = admin_overrides.merge_blocked(
            {"hash": "k4", "title_kr": "고리 3·4호기 계속운전 심사", "title": ""},
            {"hash": "m2", "title_kr": "한국형 원전 해외진출 업무협약", "title": ""}, path)
        self.assertIsNotNone(veto)
        self.assertEqual(veto["kind"], "admin_split")

    def test_a_one_sided_line_is_ignored(self):
        """한쪽이 비면 가를 것이 없다. 저장 창구가 막지만 손편집도 있다."""
        path = write_overrides([{**self.SPLIT, "right_hashes": []}])
        self.assertEqual(admin_overrides.issue_pair_overrides(path)["rejected"], [])
        self.assertEqual(admin_overrides.blocked_pairs(path), set())

    def test_an_article_standing_on_both_sides_never_splits_from_itself(self):
        path = write_overrides([{**self.SPLIT, "right_hashes": ["k1", "m1"]}])
        rejected = admin_overrides.issue_pair_overrides(path)["rejected"]
        self.assertNotIn("k1", {row["right_hash"] for row in rejected})
        self.assertEqual(len(rejected), 4, "왼쪽 4건 × 오른쪽 1건이 아니다")

    def test_deleting_the_entry_removes_the_whole_line_at_once(self):
        """사람이 한 판단은 하나다 — 절반만 남는 상태가 만들어지면 안 된다."""
        path = write_overrides([self.SPLIT])
        self.assertEqual(len(admin_overrides.issue_pair_overrides(path)["rejected"]), 8)
        path = write_overrides([])
        self.assertEqual(admin_overrides.issue_pair_overrides(path)["rejected"], [])

    def test_a_disabled_line_stops_applying(self):
        path = write_overrides([{**self.SPLIT, "enabled": False}])
        self.assertEqual(admin_overrides.issue_pair_overrides(path)["rejected"], [])
        self.assertEqual(admin_overrides.blocked_pairs(path), set())

    def test_a_broken_line_does_not_raise(self):
        """이 모듈은 어떤 입력에도 예외를 올리지 않는다 — 올리면 수집이 선다."""
        path = write_overrides([
            {"id": "g-2", "kind": "issue_group_split", "left_hashes": "문자열", "right_hashes": 7},
            {"id": "g-3", "kind": "issue_group_split"},
        ])
        self.assertEqual(admin_overrides.group_splits(path), [])
        self.assertEqual(admin_overrides.issue_pair_overrides(path)["rejected"], [])


class VetoIsWiredIntoThePipelineTests(unittest.TestCase):
    """모듈이 옳아도 호출 지점에 안 꽂혀 있으면 아무 일도 일어나지 않는다.

    이것이 이 파일에서 제일 중요한 테스트다. 파싱 테스트는 전부 통과하면서
    수집은 예전 그대로 도는 실패가 정확히 여기서 갈린다.
    """

    def tearDown(self):
        restore_overrides()

    def test_ranking_stops_folding_a_pair_the_admin_split(self):
        import ranking  # noqa: PLC0415

        write_overrides([{
            "id": "s1", "kind": "story_split",
            "left_hash": "h-left", "right_hash": "h-right",
        }])
        items = [
            {"hash": "h-left", "title_kr": "한수원 체코 원전 계약 서명", "title": ""},
            {"hash": "h-right", "title_kr": "한수원 체코 원전 계약 서명", "title": ""},
        ]
        scores = {"h-left": 90.0, "h-right": 80.0}
        vetoes: list[dict] = []
        kept, dropped = ranking.cluster_duplicates(items, scores, vetoes=vetoes)
        self.assertEqual(len(kept), 2, "관리자가 갈라 둔 조합이 다시 접혔다")
        self.assertEqual(dropped, [])
        self.assertEqual(vetoes[0]["kind"], "admin_split")
        self.assertEqual(vetoes[0]["stage"], "local_title")

    def test_identical_titles_still_fold_without_an_override(self):
        """거부권이 없을 때의 동작은 그대로여야 한다 — 위 테스트의 대조군."""
        write_overrides([])
        items = [
            {"hash": "h-left", "title_kr": "한수원 체코 원전 계약 서명", "title": ""},
            {"hash": "h-right", "title_kr": "한수원 체코 원전 계약 서명", "title": ""},
        ]
        import ranking  # noqa: PLC0415

        kept, dropped = ranking.cluster_duplicates(items, {"h-left": 90.0, "h-right": 80.0})
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)

    def test_every_fold_point_consults_the_overlay(self):
        """접는 곳이 네 군데다. 하나라도 빠지면 그 경로로 그대로 접힌다."""
        for name, needle in (
            ("news_bot.py", "admin_overrides.merge_blocked"),
            ("ranking.py", "admin_overrides.merge_blocked"),
            ("dedup.py", "admin_overrides.merge_blocked"),
        ):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(needle, text, f"{name} 이 콘솔 판정을 보지 않는다")
        # news_bot 은 퍼지 제목과 임베딩 두 곳에서 접는다.
        news_bot_text = (ROOT / "news_bot.py").read_text(encoding="utf-8")
        self.assertEqual(news_bot_text.count("admin_overrides.merge_blocked"), 2,
                         "news_bot 의 두 dedup 경로 중 하나가 빠졌다")
        # 이슈 계층은 build_data 가 저장소 파일과 함께 읽는다.
        build_text = (ROOT / "web" / "build_data.py").read_text(encoding="utf-8")
        self.assertIn("issue_pair_overrides", build_text)

    def test_news_bot_reads_the_overlay_for_keywords_and_feeds(self):
        text = (ROOT / "news_bot.py").read_text(encoding="utf-8")
        for call in ("admin_overrides.keywords_config",
                     "admin_overrides.rss_sources",
                     "admin_overrides.official_sources",
                     "admin_overrides.anti_keywords"):
            self.assertIn(call, text, f"news_bot 이 {call} 을 안 쓴다")


if __name__ == "__main__":
    unittest.main()
