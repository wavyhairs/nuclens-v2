"""Decide whether a Daily Brief workflow trigger should run the full job.

사람이 손으로 돌린 실행만 무조건 지나간다. **schedule 도 오늘 브리핑이 이미
나갔으면 멈춘다** — GitHub cron 은 예약 시각에 안 오고(daily-brief.yml 머리말이
04:45~05:40 흔들림을 적어 두고 있다), 그 사이 crawl 복구가 먼저 보내고 나면 늦게
도착한 schedule 이 끝난 하루를 다시 만지게 된다. 같은 결함을 주간 쪽에서 먼저
고쳤다(tools/weekly_trigger_gate.py).

crawl 완료 복구는 아침 창 안에서만, 그리고 오늘 것이 아직 안 나갔을 때만 돈다.

**오디오 복구** (2026-09-25) — 오늘 브리핑은 나갔는데 라이브 사이트에 오늘
오디오(빠른·전문가)가 없거나 전달에 실패했으면, 같은 아침 창 안의 crawl 완료가
한 번 더 돌린다. 그날 전문가 브리핑은 Gemini 503 과부하로 죽었고, 워크플로 안의
재시도(수 초·90초)는 전부 같은 과부하 속에 있었다. 과부하는 몇 시간이면 풀리는데
그 몇 시간 뒤를 부를 사람이 없었다. 수동 `audio_recovery` 와 같은 길(`--recover`)
을 탄다 — 이미 만든 mp3 는 다시 만들지 않고, 나간 텍스트는 다시 보내지 않는다.
판정 재료는 git 이 아니라 **라이브 audio.json** 이다(mp3·매니페스트는 git 에 없다).
못 읽으면 복구하지 않는다 — 판정 불가를 이유로 무거운 잡을 반복하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path


KST = timezone(timedelta(hours=9))

AUDIO_VARIANTS = ("fast", "expert")
# audio_brief.DELIVERY_* 중 '구독자에게 못 닿음'. preflight 는 의존성 설치 없이
# 돌아서 audio_brief 를 import 하지 않는다 — 같은 값인지는 테스트가 확인한다.
AUDIO_DELIVERY_FAILED = frozenset({"telegram_failed", "no_file_id", "queue_failed"})
AUDIO_RECOVERY_STATE = "audio_recovery"


def _already_sent(outbox_path: Path, today: str) -> bool:
    try:
        outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        outbox = {}
    return outbox.get("date") == today and outbox.get("status") == "sent"


def missing_audio(manifest: object, today: str) -> list[str]:
    """라이브 매니페스트에서 오늘 것이 빠졌거나 전달에 실패한 변형."""
    if not isinstance(manifest, dict):
        return []
    variants = manifest.get("variants") or {}
    missing: list[str] = []
    for key in AUDIO_VARIANTS:
        row = variants.get(key) or {}
        if row.get("date") != today:
            missing.append(key)
        elif str((row.get("delivery") or {}).get("state") or "") in AUDIO_DELIVERY_FAILED:
            missing.append(key)
    return missing


def fetch_live_audio_manifest(site_url: str, timeout: float = 15.0) -> dict | None:
    if not site_url:
        return None
    url = f"{site_url.rstrip('/')}/data/audio/audio.json?cb={int(time.time())}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[daily-brief-gate] live audio manifest unavailable: {type(exc).__name__}: {exc}")
        return None
    return data if isinstance(data, dict) else None


def decide(*, event_name: str, workflow_conclusion: str, now: datetime,
           outbox_path: Path, fallback_start_hour: int = 4,
           fallback_end_hour: int = 12,
           audio_manifest_loader: Callable[[], object] | None = None,
           ) -> tuple[bool, str]:
    if event_name == "workflow_dispatch":
        return True, f"primary trigger: {event_name}"
    if event_name not in {"schedule", "workflow_run"}:
        return False, f"unsupported trigger: {event_name or 'missing'}"

    local_now = now.astimezone(KST)
    today = local_now.date().isoformat()
    in_window = fallback_start_hour <= local_now.hour < fallback_end_hour
    # 자동 트리거는 전부 같은 문 앞에 선다 — 밀려서 도착한 schedule 도 마찬가지다.
    if _already_sent(outbox_path, today):
        # 오디오 복구는 crawl 완료만 한다. 창 밖이면 묻지도 않는다 — 오후에 나간
        # 아침 오디오는 의미가 적고, 라이브 조회도 필요할 때만 한다.
        if (event_name == "workflow_run" and workflow_conclusion == "success"
                and in_window and audio_manifest_loader is not None):
            missing = missing_audio(audio_manifest_loader(), today)
            if missing:
                return True, f"audio recovery: {','.join(missing)} missing on live for {today}"
        return False, f"today's brief is already sent: {today}"
    if event_name == "schedule":
        return True, f"primary trigger: {event_name}"

    if workflow_conclusion != "success":
        return False, f"crawl conclusion is {workflow_conclusion or 'missing'}"
    if not in_window:
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
    if outbox.get("status") == "sent":
        # 오늘 것이 나갔는데도 돈다면 decide 가 오디오 복구를 골랐을 때뿐이다.
        return AUDIO_RECOVERY_STATE
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
    parser.add_argument("--site-url", default=os.environ.get("SITE_URL", ""))
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    should_run, reason = decide(
        event_name=args.event_name,
        workflow_conclusion=args.workflow_conclusion,
        now=now,
        outbox_path=args.outbox,
        fallback_start_hour=args.fallback_start_hour,
        fallback_end_hour=args.fallback_end_hour,
        audio_manifest_loader=((lambda: fetch_live_audio_manifest(args.site_url))
                               if args.site_url else None),
    )
    state = classify_state(
        event_name=args.event_name, should_run=should_run, now=now,
        outbox_path=args.outbox)
    value = str(should_run).lower()
    print(f"[daily-brief-gate] should_run={value} state={state} — {reason}")
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            audio_recovery = str(state == AUDIO_RECOVERY_STATE).lower()
            handle.write(f"should_run={value}\ntrigger_state={state}\n"
                         f"audio_recovery={audio_recovery}\n")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write("### Daily automation status\n\n")
            handle.write(f"- trigger: `{state}`\n- decision: `{reason}`\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
