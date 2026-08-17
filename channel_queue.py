"""구독 채널 일괄 공개 큐 — 흩어져 나가던 자료를 한 번에 내보낸다.

왜 큐가 필요한가:
    구독자는 봇 개인 대화에 들어올 수 없다. 여러 명이 받으려면 채널이어야 하고,
    채널이 되는 순간 **도착이 흩어지는 것 자체가** 구독 경험이 된다. 지금
    파이프라인은 기사 카드를 먼저 보내고, 웹 빌드를 거쳐 십수 분 뒤 빠른 오디오,
    다시 몇 분 뒤 전문가 오디오를 보낸다. 혼자 보는 DM 에서는 아무 문제가 아니던
    그 간격이 채널에서는 알림 다섯 번으로 울린다.

    그래서 DM 발송은 그대로 둔다 — 그 자리가 리허설이자, 오디오 file_id 를 얻는
    유일한 자리다. 대신 나갈 자료를 배치로 모아 **마지막 재료(전문가 오디오)가
    준비된 순간** 순서대로 한 번에 채널에 싣는다.

오디오를 다시 올리지 않는 이유:
    mp3 는 git 에 없다 (Actions 캐시와 Cloudflare Pages 에만 산다). 텔레그램의
    file_id 는 **같은 봇이라면 다른 대화에서도 그대로 재사용된다.** DM 에 한 번
    올릴 때 받은 file_id 를 적어 두면, 채널 발송은 8MB 재업로드가 아니라 문자열
    하나를 보내는 일이 된다.

배치의 단위:
    - ``daily-<날짜>``  — 보고서추천(있는 날만)·국내·해외·빠른·전문가. 다 모아 한 번에.
    - ``weekly-<날짜>`` — 주간 판세 하나. 뜨는 즉시 그것만 보낸다.

상태 파일: ``channel_outbox.json`` (배치 목록, 최근 KEEP_DAYS 일만 보관)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 로그가 전부 한국어다. Windows 콘솔 기본 코드페이지(cp1252/949)로는 첫 print 에서
# UnicodeEncodeError 로 죽는다 — 설정용 명령(--find-channel·--check-channel)은
# 하필 그 환경에서 손으로 돌리는 것들이다 (daily_brief 와 같은 처방).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# tz 없는 today() 는 UTC 러너에서 하루 전 날짜를 준다 (synthesize.KST 와 같은 이유).
KST = timezone(timedelta(hours=9))

QUEUE_FILE = Path(__file__).parent / "channel_outbox.json"
SCHEMA_VERSION = 1

# 배치가 이 시간을 넘기면 공개하지 않는다. 어제 아침 자료가 오늘 아침 채널에
# 뜨는 것은 지연이 아니라 오배송이다. daily-brief 의 RESEND_WINDOW_H(36h)보다
# 짧게 잡아 하루를 넘기지 못하게 한다 — 같은 날 수동 재실행은 아직 살린다.
STALE_H = 20
KEEP_DAYS = 7

# 채널은 DM 보다 rate limit 이 빡빡하다(대화당 분당 20건 안팎). 배치가 5~8개
# 메시지라 이 간격이면 한도에 닿지 않는다.
SEND_GAP_SEC = 1.5

WEEKLY_ITEM_NAME = "주간판세"


# ---- 상태 파일 ---------------------------------------------------------------


def load_queue(path: Path | None = None) -> dict:
    path = path or QUEUE_FILE
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "batches": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # 큐가 깨졌다고 그날 파이프라인을 죽이지 않는다. 다만 조용히 넘어가면
        # 채널만 비는 상태가 오래 사니 로그에 남긴다.
        print(f"[channel] 큐 파일 손상 — 새로 시작합니다: {type(exc).__name__}: {exc}")
        return {"schema_version": SCHEMA_VERSION, "batches": []}
    if not isinstance(data, dict) or not isinstance(data.get("batches"), list):
        print("[channel] 큐 파일 형식이 다름 — 새로 시작합니다")
        return {"schema_version": SCHEMA_VERSION, "batches": []}
    data.setdefault("schema_version", SCHEMA_VERSION)
    return data


def save_queue(queue: dict, path: Path | None = None) -> None:
    path = path or QUEUE_FILE
    path.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def prune(queue: dict, now: datetime | None = None, keep_days: int = KEEP_DAYS) -> dict:
    """오래된 배치를 버린다. 큐는 발송 대기열이지 아카이브가 아니다."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=keep_days)
    queue["batches"] = [b for b in queue.get("batches", [])
                        if _created_at(b) is None or _created_at(b) >= cutoff]
    return queue


def _created_at(batch: dict) -> datetime | None:
    raw = batch.get("created_at")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _age_hours(batch: dict, now: datetime) -> float:
    created = _created_at(batch)
    if created is None:
        return 0.0
    return (now - created).total_seconds() / 3600


# ---- 배치 구성 ---------------------------------------------------------------


def ensure_batch(queue: dict, batch_id: str, kind: str, date: str,
                 now: datetime | None = None) -> dict:
    """배치를 찾거나 만든다. 이미 있으면 상태를 건드리지 않는다."""
    for batch in queue.setdefault("batches", []):
        if batch.get("id") == batch_id:
            return batch
    batch = {
        "id": batch_id,
        "kind": kind,
        "date": date,
        "created_at": (now or datetime.now(timezone.utc)).isoformat(),
        "status": "pending",
        "items": [],
    }
    queue["batches"].append(batch)
    return batch


def add_item(batch: dict, item: dict) -> bool:
    """항목을 배치 끝에 붙인다. 같은 이름이 이미 있으면 덮어쓰지 않는다.

    멱등이어야 하는 이유: plan 은 claim push 충돌 때 재실행되고(최대 5회),
    오디오도 캐시 재사용 경로에서 다시 적재를 시도한다. 그때마다 항목이 늘면
    구독자는 같은 카드를 두 번 본다. 그리고 **이미 sent 인 항목은 절대
    pending 으로 되돌리지 않는다** — 그 되돌림이 곧 중복 발송이다.
    """
    for existing in batch.setdefault("items", []):
        if existing.get("name") == item.get("name"):
            if existing.get("status") == "pending":
                # 아직 안 나갔으면 최신 내용으로 갱신 (재계획으로 본문이 바뀔 수 있다)
                existing.update({k: v for k, v in item.items() if k != "status"})
            return False
    batch["items"].append({**item, "status": item.get("status", "pending")})
    return True


def sync_daily_batch(outbox: dict, path: Path | None = None,
                     now: datetime | None = None) -> dict | None:
    """daily_brief 의 outbox 브리핑을 그날 배치의 텍스트 항목으로 옮긴다.

    **plan 단계에서** 부른다 — send 가 아니라. claim push 가 outbox 와 함께 이
    파일을 커밋하므로, 뒤 스텝의 `git reset --hard origin/main` 이 지나가도
    본문이 살아남는다. send 에서 적재하면 confirm push 가 실패한 날 텍스트가
    통째로 지워지고 채널에는 오디오만 뜬다.
    """
    if not isinstance(outbox, dict):
        return None
    date = str(outbox.get("date") or "")
    briefs = outbox.get("briefs") or []
    if not date or not briefs or outbox.get("status") in ("empty", "quality_rejected"):
        return None

    queue = load_queue(path)
    batch = ensure_batch(queue, f"daily-{date}", "daily", date, now=now)
    for brief in briefs:
        # stale_skipped 는 '보내지 않기로 한' 브리핑이다. 채널로도 가면 안 된다.
        if brief.get("status") == "stale_skipped" or not brief.get("text"):
            continue
        add_item(batch, {"kind": "text", "name": brief.get("name") or "브리핑",
                         "text": brief["text"]})
    if not batch["items"]:
        return None
    prune(queue, now=now)
    save_queue(queue, path)
    return batch


def record_audio(date: str, name: str, file_id: str, caption: str = "",
                 title: str = "", performer: str = "", duration: int = 0,
                 path: Path | None = None, now: datetime | None = None) -> bool:
    """DM 에 올려 받은 오디오 file_id 를 그날 배치 끝에 붙인다.

    file_id 가 없으면 적재하지 않는다 — 8MB mp3 를 채널에서 다시 만들 방법이
    없으므로, 없는 채로 큐에 앉히면 발송 시각에야 빈손인 걸 알게 된다.
    """
    if not file_id or not date:
        return False
    queue = load_queue(path)
    batch = ensure_batch(queue, f"daily-{date}", "daily", date, now=now)
    added = add_item(batch, {
        "kind": "audio", "name": name, "file_id": file_id,
        "caption": caption, "title": title, "performer": performer,
        "duration": int(duration or 0),
    })
    prune(queue, now=now)
    save_queue(queue, path)
    return added


# ---- 발송 -------------------------------------------------------------------


def channel_id() -> str | None:
    """구독 채널 ID. 봇 DM(TELEGRAM_CHAT_ID)으로 절대 폴백하지 않는다.

    폴백하면 채널 설정이 빠진 날 '조용히 나 혼자만 받는' 상태가 되고, 그건
    발송 실패보다 알아채기 어렵다 (operational_alerts 가 관리자 채팅에 대해
    같은 판정을 한다).
    """
    from telegram_send import resolve_setting
    return resolve_setting("TELEGRAM_CHANNEL_ID")


class TelegramChannel:
    """채널 전용 발송 어댑터. 텍스트는 DM 과 같은 분할 규칙을 쓴다."""

    def __init__(self, chat_id: str):
        self.chat_id = chat_id

    def send_text(self, item: dict) -> bool:
        from telegram_send import send_long_text
        resp = send_long_text(item["text"], parse_mode="HTML",
                              disable_preview=bool(item.get("disable_preview")),
                              chat_id=self.chat_id)
        return bool(resp) and all(r.get("ok") for r in resp)

    def send_audio(self, item: dict) -> bool:
        """file_id 로 싣는다 — 재업로드 없음. 그래서 multipart 가 필요 없다."""
        from telegram_send import resolve_target
        token, _ = resolve_target(self.chat_id)
        data = {"chat_id": self.chat_id, "audio": item["file_id"]}
        for key in ("caption", "title", "performer"):
            if item.get(key):
                data[key] = item[key]
        if item.get("duration"):
            data["duration"] = str(int(item["duration"]))
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendAudio",
            data=urllib.parse.urlencode(data).encode("utf-8"),
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Telegram API HTTP {exc.code}: {body}") from exc
        return bool(payload.get("ok"))


def publish(path: Path | None = None, now: datetime | None = None,
            sender: object | None = None, batch_id: str | None = None,
            gap_sec: float = SEND_GAP_SEC) -> list[dict]:
    """대기 중인 배치를 순서대로 채널에 공개한다.

    - 배치는 만들어진 순서대로, 항목은 적재된 순서대로 나간다
      (보고서추천 → 국내 → 해외 → 빠른 오디오 → 전문가 오디오).
    - 이미 sent 인 항목은 건너뛴다 → 같은 날 재실행해도 중복이 없다.
    - STALE_H 를 넘긴 배치는 공개하지 않는다 (stale_skipped).
    """
    now = now or datetime.now(timezone.utc)
    queue = load_queue(path)
    targets = [b for b in queue.get("batches", [])
               if b.get("status") in ("pending", "partial", "failed")
               and (batch_id is None or b.get("id") == batch_id)]
    if not targets:
        print("[channel] 공개 대기 중인 배치 없음")
        return []

    if sender is None:
        target_chat = channel_id()
        if not target_chat:
            # 설정이 빠졌을 뿐 자료는 온전하다. pending 으로 남겨 다음 실행이
            # 다시 시도하게 두고, 로그는 워크플로 주석으로 눈에 띄게 남긴다.
            print("::warning::TELEGRAM_CHANNEL_ID 미설정 — 구독 채널 공개를 건너뜁니다 "
                  "(자료는 큐에 남아 다음 실행에서 재시도)")
            return []
        sender = TelegramChannel(target_chat)

    results: list[dict] = []
    first = True
    for batch in targets:
        if _age_hours(batch, now) > STALE_H:
            for item in batch.get("items", []):
                if item.get("status") in ("pending", "failed"):
                    item["status"] = "stale_skipped"
            batch["status"] = _batch_status(batch)
            print(f"[channel] {batch.get('id')} — {STALE_H}h 초과, 공개 생략")
            results.append({"batch": batch.get("id"), "status": "stale_skipped"})
            continue

        for item in batch.get("items", []):
            if item.get("status") not in ("pending", "failed"):
                continue
            if not first:
                time.sleep(gap_sec)
            first = False
            ok = False
            try:
                if item.get("kind") == "audio":
                    ok = bool(sender.send_audio(item))
                else:
                    ok = bool(sender.send_text(item))
            except Exception as exc:  # noqa: BLE001 — 한 항목 실패가 나머지를 막지 않게
                print(f"[channel] {item.get('name')} 공개 실패: "
                      f"{type(exc).__name__}: {str(exc)[:200]}")
            item["status"] = "sent" if ok else "failed"
            if ok:
                item["sent_at"] = now.isoformat()
            results.append({"batch": batch.get("id"), "name": item.get("name"),
                            "kind": item.get("kind", "text"), "status": item["status"]})
            print(f"[channel] {batch.get('id')} · {item.get('name')} → {item['status']}")
        batch["status"] = _batch_status(batch)

    prune(queue, now=now)
    save_queue(queue, path)
    return results


def _batch_status(batch: dict) -> str:
    statuses = [i.get("status") for i in batch.get("items", [])]
    if not statuses:
        return "empty"
    if all(s in ("sent", "stale_skipped") for s in statuses):
        return "sent" if any(s == "sent" for s in statuses) else "stale_skipped"
    if any(s == "sent" for s in statuses):
        return "partial"
    return "pending" if all(s == "pending" for s in statuses) else "failed"


def publish_weekly(text: str, date: str | None = None, path: Path | None = None,
                   now: datetime | None = None, sender: object | None = None,
                   gap_sec: float = SEND_GAP_SEC) -> list[dict]:
    """주간 판세를 그것 하나만 즉시 채널에 올린다.

    일일 배치와 섞지 않는다 — 금요일 저녁에 뜨는 자료를 토요일 아침 배치까지
    붙들고 있으면 '주간'이라는 말이 무색해진다.
    """
    date = date or datetime.now(KST).date().isoformat()
    queue = load_queue(path)
    batch = ensure_batch(queue, f"weekly-{date}", "weekly", date, now=now)
    add_item(batch, {"kind": "text", "name": WEEKLY_ITEM_NAME, "text": text,
                     "disable_preview": True})
    prune(queue, now=now)
    save_queue(queue, path)
    return publish(path=path, now=now, sender=sender, batch_id=batch["id"],
                   gap_sec=gap_sec)


# ---- CLI --------------------------------------------------------------------


def check_channel() -> int:
    """채널이 닿는지 **메시지를 보내지 않고** 확인한다 (getChat 은 읽기 전용).

    시크릿을 바꾼 뒤 다음 브리핑(최대 24시간)을 기다려야 맞았는지 아는 구조면
    설정 오류가 오래 산다 — operational_alerts --check-admin-chat 과 같은 이유.
    """
    from telegram_send import resolve_setting
    token = resolve_setting("TELEGRAM_BOT_TOKEN")
    target = channel_id()
    if not token:
        print("TELEGRAM_BOT_TOKEN 이 없습니다.")
        return 1
    if not target:
        print("TELEGRAM_CHANNEL_ID 가 없습니다 — 구독 채널 공개는 건너뜁니다.")
        return 1

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getChat?"
        + urllib.parse.urlencode({"chat_id": target}))
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"확인 실패(네트워크): {type(exc).__name__}: {exc}")
        return 1

    if not payload.get("ok"):
        # 값 자체는 찍지 않는다 — Actions 로그는 협업자 누구나 읽고, 채팅 ID 는
        # 그 자체로 대화 상대를 특정한다 (README '관리자 알림이 안 올 때'와 같은 규칙).
        print(f"채널에 닿지 못했습니다: {payload.get('description') or '알 수 없는 오류'}")
        print("봇을 채널 관리자로 초대하고 '메시지 게시' 권한을 켰는지, "
              "비공개 채널이면 @이름 대신 -100… 숫자 ID 를 넣었는지 확인하세요.")
        return 1

    chat = payload.get("result") or {}
    label = chat.get("title") or chat.get("username") or "이름 없음"
    print(f"구독 채널 확인됨: {chat.get('type', '?')} · {label}")
    if chat.get("type") not in ("channel", "supergroup", "group"):
        print("경고: 채널이 아닙니다 — 구독자가 들어올 수 없는 대화입니다.")
        return 1
    return 0


def find_channels() -> int:
    """봇이 최근에 본 대화를 훑어 채널 후보의 숫자 ID 를 보여 준다.

    비공개 채널은 `@이름` 으로 못 보내므로 `-100…` 이 필요한데, 그 값은 화면
    어디에도 안 보인다. 봇을 관리자로 **승격하는 행위 자체가** my_chat_member
    업데이트를 남기고, 관리자가 된 뒤의 채널 글은 channel_post 로 온다 — 봇은
    privacy mode 라 일반 대화는 못 읽지만 이 둘은 받는다.

    getUpdates 는 최근 24시간분만 준다. 승격하고 하루가 지났으면 채널에 글을
    하나 더 올린 뒤 다시 부른다.
    """
    from telegram_send import resolve_setting
    token = resolve_setting("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN 이 없습니다.")
        return 1
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/getUpdates", timeout=20) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"조회 실패: {type(exc).__name__}: {exc}")
        return 1

    seen: dict[str, str] = {}
    for update in payload.get("result") or []:
        for key in ("my_chat_member", "channel_post", "message"):
            chat = (update.get(key) or {}).get("chat") or {}
            if chat.get("id") is not None:
                seen[str(chat["id"])] = chat.get("type", "?")
    if not seen:
        print("최근 24시간 안에 본 대화가 없습니다 — 봇을 채널 관리자로 올린 뒤, "
              "또는 채널에 글을 하나 올린 뒤 다시 실행하세요.")
        return 1

    # 채널명은 찍지 않는다 — 콘솔 인코딩에 걸리는 데다, 이 명령의 출력은 그대로
    # 어딘가에 붙여지기 쉽다(README '관리자 알림이 안 올 때'와 같은 규칙).
    print("최근에 본 대화 (TELEGRAM_CHANNEL_ID 에 넣을 값):")
    for chat_id, kind in seen.items():
        mark = " ← 채널" if kind in ("channel", "supergroup") else ""
        print(f"  {chat_id}  [{kind}]{mark}")
    return 0


def show_status(path: Path | None = None) -> int:
    queue = load_queue(path)
    batches = queue.get("batches", [])
    if not batches:
        print("[channel] 큐가 비어 있습니다.")
        return 0
    for batch in batches:
        print(f"{batch.get('id')} [{batch.get('status')}] {batch.get('created_at')}")
        for item in batch.get("items", []):
            print(f"  - {item.get('kind')} · {item.get('name')} → {item.get('status')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="구독 채널 일괄 공개 큐")
    parser.add_argument("--publish", action="store_true",
                        help="대기 중인 배치를 순서대로 채널에 공개")
    parser.add_argument("--status", action="store_true", help="큐 상태 출력")
    parser.add_argument("--check-channel", action="store_true",
                        help="채널이 닿는지만 확인한다(메시지를 보내지 않음)")
    parser.add_argument("--find-channel", action="store_true",
                        help="봇이 최근 본 대화의 숫자 ID 를 보여 준다(설정용)")
    args = parser.parse_args()

    if args.find_channel:
        return find_channels()
    if args.check_channel:
        return check_channel()
    if args.status:
        return show_status()
    if not args.publish:
        parser.print_help()
        return 0

    results = publish()
    failed = [r for r in results if r.get("status") == "failed"]
    sent = [r for r in results if r.get("status") == "sent"]
    print(f"[channel] 공개 완료 — 성공 {len(sent)}건 / 실패 {len(failed)}건")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
