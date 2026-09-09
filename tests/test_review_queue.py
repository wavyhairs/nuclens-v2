"""한 서버가 규칙이 정반대인 두 대기열을 다룬다 — 그 경계가 새지 않는가.

blind 는 아무 참고정보도 내보내면 안 되고, identity 는 판정 근거를 내보내야 한다.
한쪽 규칙이 다른 쪽으로 새면 조용히 새고, 그때 나온 판정은 쓸 수 없다.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from tools import blind_relabel, identity_contract_map, review_queue
from tools.gold_provenance import FIXTURES


def _walk(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif node is not None:
        yield str(node)


class QueueCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = review_queue.build_state()
        cls.cases = cls.state["cases"]

    def test_both_queues_are_present(self):
        counts = {}
        for case in self.cases:
            counts[case["queue"]] = counts.get(case["queue"], 0) + 1
        # blind 는 1회차 표본 15 + 무너진 층의 재라벨분이다. 크기를 상수로 박으면
        # 재라벨 대상이 늘어도 큐에 안 실리는 것을 눈치채지 못한다.
        self.assertEqual(counts["identity"], 22)
        self.assertGreaterEqual(counts["blind"], 15)

    def test_the_blind_queue_carries_the_relabel_round(self):
        from tools import blind_relabel, gold_provenance

        payloads = blind_relabel._payloads()
        expected = ({row["case_id"] for row in
                     gold_provenance.select_blind_sample(payloads)}
                    | {row["case_id"] for row in
                       gold_provenance.relabel_queue(payloads)})
        self.assertEqual({c["id"] for c in self.cases if c["queue"] == "blind"},
                         expected)

    def test_identity_queue_is_exactly_the_undecidable_union(self):
        cases = json.loads((FIXTURES / "identity_candidates.json")
                           .read_text(encoding="utf-8"))["cases"]
        expected = set(review_queue.identity_review_ids(cases))
        actual = {case["id"] for case in self.cases if case["queue"] == "identity"}
        self.assertEqual(actual, expected)

    def test_cases_decidable_by_contract_are_not_queued(self):
        # reason code 로 갈리는 쌍을 사람에게 다시 묻는 것은 낭비다.
        cases = {case["id"]: case for case in
                 json.loads((FIXTURES / "identity_candidates.json")
                            .read_text(encoding="utf-8"))["cases"]}
        for case in self.cases:
            if case["queue"] != "identity":
                continue
            with self.subTest(case=case["id"]):
                code = cases[case["id"]]["reason_code"]
                self.assertIn(
                    identity_contract_map.REVIEW,
                    {identity_contract_map.verdict(code, "issue_review"),
                     identity_contract_map.verdict(code, "dedup")})


class BlindnessBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = review_queue.build_state()
        cls.blind = [c for c in cls.state["cases"] if c["queue"] == "blind"]
        cls.identity = [c for c in cls.state["cases"] if c["queue"] == "identity"]

    def test_blind_cases_leak_no_answer_bearing_field(self):
        tokens = set(_walk(self.blind))
        for name in blind_relabel.FORBIDDEN:
            with self.subTest(field=name):
                self.assertNotIn(name, tokens)

    def test_identity_cases_never_expose_model_or_cache_verdicts(self):
        # identity 는 근거를 보여 주지만 모델 판정은 여기서도 가린다.
        tokens = set(_walk(self.identity))
        for name in ("prior_llm_verdict_not_gold", "prior_llm_reason_not_gold",
                     "cached_review_not_gold", "model_comparison", "human_label"):
            with self.subTest(field=name):
                self.assertNotIn(name, tokens)

    def test_identity_cases_carry_enough_to_judge_the_relation(self):
        for case in self.identity:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["left"]["title"])
                self.assertTrue(case["right"]["title"])
                self.assertIn("date_gap_days", case["context"])

    def test_the_two_queues_offer_different_label_sets(self):
        self.assertEqual(self.state["queues"]["blind"]["labels"],
                         list(blind_relabel.LABELS))
        self.assertEqual(self.state["queues"]["identity"]["labels"],
                         list(review_queue.IDENTITY_RELATIONS))


class RelationMappingTests(unittest.TestCase):
    """사람이 고르는 관계가 두 계약의 갈림을 실제로 표현하는가."""

    def test_every_relation_maps_to_both_contracts(self):
        for relation in review_queue.IDENTITY_RELATIONS:
            with self.subTest(relation=relation):
                row = review_queue.RELATION_TO_VERDICT[relation]
                self.assertEqual(set(row), {"issue_review", "dedup"})

    def test_the_contract_split_is_expressible(self):
        # 계약이 갈리는 축은 "후속에 새 행동이 있었나" 하나다. 그 축을 사람이
        # 고를 수 없으면 두 profile 의 정답을 동시에 만들 수 없다.
        row = review_queue.RELATION_TO_VERDICT["FOLLOW_UP_NEW_ACTION"]
        self.assertEqual(row["issue_review"], "MERGE")
        self.assertEqual(row["dedup"], "SEPARATE")
        same = review_queue.RELATION_TO_VERDICT["FOLLOW_UP_NO_NEW_ACTION"]
        self.assertEqual(same["issue_review"], same["dedup"])


class SidecarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(TemporaryDirectory()))
        patcher = mock.patch.object(review_queue, "SIDECAR_DIR", self.tmp)
        self.enterContext(patcher)

    def test_each_queue_writes_its_own_sidecar(self):
        review_queue.save("blind", "case-a", "REPAIR")
        review_queue.save("identity", "case-b", "SAME_EVENT")
        self.assertEqual(review_queue.load(("blind",)), {"case-a": "REPAIR"})
        self.assertEqual(review_queue.load(("identity",)), {"case-b": "SAME_EVENT"})
        self.assertEqual(len(review_queue.load()), 2)

    def test_a_label_from_the_wrong_queue_is_refused(self):
        # identity 관계를 blind 대기열에 넣으면 두 판정이 섞인다.
        with self.assertRaises(ValueError):
            review_queue.save("blind", "case-a", "SAME_EVENT")
        with self.assertRaises(ValueError):
            review_queue.save("identity", "case-b", "PASS")

    def test_sidecar_says_it_is_not_canonical_and_not_pushed(self):
        review_queue.save("blind", "case-a", "PASS")
        stored = json.loads(
            review_queue.sidecar_path("blind").read_text(encoding="utf-8"))
        self.assertIn("자동 승격되지 않는다", stored["_comment"])
        self.assertIn("gitignore", stored["_comment"])

    def test_progress_survives_a_reopen(self):
        review_queue.save("blind", "case-a", "PASS")
        review_queue.save("blind", "case-c", "BLOCK")
        self.assertEqual(review_queue.load(("blind",)),
                         {"case-a": "PASS", "case-c": "BLOCK"})


if __name__ == "__main__":
    unittest.main()
