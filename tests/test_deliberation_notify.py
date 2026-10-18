"""공론화 관리자 알림 — 사건 단위 1회·대응 필요 승격·야간 보류·첫 실행 무음·발송 실패 시 커서 유지."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import deliberation_lane as lane  # noqa: E402
import deliberation_notify as dn  # noqa: E402

DAY = datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)     # 12시 KST
NIGHT = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)  # 00시 KST


def rec(h, key, at, publisher="연합뉴스", **kw):
    return {"hash": h, "event_key": key, "archived_at": at, "publisher": publisher, "title": f"{key} {h}",
            "url": f"https://news.test/{h}", "significance": 2, "stage": "agenda_design", "stance": "neutral", **kw}


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.arc = self.tmp / "arc"
        self.arc.mkdir()
        self.state = self.tmp / "state.json"
        self.sent = []

    def write(self, rows):
        with (self.arc / "2026-10.jsonl").open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def go(self, now, sender=None):
        return dn.run(now, sender=sender or self.sent.append, archive_dir=self.arc, state_file=self.state)

    def test_flow(self):
        self.write([rec("old", "옛 사건", "2026-10-01T00:00:00+00:00")])
        self.go(DAY)                                   # 첫 실행: 조용히 커서만
        self.assertEqual(self.sent, [])
        self.write([rec(f"a{i}", "원전 공론화 의제 확정", "2026-10-16T03:10:00+00:00", publisher=f"매체{i}")
                    for i in range(5)])
        self.go(DAY.replace(hour=4))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("매체0 외 4곳", self.sent[0])
        self.assertNotIn("옛 사건", self.sent[0])
        # 같은 사건 추가 보도 → 이어진 보도 한 줄, 새 사건 0
        self.write([rec("a9", "원전 공론화 의제 확정", "2026-10-16T05:00:00+00:00")])
        self.go(DAY.replace(hour=6))
        self.assertIn("새 사건 0", self.sent[1])
        self.assertIn("의제 확정 +1", self.sent[1])
        # 칼럼(대응 필요) — 🔴 와 쟁점·준비
        self.write([rec("c1", "공론화 수요 전제 비판 칼럼", "2026-10-16T06:30:00+00:00", publisher="산경e뉴스",
                        response_needed=True, response_issue="전력수요 전망 고정", response_prep="2040 전망 근거",
                        significance=0)])
        self.go(DAY.replace(hour=7))
        self.assertIn("🔴", self.sent[2])
        self.assertIn("쟁점: 전력수요 전망 고정", self.sent[2])
        # 발송 실패 → 커서 유지, 다음 회차 재발송
        self.write([rec("d1", "새 사건", "2026-10-16T07:30:00+00:00")])
        with self.assertRaises(RuntimeError):
            self.go(DAY.replace(hour=8), sender=lambda t: (_ for _ in ()).throw(RuntimeError("tg")))
        self.go(DAY.replace(hour=9))
        self.assertIn("새 사건 d1", self.sent[3])
        # 야간 보류 → 아침에 묶어
        self.write([rec("n1", "밤 사건", "2026-10-16T14:00:00+00:00")])
        self.assertIn("야간 보류", self.go(NIGHT))
        self.go(NIGHT.replace(day=16, hour=23))      # 08시 KST
        self.assertIn("밤 사건", self.sent[4])

    def test_normalize_response(self):
        base = {"in_scope": True, "exclude_reason": "none"}
        yes = lane.normalize_judgment({**base, "response_needed": True, "response_issue": "x", "response_prep": "y"})
        self.assertEqual((yes["response_needed"], yes["response_issue"]), (True, "x"))
        no = lane.normalize_judgment({**base, "response_issue": "x"})
        self.assertEqual((no["response_needed"], no["response_issue"]), (False, ""))
        self.assertIn("response_needed", lane.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
