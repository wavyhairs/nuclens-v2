#!/usr/bin/env python3
"""봉인 도입 전에 수집된 기사의 사건일을, 본문을 다시 받아 되살린다.

왜: 큐레이션은 본문으로 사건일을 확인하지만 본문은 저장하지 않는다. PR #205 부터는
그 판정을 근거 manifest 에 봉인해 사이트가 믿을 수 있게 됐지만, 그 전에 수집된
기사에는 봉인이 없다. 사이트는 그 사건일을 '원문 근거를 다시 확인할 수 없음'으로
지운다 — 2026-09-27 라이브 기준 1,416건, 전부 이 사유였고 날짜 이상은 0건이었다.

무엇을 하나: 그 기사마다 본문을 **한 번 다시 받아**(Gemini 호출 없음) 수집 때와
**같은 판정기**(`article_quality_gate.verify_event_date_claim`)로 확인한다. 본문이
그 날짜를 말하고, 봉인을 얹은 레코드가 실제 사이트 게이트에서 날짜를 지키는 것만
사이드카 파일에 적는다.

    archive_event_date_seals.json
        {hash: {base_fingerprint, verified_event_date, verified_at, method, body_sha256}}

`base_fingerprint` 는 확인할 때 본 manifest 의 봉인값이다. 빌드는 레코드의 manifest 가
그대로일 때만 얹는다. `body_sha256` 은 어떤 본문으로 확인했는지의 지문이다 — 본문
자체는 저장하지 않는다.

**아카이브는 건드리지 않는다**(`archive_source_backfill.json` 과 같은 방식). 본문을
못 받았거나, 지금 본문이 그 날짜를 말하지 않거나, 근거가 본문이 아니면(설명문은
아카이브에 남지 않고 다시 받을 수도 없다) 적지 않는다 — 그 사건일은 지금처럼
지워진다. 이미 적힌 기사는 건너뛴다(멱등).

쓰는 법:
    python tools/restore_event_date_seals.py            # 대상만 세고 끝
    python tools/restore_event_date_seals.py --run      # 실제 수집
    python tools/restore_event_date_seals.py --run --limit 200
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import article_body  # noqa: E402
import article_quality_gate as gate  # noqa: E402
from data_quality import clean_text  # noqa: E402

OUT = ROOT / "archive_event_date_seals.json"
UNAVAILABLE = "event_date_source_unavailable"
# 크롤보다 조심스럽게 — 한 번에 몰아 받는 작업이라 동시성을 낮춘다(backfill_sources 와 같다).
WORKERS = 6
# 중간에 끊겨도 받은 만큼은 남도록 이만큼씩 받고 적는다.
BATCH = 100


def load_build():
    spec = importlib.util.spec_from_file_location(
        "nuclens_build_data", ROOT / "web" / "build_data.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def site_codes(build, record: dict) -> dict:
    """사이트 게이트가 이 레코드의 사건일을 어떤 사유로 지우는가 — 실제 게이트 그대로."""
    _visible, stats = build.apply_archive_integrity_gate([dict(record)])
    return stats.get("sanitize_codes") or {}


def targets(build, records: list[dict]) -> list[dict]:
    return [record for record in records
            if UNAVAILABLE in site_codes(build, record)]


def judge(build, record: dict, body: str, *, today: str) -> tuple[dict, str]:
    """(사이드카 항목, 거절 사유). 둘 중 하나만 채워진다."""
    reference = record.get("pub") or record.get("archived_at")
    source = {"title": clean_text(record.get("title")), "article_text": body}
    claim = gate.verify_event_date_claim(record, source, reference)
    if not claim:
        candidate = {key: record.get(key) for key in (
            "event_date", "event_date_type", "event_date_precision", "event_date_source")}
        problem = gate._event_date_problem(
            candidate, source, gate._reference_date({}, source, reference))
        return {}, problem or "not_verified"
    manifest = record.get("verified_evidence") or {}
    entry = {
        "base_fingerprint": manifest.get("manifest_fingerprint", ""),
        "verified_event_date": claim,
        "verified_at": today,
        "method": "body_refetch",
        "body_sha256": hashlib.sha256(clean_text(body).encode("utf-8")).hexdigest(),
    }
    # 적기 전에 빌드가 실제로 쓸 모양 그대로 얹어 게이트에 넣어 본다.
    trial = dict(record)
    trial["verified_evidence"] = gate.with_sealed_event_date(manifest, claim)
    visible, _stats = build.apply_archive_integrity_gate([trial])
    if not visible or visible[0].get("event_date") != record.get("event_date"):
        return {}, "gate_rejected_seal"
    return entry, ""


def load_done() -> dict:
    try:
        raw = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save(done: dict) -> None:
    OUT.write_text(json.dumps(dict(sorted(done.items())), ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run", action="store_true", help="본문을 실제로 받는다")
    parser.add_argument("--limit", type=int, default=0, help="이번에 받을 최대 건수")
    args = parser.parse_args(argv)

    build = load_build()
    records = build.load_archive()      # 이미 적힌 봉인이 얹힌 상태 — 그 기사는 대상에서 빠진다
    todo = targets(build, records)
    by_source = Counter(clean_text(r.get("event_date_source")) for r in todo)
    print(f"[restore] 아카이브 {len(records)}건 · 근거 재확인 불가로 지워지는 사건일 "
          f"{len(todo)}건 {dict(by_source)}")
    fetchable = [r for r in todo if clean_text(r.get("event_date_source")) == "article_text"]
    if args.limit:
        fetchable = fetchable[:args.limit]
    if not args.run:
        print(f"[restore] 본문을 다시 받을 대상 {len(fetchable)}건 — --run 으로 실행")
        return 0

    done = load_done()
    today = datetime.now(timezone.utc).date().isoformat()
    outcome: Counter = Counter()
    fetch_reasons: Counter = Counter()
    for start in range(0, len(fetchable), BATCH):
        batch = fetchable[start:start + BATCH]
        articles = [{"hash": r["hash"], "title": clean_text(r.get("title")),
                     "link": r.get("resolved_url") or r.get("url") or ""} for r in batch]
        bodies, stats = article_body.fetch_bodies(
            articles, max_fetch=len(articles), workers=WORKERS)
        fetch_reasons.update(stats.get("reasons") or {})
        for record in batch:
            body = bodies.get(record["hash"])
            if not body:
                outcome["body_unavailable"] += 1
                continue
            entry, problem = judge(build, record, body, today=today)
            if entry:
                done[record["hash"]] = entry
                outcome["restored"] += 1
            else:
                outcome[f"rejected:{problem}"] += 1
        save(done)
        print(f"[restore] {min(start + BATCH, len(fetchable))}/{len(fetchable)} "
              f"{dict(outcome)}", flush=True)
    print(json.dumps({"targets": len(todo), "attempted": len(fetchable),
                      "outcome": dict(outcome), "fetch_reasons": dict(fetch_reasons),
                      "sidecar_entries": len(done)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
