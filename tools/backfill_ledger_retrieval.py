"""원장의 옛 사건에 **검색용 칸**을 소급해 채운다.

왜 필요한가
-----------
`issue_ledger.catalog_rows` 에 검색 칸(엔티티·호기·국가·지문)을 더했지만, 그것은
**앞으로 쓰이는 사건에만** 붙는다. 원장에 이미 쌓인 798건은 칸이 비어 있다.

그 상태로 장기 검색을 돌리면 구조화 신호가 전부 0 이라 후보가 어휘로만 걸린다.
첫 실측에서 후보 2,204쌍 중 2,027쌍(92%)이 "공통 신원 신호 없음"으로 죽었고,
원인은 판정기가 아니라 **빈 칸**이었다.

무엇으로 채우는가
-----------------
`archive/*.jsonl` 이다. 원장은 사건마다 근거 기사 해시를 들고 있고, 아카이브는
그 해시의 원문을 들고 있다. 둘을 이으면 카탈로그를 다시 만들지 않고도 칸이 찬다.

빌드를 한 번 더 돌려 채우지 않는 이유: 그 방법은 **지금 창의 60일 카탈로그**만
건드린다. 7월 사건은 카탈로그에 없으므로 영영 빈 채로 남는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asset_alias  # noqa: E402
import entity_match  # noqa: E402
import issue_ledger  # noqa: E402


def load_archive_by_hash() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted((ROOT / "archive").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = str(row.get("hash") or "")
            if key:
                out[key] = row
    return out


def backfill(store: dict, articles: dict[str, dict], *, overwrite: bool) -> dict:
    registry = entity_match.load_entity_registry()
    alias_entries = entity_match._entity_alias_entries(registry)
    stats = Counter()
    for entry in (store.get("issues") or {}).values():
        stats["issues"] += 1
        if not overwrite and entry.get("units") is not None and entry.get("entity_ids"):
            stats["skipped"] += 1
            continue
        members = [articles[h] for h in (entry.get("hashes") or []) if h in articles]
        if not members:
            stats["no_articles"] += 1
            members = []
        text = " ".join(filter(None, [
            str(entry.get("title") or ""), str(entry.get("summary") or ""),
            *(str(row.get("title_kr") or row.get("title") or "") for row in members[:8]),
        ]))
        entity_ids, _evidence = entity_match.entity_ids_for_members(members, alias_entries) \
            if members else ([], [])
        days = sorted({str(row.get("article_date") or "")[:10]
                       for row in members if row.get("article_date")})
        countries: list[str] = []
        for row in members:
            for country in (row.get("countries") or []):
                value = str(country).strip()
                if value and value not in countries:
                    countries.append(value)
        units = sorted(asset_alias.unit_tokens(text))
        plants = sorted(asset_alias.plant_tokens(text))
        fingerprint = {}
        for row in members:
            candidate = row.get("story_fingerprint")
            if isinstance(candidate, dict) and candidate:
                fingerprint = candidate
                break

        entry["entity_ids"] = list(dict.fromkeys(
            [e for e in (entry.get("entity_ids") or []) if e] + list(entity_ids)
        ))[:issue_ledger._MAX_RETRIEVAL_TOKENS]
        entry["units"] = units[:issue_ledger._MAX_RETRIEVAL_TOKENS]
        entry["plants"] = plants[:issue_ledger._MAX_RETRIEVAL_TOKENS]
        entry["countries"] = countries[:6]
        if days:
            entry["evidence_days"] = days[:1] + days[-1:]
            entry["evidence_day_count"] = len(days)
        entry["facts"] = {
            "actors": issue_ledger._fingerprint_axis(
                fingerprint, ("actors", "actor", "operator", "organization")),
            "assets": issue_ledger._fingerprint_axis(
                fingerprint, ("assets", "asset", "facility", "project", "plant")),
            "event_family": issue_ledger._fingerprint_axis(
                fingerprint, ("event_family", "event_type", "event")),
            "action": issue_ledger._fingerprint_axis(
                fingerprint, ("action", "decision", "stage")),
        }
        stats["filled"] += 1
        stats["with_units"] += bool(units)
        stats["with_entities"] += bool(entry["entity_ids"])
        stats["with_facts"] += bool(any(entry["facts"].values()))
    return dict(stats)


def main() -> int:
    parser = argparse.ArgumentParser(description="원장 검색 칸 소급 채움")
    parser.add_argument("--path", type=Path, default=None)
    parser.add_argument("--overwrite", action="store_true",
                        help="이미 채워진 항목도 다시 계산한다")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = issue_ledger.load_store(args.path)
    articles = load_archive_by_hash()
    print(f"[backfill] 아카이브 기사 {len(articles)}건 · 원장 사건 {len(store['issues'])}건")
    stats = backfill(store, articles, overwrite=args.overwrite)
    print("[backfill] " + json.dumps(stats, ensure_ascii=False))
    if not args.dry_run:
        issue_ledger.save_store(store, args.path)
        print(f"[backfill] 저장 → {(args.path or issue_ledger.OUT_FILE).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
