"""매체 등급표 재점검(2026-10-03) — sources.json 하나가 점수·대표 순위의 단일 출처다.

무엇이 문제였나
  · 등급표(sources.json)와 점수표(news_bot.DOMAIN_SCORE)가 따로 있어 조선·중앙·동아가
    점수표에선 9점, 등급표엔 없어 랭킹에선 '모르는 매체'였다.
  · 웹 대표 선정 키가 매체 급을 tier1 하나로만 봐서 라이브 이슈 709건 중 260건이
    더 높은 급 매체를 두고 낮은 매체를 대표로 세웠다(글로벌E > 전기신문).
  · 업계 단체(NEI·WNA·KAIF)가 규제기관과 같은 official/primary 라 '공식 원문' 배지와
    must_read 격상을 받았다.
  · 토큰포스트(암호화폐 매체)가 90일 321건을 들여보내고 발송 29건을 차지했다.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import data_quality  # noqa: E402
import news_bot as nb  # noqa: E402
import story_cluster  # noqa: E402


def art(domain, **kw):
    base = {"domain": domain, "summary": "요약 한 줄", "importance": "nice_to_know",
            "article_date": "2026-10-02"}
    base.update(kw)
    profile = data_quality.source_profile(domain)
    base.setdefault("source_tier", profile["source_tier"])
    base.setdefault("evidence_role", profile["evidence_role"])
    return base


class TierTableTests(unittest.TestCase):
    def test_national_dailies_broadcasters_and_economic_papers_are_tier2(self):
        for domain in ("chosun.com", "joongang.co.kr", "donga.com", "hani.co.kr", "khan.co.kr",
                       "hankookilbo.com", "kmib.co.kr", "munhwa.com", "seoul.co.kr", "segye.com",
                       "kbs.co.kr", "imbc.com", "sbs.co.kr", "ytn.co.kr", "jtbc.co.kr",
                       "mk.co.kr", "hankyung.com", "fnnews.com", "edaily.co.kr", "asiae.co.kr",
                       "heraldcorp.com", "sedaily.com", "mt.co.kr", "etnews.com", "yna.co.kr"):
            with self.subTest(domain=domain):
                profile = data_quality.source_profile(domain)
                self.assertEqual(profile["source_tier"], 2)
                self.assertEqual(profile["evidence_role"], "independent")
                self.assertEqual(nb.source_score(domain), 8)

    def test_energy_and_nuclear_trade_press_rank_with_the_dailies(self):
        """이 서비스에서는 원자력 전문지가 종합지보다 중요할 수 있다 — 한국원자력신문이
        기본값 4점이던 것이 재점검의 출발점이다."""
        for domain in ("electimes.com", "ekn.kr", "energy-news.co.kr", "energydaily.co.kr",
                       "epj.co.kr", "energytimes.kr", "todayenergy.kr", "knpnews.com",
                       "e-platform.net"):
            with self.subTest(domain=domain):
                profile = data_quality.source_profile(domain)
                self.assertEqual(profile["source_tier"], 2)
                self.assertEqual(profile["source_type"], "specialist_media")
                self.assertEqual(nb.source_score(domain), 8)

    def test_regulators_and_nuclear_news_wires_stay_tier1_primary_or_independent(self):
        self.assertEqual(data_quality.source_profile("nssc.go.kr")["evidence_role"], "primary")
        self.assertEqual(data_quality.source_profile("nrc.gov")["evidence_role"], "primary")
        # 원자력 전문지는 tier1 묶음에 있지만 rank_tier 2 다 — 신뢰도는 높되 '공식'은 아니다.
        wnn = data_quality.source_profile("world-nuclear-news.org")
        self.assertEqual((wnn["source_tier"], wnn["evidence_role"]), (2, "independent"))
        self.assertEqual(nb.source_score("world-nuclear-news.org"), 8)
        self.assertEqual(nb.source_score("nssc.go.kr"), 10)

    def test_industry_bodies_keep_tier1_priority_but_lose_the_official_badge(self):
        """업계 입장문이 규제 의결과 같은 무게로 오르지 않게 — 수집 우선순위는 그대로."""
        for domain, kind in (("nei.org", "industry_body"), ("world-nuclear.org", "industry_body"),
                             ("sfen.org", "industry_body"), ("kaif.or.kr", "industry_body"),
                             ("ismr.or.kr", "industry_body"), ("kns.org", "academic"),
                             ("niftep.snu.ac.kr", "academic")):
            with self.subTest(domain=domain):
                profile = data_quality.source_profile(domain)
                self.assertEqual(profile["source_type"], kind)
                self.assertEqual(profile["evidence_role"], "stakeholder")
                self.assertIn(profile["source_tier"], (1, 2))
                self.assertGreaterEqual(nb.source_score(domain), 8)
                self.assertFalse(nb.is_tier1_source({"domain": domain, "link": f"https://{domain}/x"}))

    def test_peripheral_media_is_collected_but_never_independent_or_representative(self):
        for domain in ("tokenpost.kr", "job-post.co.kr", "theguru.co.kr"):
            with self.subTest(domain=domain):
                profile = data_quality.source_profile(domain)
                self.assertEqual(profile["evidence_role"], "peripheral")
                self.assertEqual(nb.source_score(domain), nb.DEFAULT_SCORE)
                self.assertGreaterEqual(nb.source_score(domain), nb.MIN_SCORE)  # 수집은 된다
                self.assertEqual(story_cluster.outlet_rank({"domain": domain}), 0)

    def test_unregistered_domain_sits_below_every_registered_tier(self):
        profile = data_quality.source_profile("regional-news.example", "지역매체")
        self.assertFalse(profile["registered"])
        self.assertEqual(nb.source_score("regional-news.example"), nb.DEFAULT_SCORE)
        self.assertLess(story_cluster.outlet_rank({"domain": "regional-news.example"}),
                        story_cluster.outlet_rank({"domain": "g-enews.com"}))
        self.assertTrue(data_quality.source_profile("chosun.com")["registered"])
        # 정부 도메인은 등록 없이도 공식 1차 자료다.
        self.assertTrue(data_quality.source_profile("mofa.go.kr")["registered"])

    def test_scores_derive_from_tiers_only(self):
        self.assertFalse(hasattr(nb, "DOMAIN_SCORE"), "점수표가 둘로 다시 갈라지면 안 된다")
        self.assertEqual(nb.TIER_SCORE, {1: 10, 2: 8, 3: 6})
        self.assertEqual(nb.source_score("g-enews.com"), 6)      # 등록 tier3
        self.assertEqual(nb.source_score("koreaherald.com"), 6)  # 영자지 — tier3 로 내림

    def test_every_registered_entry_uses_known_vocabulary(self):
        import json
        raw = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
        seen = set()
        for group in ("tier1", "tier2", "tier3"):
            for entry in raw[group]:
                with self.subTest(domain=entry["domain"]):
                    self.assertIn(entry["source_type"], data_quality.VALID_SOURCE_TYPES)
                    self.assertIn(entry["evidence_role"], data_quality.VALID_EVIDENCE_ROLES)
                    self.assertNotIn(entry["domain"], seen, "같은 도메인이 두 등급에 있다")
                    seen.add(entry["domain"])


class RepresentativeKeyTests(unittest.TestCase):
    def test_major_outlet_beats_unregistered_must_read(self):
        """2026-10-03 1위 이슈: 글로벌E(미등록, must_read) 가 전기신문을 제치고 대표였다."""
        minor = art("globale.co.kr", importance="must_read", selection_score=9.0)
        major = art("electimes.com", importance="nice_to_know", selection_score=5.0)
        self.assertIs(max([minor, major], key=story_cluster.representative_key), major)

    def test_official_document_beats_newspaper(self):
        paper = art("chosun.com", importance="must_read")
        official = art("motir.go.kr", importance="nice_to_know")
        self.assertIs(max([paper, official], key=story_cluster.representative_key), official)

    def test_article_without_any_content_never_represents(self):
        empty = art("chosun.com", summary="", detail="")
        thin = art("regional-news.example", summary="한 줄은 있다")
        self.assertIs(max([empty, thin], key=story_cluster.representative_key), thin)

    def test_within_the_same_tier_body_then_must_read_then_score_decide(self):
        a = art("chosun.com", detail="본문 요지가 충분히 길게 들어 있는 기사 " * 3)
        b = art("donga.com", importance="must_read", selection_score=20.0)
        c = art("hani.co.kr", selection_score=20.0)
        d = art("khan.co.kr", selection_score=3.0)
        self.assertEqual([x["domain"] for x in sorted([d, c, b, a], key=story_cluster.representative_key, reverse=True)],
                         ["chosun.com", "donga.com", "hani.co.kr", "khan.co.kr"])

    def test_peripheral_and_portal_copies_rank_last(self):
        crypto = art("tokenpost.kr", importance="must_read", detail="본문 " * 40)
        portal = art("nate.com", importance="must_read", detail="본문 " * 40)
        unregistered = art("regional-news.example")
        ranked = sorted([crypto, portal, unregistered], key=story_cluster.representative_key, reverse=True)
        self.assertEqual(ranked[0]["domain"], "regional-news.example")

    def test_web_build_uses_the_same_key(self):
        import build_data
        rows = [art("globale.co.kr", importance="must_read"), art("electimes.com")]
        self.assertEqual(build_data._representative_key(rows[0]), story_cluster.representative_key(rows[0]))
        self.assertIs(max(rows, key=build_data._representative_key), rows[1])

    def test_display_swap_reason_names_the_outlet_grade(self):
        minor = art("regional-news.example", summary="요약")
        major = art("chosun.com", summary="요약")
        winner, reason = story_cluster.choose_display_representative(
            [minor, major], {"a": 5.0, "b": 5.0}, current=minor)
        self.assertIs(winner, major)
        self.assertEqual(reason, "출처 등급이 더 높은 기사로 교체")


if __name__ == "__main__":
    unittest.main()
