"""CLI adapter for :mod:`operational_monitoring`.

Run after ``news_bot.py`` to persist the latest ``source_yield`` snapshot, and
again after ``data_gate_metrics.py`` to evaluate daily quality records::

    python operational_alerts.py --notify

The command never raises into collection/deploy code.  Its CLI returns non-zero
when state persistence or a requested notification fails, so GitHub Actions can
show a warning while ``continue-on-error`` keeps the main pipeline running.
The public ``TELEGRAM_CHAT_ID`` is never a fallback.  No notification is marked
delivered until Telegram actually returns success.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

import operational_monitoring as monitor


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


ROOT = Path(__file__).parent
SENT_FILE = ROOT / "sent.json"
DELIVERY_LOG = ROOT / "delivery_log.jsonl"
KST = timezone(timedelta(hours=9))


def _read_object(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"[ops-monitor] 상태 읽기 실패, 파일 보존: {path.name}: {exc}")
        return None
    if not isinstance(value, dict):
        print(f"[ops-monitor] 상태 형식 오류, 파일 보존: {path.name}")
        return None
    return value


def _read_jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        print(f"[ops-monitor] 품질 로그 읽기 건너뜀: {exc}")
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _write_object(path: Path, value: dict) -> bool:
    """Atomic best-effort save; never replace a good file with partial JSON."""
    tmp = path.with_name(path.name + ".ops-monitor.tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError as exc:
        print(f"[ops-monitor] 상태 저장 실패(비치명): {exc}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def expected_source_specs() -> dict[str, str]:
    """Read the crawler's post-admin-override source list without duplicating it."""
    try:
        import news_bot
        return {
            **{str(row["name"]): (
                "official" if row.get("source_kind") == "official" else "feed")
               for row in news_bot.RSS_SOURCES if row.get("name")},
            **{str(row["name"]): "official" for row in news_bot.OFFICIAL_DIRECT_SOURCES
               if row.get("name")},
        }
    except Exception as exc:  # source snapshot itself still remains usable
        print(f"[ops-monitor] 수집원 목록 로드 실패, 관측된 이름만 사용: {exc}")
        return {}


def telegram_sender_from_env() -> Callable[[str], object] | None:
    """Return an admin-only sender when both dedicated values are present.

    ``TELEGRAM_CHAT_ID`` is the public briefing channel and is intentionally
    never used as a fallback.  A missing admin chat means log-only operation.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    admin_chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
    if not token or not admin_chat_id:
        return None

    def send(message: str) -> object:
        # Do not import telegram_send: its CHAT_ID is fixed to the public
        # briefing channel at import time.  This small adapter cannot silently
        # route an operational warning to subscribers.
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=urllib.parse.urlencode({
                "chat_id": admin_chat_id,
                "text": message,
                "disable_web_page_preview": "true",
            }).encode("utf-8"),
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Telegram admin API HTTP {exc.code}: {body}") from exc

    return send


def run(*, sent_path: Path = SENT_FILE, log_path: Path = DELIVERY_LOG,
        notify: bool = False, sender: Callable[[str], object] | None = None,
        expected_sources: object = None,
        pipeline_outcomes: dict[str, str] | None = None,
        pipeline_observation_id: str = "",
        collection_outcome: str | None = None,
        collection_observation_id: str = "",
        now: datetime | None = None) -> dict:
    """Process source health and today's quality events; never raises."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    state = _read_object(sent_path)
    if state is None:
        return {"ok": False, "saved": False, "due": 0, "sent": False}

    specs = expected_sources if expected_sources is not None else expected_source_specs()
    collection_failed = (collection_outcome is not None and
                         str(collection_outcome).strip().lower() != "success")
    if collection_failed:
        # A crashed collector may leave yesterday's source_yield untouched.
        # Do not count that stale snapshot as a fresh successful observation.
        raw_health = state.get("source_health")
        health = (dict(raw_health) if isinstance(raw_health, Mapping) else
                  {"version": monitor.STATE_VERSION, "sources": {}})
        source_processed = False
    else:
        health, source_processed = monitor.ingest_source_snapshot(
            state.get("source_health"), state.get("source_yield"), specs, now=now)
    state["source_health"] = health

    records = _read_jsonl(log_path)
    today = now.astimezone(KST).date().isoformat()
    quality_signals, quality_scopes = monitor.daily_quality_signals(records, today)
    source_signals = monitor.source_health_signals(health)
    pipeline_signals = monitor.web_pipeline_signals(
        pipeline_outcomes, observation_id=pipeline_observation_id)
    collection_signals = monitor.collection_pipeline_signals(
        collection_outcome, observation_id=collection_observation_id)
    signals = source_signals + quality_signals + pipeline_signals + collection_signals
    scopes = set(quality_scopes)
    if source_processed:
        scopes.add("source")
    # Supplying outcomes means the web pipeline was expected and was fully
    # evaluated.  An all-success run has no signal and therefore resolves the
    # previous incident; invocations from crawl/brief checks omit this scope.
    if pipeline_outcomes is not None:
        scopes.add("web_pipeline")
    if collection_outcome is not None:
        scopes.add("collection_pipeline")

    alert_state, due = monitor.evaluate_alerts(
        signals, state.get("operational_alerts"), evaluated_scopes=scopes, now=now)
    state["operational_alerts"] = alert_state

    notification = {"sent": False, "count": len(due), "error": ""}
    if due:
        print(monitor.format_admin_alerts(due))
        active_sender = sender or (telegram_sender_from_env() if notify else None)
        if active_sender is None:
            reason = "Telegram 환경변수 없음" if notify else "--notify 미지정"
            print(f"[ops-monitor] {reason} — 로그만 남기고 계속")
            if notify:
                notification["error"] = "admin_sender_unavailable"
        else:
            alert_state, notification = monitor.notify_alerts(
                alert_state, due, active_sender, now=now)
            state["operational_alerts"] = alert_state
            if notification["sent"]:
                print(f"[ops-monitor] 관리자 알림 {notification['count']}건 발송")
            else:
                print(f"[ops-monitor] 관리자 알림 실패(비치명): {notification['error']}")
                print(f"::warning::관리자 Telegram 알림 실패: {notification['error']}")
    else:
        print("[ops-monitor] 새로 알릴 운영 품질 이상 없음")

    saved = _write_object(sent_path, state)
    return {
        "ok": True, "saved": saved, "source_processed": source_processed,
        "signals": len(signals), "due": len(due), "sent": bool(notification["sent"]),
        "notification_error": notification.get("error", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="수집원 상태·품질 이상 관리자 알림")
    parser.add_argument("--notify", action="store_true",
                        help="Telegram 환경변수가 있으면 관리자에게 묶어서 발송")
    parser.add_argument("--sent", type=Path, default=SENT_FILE)
    parser.add_argument("--delivery-log", type=Path, default=DELIVERY_LOG)
    parser.add_argument("--web-build-outcome",
                        choices=("success", "failure", "cancelled", "skipped"))
    parser.add_argument("--data-gate-outcome",
                        choices=("success", "failure", "cancelled", "skipped"))
    parser.add_argument("--web-deploy-outcome",
                        choices=("success", "failure", "cancelled", "skipped"))
    parser.add_argument("--pipeline-observation-id", default="",
                        help="재시도 중복을 막는 GitHub workflow run 식별자")
    parser.add_argument("--collect-outcome",
                        choices=("success", "failure", "cancelled", "skipped"))
    parser.add_argument("--collection-observation-id", default="",
                        help="수집 step 재시도 중복을 막는 workflow run 식별자")
    args = parser.parse_args()
    pipeline_outcomes = None
    raw_outcomes = {
        "web_build": args.web_build_outcome,
        "data_gate": args.data_gate_outcome,
        "web_deploy": args.web_deploy_outcome,
    }
    if any(value is not None for value in raw_outcomes.values()):
        pipeline_outcomes = {
            key: value or "missing" for key, value in raw_outcomes.items()}
    try:
        result = run(
            sent_path=args.sent, log_path=args.delivery_log, notify=args.notify,
            pipeline_outcomes=pipeline_outcomes,
            pipeline_observation_id=args.pipeline_observation_id,
            collection_outcome=args.collect_outcome,
            collection_observation_id=args.collection_observation_id,
        )
    except Exception as exc:  # monitoring must never make collection/deploy red
        print(f"[ops-monitor] 예상하지 못한 실패(비치명): {type(exc).__name__}: {exc}")
        print(f"::warning::운영 모니터 자체 오류: {type(exc).__name__}: {exc}")
        return 1
    if not result.get("ok") or not result.get("saved"):
        print("::warning::운영 모니터 상태를 저장하지 못했습니다.")
        return 1
    if args.notify and result.get("due") and not result.get("sent"):
        print(f"::warning::관리자 경고 {result['due']}건이 미발송 상태로 남았습니다.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
