"""검색 예산과 판정 유지 — 후속 사건이 스스로를 고립시키지 않게 한다.

왜 이 파일이 따로 있나
----------------------
`test_event_retrieval.py` 는 점수와 신호를 지키고 `test_event_retrieval_determinism.py`
는 같은 입력이 같은 후보를 내는지를 지킨다. 여기서 지키는 것은 그 앞이다 —
**검색이 찾아올 수 있는 것을 찾아오는가**, 그리고 **한 번 찾아 판정한 것을 다음
회차에 잃지 않는가.**

2026-09-20 SAR 실측이 두 검사의 이유다.

① 죽은 낱말이 예산을 먹었다
   `candidates()` 는 IDF 상위 12개 낱말로 후보 풀을 넓힌다. 그런데 IDF 가 가장
   높은 낱말은 **자기 혼자만 가진 낱말**(df=1)이고, 그 낱말의 역색인 항목은
   `{자기 자신}` 뿐이라 구조적으로 아무것도 찾아오지 못한다.

   9/18 「기후부, 전북 진안·충남 금산서 계절별 송전용량(SAR) 시범사업 시행」은
   진안·금산·1년간·탄력적으로 같은 df=1 낱말이 13개라 12칸을 다 먹었다. 8월
   사건과 이어 주는 `sar`·`계절별`·`송전용량`(df=2)이 잘려 **후보 풀이 0건**
   이었다. 그 쌍이 살아난 것은 8월 사건 쪽이 짧아 반대 방향에서 끌어왔기
   때문이다 — 운이었다.

② 판정한 쌍이 검색에서 밀려났다
   원장이 자라면 IDF 가 움직여 어제 상위 12칸에 들던 낱말이 오늘 밀려난다
   (실측 2026-09-19: 같은 원장으로 후보가 3,595 / 3,602 / 3,615쌍). 밀려난 쌍의
   판정은 그래프에 실리지 않는다 — 고리가 사라져 스토리가 쪼개지고, 거부권이
   사라져 PR #147 이 막던 오병합이 되살아난다. 둘 다 조용한 고장이다.

픽스처는 라이브 원장에서 그대로 떠 왔다. 제목도 판정 문구도 손대지 않았다.
"""

import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import event_retrieval
import thread_judge
from event_retrieval import Event, Index
from tools.build_threads import gather_pairs, sticky_pairs

# ── 라이브 실측 픽스처 (2026-09-20, issue_ledger.json) ──────────────────
SAR_AUG = "issue-099ce0d43f46036a"
SAR_SEP = "story-9e227f9daff25cd5"

SAR_AUG_TITLE = "기후부, 전력망 효율 위해 9월부터 '계절별 송전용량' 시범 적용"
SAR_AUG_SUMMARY = ("기후에너지환경부는 전력망 효율을 높이기 위해 9월부터 기온이 낮은 "
                   "겨울철 송전용량을 확대하는 '계절별 송전용량(SAR)' 방식을 일부 "
                   "선로에 시범 적용한다.")
SAR_SEP_TITLE = "기후부, 전북 진안·충남 금산서 계절별 송전용량(SAR) 시범사업 시행"
SAR_SEP_SUMMARY = ("기후에너지환경부가 19일부터 1년간 전북 진안과 충남 금산에서 기온에 "
                   "따라 송전용량을 탄력적으로 조정하는 SAR 시범사업을 시행한다.")


def event(issue_id: str, title: str, summary: str, first: str, last: str) -> Event:
    """원장 행 하나를 `Event` 로 세운다. 두 SAR 사건 모두 구조화 칸이 **비어 있다**
    (`units=[] entity_ids=[] facts={}`) — 이 사례가 어려운 이유가 그것이다."""
    text = f"{title} {summary}"
    return Event(
        issue_id=issue_id, title=title, summary=summary,
        first_seen=date.fromisoformat(first), last_seen=date.fromisoformat(last),
        units=frozenset(), plants=frozenset(), entities=frozenset(),
        assets=frozenset(), actors=frozenset(), action="",
        tokens=frozenset(event_retrieval._tokens(text)),
        briefing_count=1,
        raw={"title": title, "summary": summary, "facts": {}},
    )


def sar_pair() -> tuple[Event, Event]:
    return (event(SAR_AUG, SAR_AUG_TITLE, SAR_AUG_SUMMARY, "2026-08-24", "2026-08-24"),
            event(SAR_SEP, SAR_SEP_TITLE, SAR_SEP_SUMMARY, "2026-09-19", "2026-09-19"))


def noise(count: int) -> list[Event]:
    """풀을 희석하는 무관한 사건들. IDF 가 실제 원장처럼 벌어지게 한다."""
    return [event(f"issue-noise{index:03d}",
                  f"월성 {index}호기 계획예방정비 착수 보고 {index}",
                  f"한수원이 월성 {index}호기의 정기 정비를 시작했다고 {index}일 밝혔다.",
                  "2026-07-01", "2026-07-01") for index in range(count)]


class RetrievalBudgetTests(unittest.TestCase):
    """죽은 낱말이 예산을 먹지 않는다."""

    def setUp(self):
        self.aug, self.sep = sar_pair()
        self.index = Index([self.aug, self.sep] + noise(40))

    def test_september_event_finds_august_on_its_own(self):
        """**이 검사가 SAR 회귀의 핵심이다.**

        종전에는 9월 사건의 후보 풀이 0건이었고, 쌍은 8월 쪽에서만 만들어졌다.
        한쪽 방향에만 기대면 제목이 조금만 길어져도 연결이 통째로 사라진다.
        """
        found = [row["issue_id"] for row in
                 event_retrieval.candidates(self.index, self.sep, limit=12, min_score=3.0)]
        self.assertIn(SAR_AUG, found, "9월 사건이 8월 사건을 스스로 못 찾는다")

    def test_retrieval_is_symmetric_for_the_sar_pair(self):
        for source, target in ((self.sep, SAR_AUG), (self.aug, SAR_SEP)):
            with self.subTest(source=source.issue_id):
                found = [row["issue_id"] for row in event_retrieval.candidates(
                    self.index, source, limit=12, min_score=3.0)]
                self.assertIn(target, found)

    def test_tokens_only_this_event_owns_do_not_eat_the_budget(self):
        """df=1 낱말은 역색인 항목이 자기 자신뿐이라 아무것도 찾아오지 못한다.

        9월 사건의 `진안`·`금산서`·`1년간` 이 그런 낱말이고, 종전에는 이것들이
        IDF 최상위라 12칸을 앞에서부터 다 먹었다.
        """
        owned = [token for token in self.sep.tokens
                 if len(self.index.postings.get(f"t:{token}", ())) == 1]
        self.assertTrue(owned, "픽스처에 이 사건만 가진 낱말이 없다 — 재현이 안 된다")
        for token in owned:
            self.assertEqual(self.index.postings.get(f"t:{token}"), {SAR_SEP})
        # 그래도 공유 낱말로 8월을 찾아온다.
        shared = self.sep.tokens & self.aug.tokens
        self.assertTrue({"sar", "계절별", "송전용량"} <= shared,
                        f"두 사건이 공유하는 낱말이 달라졌다: {sorted(shared)}")

    def test_a_long_title_cannot_isolate_an_event(self):
        """고유명사를 더 붙여도 연결이 끊기지 않는다 — 후속 사건일수록 길어진다."""
        longer = event(
            SAR_SEP,
            SAR_SEP_TITLE + " 전북 진안군 충남 금산군 한국전력공사 송변전운영처 공고 제2026-318호",
            SAR_SEP_SUMMARY + " 진안군 마령면과 금산군 제원면 구간이 대상이다.",
            "2026-09-19", "2026-09-19")
        index = Index([self.aug, longer] + noise(40))
        found = [row["issue_id"] for row in
                 event_retrieval.candidates(index, longer, limit=12, min_score=3.0)]
        self.assertIn(SAR_AUG, found, "제목이 길어지자 사건이 스스로를 고립시켰다")

    def test_generic_grid_words_alone_do_not_make_a_candidate(self):
        """일반어만 공유하는 쌍은 후보로 올라오지 않는다 — 넓힌 만큼 새는지 본다."""
        generic = event("issue-generic", "유럽 전력망 노후화 대응 투자 확대",
                        "유럽 각국이 노후 전력망 교체에 투자를 늘리고 있다.",
                        "2026-09-01", "2026-09-01")
        index = Index([self.aug, self.sep, generic] + noise(40))
        found = [row["issue_id"] for row in
                 event_retrieval.candidates(index, generic, limit=12, min_score=3.0)]
        self.assertNotIn(SAR_SEP, found)
        self.assertNotIn(SAR_AUG, found)

    def test_score_pair_matches_what_retrieval_reports(self):
        """같은 척도를 두 번 구현하지 않았는가 — `sticky_pairs` 가 이 함수를 쓴다."""
        direct = event_retrieval.score_pair(self.index, self.sep, self.aug)
        found = next(row for row in event_retrieval.candidates(
            self.index, self.sep, limit=12, min_score=3.0)
            if row["issue_id"] == SAR_AUG)
        self.assertEqual(direct["score"], found["score"])
        self.assertEqual(direct["gap_days"], found["gap_days"])
        self.assertEqual(direct["signals"], found["signals"])


class StickyPairTests(unittest.TestCase):
    """판정한 쌍은 검색에서 밀려나도 그래프에 남는다."""

    def setUp(self):
        self.aug, self.sep = sar_pair()
        self.index = Index([self.aug, self.sep] + noise(40))
        self.key = thread_judge.pair_id(SAR_AUG, SAR_SEP)
        self.cache = {self.key: {
            "verdict": "same_thread", "relationship": "stage_progress",
            "reason": "SAR 시범적용 발표 후 실제 사업 시행",
            "prompt_version": thread_judge.PROMPT_VERSION,
        }}

    def test_a_judged_pair_survives_falling_out_of_retrieval(self):
        """검색이 이 쌍을 못 찾는 회차에도 판정은 유효하다."""
        # min_score 를 올려 검색이 아무것도 못 찾게 만든다 — 점수 경계에서 쌍이
        # 밀려나는 실제 상황과 같은 모양이다.
        rows = gather_pairs(self.index, per_event=12, min_score=999.0,
                            cap=None, cache=self.cache)
        keys = {row["key"] for row in rows}
        self.assertIn(self.key, keys, "판정한 쌍이 그래프에서 사라졌다")
        kept = next(row for row in rows if row["key"] == self.key)
        self.assertTrue(kept["sticky"])
        # 되살린 쌍도 신호를 들고 온다 — `thread_evidence.gate` 가 그것을 읽는다.
        self.assertIn("lexical", kept["signals"])

    def test_sticky_pairs_cost_no_llm_call(self):
        """되살린 쌍은 전부 캐시 적중이라 새 판정이 일어나지 않는다."""
        rows = sticky_pairs(self.index, set(), self.cache)
        with cache_file(self.cache) as path:
            _, stats = thread_judge.judge(
                rows, cache_path=path, client=_NoClient(), max_new_pairs=None)
        self.assertEqual(stats["asked"], 0)
        self.assertEqual(stats["calls"], 0)
        self.assertEqual(stats["from_cache"], len(rows))

    def test_a_pair_already_found_by_retrieval_is_not_duplicated(self):
        rows = gather_pairs(self.index, per_event=12, min_score=3.0,
                            cap=None, cache=self.cache)
        keys = [row["key"] for row in rows]
        self.assertEqual(keys.count(self.key), 1)
        self.assertFalse(next(row for row in rows if row["key"] == self.key).get("sticky"))

    def test_a_pair_whose_event_is_gone_is_not_revived(self):
        """한쪽이 원장에서 사라진 쌍은 고리를 걸 자리가 없다."""
        index = Index([self.sep] + noise(40))
        self.assertEqual(sticky_pairs(index, set(), self.cache), [])

    def test_an_unusable_cache_entry_is_not_revived(self):
        """계약 판본이 다르거나 값이 깨진 항목은 되살리지 않는다.

        그건 되살리는 것이 아니라 **새로 묻는 것**이라, 공짜라는 전제가 깨진다.
        """
        for broken in ({"verdict": "same_thread", "prompt_version": 0},
                       {"verdict": "아무말", "prompt_version": thread_judge.PROMPT_VERSION},
                       {"prompt_version": thread_judge.PROMPT_VERSION},
                       "문자열"):
            with self.subTest(entry=broken):
                self.assertEqual(sticky_pairs(self.index, set(), {self.key: broken}), [])

    def test_no_cache_is_not_an_error(self):
        for empty in (None, {}):
            with self.subTest(cache=empty):
                self.assertEqual(sticky_pairs(self.index, set(), empty), [])
                rows = gather_pairs(self.index, per_event=12, min_score=3.0,
                                    cap=None, cache=empty)
                self.assertTrue(all(not row.get("sticky") for row in rows))

    def test_rebuilding_with_the_same_input_yields_the_same_pairs(self):
        """재빌드가 그래프를 흔들지 않는다. 순서까지 같아야 한다."""
        first = [row["key"] for row in gather_pairs(
            self.index, per_event=12, min_score=3.0, cap=None, cache=self.cache)]
        second = [row["key"] for row in gather_pairs(
            self.index, per_event=12, min_score=3.0, cap=None, cache=self.cache)]
        self.assertEqual(first, second)

    def test_cap_does_not_drop_judged_pairs(self):
        """`--cap` 은 검색 쪽을 자르는 손잡이다. 이미 아는 답까지 버리지 않는다."""
        rows = gather_pairs(self.index, per_event=12, min_score=3.0,
                            cap=1, cache=self.cache)
        self.assertIn(self.key, {row["key"] for row in rows})


def askable_pair() -> list[Event]:
    """규칙이 거부하지 않는 쌍 — 좁은 신호(엔티티)를 공유한다.

    작은 픽스처에서 어휘 구제(`LEXICAL_RESCUE`)에 기대면 안 된다. lexical 은 IDF
    가중이라 코퍼스 크기에 딸려 움직여서, 40건짜리 색인에서는 같은 문장이라도
    실제 원장(888건)의 절반쯤으로 나온다. 예산 계산을 재는 검사가 코퍼스 크기에
    흔들리지 않도록 구조화 신호를 쓴다.
    """
    rows = []
    for index in range(3):
        row = event(f"issue-askable{index}",
                    f"한빛 3호기 계속운전 심사 {index}단계 진행",
                    f"원안위가 한빛 3호기 계속운전 심사의 {index}단계를 진행한다.",
                    f"2026-08-0{index + 1}", f"2026-08-0{index + 1}")
        rows.append(Event(**{**row.__dict__, "entities": frozenset({"hanbit"})}))
    return rows


class VisibilityGateBudgetTests(unittest.TestCase):
    """초회 신규 판정량이 화면을 내리지 않는가.

    `thread_web.gate()` 는 `build.failed / candidates` 를 본다. 예산에 걸려 **다음
    회차로 밀린** 쌍은 `failed` 가 아니라 `deferred` 로 샌다 — 그 구분이 이번
    변경의 안전장치다. 후보가 3,620 → 6,331 로 늘면서 신규 판정 740쌍이 한 번에
    생기는데, 그것이 failed 로 셌다면 740/6,331 = 11.7% 로 한계(1%)를 넘어 화면이
    통째로 내려간다.
    """

    def setUp(self):
        self.aug, self.sep = sar_pair()
        self.index = Index([self.aug, self.sep] + askable_pair() + noise(40))

    def test_deferred_pairs_are_not_counted_as_failures(self):
        rows = gather_pairs(self.index, per_event=12, min_score=3.0, cap=None, cache=None)
        askable = [row for row in rows
                   if thread_judge.rule_verdict(row["left"], row["right"],
                                                row["signals"]) is None]
        self.assertGreater(len(askable), 1, "픽스처에 물을 쌍이 모자라다")
        with cache_file({}) as path:
            _, stats = thread_judge.judge(rows, cache_path=path, client=_NoClient(),
                                          max_new_pairs=1)
        # 예산에 걸린 쌍은 밀린 것이지 실패한 것이 아니다.
        self.assertEqual(stats.get("deferred"), len(askable) - 1)
        self.assertLessEqual(stats["failed"], 1)

    def test_a_missing_key_leaves_the_backlog_for_the_next_run(self):
        """키가 없으면 새 쌍만 미판정으로 남고 기존 판정은 그대로 쓰인다."""
        askable = askable_pair()
        key = thread_judge.pair_id(askable[0].issue_id, askable[1].issue_id)
        cache = {key: {"verdict": "same_thread", "relationship": "stage_progress",
                       "reason": "고정", "prompt_version": thread_judge.PROMPT_VERSION}}
        rows = gather_pairs(self.index, per_event=12, min_score=3.0, cap=None, cache=cache)
        with cache_file(cache) as path:
            verdicts, stats = thread_judge.judge(rows, cache_path=path,
                                                 client=_NoClient(), max_new_pairs=None)
        self.assertEqual(stats["status"], "no_api_key")
        self.assertEqual(verdicts[key]["verdict"], "same_thread")
        self.assertEqual(verdicts[key]["method"], "cache")

    def test_a_cheap_rule_does_not_throw_away_a_judgment_already_made(self):
        """**값싼 거부가 판정을 덮지 않는다.**

        `no_shared_identity` 는 "구조화 칸이 비었고 어휘도 문턱 아래"라는 뜻인데,
        lexical 은 IDF 가중이라 원장이 자라면 같은 쌍의 점수가 내려간다. 판정할
        때는 문턱 위였던 쌍이 몇 주 뒤 아래로 떨어지고, 그 순간 판정이 버려졌다
        (실측 2026-09-20: 살아있는 고리 67개 · 거부권 57개).
        """
        key = thread_judge.pair_id(SAR_AUG, SAR_SEP)
        # 이 픽스처에서 SAR 쌍은 구조화 신호가 없고 lexical 이 문턱 아래다 —
        # 종전이라면 규칙이 먼저 거부했을 쌍이다.
        self.assertIsNotNone(thread_judge.rule_verdict(
            self.aug, self.sep,
            event_retrieval.score_pair(self.index, self.aug, self.sep)["signals"]))
        cache = {key: {"verdict": "same_thread", "relationship": "stage_progress",
                       "reason": "SAR 시범적용 발표 후 실제 사업 시행",
                       "prompt_version": thread_judge.PROMPT_VERSION}}
        rows = gather_pairs(self.index, per_event=12, min_score=3.0, cap=None, cache=cache)
        with cache_file(cache) as path:
            verdicts, _ = thread_judge.judge(rows, cache_path=path,
                                             client=_NoClient(), max_new_pairs=None)
        self.assertEqual(verdicts[key]["verdict"], "same_thread")
        self.assertEqual(verdicts[key]["method"], "cache")

    def test_a_structural_contradiction_still_beats_the_cache(self):
        """호기 모순은 값싼 거부가 아니라 사실이다 — 별칭표가 나중에 알아보기도 한다."""
        left = event("issue-kori2", "고리 2호기 계속운전 심사 착수",
                     "원안위가 고리 2호기 계속운전 심사에 착수했다.", "2026-08-01", "2026-08-01")
        right = event("issue-kori3", "고리 3호기 계속운전 심사 착수",
                      "원안위가 고리 3호기 계속운전 심사에 착수했다.", "2026-09-01", "2026-09-01")
        index = Index([left, right] + noise(40))
        key = thread_judge.pair_id(left.issue_id, right.issue_id)
        cache = {key: {"verdict": "same_thread", "relationship": "stage_progress",
                       "reason": "모델이 놓쳤다", "prompt_version": thread_judge.PROMPT_VERSION}}
        rows = gather_pairs(index, per_event=12, min_score=3.0, cap=None, cache=cache)
        with cache_file(cache) as path:
            verdicts, _ = thread_judge.judge(rows, cache_path=path,
                                             client=_NoClient(), max_new_pairs=None)
        self.assertEqual(verdicts[key]["verdict"], "different_thread")
        self.assertEqual(verdicts[key]["reason"], thread_judge.REJECT_UNIT_CONFLICT)


@contextmanager
def cache_file(entries: dict):
    """판정 캐시를 **임시 파일로 격리한다.**

    `thread_judge.judge()` 는 `cache_path=None` 이면 운영 캐시
    (`thread_llm_reviews.json`, 3,170쌍)를 읽는다. 검사가 그 파일을 읽으면 결과가
    저장소 상태에 딸려 흔들리고, 쓰기까지 가면 운영 데이터를 건드린다.
    """
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "thread_llm_reviews.json"
        path.write_text(json.dumps(
            {"prompt_version": thread_judge.PROMPT_VERSION, "threads": entries},
            ensure_ascii=False), encoding="utf-8")
        yield path


class _NoClient:
    """키를 쓰지 않겠다는 뜻. `tools/build_threads._OfflineClient` 와 같은 계약."""

    @staticmethod
    def is_available() -> bool:
        return False


if __name__ == "__main__":
    unittest.main()
