"""
원자력정책실 동향봇 - 텔레그램 발송 모듈

사용법:
    python telegram_send.py "메시지 내용"
    python telegram_send.py --file 메시지.txt
    python telegram_send.py --file 메시지.md --html

또는 다른 스크립트에서 import:
    from telegram_send import send_text, send_long_text
    send_text("간단한 한 줄")
    send_long_text(long_markdown, mode='html')
    send_long_text(text, chat_id=구독채널)   # 기본 대상이 아닌 곳으로
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ---- 설정 로드 ---------------------------------------------------------------
#
# 우선순위:
#   1. 환경변수 (GitHub Actions Secrets, OS env)
#   2. .env 파일 (로컬 개발용, .gitignore 처리됨)

ENV_PATH = Path(__file__).parent / ".env"


def _load_env_file() -> dict[str, str]:
    """.env 파일에서 키-값 쌍 읽기. 파일 없으면 빈 dict 반환."""
    if not ENV_PATH.exists():
        return {}
    config: dict[str, str] = {}
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    return config


def _resolve(key: str) -> str | None:
    """환경변수 먼저, 없으면 .env 파일에서 가져옴.

    빈 문자열로 '설정된' 환경변수는 .env 로 넘어가지 않는다 — 근거는
    gemini_client._resolve 의 같은 판정에 적어 두었다. 여기서는 묵은 .env 가
    발송 대상 채팅을 되살리는 것을 막는다.
    """
    value = os.environ.get(key)
    if value is None:
        value = _ENV_FILE.get(key)
    return value or None


_ENV_FILE = _load_env_file()

MAX_LEN = 4000  # 텔레그램 메시지 한도는 4096; 안전 마진 96자

MISSING_HINT = (
    "  - 로컬: .env 파일에 설정\n"
    "  - GitHub Actions: Repository Secrets에 등록"
)


def resolve_setting(key: str) -> str | None:
    """환경변수 먼저, 없으면 .env — 다른 모듈이 같은 규칙으로 읽게 공개한다."""
    return _resolve(key)


def resolve_target(chat_id: str | None = None) -> tuple[str, str]:
    """(토큰, 발송 대상) 확인. 누락이면 RuntimeError.

    예전엔 이 확인이 모듈 최상위의 sys.exit 이었다. 그 자리는 **import 만으로**
    실행되므로 발송과 무관한 코드가 이 모듈을 스치기만 해도 프로세스가 죽는다
    (2026-08-16 web/build_data 사고와 똑같은 모양 — news_bot 도 그때 같은 이유로
    최상위 검사를 함수 안으로 옮겼다). 검사를 없앤 게 아니라 자리를 옮겼다.

    chat_id 를 주면 그 대화로 보낸다. 구독 채널 일괄 공개(channel_queue)가 봇
    DM 과 같은 청크 분할·이스케이프를 쓰면서 대상만 바꾸는 통로다.
    """
    token = _resolve("TELEGRAM_BOT_TOKEN")
    target = chat_id or _resolve("TELEGRAM_CHAT_ID")
    if not token or not target:
        missing = "TELEGRAM_BOT_TOKEN" if not token else "TELEGRAM_CHAT_ID"
        raise RuntimeError(f"{missing} 누락.\n{MISSING_HINT}")
    return token, target


# ---- 핵심 발송 함수 ----------------------------------------------------------


def send_text(text: str, parse_mode: str | None = "HTML",
              reply_markup: dict | None = None,
              disable_preview: bool = False,
              chat_id: str | None = None) -> dict:
    """단일 메시지 발송. 4000자 이내여야 함.

    parse_mode:
        - "HTML"  : <b>굵게</b>, <i>기울임</i>, <a href='url'>링크</a> 지원 (권장)
        - "MarkdownV2" : 텔레그램 마크다운 (이스케이프 까다로움)
        - None    : 평문
    reply_markup:
        - inline keyboard 등 Telegram reply_markup 객체 (dict). 예:
          {"inline_keyboard": [[{"text": "👍", "callback_data": "fb:xxxx:important"}]]}
    chat_id:
        - 생략하면 TELEGRAM_CHAT_ID (봇 DM). 구독 채널로 보낼 때만 지정한다.
    """
    token, target = resolve_target(chat_id)
    data = {"chat_id": target, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    if disable_preview:
        data["disable_web_page_preview"] = "true"

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=urllib.parse.urlencode(data).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API HTTP {e.code}: {body}") from e


def is_network_error(error: BaseException) -> bool:
    """텔레그램 서버의 **응답**이 아니라 연결 자체가 실패한 오류인가.

    send_text 는 HTTP 오류(400 잘못된 요청·403 차단·429 한도)를 RuntimeError 로
    바꿔 올린다 — 서버가 답했다는 뜻이고, 같은 요청을 곧바로 다시 보내도 대개
    같은 답이 온다. 여기 걸리는 것은 그 앞 단계다: 경로 없음(Errno 101)·연결
    끊김(Errno 104)·응답 지연(timeout). URLError 와 그 밑의 소켓 오류는 모두
    OSError 다. HTTPError 도 OSError 의 자식이라 따로 뺀다 — send_text 를 거치지
    않고 이 판정을 쓰는 호출자가 있어도 서버 응답을 재시도하지 않게.
    """
    return isinstance(error, OSError) and not isinstance(error, urllib.error.HTTPError)


def send_long_text(text: str, parse_mode: str | None = "HTML",
                   reply_markup: dict | None = None,
                   disable_preview: bool = False,
                   chat_id: str | None = None,
                   network_retries: int = 0,
                   retry_wait: float = 20.0) -> list[dict]:
    """긴 메시지를 자동으로 여러 메시지로 쪼개서 발송.

    가능하면 단락(\\n\\n) 경계에서 끊고, 안 되면 줄(\\n) 경계, 그것도 안 되면
    글자 단위로 잘라서 보냄. reply_markup 은 마지막 청크에만 부착
    (피드백 키보드가 브리핑 끝에 오도록).

    network_retries: 연결 오류(:func:`is_network_error`)가 나면 retry_wait 초 쉬고
    **그 청크만** 다시 보낸다. 기본 0 — 예전 호출자는 그대로다. 청크 단위인 이유:
    브리핑 전체를 다시 보내면 이미 나간 앞 청크가 구독자에게 두 번 간다.

    2026-11-21 05:16 KST 아침 브리핑에서 러너 네트워크가 몇 초 끊겨 보고서추천
    (Errno 101)·국내(Errno 104)가 실패했고, 2초 뒤 해외는 정상으로 나갔다. 재시도가
    없어서 두 브리핑은 다음 체인 회차(06:51)까지 1시간 35분 늦었다.

    Errno 104 는 서버가 메시지를 받은 뒤 답이 끊긴 것일 수도 있어 재시도가 같은
    청크를 두 번 보낼 수 있다. 그 위험은 새로 생기는 것이 아니다 — 재시도가
    없을 때도 다음 회차가 실패한 브리핑을 통째로 다시 보낸다. 여기서 다시 보내면
    늦는 시간만 줄어들고, 겹쳐도 브리핑 전체가 아니라 청크 하나다.
    """
    chunks = split_by_length(text, MAX_LEN)
    results = []
    for i, chunk in enumerate(chunks):
        if i > 0:
            time.sleep(0.5)  # 텔레그램 rate limit 회피
        markup = reply_markup if i == len(chunks) - 1 else None
        attempt = 0
        while True:
            try:
                result = send_text(chunk, parse_mode=parse_mode, reply_markup=markup,
                                   disable_preview=disable_preview, chat_id=chat_id)
                break
            except Exception as error:  # noqa: BLE001 — 판정 뒤 그대로 다시 올린다
                if attempt >= network_retries or not is_network_error(error):
                    raise
                attempt += 1
                print(f"[telegram] 연결 오류 — {retry_wait:g}초 뒤 청크 {i + 1}/{len(chunks)} "
                      f"다시 보냄 ({attempt}/{network_retries}): "
                      f"{type(error).__name__}: {str(error)[:120]}")
                time.sleep(retry_wait)
        results.append(result)
    return results


def split_by_length(text: str, limit: int) -> list[str]:
    """텍스트를 limit 길이 이하 청크로 분할. 단락→줄→글자 순으로 끊는다."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # 단락 경계 우선
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


# ---- CLI 진입점 --------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="텔레그램 봇으로 메시지 보내기")
    parser.add_argument("message", nargs="?", help="보낼 메시지 내용 (직접 입력)")
    parser.add_argument("--file", "-f", help="메시지 파일 경로 (UTF-8)")
    parser.add_argument("--plain", action="store_true", help="평문 발송 (서식 비활성화)")
    parser.add_argument("--markdown", action="store_true", help="MarkdownV2 모드")
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.message:
        text = args.message
    else:
        parser.error("메시지 또는 --file 중 하나는 필수")

    if args.plain:
        mode = None
    elif args.markdown:
        mode = "MarkdownV2"
    else:
        mode = "HTML"

    try:
        results = send_long_text(text, parse_mode=mode)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    ok_count = sum(1 for r in results if r.get("ok"))
    print(f"[OK] {ok_count}/{len(results)} 메시지 발송 완료")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
