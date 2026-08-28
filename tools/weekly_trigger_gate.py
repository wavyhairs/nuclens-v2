"""Gate the Weekly workflow's crawl-completion recovery trigger.

The primary Friday schedule and manual dispatch always run.  A successful crawl
completion may recover Friday evening through Sunday morning, but only while the
current ISO week's Telegram delivery is not confirmed.  The channel state is
also required when a channel is configured.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


KST = timezone(timedelta(hours=9))


def _week_id(now: datetime) -> str:
    year, week, _ = now.astimezone(KST).isocalendar()
    return f"{year}-W{week:02d}"


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def delivery_state(*, now: datetime, reports_path: Path, channel_path: Path,
                   channel_required: bool) -> tuple[bool, str]:
    key = _week_id(now)
    report = (_load(reports_path).get("reports") or {}).get(key) or {}
    automation = report.get("_automation") or {}
    dm = str((automation.get("telegram") or {}).get("status") or "missing")

    channel = "not_required"
    if channel_required:
        channel = "missing"
        for batch in _load(channel_path).get("batches") or []:
            if batch.get("id") == f"weekly-{key}":
                channel = str(batch.get("status") or "missing")
                break
    complete = dm == "sent" and (not channel_required or channel == "sent")
    return complete, f"week={key} dm={dm} channel={channel}"


def _in_recovery_window(now: datetime) -> bool:
    local = now.astimezone(KST)
    weekday = local.weekday()  # Monday=0
    return ((weekday == 4 and local.hour >= 17)
            or weekday == 5
            or (weekday == 6 and local.hour < 12))


def decide(*, event_name: str, workflow_conclusion: str, now: datetime,
           reports_path: Path, channel_path: Path,
           channel_required: bool = True) -> tuple[bool, str, str]:
    complete, detail = delivery_state(
        now=now, reports_path=reports_path, channel_path=channel_path,
        channel_required=channel_required)
    if event_name in {"schedule", "workflow_dispatch"}:
        state = "schedule_trigger_created" if event_name == "schedule" else "manual_trigger"
        return True, state, f"primary trigger: {event_name}; {detail}"
    if event_name != "workflow_run":
        return False, "unsupported_trigger", f"unsupported trigger: {event_name or 'missing'}"
    if workflow_conclusion != "success":
        return False, "recovery_source_failed", (
            f"crawl conclusion is {workflow_conclusion or 'missing'}")
    if not _in_recovery_window(now):
        return False, "outside_recovery_window", (
            f"outside Friday 17:00-Sunday 12:00 KST; {detail}")
    if complete:
        return False, "delivery_already_confirmed", detail
    if "dm=failed" in detail:
        state = "delivery_failed_recovery"
    elif "dm=pending" in detail:
        state = "workflow_unconfirmed_recovery"
    elif "dm=sent" in detail:
        state = "channel_delivery_failed_recovery"
    else:
        state = "schedule_missing_recovery"
    return True, state, detail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--workflow-conclusion",
                        default=os.environ.get("TRIGGER_WORKFLOW_CONCLUSION", ""))
    parser.add_argument("--reports", type=Path, default=Path("weekly_reports.json"))
    parser.add_argument("--channel-outbox", type=Path, default=Path("channel_outbox.json"))
    parser.add_argument("--channel-required", action="store_true",
                        default=os.environ.get("CHANNEL_REQUIRED", "").lower() == "true")
    parser.add_argument("--now")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    should_run, state, reason = decide(
        event_name=args.event_name,
        workflow_conclusion=args.workflow_conclusion,
        now=now,
        reports_path=args.reports,
        channel_path=args.channel_outbox,
        channel_required=args.channel_required,
    )
    value = str(should_run).lower()
    print(f"[weekly-gate] should_run={value} state={state} — {reason}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"should_run={value}\ntrigger_state={state}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write("### Weekly automation status\n\n")
            handle.write(f"- trigger: `{state}`\n- delivery: `{reason}`\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
