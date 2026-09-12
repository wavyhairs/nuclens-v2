"""발송 이력으로 이슈 원장의 초기값을 만든다 (1회 실행).

왜 1회짜리 도구인가
-------------------
`issue_ledger` 는 카탈로그가 만들어질 때마다 스스로 갱신된다. 다만 원장을
오늘부터 시작하면 **이미 깨진 주소는 영영 404** 다 — 2026-09-12 실측으로
발행된 rss.xml 의 고유 이슈 링크 185건 중 33건이 이미 그 상태였다.

`delivery_log.jsonl` 에는 발송 항목 801건이 61일치(2026-07-14~09-12) 남아 있고,
각 행이 story_id 와 대표 기사 해시를 들고 있다. 그것만으로 "그 주소가 어떤
사건이었는지"는 되살릴 수 있다. 카탈로그의 정확한 재현이 아니라 **보관 페이지를
세울 최소한의 신원**을 채우는 것이 목적이다.

한 번 돌려 커밋하면 그 뒤로는 `build_data` 가 알아서 잇는다. 이미 원장에 있는
id 는 건드리지 않는다(빌드가 만든 값이 이력보다 정확하다).

    python tools/backfill_issue_ledger.py            # 미리보기
    python tools/backfill_issue_ledger.py --write    # 기록
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import issue_ledger  # noqa: E402

DELIVERY_LOG = ROOT / "delivery_log.jsonl"


def delivery_items(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        handle = path.open(encoding="utf-8")
    except OSError as exc:
        print(f"[backfill] 발송 이력을 열 수 없다: {exc}")
        return rows
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and not row.get("record_type") and row.get("hash"):
                rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="원장에 실제로 기록한다")
    parser.add_argument("--log", default=str(DELIVERY_LOG))
    args = parser.parse_args()

    items = delivery_items(Path(args.log))
    rows = issue_ledger.backfill_rows(items)
    store = issue_ledger.load_store()
    existing = set(store["issues"])
    fresh = [row for row in rows if row["issue_id"] not in existing]

    print(f"[backfill] 발송 항목 {len(items)}건 → 이슈 {len(rows)}건 "
          f"(원장에 이미 있는 것 {len(rows) - len(fresh)}건 제외) → 신규 {len(fresh)}건")
    for row in sorted(fresh, key=lambda r: r["first_seen"])[:8]:
        print(f"    {row['first_seen']} {row['issue_id']}  {row['title'][:44]}")
    if not args.write:
        print("[backfill] 미리보기다. 기록하려면 --write 를 붙인다.")
        return 0

    day = issue_ledger._today()
    for row in fresh:
        store["issues"][row["issue_id"]] = {
            **row,
            "revisions": [{"date": row["first_seen"] or day,
                           "title": row["title"], "summary": row["summary"]}],
            "moved_to": "",
        }
    issue_ledger.save_store(store)
    print(f"[backfill] 기록 완료 — 원장 누적 {len(store['issues'])}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
