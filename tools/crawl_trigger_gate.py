"""Durable 3-hour crawl slot claim/finalize gate.

GitHub ``schedule`` and the independent Cloudflare watchdog can both create a
run for the same slot.  The workflow-level ``nuclens-state`` concurrency group
serializes those runs; this file supplies the durable second half: the first
run claims the slot on ``main`` before any API/LLM work, and later runs observe
that claim from a fresh checkout and stop.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


STATE_FILE = Path("crawl_runs.json")
RESULT_FILE = Path("crawl_gate_result.json")
SLOT_HOURS = 3
LEASE_MINUTES = 45
RETENTION_DAYS = 21
SUCCESS_STATES = {"success_with_articles", "success_zero_articles"}


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def slot_start(now: datetime | None = None) -> datetime:
    now = _utc(now)
    return now.replace(hour=(now.hour // SLOT_HOURS) * SLOT_HOURS,
                       minute=0, second=0, microsecond=0)


def slot_id(now: datetime | None = None) -> str:
    return slot_start(now).isoformat().replace("+00:00", "Z")


def empty_state() -> dict[str, Any]:
    return {"schema_version": 1, "slots": {}}


def load_state(path: Path | None = None) -> dict[str, Any]:
    path = path or STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_state()
    if not isinstance(data, dict) or not isinstance(data.get("slots"), dict):
        raise ValueError(f"invalid crawl state: {path}")
    data.setdefault("schema_version", 1)
    return data


def save_state(state: dict[str, Any], path: Path | None = None) -> None:
    path = path or STATE_FILE
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _prune(state: dict[str, Any], now: datetime) -> None:
    cutoff = now - timedelta(days=RETENTION_DAYS)
    state["slots"] = {
        key: row for key, row in state.get("slots", {}).items()
        if key.startswith("manual:") or (_parse(key) or now) >= cutoff
    }
    manual = [(key, row) for key, row in state["slots"].items()
              if key.startswith("manual:")]
    for key, _row in sorted(manual, key=lambda item: item[0])[:-20]:
        state["slots"].pop(key, None)


def decide_claim(
    state: dict[str, Any], *, event_name: str, trigger_source: str = "",
    recovery_reason: str = "", run_id: str = "", now: datetime | None = None,
) -> dict[str, Any]:
    now = _utc(now)
    run_id = str(run_id or int(now.timestamp()))
    manual = event_name == "workflow_dispatch" and trigger_source != "backup_watchdog"
    key = f"manual:{run_id}" if manual else slot_id(now)
    existing = state.get("slots", {}).get(key) or {}
    status = str(existing.get("status") or "")

    if manual:
        should_run, trigger_state = True, "manual_trigger"
    elif status in SUCCESS_STATES:
        should_run, trigger_state = False, "slot_already_completed"
    elif status == "running":
        claimed = _parse(existing.get("claimed_at"))
        fresh = claimed is not None and now - claimed < timedelta(minutes=LEASE_MINUTES)
        if fresh:
            should_run, trigger_state = False, "slot_claim_active"
        else:
            should_run, trigger_state = True, "stale_claim_recovery"
    elif status == "failed":
        should_run, trigger_state = True, "workflow_failed_recovery"
    elif event_name == "schedule":
        should_run, trigger_state = True, "schedule_trigger_created"
    elif trigger_source == "backup_watchdog":
        trigger_state = ("workflow_failed_recovery"
                         if recovery_reason == "workflow_failed"
                         else "schedule_missing_recovery")
        should_run = True
    else:
        should_run, trigger_state = True, "manual_trigger"

    if should_run:
        attempts = int(existing.get("attempts") or 0) + 1
        state.setdefault("slots", {})[key] = {
            "slot": key,
            "attempt_id": run_id,
            "attempts": attempts,
            "claimed_at": now.isoformat().replace("+00:00", "Z"),
            "finished_at": None,
            "event_name": event_name,
            "trigger_source": trigger_source or event_name,
            "trigger_state": trigger_state,
            "status": "running",
            "new_article_count": None,
        }
        _prune(state, now)

    return {
        "should_run": should_run,
        "slot": key,
        "attempt_id": run_id,
        "trigger_state": trigger_state,
        "existing_status": status or "missing",
    }


def finalize(
    state: dict[str, Any], *, slot: str, attempt_id: str,
    collect_outcome: str, new_article_count: int | str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = _utc(now)
    row = state.get("slots", {}).get(slot)
    if not isinstance(row, dict):
        raise ValueError(f"crawl slot claim not found: {slot}")
    if str(row.get("attempt_id")) != str(attempt_id):
        raise ValueError(f"crawl slot is owned by another attempt: {slot}")

    try:
        count = max(0, int(new_article_count or 0))
    except (TypeError, ValueError):
        count = 0
    if str(collect_outcome).lower() == "success":
        status = "success_with_articles" if count else "success_zero_articles"
    else:
        status = "failed"
    row.update({
        "finished_at": now.isoformat().replace("+00:00", "Z"),
        "status": status,
        "collect_outcome": str(collect_outcome or "unknown"),
        "new_article_count": count,
    })
    return {"collection_state": status, "new_article_count": count}


def _write_outputs(result: dict[str, Any]) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            for key, value in result.items():
                if isinstance(value, bool):
                    value = str(value).lower()
                handle.write(f"{key}={value}\n")
    RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    claim = sub.add_parser("claim")
    claim.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    claim.add_argument("--trigger-source", default="")
    claim.add_argument("--recovery-reason", default="")
    claim.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    finish = sub.add_parser("finalize")
    finish.add_argument("--slot", required=True)
    finish.add_argument("--attempt-id", required=True)
    finish.add_argument("--collect-outcome", required=True)
    finish.add_argument("--new-article-count", default="0")
    args = parser.parse_args()

    state = load_state()
    if args.command == "claim":
        result = decide_claim(
            state, event_name=args.event_name, trigger_source=args.trigger_source,
            recovery_reason=args.recovery_reason, run_id=args.run_id)
        if result["should_run"]:
            save_state(state)
    else:
        result = finalize(
            state, slot=args.slot, attempt_id=args.attempt_id,
            collect_outcome=args.collect_outcome,
            new_article_count=args.new_article_count)
        save_state(state)
    _write_outputs(result)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
