"""연속일 반복의 의미 대조 — dedup.cross_day_repeats 와 ranking 연결.

사용자가 지목한 사례(2026-09-25): 9/24 조선비즈 "이탈리아, 40년 만에 원전 부활법 통과"
다음 날 World Nuclear News "이탈리아 상원, 원자력 발전 복귀 법안 가결" 이 해외 1위로
또 나갔다. 어휘 게이트의 다섯 길(제목·지문·이름·근거·story_id)이 전부 못 넘었다.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import dedup
import issue_continuity
import ranking
from gemini_client import GeminiError

NOW = datetime(2026, 9, 24, 19, 5, tzinfo=timezone.utc)
TODAY = "2026-09-25"
CFG = ranking.load_config()

# delivery_log 실측 레코드에서 판정에 쓰는 칸만 옮겼다.
ITALY_SENT = {
    "date": "2026-09-24", "hash": "b0a45ddf95efb4e3",
    "title_kr": "이탈리아, 에너지 위기 대응 위해 40년 만에 원전 부활법 통과",
    "title": "에너지 대란에… 이탈리아, 40년 만에 ‘원전 부활법’ 통과",
    "summary": "이탈리아 상원이 원자력 발전 재개를 위한 규제 기반을 마련하는 법안을 통과시켰다.",
    "tags": ["#신규원전"], "region": "해외",
    "story_fingerprint": {"countries": ["Italy"], "actors": ["Italian Parliament"],
                          "event_family": "policy_decision",
                          "drivers": ["energy security"]},
}


def candidate(h, title, summary="", **extra):
    row = {"hash": h, "title_kr": title, "title": title, "summary": summary,
           "importance": "must_read", "section": "international",
           "domain": "example.org", "link": f"https://example.org/{h}",
           "queued_at": (NOW - timedelta(hours=3)).isoformat(),
           "features": {"event_type": "policy_decision", "korea_relevance": 1,
                        "market_materiality": 2, "policy_materiality": 3,
                        "evidence_strength": 2}}
    row.update(extra)
    return row


ITALY_CAND = candidate(
    "c2d8c771daf24eb2", "이탈리아 상원, 원자력 발전 복귀 법안 가결",
    "이탈리아 상원이 원자력 발전 복귀를 위한 정부 법안을 찬성 81표, 반대 51표로 통과시켰다.",
    story_fingerprint={"countries": ["Italy"], "actors": ["Italian Senate"],
                       "assets": ["Nuclear power plants"],
                       "event_family": "policy_decision",
                       "drivers": ["energy_security", "policy_shift"]})


ITALY_SENT_TITLE = ITALY_SENT["title_kr"]


def verdict(*, match="A", title=ITALY_SENT_TITLE, relation="same_detail", candidate=0,
            reason="같은 상원 표결의 세부 보도"):
    return {"candidate": candidate, "matched_sent_title": title, "reason": reason,
            "match": match, "relation": relation}


class FakeClient:
    """판정 호출과 확인 호출에 각각 다른 응답을 준다."""

    def __init__(self, response=None, confirm=None, error=None, confirm_error=None):
        self.response, self.confirm = response, confirm
        self.error, self.confirm_error = error, confirm_error
        self.calls = []

    def __call__(self, prompt, payload, **kw):
        is_confirm = prompt == dedup.CROSS_DAY_CONFIRM_PROMPT
        self.calls.append({"payload": payload, "confirm": is_confirm, **kw})
        error = self.confirm_error if is_confirm else self.error
        if error is not None:
            raise error
        return self.confirm if is_confirm else self.response


NO_NEW_ACTION = {"pairs": [{"pair": 0, "new_facts": ["찬성 81표"], "new_action": False}]}
NEW_ACTION = {"pairs": [{"pair": 0, "new_facts": ["하원 통과"], "new_action": True}]}


class CrossDayRepeatsTests(unittest.TestCase):
    def setUp(self):
        self._avail = dedup.is_available
        dedup.is_available = lambda: True
        dedup.reset_failures()

    def tearDown(self):
        dedup.is_available = self._avail
        dedup.reset_failures()

    def test_lexical_gate_misses_the_italy_repeat(self):
        """전제: 어휘 게이트가 실제로 못 잡는다. 이게 깨지면 이 모듈의 이유가 사라진다."""
        cfg = issue_continuity.resolve_config(None)
        self.assertIsNone(issue_continuity.same_issue(ITALY_CAND, ITALY_SENT, cfg))

    def test_italy_is_a_suspect_of_itself(self):
        """어휘 예선이 진짜 짝을 모델 앞까지는 데려간다."""
        generic = issue_continuity.generic_anchors([ITALY_CAND, ITALY_SENT])
        suspects = dedup._suspects(ITALY_CAND, [ITALY_SENT], generic)
        self.assertEqual([s["hash"] for s in suspects], ["b0a45ddf95efb4e3"])

    def test_same_event_confirmed_twice_is_marked_for_drop(self):
        cand = dict(ITALY_CAND)
        client = FakeClient({"verdicts": [verdict()]}, confirm=NO_NEW_ACTION)
        verdicts = dedup.cross_day_repeats([cand], [ITALY_SENT], client=client)
        self.assertEqual(len(verdicts), 1)
        self.assertTrue(verdicts[0]["drop"])
        self.assertEqual(verdicts[0]["confirm"], "no_new_action")
        self.assertEqual(cand["continuity"]["drop"], True)
        self.assertEqual(cand["continuity"]["identity_method"], "llm_cross_day")
        self.assertEqual(cand["continuity"]["prior_hash"], "b0a45ddf95efb4e3")
        # 판정 입력에 후보와 그 짝 후보(SENT A)가 함께 실렸다.
        self.assertIn("(SENT A)", client.calls[0]["payload"])
        self.assertIn("원전 부활법", client.calls[0]["payload"])
        self.assertEqual([c["confirm"] for c in client.calls], [False, True])

    def test_confirm_pass_can_veto_the_drop(self):
        """두 번째 열쇠 — '예정 → 실제 개최' 같은 모양을 좁은 질문이 잡는다."""
        cand = dict(ITALY_CAND)
        client = FakeClient({"verdicts": [verdict()]}, confirm=NEW_ACTION)
        verdicts = dedup.cross_day_repeats([cand], [ITALY_SENT], client=client)
        self.assertFalse(verdicts[0]["drop"])
        self.assertNotIn("continuity", cand)

    def test_confirm_failure_keeps_the_candidate(self):
        cand = dict(ITALY_CAND)
        client = FakeClient({"verdicts": [verdict()]},
                            confirm_error=GeminiError("HTTP 503"))
        verdicts = dedup.cross_day_repeats([cand], [ITALY_SENT], client=client)
        self.assertFalse(verdicts[0]["drop"])
        self.assertEqual(verdicts[0]["confirm"], "failed")
        self.assertNotIn("continuity", cand)
        self.assertEqual([f["stage"] for f in dedup.LLM_FAILURES], ["cross_day_confirm"])

    def test_missing_confirm_answer_keeps_the_candidate(self):
        cand = dict(ITALY_CAND)
        client = FakeClient({"verdicts": [verdict()]}, confirm={"pairs": []})
        self.assertFalse(dedup.cross_day_repeats([cand], [ITALY_SENT], client=client)[0]["drop"])

    def test_next_step_is_kept_without_asking_again(self):
        cand = dict(ITALY_CAND)
        client = FakeClient({"verdicts": [verdict(relation="next_step", reason="하원 통과")]})
        verdicts = dedup.cross_day_repeats([cand], [ITALY_SENT], client=client)
        self.assertFalse(verdicts[0]["drop"])
        self.assertNotIn("continuity", cand)
        self.assertEqual(len(client.calls), 1)

    def test_reaction_is_kept(self):
        """분석·해설은 지우지 않는다 — replay 에서 '같은 주제의 다른 뉴스'를 가장 많이 빨아들인 칸."""
        cand = dict(ITALY_CAND)
        client = FakeClient({"verdicts": [verdict(relation="same_reaction")]},
                            confirm=NO_NEW_ACTION)
        self.assertFalse(dedup.cross_day_repeats([cand], [ITALY_SENT], client=client)[0]["drop"])

    def test_lexical_material_progression_vetoes_the_model(self):
        """어휘 판정이 단계 전환을 보면 모델 말만으로 지우지 않는다."""
        prior = {"date": "2026-09-24", "hash": "p1",
                 "title_kr": "원안위, 새울 3호기 운영허가 심사 착수",
                 "summary": "원안위가 새울 3호기 운영허가 심사에 착수했다."}
        cand = candidate("c1", "원안위, 새울 3호기 운영허가 최종 승인",
                         "원안위가 새울 3호기 운영허가를 최종 승인했다.")
        client = FakeClient({"verdicts": [verdict(title=prior["title_kr"],
                                                  relation="same_restated")]},
                            confirm=NO_NEW_ACTION)
        verdicts = dedup.cross_day_repeats([cand], [prior], client=client)
        self.assertEqual(verdicts[0]["progression"], "material")
        self.assertFalse(verdicts[0]["drop"])
        self.assertNotIn("continuity", cand)

    def test_unrelated_and_malformed_verdicts_touch_nothing(self):
        cand = dict(ITALY_CAND)
        client = FakeClient({"verdicts": [
            verdict(match=None, title="", relation="different"),
            verdict(candidate=7),
            verdict(title="미국 텍사스주, 데이터센터 신규 허가 전면 중단"),
            verdict(relation="made_up"),
            "garbage"]}, confirm=NO_NEW_ACTION)
        self.assertEqual(dedup.cross_day_repeats([cand], [ITALY_SENT], client=client), [])
        self.assertNotIn("continuity", cand)

    def test_wrong_letter_is_corrected_by_the_echoed_title(self):
        """기호는 헛짚고 제목은 맞게 옮긴 경우 — 제목이 가리키는 쪽을 쓴다."""
        other = dict(ITALY_SENT, hash="other", title_kr="이탈리아 총리, 에너지 안보 연설",
                     title="이탈리아 총리, 에너지 안보 연설")
        cand = dict(ITALY_CAND)
        client = FakeClient({"verdicts": [verdict(match="B")]}, confirm=NO_NEW_ACTION)
        verdicts = dedup.cross_day_repeats([cand], [ITALY_SENT, other], client=client)
        self.assertEqual(verdicts[0]["prior_hash"], "b0a45ddf95efb4e3")

    def test_failure_keeps_everything_and_is_recorded(self):
        cand = dict(ITALY_CAND)
        client = FakeClient(error=GeminiError("HTTP 503: UNAVAILABLE"))
        self.assertEqual(dedup.cross_day_repeats([cand], [ITALY_SENT], client=client), [])
        self.assertNotIn("continuity", cand)
        self.assertEqual([f["stage"] for f in dedup.LLM_FAILURES], ["cross_day"])
        # 기본 모델과 반대 버킷 모델, 두 번 불렀다.
        self.assertEqual(len(client.calls), 2)
        self.assertNotEqual(client.calls[0]["model"], client.calls[1]["model"])

    def test_no_suspects_means_no_call(self):
        client = FakeClient({"verdicts": []})
        unrelated = candidate("u1", "캐나다 달링턴 SMR 착공", "OPG 가 착공했다.")
        self.assertEqual(dedup.cross_day_repeats([unrelated], [ITALY_SENT], client=client), [])
        self.assertEqual(dedup.cross_day_repeats([dict(ITALY_CAND)], [], client=client), [])
        self.assertEqual(client.calls, [])

    def test_recent_window_is_three_days_and_includes_today(self):
        rows = [{"date": d, "hash": d} for d in
                ("2026-09-20", "2026-09-21", "2026-09-22", "2026-09-24", TODAY)]
        got = [r["date"] for r in dedup.recent_for_cross_day(rows, TODAY)]
        self.assertEqual(got, [TODAY, "2026-09-24", "2026-09-22"])


class IdentityReviewFallbackTests(unittest.TestCase):
    def test_same_day_dedup_falls_back_then_records_failure(self):
        dedup.reset_failures()
        avail = dedup.is_available
        dedup.is_available = lambda: True
        try:
            client = FakeClient(error=GeminiError("HTTP 503"))
            a = candidate("a", "기사 A")
            b = candidate("b", "기사 B")
            kept, dropped = dedup.editorial_dedup_articles([a, b], {"a": 2, "b": 1},
                                                           client=client)
        finally:
            dedup.is_available = avail
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual([f["stage"] for f in dedup.LLM_FAILURES], ["editorial_final"])
        dedup.reset_failures()

    def test_fallback_success_is_not_a_failure(self):
        dedup.reset_failures()
        calls = []

        def flaky(prompt, payload, **kw):
            calls.append(kw["model"])
            if len(calls) == 1:
                raise GeminiError("HTTP 503")
            return {"groups": []}

        dedup._call_identity_review("p", "x", policy_name="dedup", label="dedup",
                                    client=flaky)
        self.assertEqual(len(calls), 2)
        self.assertEqual(dedup.LLM_FAILURES, [])


TITLES = [
    "체코 두코바니 원전 본계약 서명",
    "미국 NRC, 팰리세이즈 재가동 승인",
    "일본 가시와자키가리와 6호기 재가동",
    "캐나다 달링턴 SMR 착공",
]


class RankingHookTests(unittest.TestCase):
    """판정은 dedup 이, 제거와 빈자리 보충은 ranking 이 기존 연속일 경로로 한다."""

    def test_marked_repeat_is_removed_and_the_slot_backfilled(self):
        pool = [candidate(f"h{i}", t) for i, t in enumerate(TITLES[:4])]
        pool[0]["importance"] = "must_read"
        seen = {}

        def review(rows):
            seen["hashes"] = [r["hash"] for r in rows]
            rows[0]["continuity"] = {"drop": True, "progression": "none",
                                     "identity_method": "llm_cross_day",
                                     "prior_title": "어제 것", "prior_date": "2026-09-24"}
            return [{"hash": rows[0]["hash"], "drop": True}]

        selected, diag = ranking.rank_and_select(
            pool, 3, CFG, NOW, continuity_recheck=lambda rows: None,
            cross_day_review=review)
        dropped = diag["dropped_repeat"][0]["hash"]
        self.assertIn(dropped, seen["hashes"])
        self.assertNotIn(dropped, [a["hash"] for a in selected])
        self.assertEqual(len(selected), 3)       # 빈자리는 다음 후보가 채운다
        self.assertEqual(diag["cross_day"], [{"hash": dropped, "drop": True}])

    def test_hook_is_skipped_without_recheck_and_default_is_unchanged(self):
        pool = [candidate(f"h{i}", t) for i, t in enumerate(TITLES[:3])]
        selected, diag = ranking.rank_and_select(pool, 3, CFG, NOW)
        self.assertEqual(len(selected), 3)
        self.assertEqual(diag["cross_day"], [])


if __name__ == "__main__":
    unittest.main()
