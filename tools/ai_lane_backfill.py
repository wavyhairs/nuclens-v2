"""해외 AI 레인 수동 백필 — 레인이 하는 판정을 사람이 대신 적는다.

언제 쓰나
---------
레인을 처음 켤 때, 또는 레인이 놓친 기간을 사람이 메울 때. 판정은 gold 라벨
(``eval_artifacts/ai_lane/gold_labels.jsonl`` 의 포함 기사)에서, 레인 프롬프트가 쓸
해석 문장은 ``seed_manual.json`` 에서 온다. 레코드는 레인과 **같은 함수**
(``ai_lane_pipeline.make_record``)로 만들고 판정자를 ``manual-claude`` 로 남긴다.

회차 날짜는 메인 수집 시각(``collected_at``)을 따르므로, 지난 기사는 지난 회차에 들어간다.
이미 DB 에 있는 기사는 건너뛴다(멱등). 판정 이력에도 적어 레인이 다시 묻지 않게 한다.

사용:
    python tools/ai_lane_backfill.py            # 쓰기
    python tools/ai_lane_backfill.py --dry-run  # 무엇이 들어갈지만
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ai_lane_pipeline as lane  # noqa: E402

GOLD = ROOT / "eval_artifacts" / "ai_lane" / "gold_labels.jsonl"
SEED = ROOT / "eval_artifacts" / "ai_lane" / "seed_manual.json"
MAIN_ARCHIVE = ROOT / "archive"
VERDICT_FIELDS = ("application_area", "ai_tech", "actor_type", "actor_names", "maturity",
                  "adoption_significance", "countries", "tech_scope")


def build_records(*, now: datetime) -> list[dict]:
    gold = [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]
    wanted = {row["id"]: row for row in gold if row.get("in_scope")}
    texts = json.loads(SEED.read_text(encoding="utf-8"))["texts"]
    main: dict[str, dict] = {}
    for path in sorted(MAIN_ARCHIVE.glob("*.jsonl")):
        for row in lane._read_jsonl(path):
            if row.get("hash") in wanted:
                main[row["hash"]] = row
    missing = sorted(set(wanted) - set(main))
    if missing:
        raise SystemExit(f"메인 아카이브에 없는 gold 기사: {missing}")
    records = []
    for digest, label in wanted.items():
        article = lane.from_main_record(main[digest])
        extra = texts.get(digest, {})
        for key in ("summary", "title_kr"):
            if key in extra:
                article[key] = extra[key]
        verdict = {key: label.get(key) for key in VERDICT_FIELDS}
        verdict.update({"in_scope": True, "exclude_reason": "none", "reason": label.get("note", "")[:60],
                        "why_important": extra.get("why_important", ""),
                        "implication": extra.get("implication", "")})
        records.append(lane.make_record(article, verdict, judged_at=now.isoformat(),
                                        model="", judge="manual-claude"))
    records.sort(key=lambda r: r["collected_at"])
    return records


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    existing = lane.archive_identities(lane.load_archive())
    records = [r for r in build_records(now=now) if r["hash"] not in existing["hashes"]]
    for r in records:
        print(f"  {r['collected_at'][:10]} {r['maturity']:<10} {r['title_kr'][:60]}")
    if args.dry_run:
        print(f"dry-run: {len(records)}건")
        return
    lane.append_records(records)
    state = lane.load_state()
    for r in records:
        state["judged"][r["hash"]] = {"at": now.isoformat(), "in": True, "reason": "manual"}
    lane.save_state(state, now=now)
    print(f"백필 {len(records)}건 → {lane.ARCHIVE_DIR}")


if __name__ == "__main__":
    main()
