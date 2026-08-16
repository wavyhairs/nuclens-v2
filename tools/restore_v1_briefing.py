"""v1(nuclens.pages.dev)에만 남은 회차를 v2 delivery_log 로 되살린다.

왜 필요한가
-----------
v2 저장소를 2026-08-15 에 새로 만들면서 **그날 발송분의 delivery_log 레코드가
넘어오지 않았다.** 기사 자체는 archive 에 전부 있는데(17건 hash 전량 일치)
발송 기록이 없어서 web/build_data 가 그날을 briefing 으로 세지 못한다 —
'브리핑 날짜는 발송된 기사에서만 나온다'(build_briefings)는 계약 때문이다.
그래서 v2 는 08-15 가 통째로 빠진 채 08-16 이 제29호가 됐다(v1 은 제30호).

출처가 published data 인 이유
-----------------------------
v1 저장소는 남아 있지 않다 — GitHub(wavyhairs)에도, 로컬 zip 넷 중 어디에도
08-15 delivery_log 레코드가 없다(전부 그 이전에 묶인 것). 남은 유일한 사본이
v1 이 배포한 `data/news.json` 이다.

되살리는 것과 안 되살리는 것
---------------------------
  · 되살림 — hash / 발송일 / selection_score / selection_reasons / report_pick*
  · 비움  — breakdown(점수 내역), story_* 계약

**breakdown 을 지어내지 않는다.** '왜 이 기사가 뽑혔지 → breakdown 을 본다'가
README 가 약속한 확인 경로다. 그 자리에 만들어 낸 숫자를 넣으면 그 설명 전체가
못 믿을 것이 된다. 대신 v1 이 이미 계산해 배포한 `selection_reasons` 를 그대로
싣는다(지어낸 값이 아니라 그날 실제로 표시된 문구다).

story_* 는 2026-08-16 에 들어온 계약이라 이 회차의 이웃(08-14 이전 338건)도
전부 비어 있다 — 08-15 만 비는 게 아니라 그 시기 전체가 그렇다.

사용
----
    python tools/restore_v1_briefing.py --date 2026-08-15            # 미리보기
    python tools/restore_v1_briefing.py --date 2026-08-15 --apply    # 적용
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DELIVERY_LOG = ROOT / "delivery_log.jsonl"
ARCHIVE_DIR = ROOT / "archive"
DEFAULT_SOURCE = "https://nuclens.pages.dev"
# Cloudflare 가 기본 urllib UA 를 403 으로 막는다.
UA = {"User-Agent": "nuclens-restore/1.0"}


def fetch_json(base: str, name: str) -> object:
    if base.startswith(("http://", "https://")):
        url = f"{base.rstrip('/')}/data/{name}?cb={int(datetime.now().timestamp())}"
        request = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    return json.loads((Path(base) / name).read_text(encoding="utf-8"))


def archive_hashes() -> set[str]:
    found: set[str] = set()
    for path in sorted(ARCHIVE_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("hash"):
                found.add(row["hash"])
    return found


def delivered_dates() -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in delivery_records():
        date = row.get("date")
        if date:
            counts[date] = counts.get(date, 0) + 1
    return counts


def delivery_records() -> list[dict]:
    if not DELIVERY_LOG.exists():
        return []
    rows = []
    for line in DELIVERY_LOG.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("record_type") or not row.get("hash"):
            continue
        rows.append(row)
    return rows


def already_delivered(exclude_date: str) -> dict[str, str]:
    """hash → 이미 기록된 발송일 (복원 대상 날짜는 뺀다)."""
    return {row["hash"]: row.get("date", "")
            for row in delivery_records() if row.get("date") != exclude_date}


def build_records(news: list[dict], date: str, source: str) -> list[dict]:
    """v1 news.json 의 그날 발송분 → delivery_log 기사 레코드."""
    stamp = datetime.now(timezone.utc).isoformat()
    records = []
    for row in news:
        if row.get("briefing_date") != date or not row.get("hash"):
            continue
        record = {
            "hash": row["hash"],
            "date": date,
            "title_kr": row.get("title_kr") or row.get("title") or "",
            "domain": row.get("domain") or "",
            "section": row.get("section") or "",
            "region": row.get("region") or "",
            "score": row.get("selection_score"),
            "report_pick": row.get("report_pick") or "",
            "report_pick_why": row.get("report_pick_why") or "",
            "report_pick_angles": row.get("report_pick_angles") or [],
            # v1 이 그날 실제로 화면에 띄운 문구. breakdown 이 없으므로 build_data 가
            # 이 값을 그대로 쓴다(selection_reasons).
            "selection_reasons": row.get("selection_reasons") or [],
            # 이 줄이 원본 발송 기록이 아니라 복원본임을 남긴다. 없으면 다음 사람이
            # breakdown 이 왜 비었는지 알 방법이 없다.
            "restored_from": source,
            "restored_at": stamp,
        }
        records.append(record)
    return records


def build_stats(briefings: list[dict], date: str, source: str) -> dict | None:
    """그날 선정 통계. v1 은 지역별 분해 없이 합계만 배포한다.

    build_data.selection_view 가 domestic+overseas 를 **합쳐서만** 읽으므로
    합계를 한쪽에 실어도 화면에 나가는 값은 정확하다. 지역별로 쪼개는 것은
    추측이 되므로 하지 않는다.
    """
    row = next((r for r in briefings if r.get("date") == date), None)
    if not row or row.get("candidate_count") is None:
        return None
    return {
        "record_type": "selection_stats",
        "date": date,
        "generated_at": row.get("pipeline_ran_at") or f"{date}T07:30:00+09:00",
        "pipeline_status": row.get("pipeline_status") or "ok",
        "domestic": {
            "candidate_count": int(row.get("candidate_count") or 0),
            "selected_count": int(row.get("issue_count") or 0),
            "below_floor_count": int(row.get("below_floor_count") or 0),
        },
        "overseas": {"candidate_count": 0, "selected_count": 0, "below_floor_count": 0},
        "restored_from": source,
        "restored_note": "v1 published data 에는 지역별 분해가 없어 합계를 domestic 에 실었다",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="복원할 브리핑 날짜 (YYYY-MM-DD)")
    parser.add_argument("--source", default=DEFAULT_SOURCE,
                        help="v1 사이트 URL 또는 data/ 를 담은 로컬 경로")
    parser.add_argument("--apply", action="store_true", help="delivery_log 에 실제로 덧붙인다")
    args = parser.parse_args()

    existing = delivered_dates()
    if existing.get(args.date):
        print(f"[restore] {args.date} 는 이미 기사 레코드 {existing[args.date]}건이 있다 — 중단."
              f" 덮어쓰면 중복 발송 이력이 된다.")
        return 1

    news = fetch_json(args.source, "news.json")
    briefings = fetch_json(args.source, "briefings.json")
    if not isinstance(news, list) or not isinstance(briefings, list):
        print("[restore] 원본 형식이 예상과 다르다 — 중단")
        return 1

    records = build_records(news, args.date, args.source)
    if not records:
        print(f"[restore] 원본에 {args.date} 발송 기사가 없다 — 중단")
        return 1

    known = archive_hashes()
    missing = [r["hash"] for r in records if r["hash"] not in known]
    if missing:
        # 없는 기사에 발송 기록만 붙이면 build_data 가 조인에 실패해 그날이 다시
        # 비거나, 더 나쁘게는 제목 없는 카드가 뜬다.
        print(f"[restore] archive 에 없는 기사 {len(missing)}건 — 중단: {missing[:5]}")
        return 1

    # v2 가 이미 다른 날 내보낸 기사는 건드리지 않는다.
    #
    # v2 에 08-15 가 없었으므로 그날 v1 이 보낸 기사 중 일부는 큐에 남아 **08-16 에
    # v2 로 실제 발송됐다**. load_deliveries 는 hash 당 마지막 레코드를 쓰므로,
    # 뒤에 붙는 복원본이 그 기사들을 08-15 로 되돌려 놓는다 — 오늘 텔레그램으로
    # 나간 브리핑에서 9건이 사라지고 어제로 옮겨 간다. 실측으로 확인했다.
    #
    # 두 타임라인 다 사실이지만 v2 에서 참인 것은 v2 가 보낸 날짜다. 겹치는
    # 기사는 건너뛴다 — 기사가 사이트에서 사라지는 게 아니라 실제로 나간 날에
    # 남는다.
    taken = already_delivered(args.date)
    overlap = [r for r in records if r["hash"] in taken]
    records = [r for r in records if r["hash"] not in taken]
    if not records:
        print(f"[restore] {args.date} 발송분이 전부 다른 날짜로 이미 기록돼 있다 — 중단")
        return 1

    stats = build_stats(briefings, args.date, args.source)
    scored = sum(1 for r in records if r["score"] is not None)
    reasoned = sum(1 for r in records if r["selection_reasons"])
    print(f"[restore] {args.date} — 기사 {len(records)}건 (archive 전량 일치)")
    if overlap:
        dates = sorted({taken[r['hash']] for r in overlap})
        print(f"[restore]   건너뜀 {len(overlap)}건 — v2 가 이미 {', '.join(dates)} 에 발송함")
    print(f"[restore]   selection_score {scored}건 / selection_reasons {reasoned}건")
    print(f"[restore]   selection_stats {'복원' if stats else '없음(원본에도 없음)'}")
    print(f"[restore]   breakdown·story_* 는 비운다 — 원본에 없다(지어내지 않는다)")
    for record in records[:3]:
        print(f"[restore]   · {record['hash']} {record['title_kr'][:40]}")

    if not args.apply:
        print("[restore] 미리보기만 했다. 적용하려면 --apply")
        return 0

    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    if stats:
        lines.append(json.dumps(stats, ensure_ascii=False))
    with DELIVERY_LOG.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"[restore] delivery_log.jsonl 에 {len(lines)}줄 추가")
    return 0


if __name__ == "__main__":
    sys.exit(main())
