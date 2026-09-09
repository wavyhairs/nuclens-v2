"""캡처 파일 하나에서 profile 별 `REPLAY_FIDELITY_PROVEN` / `NOT_PROVEN` 을 낸다.

`recorded_replay` 가 한 orchestration 을 채점하고, `replay_inputs` 가 그 입력을
복원한다. 이 모듈은 둘을 이어 **캡처 → 판정**까지 한 번에 돌린다. Gemini 호출은
0회다.

## orchestration 묶기

한 캡처 파일에는 여러 회차가 섞여 있다. 재현 단위는 `curate_batch` 의 chunk 하나다.

    curation            ← 새 chunk 시작
    curation:재생성      ← 같은 chunk 의 품질 게이트 재생성
    curation            ← 다음 chunk

묶기를 잘못하면 요청 열이 어긋나 NOT_PROVEN 이 난다. 즉 **묶기의 정확성도 하네스가
채점한다** — 여기서 따로 증명하려 애쓸 필요가 없다.

## 캡처가 담지 못하는 것 (판정에 반영)

`_capture` 는 HTTP 200 payload 를 받은 뒤 기록한다. 잘린 응답(MAX_TOKENS)은
payload 가 오므로 기록되지만, 429/5xx 로 끝난 시도는 줄이 남지 않는다. 그래서
**요청이 통째로 실패해 분할·유실로 간 회차는 캡처만으로 재현되지 않는다.**
판정문에 그 한계를 함께 적는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import recorded_replay, replay_inputs

CAPTURE_LIMITS = (
    "429/5xx 로 끝난 시도는 캡처에 줄이 남지 않는다. 요청 전체가 실패해 분할·유실로 "
    "간 회차는 이 판정의 범위 밖이다.")


def group_curation(records: list[dict]) -> list[list[dict]]:
    """재생성을 앞선 chunk 에 붙여 orchestration 단위로 묶는다."""
    groups: list[list[dict]] = []
    for record in records:
        label = (record.get("detail") or {}).get("task")
        if label == "curation:재생성" and groups:
            groups[-1].append(record)
        else:
            groups.append([record])
    return groups


def _run_curation(group: list[dict], index: dict[str, dict],
                  work_dir: Path) -> dict:
    built = replay_inputs.reconstruct_curation(group[0], index)
    driver = recorded_replay.curation_driver(
        built["articles"], [], built["bodies"], work_dir)
    report = recorded_replay.verify(group, driver)
    report["independence"] = replay_inputs.independence(built)
    report["unresolved"] = built["unresolved_tags"]
    report["needs_reports_kb"] = built["prompt_needs_reports_kb"]
    return report


def _run_dedup(group: list[dict], index: dict[str, dict], _work_dir: Path) -> dict:
    built = replay_inputs.reconstruct_dedup(group[0], index)
    driver = recorded_replay.dedup_driver(
        built["articles"], built["scores"], stage=built["stage"])
    report = recorded_replay.verify(group, driver)
    report["independence"] = replay_inputs.independence(built)
    report["unresolved"] = built["unresolved_indices"]
    return report


def evaluate(records: list[dict], callsite: str, *, archive_dir: Path,
             curated: Path, work_dir: Path) -> dict:
    selected = recorded_replay.select(records, callsite)
    if not selected:
        return {"callsite": callsite, "status": recorded_replay.NOT_PROVEN,
                "reason": "no captured calls for this callsite",
                "orchestrations": 0, "capture_limits": CAPTURE_LIMITS}

    if callsite == "curation":
        groups = group_curation(selected)
        index = replay_inputs.load_article_index(
            archive_dir=archive_dir, curated=curated)
        runner = _run_curation
    elif callsite in {"dedup", "dedup_final"}:
        groups = [[record] for record in selected]
        index = replay_inputs.load_title_index(
            archive_dir=archive_dir, curated=curated)
        runner = _run_dedup
    else:
        return {"callsite": callsite, "status": recorded_replay.NOT_PROVEN,
                "reason": f"no input reconstructor for {callsite}",
                "orchestrations": len(selected), "capture_limits": CAPTURE_LIMITS}

    reports = [runner(group, index, work_dir) for group in groups]
    proven = [r for r in reports if r["status"] == recorded_replay.PROVEN]
    # 하나라도 어긋나면 profile 전체가 NOT_PROVEN 이다. "대체로 맞는 replay" 라는
    # 것은 없다 — 어긋난 회차가 왜 어긋났는지 모르는 채로 숫자를 만들면 안 된다.
    status = (recorded_replay.PROVEN if len(proven) == len(reports)
              else recorded_replay.NOT_PROVEN)
    return {
        "callsite": callsite,
        "status": status,
        "orchestrations": len(reports),
        "proven": len(proven),
        "recorded_calls": sum(r["recorded_calls"] for r in reports),
        "articles_with_repo_identity": sum(
            r["independence"]["articles_with_repo_identity"] for r in reports),
        "prompt_derived_fields": sum(
            r["independence"]["prompt_derived_fields"] for r in reports),
        "always_prompt_derived": reports[0]["independence"]["always_prompt_derived"],
        "unresolved": [item for r in reports for item in r["unresolved"]],
        "first_failures": [
            {"mismatched_calls": r["mismatched_calls"],
             "differences": r["mismatches"][0]["differences"][:5] if r["mismatches"]
             else [],
             "driver_error": r["production_result"].get("driver_error")
             if isinstance(r["production_result"], dict) else None}
            for r in reports if r["status"] != recorded_replay.PROVEN][:3],
        "capture_limits": CAPTURE_LIMITS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--callsite", action="append", default=[],
                        choices=sorted(recorded_replay.CALLSITE_LABELS))
    parser.add_argument("--archive-dir", type=Path, default=ROOT / "archive")
    parser.add_argument("--curated", type=Path, default=ROOT / "curated.json")
    parser.add_argument("--work-dir", type=Path,
                        default=ROOT / ".eval" / "fidelity")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    args.work_dir.mkdir(parents=True, exist_ok=True)
    records = recorded_replay.load_capture(args.capture)
    callsites = args.callsite or ["curation", "dedup", "dedup_final"]
    report = {
        "capture": str(args.capture),
        "captured_calls": len(records),
        "live_gemini_calls": 0,
        "profiles": {name: evaluate(records, name, archive_dir=args.archive_dir,
                                    curated=args.curated, work_dir=args.work_dir)
                     for name in callsites},
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    # 하나라도 NOT_PROVEN 이면 0 이 아니다 — CI 에 걸 수 있어야 한다.
    return 0 if all(row["status"] == recorded_replay.PROVEN
                    for row in report["profiles"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
