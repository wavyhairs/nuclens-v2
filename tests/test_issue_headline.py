"""표시 제목은 화면까지만 간다 — 신원 판정에는 닿지 않는다.

이 파일이 지키는 것은 두 가지다.

    ① `headline_display` 가 어떤 상류 판정에도 입력되지 않는다 (PART F #2 · H #8)
    ② 무슨 일이 있어도 카드 제목 칸이 비지 않는다

②가 ①만큼 중요하다. 표시 전용 칸은 폴백이 없으면 LLM 이 한 번 죽을 때마다
화면이 빈다. 그래서 `build()` 는 **모든 이슈를 담아 돌려준다** — 물어보지
못했으면 원 제목이 그 자리에 선다.
"""

import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import event_retrieval
import issue_headline
import thread_identity
from event_retrieval import Event
from tools.build_threads import build_edges, expand_folds

FIXTURE = Path(__file__).parent / "fixtures" / "thread_misjoin_2026-09-19.json"
ROOT = Path(__file__).resolve().parent.parent

# 제목에서 사건 신원을 읽는 모듈들. 여기 어디에도 표시 칸이 나타나면 안 된다.
IDENTITY_MODULES = (
    "dedup.py", "event_identity.py", "event_stage.py", "issue_continuity.py",
    "issue_ledger.py", "story_identity.py", "story_fingerprint.py",
    "asset_alias.py", "event_retrieval.py",
    "thread_judge.py", "thread_identity.py", "thread_evidence.py",
)


class _FakeClient:
    """정해진 답만 돌려주는 모델. 네트워크를 쓰지 않는다."""

    def __init__(self, headlines, *, raise_on_call=False):
        self.headlines = headlines
        self.raise_on_call = raise_on_call
        self.calls = 0

    @staticmethod
    def is_available():
        return True

    def call_json(self, _system, user_message, **_kwargs):
        self.calls += 1
        if self.raise_on_call:
            raise RuntimeError("모델이 죽었다")
        count = user_message.count("[")
        return {"items": [{"idx": idx, "headline": self.headlines[idx]}
                          for idx in range(min(count, len(self.headlines)))]}


def _issue(issue_id="i1", title="원안위, 오르비텍 핵연료물질 사용 허가 의결",
           change="", detail="원안위가 9월 15일 오르비텍의 핵연료물질 사용을 허가했다"):
    return {"issue_id": issue_id, "title": title, "change": change, "detail": detail}


class IdentityIsolationTests(unittest.TestCase):
    """PART H #8 — 표시 제목을 바꿔도 issue_id·thread_id 가 움직이지 않는다."""

    def test_no_identity_module_mentions_the_display_field(self):
        """정적으로 못 박는다. 배선은 실수로 생기지 코드 리뷰로만 막히지 않는다."""
        offenders = []
        for name in IDENTITY_MODULES:
            source = (ROOT / name).read_text(encoding="utf-8")
            body = "\n".join(line for line in source.splitlines()
                             if not line.lstrip().startswith("#"))
            for needle in ("headline_display", "issue_headline"):
                if needle in body:
                    offenders.append(f"{name} 가 {needle} 를 읽는다")
        self.assertEqual(offenders, [])

    def test_the_display_field_does_not_move_thread_ids(self):
        """원장 행에 표시 칸을 심어도 스토리 묶음이 그대로여야 한다."""
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

        def run(store):
            with mock.patch.object(event_retrieval.issue_ledger, "load_store",
                                   return_value=store):
                events = event_retrieval.load_events()
            index = event_retrieval.Index(events)
            pairs, verdicts = [], {}
            for key, row in payload["judgments"].items():
                left_id, right_id = key.split("--")
                pairs.append({"key": key, "left": index.by_id[left_id],
                              "right": index.by_id[right_id],
                              "signals": row["signals"]})
                verdicts[key] = {"verdict": row["verdict"],
                                 "relationship": row["relationship"],
                                 "method": "cache"}
            roots = thread_identity.fold_duplicates(index.events)
            nodes = {k: v for k, v in index.by_id.items() if roots[k] == k}
            accepted, negative, _relations, _stats = build_edges(pairs, verdicts, roots)
            groups, _cstats = thread_identity.cluster(nodes, accepted, negative)
            resolved = thread_identity.resolve(
                expand_folds(groups, roots), index.by_id, {})
            return sorted((row["thread_id"], tuple(sorted(row["event_ids"])))
                          for row in resolved["threads"])

        plain = {"issues": payload["issues"]}
        decorated = {"issues": {
            issue_id: {**row,
                       "headline_display": f"완전히 다른 표시 제목 {issue_id}",
                       "change_display": "달라진 것", "card_why": "왜 중요한가"}
            for issue_id, row in payload["issues"].items()}}
        self.assertEqual(run(plain), run(decorated))


class FallbackTests(unittest.TestCase):
    """카드 제목 칸은 절대 비지 않는다."""

    def _build(self, issues, **kwargs):
        with mock.patch.object(issue_headline, "save_cache"), \
             mock.patch.object(issue_headline, "load_cache", return_value={}):
            return issue_headline.build(issues, **kwargs)

    def test_without_a_key_every_issue_keeps_its_own_title(self):
        class Offline:
            @staticmethod
            def is_available():
                return False
        issues = [_issue("i1"), _issue("i2", title="고리 3·4호기 계속운전 연내 결론")]
        out, stats = self._build(issues, client=Offline())
        self.assertEqual(out["i1"], issues[0]["title"])
        self.assertEqual(out["i2"], issues[1]["title"])
        self.assertEqual(stats["status"], "no_api_key")

    def test_a_dead_model_falls_back_instead_of_blanking(self):
        out, stats = self._build([_issue()], client=_FakeClient([], raise_on_call=True))
        self.assertEqual(out["i1"], _issue()["title"])
        self.assertEqual(stats["fell_back"], 1)

    def test_investment_vocabulary_is_thrown_away(self):
        out, stats = self._build(
            [_issue()], client=_FakeClient(["원전 수혜주 기대감 확산…호재 지속"]))
        self.assertEqual(out["i1"], _issue()["title"], "투자 문법이 화면까지 갔다")
        self.assertEqual(stats["rejected"], 1)

    def test_an_invented_number_is_thrown_away(self):
        out, stats = self._build(
            [_issue()], client=_FakeClient(["원안위, 오르비텍 등 17개사 허가 의결"]))
        self.assertEqual(out["i1"], _issue()["title"], "없던 수치가 제목에 올라갔다")
        self.assertEqual(stats["rejected"], 1)

    def test_an_overlong_headline_is_thrown_away(self):
        out, _stats = self._build([_issue()], client=_FakeClient(["가" * 80]))
        self.assertEqual(out["i1"], _issue()["title"])

    def test_a_good_headline_is_used(self):
        good = "원안위, 오르비텍 핵연료물질 사용 허가"
        out, stats = self._build([_issue()], client=_FakeClient([good]))
        self.assertEqual(out["i1"], good)
        self.assertEqual(stats["asked"], 1)


class CacheReuseTests(unittest.TestCase):
    """같은 입력이면 다시 묻지 않는다. 뜻이 달라지면 반드시 다시 묻는다."""

    def test_identical_input_reuses_the_stored_headline(self):
        row = _issue()
        fingerprint = issue_headline.input_fingerprint(
            row["title"], row["change"], row["detail"])
        cache = {"i1": {"headline": "저장된 제목입니다",
                        "input_fingerprint": fingerprint,
                        "prompt_version": issue_headline.PROMPT_VERSION}}
        client = _FakeClient(["새로 만든 제목"])
        with mock.patch.object(issue_headline, "load_cache", return_value=cache), \
             mock.patch.object(issue_headline, "save_cache"):
            out, stats = issue_headline.build([row], client=client)
        self.assertEqual(out["i1"], "저장된 제목입니다")
        self.assertEqual(stats["from_cache"], 1)
        self.assertEqual(client.calls, 0, "입력이 같은데 모델을 불렀다")

    def test_a_changed_input_forces_a_new_headline(self):
        row = _issue()
        stale = issue_headline.input_fingerprint(row["title"], "", "옛 요지")
        cache = {"i1": {"headline": "옛 제목", "input_fingerprint": stale,
                        "prompt_version": issue_headline.PROMPT_VERSION}}
        client = _FakeClient(["원안위, 오르비텍 핵연료물질 사용 허가"])
        with mock.patch.object(issue_headline, "load_cache", return_value=cache), \
             mock.patch.object(issue_headline, "save_cache"):
            out, _stats = issue_headline.build([row], client=client)
        self.assertEqual(out["i1"], "원안위, 오르비텍 핵연료물질 사용 허가")
        self.assertEqual(client.calls, 1)

    def test_an_old_prompt_version_is_not_trusted(self):
        row = _issue()
        fingerprint = issue_headline.input_fingerprint(
            row["title"], row["change"], row["detail"])
        cache = {"i1": {"headline": "옛 계약의 제목", "input_fingerprint": fingerprint,
                        "prompt_version": issue_headline.PROMPT_VERSION - 1}}
        client = _FakeClient(["원안위, 오르비텍 핵연료물질 사용 허가"])
        with mock.patch.object(issue_headline, "load_cache", return_value=cache), \
             mock.patch.object(issue_headline, "save_cache"):
            out, _stats = issue_headline.build([row], client=client)
        self.assertNotEqual(out["i1"], "옛 계약의 제목")

    def test_the_fingerprint_moves_with_every_input(self):
        base = issue_headline.input_fingerprint("제목", "변화", "요지")
        self.assertNotEqual(base, issue_headline.input_fingerprint("제목2", "변화", "요지"))
        self.assertNotEqual(base, issue_headline.input_fingerprint("제목", "변화2", "요지"))
        self.assertNotEqual(base, issue_headline.input_fingerprint("제목", "변화", "요지2"))
        self.assertEqual(base, issue_headline.input_fingerprint("  제목 ", "변화", "요지"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
