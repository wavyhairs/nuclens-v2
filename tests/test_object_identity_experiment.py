"""`experiments/object_identity` — Object 기반 Event resolver 실험의 계약.

이 검사는 두 가지를 잠근다. ① resolver 의 결정 규칙(거부권 · 키 매치 · 모름≠다름 · 단계
분열 · lexical 은 production 을 따른다 · 멱등). ② 실험이 production 에 연결되지 않았다는 것.
"""

import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from experiments.object_identity import objects as objmod  # noqa: E402
from experiments.object_identity.resolver import Item, Resolver  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def item(hash_, day, title, *, prod="", tags=(), countries=("KR",), gate_stages=()):
    row = {"title_kr": title, "title": "", "tags": list(tags)}
    return Item(hash=hash_, pub_date=day, title_kr=title, tags=list(tags), countries=list(countries),
                gate_stages=frozenset(gate_stages), production_issue=prod, signals=objmod.extract(row))


class ObjectExtractionTests(unittest.TestCase):
    def test_unit_is_the_most_specific_object(self):
        key = objmod.extract({"title_kr": "고리 2호기 계속운전 승인"}).key(use_lexicon=False)
        self.assertEqual(key, ("unit", frozenset({"kori-2"})))

    def test_generic_plant_names_are_not_read_from_free_text(self):
        """'고리' 는 일반명사와 겹친다 — 태그나 호기 없이는 Object 가 아니다."""
        self.assertIsNone(objmod.extract({"title_kr": "연결고리 끊긴 전력망 정책"}).key(use_lexicon=False))

    def test_plant_tag_yields_entity_level_object(self):
        key = objmod.extract({"title_kr": "원안위 정기검사 착수", "tags": ["#한빛원전"]}).key(use_lexicon=False)
        self.assertEqual(key, ("plant", frozenset({"hanbit"})))

    def test_summary_entities_are_off_by_default(self):
        """요약에 예시로 적힌 두코바니가 Object 가 되면 안 된다(첫 재생에서 실제로 났다)."""
        row = {"title_kr": "정부, 웨스팅하우스 지분 인수 검토",
               "verified_evidence": {"entities": ["dukovany", "barakah"]}}
        self.assertIsNone(objmod.extract(row).key(use_lexicon=False))
        self.assertEqual(objmod.extract(row, gate_entities=True).key(use_lexicon=False)[0], "plant")

    def test_lexicon_only_counts_when_enabled(self):
        row = {"title_kr": "고준위 특별법 국회 통과"}
        self.assertIsNone(objmod.extract(row).key(use_lexicon=False))
        self.assertEqual(objmod.extract(row).key(use_lexicon=True), ("lexicon", frozenset({"hlw-special-act"})))

    def test_different_units_of_one_plant_conflict(self):
        left = objmod.extract({"title_kr": "한빛 3호기 재가동"})
        right = objmod.extract({"title_kr": "한빛 4호기 재가동"})
        self.assertTrue(objmod.unit_conflict(left, right))
        self.assertFalse(objmod.same_object(left.key(use_lexicon=False), right.key(use_lexicon=False)))


class ResolverTests(unittest.TestCase):
    def resolve(self, items, **kwargs):
        kwargs.setdefault("strict_transition", True)
        kwargs.setdefault("production_bridge", "partial")
        return Resolver(**kwargs).resolve(items)

    def test_same_object_same_transition_joins_across_production_split(self):
        a = item("a", "2026-08-01", "새울 3호기 시운전 중 자동 정지", prod="p1")
        b = item("b", "2026-08-25", "새울 3호기 자동정지 원인은 운전원 조작 오류", prod="p2")
        res = self.resolve([a, b])
        self.assertEqual(res.assignment["a"], res.assignment["b"])
        self.assertEqual(res.reason_of["b"], "key_match")

    def test_new_material_stage_opens_a_new_event_on_the_same_object(self):
        a = item("a", "2026-08-01", "새울 3호기 시운전 중 자동 정지", prod="p1")
        b = item("b", "2026-08-20", "원안위, 새울 3호기 재가동 승인", prod="p1")
        res = self.resolve([a, b])
        self.assertNotEqual(res.assignment["a"], res.assignment["b"])
        self.assertEqual(res.reason_of["b"], "new:stage_split")

    def test_different_unit_never_joins_even_when_production_did(self):
        a = item("a", "2026-08-01", "한빛 3호기 재가동 승인", prod="p1")
        b = item("b", "2026-08-02", "한빛 4호기 재가동 승인", prod="p1")
        res = self.resolve([a, b])
        self.assertNotEqual(res.assignment["a"], res.assignment["b"])

    def test_unknown_stage_is_not_a_difference_but_needs_production_or_title(self):
        a = item("a", "2026-08-01", "월성 2호기 중수 누설 논란", prod="p1")
        b = item("b", "2026-08-02", "월성 2호기 관련 지역 설명회", prod="p1")            # 단계 없음
        c = item("c", "2026-08-03", "월성 2호기 앞바다 해양생태 조사 결과 공개", prod="p9")  # 단계 없음 · 다른 묶음 · 제목도 다름
        res = self.resolve([a, b, c])
        self.assertEqual(res.assignment["a"], res.assignment["b"], "production 이 같은 묶음 → 후퇴 경로로 붙는다")
        self.assertEqual(res.reason_of["b"], "production_bridge")
        self.assertNotEqual(res.assignment["a"], res.assignment["c"], "아무 근거도 없으면 붙이지 않는다")
        self.assertEqual(res.grade_of["b"], "object")

    def test_no_object_follows_production(self):
        a = item("a", "2026-08-01", "정부, 전력 인프라 투자 확대", prod="p1")
        b = item("b", "2026-08-02", "산업계, 전력 인프라 투자 환영", prod="p1")
        c = item("c", "2026-08-02", "AI 데이터센터 냉각 기술 경쟁", prod="p2")
        res = self.resolve([a, b, c])
        self.assertEqual(res.assignment["a"], res.assignment["b"])
        self.assertNotEqual(res.assignment["a"], res.assignment["c"])
        self.assertEqual({res.grade_of[h] for h in "abc"}, {"lexical"})

    def test_human_rejected_pair_is_a_veto(self):
        a = item("a", "2026-08-01", "새울 3호기 시운전 중 자동 정지", prod="p1")
        b = item("b", "2026-08-02", "새울 3호기 시운전 중 자동 정지 원인 조사", prod="p1")
        res = self.resolve([a, b], rejected_pairs={frozenset({"a", "b"})})
        self.assertNotEqual(res.assignment["a"], res.assignment["b"])

    def test_bucket_separates_a_repeat_of_the_same_transition(self):
        a = item("a", "2026-07-01", "한울 4호기 자동정지", prod="p1")
        b = item("b", "2026-09-15", "한울 4호기 자동정지", prod="p2")
        self.assertNotEqual(*[self.resolve([a, b], bucket_days=30).assignment[h] for h in "ab"])
        self.assertEqual(*[self.resolve([a, b], bucket_days=90).assignment[h] for h in "ab"])

    def test_resolution_is_idempotent_and_order_independent(self):
        rows = [
            item("a", "2026-08-01", "새울 3호기 시운전 중 자동 정지", prod="p1"),
            item("b", "2026-08-02", "새울 3호기 자동정지 원안위 조사 착수", prod="p2"),
            item("c", "2026-08-20", "원안위, 새울 3호기 재가동 승인", prod="p1"),
            item("d", "2026-08-21", "정부, 전력 인프라 투자 확대", prod="p3"),
        ]
        first = self.resolve(rows).assignment
        second = self.resolve(list(reversed(rows))).assignment
        self.assertEqual(first, second)


class IsolationTests(unittest.TestCase):
    def test_production_modules_do_not_import_the_experiment(self):
        """실험은 읽기만 한다. 어떤 production 파일도 experiments 를 import 하면 안 된다."""
        pattern = re.compile(r"^\s*(from|import)\s+experiments\b", re.M)
        offenders = []
        for path in list(ROOT.glob("*.py")) + list((ROOT / "web").glob("*.py")) + list((ROOT / "tools").glob("*.py")):
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_experiment_has_no_network_or_llm_calls(self):
        banned = re.compile(r"\b(requests|urllib|httpx|gemini_client|google\.genai)\b")
        for path in (ROOT / "experiments" / "object_identity").glob("*.py"):
            self.assertIsNone(banned.search(path.read_text(encoding="utf-8")), path.name)


if __name__ == "__main__":
    unittest.main()
