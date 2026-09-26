"""카카오톡 아침 브리핑 — 그날 브리핑을 카카오톡 '나와의 채팅'으로 보낸다.

무엇을 하나
-----------
outbox 의 국내·해외 카드 제목을 카카오 메시지 API 의 **나에게 보내기**
(`/v2/api/talk/memo/default/send`)로 보낸다. 카카오 로그인한 본인에게만 가는
대신 **무료**이고, 사업자 등록·비즈 채널·검수가 필요 없다. 알림톡·친구톡·채널
메시지는 건당 과금이라 이 경로를 골랐다.

왜 제목만인가
-------------
기본 텍스트 템플릿은 200자까지만 보인다(넘으면 '…'로 잘린다). 카드 한 장이 이미
300자를 넘으니 본문은 실을 수 없다. 그래서 제목 목록을 200자 안쪽 말풍선으로
나눠 보내고, 말풍선마다 버튼이 그날 웹 브리핑(`/brief/<날짜>`)을 연다.
기사 원문 링크는 달 수 없다 — 카카오는 앱에 등록한 도메인으로만 링크를 연다.

제목은 텔레그램 카드에 찍힌 번호(`brief_rank`) 그대로 세운다. 두 채널이 다른
순서로 말하면 어느 쪽이 맞는지 되묻게 된다.

언제 보내는가
-------------
Daily Brief 워크플로에서 웹 배포 뒤에 돈다. 버튼이 여는 주소가 오늘 것을
보여 주려면 배포가 끝나 있어야 한다. 배포가 실패한 날에도 카톡은 보내되 버튼은
사이트 첫 화면을 연다(`WEB_DEPLOY_OUTCOME`). 텔레그램 발송 성패와는 무관하다.

보내지 않는 경우
----------------
* 설정이 하나도 없다 — 선택 기능이라 조용히 건너뛴다. **반쪽만** 있으면 오류.
* 오늘 것을 이미 보냈다 — outbox 의 `kakao` 칸을 본다(재실행 중복 방지).
* outbox 가 비었거나(empty) 품질 claim 불일치로 발송이 막혔다.

토큰
----
액세스 토큰은 몇 시간이면 만료되므로 매번 리프레시 토큰으로 새로 받는다.
리프레시 토큰은 두 달짜리이고, 남은 기간이 한 달 미만일 때 갱신 요청을 하면
새 것이 함께 온다. **그 새 토큰을 저장하지 않으면 두 달 뒤 조용히 끊긴다.**
`KAKAO_SECRET_WRITER`(이 저장소 Secrets 쓰기 권한 PAT)가 있으면 `gh secret set`
으로 `KAKAO_REFRESH_TOKEN` 을 스스로 갈아 끼우고, 없으면 ::warning:: 과 함께
카톡으로도 재인증 안내를 한 줄 보낸다. 토큰 값은 어디에도 찍지 않는다.

환경
----
`KAKAO_REST_API_KEY`   카카오 앱의 REST API 키 (GitHub Secret)
`KAKAO_REFRESH_TOKEN`  `--auth` 로 받은 리프레시 토큰 (GitHub Secret)
`KAKAO_CLIENT_SECRET`  앱에서 클라이언트 시크릿을 켰을 때만 (GitHub Secret, 선택)
`KAKAO_SECRET_WRITER`  리프레시 토큰 자동 교체용 PAT (GitHub Secret, 선택)
`SITE_URL`             기본 https://nuclens-v2.pages.dev

    python tools/kakao_notify.py --auth       # 최초 1회: 로그인 → 리프레시 토큰 발급
    python tools/kakao_notify.py --test       # 연결 확인용 한 줄 발송
    python tools/kakao_notify.py --dry-run    # 보낼 말풍선만 찍는다 (토큰 불필요)
    python tools/kakao_notify.py              # 평소 (워크플로가 부르는 명령)
    python tools/kakao_notify.py --force      # 오늘 보냈다는 기록을 무시하고 보낸다
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

try:  # 저장소 다른 모듈과 같은 처리 — 한국어 진단이 cp1252 에서 터지지 않게.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python tools/kakao_notify.py` 로 직접 부를 때
    sys.path.insert(0, str(ROOT))

# 환경변수 → .env 순서로 읽는 규칙을 한 곳에 둔다(로컬 --auth 가 .env 를 읽는다).
from telegram_send import resolve_setting  # noqa: E402

OUTBOX_FILE = ROOT / "outbox.json"

DEFAULT_SITE = "https://nuclens-v2.pages.dev"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
SCOPE = "talk_message"
# `--auth` 가 이 주소로 로컬 서버를 띄워 인가 코드를 받는다. 카카오 앱의
# Redirect URI 에 **글자 그대로** 등록해야 한다.
DEFAULT_REDIRECT = "http://localhost:8765/callback"
SECRET_NAME = "KAKAO_REFRESH_TOKEN"

# 기본 텍스트 템플릿이 보여 주는 한도. 넘으면 말풍선이 '…'로 잘린다.
TEXT_LIMIT = 200
TITLE_CHARS = 60
# 지역당 말풍선 상한. 평소 국내 7·해외 10건이면 각각 2개 안쪽이다.
MAX_MESSAGES_PER_REGION = 3
REGIONS = (("국내", "🇰🇷"), ("해외", "🌐"))
BUTTON_TITLE = "브리핑 전체 보기"
TIMEOUT = 20
SEND_GAP_S = 0.3
AUTH_WAIT_S = 300

REAUTH_NOTICE = ("⚙️ 카카오 연동 토큰이 한 달 안에 만료됩니다. PC에서 "
                 "python tools/kakao_notify.py --auth 로 다시 발급해 "
                 "GitHub Secret KAKAO_REFRESH_TOKEN 을 바꿔 주세요.")
TEST_TEXT = "✅ Nuclens 카카오톡 연결 확인 — 내일 아침부터 브리핑이 여기로 옵니다."


class KakaoError(RuntimeError):
    """카카오 API 오류. `code` 로 재인증이 필요한 경우를 가른다."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---- 말풍선 짓기 -------------------------------------------------------------


def text_units(text: str) -> int:
    """카카오가 세는 길이를 **보수적으로** — UTF-16 코드 단위로 센다.

    한글은 1, 국기 이모지는 4로 잡힌다. 실제보다 길게 세면 말풍선이 하나 더
    늘 뿐이지만, 짧게 세면 제목 끝이 '…'로 잘려 무엇인지 알 수 없게 된다.
    """
    return len(text.encode("utf-16-le")) // 2


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def region_titles(outbox: dict, region: str) -> list[str]:
    """그 지역 카드 제목을 텔레그램에 찍힌 번호 순서대로."""
    rows = []
    for item in outbox.get("items") or []:
        if not isinstance(item, dict):
            continue
        if (item.get("brief_region") or item.get("region")) != region:
            continue
        title = _shorten(item.get("title_kr") or item.get("title") or "", TITLE_CHARS)
        if not title:
            continue
        rank = item.get("brief_rank")
        rows.append((rank if type(rank) is int else 10**6, len(rows), title))
    return [title for _rank, _order, title in sorted(rows)]


def pack_lines(head: str, cont_head: str, lines: list[str], *,
               limit: int = TEXT_LIMIT,
               max_messages: int = MAX_MESSAGES_PER_REGION) -> list[str]:
    """머리줄 + 번호 줄을 limit 안쪽 말풍선으로 나눈다.

    상한을 넘으면 마지막 말풍선 끝에 '… 외 N건'을 붙인다 — 조용히 떨어뜨리면
    그날 몇 건이었는지부터 거짓말이 된다.
    """
    blocks: list[list[str]] = [[head]]
    for idx, line in enumerate(lines):
        block = blocks[-1]
        if text_units("\n".join(block + [line])) <= limit:
            block.append(line)
            continue
        if len(blocks) >= max_messages:
            rest = len(lines) - idx
            more = f"… 외 {rest}건은 웹에서"
            while len(block) > 1 and text_units("\n".join(block + [more])) > limit:
                block.pop()
                rest += 1
                more = f"… 외 {rest}건은 웹에서"
            block.append(more)
            break
        blocks.append([cont_head, line])
    return ["\n".join(block) for block in blocks]


def _date_label(brief_date: str) -> str:
    try:
        day = datetime.strptime(brief_date, "%Y-%m-%d")
    except ValueError:
        return brief_date
    return f"{day.month}/{day.day}"


def build_messages(outbox: dict) -> list[str]:
    """지역별 제목 목록 말풍선. 제목이 없는 지역은 건너뛴다."""
    label = _date_label(str(outbox.get("date") or ""))
    messages: list[str] = []
    for region, flag in REGIONS:
        titles = region_titles(outbox, region)
        if not titles:
            continue
        head = f"{flag} {region} 브리핑 · {label} ({len(titles)}건)"
        cont_head = f"{flag} {region} 브리핑 (이어서)"
        lines = [f"{n}. {title}" for n, title in enumerate(titles, 1)]
        messages.extend(pack_lines(head, cont_head, lines))
    return messages


def brief_link(site: str, brief_date: str, *, deployed: bool) -> str:
    """버튼이 여는 주소. 배포가 끝났을 때만 날짜 페이지를 가리킨다 —
    배포 전의 `/brief/<오늘>` 은 아직 없는 주소다."""
    try:
        datetime.strptime(brief_date, "%Y-%m-%d")
    except ValueError:
        deployed = False
    return f"{site}/brief/{brief_date}" if deployed else f"{site}/"


def skip_reason(outbox: dict) -> str:
    if not isinstance(outbox, dict) or not outbox:
        return "outbox 없음"
    if outbox.get("status") == "empty":
        return "오늘 브리핑 없음(outbox=empty)"
    # send_outbox 가 막은 outbox 는 카톡도 보내지 않는다 — 검증 계약이 어긋난
    # 문구를 다른 창구로 흘리면 품질 게이트를 옆문으로 넘는 셈이다.
    if outbox.get("status") == "quality_rejected" or outbox.get("quality_gate_error"):
        return "품질 claim 불일치로 발송이 막힌 outbox"
    if not outbox.get("date"):
        return "outbox 날짜 없음"
    return ""


def already_sent(outbox: dict, brief_date: str) -> bool:
    """오늘 것을 이미 보냈는가. push 와 같이 outbox 의 한 칸을 빌린다."""
    record = outbox.get("kakao") if isinstance(outbox, dict) else None
    return (bool(brief_date) and isinstance(record, dict)
            and str(record.get("date") or "") == brief_date)


def record_sent(path: Path, brief_date: str, *, sent: int, total: int) -> None:
    """발송 사실을 outbox 에 적는다. 뒤의 커밋 스텝이 싣는다.
    실패해도 넘어간다 — 메시지는 이미 나갔다."""
    outbox = _read_json(path, None)
    if not isinstance(outbox, dict):
        return
    outbox["kakao"] = {
        "date": brief_date,
        "sent_at": _now().isoformat(),
        "messages": sent,
        "planned": total,
    }
    try:
        path.write_text(json.dumps(outbox, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except OSError as exc:  # pragma: no cover — 러너 디스크 사고
        print(f"[kakao] outbox 기록 실패({type(exc).__name__}) — 발송은 끝났다")


# ---- 카카오 API --------------------------------------------------------------


def _payload(response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _token_request(data: dict, client_secret: str | None) -> dict:
    if client_secret:
        data = {**data, "client_secret": client_secret}
    try:
        response = requests.post(TOKEN_URL, data=data, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise KakaoError(f"토큰 요청 실패({type(exc).__name__})") from exc
    body = _payload(response)
    if response.status_code != 200 or not body.get("access_token"):
        # 토큰 값이 섞일 수 있는 본문 전체는 찍지 않는다 — 오류 칸만 고른다.
        code = str(body.get("error") or "")
        detail = " ".join(str(body.get(k) or "") for k in
                          ("error_code", "error_description")).strip()
        raise KakaoError(f"HTTP {response.status_code} {code} {detail}".strip(), code)
    return body


def exchange_code(client_id: str, client_secret: str | None,
                  redirect_uri: str, code: str) -> dict:
    return _token_request({"grant_type": "authorization_code", "client_id": client_id,
                           "redirect_uri": redirect_uri, "code": code}, client_secret)


def refresh_tokens(client_id: str, client_secret: str | None, refresh_token: str) -> dict:
    """새 액세스 토큰. 리프레시 토큰이 한 달 미만 남았으면 새 것도 함께 온다."""
    return _token_request({"grant_type": "refresh_token", "client_id": client_id,
                           "refresh_token": refresh_token}, client_secret)


def send_to_me(access_token: str, text: str, link: str) -> None:
    template = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": link, "mobile_web_url": link},
        "button_title": BUTTON_TITLE,
    }
    try:
        response = requests.post(
            MEMO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data={"template_object": json.dumps(template, ensure_ascii=False)},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise KakaoError(f"발송 요청 실패({type(exc).__name__})") from exc
    body = _payload(response)
    if response.status_code != 200 or body.get("result_code") != 0:
        code = str(body.get("code") or "")
        hint = ""
        if code == "-402":
            hint = " — 동의항목 '카카오톡 메시지 전송(talk_message)'을 켜고 --auth 를 다시"
        raise KakaoError(f"HTTP {response.status_code} code={code} "
                         f"{str(body.get('msg') or '')[:120]}{hint}", code)


def persist_refresh_token(new_token: str, *, writer: str | None,
                          repo: str | None) -> tuple[bool, str]:
    """새 리프레시 토큰을 GitHub Secret 에 되쓴다. (성공, 실패 사유)."""
    if not writer:
        return False, "KAKAO_SECRET_WRITER 미설정"
    if not repo:
        return False, "GITHUB_REPOSITORY 미설정"
    gh = shutil.which("gh")
    if not gh:
        return False, "gh CLI 없음"
    # --body 로 넘기면 프로세스 목록에 값이 보인다. 표준입력으로 준다.
    try:
        proc = subprocess.run([gh, "secret", "set", SECRET_NAME, "--repo", repo],
                              input=new_token, text=True, capture_output=True,
                              env={**os.environ, "GH_TOKEN": writer}, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, type(exc).__name__
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[:160] or "gh 실패"
    return True, ""


# ---- 명령 --------------------------------------------------------------------


def _settings() -> tuple[str, str, str]:
    return (resolve_setting("KAKAO_REST_API_KEY") or "",
            resolve_setting("KAKAO_REFRESH_TOKEN") or "",
            resolve_setting("KAKAO_CLIENT_SECRET") or "")


def _access_token(client_id: str, refresh: str, secret: str) -> tuple[str | None, list[str]]:
    """(액세스 토큰, 함께 보낼 안내 말풍선). 실패하면 토큰이 None."""
    try:
        tokens = refresh_tokens(client_id, secret or None, refresh)
    except KakaoError as exc:
        if exc.code == "invalid_grant":
            print(f"::error::[kakao] 리프레시 토큰이 만료·폐기됐다({exc}) — "
                  "PC에서 python tools/kakao_notify.py --auth 로 다시 발급해 "
                  f"{SECRET_NAME} 를 바꿔야 한다")
        else:
            print(f"::error::[kakao] 액세스 토큰 갱신 실패 — {exc}")
        return None, []

    notices: list[str] = []
    new_refresh = tokens.get("refresh_token")
    if new_refresh and new_refresh != refresh:
        ok, why = persist_refresh_token(
            new_refresh, writer=resolve_setting("KAKAO_SECRET_WRITER"),
            repo=os.environ.get("GITHUB_REPOSITORY"))
        if ok:
            print(f"[kakao] 리프레시 토큰 갱신 → {SECRET_NAME} 교체 완료")
        else:
            # 매일 다시 뜬다 — 옛 토큰이 끊기는 날까지 알아챌 기회를 준다.
            print(f"::warning::[kakao] 리프레시 토큰이 갱신됐지만 저장하지 못했다({why}). "
                  "한 달 안에 --auth 로 다시 발급해 Secret 을 바꾸거나 "
                  "KAKAO_SECRET_WRITER 를 설정할 것")
            notices.append(REAUTH_NOTICE)
    return tokens["access_token"], notices


def cmd_send(args) -> int:
    site = (os.environ.get("SITE_URL") or DEFAULT_SITE).rstrip("/")
    outbox = _read_json(OUTBOX_FILE, {})
    outbox = outbox if isinstance(outbox, dict) else {}

    reason = skip_reason(outbox)
    if reason:
        print(f"[kakao] {reason} — 보내지 않는다")
        return 0
    brief_date = str(outbox.get("date") or "")
    messages = build_messages(outbox)
    if not messages:
        print("[kakao] 보낼 제목이 없다 — 보내지 않는다")
        return 0
    # 워크플로 밖(로컬 수동 실행)에는 이 값이 없다 — 그때는 이미 배포된 뒤다.
    deployed = (os.environ.get("WEB_DEPLOY_OUTCOME") or "success") == "success"
    link = brief_link(site, brief_date, deployed=deployed)
    print(f"[kakao] 보낼 것: {brief_date} 말풍선 {len(messages)}개 → {link}")

    if not args.force and already_sent(outbox, brief_date):
        print(f"[kakao] {brief_date} 카톡은 이미 나갔다 — 재실행이라 보내지 않는다")
        return 0
    if args.dry_run:
        for text in messages:
            print(f"--- ({text_units(text)}/{TEXT_LIMIT})\n{text}")
        return 0

    client_id, refresh, secret = _settings()
    if not client_id and not refresh:
        print("[kakao] KAKAO_REST_API_KEY·KAKAO_REFRESH_TOKEN 미설정 — 선택 기능이라 건너뛴다")
        return 0
    if not client_id or not refresh:
        missing = "KAKAO_REST_API_KEY" if not client_id else "KAKAO_REFRESH_TOKEN"
        print(f"::error::[kakao] {missing} 만 비어 있다 — 카톡 브리핑이 나가지 않는다")
        return 1

    access, notices = _access_token(client_id, refresh, secret)
    if not access:
        return 1

    outgoing = messages + notices
    sent = failed = 0
    reasons: list[str] = []
    for n, text in enumerate(outgoing):
        if n:
            time.sleep(SEND_GAP_S)
        try:
            send_to_me(access, text, link)
            sent += 1
        except KakaoError as exc:
            failed += 1
            reasons.append(str(exc)[:160])

    print(f"[kakao] 발송 {sent} · 실패 {failed} (말풍선 {len(outgoing)})")
    if reasons:
        print(f"[kakao] 실패 사유: {' | '.join(sorted(set(reasons))[:3])}")
    if sent:
        record_sent(OUTBOX_FILE, brief_date, sent=sent, total=len(outgoing))
    if failed and not sent:
        print("::error::[kakao] 전부 발송 실패 — 동의항목·도메인·토큰 설정 확인 필요")
        return 1
    if failed:
        print(f"::warning::[kakao] {len(outgoing)}개 중 {failed}개 발송 실패")
    return 0


def cmd_test() -> int:
    client_id, refresh, secret = _settings()
    if not client_id or not refresh:
        print("ERROR: KAKAO_REST_API_KEY·KAKAO_REFRESH_TOKEN 이 필요하다 (.env 또는 환경변수)")
        return 1
    access, notices = _access_token(client_id, refresh, secret)
    if not access:
        return 1
    site = (os.environ.get("SITE_URL") or DEFAULT_SITE).rstrip("/")
    try:
        for text in [TEST_TEXT, *notices]:
            send_to_me(access, text, f"{site}/")
    except KakaoError as exc:
        print(f"ERROR: 발송 실패 — {exc}")
        return 1
    print("[kakao] 테스트 메시지 발송 완료 — 카카오톡 '나와의 채팅'을 확인하세요")
    return 0


def authorize_url(client_id: str, redirect_uri: str) -> str:
    query = urllib.parse.urlencode({"response_type": "code", "client_id": client_id,
                                    "redirect_uri": redirect_uri, "scope": SCOPE})
    return f"{AUTHORIZE_URL}?{query}"


def extract_code(pasted: str) -> str:
    """붙여넣은 리디렉트 주소(또는 코드 그 자체)에서 인가 코드를 꺼낸다."""
    pasted = pasted.strip()
    if "code=" in pasted:
        query = urllib.parse.urlparse(pasted).query or pasted.split("?", 1)[-1]
        return (urllib.parse.parse_qs(query).get("code") or [""])[0]
    return pasted if pasted and "://" not in pasted else ""


def wait_for_code(redirect_uri: str, timeout: float = AUTH_WAIT_S) -> str:
    """localhost 리디렉트면 그 자리에 잠깐 서버를 띄워 인가 코드를 받는다."""
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.hostname not in ("localhost", "127.0.0.1") or not parsed.port:
        return ""
    result: dict[str, str] = {}
    want_path = parsed.path or "/"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server 규약
            url = urllib.parse.urlparse(self.path)
            if url.path != want_path:  # favicon 등
                self.send_response(404)
                self.end_headers()
                return
            query = urllib.parse.parse_qs(url.query)
            result["code"] = (query.get("code") or [""])[0]
            result["error"] = (query.get("error_description") or query.get("error") or [""])[0]
            message = ("카카오 연결 완료 — 이 창은 닫고 터미널로 돌아가세요."
                       if result["code"] else f"연결 실패: {result['error']}")
            body = f"<meta charset='utf-8'><p>{message}</p>".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # 인가 코드가 든 요청 줄을 찍지 않는다
            pass

    try:
        server = HTTPServer((parsed.hostname, parsed.port), Handler)
    except OSError as exc:
        print(f"[kakao] 로컬 서버를 못 띄웠다({exc}) — 주소를 직접 붙여넣는 방식으로 진행")
        return ""
    server.timeout = 1
    deadline = time.monotonic() + timeout
    try:
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if result.get("error"):
        print(f"[kakao] 카카오가 거절했다: {result['error']}")
    return result.get("code", "")


def cmd_auth(args) -> int:
    client_id = resolve_setting("KAKAO_REST_API_KEY") or ""
    secret = resolve_setting("KAKAO_CLIENT_SECRET") or ""
    redirect = args.redirect_uri or resolve_setting("KAKAO_REDIRECT_URI") or DEFAULT_REDIRECT
    if not client_id:
        print("ERROR: KAKAO_REST_API_KEY 가 필요하다 (.env 에 넣거나 환경변수로)")
        return 1

    url = authorize_url(client_id, redirect)
    print("1) 아래 주소에서 카카오 로그인 → '카카오톡 메시지 전송'에 동의하세요.")
    print(f"   (앱의 Redirect URI 에 {redirect} 가 등록돼 있어야 합니다)\n")
    print(f"   {url}\n")
    code = ""
    if not args.manual:
        try:
            import webbrowser  # noqa: PLC0415
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — 브라우저가 없으면 주소를 직접 연다
            pass
        code = wait_for_code(redirect)
    if not code:
        pasted = input("2) 로그인 후 주소창의 URL(…?code=…)을 통째로 붙여넣으세요: ")
        code = extract_code(pasted)
    if not code:
        print("ERROR: 인가 코드를 찾지 못했다")
        return 1

    try:
        tokens = exchange_code(client_id, secret or None, redirect, code)
        send_to_me(tokens["access_token"], TEST_TEXT, f"{DEFAULT_SITE}/")
    except KakaoError as exc:
        print(f"ERROR: {exc}")
        return 1

    refresh = tokens.get("refresh_token") or ""
    days = int(tokens.get("refresh_token_expires_in") or 0) // 86400
    print("\n✅ 카카오톡 '나와의 채팅'으로 테스트 메시지를 보냈습니다.")
    print(f"\n{SECRET_NAME} (유효 약 {days}일) — GitHub Secret 에 그대로 넣으세요:\n")
    print(f"   {refresh}\n")
    print("   GitHub → Settings → Secrets and variables → Actions → New repository secret")
    print(f"   또는: gh secret set {SECRET_NAME}   (실행 후 값 붙여넣기)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="카카오톡 '나에게 보내기' 아침 브리핑")
    parser.add_argument("--auth", action="store_true",
                        help="최초 1회: 카카오 로그인 → 리프레시 토큰 발급")
    parser.add_argument("--manual", action="store_true",
                        help="--auth 에서 브라우저·로컬 서버 없이 주소를 직접 붙여넣는다")
    parser.add_argument("--redirect-uri", default="",
                        help=f"--auth 의 Redirect URI (기본 {DEFAULT_REDIRECT})")
    parser.add_argument("--test", action="store_true", help="연결 확인용 한 줄 발송")
    parser.add_argument("--force", action="store_true",
                        help="오늘 이미 보냈다는 기록을 무시하고 보낸다")
    parser.add_argument("--dry-run", action="store_true",
                        help="보낼 말풍선만 찍고 보내지 않는다")
    args = parser.parse_args(argv)

    if args.auth:
        return cmd_auth(args)
    if args.test:
        return cmd_test()
    return cmd_send(args)


if __name__ == "__main__":
    raise SystemExit(main())
