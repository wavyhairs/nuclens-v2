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

VERDICTS = {
    "IDENTITY_REVIEW": {"MERGE", "SEPARATE", "AMBIGUOUS"},
    "CURATION": {"PASS", "REPAIR", "BLOCK"},
    "SEMANTIC": {"PASS", "REPAIR", "UNVERIFIABLE", "BLOCK"},
    "SYNTHESIS": {"PASS", "REPAIR", "UNVERIFIABLE", "BLOCK"},
}
ERROR_TYPES = {
    "FACT_ERROR", "NUMBER_ERROR", "ENTITY_ERROR", "DATE_ERROR", "SCOPE_ERROR",
    "STAGE_ERROR", "CAUSALITY_ERROR", "TEMPORAL_ERROR", "CERTAINTY_ERROR",
    "UNSUPPORTED_INFERENCE",
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


def select_cases(cases: list[dict], case_ids: list[str]) -> list[dict]:
    """Select an explicit checkpoint subset without changing fixture Gold."""
    if not case_ids:
        return cases
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate --case-id")
    by_id = {case["id"]: case for case in cases}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise ValueError(f"unknown or non-human-labelled --case-id: {missing}")
    return [by_id[case_id] for case_id in case_ids]


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


def validate_result(task: str, result: object) -> tuple[str, list[str]]:
    """Validate the evaluator contract instead of treating parsed JSON as success."""
    if not isinstance(result, dict):
        raise ValueError("response must be a JSON object")
    verdict = result.get("verdict")
    if verdict not in VERDICTS[task]:
        raise ValueError(f"invalid verdict for {task}: {verdict!r}")
    error_types = result.get("error_types") or []
    if not isinstance(error_types, list) or not all(
            isinstance(value, str) for value in error_types):
        raise ValueError("error_types must be a list of strings")
    if task != "IDENTITY_REVIEW" and any(
            value not in ERROR_TYPES for value in error_types):
        raise ValueError(f"unknown error type for {task}: {error_types!r}")
    return verdict, error_types


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, int(len(ordered) * fraction) - 1)]


def failure_type(row: dict) -> str | None:
    """Normalize legacy rows while keeping environment failures out of API metrics."""
    kind = row.get("failure_type")
    error = str(row.get("error") or "")
    if kind == "api" and ("WinError 10013" in error or
                           "forbidden by its access permissions" in error):
        return "environment"
    return kind


def _metric_block(rows: list[dict]) -> dict:
    completed = [row for row in rows if row.get("status") == "ok"]
    latency = [float(row["latency_seconds"]) for row in completed]
    gold_labels = sorted({str(row.get("gold")) for row in completed
                          if row.get("gold") is not None})
    gold_counts = {label: sum(row.get("gold") == label for row in completed)
                   for label in gold_labels}
    true_counts = {
        label: sum(row.get("gold") == label and row.get("prediction") == label
                   for row in completed)
        for label in gold_labels
    }
    recalls = {
        label: (true_counts[label] / gold_counts[label] if gold_counts[label] else None)
        for label in gold_labels
    }
    class_recalls = [value for value in recalls.values() if value is not None]
    predicted_counts: dict[str, int] = {}
    for row in completed:
        prediction = str(row.get("prediction"))
        predicted_counts[prediction] = predicted_counts.get(prediction, 0) + 1
    precisions = {
        label: (true_counts[label] / predicted_counts.get(label, 0)
                if predicted_counts.get(label, 0) else None)
        for label in gold_labels
    }
    grouped: dict[str, list[str]] = {}
    for row in completed:
        key = f"{row['model']}|{row['config']}|{row['fixture_id']}"
        grouped.setdefault(key, []).append(str(row.get("prediction")))
    repeat_groups = [values for values in grouped.values() if len(values) >= 2]
    consistent = sum(len(set(values)) == 1 for values in repeat_groups)
    return {
        "completed": len(completed),
        "correct": sum(row.get("gold") == row.get("prediction") for row in completed),
        "accuracy": (sum(row.get("gold") == row.get("prediction")
                         for row in completed) / len(completed) if completed else None),
        "balanced_accuracy": (sum(class_recalls) / len(class_recalls)
                              if class_recalls else None),
        "per_label_recall": recalls,
        "per_label_precision": precisions,
        "gold_counts": gold_counts,
        "merge_recall": recalls.get("MERGE"),
        "separate_recall": recalls.get("SEPARATE"),
        "false_merge": sum(row.get("gold") == "SEPARATE"
                           and row.get("prediction") == "MERGE" for row in completed),
        "false_split": sum(row.get("gold") == "MERGE"
                           and row.get("prediction") == "SEPARATE" for row in completed),
        "prediction_counts": predicted_counts,
        "repeat_consistency": (consistent / len(repeat_groups)
                               if repeat_groups else None),
        "repeat_groups": len(repeat_groups),
        "latency_p50": statistics.median(latency) if latency else None,
        "latency_p95": _percentile(latency, .95),
        "latency_max": max(latency) if latency else None,
        "thought_tokens": sum(int(row.get("thought_tokens") or 0) for row in completed),
        "prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in completed),
        "candidate_tokens": sum(int(row.get("candidate_tokens") or 0)
                                for row in completed),
        "total_tokens": sum(int(row.get("total_tokens") or 0) for row in completed),
        "retry_telemetry_rows": sum("retry_count" in row for row in completed),
        "retries": (sum(int(row.get("retry_count") or 0) for row in completed)
                    if all("retry_count" in row for row in completed) else None),
        "token_telemetry_rows": sum("total_tokens" in row for row in completed),
    }


def summarize(rows: list[dict]) -> dict:
    completed = [row for row in rows if row.get("status") == "ok"]
    error_counts: dict[str, int] = {}
    for row in completed:
        for error_type in row.get("error_types") or []:
            error_counts[error_type] = error_counts.get(error_type, 0) + 1
    metrics = _metric_block(rows)
    configs = {
        config: _metric_block([row for row in rows if row.get("config") == config])
        for config in sorted({str(row.get("config")) for row in rows})
    }
    return {
        **metrics,
        "failed": len(rows) - len(completed),
        "truncation": sum(bool(row.get("truncated")) for row in rows),
        "json_failure": sum(failure_type(row) == "json" for row in rows),
        "schema_failure": sum(failure_type(row) == "schema" for row in rows),
        "api_failure": sum(failure_type(row) == "api" for row in rows),
        "environment_failure": sum(
            failure_type(row) == "environment" for row in rows),
        "quota_429": sum(failure_type(row) == "quota" for row in rows),
        "error_types": error_counts,
        "configs": configs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=sorted(SYSTEMS))
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--config", action="append", default=[])
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument(
        "--case-id", action="append", default=[],
        help="evaluate only these human-labelled case ids, in supplied order")
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
    try:
        cases = select_cases(cases, args.case_id)
    except ValueError as exc:
        parser.error(str(exc))
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
    environment_stopped = False
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
            detail: dict = {}
            try:
                result = gemini_client.call_json(
                    SYSTEMS[args.task], user_message(args.task, case),
                    temperature=0.0, max_output_tokens=4096, timeout=120,
                    retries=1, model=model, label=f"eval:{args.task}", **kwargs)
                detail = (gemini_client._CALL_DETAIL[-1]
                          if len(gemini_client._CALL_DETAIL) > before else {})
                prediction, error_types = validate_result(args.task, result)
                row.update(status="ok", prediction=prediction,
                           error_types=error_types,
                           latency_seconds=time.monotonic() - started,
                           thought_tokens=detail.get("thought_tokens") or 0,
                           prompt_tokens=detail.get("prompt_tokens") or 0,
                           candidate_tokens=detail.get("candidate_tokens") or 0,
                           total_tokens=detail.get("total_tokens") or 0,
                           retry_count=detail.get("retry_count") or 0,
                           requested_thinking=detail.get("requested_thinking"),
                           finish_reason=detail.get("finish_reason"),
                           truncated=bool(detail.get("truncated")))
            except json.JSONDecodeError as exc:
                row.update(status="failed", failure_type="json",
                           truncated=False, error=str(exc)[:300])
            except ValueError as exc:
                row.update(status="failed", failure_type="schema",
                           truncated=False, error=str(exc)[:300])
            except gemini_client.GeminiTruncated as exc:
                row.update(status="failed", failure_type="truncation",
                           truncated=True, error=str(exc)[:300])
            except Exception as exc:  # persisted checkpoint, never converted to PASS
                error = str(exc)
                is_quota = "429" in error or "quota" in error.lower()
                is_environment = ("WinError 10013" in error or
                                  "forbidden by its access permissions" in error)
                row.update(status="failed",
                           failure_type=("quota" if is_quota else
                                         "environment" if is_environment else "api"),
                           truncated=False, error=error[:300])
                quota_stopped = is_quota
                environment_stopped = is_environment
            if not detail and len(gemini_client._CALL_DETAIL) > before:
                detail = gemini_client._CALL_DETAIL[-1]
            row.setdefault("retry_count", detail.get("retry_count") or 0)
            row.setdefault("requested_thinking", detail.get("requested_thinking"))
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            rows.append(row)
            if row.get("status") == "ok":
                done.add(key)
            new_attempted += 1
            if quota_stopped or environment_stopped:
                break
    summary = summarize(latest_results(rows))
    summary.update({
        "planned_combinations": len(planned),
        "completed_keys_before_run": len(planned) - len(pending_before),
        "new_calls_attempted": new_attempted,
        "pending_combinations": sum(job[4] not in done for job in planned),
        "checkpoint_call_limit": args.max_new_calls,
        "quota_state": "STOPPED_ON_QUOTA" if quota_stopped else "NOT_HIT",
        "environment_state": ("STOPPED_ON_ENVIRONMENT_FAILURE"
                              if environment_stopped else "AVAILABLE"),
    })
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
