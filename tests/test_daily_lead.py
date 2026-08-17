"""daily_lead.py 단위 테스트 — 항상-쓰기 계약·재시도 사다리·절 경계 절단.

핵심 계약: generate()가 어떤 경로로 끝나든 daily_leads.json 은 존재해야 한다.
파일 부재는 daily-brief.yml 의 git add 를 죽이고 trend_insights 커밋까지
동반 사망시킨다 (2026-08-02 실사고).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import daily_lead
from gemini_client import GeminiError


# 길이 사다리를 시험하는 문장이지 환각을 시험하는 문장이 아니다 — 등장하는
# 기관·국가·수치는 아래 _TITLES 안에 실제로 있어야 한다. 그렇지 않으면 근거
# 게이트가 먼저 잡아 길이 경로에 도달하지 못한다.
LONG_LEAD = (
    "국내에서는 한수원이 영덕군과 신규 원전 건설 협력에 합의했고, 해외에서는 헝가리 "
    "원전이 가뭄으로 가동을 중단한 가운데 중국 정부가 신규 원전 8기 건설을 승인했습니다"
)


# 제목은 실제와 비슷해야 한다 — "이슈 0" 같은 제목으로는 어떤 종합 문장도
# 구체성 검사를 통과할 수 없어(공유할 낱말이 없다) 테스트가 게이트를 잘못 잡는다.
_TITLES = [
    "한수원, 영덕군과 신규 원전 건설 협력 합의",
    "헝가리 원전, 가뭄으로 가동 중단",
    "중국 정부, 신규 원전 8기 건설 승인",
    "미국 NRC, 환경심사 규정 개정안 발표",
]


def delivery_row(idx=0, date="2026-08-02"):
    return {
        "date": date,
        "hash": f"hash{idx}",
        "title_kr": _TITLES[idx % len(_TITLES)],
        "score": 10 - idx,
        "region": "국내" if idx % 2 == 0 else "해외",
    }


class DailyLeadTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._orig = (daily_lead.BASE, daily_lead.OUT_FILE, daily_lead.DELIVERY_LOG)
        daily_lead.BASE = base
        daily_lead.OUT_FILE = base / "daily_leads.json"
        daily_lead.DELIVERY_LOG = base / "delivery_log.jsonl"
        self.addCleanup(self._restore)
        self.calls = []
        self.responses = []
        self.available = True
        self.raises = False
        self._orig_gemini = (daily_lead.is_available, daily_lead.call_json)
        daily_lead.is_available = lambda: self.available
        daily_lead.call_json = self._fake_call

    def _restore(self):
        daily_lead.BASE, daily_lead.OUT_FILE, daily_lead.DELIVERY_LOG = self._orig
        daily_lead.is_available, daily_lead.call_json = self._orig_gemini

    def _fake_call(self, system_prompt, user_message, **kwargs):
        self.calls.append(user_message)
        if self.raises:
            raise GeminiError("429")
        return self.responses.pop(0) if self.responses else {"lead": ""}

    def write_log(self, rows):
        daily_lead.DELIVERY_LOG.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
            encoding="utf-8",
        )

    def read_leads(self):
        return json.loads(daily_lead.OUT_FILE.read_text(encoding="utf-8"))["leads"]

    def seed_existing(self):
        daily_lead.OUT_FILE.write_text(
            json.dumps({"leads": {"2026-08-01": {"lead": "기존 문장.", "evidence": []}}},
                       ensure_ascii=False),
            encoding="utf-8",
        )

    # ── 항상-쓰기 계약 ──────────────────────────────────────────────

    def test_no_api_key_still_writes_file(self):
        self.available = False
        self.assertFalse(daily_lead.generate())
        self.assertTrue(daily_lead.OUT_FILE.exists())
        self.assertEqual(self.read_leads(), {})

    def test_no_delivery_rows_still_writes_file(self):
        self.assertFalse(daily_lead.generate())
        self.assertTrue(daily_lead.OUT_FILE.exists())

    def test_gemini_error_preserves_existing_leads(self):
        self.seed_existing()
        self.write_log([delivery_row(0)])
        self.raises = True
        self.assertFalse(daily_lead.generate())
        self.assertEqual(self.read_leads()["2026-08-01"]["lead"], "기존 문장.")

    def test_empty_lead_preserves_existing_and_writes(self):
        self.seed_existing()
        self.write_log([delivery_row(0)])
        self.responses = [{"lead": ""}]
        self.assertFalse(daily_lead.generate())
        leads = self.read_leads()
        self.assertIn("2026-08-01", leads)
        self.assertNotIn("2026-08-02", leads)

    # ── 성공 경로 ──────────────────────────────────────────────────

    def test_success_saves_lead_with_evidence(self):
        self.write_log([delivery_row(0), delivery_row(1)])
        self.responses = [{"lead": "한수원이 영덕군과 신규 원전 건설에 합의한 가운데 헝가리는 가뭄으로 가동을 중단했습니다", "evidence_idx": [0, 1, 99]}]
        self.assertTrue(daily_lead.generate())
        entry = self.read_leads()["2026-08-02"]
        self.assertIn("영덕군", entry["lead"])
        self.assertEqual([e["hash"] for e in entry["evidence"]], ["hash0", "hash1"])
        self.assertNotIn("truncated", entry)

    def test_old_leads_pruned_after_keep_days(self):
        daily_lead.OUT_FILE.write_text(
            json.dumps({"leads": {"2020-01-01": {"lead": "옛 문장."}}}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.write_log([delivery_row(0)])
        self.responses = [{"lead": "한수원이 영덕군과 신규 원전 건설에 합의했습니다", "evidence_idx": [0]}]
        self.assertTrue(daily_lead.generate())
        self.assertNotIn("2020-01-01", self.read_leads())

    # ── 재시도 사다리 ──────────────────────────────────────────────

    def test_overlength_retries_once_and_uses_short(self):
        self.write_log([delivery_row(0)])
        self.responses = [{"lead": LONG_LEAD, "evidence_idx": [0]},
                          {"lead": "한수원이 영덕군과 신규 원전 건설에 합의했습니다", "evidence_idx": [0]}]
        self.assertTrue(daily_lead.generate())
        entry = self.read_leads()["2026-08-02"]
        self.assertIn("영덕군", entry["lead"])
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("truncated", entry)

    def test_overlength_twice_falls_back_to_clause_cut(self):
        # 절단 결과가 어느 절에서 끊기든 근거가 남아 있도록 관련 기사를 모두 싣는다.
        self.write_log([delivery_row(i) for i in range(4)])
        self.responses = [{"lead": LONG_LEAD, "evidence_idx": [0]},
                          {"lead": LONG_LEAD, "evidence_idx": [0]}]
        self.assertTrue(daily_lead.generate())
        entry = self.read_leads()["2026-08-02"]
        self.assertTrue(entry.get("truncated"))
        self.assertLessEqual(len(entry["lead"]), daily_lead.LEAD_LIMIT + 1)
        self.assertTrue(entry["lead"])

    # ── 공허한 종합 문장 차단 ─────────────────────────────────

    def test_vacuous_lead_is_detected(self):
        items = [{"hash": "a", "title_kr": "중국 정부, 신규 원전 8기 건설 승인"},
                 {"hash": "b", "title_kr": "헝가리 원전, 가뭄으로 가동 중단"}]
        vacuous = "국내외에서 원자력 및 에너지 정책과 현실에 대한 다양한 논의와 상황 변화가 있었습니다."
        concrete = "중국이 신규 원전 8기를 승인한 가운데 헝가리는 가뭄으로 가동을 중단했습니다."
        self.assertFalse(daily_lead.is_substantive(vacuous, items, {}))
        self.assertTrue(daily_lead.is_substantive(concrete, items, {}))

    def test_vacuous_first_answer_triggers_a_retry(self):
        self.write_log([delivery_row(0), delivery_row(1)])
        vacuous = "국내외에서 다양한 논의와 상황 변화가 있었습니다"
        self.responses = [{"lead": vacuous, "evidence_idx": [0]},
                          {"lead": "한수원이 영덕군과 신규 원전 건설에 합의했습니다", "evidence_idx": [0]}]
        self.assertTrue(daily_lead.generate())
        self.assertEqual(len(self.calls), 2, "공허하면 한 번 더 물어야 한다")
        self.assertIn("영덕군", self.read_leads()["2026-08-02"]["lead"])

    def test_twice_vacuous_saves_nothing_so_web_uses_the_title(self):
        self.write_log([delivery_row(0)])
        vacuous = "국내외에서 다양한 논의와 상황 변화가 있었습니다"
        self.responses = [{"lead": vacuous, "evidence_idx": [0]},
                          {"lead": vacuous, "evidence_idx": [0]}]
        self.assertFalse(daily_lead.generate())
        self.assertNotIn("2026-08-02", self.read_leads())

    # ── 근거 밖 사실 차단 ────────────────────────────────────
    #
    # is_substantive 는 낱말이 겹치는지만 본다. 근거 제목의 낱말을 쓰면서 없는
    # 기관·수치·날짜를 끼워 넣은 문장은 그 검사를 그대로 통과하고, 히어로 한 줄은
    # 사이트에서 가장 눈에 띄는 자리라 그게 그대로 사고가 된다.

    def test_lead_naming_an_absent_company_is_repaired(self):
        self.write_log([delivery_row(0), delivery_row(2)])
        bad = "한수원과 웨스팅하우스가 영덕군 신규 원전 건설에 합의했습니다"
        good = "한수원이 영덕군과 신규 원전 건설에 합의했습니다"
        self.responses = [{"lead": bad, "evidence_idx": [0]},
                          {"lead": good, "evidence_idx": [0]}]
        self.assertTrue(daily_lead.generate())
        entry = self.read_leads()["2026-08-02"]
        self.assertEqual(entry["lead"], good + ".")
        self.assertNotIn("웨스팅하우스", entry["lead"])
        self.assertEqual(len(self.calls), 2)

    def test_lead_with_an_invented_number_is_not_saved_when_repair_fails(self):
        """웹 히어로는 lead 가 없으면 이슈 제목으로 돌아간다 — 그쪽이 낫다."""
        self.write_log([delivery_row(0)])
        bad = "한수원이 영덕군에 신규 원전 12기 건설을 확정했습니다"
        self.responses = [{"lead": bad, "evidence_idx": [0]},
                          {"lead": bad, "evidence_idx": [0]}]
        self.assertFalse(daily_lead.generate())
        self.assertNotIn("2026-08-02", self.read_leads())

    def test_facts_present_in_the_day_are_not_flagged(self):
        items = [delivery_row(0), delivery_row(2)]
        self.assertEqual(daily_lead.unsupported_lead_facts(
            "한수원이 영덕군과 합의한 가운데 중국은 신규 원전 8기를 승인했습니다.",
            items, {}, "2026-08-02"), {})

    def test_repair_result_is_checked_again_before_saving(self):
        """마지막 변환 뒤에 다시 본다 — 재요청 답이 새 사실을 넣을 수도 있다."""
        self.write_log([delivery_row(0)])
        self.responses = [
            {"lead": "한수원과 웨스팅하우스가 영덕군 원전에 합의했습니다", "evidence_idx": [0]},
            {"lead": "한수원이 영덕군에 원전 500MW 건설을 합의했습니다", "evidence_idx": [0]},
        ]
        self.assertFalse(daily_lead.generate())
        self.assertNotIn("2026-08-02", self.read_leads())

    def test_clause_cut_prefers_boundary(self):
        cut = daily_lead._clause_cut(LONG_LEAD)
        self.assertLessEqual(len(cut), daily_lead.LEAD_LIMIT + 1)
        # 절 경계("…했고, ")에서 끊고, 뒤쪽 절은 통째로 버린다.
        self.assertIn("영덕군과 신규 원전 건설 협력에 합의했고", cut)
        self.assertNotIn("헝가리", cut)

    def test_clause_cut_without_boundary_uses_ellipsis(self):
        text = "가" * 120
        cut = daily_lead._clause_cut(text)
        self.assertTrue(cut.endswith("…"))
        self.assertLessEqual(len(cut), daily_lead.LEAD_LIMIT)


if __name__ == "__main__":
    unittest.main(verbosity=1)
