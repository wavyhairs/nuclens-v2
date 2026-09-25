"""Gate every Weekly workflow trigger against "did this week already go out?".

사람이 손으로 돌린 실행만 무조건 지나간다. **나머지는 전부 — 금요일 schedule 도 —
현재 ISO 주차의 Telegram 발송이 확인되지 않았을 때만 실행한다.**

schedule 을 예외로 둘 수 없는 이유는 이 저장소의 cron 이 제시간에 안 오기
때문이다. 2026-09-04 와 09-11 은 둘 다 저녁 crawl recovery 가 발송을 끝낸 뒤,
4시간 반 밀린 금요일 schedule 이 21시대에 들어와 이미 끝난 주차를 다시 확정했다.
발송 자체는 멱등 경로가 막았지만 상태·시각·운영 로그가 흔들렸고, 무엇보다 몇 분과
한 번의 웹 배포를 매주 헛으로 태웠다.

자동 호출자는 자기를 밝힌다(``trigger_source``). crawl.yml 의 backup_watchdog 과
같은 규약이다 — 사람의 workflow_dispatch 와 Worker 의 workflow_dispatch 를 가르는
것은 이벤트 이름이 아니라 그 한 줄이다.

crawl 완료 recovery 는 금요일 저녁~일요일 오전만 열어 낡은 발송을 막는다. 채널
상태는 채널이 설정돼 있을 때만 함께 본다.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


KST = timezone(timedelta(hours=9))

# 이 값으로 자기를 밝힌 workflow_dispatch 는 '사람의 수동 실행'이 아니라 자동
# 안전망이다. crawl.yml 의 독립 Worker 가 쓰는 이름을 그대로 쓴다.
BACKUP_TRIGGER_SOURCE = "backup_watchdog"


# 주간 경계 — weekly_bot.WEEK_CUTOFF_WEEKDAY / WEEK_CUTOFF_TIME 과 같은 값이어야 한다
# (테스트가 대조한다). 이 게이트는 의존성 설치 전에 돌아서 weekly_bot 을 import 하지 않는다.
CUTOFF_WEEKDAY = 4          # Friday
CUTOFF_HOUR, CUTOFF_MINUTE = 17, 5


def _cutoff(now: datetime) -> datetime:
    local = now.astimezone(KST)
    back = (local.weekday() - CUTOFF_WEEKDAY) % 7
    cutoff = (local - timedelta(days=back)).replace(
        hour=CUTOFF_HOUR, minute=CUTOFF_MINUTE, second=0, microsecond=0)
    return cutoff if cutoff <= local else cutoff - timedelta(days=7)


def _week_id(now: datetime) -> str:
    """리포트 저장 키와 같은 규칙 — 실행 시각이 아니라 경계가 속한 ISO 주차."""
    year, week, _ = _cutoff(now).isocalendar()
    return f"{year}-W{week:02d}"


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def delivery_snapshot(*, now: datetime, reports_path: Path, channel_path: Path,
                      channel_required: bool) -> dict:
    """현재 ISO 주차가 어디까지 나갔는가.

    '나갔는가'의 판정은 이 함수 하나만 갖는다. 게이트(실행할까)와 운영 알림
    (안 나갔다고 알릴까)이 서로 다른 규칙을 들면, 둘 중 하나는 반드시 틀린
    말을 하게 된다.
    """
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
    return {
        "week_id": key, "dm": dm, "channel": channel,
        "complete": dm == "sent" and (not channel_required or channel == "sent"),
    }


def delivery_state(*, now: datetime, reports_path: Path, channel_path: Path,
                   channel_required: bool) -> tuple[bool, str]:
    snapshot = delivery_snapshot(
        now=now, reports_path=reports_path, channel_path=channel_path,
        channel_required=channel_required)
    return snapshot["complete"], (
        f"week={snapshot['week_id']} dm={snapshot['dm']} "
        f"channel={snapshot['channel']}")


def _in_recovery_window(now: datetime) -> bool:
    local = now.astimezone(KST)
    weekday = local.weekday()  # Monday=0
    # 경계(금 17:05) 전의 금요일 복구는 **지난주** 창을 만든다 — 열지 않는다.
    return ((weekday == CUTOFF_WEEKDAY
             and (local.hour, local.minute) >= (CUTOFF_HOUR, CUTOFF_MINUTE))
            or weekday == 5
            or (weekday == 6 and local.hour < 12))


def decide(*, event_name: str, workflow_conclusion: str, now: datetime,
           reports_path: Path, channel_path: Path,
           channel_required: bool = True,
           trigger_source: str = "") -> tuple[bool, str, str]:
    complete, detail = delivery_state(
        now=now, reports_path=reports_path, channel_path=channel_path,
        channel_required=channel_required)
    # 사람의 수동 실행만 질문 없이 지나간다 — 그것이 수동 실행의 목적이다.
    if event_name == "workflow_dispatch" and trigger_source != BACKUP_TRIGGER_SOURCE:
        return True, "manual_trigger", f"primary trigger: {event_name}; {detail}"
    if event_name not in {"schedule", "workflow_run", "workflow_dispatch"}:
        return False, "unsupported_trigger", f"unsupported trigger: {event_name or 'missing'}"
    # **모든 자동 트리거가 같은 문 앞에 선다.** 늦은 schedule 도 예외가 아니다.
    if complete:
        return False, "delivery_already_confirmed", detail
    if event_name == "schedule":
        return True, "schedule_trigger_created", f"primary trigger: schedule; {detail}"
    if event_name == "workflow_run" and workflow_conclusion != "success":
        return False, "recovery_source_failed", (
            f"crawl conclusion is {workflow_conclusion or 'missing'}")
    if not _in_recovery_window(now):
        return False, "outside_recovery_window", (
            f"outside Friday 17:05-Sunday 12:00 KST; {detail}")
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
    parser.add_argument("--trigger-source",
                        default=os.environ.get("TRIGGER_SOURCE", ""))
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
        trigger_source=args.trigger_source,
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
