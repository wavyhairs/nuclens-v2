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
        label = case.get("human_label") or case.get("expected_verdict")
        if label and case.get("label_status") != "HUMAN_LABEL_REQUIRED":
            cases.append({**case, "gold": label})
    return cases


def user_message(task: str, case: dict) -> str:
    if task == "IDENTITY_REVIEW":
        return f"A: {case['left_title']}\nB: {case['right_title']}"
    excluded = {"human_label", "expected_verdict", "gold", "label_status"}
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
    args = parser.parse_args()
    if not gemini_client.is_available():
        parser.error("GEMINI_API_KEY is required for live evaluation")
    cases = labelled_cases(json.loads(args.fixtures.read_text(encoding="utf-8")))
    if not cases:
        parser.error("no human-labelled cases; refusing self-evaluation")
    configs = args.config or ["none", "level:medium", "level:high"]
    models = args.model or [gemini_client.MODEL]
    args.out.mkdir(parents=True, exist_ok=True)
    results_path = args.out / "results.jsonl"
    rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
            if line.strip()] if results_path.exists() else []
    done = {row["key"] for row in rows}
    with results_path.open("a", encoding="utf-8") as stream:
        for model in models:
            for config in configs:
                kwargs = config_kwargs(config)
                for case in cases:
                    for repeat in range(args.repeat):
                        key = result_key(model, config, case["id"], repeat)
                        if key in done:
                            continue
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
                            row.update(status="failed",
                                       failure_type=("quota" if "429" in error else "json"),
                                       truncated=False, error=error[:300])
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                        stream.flush()
                        rows.append(row)
    summary = summarize(rows)
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
