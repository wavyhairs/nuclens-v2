"""Resumable offline Gemini Gold evaluator; never touches production caches."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gemini_client

SYSTEMS = {
    "IDENTITY_REVIEW": """두 제목이 같은 구체적 사건인지 판정한다. JSON만 출력한다.
{"verdict":"MERGE|SEPARATE|AMBIGUOUS","reason_code":"..."}""",
    "CURATION": """기사 표현이 사건·단계·범위·날짜·인과를 섞었는지 판정한다. JSON만 출력한다.
{"verdict":"PASS|REPAIR|BLOCK","error_types":["..."]}""",
    "SEMANTIC": """주장을 source facts만으로 검증한다. JSON만 출력한다.
{"verdict":"PASS|REPAIR|UNVERIFIABLE|BLOCK","error_types":["..."]}""",
    "SYNTHESIS": """합성 문장이 제공된 근거의 범위·단계·인과를 지키는지 판정한다. JSON만 출력한다.
{"verdict":"PASS|REPAIR|UNVERIFIABLE|BLOCK","error_types":["..."]}""",
}


def result_key(model: str, config: str, fixture_id: str, repeat: int) -> str:
    return f"{model}|{config}|{fixture_id}|{repeat}"


def completed_keys(rows: list[dict]) -> set[str]:
    return {row["key"] for row in rows if row.get("status") == "ok"}


def latest_results(rows: list[dict]) -> list[dict]:
    return list({row["key"]: row for row in rows}.values())


def config_kwargs(config: str) -> dict[str, str]:
    if config in {"none", "unspecified", "current"}:
        return {}
    level = config.removeprefix("level:")
    if level not in gemini_client._THINKING_LEVELS:
        raise ValueError(f"invalid config: {config}")
    return {"thinking_level": level}


def labelled_cases(payload: dict) -> list[dict]:
    cases = []
    for case in payload.get("cases") or []:
        label = case.get("human_label")
        if label and case.get("label_status") in {"USER_SPECIFIED", "HUMAN_LABELLED"}:
            cases.append({**case, "gold": label})
    return cases


def user_message(task: str, case: dict) -> str:
    if task == "IDENTITY_REVIEW":
        left = case.get("left_title") or (case.get("a") or {}).get("title")
        right = case.get("right_title") or (case.get("b") or {}).get("title")
        if not left or not right:
            raise ValueError(f"identity fixture {case.get('id')} has no pair titles")
        return f"A: {left}\nB: {right}"
    if task == "SEMANTIC":
        return json.dumps({"claim": case.get("claim"),
                           "source_evidence": case.get("source_evidence")},
                          ensure_ascii=False, sort_keys=True)
    excluded = {
        "human_label", "expected_verdict", "gold", "label_status",
        "human_dimensions", "human_error_types", "human_notes", "reason_code",
        "error_types", "required_repair", "selection_metadata_not_gold",
        "candidate_kind",
    }
    return json.dumps({key: value for key, value in case.items() if key not in excluded},
                      ensure_ascii=False, sort_keys=True)


def summarize(rows: list[dict]) -> dict:
    completed = [row for row in rows if row.get("status") == "ok"]
    latency = [float(row["latency_seconds"]) for row in completed]
    false_merge = sum(row.get("gold") == "SEPARATE" and row.get("prediction") == "MERGE"
                      for row in completed)
    false_split = sum(row.get("gold") == "MERGE" and row.get("prediction") == "SEPARATE"
                      for row in completed)
    grouped: dict[str, list[str]] = {}
    for row in completed:
        key = f"{row['model']}|{row['config']}|{row['fixture_id']}"
        grouped.setdefault(key, []).append(str(row.get("prediction")))
    consistent = sum(len(set(values)) == 1 for values in grouped.values())
    error_counts: dict[str, int] = {}
    for row in completed:
        for error_type in row.get("error_types") or []:
            error_counts[error_type] = error_counts.get(error_type, 0) + 1
    return {
        "completed": len(completed),
        "failed": len(rows) - len(completed),
        "correct": sum(row.get("gold") == row.get("prediction") for row in completed),
        "false_merge": false_merge,
        "false_split": false_split,
        "repeat_consistency": consistent / len(grouped) if grouped else None,
        "latency_p50": statistics.median(latency) if latency else None,
        "latency_p95": (sorted(latency)[max(0, int(len(latency) * .95) - 1)]
                        if latency else None),
        "thought_tokens": sum(int(row.get("thought_tokens") or 0) for row in completed),
        "truncation": sum(bool(row.get("truncated")) for row in rows),
        "json_failure": sum(row.get("failure_type") == "json" for row in rows),
        "quota_429": sum(row.get("failure_type") == "quota" for row in rows),
        "error_types": error_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=sorted(SYSTEMS))
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--config", action="append", default=[])
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--max-new-calls", type=int, default=30,
        help="checkpoint after at most this many new calls (default: 30; 0 = plan only)")
    args = parser.parse_args()
    if args.max_new_calls < 0:
        parser.error("--max-new-calls must be >= 0")
    if args.max_new_calls and not gemini_client.is_available():
        parser.error("GEMINI_API_KEY is required for live evaluation")
    cases = labelled_cases(json.loads(args.fixtures.read_text(encoding="utf-8")))
    if not cases:
        parser.error("no human-labelled cases; refusing self-evaluation")
    configs = args.config or ["none", "level:medium", "level:high"]
    models = args.model or [gemini_client.MODEL]
    for config in configs:
        config_kwargs(config)
    args.out.mkdir(parents=True, exist_ok=True)
    results_path = args.out / "results.jsonl"
    rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
            if line.strip()] if results_path.exists() else []
    # Only successful keys are complete. Quota/truncation/API failures stay pending
    # and may be retried on a later bounded run.
    done = completed_keys(rows)
    planned = [
        (model, config, case, repeat,
         result_key(model, config, case["id"], repeat))
        for model in models
        for config in configs
        for case in cases
        for repeat in range(args.repeat)
    ]
    pending_before = [job for job in planned if job[4] not in done]
    new_attempted = 0
    quota_stopped = False
    with results_path.open("a", encoding="utf-8") as stream:
        for model, config, case, repeat, key in pending_before:
            if new_attempted >= args.max_new_calls:
                break
            kwargs = config_kwargs(config)
            before = len(gemini_client._CALL_DETAIL)
            started = time.monotonic()
            row = {"key": key, "model": model, "config": config,
                   "fixture_id": case["id"], "repeat": repeat,
                   "gold": case["gold"]}
            try:
                result = gemini_client.call_json(
                    SYSTEMS[args.task], user_message(args.task, case),
                    temperature=0.0, max_output_tokens=4096, timeout=120,
                    retries=1, model=model, label=f"eval:{args.task}", **kwargs)
                detail = (gemini_client._CALL_DETAIL[-1]
                          if len(gemini_client._CALL_DETAIL) > before else {})
                row.update(status="ok", prediction=result.get("verdict"),
                           error_types=result.get("error_types") or [],
                           latency_seconds=time.monotonic() - started,
                           thought_tokens=detail.get("thought_tokens") or 0,
                           truncated=bool(detail.get("truncated")))
            except gemini_client.GeminiTruncated as exc:
                row.update(status="failed", failure_type="truncation",
                           truncated=True, error=str(exc)[:300])
            except Exception as exc:  # persisted checkpoint, never converted to PASS
                error = str(exc)
                is_quota = "429" in error or "quota" in error.lower()
                row.update(status="failed",
                           failure_type=("quota" if is_quota else "api_or_json"),
                           truncated=False, error=error[:300])
                quota_stopped = is_quota
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            rows.append(row)
            if row.get("status") == "ok":
                done.add(key)
            new_attempted += 1
            if quota_stopped:
                break
    summary = summarize(latest_results(rows))
    summary.update({
        "planned_combinations": len(planned),
        "completed_keys_before_run": len(planned) - len(pending_before),
        "new_calls_attempted": new_attempted,
        "pending_combinations": sum(job[4] not in done for job in planned),
        "checkpoint_call_limit": args.max_new_calls,
        "quota_state": "STOPPED_ON_QUOTA" if quota_stopped else "NOT_HIT",
    })
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
