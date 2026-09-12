"""호기 신원이 **언어를 건너** 같은 이름인가.

`build_data._UNIT_RE` 는 `호기` 를 요구하고 `_FACILITY_NAMES` 25개가 전부 한글이라
영문 기사의 "Kori Unit 2" 는 호기 검사에 걸리지조차 않았다. 그 구멍을 이 모듈이
메운다 — 그러므로 검사도 **영문 쪽을 먼저** 본다.
"""

import unittest

import asset_alias


class UnitTokenTests(unittest.TestCase):
    def test_korean_and_english_produce_the_same_token(self):
        for text in ("고리 2호기 계속운전 신청", "고리2호기 심사 착수",
                     "Kori Unit 2 continued operation", "Kori 2 licence renewal",
                     "Kori-2 restart"):
            self.assertEqual(asset_alias.unit_tokens(text), {"kori-2"}, text)

    def test_multi_unit_runs_expand(self):
        self.assertEqual(asset_alias.unit_tokens("고리 3·4호기 계속운전 심사"),
                         {"kori-3", "kori-4"})
        self.assertEqual(asset_alias.unit_tokens("한빛 1, 2호기 내년 심사"),
                         {"hanbit-1", "hanbit-2"})
        self.assertEqual(asset_alias.unit_tokens("Hanbit 3 and 4 restart"),
                         {"hanbit-3", "hanbit-4"})
        self.assertEqual(asset_alias.unit_tokens("Barakah units 1 and 2"),
                         {"barakah-1", "barakah-2"})

    def test_longer_plant_name_wins(self):
        """'신고리 2호기' 가 `kori-2` 도 내놓으면 서로 다른 발전소가 한 이름이 된다."""
        self.assertEqual(asset_alias.unit_tokens("신고리 2호기"), {"shin-kori-2"})
        self.assertEqual(asset_alias.unit_tokens("Shin-Kori 2 reactor"), {"shin-kori-2"})

    def test_bare_numbers_do_not_become_units(self):
        self.assertEqual(asset_alias.unit_tokens("원전 10기 건설 승인"), set())
        self.assertEqual(asset_alias.unit_tokens("2026년 원전 정책"), set())
        self.assertEqual(asset_alias.unit_tokens("NRC Ginna 계속운전"), set())


class UnitConflictTests(unittest.TestCase):
    def test_same_plant_different_unit_conflicts_across_languages(self):
        self.assertTrue(asset_alias.conflict("고리 2호기 계속운전", "Kori Unit 3 outage"))
        self.assertTrue(asset_alias.conflict("한빛 3호기 정비", "한빛 4호기 정비"))

    def test_same_unit_does_not_conflict(self):
        self.assertFalse(asset_alias.conflict("고리 2호기 계속운전",
                                              "Kori Unit 2 continued operation"))

    def test_missing_unit_is_not_a_contradiction(self):
        """없는 것은 반대가 아니다 — `_cluster_fingerprint_conflict` 와 같은 규칙."""
        self.assertFalse(asset_alias.conflict("고리 2호기", "고리 원전 정비 계획"))
        self.assertFalse(asset_alias.conflict("원자력 정책", "Kori Unit 2"))

    def test_different_plants_are_not_a_unit_conflict(self):
        self.assertFalse(asset_alias.conflict("고리 2호기", "한빛 3호기"))

    def test_overlapping_unit_sets_do_not_conflict(self):
        self.assertFalse(asset_alias.conflict("고리 3·4호기 심사", "고리 4호기 승인"))


class PlantTokenTests(unittest.TestCase):
    def test_plant_level_is_the_wider_signal(self):
        self.assertIn("kori", asset_alias.plant_tokens("고리 원전 계속운전"))
        self.assertIn("wolsong", asset_alias.plant_tokens("Wolsong NPP outage"))


if __name__ == "__main__":
    unittest.main()
