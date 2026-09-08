"""blind 패킷이 정말로 가려져 있는가.

이 파일의 존재 이유는 한 가지다 — 정답이 새면 재검증이 재검증이 아니게 되고,
그때 나온 일치율은 anchoring 을 재는 대신 anchoring 을 확인해 줄 뿐이다.
그리고 새는 것은 조용히 샌다. 그래서 필드를 열거해 훑는다.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools import blind_relabel, gold_provenance


def _walk(node):
    """중첩 구조 전체에서 키와 문자열 값을 모두 뱉는다."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif node is not None:
        yield str(node)


class PacketBlindnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payloads = blind_relabel._payloads()
        cls.sample = gold_provenance.select_blind_sample(cls.payloads)
        cls.packet = blind_relabel.build_packet(cls.payloads, cls.sample)

    def test_packet_has_every_sampled_case(self):
        self.assertEqual({case["id"] for case in self.packet["cases"]},
                         {row["case_id"] for row in self.sample})
        self.assertEqual(len(self.packet["cases"]), 15)

    def test_no_answer_bearing_key_appears_anywhere(self):
        tokens = set(_walk(self.packet))
        for name in blind_relabel.FORBIDDEN:
            with self.subTest(field=name):
                self.assertNotIn(name, tokens)

    def test_stored_labels_do_not_leak_as_values(self):
        # 라벨 문자열이 케이스 본문에 섞여 나가면 키를 지운 의미가 없다.
        rendered = json.dumps(self.packet, ensure_ascii=False)
        for item in self.sample:
            case = next(c for c in self.payloads[item["task"]]["cases"]
                        if c["id"] == item["case_id"])
            for field in ("human_notes", "required_repair"):
                value = case.get(field)
                if isinstance(value, str) and len(value) > 12:
                    with self.subTest(case=item["case_id"], field=field):
                        self.assertNotIn(value, rendered)

    def test_construction_kind_is_hidden_because_it_is_the_answer(self):
        # semantic 의 candidate_kind 는 한 단어로 정답을 말해 준다.
        kinds = {c.get("candidate_kind") for c in self.payloads["semantic"]["cases"]}
        rendered = json.dumps(self.packet, ensure_ascii=False)
        for kind in kinds - {None}:
            with self.subTest(kind=kind):
                self.assertNotIn(kind, rendered)

    def test_order_hides_the_strata(self):
        # 층별로 뭉쳐 나오면 "이건 perturbation 묶음"이 드러난다.
        stratum = {row["case_id"]: row["stratum"] for row in self.sample}
        order = [stratum[case["id"]] for case in self.packet["cases"]]
        runs = sum(1 for a, b in zip(order, order[1:]) if a != b)
        self.assertGreaterEqual(runs, 4, f"층이 뭉쳐 있다: {order}")

    def test_order_is_deterministic(self):
        again = blind_relabel.build_packet(self.payloads, self.sample)
        self.assertEqual([c["id"] for c in self.packet["cases"]],
                         [c["id"] for c in again["cases"]])

    def test_curation_cases_carry_source_and_output_to_judge(self):
        curation = [c for c in self.packet["cases"] if c["task"] == "curation"]
        self.assertEqual(len(curation), 9)
        for case in curation:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["source"]["title"])
                self.assertTrue(case["output"]["title_kr"])

    def test_semantic_cases_carry_claim_and_evidence(self):
        semantic = [c for c in self.packet["cases"] if c["task"] == "semantic"]
        self.assertEqual(len(semantic), 6)
        for case in semantic:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["claim"])
                self.assertIsNotNone(case["evidence"])


class SidecarTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(TemporaryDirectory())) / "blind.json"

    def test_labels_persist_without_touching_canonical_gold(self):
        blind_relabel.save_label("a", "PASS", path=self.path)
        blind_relabel.save_label("b", "BLOCK", path=self.path)
        self.assertEqual(blind_relabel.load_sidecar(self.path),
                         {"a": "PASS", "b": "BLOCK"})
        self.assertIn("자동 승격되지 않는다",
                      json.loads(self.path.read_text(encoding="utf-8"))["_comment"])

    def test_unknown_verdicts_are_refused(self):
        with self.assertRaises(ValueError):
            blind_relabel.save_label("a", "MAYBE", path=self.path)

    def test_a_missing_sidecar_is_empty_not_an_error(self):
        self.assertEqual(blind_relabel.load_sidecar(self.path), {})


if __name__ == "__main__":
    unittest.main()
