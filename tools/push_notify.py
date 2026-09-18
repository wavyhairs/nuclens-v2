"""아침 알림을 한 번 보낸다 — 07:00 KST.

무엇을 하나
-----------
엣지의 발송 창구(`/push/send`)를 커서가 빌 때까지 부른다. 서명도 발송도 거기서
한다(`functions/push/send.js`) — 이 스크립트는 **언제 보낼지**와 **보낼 값이
있는지**만 판단한다. 그 둘이 파이썬에 남은 이유는, 둘 다 저장소의 사실(브리핑
날짜)과 운영 판단(시각)이지 암호 연산이 아니기 때문이다.

보내지 않는 두 경우
-------------------
1. **오늘 브리핑이 아직 없다.** 발송 시각이 밀리거나 아침 빌드가 실패하면
   어제 것을 오늘 아침 알림으로 보내게 된다. `push.json` 의 날짜가 오늘(KST)이
   아니면 보내지 않는다 — 늦는 것이 틀린 것보다 낫다.
2. **알림 시간이 아니다.** GitHub cron 은 예약대로 뜨지 않는다(이 저장소 실측:
   15~66분 지연). 07:00 을 겨냥해 예약하되, 실제 도착이 창 밖이면 보내지 않는다.
   점심에 도착한 '아침 알림'은 알림이 아니라 방해다.

둘 다 조용히 넘어간다(종료 코드 0). 알림을 못 보낸 것은 사고가 아니라 판단이고,
워크플로를 빨갛게 만들면 진짜 고장과 구분되지 않는다.

필요한 것
---------
`PUSH_SEND_TOKEN`  Cloudflare 와 같은 값(GitHub 저장소 Secret).
`SITE_URL`         기본 https://nuclens-v2.pages.dev

    python tools/push_notify.py               # 창·날짜를 다 본다
    python tools/push_notify.py --force       # 둘 다 무시하고 보낸다(수동 점검)
    python tools/push_notify.py --dry-run     # 판단만 하고 끝낸다
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta, timezone

try:  # 저장소 다른 모듈과 같은 처리 — 한국어 진단이 cp1252 에서 터지지 않게.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:  # pragma: no cover
    pass

KST = timezone(timedelta(hours=9))
DEFAULT_SITE = "https://nuclens-v2.pages.dev"

# 알림이 와도 되는 창. 시작은 사용자가 정한 시각이고, 끝은 "이 시간을 넘으면
# 아침 알림이 아니다"는 선이다. cron 지연 실측(15~66분)이 이 안에 들어간다.
SEND_AT = time(7, 0)
WINDOW_END = time(9, 30)

BATCH_LIMIT = 100
# 한 번에 다 돌지 않는다 — 커서가 안 줄어드는 사고에서 무한 반복을 막는 상한이다.
MAX_BATCHES = 60


def _get_json(url: str, timeout: int = 20) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "nuclens-push/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, token: str, payload: dict, timeout: int = 60) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "nuclens-push/1",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def within_window(now: datetime) -> bool:
    """알림을 보내도 되는 시각인가."""
    return SEND_AT <= now.astimezone(KST).timetz().replace(tzinfo=None) <= WINDOW_END


def card_is_todays(card: dict, now: datetime) -> bool:
    """굽힌 알림 카드가 **오늘** 것인가."""
    return str(card.get("date") or "") == now.astimezone(KST).date().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="아침 알림 발송")
    parser.add_argument("--force", action="store_true",
                        help="시각·날짜 판단을 무시하고 보낸다")
    parser.add_argument("--dry-run", action="store_true",
                        help="판단만 하고 보내지 않는다")
    args = parser.parse_args()

    site = os.environ.get("SITE_URL", DEFAULT_SITE).rstrip("/")
    token = os.environ.get("PUSH_SEND_TOKEN", "")
    now = datetime.now(KST)

    if not args.force and not within_window(now):
        print(f"[push] {now:%H:%M} KST — 알림 창({SEND_AT:%H:%M}~{WINDOW_END:%H:%M}) 밖이라 보내지 않는다")
        return 0

    try:
        card = _get_json(f"{site}/data/push.json?cb={int(now.timestamp())}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[push] push.json 을 못 읽었다 ({type(exc).__name__}: {exc}) — 보내지 않는다")
        return 0

    if not args.force and not card_is_todays(card, now):
        print(f"[push] 카드 날짜 {card.get('date') or '(없음)'} — 오늘 브리핑이 아직 없다. 보내지 않는다")
        return 0

    print(f"[push] 보낼 것: {card.get('title')} · {card.get('body')}")
    if args.dry_run:
        print("[push] --dry-run — 여기서 멈춘다")
        return 0
    if len(token) < 16:
        print("[push] PUSH_SEND_TOKEN 이 없다(또는 너무 짧다) — 보내지 않는다", file=sys.stderr)
        return 1

    sent = pruned = failed = 0
    cursor = ""
    reasons: list[str] = []
    for batch in range(MAX_BATCHES):
        payload = {"limit": BATCH_LIMIT, "topic": str(card.get("tag") or "nuclens-brief")}
        if cursor:
            payload["cursor"] = cursor
        try:
            result = _post_json(f"{site}/push/send", token, payload)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:200]
            print(f"[push] 발송 창구가 {exc.code} 를 냈다 — {body}", file=sys.stderr)
            return 1
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"[push] 발송 실패 ({type(exc).__name__}: {exc})", file=sys.stderr)
            return 1
        sent += int(result.get("sent") or 0)
        pruned += int(result.get("pruned") or 0)
        failed += int(result.get("failed") or 0)
        reasons.extend(result.get("reasons") or [])
        cursor = str(result.get("cursor") or "")
        if result.get("done") or not cursor:
            break
    else:
        print(f"[push] 배치 상한 {MAX_BATCHES} 에 걸렸다 — 커서가 안 줄고 있다", file=sys.stderr)

    print(f"[push] 보냄 {sent} · 죽은 구독 정리 {pruned} · 실패 {failed}")
    if reasons:
        print(f"[push] 실패 사유: {', '.join(sorted(set(reasons))[:5])}")
    # 구독자가 0명인 것은 실패가 아니다. 실패만 있는 경우가 실패다.
    return 1 if failed and not sent else 0


if __name__ == "__main__":
    raise SystemExit(main())
