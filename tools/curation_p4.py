"""Curation P4 preflight and explicitly-approved live runners.

Default execution is plan-only and performs zero OpenAI/Gemini calls.  All
artifacts are written below ``.eval``; production caches and settings are never
opened for writing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gemini_client
import news_bot
from tools import curation_p4_judge as judge
from tools import replay_inputs

CAPTURE_ROOT = ROOT / ".eval" / "gemini-reasoning-v2" / "captures"
GOLD_PATH = ROOT / "tests" / "fixtures" / "gemini_reasoning" / "curation_gold.json"
DEFAULT_OUT = ROOT / ".eval" / "gemini-reasoning-v2" / "p4-curation"
ARMS = ("current", "level:low", "level:medium", "level:high")
GEMINI_PRICE = {"input": 0.25, "output": 1.50}  # USD / 1M, 2026-09-19


def load_capture_records(root: Path = CAPTURE_ROOT) -> list[tuple[Path, dict]]:
    rows = []
    for path in sorted(root.glob("*/llm_capture.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append((path, json.loads(line)))
    return rows


def _risk_tags(article: dict, body: str) -> set[str]:
    text = " ".join((str(article.get("title") or ""),
                     str(article.get("description") or ""), body))
    tags = {"factual_support", "unsupported_inference", "important_omission",
            "certainty", "core_distortion"}
    if any(token in text for token in ("·", " 및 ", "이어", "동시에", "한편")):
        tags.add("event_boundary")
    if any(token in text for token in ("한국", "미국", "유럽", "글로벌", "해외", "국내")):
        tags.add("scope")
    if any(token in text for token in ("계획", "검토", "예정", "승인", "허가", "착공", "운영", "협상", "계약")):
        tags.add("stage")
    if any(char.isdigit() for char in text):
        tags.add("date")
    if any(token in text for token in ("때문", "따라", "영향", "원인", "결과", "전망")):
        tags.add("causality")
    return tags


def candidate_captures(records: list[tuple[Path, dict]], index: dict[str, dict]) -> list[dict]:
    choices = []
    for path, record in records:
        detail = record.get("detail") or {}
        if detail.get("task") != "curation" or detail.get("label") != "curation":
            continue
        try:
            rebuilt = replay_inputs.reconstruct_curation(record, index)
        except (KeyError, TypeError, ValueError):
            continue
        if (len(rebuilt["articles"]) != news_bot.BATCH_CHUNK
                or rebuilt["is_regeneration"] or rebuilt["prompt_needs_reports_kb"]):
            continue
        coverage = {dimension: 0 for dimension in judge.DIMENSIONS}
        for article in rebuilt["articles"]:
            body = (rebuilt.get("bodies") or {}).get(article["hash"], "")
            for dimension in _risk_tags(article, body):
                coverage[dimension] += 1
        choices.append({"path": path, "record": record, "input": rebuilt,
                        "risk_coverage": coverage,
                        "risk_floor": min(coverage.values()),
                        "risk_total": sum(coverage.values()),
                        "captured_at": float(record.get("captured_at") or 0)})
    return choices


def select_canary(records: list[tuple[Path, dict]], index: dict[str, dict]) -> dict:
    choices = candidate_captures(records, index)
    if not choices:
        raise RuntimeError("no 15-item initial curation capture without reports_kb")
    # Balance first, breadth second, recency third. This is fixed before outputs.
    return max(choices, key=lambda row: (row["risk_floor"], row["risk_total"],
                                         row["captured_at"]))


def arm_order(capture_id: str) -> list[str]:
    return sorted(ARMS, key=lambda arm: hashlib.sha256(
        f"curation-p4-arm-order-v1|{capture_id}|{arm}".encode("utf-8")
    ).hexdigest())


class ReasoningClient:
    """Delegate to production transport while changing only thinking level."""

    def __init__(self, arm: str, max_calls: int):
        if arm not in ARMS:
            raise ValueError(f"unknown arm: {arm}")
        self.arm = arm
        self.max_calls = max_calls
        self.calls = 0

    def __call__(self, system_prompt: str, user_message: str, **kwargs):
        if self.calls >= self.max_calls:
            raise RuntimeError(f"approved Gemini logical-call budget exhausted ({self.max_calls})")
        self.calls += 1
        # Production currently supplies no reasoning kwarg. Refuse a future drift
        # instead of silently overriding a newly activated production policy.
        if "thinking_level" in kwargs or "thinking_budget" in kwargs:
            raise RuntimeError("production reasoning is no longer inactive; re-review required")
        if self.arm != "current":
            kwargs["thinking_level"] = self.arm.removeprefix("level:")
        return gemini_client.call_json(system_prompt, user_message, **kwargs)


def _read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def hard_gate(articles: list[dict], output: dict[str, dict], details: list[dict],
              log_rows: list[dict]) -> dict:
    expected = {article["hash"] for article in articles}
    actual = set(output)
    schema_errors = {}
    for digest, item in output.items():
        errors = news_bot.curation_errors(item, require_features=True)
        if errors:
            schema_errors[digest] = errors
    bad_logs = [row for row in log_rows if row.get("record_type") in {
        "curation_failure", "quality_event"
    }]
    truncation = sum(bool(row.get("truncated")) for row in details)
    http_failures = sum(bool(row.get("http_status")) for row in details)
    checks = {
        "input_count_is_15": len(articles) == news_bot.BATCH_CHUNK == 15,
        "no_missing_items": not (expected - actual),
        "no_extra_items": not (actual - expected),
        "schema_valid": not schema_errors,
        "no_truncation": truncation == 0,
        "no_transport_failure": http_failures == 0,
        "no_failure_or_quarantine_log": not bad_logs,
    }
    return {
        "status": "PASS" if all(checks.values()) else "BLOCK",
        "checks": checks,
        "missing_hashes": sorted(expected - actual), "extra_hashes": sorted(actual - expected),
        "schema_errors": schema_errors, "truncation": truncation,
        "transport_failures": http_failures, "failure_log_rows": bad_logs,
        "logical_calls": len([row for row in details if row.get("retry_count") in (0, None)]),
        "transport_attempts": len(details),
        "regeneration_calls": sum(str(row.get("task", "")).startswith("curation:재생성")
                                  for row in details),
    }


def _source_packet(article: dict, bodies: dict[str, str] | None) -> dict:
    return {"title": article.get("title", ""), "description": article.get("description", ""),
            "body": (bodies or {}).get(article["hash"], ""),
            "publisher": article.get("publisher") or article.get("domain", "")}


def canary_judge_requests(selection: dict, arm_outputs: dict[str, dict]) -> list[judge.BlindRequest]:
    if set(arm_outputs) != set(ARMS):
        raise RuntimeError("all four distinct arms must pass before blind judging")
    articles = selection["input"]["articles"]
    requests = []
    for article in articles:
        candidates = {arm: arm_outputs[arm][article["hash"]] for arm in ARMS}
        requests.append(judge.build_blind_request(
            article["hash"], _source_packet(article, selection["input"].get("bodies")),
            candidates, 0))
    return requests


def _captured_items(selection: dict) -> dict[str, dict]:
    """Recorded baseline output, used only to estimate future judge payload size."""
    response = selection["record"].get("response") or {}
    try:
        text = response["candidates"][0]["content"]["parts"][0]["text"]
        items = json.loads(text).get("items") or []
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {}
    return {str(item.get("id")): item for item in items if isinstance(item, dict) and item.get("id")}


def estimated_canary_judge_requests(selection: dict) -> list[judge.BlindRequest]:
    captured = _captured_items(selection)
    proxies = {}
    for article in selection["input"]["articles"]:
        item = captured.get(article["hash"][:8])
        if item is None:
            return []
        clean = {key: value for key, value in item.items() if key not in {"id", "idx"}}
        proxies[article["hash"]] = clean
    arm_outputs = {arm: proxies for arm in ARMS}
    return canary_judge_requests(selection, arm_outputs)


def summarize_canary_judgments(rows: list[dict], arm_results: dict[str, dict]) -> dict:
    pairwise = {arm: {"win": 0, "loss": 0, "tie": 0} for arm in ARMS if arm != "current"}
    verdicts = {arm: {verdict: 0 for verdict in judge.VERDICTS} for arm in ARMS}
    dimension_errors = {arm: {dimension: 0 for dimension in judge.DIMENSIONS} for arm in ARMS}
    unsafe_pass = {arm: 0 for arm in ARMS}
    for row in rows:
        aliases = row["aliases"]
        reverse = {internal: alias for alias, internal in aliases.items()}
        judgment = row["judgment"]
        by_alias = {item["candidate"]: item for item in judgment["candidates"]}
        for arm in ARMS:
            candidate = by_alias[reverse[arm]]
            verdicts[arm][candidate["final_verdict"]] += 1
            errors = {item["name"] for item in candidate["dimensions"]
                      if item["status"] == "ERROR"}
            for dimension in errors:
                dimension_errors[arm][dimension] += 1
            # A PASS accompanied by an error is rejected by validate_judgment;
            # retain this explicit metric so it cannot silently disappear.
            unsafe_pass[arm] += int(candidate["final_verdict"] == "PASS" and bool(errors))
        baseline_alias = reverse["current"]
        for arm in pairwise:
            other_alias = reverse[arm]
            comparison = next(item for item in judgment["pairwise"]
                              if {item["left"], item["right"]}
                              == {baseline_alias, other_alias})
            preference = comparison["preferred"]
            if preference == "TIE":
                pairwise[arm]["tie"] += 1
            elif preference == other_alias:
                pairwise[arm]["win"] += 1
            else:
                pairwise[arm]["loss"] += 1
    operations = {}
    total_cost = 0.0
    for arm, payload in arm_results.items():
        details = payload.get("details") or []
        input_tokens = sum(int(row.get("prompt_tokens") or 0) for row in details)
        output_tokens = sum(int(row.get("candidate_tokens") or 0) for row in details)
        thought_tokens = sum(int(row.get("thought_tokens") or 0) for row in details)
        cost = (input_tokens / 1_000_000 * GEMINI_PRICE["input"]
                + output_tokens / 1_000_000 * GEMINI_PRICE["output"])
        total_cost += cost
        operations[arm] = {
            "hard_gate": payload["hard_gate"], "latency_seconds": round(sum(
                float(row.get("latency_seconds") or 0) for row in details), 3),
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "thought_tokens": thought_tokens, "cost_usd": round(cost, 5),
        }
    return {
        "baseline_pairwise": pairwise,
        "candidate_verdicts": verdicts,
        "intervention": {arm: counts["REPAIR"] + counts["BLOCK"]
                         for arm, counts in verdicts.items()},
        "unsafe_pass": unsafe_pass,
        "dimension_errors": dimension_errors,
        "operations": operations,
        "total_gemini_cost_usd": round(total_cost, 5),
        "winner_policy": "No scalar winner; use baseline pairwise win/loss/tie as primary.",
    }


def _historical_usage(records: list[tuple[Path, dict]]) -> dict:
    initial, regen = [], 0
    orchestrations: set[tuple[str, int]] = set()
    for path, record in records:
        detail = record.get("detail") or {}
        if detail.get("task") not in {"curation", "curation:재생성"}:
            continue
        if detail.get("task") == "curation:재생성":
            regen += 1
        if detail.get("task") == "curation" and detail.get("retry_count") in (0, None):
            orchestrations.add((str(path), int(record.get("seq") or 0)))
            if detail.get("prompt_tokens") and detail.get("candidate_tokens"):
                initial.append(detail)
    return {
        "orchestrations": len(orchestrations), "regeneration_calls": regen,
        "regeneration_rate_per_orchestration": regen / len(orchestrations) if orchestrations else 0,
        "median_input_tokens": int(statistics.median(
            [row["prompt_tokens"] for row in initial])) if initial else 0,
        "median_output_tokens": int(statistics.median(
            [row["candidate_tokens"] for row in initial])) if initial else 0,
        "median_latency_seconds": round(statistics.median(
            [row["latency_seconds"] for row in initial]), 2) if initial else 0,
    }


def build_preflight(records: list[tuple[Path, dict]], selection: dict | None,
                    gold: dict) -> dict:
    historical = _historical_usage(records)
    expected_per_arm = 1 + historical["regeneration_rate_per_orchestration"]
    expected_calls = round(expected_per_arm * len(ARMS), 1)
    input_tokens = round(historical["median_input_tokens"] * expected_calls)
    output_tokens = round(historical["median_output_tokens"] * expected_calls)
    gemini_cost = input_tokens / 1_000_000 * GEMINI_PRICE["input"] \
        + output_tokens / 1_000_000 * GEMINI_PRICE["output"]
    calibration = judge.calibration_requests(gold)
    historical_calibration = judge.historical_calibration_requests(gold)
    canary_judging = estimated_canary_judge_requests(selection) if selection else []
    canary = ({
        "artifact": str(selection["path"].relative_to(ROOT)),
        "capture_seq": selection["record"].get("seq"),
        "captured_at": selection["captured_at"],
        "items": len(selection["input"]["articles"]),
        "batch_chunk": news_bot.BATCH_CHUNK,
        "arms": list(ARMS),
        "execution_order": arm_order(selection["path"].parent.name),
        "risk_coverage": selection["risk_coverage"],
    } if selection else {
        "status": "UNAVAILABLE_NO_ELIGIBLE_CAPTURE",
        "items": 0,
        "batch_chunk": news_bot.BATCH_CHUNK,
        "arms": list(ARMS),
    })
    return {
        "generated_at_epoch": time.time(), "live_calls_made": {"openai": 0, "gemini": 0},
        "production_changes": False,
        "canary": canary,
        "judge_calibration": {
            "policy": judge.EVALUATOR_POLICY,
            "channel": "manual ChatGPT UI (model display name recorded on import)",
            "gold_cases": len(calibration) // judge.CALIBRATION_THRESHOLDS["repeats"],
            "historical_only_cases": (
                len(historical_calibration) // judge.CALIBRATION_THRESHOLDS["repeats"]),
            "repeats": judge.CALIBRATION_THRESHOLDS["repeats"],
            "thresholds": judge.CALIBRATION_THRESHOLDS,
            "manual_packets": (judge.CALIBRATION_THRESHOLDS["repeats"]
                               if calibration else 0),
            "openai_api_calls": 0, "incremental_api_cost_usd": 0,
            "approximate_prompt_and_answer_volume": judge.estimate_tokens(calibration),
            "instruction": (
                "Run each repeat packet in a separate fresh ChatGPT conversation."
                if calibration else
                "Do not run a judge: no source-complete calibration cases are eligible."
            ),
        },
        "gemini_canary_estimate": {
            "logical_calls_minimum": len(ARMS), "logical_calls_expected": expected_calls,
            "logical_calls_fail_closed_maximum": 20 * len(ARMS),
            "proposed_approval_cap": "3 logical calls/arm, 12 total",
            "estimated_input_tokens": input_tokens, "estimated_output_tokens": output_tokens,
            "estimated_cost_usd": round(gemini_cost, 4),
            "estimated_api_seconds": round(historical["median_latency_seconds"] * expected_calls),
            "planning_time_minutes": "2-4 expected pure serial API time; pacing/backoff can extend it",
            "pricing_usd_per_million": GEMINI_PRICE,
            "quota_note": "Account-specific RPM/RPD is not exposed locally; verify the provider dashboard before approval.",
        },
        "post_canary_judge_estimate": {
            "manual_packets": 1, "openai_api_calls": 0, "incremental_api_cost_usd": 0,
            "estimate_from_recorded_baseline_proxy": (
                judge.estimate_tokens(canary_judging) if canary_judging else None),
            "note": "One upload/paste packet; recomputed from actual outputs after the hard gate.",
        },
        "gate": ("HALTED_SOURCE_COMPLETE_CALIBRATION_POOL_EMPTY"
                 if not calibration else "BLOCKED_PENDING_MANUAL_CHATGPT_CALIBRATION_IMPORT"),
    }


def run_gemini_canary(selection: dict, out: Path, *, max_calls_per_arm: int) -> dict:
    if max_calls_per_arm <= 0:
        raise RuntimeError("max_calls_per_arm must be positive for live canary")
    results = {}
    for arm in arm_order(selection["path"].parent.name):
        arm_dir = out / "arms" / arm.replace(":", "-")
        arm_dir.mkdir(parents=True, exist_ok=True)
        log_path = arm_dir / "delivery_log.jsonl"
        if log_path.exists():
            raise RuntimeError(f"refusing to append to prior canary log: {log_path}")
        client = ReasoningClient(arm, max_calls_per_arm)
        before = len(gemini_client._CALL_DETAIL)
        started = time.monotonic()
        output = news_bot.curate_batch(
            selection["input"]["articles"], [], selection["input"].get("bodies"),
            client=client, log_path=log_path)
        details = gemini_client._CALL_DETAIL[before:]
        gate = hard_gate(selection["input"]["articles"], output, details, _read_log(log_path))
        payload = {"arm": arm, "wall_seconds": round(time.monotonic() - started, 3),
                   "output": output, "details": details, "hard_gate": gate}
        (arm_dir / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        results[arm] = payload
        if gate["status"] != "PASS":
            break
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--phase", choices=[
        "preflight", "export-calibration", "import-calibration", "canary",
        "export-canary-judge", "import-canary-judge"], default="preflight")
    parser.add_argument("--approve-live-calls", action="store_true")
    parser.add_argument("--max-gemini-calls-per-arm", type=int, default=0)
    parser.add_argument("--answer", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.phase == "canary" and not args.approve_live_calls:
        parser.error("Gemini live canary requires --approve-live-calls")

    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)

    if args.phase == "preflight":
        records = load_capture_records()
        index = replay_inputs.load_article_index(
            archive_dir=ROOT / "archive", curated=ROOT / "curated.json")
        choices = candidate_captures(records, index)
        selection = max(choices, key=lambda row: (
            row["risk_floor"], row["risk_total"], row["captured_at"])) if choices else None
        report = build_preflight(records, selection, gold)
        calibration_summary_path = args.out / "calibration-summary.json"
        if calibration_summary_path.exists():
            calibration = json.loads(calibration_summary_path.read_text(encoding="utf-8"))
            report["judge_calibration"]["historical_result"] = calibration
            if report["judge_calibration"]["gold_cases"]:
                report["gate"] = (
                    "READY_FOR_EXPLICIT_GEMINI_APPROVAL"
                    if (calibration.get("status") == "PASS"
                        and calibration.get("calibration_scope") == "source_complete")
                    else "HALTED_JUDGE_CALIBRATION_NOT_PROVEN")
        (args.out / "preflight.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.phase == "export-calibration":
        try:
            exported = judge.export_manual_calibration_packets(
                gold, args.out / "manual-calibration")
        except judge.JudgeValidationError as exc:
            parser.error(f"calibration export refused: {exc}")
        report = {"policy": judge.EVALUATOR_POLICY, "openai_api_calls": 0,
                  "packets": exported,
                  "instruction": "Use one fresh ChatGPT conversation per packet; upload the three JSON answers."}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.phase == "import-calibration":
        if not args.answer:
            parser.error("import-calibration requires one or more --answer JSON files")
        manifest = args.out / "manual-calibration" / "calibration-manifest.private.json"
        imported = []
        try:
            for answer in args.answer:
                imported.extend(judge.import_manual_calibration_answer(answer, manifest))
        except judge.JudgeValidationError as exc:
            parser.error(f"manual calibration rejected: {exc}")
        results_path = args.out / "calibration.jsonl"
        existing = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()] if results_path.exists() else []
        existing.extend(imported)
        with results_path.open("a", encoding="utf-8") as stream:
            for row in imported:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
        summary = judge.calibration_summary(gold, existing)
        (args.out / "calibration-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["status"] == "PASS" else 2

    calibration_path = args.out / "calibration-summary.json"
    calibration_state = (json.loads(calibration_path.read_text(encoding="utf-8"))
                         if calibration_path.exists() else {})
    if (calibration_state.get("status") != "PASS"
            or calibration_state.get("calibration_scope") != "source_complete"):
        parser.error("Gemini canary refused: current-policy GPT calibration has not passed")
    records = load_capture_records()
    index = replay_inputs.load_article_index(
        archive_dir=ROOT / "archive", curated=ROOT / "curated.json")
    selection = select_canary(records, index)
    if args.phase == "canary":
        if args.max_gemini_calls_per_arm <= 0:
            parser.error("canary requires --max-gemini-calls-per-arm > 0")
        results = run_gemini_canary(
            selection, args.out, max_calls_per_arm=args.max_gemini_calls_per_arm)
        gates_pass = set(results) == set(ARMS) and all(
            row["hard_gate"]["status"] == "PASS" for row in results.values())
        summary = {"status": "READY_FOR_BLIND_JUDGE" if gates_pass else "BLOCK",
                   "arms_completed": list(results),
                   "hard_gates": {arm: row["hard_gate"] for arm, row in results.items()}}
        (args.out / "canary-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if gates_pass else 2

    canary_summary_path = args.out / "canary-summary.json"
    if (not canary_summary_path.exists()
            or json.loads(canary_summary_path.read_text(encoding="utf-8")).get("status")
            != "READY_FOR_BLIND_JUDGE"):
        parser.error("blind judging refused: all four canary hard gates must pass")
    arm_outputs = {}
    for arm in ARMS:
        result_path = args.out / "arms" / arm.replace(":", "-") / "result.json"
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if payload.get("hard_gate", {}).get("status") != "PASS":
            parser.error(f"blind judging refused: hard gate failed for {arm}")
        arm_outputs[arm] = payload["output"]
    requests = canary_judge_requests(selection, arm_outputs)
    if args.phase == "export-canary-judge":
        exported = judge.export_manual_canary_packet(
            requests, args.out / "manual-canary-judge")
        print(json.dumps({"policy": judge.EVALUATOR_POLICY, "openai_api_calls": 0,
                          "packet": exported}, ensure_ascii=False, indent=2))
        return 0

    if not args.answer:
        parser.error("import-canary-judge requires --answer JSON")
    if len(args.answer) != 1:
        parser.error("import-canary-judge accepts exactly one --answer JSON")
    try:
        imported = judge.import_manual_canary_answer(
            args.answer[0], args.out / "manual-canary-judge" / "canary-manifest.private.json")
    except judge.JudgeValidationError as exc:
        parser.error(f"manual canary judgment rejected: {exc}")
    results_path = args.out / "canary-judge.jsonl"
    with results_path.open("a", encoding="utf-8") as stream:
        for row in imported:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    existing = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    latest = {row["key"]: row for row in existing}
    completed = [row for row in latest.values()
                 if row.get("status") == "ok" and row.get("key") in {
                     judge.result_key(request.case_id, request.repeat) for request in requests}]
    summary = {"status": "DONE" if len(completed) == len(requests) else "INCOMPLETE",
               "policy": judge.EVALUATOR_POLICY, "planned": len(requests),
               "completed": len(completed), "manual_answers_imported": len(imported),
               "openai_api_calls": 0}
    if summary["status"] == "DONE":
        summary["metrics"] = summarize_canary_judgments(completed, {
            arm: json.loads((args.out / "arms" / arm.replace(":", "-") / "result.json")
                            .read_text(encoding="utf-8"))
            for arm in ARMS
        })
    (args.out / "canary-judge-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "DONE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
