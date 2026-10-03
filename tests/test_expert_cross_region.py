"""전문가 오디오 — 국내·해외에 같은 발표가 한 번씩 뽑힌 날.

중복 제거는 국내와 해외를 따로 돌린다(한 번에 돌리면 한 지역이 통째로 비는
사고). 그래서 2026-10-03 국내 1위 '한미 텍사스 가스발전소·원전 8기 합의' 와
해외 2위 '트럼프, 알래스카 LNG·원전 건설 일방 발표' 가 같은 발표인데 양쪽에
한 번씩 들어갔고, 전문가 대본은 둘을 처음부터 따로 설명했다.

기사를 빼지 않는다 — 대본 작성기가 해외 쪽을 '앞서 다룬 사건' 으로 짧게 잇는다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import expert_audio_brief as expert
import llm_policy
from gemini_client import GeminiError


def issue(issue_id: str, title: str, region: str, rank: int) -> dict:
    return {"issue_id": issue_id, "title": title, "summary": f"{title} 요약",
            "region": region, "brief_region": region, "brief_rank": rank,
            "selection_score": 10.0, "tags": [], "story_fingerprint": {},
            "entity_ids": [], "related_articles": [{"hash": issue_id, "title_kr": title}]}


ISSUES = [
    issue("d1", "한미, 223억 달러 규모 텍사스 가스복합발전소 및 대형 원전 8기 건설 합의", "국내", 1),
    issue("d2", "우진엔텍, 한수원 입찰참가 제한 처분에 소송 대응", "국내", 2),
    issue("o1", "G7, 트럼프 압박에 원유 1억 배럴 방출 발표", "해외", 1),
    issue("o2", "트럼프 대통령, 한국의 알래스카 LNG 투자 및 원전 건설 일방 발표", "해외", 2),
]


class CrossRegionJudgeTests(unittest.TestCase):
    def setUp(self):
        self._orig = expert._call_structured
        self.addCleanup(setattr, expert, "_call_structured", self._orig)
        self.calls = []

    def _install(self, response):
        def fake(system, message, **kw):
            self.calls.append((kw.get("label"), system, message))
            if isinstance(response, Exception):
                raise response
            return response
        expert._call_structured = fake

    def test_links_overseas_story_to_the_domestic_one(self):
        self._install({"links": [{"overseas": "o2", "domestic": "d1", "why": "같은 발표"}]})
        links = expert.cross_region_links(ISSUES)
        self.assertEqual({"o2"}, set(links))
        self.assertEqual("d1", links["o2"]["issue_id"])
        self.assertEqual(1, links["o2"]["brief_rank"])
        self.assertIn("텍사스", links["o2"]["title"])
        label, system, message = self.calls[0]
        self.assertEqual("expert_cross_region", label)
        self.assertIn("같은 발표", system)
        self.assertIn("[d1]", message)
        self.assertIn("[o2]", message)

    def test_judge_failure_means_no_hint_not_no_audio(self):
        self._install(GeminiError("HTTP 503"))
        self.assertEqual({}, expert.cross_region_links(ISSUES))

    def test_bogus_ids_and_wrong_direction_are_ignored(self):
        self._install({"links": [
            {"overseas": "d1", "domestic": "o2"},        # 방향이 거꾸로
            {"overseas": "o9", "domestic": "d1"},        # 없는 id
            {"overseas": "o2", "domestic": "o1"},        # 해외끼리
            {"overseas": "o2", "domestic": "d1"},
            {"overseas": "o2", "domestic": "d2"},        # 둘째 짝은 무시
        ]})
        links = expert.cross_region_links(ISSUES)
        self.assertEqual({"o2": "d1"}, {o: v["issue_id"] for o, v in links.items()})

    def test_one_region_only_days_do_not_call_the_judge(self):
        self._install({"links": []})
        self.assertEqual({}, expert.cross_region_links(ISSUES[:2]))
        self.assertEqual([], self.calls)

    def test_profile_is_registered_for_the_label(self):
        self.assertTrue(llm_policy.profile("expert_cross_region").model())


class SameEventRuleTests(unittest.TestCase):
    def test_overseas_prompt_tells_the_writer_to_bridge_not_repeat(self):
        dossiers = [{"issue_id": "o1", "title": "G7 원유 방출"},
                    {"issue_id": "o2", "title": "트럼프, 알래스카 LNG·원전 발표",
                     "same_event_as": {"issue_id": "d1", "title": "한미 텍사스 합의",
                                       "brief_rank": 1, "why": "같은 발표"}}]
        rule = expert._same_event_rule(dossiers)
        self.assertIn("국내 1번", rule)
        self.assertIn("한미 텍사스 합의", rule)
        self.assertIn("앞서 국내 소식에서", rule)
        self.assertIn("예외", rule)
        prompt = expert.script_prompt({"date": "2026-10-03"}, dossiers, {}, "해외", (1, 1),
                                      offset=0, block_total=2)
        self.assertIn("[국내에서 이미 다룬 사건", prompt)

    def test_no_links_means_no_rule(self):
        self.assertEqual("", expert._same_event_rule([{"issue_id": "o1", "title": "x"}]))


class EndToEndWiringTests(unittest.TestCase):
    def test_overseas_script_call_sees_the_domestic_title_only_for_the_linked_story(self):
        seen = []

        def fake(system, message, **kw):
            label = kw.get("label", "")
            seen.append((label, message))
            if label.startswith("expert_dossiers"):
                import re
                ids = re.findall(r'"issue_id": "([do]\d)"', message)
                return {"dossiers": [{"issue_id": i, "title": f"이슈 {i}", "body": "가" * 400}
                                     for i in dict.fromkeys(ids)]}
            if label == "expert_cross_region":
                return {"links": [{"overseas": "o2", "domestic": "d1", "why": "같은 발표"}]}
            if label == "expert_plan":
                return {"segments": []}
            if label.startswith("expert_script"):
                return {"script": "\n".join(f"HOST: {'가' * 150}" for _ in range(12))}
            if label.startswith("expert_verify"):
                return {"verdict": "PASS", "findings": [], "passed": True,
                        "coverage_score": 99, "factual_support_score": 99,
                        "stage_precision_score": 99, "expert_depth_score": 99,
                        "single_speaker_score": 100, "unsupported_critical_claims": []}
            return {}
        original = expert._call_structured
        expert._call_structured = fake
        try:
            briefing = {"date": "2026-10-03", "headline": "h", "highlight_issues": [],
                        "issues": [{"issue_id": i["issue_id"]} for i in ISSUES]}
            _script, _dossiers, _plan, report = expert.generate_expert_script(briefing, ISSUES)
        finally:
            expert._call_structured = original
        overseas = next(m for label, m in seen if label.startswith("expert_script_해외"))
        domestic = next(m for label, m in seen if label.startswith("expert_script_국내"))
        self.assertIn("[국내에서 이미 다룬 사건", overseas)
        self.assertIn("텍사스", overseas)                 # 짝이 된 국내 story 제목만
        self.assertNotIn("우진엔텍", overseas)            # 다른 국내 story 는 여전히 안 보인다
        self.assertNotIn("[국내에서 이미 다룬 사건", domestic)
        self.assertEqual([{"overseas": "o2", "issue_id": "d1", "brief_rank": 1,
                           "why": "같은 발표"}],
                         [{k: r[k] for k in ("overseas", "issue_id", "brief_rank", "why")}
                          for r in report["cross_region_links"]])
        self.assertEqual(1, sum(1 for label, _ in seen if label == "expert_cross_region"))


if __name__ == "__main__":
    unittest.main()
