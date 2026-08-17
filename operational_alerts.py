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


# ── 관리자 채팅 진단 ────────────────────────────────────────────────────────
#
# 텔레그램이 주는 말은 `chat not found` 한 줄이다. 그 한 줄은 **무엇을 고쳐야
# 하는지 아무것도 말하지 않는다** — 그래서 이 경고는 3시간마다 새로 찍히면서
# 몇 주씩 방치된다(실측 2026-08-17: 회차마다 같은 400 이 반복).
#
# 여기서 하는 일은 원인을 좁혀 주는 것이다. 다만 **값 자체는 절대 로그에 남기지
# 않는다** — Actions 로그는 저장소를 보는 사람 누구나 읽고, 채팅 ID 는 그 자체로
# 대화 상대를 특정한다. 값의 '모양'만 말한다.

# 재시도로 낫지 않는 응답. 나머지(5xx·네트워크)는 다음 회차에 저절로 풀릴 수 있다.
PERMANENT_CHAT_ERRORS = (
    "chat not found",
    "bot was blocked by the user",
    "bot was kicked",
    "user is deactivated",
    "not enough rights",
    "have no rights",
    "bot can't initiate conversation",
)


def is_permanent_chat_error(description: str) -> bool:
    lowered = str(description or "").lower()
    return any(hint in lowered for hint in PERMANENT_CHAT_ERRORS)


def describe_admin_chat_id(raw: object, public_chat_id: object = "") -> str:
    """설정값의 **모양**만 보고 다음에 확인할 것을 한 줄로 말한다.

    실제로 자주 나는 원인 순서대로 본다. 첫 번째(공백·따옴표가 섞인 값)는
    시크릿을 붙여 넣을 때 생기고, 화면에서는 보이지 않아 제일 오래 산다.
    """
    value = str(raw or "")
    stripped = value.strip()
    if not stripped:
        return "값이 비어 있습니다 — 시크릿이 등록되지 않았거나 빈 문자열입니다."
    if stripped != value or any(ch in stripped for ch in "'\"\n\r\t "):
        return ("값에 공백·따옴표가 섞여 있습니다 — 시크릿을 따옴표 없이 한 줄로 "
                "다시 등록하세요(붙여 넣을 때 가장 흔한 원인입니다).")
    if stripped.startswith("@"):
        return ("채널 username 형태입니다 — 공개 채널이어야 하고 봇이 그 채널의 "
                "관리자여야 합니다. 비공개 채널이면 숫자 ID(-100…)를 쓰세요.")
    if stripped.startswith("-100"):
        return ("슈퍼그룹·채널 ID 형태입니다 — 봇이 그 방에 아직 있는지, "
                "내보내진 것은 아닌지 확인하세요.")
    if stripped.startswith("-"):
        return ("옛 그룹 ID 형태입니다 — 그룹이 슈퍼그룹으로 승격되면 ID 가 "
                "`-100…` 으로 **바뀝니다.** 새 ID 로 갱신하세요.")
    if not stripped.lstrip("-").isdigit():
        return "숫자도 @username 도 아닙니다 — 채팅 ID 형식이 아닙니다."
    if stripped == str(public_chat_id or "").strip():
        return ("공개 채널과 같은 ID 인데 그 방에도 닿지 못했습니다 — 봇 토큰이 "
                "바뀌었거나 대화가 삭제됐는지 확인하세요.")
    return ("개인 대화 ID 형태입니다 — 그 사람이 봇에게 먼저 /start 를 눌러야 "
            "봇이 말을 걸 수 있습니다. 아직 누른 적이 없다면 이 오류가 납니다.")


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
        # Deliberately not telegram_send: its default target is the public
        # briefing chat.  This adapter takes no chat argument at all, so it
        # cannot silently route an operational warning to subscribers.
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
            description = body
            try:
                description = json.loads(body).get("description") or body
            except (json.JSONDecodeError, AttributeError):
                pass
            if is_permanent_chat_error(description):
                # 재시도로 낫지 않는다. 텔레그램이 준 말 뒤에 **다음에 할 일**을
                # 붙인다 — 그러지 않으면 같은 400 이 3시간마다 새로 찍히기만 한다.
                raise RuntimeError(
                    f"Telegram admin API HTTP {exc.code}: {description} "
                    f"— TELEGRAM_ADMIN_CHAT_ID 설정 문제입니다. "
                    f"{describe_admin_chat_id(admin_chat_id, os.environ.get('TELEGRAM_CHAT_ID'))}"
                ) from exc
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
                # 설정 오류와 일시 장애를 가른다. 둘 다 warning 으로 찍으면 3시간
                # 마다 같은 줄이 쌓이면서 **고쳐야 낫는 것**과 **기다리면 낫는
                # 것**이 섞이고, 그러면 둘 다 안 읽힌다. 경고는 그대로 두되(알림은
                # 유실되지 않고 다음 회차에 재시도된다) 사유만 나눈다.
                if is_permanent_chat_error(notification["error"]):
                    print(f"::error::관리자 Telegram 알림이 설정 때문에 막혀 있습니다 "
                          f"— 재시도로 낫지 않습니다: {notification['error']}")
                    print("[ops-monitor] 확인: python operational_alerts.py --check-admin-chat")
                else:
                    print(f"::warning::관리자 Telegram 알림 실패: {notification['error']}")
    else:
        print("[ops-monitor] 새로 알릴 운영 품질 이상 없음")

    saved = _write_object(sent_path, state)
    return {
        "ok": True, "saved": saved, "source_processed": source_processed,
        "signals": len(signals), "due": len(due), "sent": bool(notification["sent"]),
        "notification_error": notification.get("error", ""),
    }


def check_admin_chat() -> int:
    """관리자 채팅이 닿는지 **메시지를 보내지 않고** 확인한다.

    `getChat` 은 읽기 전용이라 시험 삼아 눌러도 상대에게 아무것도 안 간다.
    시크릿을 바꾼 뒤 다음 크롤(최대 3시간)을 기다려야 맞았는지 아는 구조였는데,
    그 왕복이 길어서 설정 오류가 오래 산다.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    admin_chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
    if not token:
        print("TELEGRAM_BOT_TOKEN 이 없습니다.")
        return 1
    if not admin_chat_id:
        print("TELEGRAM_ADMIN_CHAT_ID 가 없습니다 — 관리자 알림은 로그만 남습니다.")
        return 1

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getChat?"
        + urllib.parse.urlencode({"chat_id": admin_chat_id}))
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"확인 실패(네트워크): {type(exc).__name__}: {exc}")
        return 1

    if payload.get("ok"):
        chat = payload.get("result") or {}
        # 방 이름까지만 적는다 — ID 는 로그에 남기지 않는다.
        label = chat.get("title") or chat.get("username") or chat.get("first_name") or "이름 없음"
        print(f"관리자 채팅 확인됨: {chat.get('type', '?')} · {label}")
        return 0

    description = payload.get("description") or "알 수 없는 오류"
    print(f"관리자 채팅에 닿지 못했습니다: {description}")
    print(describe_admin_chat_id(admin_chat_id, os.environ.get("TELEGRAM_CHAT_ID")))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="수집원 상태·품질 이상 관리자 알림")
    parser.add_argument("--notify", action="store_true",
                        help="Telegram 환경변수가 있으면 관리자에게 묶어서 발송")
    parser.add_argument("--check-admin-chat", action="store_true",
                        help="관리자 채팅이 닿는지만 확인한다(메시지를 보내지 않음)")
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
    if args.check_admin_chat:
        return check_admin_chat()
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
