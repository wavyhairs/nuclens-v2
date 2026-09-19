"""Decide whether a Daily Brief workflow trigger should run the full job.

사람이 손으로 돌린 실행만 무조건 지나간다. **schedule 도 오늘 브리핑이 이미
나갔으면 멈춘다** — GitHub cron 은 예약 시각에 안 오고(daily-brief.yml 머리말이
04:45~05:40 흔들림을 적어 두고 있다), 그 사이 crawl 복구가 먼저 보내고 나면 늦게
도착한 schedule 이 끝난 하루를 다시 만지게 된다. 같은 결함을 주간 쪽에서 먼저
고쳤다(tools/weekly_trigger_gate.py).

crawl 완료 복구는 아침 창 안에서만, 그리고 오늘 것이 아직 안 나갔을 때만 돈다.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


KST = timezone(timedelta(hours=9))


def _already_sent(outbox_path: Path, today: str) -> bool:
    try:
        outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        outbox = {}
    return outbox.get("date") == today and outbox.get("status") == "sent"


def decide(*, event_name: str, workflow_conclusion: str, now: datetime,
           outbox_path: Path, fallback_start_hour: int = 4,
           fallback_end_hour: int = 12) -> tuple[bool, str]:
    if event_name == "workflow_dispatch":
        return True, f"primary trigger: {event_name}"
    if event_name not in {"schedule", "workflow_run"}:
        return False, f"unsupported trigger: {event_name or 'missing'}"

    local_now = now.astimezone(KST)
    today = local_now.date().isoformat()
    # 자동 트리거는 전부 같은 문 앞에 선다 — 밀려서 도착한 schedule 도 마찬가지다.
    if _already_sent(outbox_path, today):
        return False, f"today's brief is already sent: {today}"
    if event_name == "schedule":
        return True, f"primary trigger: {event_name}"

    if workflow_conclusion != "success":
        return False, f"crawl conclusion is {workflow_conclusion or 'missing'}"
    if not fallback_start_hour <= local_now.hour < fallback_end_hour:
        return False, (
            f"outside fallback window: {local_now:%Y-%m-%d %H:%M KST} "
            f"({fallback_start_hour:02d}:00-{fallback_end_hour:02d}:00)"
        )
    return True, f"missed primary schedule fallback: {today}"


def classify_state(*, event_name: str, should_run: bool, now: datetime,
                   outbox_path: Path) -> str:
    if event_name == "workflow_dispatch":
        return "manual_trigger"
    if event_name not in {"schedule", "workflow_run"}:
        return "unsupported_trigger"
    if not should_run:
        # 늦은 schedule 이 멈춘 자리를 '복구 불필요'라고 부르면 로그만 보고는
        # cron 이 밀렸다는 사실을 못 읽는다. 주간 게이트와 같은 이름을 쓴다.
        return ("delivery_already_confirmed" if event_name == "schedule"
                else "recovery_not_needed")
    if event_name == "schedule":
        return "schedule_trigger_created"
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
