"""발송됐는데 아카이브에 없는 기사(고아)를 찾고, 같은 기사의 아카이브 해시로 잇는다.

왜
--
같은 기사가 네이버 경로와 Google News 경로로 따로 들어오면 해시가 둘이 된다.
아카이브는 제목 완전일치로 첫 사본만 남기고, 발송은 등급 높은 두 번째 사본으로
나간다. 그러면 웹 빌드(해시 조인)가 그 기사를 '발송 안 됨'으로 보아 카드도 검색도
없다 (2026-10-09 월성 2~4호기 계속운전). 수집 단계 접기(news_bot.fold_cross_run_duplicates)
가 앞으로의 고아를 막고, 이 도구는 **과거** 고아를 `delivery_hash_aliases.json` 에
이어 붙인다. 빌드는 그 파일을 먼저 보고, 없으면 발송 기록의 원제목으로 잇는다.

쓰는 법
-------
    python tools/delivery_orphans.py                 # 고아 표만 출력
    python tools/delivery_orphans.py --write-aliases # 잇기 가능한 것을 별칭 파일에 합침
    python tools/delivery_orphans.py --since 2026-09-01

잇는 근거는 제목 완전일치뿐이다. 14일 캐시(curated.json)에 고아 해시의 원제목이
남아 있을 때만 이을 수 있다 — 캐시가 만료된 옛 고아는 '미복원' 으로 남는다.
외부 호출 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_quality import title_key  # noqa: E402

DELIVERY_LOG = ROOT / "delivery_log.jsonl"
ARCHIVE_DIR = ROOT / "archive"
CURATED = ROOT / "curated.json"
ALIASES = ROOT / "delivery_hash_aliases.json"


def load_archive_titles(archive_dir: Path = ARCHIVE_DIR) -> tuple[set[str], dict[str, str]]:
    hashes: set[str] = set()
    by_title: dict[str, str] = {}
    for path in sorted(archive_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            h = str(record.get("hash") or "")
            if not h:
                continue
            hashes.add(h)
            key = title_key(record.get("title"))
            if key:
                by_title.setdefault(key, h)
    return hashes, by_title


def load_delivery_rows(path: Path = DELIVERY_LOG, since: str = "") -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("record_type"):
            continue
        if not row.get("hash") or not row.get("date") or not row.get("title_kr"):
            continue
        if since and str(row["date"]) < since:
            continue
        rows.append(row)
    return rows


def find_orphans(rows: list[dict], archive_hashes: set[str], by_title: dict[str, str],
                 curated: dict, aliases: dict[str, str]) -> list[dict]:
    """고아 발송 행마다 {hash, date, title, target, how} — target 이 비면 미복원."""
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        h = str(row["hash"])
        if h in archive_hashes or h in seen:
            continue
        seen.add(h)
        cached = curated.get(h) if isinstance(curated.get(h), dict) else {}
        title = str(row.get("title") or cached.get("title") or "")
        target, how = "", ""
        if aliases.get(h) in archive_hashes:
            target, how = aliases[h], "alias"
        elif title and by_title.get(title_key(title)) in archive_hashes:
            target, how = by_title[title_key(title)], "title"
        link = str(cached.get("link") or "")
        out.append({"hash": h, "date": str(row.get("date") or ""),
                    "title": title or str(row.get("title_kr") or ""),
                    "target": target, "how": how,
                    "source": "google" if "news.google." in link else str(cached.get("domain") or "?")})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="발송 고아 찾기 / 별칭 잇기")
    parser.add_argument("--write-aliases", action="store_true")
    parser.add_argument("--since", default="")
    args = parser.parse_args(argv)

    archive_hashes, by_title = load_archive_titles()
    curated = json.loads(CURATED.read_text(encoding="utf-8")) if CURATED.exists() else {}
    existing: dict[str, str] = {}
    if ALIASES.exists():
        existing = (json.loads(ALIASES.read_text(encoding="utf-8")) or {}).get("aliases") or {}
    rows = load_delivery_rows(since=args.since)
    orphans = find_orphans(rows, archive_hashes, by_title, curated, existing)

    resolvable = [o for o in orphans if o["target"]]
    print(f"발송 {len(rows)}건 중 아카이브에 없는 해시 {len(orphans)}건 — "
          f"잇기 가능 {len(resolvable)} · 미복원 {len(orphans) - len(resolvable)}")
    for o in sorted(orphans, key=lambda o: o["date"]):
        mark = f"-> {o['target'][:8]} ({o['how']})" if o["target"] else "미복원"
        print(f"  {o['date']} {o['hash'][:8]} {o['source']:12s} {mark:22s} {o['title'][:48]}")

    if args.write_aliases:
        merged = dict(existing)
        added = 0
        for o in resolvable:
            if merged.get(o["hash"]) != o["target"]:
                merged[o["hash"]] = o["target"]
                added += 1
        ALIASES.write_text(json.dumps({
            "_comment": "발송 해시 -> 아카이브 해시. 같은 기사가 두 경로로 들어와 해시가 "
                        "갈린 과거 발송분을 잇는다. tools/delivery_orphans.py --write-aliases 가 채운다.",
            "aliases": dict(sorted(merged.items())),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"별칭 파일 갱신: +{added} -> {len(merged)}건 ({ALIASES.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
