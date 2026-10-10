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

공정 비교 — 원래 비교만 보면 안 되는 이유
-----------------------------------------
그림자는 선별 **전** 풀을 줄 세운다. 실제 선별은 그 뒤에 같은 사건의 다른 기사(중복),
어제 이미 보낸 사건(연속일 반복), 하한 미달을 뺀다. 원래 비교는 그렇게 정당하게 빠진
기사를 '그림자가 넣었을 기사'로 세고, 같은 사건을 다른 기사로 고른 경우(실제 '공론화위
출범' · 그림자 '공론화위 1차 회의')를 넣음·뺌 한 쌍으로 센다. 첫 기록(2026-10-10)에서
넣음 13건 중 3건이 중복으로 빠진 must_read 였다.

공정 비교는 같은 게이트를 통과한 후보끼리, 사건 단위로 비교한다 — 그림자 순서대로
내려가며 연속일 반복·하한 미달은 건너뛰고, 중복은 짝(dup_of)의 사건으로 접어 k 개
사건을 채운다. '넣음'에는 실제가 왜 안 골랐는지와, 최근 보도의 후속이라 감점받았는지를
붙인다 — 그림자는 어제 무엇이 나갔는지 모르므로 후속 보도를 그대로 올린다. 행마다 실제
결과(`actual`)가 적힌 기록에서만 계산한다(shadow_rank.actual_outcomes).
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


GATED = frozenset({"repeat", "below_floor"})
_ACTUAL_LABELS = {"duplicate": "중복", "repeat": "연속일 반복", "below_floor": "하한 미달",
                  "ranked_out": "점수 순위 밖", "selected": "선정"}


def _follow_up(row: dict) -> bool:
    return bool(row.get("follow_up") or row.get("follow_up_relation"))


def fair_compare(record: dict) -> dict | None:
    """같은 게이트·같은 사건으로 비교. 실제 결과가 안 적힌 기록이면 None."""
    rows = record.get("rows") or []
    if not rows or not any("actual" in r for r in rows):
        return None
    k = int(record.get("k") or 0)
    by_hash = {r["hash"]: r for r in rows}

    def event(h: str) -> str:
        seen: set[str] = set()
        while (h in by_hash and h not in seen and by_hash[h].get("actual") == "duplicate"
               and by_hash[h].get("dup_of")):
            seen.add(h)
            h = by_hash[h]["dup_of"]
        return h

    selected = set(record.get("selected_hashes") or ()) or {
        r["hash"] for r in rows if r.get("selected")}
    shadow_sorted = sorted(rows, key=lambda r: (r.get("shadow_mean", 99), r.get("score_rank", 99)))
    raw_top = shadow_sorted[:k] if k else []
    picks: list[tuple[str, dict]] = []   # (사건, 그림자가 그 사건으로 처음 고른 행)
    for r in shadow_sorted:
        if len(picks) >= k:
            break
        ev = event(r["hash"])
        # 사건의 대표 기사가 반복·하한으로 빠졌으면 그 사건 전체가 게이트 밖이다
        # (실측 10/10: 비스트라 대출 기사는 어제 보낸 사건의 다른 기사였다).
        if r.get("actual") in GATED or (by_hash.get(ev) or {}).get("actual") in GATED:
            continue
        if ev in {e for e, _ in picks}:
            continue
        picks.append((ev, r))
    shadow_events = {e for e, _ in picks}
    added = []
    for e, r in picks:
        if e in selected:
            continue
        row = dict(r)
        if e != r["hash"]:
            representative = by_hash.get(e)
            row["same_event_as"] = (
                f"같은 사건의 대표 「{representative.get('title', '')[:24]}」도 실제 "
                f"{_ACTUAL_LABELS.get(representative.get('actual'), '미선정')}"
                if representative else "같은 사건의 대표는 후보 30건 밖")
        added.append(row)
    dropped = [by_hash.get(h, {"hash": h, "title": "(그림자 후보 30건 밖)"})
               for h in sorted(selected - shadow_events)]
    return {
        "overlap": len(shadow_events & selected), "added": added, "dropped": dropped,
        # 그림자가 실제와 같은 사건을 다른 기사로 고른 수 — 원래 비교는 이걸 넣음·뺌으로 셌다.
        "same_event": sum(1 for e, r in picks if e in selected and e != r["hash"]),
        "gated": {key: sum(1 for r in raw_top if r.get("actual") == key)
                  for key in ("duplicate", "repeat", "below_floor")},
        "added_follow_up": sum(1 for r in added if _follow_up(r)),
        "partial": list(record.get("actual_partial") or ()),
    }


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
        "fair": fair_compare(record),
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

    lines.extend(_render_fair(ok, titles=titles))

    lines.append("")
    lines.append("원래 비교 (선별 전 풀 그대로 — 중복·반복·하한으로 빠진 기사도 그림자 상위에 남는다)")
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
            lines.append(f"[{s['date']} {s['region']} · 원래 비교]")
            for r in s["added"]:
                lines.append(f"  + 넣음  그림자 {r.get('shadow_mean')} · 점수순위 {r.get('score_rank')} · "
                             f"{r.get('importance', '')[:4]} · {r.get('title', '')}")
            for r in s["dropped"]:
                lines.append(f"  - 뺌    그림자 {r.get('shadow_mean')} · 점수순위 {r.get('score_rank')} · "
                             f"{r.get('importance', '')[:4]} · {r.get('title', '')}")
    return "\n".join(lines)


def _row_note(r: dict) -> str:
    notes = []
    if r.get("same_event_as"):
        notes.append(str(r["same_event_as"]))
    actual = r.get("actual")
    if actual and actual != "duplicate":
        notes.append(f"실제: {_ACTUAL_LABELS.get(actual, actual)}")
    if _follow_up(r):
        penalty = r.get("follow_up")
        notes.append("최근 보도의 후속"
                     + (f" {penalty}" if isinstance(penalty, (int, float))
                        and not isinstance(penalty, bool) else ""))
    return f" ({', '.join(notes)})" if notes else ""


def _render_fair(ok: list[dict], *, titles: bool) -> list[str]:
    lines = ["", "공정 비교 (같은 게이트를 통과한 후보끼리 · 같은 사건은 하나로)"]
    for s in ok:
        if not s.get("fair"):
            lines.append(f"  · {s['date']} {s['region']}: 실제 결과 미기록 — 원래 비교만 있음")
    fair = [s for s in ok if s.get("fair")]
    if not fair:
        return lines
    lines.append(f"{'날짜':10s} {'지역':4s} {'k':>2s} {'겹침':>4s} {'넣음':>4s} {'뺌':>3s} "
                 f"{'같은사건':>6s}  상위 게이트 제외(중복/반복/하한)  넣음 중 후속")
    for s in fair:
        f = s["fair"]
        g = f["gated"]
        lines.append(f"{s['date']:10s} {s['region']:4s} {s['k']:>2d} {f['overlap']:>4d} "
                     f"{len(f['added']):>4d} {len(f['dropped']):>3d} {f['same_event']:>6d}  "
                     f"{g['duplicate']:>14d}/{g['repeat']}/{g['below_floor']:<12d}"
                     f"{f['added_follow_up']:>6d}"
                     + (f"  ※사유 일부 복원({', '.join(f['partial'])})" if f["partial"] else ""))
    total_k = sum(s["k"] for s in fair)
    total = sum(s["fair"]["overlap"] for s in fair)
    lines.append(f"합계: 실제 선정 {total_k}건 중 그림자와 같은 사건 {total}건"
                 f" ({(total / total_k * 100) if total_k else 0:.0f}%) · "
                 f"넣음 {sum(len(s['fair']['added']) for s in fair)}"
                 f"(그중 최근 보도의 후속 {sum(s['fair']['added_follow_up'] for s in fair)}) · "
                 f"뺌 {sum(len(s['fair']['dropped']) for s in fair)}")
    if titles:
        for s in fair:
            f = s["fair"]
            if not f["added"] and not f["dropped"]:
                continue
            lines.append("")
            lines.append(f"[{s['date']} {s['region']} · 공정 비교]")
            for r in f["added"]:
                lines.append(f"  + 넣음  그림자 {r.get('shadow_mean')} · 점수순위 {r.get('score_rank')} · "
                             f"{r.get('importance', '')[:4]} · {r.get('title', '')}{_row_note(r)}")
            for r in f["dropped"]:
                lines.append(f"  - 뺌    그림자 {r.get('shadow_mean', '-')} · 점수순위 "
                             f"{r.get('score_rank', '-')} · {str(r.get('importance', ''))[:4]} · "
                             f"{r.get('title', '')}{_row_note(r)}")
    return lines


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
