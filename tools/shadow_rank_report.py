"""그림자 하루치 순위 vs 실제 발송 — 1주 비교 보고.

읽는 것: shadow_rank_log.jsonl (shadow_rank.run_all 이 아침마다 남김).
외부 호출 0. 숫자는 전부 그 파일에서 나온다.

    python tools/shadow_rank_report.py                # 전체 기간
    python tools/shadow_rank_report.py --since 2026-10-10 --until 2026-10-16
    python tools/shadow_rank_report.py --titles       # 넣고 뺄 기사 제목까지

보는 것
-------
- 겹침: 실제 선정 k 건과 그림자 상위 k 건이 몇 건 같은가. 1.0 이면 새 방식이 아무것도 안 바꾼다.
- 넣음/뺌: 그림자가 올렸을 기사(선정 안 됐는데 그림자 상위 k)와 내렸을 기사(선정됐는데 그림자 k 밖).
  사람이 제목을 보고 어느 쪽이 맞는지 판정하는 자리다 — 이 도구는 판정하지 않는다.
- 순서 흔들림: 같은 후보를 순서만 섞어 두 번 물었을 때 평균 순위 차이. 0 이면 순서에 둔감.
- 경계·재질문: 경계 기사 수와, 한 건씩 두 번 더 물었을 때 등급이 갈린 비율.
  큐레이션 등급(importance)과 재질문 등급이 다른 비율도 함께 — 묶음 없이 혼자 보면 답이 바뀌는가.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "shadow_rank_log.jsonl"


def load(path: Path = LOG, since: str = "", until: str = "") -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict) or rec.get("record_type") != "shadow_rank":
            continue
        day = str(rec.get("date") or "")
        if (since and day < since) or (until and day > until):
            continue
        rows.append(rec)
    return rows


def summarize(record: dict) -> dict:
    """레코드 하나 → 비교 수치. 실패 레코드는 error 만."""
    if record.get("error"):
        return {"date": record.get("date"), "region": record.get("region"),
                "error": record["error"]}
    rows = record.get("rows") or []
    k = int(record.get("k") or 0)
    actual = {r["hash"] for r in rows if r.get("selected")}
    shadow_sorted = sorted(rows, key=lambda r: (r.get("shadow_mean", 99), r.get("score_rank", 99)))
    shadow_top = {r["hash"] for r in shadow_sorted[:k]} if k else set()
    added = [r for r in shadow_sorted[:k] if r["hash"] not in actual]
    dropped = [r for r in rows if r.get("selected") and r["hash"] not in shadow_top]
    gaps = [int(r.get("gap") or 0) for r in rows]
    reask = record.get("reask") or []
    flips = 0
    vs_curation = 0
    judged = 0
    for item in reask:
        grades = [g for g in item.get("grades") or [] if g and not str(g).startswith("error")]
        if len(grades) >= 2:
            judged += 1
            if len(set(grades)) > 1:
                flips += 1
            if item.get("importance") and item["importance"] not in grades:
                vs_curation += 1
    return {
        "date": record.get("date"), "region": record.get("region"), "k": k,
        "candidates": len(rows), "calls": int(record.get("calls") or 0),
        "overlap": len(actual & shadow_top), "added": added, "dropped": dropped,
        "mean_gap": round(sum(gaps) / len(gaps), 2) if gaps else 0.0,
        "boundary": len(record.get("boundary") or []),
        "reask_judged": judged, "reask_flips": flips, "reask_vs_curation": vs_curation,
    }


def render(summaries: list[dict], *, titles: bool = False) -> str:
    lines: list[str] = []
    ok = [s for s in summaries if "error" not in s]
    failed = [s for s in summaries if "error" in s]
    lines.append(f"그림자 순위 기록 {len(summaries)}벌 (성공 {len(ok)} · 실패 {len(failed)})")
    if failed:
        for s in failed:
            lines.append(f"  ! {s['date']} {s['region']}: {s['error']}")
    if not ok:
        return "\n".join(lines)

    lines.append("")
    lines.append(f"{'날짜':10s} {'지역':4s} {'k':>2s} {'후보':>4s} {'겹침':>4s} {'넣음':>4s} {'뺌':>3s} "
                 f"{'순서차':>6s} {'경계':>4s} {'재질문 갈림':>10s} {'호출':>4s}")
    for s in ok:
        lines.append(f"{s['date']:10s} {s['region']:4s} {s['k']:>2d} {s['candidates']:>4d} "
                     f"{s['overlap']:>4d} {len(s['added']):>4d} {len(s['dropped']):>3d} "
                     f"{s['mean_gap']:>6.2f} {s['boundary']:>4d} "
                     f"{s['reask_flips']:>4d}/{s['reask_judged']:<5d} {s['calls']:>4d}")
    total_k = sum(s["k"] for s in ok)
    total_overlap = sum(s["overlap"] for s in ok)
    total_judged = sum(s["reask_judged"] for s in ok)
    total_flips = sum(s["reask_flips"] for s in ok)
    total_vs = sum(s["reask_vs_curation"] for s in ok)
    lines.append("")
    lines.append(f"합계: 실제 선정 {total_k}건 중 그림자 상위와 겹침 {total_overlap}건"
                 f" ({(total_overlap / total_k * 100) if total_k else 0:.0f}%) · "
                 f"넣음 {sum(len(s['added']) for s in ok)} · 뺌 {sum(len(s['dropped']) for s in ok)} · "
                 f"평균 순서차 {sum(s['mean_gap'] for s in ok) / len(ok):.2f} · "
                 f"경계 {sum(s['boundary'] for s in ok)} · "
                 f"재질문 갈림 {total_flips}/{total_judged} · 큐레이션 등급과 다름 {total_vs}/{total_judged} · "
                 f"호출 {sum(s['calls'] for s in ok)}")
    if titles:
        for s in ok:
            if not s["added"] and not s["dropped"]:
                continue
            lines.append("")
            lines.append(f"[{s['date']} {s['region']}]")
            for r in s["added"]:
                lines.append(f"  + 넣음  그림자 {r.get('shadow_mean')} · 점수순위 {r.get('score_rank')} · "
                             f"{r.get('importance', '')[:4]} · {r.get('title', '')}")
            for r in s["dropped"]:
                lines.append(f"  - 뺌    그림자 {r.get('shadow_mean')} · 점수순위 {r.get('score_rank')} · "
                             f"{r.get('importance', '')[:4]} · {r.get('title', '')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="그림자 하루치 순위 vs 실제 발송 비교")
    parser.add_argument("--since", default="")
    parser.add_argument("--until", default="")
    parser.add_argument("--titles", action="store_true")
    parser.add_argument("--path", default=str(LOG))
    args = parser.parse_args(argv)
    records = load(Path(args.path), args.since, args.until)
    if not records:
        print("기록이 없습니다 — shadow_rank_log.jsonl 이 비었거나 기간에 해당 없음")
        return 1
    print(render([summarize(r) for r in records], titles=args.titles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
