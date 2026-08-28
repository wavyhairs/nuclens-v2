"""Decide whether a Daily Brief workflow trigger should run the full job.

Scheduled and manual triggers always run.  A successful crawl completion is a
fallback for a missed GitHub cron: it runs only during the morning window and
only when today's brief has not already been sent.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


KST = timezone(timedelta(hours=9))


def decide(*, event_name: str, workflow_conclusion: str, now: datetime,
           outbox_path: Path, fallback_start_hour: int = 4,
           fallback_end_hour: int = 12) -> tuple[bool, str]:
    if event_name in {"schedule", "workflow_dispatch"}:
        return True, f"primary trigger: {event_name}"
    if event_name != "workflow_run":
        return False, f"unsupported trigger: {event_name or 'missing'}"
    if workflow_conclusion != "success":
        return False, f"crawl conclusion is {workflow_conclusion or 'missing'}"

    local_now = now.astimezone(KST)
    if not fallback_start_hour <= local_now.hour < fallback_end_hour:
        return False, (
            f"outside fallback window: {local_now:%Y-%m-%d %H:%M KST} "
            f"({fallback_start_hour:02d}:00-{fallback_end_hour:02d}:00)"
        )

    try:
        outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        outbox = {}
    today = local_now.date().isoformat()
    if outbox.get("date") == today and outbox.get("status") == "sent":
        return False, f"today's brief is already sent: {today}"
    return True, f"missed primary schedule fallback: {today}"


def classify_state(*, event_name: str, should_run: bool, now: datetime,
                   outbox_path: Path) -> str:
    if event_name == "schedule":
        return "schedule_trigger_created"
    if event_name == "workflow_dispatch":
        return "manual_trigger"
    if event_name != "workflow_run":
        return "unsupported_trigger"
    if not should_run:
        return "recovery_not_needed"
    outbox = {}
    try:
        outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    today = now.astimezone(KST).date().isoformat()
    if outbox.get("date") != today:
        return "schedule_missing_recovery"
    if outbox.get("status") in {"failed", "partial"}:
        return "delivery_failed_recovery"
    return "workflow_unconfirmed_recovery"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument(
        "--workflow-conclusion",
        default=os.environ.get("TRIGGER_WORKFLOW_CONCLUSION", ""),
    )
    parser.add_argument("--outbox", type=Path, default=Path("outbox.json"))
    parser.add_argument("--now", help="ISO timestamp override for tests/diagnostics")
    parser.add_argument("--fallback-start-hour", type=int, default=4)
    parser.add_argument("--fallback-end-hour", type=int, default=12)
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    should_run, reason = decide(
        event_name=args.event_name,
        workflow_conclusion=args.workflow_conclusion,
        now=now,
        outbox_path=args.outbox,
        fallback_start_hour=args.fallback_start_hour,
        fallback_end_hour=args.fallback_end_hour,
    )
    state = classify_state(
        event_name=args.event_name, should_run=should_run, now=now,
        outbox_path=args.outbox)
    value = str(should_run).lower()
    print(f"[daily-brief-gate] should_run={value} state={state} — {reason}")
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"should_run={value}\ntrigger_state={state}\n")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write("### Daily automation status\n\n")
            handle.write(f"- trigger: `{state}`\n- decision: `{reason}`\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
