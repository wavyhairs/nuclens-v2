"""판정 승격이 이력을 지우지 않고, 무너진 층을 숨기지 않는가.

여기서 지켜야 할 선이 둘이다. 원본 라벨은 어떤 경우에도 사라지지 않아야 하고,
"표본이 전부 뒤집힌 층"은 판정에 그대로 드러나야 한다 — 그 층의 나머지 라벨을
계속 쓰면 뒤집힌 것을 모르는 채로 평가가 돌아간다.
"""

import copy
import unittest

from tools import promote_reviews, review_queue
from tools.gold_provenance import AI_ASSISTED


def _payload(rows):
    return {"cases": [dict(row) for row in rows]}


class ApplyBlindTests(unittest.TestCase):
    ROWS = [
        {"id": "a", "human_label": "REPAIR", "label_status": AI_ASSISTED},
        {"id": "b", "human_label": "PASS", "label_status": AI_ASSISTED},
        {"id": "c", "human_label": "BLOCK", "label_status": "USER_SPECIFIED"},
        {"id": "d", "human_label": None, "label_status": "HUMAN_LABEL_REQUIRED"},
    ]

    def test_agreement_promotes_without_changing_the_label(self):
        payload = _payload(self.ROWS)
        promote_reviews.apply_blind(payload, {"a": "REPAIR"})
        case = payload["cases"][0]
        self.assertEqual(case["human_label"], "REPAIR")
        self.assertEqual(case["label_status"], promote_reviews.BLIND_CONFIRMED)
        self.assertNotIn("superseded_label", case)

    def test_disagreement_takes_the_blind_label_and_keeps_the_old_one(self):
        payload = _payload(self.ROWS)
        promote_reviews.apply_blind(payload, {"b": "REPAIR"})
        case = payload["cases"][1]
        self.assertEqual(case["human_label"], "REPAIR")
        self.assertEqual(case["superseded_label"], "PASS")
        self.assertEqual(case["label_status"], promote_reviews.BLIND_CORRECTED)

    def test_a_non_ai_assisted_row_is_never_overwritten(self):
        # blind 표본은 AI 보조 라벨에서만 뽑았다. 다른 상태가 섞였다면 표본 선정이
        # 어긋난 것이므로 조용히 덮으면 안 된다.
        payload = _payload(self.ROWS)
        changed = promote_reviews.apply_blind(payload, {"c": "PASS"})
        self.assertEqual(payload["cases"][2]["human_label"], "BLOCK")
        self.assertEqual(payload["cases"][2]["label_status"], "USER_SPECIFIED")
        self.assertEqual(changed.get("skipped_unexpected_status"), 1)

    def test_unsampled_rows_are_left_exactly_as_they_were(self):
        payload = _payload(self.ROWS)
        before = copy.deepcopy(payload)
        promote_reviews.apply_blind(payload, {"a": "REPAIR"})
        self.assertEqual(payload["cases"][1:], before["cases"][1:])

    def test_promotion_is_idempotent(self):
        payload = _payload(self.ROWS)
        promote_reviews.apply_blind(payload, {"b": "REPAIR"})
        after_first = copy.deepcopy(payload)
        promote_reviews.apply_blind(payload, {"b": "REPAIR"})
        self.assertEqual(payload, after_first)


class StratumRiskTests(unittest.TestCase):
    def test_a_fully_flipped_stratum_demands_a_relabel(self):
        payload = _payload([
            {"id": "p1", "human_label": "REPAIR", "superseded_label": "PASS",
             "label_status": promote_reviews.BLIND_CORRECTED},
            {"id": "p2", "human_label": "REPAIR", "superseded_label": "PASS",
             "label_status": promote_reviews.BLIND_CORRECTED},
            {"id": "p3", "human_label": "PASS", "label_status": AI_ASSISTED},
        ])
        risk = promote_reviews.stratum_risk(payload)
        self.assertEqual(risk["PASS"]["verdict"], "RELABEL_REQUIRED")
        # 표본에 안 든 나머지가 몇 건인지가 다음 작업량이다.
        self.assertEqual(risk["PASS"]["unsampled"], 1)

    def test_a_fully_confirmed_stratum_is_reported_as_confirmed(self):
        payload = _payload([
            {"id": "r1", "human_label": "REPAIR",
             "label_status": promote_reviews.BLIND_CONFIRMED},
            {"id": "r2", "human_label": "REPAIR",
             "label_status": promote_reviews.BLIND_CONFIRMED},
        ])
        self.assertEqual(
            promote_reviews.stratum_risk(payload)["REPAIR"]["verdict"], "CONFIRMED")

    def test_a_partially_shaken_stratum_is_not_called_confirmed(self):
        payload = _payload([
            {"id": "b1", "human_label": "BLOCK",
             "label_status": promote_reviews.BLIND_CONFIRMED},
            {"id": "b2", "human_label": "BLOCK", "superseded_label": "BLOCK",
             "label_status": promote_reviews.BLIND_CORRECTED},
            {"id": "b3", "human_label": "BLOCK", "label_status": AI_ASSISTED},
        ])
        risk = promote_reviews.stratum_risk(payload)
        self.assertEqual(risk["BLOCK"]["verdict"], "PARTIALLY_SHAKEN")

    def test_one_flip_alone_does_not_condemn_a_stratum(self):
        # 표본 1건이 뒤집힌 것은 층 전체의 성질이라는 근거가 못 된다.
        payload = _payload([
            {"id": "x", "human_label": "REPAIR", "superseded_label": "PASS",
             "label_status": promote_reviews.BLIND_CORRECTED},
            {"id": "y", "human_label": "PASS", "label_status": AI_ASSISTED},
        ])
        self.assertEqual(
            promote_reviews.stratum_risk(payload)["PASS"]["verdict"],
            "PARTIALLY_SHAKEN")

    def test_a_stratum_with_nothing_left_is_not_asked_to_relabel(self):
        # 뒤집혔어도 미표본이 0이면 재라벨할 대상 자체가 없다.
        payload = _payload([
            {"id": "z", "human_label": "BLOCK", "superseded_label": "REPAIR",
             "label_status": promote_reviews.BLIND_CORRECTED},
        ])
        self.assertEqual(
            promote_reviews.stratum_risk(payload)["REPAIR"]["verdict"],
            "RESOLVED_WITH_CORRECTIONS")

    def test_an_unsampled_stratum_is_unverified_not_confirmed(self):
        payload = _payload([{"id": "x", "human_label": "BLOCK",
                             "label_status": AI_ASSISTED}])
        self.assertEqual(
            promote_reviews.stratum_risk(payload)["BLOCK"]["verdict"], "UNVERIFIED")


class ApplyRelationsTests(unittest.TestCase):
    ROWS = [{"id": "i1", "human_label": "SEPARATE", "reason_code": "different_action"},
            {"id": "i2", "human_label": "MERGE", "reason_code": "same_action"}]

    def test_relations_are_added_without_touching_label_or_reason(self):
        payload = _payload(self.ROWS)
        promote_reviews.apply_relations(payload, {"i1": "FOLLOW_UP_NEW_ACTION"})
        case = payload["cases"][0]
        self.assertEqual(case["human_relation"], "FOLLOW_UP_NEW_ACTION")
        self.assertEqual(case["relation_status"], promote_reviews.RELATION_REVIEWED)
        # 하나의 정답지가 없으므로 라벨을 관계로 덮지 않는다.
        self.assertEqual(case["human_label"], "SEPARATE")
        self.assertEqual(case["reason_code"], "different_action")

    def test_an_unknown_relation_is_refused(self):
        with self.assertRaises(ValueError):
            promote_reviews.apply_relations(_payload(self.ROWS), {"i1": "NOT_A_RELATION"})

    def test_every_stored_relation_maps_to_both_contracts(self):
        relations = review_queue.load(("identity",))
        for relation in set(relations.values()):
            with self.subTest(relation=relation):
                self.assertIn(relation, review_queue.RELATION_TO_VERDICT)


if __name__ == "__main__":
    unittest.main()
