"""story_echo — 메이저가 먼저 쓴 사실을 며칠 뒤 다른 매체가 다시 쓴 기사.

2026-10-03 국내 1위(글로벌E)가 10/1 서울신문의 되풀이였고, 소급하면 발송 254건 중
약 20건이 같은 꼴이었다. 여기서 (1) 그 꼴이 걸리고, (2) 다른 사건·동시 보도·진전은
안 걸리며, (3) 처분이 '이미 브리핑됨 → 제외 / 아직 → 등급 내리고 날짜' 로 갈리는지
잠근다.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import story_echo  # noqa: E402

KST = timezone(timedelta(hours=9))
CUTOFF = datetime(2026, 10, 2, 7, 5, tzinfo=KST)   # 10/2 아침 브리핑
GRACE = 1.0


def prior(h, title, summary, published, **kw):
    return {"hash": h, "title_kr": title, "title": "", "summary": summary, "detail": "",
            "published_at": published, "domain": kw.pop("domain", "seoul.co.kr"),
            "importance": kw.pop("importance", "must_read"), **kw}


def cand(h, title, summary, members, **kw):
    return {"hash": h, "title_kr": title, "title": "", "summary": summary, "detail": "",
            "importance": kw.pop("importance", "must_read"), "domain": kw.pop("domain", "globale.co.kr"),
            "published_at": "2026-10-03T03:04:00+09:00", "story_id": kw.pop("story_id", "story-x"),
            "story_members": [{"hash": m} for m in members], **kw}


SEOUL = prior("p-seoul", "한미, 223억 달러 텍사스 가스발전 1호 사업 확정 및 원전 8기 건설 합의",
              "한미 양국이 223억 달러 규모의 텍사스 가스복합화력발전소 건설을 대미 투자 1호로 확정하고, 미국 내 대형 원전 8기 건설에 합의했다.",
              "2026-10-01T10:39:00+09:00")
GLOBALE = cand("c-globale", "한미, 223억 달러 규모 텍사스 가스복합발전소 및 대형 원전 8기 건설 합의",
               "한미가 223억 달러 규모의 텍사스 가스복합화력발전소 건설을 확정하고, 미국 내 대형 원전 8기 건설에도 큰 틀에서 합의했다.",
               ["p-seoul"])


class FindEchoTests(unittest.TestCase):
    def test_same_fact_a_day_later_is_an_echo(self):
        echo = story_echo.find_echo(GLOBALE, {"p-seoul": SEOUL}, CUTOFF, GRACE)
        self.assertIsNotNone(echo)
        self.assertEqual(echo["prior_hash"], "p-seoul")
        self.assertEqual(echo["prior_date"], "2026-10-01")
        self.assertGreaterEqual(echo["similarity"], story_echo.ECHO_QUANTITY_SIMILARITY)
        self.assertTrue(echo["quantities_subset"])

    def test_same_cycle_sibling_is_not_an_echo(self):
        """직전 브리핑 **이후**에 나온 선행 기사는 동시 보도다 — dedup 의 몫이지 되풀이가 아니다."""
        sibling = dict(SEOUL, published_at="2026-10-02T20:00:00+09:00")
        self.assertIsNone(story_echo.find_echo(GLOBALE, {"p-seoul": sibling}, CUTOFF, GRACE))

    def test_progress_is_not_an_echo(self):
        """수치가 새로 붙거나 척도가 올라가면(#220 progression) 되풀이가 아니다."""
        before = prior("p1", "한수원, 체코 두코바니 원전 협상 진행", "한수원이 체코와 두코바니 신규 원전 계약 조건을 협의하고 있다.",
                       "2026-09-30T09:00:00+09:00")
        after = cand("c1", "한수원, 체코 두코바니 원전 본계약 체결", "한수원이 체코전력공사와 두코바니 신규 원전 2기 본계약을 체결했다.", ["p1"])
        self.assertIsNone(story_echo.find_echo(after, {"p1": before}, CUTOFF, GRACE))

    def test_different_event_in_the_same_broad_story_is_not_an_echo(self):
        """소급 실측: '외교장관 회담 예정'(9/17) → '회담 계기 협정 진전 기대'(9/19) 는 0.62 에 수치가
        없어 걸리지 않는다. NERC 규제 ↔ 하원 법안(0.36)도 같다."""
        before = prior("p1", "대미 투자 프로젝트 관련 한미 외교장관 회담 예정", "한미 외교장관이 다음 주 회담을 갖고 대미 투자 프로젝트를 논의할 예정이다.",
                       "2026-09-17T09:00:00+09:00")
        after = cand("c1", "한미 외교장관 회담 계기 대미 투자 및 원자력 협정 진전 기대",
                     "한미 외교장관 회담을 계기로 대미 투자와 원자력 협정 논의가 진전될 것으로 기대된다.", ["p1"], importance="nice_to_know")
        self.assertIsNone(story_echo.find_echo(after, {"p1": before}, CUTOFF, GRACE))
        nerc = cand("c2", "NERC, 데이터센터 전력망 연결 관련 신규 규제 도입", "NERC 가 데이터센터의 전력망 연결에 관한 새 규제를 도입했다.", ["p2"],
                    importance="nice_to_know")
        house = prior("p2", "미국 하원, 데이터센터 전력망 건설비용 부담 법안 통과", "미 하원이 데이터센터가 전력망 건설비용을 부담하게 하는 법안을 통과시켰다.",
                      "2026-09-17T09:00:00+09:00")
        self.assertIsNone(story_echo.find_echo(nerc, {"p2": house}, CUTOFF, GRACE))

    def test_official_document_is_never_an_echo(self):
        """보도자료가 기사보다 늦게 올라오는 것은 되풀이가 아니라 원문이다."""
        official = dict(GLOBALE, evidence_role="primary", domain="motir.go.kr")
        self.assertIsNone(story_echo.find_echo(official, {"p-seoul": SEOUL}, CUTOFF, GRACE))

    def test_prior_without_curation_is_ignored(self):
        """요약이 없는 멤버(수집 단계에서만 접힌 raw source)로는 대조하지 않는다."""
        raw = dict(SEOUL, summary="")
        self.assertIsNone(story_echo.find_echo(GLOBALE, {"p-seoul": raw}, CUTOFF, GRACE))


class ApplyTests(unittest.TestCase):
    def test_echo_of_a_briefed_story_is_dropped_as_a_repeat(self):
        item = dict(GLOBALE)
        kept, dropped = story_echo.apply([item], {"p-seoul": SEOUL}, CUTOFF, GRACE,
                                         {"story_ids": {"story-x"}, "hashes": set()})
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0]["reason"], "story_echo_repeat")
        self.assertEqual(dropped[0]["prior_hash"], "p-seoul")
        self.assertTrue(item["continuity"]["drop"])
        self.assertEqual(item["continuity"]["identity_method"], "story_echo")

    def test_echo_whose_prior_was_a_member_of_a_sent_card_is_also_dropped(self):
        item = dict(GLOBALE, story_id="story-other")
        kept, dropped = story_echo.apply([item], {"p-seoul": SEOUL}, CUTOFF, GRACE,
                                         {"story_ids": set(), "hashes": {"p-seoul"}})
        self.assertEqual(len(dropped), 1)

    def test_echo_of_an_unbriefed_story_is_demoted_and_dated(self):
        item = dict(GLOBALE)
        kept, dropped = story_echo.apply([item], {"p-seoul": SEOUL}, CUTOFF, GRACE,
                                         {"story_ids": set(), "hashes": set()})
        self.assertEqual(dropped, [])
        self.assertIs(kept[0], item)
        self.assertEqual(item["importance"], "nice_to_know")
        self.assertEqual(item["importance_llm"], "must_read")
        self.assertEqual(item["stale_since"], "2026-10-01")
        self.assertEqual(item["story_echo"]["prior_domain"], "seoul.co.kr")

    def test_non_echo_passes_untouched(self):
        item = cand("c9", "원안위, 신한울 3호기 운영허가 심사 착수", "원안위가 신한울 3호기 운영허가 심사에 착수했다.", [])
        kept, dropped = story_echo.apply([item], {}, CUTOFF, GRACE, None)
        self.assertEqual((len(kept), dropped), (1, []))
        self.assertNotIn("story_echo", item)
        self.assertEqual(item["importance"], "must_read")


class BriefedStoriesTests(unittest.TestCase):
    def test_reads_story_ids_and_member_hashes_from_sent_rows(self):
        import json
        import tempfile
        rows = [
            {"hash": "s1", "date": "2026-10-02", "story_id": "story-x",
             "story_members": [{"hash": "m1"}, {"hash": "m2"}]},
            {"record_type": "quality_event", "hash": "ignored", "date": "2026-10-02"},
            {"hash": "old", "date": "2026-09-01", "story_id": "story-old"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery_log.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            seen = story_echo.briefed_stories(path, today="2026-10-03")
        self.assertEqual(seen["story_ids"], {"story-x"})
        self.assertEqual(seen["hashes"], {"s1", "m1", "m2"})


if __name__ == "__main__":
    unittest.main()
