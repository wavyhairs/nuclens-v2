"""아침 알림 — 브리핑이 나간 뒤 구독자 전원에게 "오늘 브리핑" 한 번.

무엇을 하나
-----------
`/push/list` 에서 구독자를 받아 `pywebpush` 로 **각 endpoint 에 직접** 보낸다.
알림에 보일 제목·본문이 푸시 본문 안에 그대로 실린다(RFC 8291 암호화는
pywebpush 가 한다). 서비스워커는 받은 것을 그대로 띄우기만 한다.

왜 엣지가 아니라 여기서 보내는가
--------------------------------
예전 판은 엣지(`functions/push/send.js`)가 서명하고 **본문 없는** 알림을 보냈고,
서비스워커가 받는 순간 `/data/push.json` 을 다시 읽어 제목을 붙였다. 검증 못 할
암호 구현을 배포 경로에 두지 않으려던 선택이었지만, 대가가 셋이었다: 알림 하나에
왕복이 한 번 더 붙고(그 왕복이 실패하면 일반 문구만 뜬다), 서명·암호화가 Worker
CPU 예산 위에 얹히고, 발송 경로가 파이썬·자바스크립트 두 곳으로 갈렸다.
pywebpush 는 그 셋을 한 번에 지운다 — 암호 구현은 이미 검증된 라이브러리가 하고,
러너에는 CPU 예산이 없고, 발송은 이 파일 하나다.

언제 보내는가
-------------
**Daily Brief 워크플로 안에서** 배포가 끝난 뒤 이어서 돈다. 예전엔 07:00 KST 전용
cron 이 따로 있었고 "지금이 아침인가"를 이 스크립트가 판단했다. 그 판단을 걷은
이유는, 알림이 와야 하는 때란 사람이 정한 시각이 아니라 **오늘 브리핑이 실제로
올라온 때**이기 때문이다. 브리핑이 늦으면 알림도 늦는 것이 맞고, 브리핑이 없으면
알림도 없는 것이 맞다 — 창(window) 판단은 그 둘을 흉내 내던 대역이었다.

보내지 않는 경우
----------------
* 구독자 0명 — 정상이다. 조용히 끝낸다.
* 오늘 것을 이미 보냈다 — outbox 의 기록을 본다(재실행 중복 방지). `--force` 로 넘는다.
* 설정이 없다 — **운영 오류로 남긴다.** 설정을 깜빡한 것이 조용히 '알림 없는
  서비스'가 되면 안 된다. 스텝은 continue-on-error 라 브리핑을 죽이지는 않는다.

환경
----
`PUSH_ADMIN_TOKEN`   Cloudflare Pages 시크릿과 **같은 값**(GitHub Secret).
`VAPID_PRIVATE_KEY`  base64url 32바이트 또는 PEM (GitHub Secret).
`VAPID_SUBJECT`      `mailto:<연락처>` (GitHub Variable).
`SITE_URL`           기본 https://nuclens-v2.pages.dev

    python tools/push_notify.py               # 평소
    python tools/push_notify.py --force       # 중복 기록을 무시하고 보낸다
    python tools/push_notify.py --dry-run     # 보낼 것만 찍고 끝낸다
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

try:  # 저장소 다른 모듈과 같은 처리 — 한국어 진단이 cp1252 에서 터지지 않게.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parents[1]
BRIEFINGS_FILE = ROOT / "web" / "public" / "data" / "briefings.json"
OUTBOX_FILE = ROOT / "outbox.json"

DEFAULT_SITE = "https://nuclens-v2.pages.dev"
DEFAULT_SUBJECT = "mailto:nuclens@users.noreply.github.com"

MAX_TITLES = 3
TITLE_CHARS = 40
FALLBACK_TITLE = "Nuclens 오늘 브리핑"
FALLBACK_BODY = "오늘의 원전 현안이 올라왔습니다."
PUSH_URL = "/?src=push"

# 목록은 한 번에 오고(구독 수만큼 자란다), 발송은 구독마다 한 번이다. 둘의
# 성질이 달라 상한도 다르다 — 발송 하나가 오래 물리면 나머지가 그만큼 밀린다.
LIST_TIMEOUT = 30
SEND_TIMEOUT = 15
PRUNE_TIMEOUT = 15
# 아침 알림은 반나절 지나면 보낼 이유가 없다. 꺼진 폰이 저녁에 켜지면 그때 온다.
PUSH_TTL = 6 * 3600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_payload(briefings: list[dict], brief_date: str | None = None) -> dict:
    """알림 본문 = 그날 상위 3건 제목. **여기서 문장을 새로 짓지 않는다** —
    화면이 정한 순서와 제목을 그대로 쓴다. 알림이 화면과 다른 말을 하면 누른
    뒤에 배신당한 기분이 든다.

    briefings.json 은 최신이 먼저다(V2). 날짜로 세워 마지막을 쓰므로 그 순서가
    뒤집혀도 같은 날을 고른다 — 데이터 순서에 기대지 않는다.
    """
    days = sorted((b for b in briefings if b.get("issues")),
                  key=lambda b: str(b.get("date") or ""))
    if brief_date:
        days = [b for b in days if str(b.get("date") or "") == brief_date] or days
    if not days:
        return {"title": FALLBACK_TITLE, "body": FALLBACK_BODY,
                "url": PUSH_URL, "tag": "nuclens-brief"}
    day = days[-1]
    date_text = str(day.get("date") or "")
    issues = day.get("issues") or []
    titles = [str(issue.get("title") or "").strip() for issue in issues[:MAX_TITLES]]
    body = "\n".join(f"{n}. {title[:TITLE_CHARS]}"
                     for n, title in enumerate(titles, 1) if title)
    label = date_text[5:].replace("-", "/") if len(date_text) >= 10 else ""
    return {
        "title": f"Nuclens {label} 브리핑 · {len(issues)}건" if label else FALLBACK_TITLE,
        "body": body or FALLBACK_BODY,
        "url": PUSH_URL,
        "tag": f"nuclens-brief-{date_text}" if date_text else "nuclens-brief",
    }


def payload_date(payload: dict) -> str:
    """알림 tag 에서 브리핑 날짜를 되읽는다 — 중복 판단의 근거."""
    tag = str(payload.get("tag") or "")
    return tag[len("nuclens-brief-"):] if tag.startswith("nuclens-brief-") else ""


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def already_notified(outbox: dict, brief_date: str) -> bool:
    """오늘 것을 이미 보냈는가.

    별도 상태 시스템을 두지 않는다 — outbox 는 이미 "그날 무엇이 나갔는가"의
    기록이고, Daily Brief 의 뒤 스텝이 이미 커밋한다. 한 칸을 빌린다.
    """
    if not brief_date:
        return False
    record = outbox.get("push") if isinstance(outbox, dict) else None
    return isinstance(record, dict) and str(record.get("date") or "") == brief_date


def record_notified(path: Path, brief_date: str, *, sent: int, total: int) -> None:
    """발송 사실을 outbox 에 적는다. 실패해도 넘어간다 — 알림은 이미 나갔고,
    기록을 못 남긴 것 때문에 스텝을 죽이면 그쪽이 더 나쁜 거짓말이다."""
    outbox = _read_json(path, None)
    if not isinstance(outbox, dict):
        return
    outbox["push"] = {
        "date": brief_date,
        "notified_at": _now().isoformat(),
        "sent": sent,
        "subscriptions": total,
    }
    try:
        path.write_text(json.dumps(outbox, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except OSError as exc:  # pragma: no cover — 러너 디스크 사고
        print(f"[push] outbox 기록 실패({type(exc).__name__}) — 발송은 끝났다")


def clean_subject(raw: str) -> str:
    """VAPID `sub` 클레임 — 푸시 서비스가 문제 생겼을 때 연락할 곳.

    `mailto:<이메일>` 이거나 `https://<주소>` 여야 한다. **앞뒤 공백을 걷는다** —
    설정 화면에 붙여넣을 때 딸려 들어온 스페이스 하나로 `py_vapid` 가
    "Missing 'sub' from claims" 를 던지고, 그 예외는 구독자마다 한 번씩 쌓여
    '전원 발송 실패'로만 보인다. 공백 하나를 그 증상에서 역추적하는 것은 비싸다
    (2026-09-19 실측: ' mailto:…' · 'mailto:https://…' 둘 다 서명 단계에서 사망).

    모양이 아예 틀리면 빈 문자열을 낸다 — 부르는 쪽이 **구독자를 부르기 전에**
    멈추고 무엇이 잘못됐는지 말한다.
    """
    value = str(raw or "").strip()
    if value.startswith("mailto:") and "@" in value[7:]:
        return value
    if value.startswith("https://") and len(value) > len("https://"):
        return value
    return ""


def load_vapid_key(private_key: str):
    """시크릿에 담긴 개인키를 pywebpush 가 받는 모양으로.

    pywebpush 는 **문자열을 '파일 경로 아니면 base64url raw 키'로만 본다.**
    시크릿에 PEM 본문을 넣으면 어느 쪽도 아니라 'Could not deserialize key
    data' 로 죽는다(2026-09-19 실측: raw 32B ✓ / Vapid.from_pem ✓ / PEM 문자열 ✗).

    이 저장소가 `web/tools/gen_vapid_keys.mjs` 로 낸 키는 base64url 32바이트라
    그대로 지나간다. PEM 을 쓰는 운영자도 있으므로 둘 다 받는다.
    """
    if "-----BEGIN" in private_key:
        from py_vapid import Vapid  # noqa: PLC0415 — pywebpush 와 함께 온다

        return Vapid.from_pem(private_key.strip().encode())
    return private_key


def fetch_subscriptions(site: str, token: str) -> list[dict]:
    """구독 목록. 인증 실패는 조용히 넘기지 않는다 — 토큰이 어긋난 배포는
    '구독자 0명'과 로그에서 구분이 안 되고, 그 상태로 몇 달이 간다."""
    response = requests.get(f"{site}/push/list",
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=LIST_TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:160]}")
    payload = response.json()
    return [sub for sub in (payload.get("subscriptions") or [])
            if sub.get("endpoint") and sub.get("keys")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="아침 알림 발송")
    parser.add_argument("--force", action="store_true",
                        help="오늘 이미 보냈다는 기록을 무시하고 보낸다")
    parser.add_argument("--dry-run", action="store_true",
                        help="보낼 것만 찍고 보내지 않는다")
    args = parser.parse_args(argv)

    site = (os.environ.get("SITE_URL") or DEFAULT_SITE).rstrip("/")
    token = os.environ.get("PUSH_ADMIN_TOKEN") or ""
    private_key = os.environ.get("VAPID_PRIVATE_KEY") or ""
    # 변수가 비어 있으면 기본값으로 물러난다. 하지만 **값이 있는데 모양이 틀린 것**은
    # 물러날 자리가 아니다 — 운영자가 설정했다고 믿고 있는 값이라, 조용히 다른 값으로
    # 보내면 그 사실이 영영 안 드러난다.
    raw_subject = os.environ.get("VAPID_SUBJECT") or ""
    subject = clean_subject(raw_subject) if raw_subject.strip() else DEFAULT_SUBJECT

    briefings = _read_json(BRIEFINGS_FILE, [])
    payload = build_payload(briefings if isinstance(briefings, list) else [])
    brief_date = payload_date(payload)
    print(f"[push] 보낼 것: {payload['title']}")

    outbox = _read_json(OUTBOX_FILE, {})
    if not args.force and already_notified(outbox if isinstance(outbox, dict) else {}, brief_date):
        print(f"[push] {brief_date} 알림은 이미 나갔다 — 재실행이라 보내지 않는다")
        return 0

    if args.dry_run:
        print(f"[push] --dry-run — 본문:\n{payload['body']}")
        return 0

    if not token or not private_key:
        missing = " · ".join(name for name, value in
                             (("PUSH_ADMIN_TOKEN", token), ("VAPID_PRIVATE_KEY", private_key))
                             if not value)
        print(f"::error::[push] {missing} 미설정 — 아침 알림이 나가지 않는다")
        return 1

    # 구독자를 부르기 **전에** 본다. 여기서 안 막으면 py_vapid 가 서명 단계에서
    # 구독마다 예외를 던지고, 로그에는 '전원 발송 실패'만 남는다 — 공백 하나가
    # 원인인 것을 그 증상에서 되짚는 데 시간이 든다.
    if not subject:
        print(f"::error::[push] VAPID_SUBJECT 가 {raw_subject!r} 다 — "
              f"'mailto:<이메일>' 또는 'https://<주소>' 여야 한다")
        return 1

    from pywebpush import WebPushException, webpush  # noqa: PLC0415 — 로컬엔 없을 수 있다

    try:
        vapid_key = load_vapid_key(private_key)
    except Exception as exc:  # noqa: BLE001 — 키 모양이 틀린 모든 경우
        print(f"::error::[push] VAPID_PRIVATE_KEY 를 읽지 못했다 "
              f"({type(exc).__name__}: {str(exc)[:120]})")
        return 1

    try:
        subscriptions = fetch_subscriptions(site, token)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"::error::[push] /push/list 조회 실패 — {str(exc)[:160]}")
        return 1

    if not subscriptions:
        print("[push] 구독자 0명 — 보낼 곳이 없다(정상)")
        return 0

    data = json.dumps(payload, ensure_ascii=False)
    sent = dead = failed = 0
    reasons: list[str] = []
    for subscription in subscriptions:
        endpoint = subscription["endpoint"]
        try:
            webpush(subscription_info={"endpoint": endpoint, "keys": subscription["keys"]},
                    data=data, vapid_private_key=vapid_key,
                    vapid_claims={"sub": subject}, ttl=PUSH_TTL, timeout=SEND_TIMEOUT)
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            # 404·410 은 "이 구독은 이제 없다"는 뜻이다. 남겨 두면 매일 같은
            # 실패를 다시 사고, 목록이 실제 독자 수를 말하지 않게 된다.
            if status in (404, 410):
                dead += 1
                try:
                    requests.delete(f"{site}/push/subscribe", json={"endpoint": endpoint},
                                    timeout=PRUNE_TIMEOUT)
                except requests.RequestException as prune_exc:
                    print(f"[push] 만료 구독 정리 실패({type(prune_exc).__name__}) — 다음 회차가 다시 만난다")
            else:
                failed += 1
                reasons.append(f"HTTP {status}")
        except Exception as exc:  # noqa: BLE001 — 발송 하나가 나머지를 막지 않는다
            failed += 1
            reasons.append(type(exc).__name__)

    print(f"[push] 발송 {sent} · 만료 정리 {dead} · 실패 {failed} (구독 {len(subscriptions)})")
    if reasons:
        # 사유는 몇 개만 — 로그가 구독자 수만큼 길어지면 아무도 안 읽는다.
        print(f"[push] 실패 사유: {', '.join(sorted(set(reasons))[:5])}")

    if sent:
        record_notified(OUTBOX_FILE, brief_date, sent=sent, total=len(subscriptions))

    # 셋을 로그에서 가른다. 전원 실패는 설정·키가 어긋났다는 뜻이라 사람이
    # 봐야 하고, 일부 실패는 폰 하나가 꺼져 있던 날일 수 있다.
    if failed and not sent:
        print(f"::error::[push] 구독 {len(subscriptions)}건 전원 발송 실패 — 키·구독 설정 확인 필요")
        return 1
    if failed:
        print(f"::warning::[push] {len(subscriptions)}건 중 {failed}건 발송 실패")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
