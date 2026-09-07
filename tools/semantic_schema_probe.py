"""Bounded, resumable probe for the Semantic non-object JSON regression.

This is intentionally not a full evaluator.  It runs one known case at medium,
uses no HTTP retries, persists every attempt immediately, and hard-caps the two
diagnostic phases (one pre-schema observation, three post-schema validations).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gemini_client
import semantic_verifier

CASE_ID = "semantic-aa57a5168dafb9f9-unsupported"
PHASE_LIMIT = {"pre-schema": 1, "post-schema": 3}


def load_case(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for case in payload.get("cases") or []:
        if case.get("id") == CASE_ID:
            return case
    raise ValueError(f"missing fixture case: {CASE_ID}")


def run(args: argparse.Namespace) -> dict:
    limit = PHASE_LIMIT[args.phase]
    if not 0 <= args.max_new_calls <= limit:
        raise ValueError(f"{args.phase} max-new-calls must be 0..{limit}")
    case = load_case(args.fixtures)
    args.out.mkdir(parents=True, exist_ok=True)
    results_path = args.out / "results.jsonl"
    rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
            if line.strip()] if results_path.exists() else []
    attempted_keys = {row["key"] for row in rows}
    jobs = [
        (repeat, f"semantic-schema-probe-v1|{args.phase}|{args.model}|medium|{CASE_ID}|{repeat}")
        for repeat in range(limit)
    ]
    gemini_client.reset_call_log()
    new = 0
    with results_path.open("a", encoding="utf-8") as stream:
        for repeat, key in jobs:
            if key in attempted_keys or new >= args.max_new_calls:
                continue
            before = len(gemini_client._CALL_DETAIL)
            row = {"key": key, "phase": args.phase, "model": args.model,
                   "config": "level:medium", "fixture_id": CASE_ID,
                   "repeat": repeat, "gold": case.get("human_label")}
            try:
                result = gemini_client.call_json(
                    semantic_verifier.SYSTEM_PROMPT,
                    semantic_verifier.verification_prompt(
                        case.get("source_evidence"), case.get("claim") or "",
                        context=case.get("generated_context_not_source_evidence")),
                    temperature=0.0, max_output_tokens=6000, timeout=150.0,
                    retries=0, thinking_level="medium", model=args.model,
                    label=f"eval:SEMANTIC:schema:{args.phase}",
                    capture_response_text=True,
                    response_json_schema=(semantic_verifier.OUTPUT_JSON_SCHEMA
                                          if args.phase == "post-schema" else None),
                )
                normalized = semantic_verifier.normalize_report(result)
                row.update(status="ok", prediction=normalized["verdict"],
                           error_types=[finding["type"]
                                        for finding in normalized["findings"]])
            except Exception as exc:
                row.update(status="failed", failure_type=(
                    "schema" if isinstance(exc, gemini_client.GeminiError)
                    and "semantic" in str(exc) else "api"), error=str(exc)[:500])
            detail = (gemini_client._CALL_DETAIL[-1]
                      if len(gemini_client._CALL_DETAIL) > before else {})
            for field in ("requested_thinking", "finish_reason", "truncated",
                          "thought_tokens", "prompt_tokens", "candidate_tokens",
                          "total_tokens", "retry_count", "raw_response_text",
                          "latency_seconds", "http_status", "quota_kind"):
                if field in detail:
                    row[field] = detail[field]
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            rows.append(row)
            attempted_keys.add(key)
            new += 1
    stats = gemini_client.call_stats()
    summary = {
        "phase": args.phase,
        "case_id": CASE_ID,
        "model": args.model,
        "planned": limit,
        "new_attempts": new,
        "physical_api_calls_this_run": stats["total"],
        "per_model_calls_this_run": stats["per_model"],
        "phase_rows": [row for row in rows if row.get("phase") == args.phase],
    }
    (args.out / f"summary-{args.phase}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=sorted(PHASE_LIMIT), required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--max-new-calls", type=int, required=True)
    args = parser.parse_args()
    try:
        summary = run(args)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
