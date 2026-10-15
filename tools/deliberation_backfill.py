"""신규원전 공론화 레인 백필 — 0단계 gold 를 레인 DB 로 옮긴다(멱등).

    python tools/deliberation_backfill.py            # deliberation_archive/ · deliberation_state.json 갱신
    python tools/deliberation_backfill.py --dry-run

무엇을 넣나
  1. gold 포함 140건(메인 아카이브 2026-09-01~10-15) — 판정자 ``manual-claude``. 사실 문장은 메인
     큐레이션 것, 분류(국면·주체·입장·사건 이름)는 손라벨.
  2. 그 대표 기사에 **접혀 있던 기사** — 발송 기록(delivery_log 의 ``story_raw_sources``)과 지금 큐
     (digest_queue 의 ``raw_sources``)에 남은 것. 판정자 ``inherited``. 아카이브에는 대표 한 줄만 남아
     「공론화위 출범」 같은 사건이 매체 1곳으로 보이던 것을 실제 매체 수에 가깝게 되돌린다.
  3. gold 제외 34건과 위 기사 전부를 ``deliberation_state.json`` 의 판정 이력에 적는다 — 운영 레인이
     최근 3일 창에서 같은 기사를 다시 묻지 않게.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import deliberation_lane as lane  # noqa: E402
import deliberation_pipeline as pipe  # noqa: E402

GOLD = BASE / "eval_artifacts" / "deliberation" / "gold.jsonl"


def _jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def main_archive(hashes: set[str]) -> dict[str, dict]:
    rows = {}
    for path in sorted((BASE / "archive").glob("*.jsonl")):
        for row in _jsonl(path):
            if row.get("hash") in hashes:
                rows.setdefault(row["hash"], row)
    return rows


def _day(stamp: object) -> str:
    return str(stamp or "")[:10]


def _closest(raw: dict, owners: list[str], rep_day: dict[str, str]) -> str:
    """발송 기록의 사건 묶음은 며칠에 걸친다(「출범」 묶음에 9일 「1차 회의」 기사가 함께 있다).
    접힌 기사는 묶음 안에서 **날짜가 가장 가까운** gold 대표에게 붙인다."""
    day = _day(raw.get("pub")) or rep_day[owners[0]]
    distance = lambda h: abs((datetime.fromisoformat(rep_day[h]) - datetime.fromisoformat(day)).days)  # noqa: E731
    try:
        return min(owners, key=lambda h: (distance(h), owners.index(h)))
    except ValueError:
        return owners[0]


def folded_sources(gold_in: set[str], rep_day: dict[str, str]) -> dict[str, list[dict]]:
    """대표 hash → 접힌 기사 목록. 발송 기록이 먼저, 큐가 나중(같은 hash 는 한 번만).

    발송 기록의 묶음은 수집 단계 접기보다 넓다 — 제목이 프리필터를 넘는 기사만 받는다
    (실측: 「공론화위원회 출범」 묶음에 「기후부 출범 1년」·「전기본 위원 명단 공방」이 섞여 있었다).
    """
    out: dict[str, list[dict]] = {}
    for row in _jsonl(BASE / "delivery_log.jsonl"):
        raws = row.get("story_raw_sources")
        if not isinstance(raws, list) or not raws:
            continue
        owners = [h for h in dict.fromkeys([row.get("hash")] + list(row.get("story_article_hashes") or []))
                  if h in gold_in]
        if not owners:
            continue
        for raw in raws:
            if isinstance(raw, dict) and lane.prefilter({"title": raw.get("title")})["passed"]:
                out.setdefault(_closest(raw, owners, rep_day), []).append(raw)
    try:
        queue = json.loads((BASE / "digest_queue.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        queue = []
    items = queue if isinstance(queue, list) else next(iter(queue.values()), []) if isinstance(queue, dict) else []
    for entry in items:
        if isinstance(entry, dict) and entry.get("hash") in gold_in and isinstance(entry.get("raw_sources"), list):
            out.setdefault(entry["hash"], []).extend(r for r in entry["raw_sources"] if isinstance(r, dict))
    return out


def verdict_from_gold(g: dict) -> dict:
    return {"in_scope": True, "exclude_reason": "none", "stage": g["stage"], "actor_type": g["actor_type"],
            "actor_names": [], "stance": g["stance"], "article_type": g["article_type"],
            "significance": g["significance"], "event_key": g["event_key"], "event_date": "",
            "confidence": g.get("confidence", "high"), "reason": "0단계 gold 손라벨",
            "title_kr": "", "summary": "", "why_important": "", "implication": ""}


def build(now: datetime) -> tuple[list[dict], dict[str, dict]]:
    gold = _jsonl(GOLD)
    gold_in = {g["hash"]: g for g in gold if g["in_scope"]}
    mains = main_archive({g["hash"] for g in gold})
    judged_at = now.isoformat()
    records: list[dict] = []
    judged: dict[str, dict] = {}
    for g in gold:
        judged[g["hash"]] = {"at": judged_at, "in": g["in_scope"], "reason": g.get("exclude_reason", "manual"),
                             "feed": "", "v": lane.PROMPT_VERSION}
    reps: dict[str, dict] = {}
    for digest, g in gold_in.items():
        main = mains.get(digest)
        if main is None:
            continue
        record = pipe.make_record(pipe.from_main_record(main), verdict_from_gold(g), judged_at=judged_at,
                                  judge="manual-claude")
        records.append(record)
        reps[digest] = record
    seen = {r["hash"] for r in records} | set(judged)
    # 같은 매체의 같은 기사가 제목 끝만 잘려(「…」) 두 번 오는 일이 있다 — 앞 16자로 본다.
    title16 = lambda r: (r["publisher"], pipe.title_key(r["title_kr"])[:16])  # noqa: E731
    seen_titles = {title16(r) for r in records}
    rep_day = {h: _day(r["collected_at"]) for h, r in reps.items()}
    for owner, raws in folded_sources(set(reps), rep_day).items():
        rep = reps[owner]
        for raw in raws:
            digest = str(raw.get("hash") or "")
            if not digest or digest in seen:
                continue
            item = pipe.from_folded(raw, owner, now_iso=rep["collected_at"])
            record = pipe.inherit(item, rep, judged_at=judged_at)
            key = title16(record)
            if key in seen_titles:
                continue
            seen.add(digest)
            seen_titles.add(key)
            records.append(record)
            judged[digest] = {"at": judged_at, "in": True, "reason": "inherited", "feed": "", "v": lane.PROMPT_VERSION}
    return records, judged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    records, judged = build(now)
    existing = {r["hash"] for r in pipe.shared.load_archive(pipe.ARCHIVE_DIR)}
    new = [r for r in records if r["hash"] not in existing]
    by_origin: dict[str, int] = {}
    for r in new:
        by_origin[r["judge"]] = by_origin.get(r["judge"], 0) + 1
    print(f"레코드 {len(records)}건(새로 {len(new)} · {by_origin}) · 판정 이력 {len(judged)}건")
    if args.dry_run:
        return
    # 메인 수집 시각(collected_at)의 달에 넣는다 — 회차가 그 시각을 따른다.
    for r in new:
        r["archived_at"] = r["collected_at"]
    pipe.shared.append_records(new, pipe.ARCHIVE_DIR)
    state = pipe.load_state(pipe.STATE_FILE)
    for digest, entry in judged.items():
        state["judged"].setdefault(digest, entry)
    pipe.save_state(state, pipe.STATE_FILE, now=now)


if __name__ == "__main__":
    main()
