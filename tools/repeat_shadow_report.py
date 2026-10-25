"""재발송 그림자 판정 보고 — `repeat_shadow_log.jsonl` 을 날짜별로 센다. LLM 0회.

    python tools/repeat_shadow_report.py                 # 전체 기간
    python tools/repeat_shadow_report.py --since 2026-10-26
    python tools/repeat_shadow_report.py --rows          # 뺐을·내렸을 카드 제목까지

읽는 법
-------
- ``재발송률`` = drop / (판정된 카드). 그림자가 '새 사실 0'이라 본 카드의 비율이다.
  회귀셋 기준선(9/26~10/25 사람 판정)은 10.5%(497건 중 52건).
- ``1번 재탕`` = 1번 자리 카드가 drop 인 날 수. 기준선은 30일에 10번.
- ``error·skipped`` 가 늘면 판정이 측정이 아니라 결측이다 — 비율보다 먼저 본다.
전환 판단은 이 표와 `tools/repeat_send_replay.py` 의 회귀셋 대조를 함께 본다.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "repeat_shadow_log.jsonl"


def load(path: Path = LOG, since: str = "") -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and str(rec.get("date") or "") >= since:
            out.append(rec)
    return out


def summarize(records: list[dict]) -> dict:
    by_day: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    lead_drop: set[str] = set()
    for rec in records:
        day = str(rec.get("date") or "")
        for row in rec.get("rows") or ():
            by_day[day][row.get("verdict") or "?"] += 1
        if rec.get("lead_verdict") == "drop":
            lead_drop.add(day)
    total = collections.Counter()
    for counts in by_day.values():
        total.update(counts)
    judged = sum(total.values())
    return {"days": dict(by_day), "total": dict(total), "judged": judged,
            "drop_rate": round(total["drop"] / judged, 3) if judged else None,
            "lead_drop_days": sorted(lead_drop)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default="")
    parser.add_argument("--rows", action="store_true")
    args = parser.parse_args()
    records = load(since=args.since)
    if not records:
        print("기록이 없습니다 — repeat_shadow_log.jsonl 이 비었거나 기간에 해당 없음")
        return 0
    s = summarize(records)
    print(f"카드 {s['judged']}건 · 재발송률(drop) {s['drop_rate']} · 1번 재탕 {len(s['lead_drop_days'])}일")
    print(f"판정 합계: {s['total']}")
    for day in sorted(s["days"]):
        print(f"  {day}  {dict(s['days'][day])}")
    if args.rows:
        for rec in records:
            for row in rec.get("rows") or ():
                if row.get("verdict") in ("drop", "late", "error"):
                    told = ", ".join(f"{t['date']} {t['title'][:24]}" for t in (row.get("told") or [])[:3])
                    print(f"  {rec['date']} {rec['region']} #{row.get('position')} {row['verdict']:<6} "
                          f"{row.get('title', '')[:40]}  ← {told}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
