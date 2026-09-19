"""명시적 `different_thread` 를 거부권으로 쓴다 — 브리지 한 개가 그것을 우회하지 못한다.

왜 이 파일이 따로 있나
----------------------
`test_thread_identity.py` 는 묶음 가드(호기 모순·약한 고리·성장 제동)를 지킨다.
여기서 지키는 것은 그 앞 단계다 — **판정이 이미 "다르다"고 말한 증거가 묶음까지
전달되는가.** 2026-09-19 이전에는 전달되지 않았다. `tools/build_threads` 가
`same_thread` 만 골라 `cluster()` 에 넘기고 2,368건의 `different_thread` 를
그 자리에서 버렸다.

실패 사례 둘은 **라이브 원장과 판정 캐시에서 그대로 떠서** 픽스처로 박았다
(`fixtures/thread_misjoin_2026-09-19.json`). 제목도 판정 문구도 손대지 않았다 —
손대면 재현이 아니라 창작이다.
"""

import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import event_retrieval
import thread_evidence
import thread_identity
from event_retrieval import Event
from tools.build_threads import build_edges, expand_folds

FIXTURE = Path(__file__).parent / "fixtures" / "thread_misjoin_2026-09-19.json"


def _event(issue_id, title, first, last=None, *, units=(), entities=(), summary=""):
    return Event(
        issue_id=issue_id, title=title, summary=summary,
        first_seen=date.fromisoformat(first),
        last_seen=date.fromisoformat(last or first),
        units=frozenset(units), plants=frozenset(), entities=frozenset(entities),
        assets=frozenset(), actors=frozenset(), action="", tokens=frozenset(),
        briefing_count=1, raw={},
    )


def _negatives(*pairs):
    out: dict[str, set] = {}
    for left, right in pairs:
        out.setdefault(left, set()).add(right)
        out.setdefault(right, set()).add(left)
    return out


class NegativeEdgeVetoTests(unittest.TestCase):
    """PART H #1·#2 — 거부권이 전이 병합을 끊는다."""

    def _events(self, rows):
        return {event.issue_id: event for event in rows}

    def test_bridge_cannot_carry_a_cluster_past_explicit_negative(self):
        """Left={A,B} 와 Right={C} 사이에 A↔C 거부가 있으면 B↔C 고리로도 못 합친다."""
        events = self._events([
            _event("a", "고리 2호기 계속운전 신청", "2026-03-01", units={"kori-2"}),
            _event("b", "고리 2호기 계속운전 심사", "2026-04-01", units={"kori-2"}),
            _event("c", "고리 2호기 계획예방정비 착수", "2026-05-01", units={"kori-2"}),
        ])
        accepted = [("a", "b"), ("b", "c")]
        groups, stats = thread_identity.cluster(
            events, accepted, _negatives(("a", "c")))
        self.assertEqual(sorted(sorted(group) for group in groups), [["a", "b"]])
        self.assertEqual(stats["blocked_negative_edge"], 1)

    def test_same_link_set_merges_when_the_negative_is_absent(self):
        """거부권이 없으면 **종전과 똑같이** 합쳐진다 — 이 테스트가 대조군이다."""
        events = self._events([
            _event("a", "고리 2호기 계속운전 신청", "2026-03-01", units={"kori-2"}),
            _event("b", "고리 2호기 계속운전 심사", "2026-04-01", units={"kori-2"}),
            _event("c", "고리 2호기 계획예방정비 착수", "2026-05-01", units={"kori-2"}),
        ])
        groups, stats = thread_identity.cluster(events, [("a", "b"), ("b", "c")])
        self.assertEqual(sorted(sorted(group) for group in groups), [["a", "b", "c"]])
        self.assertEqual(stats["blocked_negative_edge"], 0)

    def test_transitive_closure_stops_at_the_negative(self):
        """A≠C 인데 A=B·B=C 가 있다. A-B-C 가 한 스토리가 되어서는 안 된다."""
        events = self._events([
            _event("a", "가", "2026-03-01", entities={"khnp"}),
            _event("b", "나", "2026-04-01", entities={"khnp"}),
            _event("c", "다", "2026-05-01", entities={"khnp"}),
        ])
        groups, _stats = thread_identity.cluster(
            events, [("a", "b"), ("b", "c")], _negatives(("a", "c")))
        for group in groups:
            self.assertFalse({"a", "c"} <= group,
                             "A 와 C 가 같은 스토리에 있다 — 거부권이 새어 나갔다")

    def test_negative_between_two_multi_member_clusters(self):
        """어느 자리의 모순이든 잡는다. 대표 두 건만 보면 놓친다."""
        events = self._events([
            _event("a1", "가1", "2026-03-01"), _event("a2", "가2", "2026-03-02"),
            _event("b1", "나1", "2026-04-01"), _event("b2", "나2", "2026-04-02"),
        ])
        accepted = [("a1", "a2"), ("b1", "b2"), ("a1", "b1"), ("a2", "b2")]
        groups, stats = thread_identity.cluster(
            events, accepted, _negatives(("a2", "b1")))
        self.assertEqual(sorted(sorted(group) for group in groups),
                         [["a1", "a2"], ["b1", "b2"]])
        self.assertEqual(stats["blocked_negative_edge"], 1)

    def test_blocked_pair_is_named_in_diagnostics(self):
        """왜 막혔는지 사람이 되물을 수 있어야 한다."""
        events = self._events([
            _event("a", "가", "2026-03-01"), _event("b", "나", "2026-04-01"),
            _event("c", "다", "2026-05-01"),
        ])
        _groups, stats = thread_identity.cluster(
            events, [("a", "b"), ("b", "c")], _negatives(("a", "c")))
        samples = stats["negative_block_samples"]
        self.assertEqual(len(samples), 1)
        self.assertIn(("a", "c"), samples[0]["pairs"])


class PreservedThreadTests(unittest.TestCase):
    """PART D — 오병합을 막는다고 정상 스토리를 쪼개지 않는다."""

    def _events(self, rows):
        return {event.issue_id: event for event in rows}

    def test_stage_progression_stays_one_thread(self):
        """같은 호기의 신청 → 심사 → 의견수렴 → 승인은 한 이야기다."""
        events = self._events([
            _event("s1", "고리 2호기 계속운전 신청", "2026-03-01", units={"kori-2"}),
            _event("s2", "고리 2호기 계속운전 심사 착수", "2026-04-01", units={"kori-2"}),
            _event("s3", "고리 2호기 계속운전 주민 의견수렴", "2026-05-01", units={"kori-2"}),
            _event("s4", "고리 2호기 계속운전 승인", "2026-06-01", units={"kori-2"}),
        ])
        accepted = [("s1", "s2"), ("s2", "s3"), ("s3", "s4")]
        groups, stats = thread_identity.cluster(events, accepted, negative={})
        self.assertEqual(sorted(sorted(group) for group in groups),
                         [["s1", "s2", "s3", "s4"]])
        self.assertEqual(stats["blocked_negative_edge"], 0)

    def test_other_units_stay_separate(self):
        """다른 호기는 종전 가드가 계속 가른다 — 거부권을 더해도 그대로다."""
        events = self._events([
            _event("k1", "고리 2호기 계속운전 신청", "2026-03-01", units={"kori-2"}),
            _event("k2", "고리 2호기 계속운전 심사", "2026-04-01", units={"kori-2"}),
            _event("w1", "월성 1호기 해체 승인", "2026-05-01", units={"wolsong-1"}),
            _event("w2", "월성 1호기 해체 착수", "2026-06-01", units={"wolsong-1"}),
        ])
        groups, _stats = thread_identity.cluster(
            events, [("k1", "k2"), ("w1", "w2"), ("k2", "w1")], negative={})
        self.assertEqual(sorted(sorted(group) for group in groups),
                         [["k1", "k2"], ["w1", "w2"]])


class DuplicateFoldTests(unittest.TestCase):
    """제목이 같은 사본이 서로 반대 판정을 남기는 것을 무력화한다."""

    def test_identical_titles_in_one_window_fold(self):
        events = [
            _event("e1", "원안위, 2027년 예산 3,030억 편성", "2026-09-02", "2026-09-03"),
            _event("e2", "원안위, 2027년 예산 3,030억 편성", "2026-09-03"),
        ]
        roots = thread_identity.fold_duplicates(events)
        self.assertEqual(roots["e2"], "e1")

    def test_the_same_headline_months_later_is_a_different_event(self):
        events = [
            _event("e1", "원안위, 정기검사 결과 보고", "2026-03-01"),
            _event("e2", "원안위, 정기검사 결과 보고", "2026-09-01"),
        ]
        roots = thread_identity.fold_duplicates(events)
        self.assertEqual(roots["e2"], "e2")

    def test_folding_never_touches_different_titles(self):
        events = [
            _event("e1", "원안위, 오르비텍 핵연료물질 사용 허가", "2026-09-15"),
            _event("e2", "원안위, 2027년 예산 3,030억 편성", "2026-09-15"),
        ]
        roots = thread_identity.fold_duplicates(events)
        self.assertEqual({roots["e1"], roots["e2"]}, {"e1", "e2"})


class GenericEntityTests(unittest.TestCase):
    """PART H #3 — 범용 기관 하나를 공유한다는 이유로 잇지 않는다."""

    def test_regulator_alone_is_not_evidence(self):
        left = _event("x", "원안위, 2027년 예산 3,030억 편성", "2026-09-03",
                      entities={"nssc"})
        right = _event("y", "원안위, 오르비텍 핵연료물질 사용 허가 의결", "2026-09-15",
                       entities={"nssc"})
        ok, reason = thread_evidence.gate(
            {"verdict": "same_thread", "relationship": "same_matter"},
            left, right, {"lexical": 2.75})
        self.assertFalse(ok)
        self.assertEqual(reason, thread_evidence.REJECT_GENERIC_SCOPE)

    def test_a_shared_unit_is_evidence(self):
        left = _event("x", "고리 2호기 계속운전 신청", "2026-03-01", units={"kori-2"})
        right = _event("y", "고리 2호기 계속운전 심사", "2026-04-01", units={"kori-2"})
        ok, _reason = thread_evidence.gate(
            {"verdict": "same_thread", "relationship": "stage_progress"},
            left, right, {"lexical": 0.4})
        self.assertTrue(ok, "호기를 공유하는데 막혔다")

    def test_cause_effect_needs_an_object_not_a_shared_company(self):
        """공통 기업 하나로 인과를 주장하는 것이 실측의 오병합 1번이었다."""
        left = _event("x", "미국의 러시아산 우라늄 수입 금지법과 예외 조항의 실태",
                      "2026-08-19", entities={"westinghouse"})
        right = _event("y", "웨스팅하우스 지분 확보 시나리오와 국내 원전 수출 영향 분석",
                       "2026-08-28", entities={"westinghouse"})
        ok, reason = thread_evidence.gate(
            {"verdict": "same_thread", "relationship": "cause_effect"},
            left, right, {"lexical": 2.036})
        self.assertFalse(ok)
        self.assertEqual(reason, thread_evidence.REJECT_CAUSE_WITHOUT_OBJECT)

    def test_a_rejected_link_is_not_a_negative_edge(self):
        """게이트 거부는 `uncertain` 이다. 거부권 재료로 쓰면 그것도 오판이다."""
        left = _event("x", "가", "2026-01-01", entities={"nssc"})
        right = _event("y", "나", "2026-02-01", entities={"nssc"})
        pairs = [{"key": "x--y", "left": left, "right": right,
                  "signals": {"lexical": 0.0}}]
        verdicts = {"x--y": {"verdict": "same_thread", "relationship": "same_matter",
                             "method": "cache"}}
        accepted, negative, _stats = build_edges(
            pairs, verdicts, {"x": "x", "y": "y"})
        self.assertEqual(accepted, [])
        self.assertEqual(negative, {})


class RuleRejectTests(unittest.TestCase):
    """규칙 거부는 거부권이 아니다 — '안 봤다'와 '다르다'는 다른 말이다."""

    def _pair(self, verdict):
        left = _event("x", "가", "2026-01-01")
        right = _event("y", "나", "2026-02-01")
        pairs = [{"key": "x--y", "left": left, "right": right, "signals": {}}]
        return build_edges(pairs, {"x--y": verdict}, {"x": "x", "y": "y"})

    def test_rule_rejects_never_become_negative_edges(self):
        _accepted, negative, _stats = self._pair(
            {"verdict": "different_thread", "reason": "no_shared_identity",
             "method": "rule"})
        self.assertEqual(negative, {}, "값싼 규칙 거부가 거부권이 됐다")

    def test_model_rejects_do_become_negative_edges(self):
        _accepted, negative, _stats = self._pair(
            {"verdict": "different_thread", "reason": "다른 사안이다", "method": "cache"})
        self.assertEqual(negative, {"x": {"y"}, "y": {"x"}})

    def test_uncertain_is_not_a_negative_edge(self):
        accepted, negative, _stats = self._pair(
            {"verdict": "uncertain", "method": "cache"})
        self.assertEqual((accepted, negative), ([], {}))


class LiveMisjoinReplayTests(unittest.TestCase):
    """PART H #4·#5 — 라이브에서 실제로 일어난 오병합 두 건을 그대로 돌린다."""

    @classmethod
    def setUpClass(cls):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        store = {"issues": payload["issues"]}
        with mock.patch.object(event_retrieval.issue_ledger, "load_store",
                               return_value=store):
            events = event_retrieval.load_events()
        cls.index = event_retrieval.Index(events)
        cls.judgments = payload["judgments"]

    def _run(self, *, gate: bool = True):
        """픽스처를 실제 파이프라인에 그대로 태운다.

        Args:
            gate: False 면 증거 게이트를 통과시킨 채 돌린다. **거부권만으로도
                막히는가**를 따로 묻기 위한 것이다 — 두 방어선이 서로를 가리면
                어느 쪽이 일하는지 영영 알 수 없다.
        """
        pairs, verdicts = [], {}
        for key, row in self.judgments.items():
            left_id, right_id = key.split("--")
            pairs.append({"key": key,
                          "left": self.index.by_id[left_id],
                          "right": self.index.by_id[right_id],
                          # 실제 888건 색인에서 뜬 값이다. 픽스처 12건으로 다시
                          # 계산하면 IDF 척도가 달라진다(파일 _comment 참조).
                          "signals": row["signals"]})
            verdicts[key] = {"verdict": row["verdict"],
                             "relationship": row["relationship"],
                             "method": "cache"}
        roots = thread_identity.fold_duplicates(self.index.events)
        nodes = {event_id: event for event_id, event in self.index.by_id.items()
                 if roots[event_id] == event_id}
        if gate:
            accepted, negative, stats = build_edges(pairs, verdicts, roots)
        else:
            with mock.patch.object(thread_evidence, "gate", return_value=(True, "")):
                accepted, negative, stats = build_edges(pairs, verdicts, roots)
        groups, cluster_stats = thread_identity.cluster(nodes, accepted, negative)
        return expand_folds(groups, roots), stats, cluster_stats

    def _thread_of(self, groups, event_id):
        for index, group in enumerate(groups):
            if event_id in group:
                return index
        return None

    def _assert_apart(self, groups, left, right, message):
        left_thread = self._thread_of(groups, left)
        self.assertFalse(
            left_thread is not None and left_thread == self._thread_of(groups, right),
            message)

    def test_uranium_ban_does_not_reach_the_us_eight_units(self):
        """러시아산 우라늄 규제 ↔ 미국 원전 8기 건설.

        중간의 '웨스팅하우스 지분 확보 시나리오' 가 다리였다. 그 다리와 원전 8기
        사이에는 이미 `different_thread` 판정이 있었다.
        """
        groups, _stats, _cluster = self._run()
        self._assert_apart(groups, "issue-79643a341ee815a2", "story-2eba66f067fdc974",
                           "우라늄 수입 규제와 미국 원전 8기 건설이 한 스토리다")
        self._assert_apart(groups, "issue-79643a341ee815a2", "story-224aef821c084125",
                           "공통 기업 하나로 만든 cause_effect 가 살아남았다")

    def test_the_veto_alone_would_also_have_blocked_it(self):
        """게이트를 꺼도 막힌다 — 거부권이 **독립된** 2차 방어선임을 못 박는다.

        게이트가 켜져 있으면 우라늄 고리는 판정 단계에서 이미 죽어서 거부권이
        발동할 일조차 없다. 그러면 거부권이 도는지 이 픽스처로는 알 수 없다.
        """
        groups, _stats, cluster_stats = self._run(gate=False)
        self._assert_apart(groups, "issue-79643a341ee815a2", "story-2eba66f067fdc974",
                           "거부권만으로는 우라늄이 원전 8기까지 갔다")
        self.assertGreaterEqual(cluster_stats["blocked_negative_edge"], 1,
                                "거부권이 한 번도 발동하지 않았다")

    def test_the_korea_us_negotiation_thread_survives(self):
        """같은 사안의 정상 스토리는 남아야 한다 — 이것이 없으면 위 테스트는 무의미하다."""
        groups, _stats, _cluster = self._run()
        negotiation = ["story-d5217cd852b861bd", "issue-40c55eaaa3e1c6e4",
                       "story-4e1bbcf5fe2a6b99", "story-a3e57010d24635a7",
                       "story-7524648ff8032947", "story-2eba66f067fdc974"]
        threads = {self._thread_of(groups, event_id) for event_id in negotiation}
        self.assertEqual(len(threads), 1, "한미 웨스팅하우스 협상이 조각났다")
        self.assertNotIn(None, threads)

    def test_smr_regulation_does_not_absorb_an_individual_licence(self):
        """SMR 규제체계 ↔ 오르비텍 핵연료물질 사용 허가.

        근거가 "원자력안전위원회의 규제 및 예산 관련 활동임" 하나였다. 기관이
        같다는 것은 장기 스토리의 근거가 아니다.
        """
        groups, _stats, _cluster = self._run()
        for budget in ("story-87c6c33c04c22365", "story-706bd2b20f78dfa3"):
            self._assert_apart(groups, budget, "story-51c2f9141fbf7dee",
                               f"{budget} 와 오르비텍 개별 허가가 한 스토리다")
        self._assert_apart(groups, "issue-bccf5f15f321ec1b", "story-51c2f9141fbf7dee",
                           "SMR 입지 규제와 오르비텍 개별 허가가 한 스토리다")

    def test_the_duplicate_budget_events_never_split_apart(self):
        """제목이 글자까지 같은 두 사건이 서로 다른 스토리로 갈리면 안 된다."""
        groups, _stats, _cluster = self._run()
        left = self._thread_of(groups, "story-87c6c33c04c22365")
        right = self._thread_of(groups, "story-706bd2b20f78dfa3")
        self.assertEqual(left, right, "같은 제목의 사본이 두 스토리로 갈렸다")

    def test_replay_is_idempotent(self):
        """PART H #9 — 같은 입력이면 같은 묶음이다."""
        first, _s1, _c1 = self._run()
        second, _s2, _c2 = self._run()
        self.assertEqual([sorted(group) for group in first],
                         [sorted(group) for group in second])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
