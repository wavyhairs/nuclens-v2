"""원문 대조 격리 재검(그림자 모드) 기록을 사람이 검토할 모양으로 뽑는다.

    python tools/integrity_shadow_report.py              # 최근 7일
    python tools/integrity_shadow_report.py --days 14

판정을 격리에 반영할지 정하려면 두 가지를 봐야 한다.

* 검사기가 '원문과 맞음'이라 한 것(would_release)이 정말 게이트 오탐인가 —
  틀린 요약을 내보내게 되는 쪽이라 하나씩 본다. 걸린 구절과 원문 제목을 같이 찍는다.
* '원문과 다름·없음'(would_repair·would_strip)의 인용이 원문에 실제로 있는가 —
  재생성 메모로 쓸 근거다.

기사마다 가장 최근 판정 한 줄만 센다(같은 기사를 회차마다 다시 물을 수 있다).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import integrity_shadow  # noqa: E402
import summary_verify  # noqa: E402


def latest_rows(rows: list[dict], since: str) -> list[dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        if not row.get("called") or str(row.get("checked_at") or "") < since:
            continue
        current = latest.get(row.get("hash"))
        if current is None or str(row.get("checked_at")) >= str(current.get("checked_at")):
            latest[row.get("hash")] = row
    return sorted(latest.values(), key=lambda row: str(row.get("checked_at")))


def render(rows: list[dict], *, days: int) -> list[str]:
    decisions = Counter(integrity_shadow.decision(row) for row in rows)
    sources = Counter(str(row.get("source_kind") or "?") for row in rows)
    lines = [
        f"원문 대조 격리 재검 — 최근 {days}일 · 기사 {len(rows)}건",
        "  판정: " + " · ".join(f"{integrity_shadow.DECISION_LABELS.get(key, key)} {count}"
                              for key, count in decisions.most_common()),
        "  원문: " + " · ".join(f"{key} {count}" for key, count in sources.most_common()),
    ]
    for key in ("would_release", "would_repair", "would_strip", "uncertain"):
        group = [row for row in rows if integrity_shadow.decision(row) == key]
        if not group:
            continue
        lines.append("")
        lines.append(f"[{integrity_shadow.DECISION_LABELS[key]}] {len(group)}건")
        for row in group:
            lines.append(f"- {row.get('hash')} 「{row.get('title_kr')}」")
            lines.append(f"    원문 제목: {row.get('source_title') or '-'}"
                         f" ({row.get('source_kind') or '?'})")
            concerns = " / ".join(row.get("concerns") or ()) or "-"
            lines.append(f"    게이트가 건 것: {concerns}")
            if key != "would_release":
                lines.append(f"    검사기: [{row.get('field') or '-'}] '{row.get('claim') or ''}'"
                             f" ↔ 원문 '{row.get('source_quote') or ''}'"
                             f" (인용 확인 {'O' if row.get('quote_in_source') else 'X'})"
                             f" — {row.get('reason') or ''}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--path", type=Path, default=integrity_shadow.LOG_FILE)
    args = parser.parse_args(argv)
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat(
        timespec="seconds")
    rows = latest_rows(summary_verify.load_log(args.path), since)
    print("\n".join(render(rows, days=args.days)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
