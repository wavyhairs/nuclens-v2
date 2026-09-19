"""`headline_display` 가 화면에 닿는 **유일한 길** — 그리고 신원은 따라오지 않는다.

카드 제목은 두 계층으로 갈라져 있다.

    issue.title             신원. dedup·event_stage·issue_continuity·
                            story_fingerprint·asset_alias·thread_judge 가 읽는다
    issue.headline_display  표시. Web·Telegram·briefing 만 읽는다

이 파일은 그 경계가 새지 않는지 본다. `tests/test_issue_headline.py` 가 생성
쪽(폴백·캐시·투자어휘 거부)을 맡고, 여기서는 **배선**을 맡는다.

이 검사가 root `tests/` 가 아니라 여기 있는 이유는 `test_why_short_card.py` 와
같다 — `build_data` 를 정상적으로 import 하는 자리가 여기다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_data as bd  # noqa: E402

APP_JS = ROOT / "public" / "app.js"


class HeadlineWiringTests(unittest.TestCase):
    def _rows(self):
        catalog = [
            {"issue_id": "i1", "title": "원안위, 오르비텍 핵연료물질 사용 허가 의결",
             "latest_change": "", "detail": "원안위가 허가했다"},
            {"issue_id": "i2", "title": "고리 3·4호기 계속운전 연내 결론",
             "latest_change": "심사 착수 → 연내 결론", "detail": ""},
        ]
        briefings = [{"issues": [dict(catalog[0])]}]
        return catalog, briefings

    def test_every_row_gets_a_headline(self):
        catalog, briefings = self._rows()
        with mock.patch.object(bd.issue_headline, "build",
                               return_value=({}, {"from_cache": 0, "asked": 0,
                                                  "calls": 0, "fell_back": 2,
                                                  "status": "no_api_key",
                                                  "reject_reasons": {}})):
            bd.apply_headline_display(catalog, briefings)
        for row in catalog + briefings[0]["issues"]:
            self.assertTrue(row["headline_display"],
                            f"{row['issue_id']} 의 제목 칸이 비었다")

    def test_the_canonical_title_is_never_touched(self):
        catalog, briefings = self._rows()
        before = [row["title"] for row in catalog]
        with mock.patch.object(bd.issue_headline, "build",
                               return_value=({"i1": "완전히 다른 표시 제목",
                                              "i2": "또 다른 표시 제목"},
                                             {"from_cache": 0, "asked": 2, "calls": 1,
                                              "fell_back": 0, "status": "ok",
                                              "reject_reasons": {}})):
            bd.apply_headline_display(catalog, briefings)
        self.assertEqual([row["title"] for row in catalog], before)
        self.assertEqual(catalog[0]["headline_display"], "완전히 다른 표시 제목")

    def test_briefing_rows_reuse_the_catalog_headline(self):
        """브리핑 행에서 또 묻지 않는다 — 같은 이슈를 날짜 수만큼 중복 질의하게 된다."""
        catalog, briefings = self._rows()
        calls = []

        def _fake(requests, **_kwargs):
            calls.append([row["issue_id"] for row in requests])
            return ({"i1": "표시 제목 하나", "i2": "표시 제목 둘"},
                    {"from_cache": 0, "asked": 2, "calls": 1, "fell_back": 0,
                     "status": "ok", "reject_reasons": {}})

        with mock.patch.object(bd.issue_headline, "build", side_effect=_fake):
            bd.apply_headline_display(catalog, briefings)
        self.assertEqual(calls, [["i1", "i2"]], "브리핑 행에서 또 물었다")
        self.assertEqual(briefings[0]["issues"][0]["headline_display"], "표시 제목 하나")

    def test_a_missing_headline_falls_back_to_the_title(self):
        catalog, briefings = self._rows()
        with mock.patch.object(bd.issue_headline, "build",
                               return_value=({"i1": ""},
                                             {"from_cache": 0, "asked": 0, "calls": 0,
                                              "fell_back": 2, "status": "ok",
                                              "reject_reasons": {}})):
            bd.apply_headline_display(catalog, briefings)
        self.assertEqual(catalog[0]["headline_display"], catalog[0]["title"])
        self.assertEqual(catalog[1]["headline_display"], catalog[1]["title"])


class DisplayRepairBoundaryTests(unittest.TestCase):
    """표시 수리는 두 칸에 **같이** 걸린다. 한쪽만 고치면 카드가 한 수리 뒤처진다."""

    def test_the_repair_pass_also_fixes_the_display_headline(self):
        payload = {"issue_id": "i1",
                   "title": "상업운전 앞둔 새울 3호기 시운전 중 자동정지 및 사업기간 연장",
                   "headline_display": "상업운전 앞둔 새울 3호기 시운전 중 자동정지 및 사업기간 연장"}
        bd.apply_display_headline_repairs(payload)
        self.assertEqual(payload["headline_display"], payload["title"],
                         "표시 제목이 수리를 못 받아 canonical 과 어긋났다")


class AppJsContractTests(unittest.TestCase):
    """화면이 그 칸을 실제로 읽는가. 배선은 조용히 빠진다."""

    def setUp(self):
        self.source = APP_JS.read_text(encoding="utf-8")

    def test_the_card_reads_the_display_headline(self):
        self.assertIn("issue.headline_display", self.source)
        self.assertIn("function issueHeadline(issue)", self.source)

    def test_the_card_title_button_does_not_read_the_canonical_title(self):
        for line in self.source.splitlines():
            if "issue-title-button" in line:
                self.assertNotIn("esc(issue.title)", line,
                                 f"카드 제목이 신원 칸을 읽는다: {line.strip()[:90]}")

    def test_the_flow_renders_only_judged_relations(self):
        """이음매의 말은 판정이 준 것뿐이다. 화면이 지어내지 않는다."""
        self.assertIn("step.relation_label", self.source)
        self.assertIn("function threadFlow(thread)", self.source)
        for invented in ('relation_label || "관련"', "relation_label || '관련'"):
            self.assertNotIn(invented, self.source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
