"""달력 전용 공식 일정 수집 → event_schedule.json.

왜 뉴스 파이프라인이 아닌가
---------------------------
`event_calendar` 는 이미 수집된 기사에서 일정을 유도한다. 그래서 **보도되지 않은
일정은 존재하지 않는다.** 실측(2026-08-29)으로 그 구멍이 얼마나 큰지 확인했다:

  · 한국원자력학회 공지 "AI 시대 국가경쟁력을 위한 전원믹스와 시장제도 심포지움
    개최(9.9(수) 14:00, 대한상공회의소)" — 8월 14일에 올라왔고, 날짜·시각·장소가
    공지 제목 한 줄에 다 있다. 어떤 기사도 이 심포지움을 예고하지 않았다.
  · 국회 행사알림 2026-09-03 "국가전력망 민간참여 본격화… 전력산업 공공성 강화를
    위한 정책 연속세미나"(김주영 의원실·혁신더하기연구소·전기신문, 의원회관
    제7간담회의실) — 국회 일정 API 가 주최·장소·시각을 구조화해 주는데, 이런
    의원실 토론회는 열리기 전에는 기사가 되지 않는다.
  · 한국원자력산업협회 Monthly Calendar 는 11월 행사까지 이미 올라와 있다.
    뉴스는 대개 행사 며칠 전에야 쓴다 — 30일 창에서 보이는 시점 차가 크다.

그래서 **기사와 무관한 두 번째 수집 경로**를 둔다. news_bot 에 섞지 않는 이유는
셋이다. ① 이 일정들은 기사가 아니라 아카이브·이슈 클러스터링·랭킹에 들어가면
안 된다. ② 게시판 파서는 사이트 개편 한 번에 조용히 죽는데, 그 실패가 뉴스
수집을 흔들면 안 된다. ③ 달력은 화면 한 칸이고, 죽어도 사이트는 나가야 한다.
`pubs_fetch` 가 발간물 탭에 대해 이미 같은 모양을 하고 있다 — 그 선례를 따른다.

수집원 (2026-08-29 실검증)
--------------------------
  kns_notice      한국원자력학회 공지사항. `<ul class="board-list">` 서버렌더.
                  제목 안에 "(9.9(수) 14:00, 대한상공회의소)" 꼴로 날짜·시각·
                  장소가 함께 온다.
  kaif_notice     한국원자력산업협회 공지사항(`/ko/?c=193`). 제목 끝의
                  "(~ 9. 8. 15:00)" 이 마감일이다. **루트를 먼저 한 번 받아야**
                  한다 — 세션 없이 `?c=193` 을 바로 치면 "유효하지 않은 문자"
                  alert 86 바이트가 돌아온다(실측).
  kaif_calendar   같은 협회의 Monthly Calendar AJAX(`ax.204.php`). 구분·행사명·
                  기간·장소·웹사이트가 표로 오는 **이미 만들어진 원자력 행사
                  일정표**다. 576건이 쌓여 있고 몇 달 앞까지 들어온다. 이 모듈이
                  붙인 수집원 중 단연 밀도가 높다.
  niftep_notice   서울대 원자력미래기술정책연구소 학술/세미나 공지. 목록 링크가
                  JS(`eclick('view',IDX)`)라 상세 URL 을 `?mode=view&bid=5&idx=`
                  로 만들어 붙인다(실검증: GET 으로 상세가 열린다).
                  **브라우저 UA 가 아니면 웹방화벽이 막는다** — `nuclear-news-
                  bot/1.0` 으로는 "Firewall Alert" 404 가 온다.
                  다만 게시글이 19건뿐이고 최신 글이 2026-02-11 이다. 수확은
                  거의 없다고 보는 편이 맞고, 그래도 두는 이유는 이 연구소가
                  여는 워크숍이 정책 쪽에서 값이 크기 때문이다.
  assembly_events 국회 행사알림. 월 단위 JSON 으로 '무슨 일이 있는 날'을 먼저
                  받고(`findSchlDaySmn.json`), 일정이 있는 날만 상세를 받는다
                  (`findSchlSmn.json`). 제목·시각·장소·주최·링크가 그대로 온다.
  kpx_notice      한국전력거래소 공지사항(`new.kpx.or.kr/board.es?…bid=0042`).
                  서버렌더 표라 목록은 쉽게 읽히는데, **이 게시판만 상세 본문까지
                  받는다** — 제목은 행사 이름만 말하고 행사일은 본문의 '일 시 :'
                  줄에 있다(실측 2026-09-10: '2026년 ESS중앙계약시장 사업자
                  의견수렴 간담회 개최' / 등록일 7/23 / 본문 '1. 일 시 : 2026년
                  7월 29일(수) 14:00'). 등록일을 행사일로 쓰면 6일 어긋난 칸이
                  선다. 전기본 공개토론회·전력거버넌스 포럼처럼 본문이 포스터
                  이미지 한 장뿐인 공지도 흔한데, 그런 글은 행사일을 모르는 것이
                  사실이라 칸을 세우지 않는다(`no_event_date`).

넣지 않은 것과 이유 (재시도 전에 여기부터 볼 것)
  한국원자력환경공단  공지사항 상위가 전부 조달·홍보다(PQ 평가기준·공급업체
                      등록안내·사진 공모전). 목록 링크도 JS `data-keyValue` 라
                      상세 URL 이 없다. 방폐물 일정은 오히려 KAIF 달력에 뜬다.
  혁신형SMR사업단     `ismr.or.kr` 루트가 399 바이트 껍데기다 — 서버렌더 목록이
                      없어 붙일 자리가 없다.

무엇을 하지 않는가
------------------
* LLM 을 부르지 않는다. 날짜도 이름도 원문의 부분 문자열이다.
* 공식이라고 통과시키지 않는다. 모든 행은 `event_relevance.judge` 를 지난다.
* 게시판 하나가 죽어도 나머지는 계속 걷는다(소스별 try/except 격리).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import event_relevance
from data_quality import clean_text, normalize_url

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

BASE = Path(__file__).parent
OUT_FILE = BASE / "event_schedule.json"
KST = timezone(timedelta(hours=9))

# 기관 사이트는 봇 UA 를 막는다. 실측: niftep.snu.ac.kr 은 `nuclear-news-bot/1.0`
# 에 웹방화벽 404("Firewall Alert")를 주고 브라우저 UA 에는 200 을 준다. 사람이
# 보는 것과 같은 공개 페이지를 같은 빈도로 한 번 받을 뿐이므로 UA 를 맞춘다.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 25

# 앞으로 이만큼까지의 일정만 저장한다. 달력 창은 30일이지만 협회 일정표는 몇 달
# 앞을 알려 주므로, 창에 들어올 때까지 들고 있다가 그날 칸에 세운다.
HORIZON_DAYS = 400
# 지난 일정을 바로 지우지 않는 까닭: 갓 지난 일정이 남아 있어야 그 일을 뒤늦게
# 다룬 기사가 같은 사건으로 접힌다. 달력 자체는 창 밖을 어차피 안 그린다.
KEEP_PAST_DAYS = 14
MAX_EVENTS = 600


# ── 날짜 읽기 ────────────────────────────────────────────────────────────
#
# `article_quality_gate.explicit_dates` 를 그대로 쓰지 못한다. 그 추출기는 기사
# 본문의 어법("9월 1일", "2026.11.16")을 읽는데, 공지 제목은 줄인 꼴을 쓴다 —
# 실측으로 셋 다 빈 결과가 나왔다:
#     "…심포지움 개최(9.9(수) 14:00, 대한상공회의소)"   → ()
#     "…시행공고 (~ 9. 8. 15:00)"                        → ()
#     "…포럼 참가자 모집(~9. 10.)"                       → ()
# 그래서 공지 제목 전용 파서를 둔다. 대신 **증거 없이는 날짜로 인정하지 않는다**
# (아래 _MD_EVIDENCE): 줄인 'M.D' 는 요일·시각·물결표·개최/마감 같은 말이 함께
# 있을 때만 날짜다. 없으면 '3. 인력'의 '3.'이나 버전 번호가 날짜가 된다.

_YMD_RE = re.compile(r"(?<!\d)(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*[.일]?(?!\d)")
_MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[.월]\s*(\d{1,2})\s*(?:일|\.)?(?!\d)")
# 줄인 M.D 를 날짜로 인정하는 근거. 하나라도 있어야 한다.
_MD_EVIDENCE = re.compile(
    r"\([월화수목금토일]\)|\d{1,2}\s*:\s*\d{2}|~|∼|-\s*\d{1,2}\s*[.월]|"
    r"개최|마감|까지|부터|접수|신청|모집|예정|열림|개막|시행|공고")
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})\s*:\s*(\d{2})(?!\d)")
_RANGE_MARK_RE = re.compile(r"~|∼|–|—|부터|to\b")
_DEADLINE_MARK_RE = re.compile(r"마감|까지|접수|신청|모집|제출|deadline|due")


def _resolve_year(month: int, day: int, posted: date) -> date | None:
    """연도 없는 'M.D' 를 게시일 기준으로 푼다.

    게시일보다 한참 이전이면 다음 해다 — 12월에 올라온 '1. 15.' 공지가 그렇다.
    한 달치 여유를 두는 이유는 갓 지난 날짜(8/24 게시, '8. 31.' 마감이 아니라
    '8. 20.' 회고)를 내년으로 밀어 보내지 않기 위해서다.
    """
    for year in (posted.year, posted.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate >= posted - timedelta(days=30):
            return candidate
    return None


def notice_dates(text: object, posted: date) -> list[date]:
    """공지 제목·본문이 **실제로 적은** 날짜들. 적혀 있지 않으면 빈 목록이다."""
    raw = clean_text(text)
    if not raw:
        return []
    found: list[date] = []
    # 연도가 붙은 표기를 먼저 먹고 그 자리를 지운다. 안 그러면 "2026.11.16" 의
    # 뒤쪽 "11.16" 이 줄인 M.D 로 한 번 더 잡힌다.
    def _take_ymd(match: re.Match) -> str:
        year, month, day = (int(part) for part in match.groups())
        try:
            found.append(date(year, month, day))
        except ValueError:
            pass
        return " " * len(match.group(0))
    rest = _YMD_RE.sub(_take_ymd, raw)
    if _MD_EVIDENCE.search(raw):
        for match in _MD_RE.finditer(rest):
            month, day = int(match.group(1)), int(match.group(2))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            resolved = _resolve_year(month, day, posted)
            if resolved is not None:
                found.append(resolved)
    # 같은 날짜를 두 번 적은 공지가 있다. 순서는 지키고 중복만 없앤다.
    out: list[date] = []
    for day in found:
        if day not in out:
            out.append(day)
    return out


def notice_time(text: object) -> str:
    """제목이 적은 시각. 없으면 빈 문자열 — 지어내지 않는다."""
    match = _TIME_RE.search(clean_text(text))
    if not match:
        return ""
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def notice_span(text: object, posted: date) -> tuple[date, date, str] | None:
    """공지가 말하는 구간과 종류. 날짜가 없으면 None.

    두 날짜가 물결표·'부터'로 이어져 있으면 기간이고, 마감 표지가 있으면 그
    날짜는 '그날 무엇을 한다'가 아니라 '그날까지'다 — `event_calendar` 의
    point/deadline/range 어휘를 그대로 쓴다.
    """
    days = notice_dates(text, posted)
    if not days:
        return None
    raw = clean_text(text)
    if len(days) >= 2:
        start, end = min(days[:2]), max(days[:2])
        if start != end and _RANGE_MARK_RE.search(raw):
            return start, end, "range"
    when = days[0]
    if _DEADLINE_MARK_RE.search(raw):
        return when, when, "deadline"
    return when, when, "point"


# ── 수집 결과 한 줄 ──────────────────────────────────────────────────────

def _event_id(source_id: str, url: str, label: str, when: str) -> str:
    seed = f"{source_id}|{url}|{re.sub(r'\s+', '', label)}|{when}"
    return "of-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def make_event(*, source_id: str, publisher: str, title: str, url: str,
               posted: date, start: date, end: date, kind: str,
               organizer: str = "", host: str = "", place: str = "",
               time: str = "", label: str = "", post_id: str = "") -> dict | None:
    """공식 일정 한 건. 판정을 통과한 것만 돌려준다.

    `label` 을 따로 받는 것은 협회 일정표처럼 행사명이 제목과 별개의 칸으로
    오는 경우 때문이다. 없으면 공지 제목이 곧 이름이다.

    주최는 두 칸으로 받는다. `organizer` 는 **행사마다 다른 값**(국회 행사알림의
    `orgNm`)이라 판정에도 쓰고 화면에도 쓴다. `host` 는 게시판 주인이 곧 주최인
    경우(학회가 제 학회 공지에 올린 심포지엄)에만 채우는 **표시 전용** 값이고
    판정에는 절대 들어가지 않는다 — 넣었다가 기관 이름이 관심어로 걸려 게시판에
    실린 것이 전부 통과했다(`event_relevance.judge` 머리말의 Smart Energy Week
    실측).

    협회 Monthly Calendar 처럼 남의 행사를 모아 싣는 일정표는 **둘 다 비운다.**
    협회는 그 행사의 주최가 아니라 소개자다 — WNA Symposium 의 주최를
    '한국원자력산업협회'로 적으면 그 자리에서 거짓말이 된다. 그런 행은 주최 없이
    나가고, 화면은 `publisher` 를 '출처'로 따로 밝힌다.
    """
    title = clean_text(title)
    label = clean_text(label) or title
    link = normalize_url(url)
    if not title or not link:
        return None
    verdict = event_relevance.judge(title, organizer, place, label)
    if not verdict["ok"]:
        return {"_dropped": verdict["reason"]}
    return {
        "id": _event_id(source_id, link, label, start.isoformat()),
        "date": start.isoformat(),
        "end_date": end.isoformat(),
        "kind": kind,
        "time": time,
        "label": label,
        "host": clean_text(organizer) or clean_text(host),
        # 판정에 실제로 쓴 주최 문자열. 표시용 `host` 와 갈라 두는 이유는
        # 빌드가 이 판정을 **똑같은 입력으로** 다시 재기 때문이다
        # (`event_calendar.verify_official`). 표시용 값을 대신 넣으면 두 판정의
        # 입력이 달라져, 수집이 막은 것을 빌드가 통과시킬 자리가 생긴다.
        "organizer": clean_text(organizer),
        "place": clean_text(place),
        "url": link,
        "source_id": source_id,
        "publisher": publisher,
        # 근거. 달력 상세가 이 한 줄을 그대로 보여 준다 — 짧은 이름이 아니라
        # 기관이 실제로 쓴 문장이 근거라는 원칙은 뉴스 경로와 같다.
        "notice_title": title,
        # 최초 확인일. 게시판이 게시일을 주면 그날이고(그 기관이 처음 알린 날),
        # 안 주면 우리가 처음 본 날이다. 아래 merge 가 이 값을 낮은 쪽으로 지킨다.
        "first_seen": posted.isoformat(),
        "topics": verdict["topics"],
        "relevance": verdict["grounds"].get("relevance", ""),
        "significance": verdict["grounds"].get("significance", ""),
        # 게시판이 주는 안정적인 게시물 번호. 날짜·장소가 바뀌면 `id` 는 바뀌지만
        # 이 값은 안 바뀐다 — `merge_events` 가 이 값으로 옛 줄을 찾아 옮긴다.
        # 그래야 연기·장소변경 공지가 새 일정이 아니라 **같은 일정의 갱신**이 된다.
        **({"post_id": str(post_id)} if post_id else {}),
    }


# ── 소스별 파서 (fixture 로 단위 테스트한다) ─────────────────────────────

_KNS_ROW_RE = re.compile(r'<ul class="board-list">([\s\S]*?)</ul>', re.I)
_KNS_LINK_RE = re.compile(r'<a href="(/boards/chk_view/[^"]+)"[^>]*>([\s\S]*?)</a>', re.I)
_KNS_DATE_RE = re.compile(r'<span class="date">\s*(\d{4}-\d{2}-\d{2})', re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_kns_notice(page: str, *, base: str = "https://www.kns.org/") -> list[dict]:
    """한국원자력학회 공지사항 목록 → 일정 후보."""
    out: list[dict] = []
    for block in _KNS_ROW_RE.findall(page):
        link = _KNS_LINK_RE.search(block)
        day = _KNS_DATE_RE.search(block)
        if not (link and day):
            continue
        title = clean_text(_TAG_RE.sub(" ", link.group(2)))
        posted = date.fromisoformat(day.group(1))
        span = notice_span(title, posted)
        if span is None:
            continue
        start, end, kind = span
        event = make_event(
            source_id="kns_notice", publisher="한국원자력학회", title=title,
            url=urljoin(base, link.group(1)), posted=posted,
            start=start, end=end, kind=kind, host="한국원자력학회",
            label=_label_from_title(title),
            place=_place_in_title(title), time=notice_time(title))
        if event:
            out.append(event)
    return out


def _trailing_paren(title: str) -> tuple[int, str] | None:
    """제목 끝 괄호 — 여는 자리와 그 안. 없으면 None.

    정규식 하나로 잡으려다 틀렸다. 바깥 괄호 안에 요일 괄호가 한 번 더 들어
    있어서(`…개최(9.9(수) 14:00, 대한상공회의소)`) '괄호 안에 괄호가 없다'는
    패턴이 아예 안 걸렸다. 그래서 뒤에서부터 짝을 세어 바깥 여는 괄호를 찾는다.
    """
    text = clean_text(title)
    if not text.endswith((")", "）")):
        return None
    depth = 0
    for at in range(len(text) - 1, -1, -1):
        if text[at] in ")）":
            depth += 1
        elif text[at] in "(（":
            depth -= 1
            if depth == 0:
                return at, text[at + 1:-1]
    return None


def _place_in_title(title: str) -> str:
    """제목 끝 괄호의 마지막 조각이 장소인 꼴 — "(9.9(수) 14:00, 대한상공회의소)".

    날짜·시각으로 읽히는 조각은 장소가 아니다. 못 찾으면 빈 문자열 — 없는
    장소를 지어내지 않는다.
    """
    found = _trailing_paren(title)
    if not found:
        return ""
    tail = found[1].split(",")[-1].strip()
    if not tail or _MD_RE.search(tail) or _TIME_RE.search(tail):
        return ""
    return tail if 2 <= len(tail) <= 40 else ""


def _label_from_title(title: str) -> str:
    """칸에 설 이름. 이미 칸으로 뽑아낸 날짜·시각·장소 괄호는 뗀다.

    떼는 이유는 화면이 같은 말을 두 번 하기 때문이다 — 칸이 '9월 9일'을,
    칩 이름이 '…심포지움 개최(9.9(수) 14:00, 대한상공회의소)'를 말하면 좁은
    칸에서 정작 행사 이름이 잘린다.

    **뗀 말은 사라지지 않는다.** 공지 제목 원문은 `notice_title` 로 그대로
    남아 근거가 되고(달력 상세가 그것을 보여 준다), 날짜·시각·장소는 각자의
    칸에 들어간다. 그래서 이 자르기는 정보를 버리는 것이 아니라 옮기는 것이다.
    날짜가 안 들어 있는 괄호는 건드리지 않는다 — '(2차)'·'(NECX)'는 이름의 일부다.
    """
    found = _trailing_paren(title)
    if not found:
        return clean_text(title)
    at, inner = found
    if not (_MD_RE.search(inner) or _YMD_RE.search(inner)):
        return clean_text(title)
    trimmed = clean_text(title)[:at].strip()
    return trimmed or clean_text(title)


_KAIF_ROW_RE = re.compile(r"<tr>([\s\S]*?)</tr>", re.I)
_KAIF_LINK_RE = re.compile(r'<td class="col-tit">\s*<a href="([^"]+)"[^>]*>([\s\S]*?)</a>', re.I)
_KAIF_DATE_RE = re.compile(r'<td class="col-date">\s*(\d{4})[.\-](\d{2})[.\-](\d{2})', re.I)


def parse_kaif_notice(page: str, *,
                      base: str = "https://www.kaif.or.kr/ko/") -> list[dict]:
    """한국원자력산업협회 공지사항 목록 → 일정 후보."""
    out: list[dict] = []
    for block in _KAIF_ROW_RE.findall(page):
        link = _KAIF_LINK_RE.search(block)
        day = _KAIF_DATE_RE.search(block)
        if not (link and day):
            continue
        title = clean_text(_TAG_RE.sub(" ", link.group(2)))
        posted = date(*(int(part) for part in day.groups()))
        span = notice_span(title, posted)
        if span is None:
            continue
        start, end, kind = span
        event = make_event(
            source_id="kaif_notice", publisher="한국원자력산업협회", title=title,
            url=urljoin(base, clean_text(link.group(1))), posted=posted,
            start=start, end=end, kind=kind, host="한국원자력산업협회",
            label=_label_from_title(title), time=notice_time(title))
        if event:
            out.append(event)
    return out


_CAL_CELL_RE = re.compile(r"<td[^>]*>([\s\S]*?)</td>", re.I)
_CAL_HREF_RE = re.compile(r'href="([^"]+)"', re.I)
_CAL_SPAN_RE = re.compile(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})")


def parse_kaif_calendar(page: str, *, today: date,
                        page_url: str = "https://www.kaif.or.kr/ko/?c=240") -> list[dict]:
    """협회 Monthly Calendar 표 → 일정 후보.

    칸 차례는 번호·구분·행사명·기간·장소·웹사이트다. 기간은 "2026.11.16 ~
    2026.11.18" 또는 하루짜리 "2026.09.04" 로 온다.

    이 표에는 **게시일 칸이 없다.** 그래서 최초 확인일은 우리가 처음 본 날이고,
    저장본이 그 값을 지킨다(`merge_events`).
    """
    out: list[dict] = []
    for block in _KAIF_ROW_RE.findall(page):
        cells = _CAL_CELL_RE.findall(block)
        if len(cells) < 5:
            continue
        text = [clean_text(_TAG_RE.sub(" ", cell)) for cell in cells]
        category, label, span_text, place = text[1], text[2], text[3], text[4]
        days = [date(*(int(part) for part in match.groups()))
                for match in _CAL_SPAN_RE.finditer(span_text)]
        if not label or not days:
            continue
        start, end = min(days), max(days)
        kind = "range" if end > start else "point"
        # 웹사이트 칸의 링크가 있으면 그 행사의 1차 출처다. 없으면 일정표 자체를
        # 가리킨다 — 근거 URL 이 없는 칩은 만들지 않는다.
        site = _CAL_HREF_RE.search(cells[5] if len(cells) > 5 else "")
        url = clean_text(site.group(1)) if site else ""
        if url and not url.startswith("http"):
            url = "https://" + url.lstrip("/")
        event = make_event(
            source_id="kaif_calendar", publisher="한국원자력산업협회",
            title=f"{label} ({category})" if category else label,
            label=label, url=url or page_url, posted=today,
            start=start, end=end, kind=kind, place=place)
        if event:
            out.append(event)
    return out


_NIFTEP_ROW_RE = re.compile(r"<li>([\s\S]*?)</li>", re.I)
_NIFTEP_LINK_RE = re.compile(r"eclick\('view',(\d+)\)[^>]*>([\s\S]*?)</a>", re.I)
_NIFTEP_DATE_RE = re.compile(r'col_date">\s*(\d{4}-\d{2}-\d{2})', re.I)


def parse_niftep_notice(
        page: str, *,
        base: str = "https://niftep.snu.ac.kr/kr/sub/notice/notice.asp") -> list[dict]:
    """서울대 원자력미래기술정책연구소 학술/세미나 공지 → 일정 후보."""
    out: list[dict] = []
    for block in _NIFTEP_ROW_RE.findall(page):
        link = _NIFTEP_LINK_RE.search(block)
        day = _NIFTEP_DATE_RE.search(block)
        if not (link and day):
            continue
        title = clean_text(_TAG_RE.sub(" ", link.group(2)))
        posted = date.fromisoformat(day.group(1))
        span = notice_span(title, posted)
        if span is None:
            continue
        start, end, kind = span
        event = make_event(
            source_id="niftep_notice",
            publisher="서울대 원자력미래기술정책연구소", title=title,
            url=f"{base}?mode=view&bid=5&idx={link.group(1)}", posted=posted,
            start=start, end=end, kind=kind, label=_label_from_title(title),
            host="서울대 원자력미래기술정책연구소", time=notice_time(title))
        if event:
            out.append(event)
    return out


# ── 전력거래소 공지사항 ──────────────────────────────────────────────────
#
# 다른 게시판과 **읽는 방법이 다르다.** KNS·KAIF 는 제목 한 줄에 날짜·시각·장소가
# 다 들어 있어("…심포지움 개최(9.9(수) 14:00, 대한상공회의소)") 목록만으로 칸을
# 세울 수 있다. KPX 공지는 그렇지 않다 — 제목은 행사 이름만 말하고 날짜는 본문
# 안쪽의 '일 시 :' 줄에 있다(실측 2026-09-10):
#
#     제목  2026년 ESS중앙계약시장 사업자 의견수렴 간담회 개최   (등록일 2026/07/23)
#     본문  1. 일 시 : 2026년 7월 29일(수) 14:00 ~ 15:30
#           2. 장 소 : 스페이스쉐어 서울중부센터 9층 스카이홀
#
# 그래서 이 수집원만 **상세 본문까지 받는다.** 목록의 등록일은 게시일이지
# 행사일이 아니다 — 그 둘을 섞으면 7월 23일 칸에 7월 29일 간담회가 선다.
#
# 날짜는 **라벨이 붙은 줄에서만** 읽는다. 본문 전체를 훑으면 신청 마감이 행사일이
# 된다(실측 77367: 'ㅇ 일시 : 5/26(화) 10시~12시' 와 'ㅇ 참석 방법 : 5/19(화)까지
# …제출' 이 같은 본문에 있다). 라벨이 없으면 날짜를 만들지 않는다 — 실측으로
# 제12차 전기본 공개토론회(78098)와 전력거버넌스 포럼(77122)은 본문이 포스터
# 이미지 한 장뿐이고, VPP 설명회 의향조사(77918)는 '개최 일자 : 10월 말(구체적
# 날짜는 추후 재 공지)' 이다. 셋 다 행사일을 모르는 것이 사실이라 칸을 세우지 않고
# 후보로만 남긴다.
#
# 판정은 다른 게시판과 **똑같이** 제목·이름·장소만 본다. 게시판 주인
# ('한국전력거래소')은 넘기지 않는다 — 넘겼다가 그 안의 '전력거래'가 관심어로
# 걸려 게시판에 실린 것이 전부 통과한 선례가 있다(event_relevance.judge 머리말).

KPX_SITE = "https://new.kpx.or.kr"
KPX_BOARD_PATH = "/board.es?mid=a10501010000&bid=0042"
KPX_BOARD_URL = KPX_SITE + KPX_BOARD_PATH
# 한 쪽에 10건. 하루 한 번 도는 수집이라 두 쪽(20건)이면 주말을 끼고도 새 글을
# 놓치지 않는다. 더 받아 봐야 이미 본 글을 다시 판정할 뿐이다.
KPX_LIST_PAGES = 2
# 상세를 받는 건수 상한. 목록 판정을 통과한 글만 받으므로 평소엔 0~2건이다.
# 게시판이 개편돼 판정이 무너지는 날 20건을 연달아 받지 않도록 뚜껑을 둔다.
KPX_MAX_DETAILS = 8

_KPX_ROW_RE = re.compile(r"<tr>([\s\S]*?)</tr>", re.I)
_KPX_LINK_RE = re.compile(r'<a href="([^"]*act=view[^"]*)"[^>]*>([\s\S]*?)</a>', re.I)
_KPX_DATE_RE = re.compile(r'aria-label="등록일"[^>]*>\s*(\d{4})/(\d{2})/(\d{2})')
_KPX_POST_RE = re.compile(r"list_no=(\d+)")
_KPX_VIEW_RE = re.compile(r'<article class="board_view">([\s\S]*?)</article>', re.I)
_KPX_BREAK_RE = re.compile(r"<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", re.I)
# 목록 제목 앞의 새글 표식(`<span class="sr_only">새글</span>`). 떼지 않으면
# 행사 이름 앞에 '새글' 이 남는다. **태그째로** 떼는 이유는 글자만 지우면
# 'New Nuclear' 같은 영문 제목에서 낱말 한 조각이 함께 사라지기 때문이다.
_KPX_BADGE_RE = re.compile(r'<span class="sr_only">[\s\S]*?</span>', re.I)


def _kpx_label(*names: str) -> re.Pattern:
    """본문에서 '무엇이 적힌 줄인가'를 정하는 라벨. 값은 콜론 뒤다.

    글자 사이에 공백을 허용한다. 관공서 표기가 '일 시'·'장 소'처럼 자간을
    벌려 쓰는데(실측: KPX 공지 본문의 '1. 일 시 :'), 붙여 쓴 꼴만 적어 두면
    정작 가장 흔한 표기를 못 읽는다.
    """
    spaced = [r"\s*".join(re.escape(ch) for ch in name if not ch.isspace())
              for name in names]
    return re.compile(
        r"^\s*(?:[0-9]{1,2}\s*[.)]|[ㅇ○●·•▪◦□■*\-–]|[가-힣]\s*[.)])?\s*"
        rf"(?:{'|'.join(spaced)})\s*[:：]\s*(.+)$")


_KPX_WHEN_RE = _kpx_label("일시", "일 자", "일자", "행사일", "행사일시", "개최일",
                          "개최일시", "개최 일자", "개최일자", "기간", "행사기간",
                          "개최기간", "교육일시", "설명회 일시", "토론회 일시")
_KPX_PLACE_RE = _kpx_label("장소", "개최장소", "행사장소", "개최 장소")
_KPX_HOST_RE = _kpx_label("주최", "주관", "주최/주관", "주최 주관", "주최·주관")
_KPX_AUDIENCE_RE = _kpx_label("참가대상", "참석대상", "대상", "참여대상")
_KPX_DEADLINE_RE = _kpx_label("신청기간", "접수기간", "신청마감", "접수마감",
                              "등록마감", "신청 방법", "참가신청")
# 'M/D' 는 KPX 본문이 즐겨 쓰는 표기다("일시 : 5/26(화) 10시~12시"). 공용
# `notice_dates` 의 줄임 표기 규칙(`_MD_RE`)에는 슬래시가 없다 — 거기에 넣으면
# 모든 게시판에서 '1/2' 같은 분수·비율이 날짜가 된다. 그래서 여기서만 점으로
# 바꿔 넘긴다.
_KPX_SLASH_MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})(?!\d)")
# '10시', '오후 2시 30분' 처럼 콜론이 없는 시각. 공용 `notice_time` 은 HH:MM 만 본다.
_KPX_HOUR_RE = re.compile(r"(?<!\d)(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?")
_KPX_PM_RE = re.compile(r"오후|pm", re.I)
# 장소 칸에 값 대신 '아직 모른다'가 적히는 경우. 없는 장소를 지어내지 않는다.
_KPX_UNSET = ("미정", "추후", "별도 공지", "별도공지", "추후 공지", "확정 시")


def parse_kpx_list(page: str, *, base: str = KPX_BOARD_URL) -> list[dict]:
    """공지사항 목록 → 게시물 한 줄들. **아직 일정이 아니다.**

    돌려주는 것은 게시물 자체다(제목·게시일·상세 URL·게시물 번호). 일정이 되려면
    상세 본문에서 행사일을 찾아야 하고, 그건 `kpx_events` 가 한다. 이 함수가
    일정을 만들지 않는 것이 곧 '게시일을 행사일로 쓰지 않는다'는 계약이다.
    """
    out: list[dict] = []
    for block in _KPX_ROW_RE.findall(page):
        link = _KPX_LINK_RE.search(block)
        day = _KPX_DATE_RE.search(block)
        if not (link and day):
            continue
        title = clean_text(_TAG_RE.sub(" ", _KPX_BADGE_RE.sub(" ", link.group(2))))
        href = clean_text(link.group(1)).replace("&amp;", "&")
        post = _KPX_POST_RE.search(href)
        if not (title and post):
            continue
        out.append({
            "post_id": post.group(1),
            "title": title,
            "posted": date(*(int(part) for part in day.groups())),
            # 목록 링크에는 그때의 쪽 번호(`nPage`)가 붙어 있다. 새 글이 올라오면
            # 같은 게시물의 쪽 번호가 밀리고, 그 URL 을 그대로 쓰면 `_event_id` 가
            # 날마다 달라져 같은 행사가 새 일정으로 다시 선다. 게시물 번호만으로
            # 표준 주소를 만든다(실측: 이 주소로 상세가 그대로 열린다).
            "url": urljoin(base, f"{KPX_BOARD_PATH}&act=view&list_no={post.group(1)}"),
        })
    return out


def kpx_body_text(page: str) -> str:
    """상세 페이지 → 본문 줄들. 태그를 떼되 **줄 경계는 지킨다.**

    줄이 무너지면 '일 시' 줄의 값과 '참석 방법' 줄의 마감일이 한 줄로 붙고,
    그 순간 라벨로 날짜를 고르는 이 파서의 전제가 사라진다.
    """
    view = _KPX_VIEW_RE.search(page)
    if not view:
        return ""
    body = _KPX_BREAK_RE.sub("\n", view.group(1))
    text = _TAG_RE.sub(" ", body).replace("&nbsp;", " ").replace("&amp;", "&")
    lines = [clean_text(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _kpx_field(text: str, pattern: re.Pattern) -> str:
    """라벨이 붙은 줄의 값. 없으면 빈 문자열."""
    for line in text.splitlines():
        found = pattern.match(line)
        if found:
            value = clean_text(found.group(1))
            if value:
                return value
    return ""


def kpx_time(clause: str) -> str:
    """행사 시각. HH:MM 을 먼저 보고, 없으면 '10시'·'오후 2시 30분' 을 읽는다."""
    explicit = notice_time(clause)
    if explicit:
        return explicit
    found = _KPX_HOUR_RE.search(clause)
    if not found:
        return ""
    hour = int(found.group(1))
    minute = int(found.group(2) or 0)
    if _KPX_PM_RE.search(clause[:found.start()]) and hour < 12:
        hour += 12
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def kpx_place(clause: str) -> str:
    """행사 장소. '추후 별도 공지' 처럼 값이 아닌 값은 버린다."""
    head = clean_text(clause.split("(")[0]) or clean_text(clause)
    if not head or any(mark in head for mark in _KPX_UNSET):
        return ""
    return head[:60]


def kpx_event_span(text: str, posted: date):
    """본문이 **행사일이라고 말한 자리**에서만 날짜를 읽는다.

    `notice_span` 을 라벨 줄에만 걸어 준다. 라벨이 없으면 None — 게시일로
    대신하지 않고, '며칠 뒤' 같은 추정도 하지 않는다.
    """
    clause = _kpx_field(text, _KPX_WHEN_RE)
    if not clause:
        return None, ""
    normalized = _KPX_SLASH_MD_RE.sub(r"\1.\2.", clause)
    span = notice_span(normalized, posted)
    if span is None:
        return None, clause
    start, end, kind = span
    # 이 줄은 '언제 여는가'를 말하는 자리다. 그 줄에 '까지·마감' 이 섞여 있어도
    # 행사일은 행사일이다 — 마감으로 바꾸면 달력이 접수 창을 행사로 그린다.
    return (start, end, "range" if end > start else "point"), clause


def kpx_events(rows: list[dict], fetch_body, *,
               max_details: int = KPX_MAX_DETAILS) -> list[dict]:
    """게시물 목록 → 일정 후보. 판정을 통과한 글만 상세를 받는다.

    상세를 먼저 받고 판정하지 않는 이유는 요청 수다. 목록 20건 중 채용·입찰·
    개인정보 처리방침 공지가 대부분이고, 그것들은 제목만으로 확실히 걸린다.
    판정을 통과한 글만 본문을 열면 하루 상세 요청이 평소 0~2건이다.

    `fetch_body(row) -> str` 를 주입받는 까닭은 fixture 로 단위 테스트하기
    위해서다(다른 파서들이 페이지 문자열을 받는 것과 같은 이유).
    """
    out: list[dict] = []
    opened = 0
    for row in rows:
        # 제목만으로 확실히 아닌 것을 먼저 거른다. 여기서 쓰는 판정은
        # make_event 가 다시 쓰는 것과 **같은 입력**이다 (게시판 주인은 안 넘긴다).
        verdict = event_relevance.judge(row["title"], "", "", "")
        if not verdict["ok"]:
            out.append({"_dropped": verdict["reason"]})
            continue
        if opened >= max_details:
            out.append({"_dropped": "detail_budget"})
            continue
        opened += 1
        body = fetch_body(row) or ""
        span, clause = kpx_event_span(body, row["posted"])
        if span is None:
            # 행사일을 모른다. 게시일을 대신 쓰지 않는다 — 이 게시판에는 본문이
            # 포스터 이미지 한 장뿐인 토론회 공지가 실제로 있다.
            out.append({"_dropped": "no_event_date" if not clause
                        else "unreadable_event_date"})
            continue
        start, end, kind = span
        place = kpx_place(_kpx_field(body, _KPX_PLACE_RE))
        event = make_event(
            source_id="kpx_notice", publisher="한국전력거래소",
            title=row["title"], url=row["url"], posted=row["posted"],
            start=start, end=end, kind=kind,
            # 주최는 표시 전용 칸으로만 보낸다. 이 게시판의 행사는 대개 거래소
            # 자신의 것이고, 주최 문자열을 판정에 넣으면 '한국전력거래소' 안의
            # '전력거래'가 관심어로 걸려 게시판에 실린 것이 전부 통과한다.
            host=_kpx_field(body, _KPX_HOST_RE) or "한국전력거래소",
            place=place, time=kpx_time(clause),
            label=_label_from_title(row["title"]),
            post_id=row["post_id"])
        if not event or event.get("_dropped"):
            out.append(event or {"_dropped": "no_interest_match"})
            continue
        # 달력이 그리지는 않지만 근거로 남기는 칸들. 접수 창과 행사일을 갈라
        # 두는 것 자체가 이 수집원의 요점이라 마감일은 따로 적어 둔다.
        for key, pattern in (("audience", _KPX_AUDIENCE_RE),
                             ("apply_info", _KPX_DEADLINE_RE)):
            value = _kpx_field(body, pattern)
            if value:
                event[key] = value[:120]
        event["when_clause"] = clause[:160]
        out.append(event)
    return out


# 국회 행사알림에서 이 달력이 보는 구분. 문화행사(ARTCL)와 휴일은 빼고,
# 의사일정(ARTCL 아님)·정책행사·의원실행사·세미나만 본다.
ASSEMBLY_KINDS = {"MEMNA": "의원실행사", "POLIC": "정책행사", "SEMNA": "세미나"}


def parse_assembly_day(rows: list[dict], *, day: date) -> list[dict]:
    """국회 행사알림 하루치 JSON → 일정 후보.

    `eventDate` 가 "2026-09-03 14:00~16:00" 처럼 날짜와 시각을 한 문자열에
    담아 온다. 날짜는 그 앞 10자를 쓰고 시각은 `eventTime` 칸을 쓴다 —
    두 값이 어긋나면 API 가 준 전용 칸을 믿는다.
    """
    out: list[dict] = []
    for row in rows:
        code = str(row.get("eventDivCd") or "").upper()
        if code not in ASSEMBLY_KINDS:
            continue
        title = clean_text(_TAG_RE.sub(" ", str(row.get("title") or "")))
        stamp = str(row.get("eventDate") or "")[:10]
        try:
            when = date.fromisoformat(stamp)
        except ValueError:
            continue
        host = clean_text(row.get("orgNm") or "")
        place = clean_text(row.get("placeNm") or "")
        event = make_event(
            source_id="assembly_events", publisher="국회",
            title=title, url=clean_text(row.get("linkUrl") or "")
            or "https://www.assembly.go.kr/portal/noti/seminar/scheduleSmn.do?menuNo=600102",
            posted=day, start=when, end=when, kind="point",
            organizer=host or ASSEMBLY_KINDS[code], place=place,
            time=clean_text(row.get("eventTime") or "").replace(" ", ""))
        if event:
            out.append(event)
    return out


# ── 네트워크 ─────────────────────────────────────────────────────────────

def _session():
    import requests
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT,
                            "Accept-Language": "ko-KR,ko;q=0.9"})
    return session


def fetch_kns(today: date) -> list[dict]:
    session = _session()
    response = session.get("https://www.kns.org/boards/lists/notice", timeout=TIMEOUT)
    response.raise_for_status()
    response.encoding = "utf-8"
    return parse_kns_notice(response.text)


def _kaif_session():
    """협회는 루트를 먼저 받아야 서브페이지를 준다.

    세션 없이 `?c=193` 을 바로 치면 본문 대신 86 바이트짜리
    "유효하지 않은 문자가 사용되었습니다" alert 가 온다(실측 2026-08-29).
    """
    session = _session()
    session.headers["Referer"] = "https://www.kaif.or.kr/"
    session.get("https://www.kaif.or.kr/", timeout=TIMEOUT)
    return session


def fetch_kaif_notice(today: date) -> list[dict]:
    session = _kaif_session()
    response = session.get("https://www.kaif.or.kr/ko/?c=193", timeout=TIMEOUT)
    response.raise_for_status()
    response.encoding = "utf-8"
    return parse_kaif_notice(response.text)


def fetch_kaif_calendar(today: date) -> list[dict]:
    session = _kaif_session()
    session.get("https://www.kaif.or.kr/ko/?c=240", timeout=TIMEOUT)
    # ps 는 한 번에 받을 줄 수. 앞으로의 일정만 쓰지만 목록은 등록 역순이라
    # 넉넉히 받아야 몇 달 뒤 행사가 잘리지 않는다.
    payload = {"c": "204", "s": "", "gbn": "list", "sp": "", "sw": "",
               "cidx": "", "bbsid": "240", "sdate": "", "edate": "",
               "ps": "60", "w1": "", "w2": "", "w3": "", "gp": "1", "ix": ""}
    response = session.post("https://www.kaif.or.kr/common/plugin/kaif/ax.204.php",
                            data=payload, timeout=TIMEOUT)
    response.raise_for_status()
    response.encoding = "utf-8"
    return parse_kaif_calendar(response.text, today=today)


def fetch_niftep(today: date) -> list[dict]:
    session = _session()
    response = session.get("https://niftep.snu.ac.kr/kr/sub/notice/notice.asp",
                           timeout=TIMEOUT)
    response.raise_for_status()
    response.encoding = "utf-8"
    return parse_niftep_notice(response.text)


def fetch_kpx(today: date) -> list[dict]:
    """전력거래소 공지사항 — 목록 몇 쪽을 받고, 판정을 통과한 글만 본문을 연다.

    이 수집원만 상세를 받는 이유는 `parse_kpx_list` 머리말에 적었다: 게시일과
    행사일이 다른 자리에 있고, 행사일은 본문에만 있다.
    """
    session = _session()
    session.headers["Referer"] = KPX_BOARD_URL
    rows: list[dict] = []
    seen: set[str] = set()
    for page in range(1, KPX_LIST_PAGES + 1):
        response = session.get(f"{KPX_BOARD_URL}&nPage={page}", timeout=TIMEOUT)
        response.raise_for_status()
        response.encoding = "utf-8"
        for row in parse_kpx_list(response.text):
            if row["post_id"] in seen:
                continue
            seen.add(row["post_id"])
            rows.append(row)

    def body(row: dict) -> str:
        detail = session.get(row["url"], timeout=TIMEOUT)
        detail.raise_for_status()
        detail.encoding = "utf-8"
        return kpx_body_text(detail.text)

    return kpx_events(rows, body)


ASSEMBLY_BASE = "https://www.assembly.go.kr/portal/noti/seminar"


def fetch_assembly(today: date, *, days: int = 45) -> list[dict]:
    """국회 행사알림 — 월 요약을 먼저 받고 **일정이 있는 날만** 상세를 받는다.

    날마다 상세를 치면 45회가 되는데, 월 요약이 날짜별 건수를 주므로 빈 날은
    아예 부르지 않는다(실측 2026-09: 30일 중 이 달력이 볼 구분이 있는 날은 13일).
    """
    session = _session()
    session.headers["Referer"] = f"{ASSEMBLY_BASE}/scheduleSmn.do?menuNo=600102"
    session.get(f"{ASSEMBLY_BASE}/scheduleSmn.do?menuNo=600102", timeout=TIMEOUT)
    horizon = today + timedelta(days=days)
    months = sorted({(cursor.year, cursor.month) for cursor in
                     (today + timedelta(days=step) for step in range(days + 1))})
    busy: list[date] = []
    for year, month in months:
        response = session.get(f"{ASSEMBLY_BASE}/findSchlDaySmn.json",
                               params={"meetYear": f"{year:04d}",
                                       "meetMonth": f"{month:02d}"},
                               timeout=TIMEOUT)
        response.raise_for_status()
        for row in (response.json().get("scheduleDay") or []):
            try:
                when = date.fromisoformat(str(row.get("eventDate"))[:10])
            except ValueError:
                continue
            if not (today <= when <= horizon):
                continue
            if sum(int(row.get(key) or 0) for key in
                   ("memnaTms", "policTms", "semnaTms")) > 0:
                busy.append(when)
    out: list[dict] = []
    for when in sorted(busy):
        response = session.get(f"{ASSEMBLY_BASE}/findSchlSmn.json",
                               params={"meetYear": f"{when.year:04d}",
                                       "meetMonth": f"{when.month:02d}",
                                       "meetDate": f"{when.day:02d}"},
                               timeout=TIMEOUT)
        response.raise_for_status()
        out.extend(parse_assembly_day(response.json().get("scheduleList") or [],
                                      day=today))
    return out


SOURCES = (
    {"id": "kns_notice", "name": "한국원자력학회 공지", "fetch": fetch_kns},
    {"id": "kaif_notice", "name": "원자력산업협회 공지", "fetch": fetch_kaif_notice},
    {"id": "kaif_calendar", "name": "원자력산업협회 일정표", "fetch": fetch_kaif_calendar},
    {"id": "niftep_notice", "name": "서울대 원자력정책연구소 공지", "fetch": fetch_niftep},
    {"id": "assembly_events", "name": "국회 행사알림", "fetch": fetch_assembly},
    {"id": "kpx_notice", "name": "전력거래소 공지", "fetch": fetch_kpx},
)


# ── 저장소 ───────────────────────────────────────────────────────────────

def load_store() -> dict:
    try:
        raw = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return {
        "generated_at": raw.get("generated_at") or "",
        "events": list(raw.get("events") or []),
        "last_checked": dict(raw.get("last_checked") or {}),
    }


def save_store(store: dict) -> None:
    tmp = OUT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(OUT_FILE)


def collected_today(store: dict) -> bool:
    return str(store.get("generated_at") or "")[:10] == \
        datetime.now(KST).date().isoformat()


def _post_key(row: dict) -> str:
    """게시물 한 건을 가리키는 열쇠. 게시물 번호를 주는 수집원에만 있다."""
    post = str(row.get("post_id") or "")
    return f"{row.get('source_id')}|{post}" if post else ""


def merge_events(kept: list[dict], fresh: list[dict]) -> tuple[list[dict], int]:
    """새로 걷은 것을 저장본에 얹는다. **최초 확인일은 낮은 쪽이 이긴다.**

    협회 일정표에는 게시일 칸이 없어 최초 확인일이 '우리가 처음 본 날'이다.
    매 실행 오늘로 덮어쓰면 그 값이 영영 오늘이 되고 '언제부터 알던 일정인가'를
    잃는다. 나머지 칸은 새 값이 이긴다 — 기관이 날짜·장소를 고치면 그것이 사실이다.

    `id` 는 날짜를 재료로 만들어지므로 **연기되면 같은 행사가 다른 id 를 받는다.**
    그래서 게시물 번호를 주는 수집원(KPX)은 그 번호로 옛 줄을 찾아 자리를 옮긴다 —
    옮기지 않으면 '9월 3일 토론회'와 '9월 17일로 연기된 같은 토론회'가 달력에
    나란히 선다. 번호가 없는 수집원의 동작은 그대로다.
    """
    by_id = {row["id"]: row for row in kept}
    by_post = {key: row for key, row in
               ((_post_key(row), row) for row in kept) if key}
    added = 0
    for row in fresh:
        existing = by_id.get(row["id"])
        if existing is not None:
            first_seen = min(existing.get("first_seen") or row["first_seen"],
                             row["first_seen"])
            existing.update(row)
            existing["first_seen"] = first_seen
            continue
        key = _post_key(row)
        prior = by_post.get(key) if key else None
        if prior is not None:
            # 같은 게시물이 날짜·장소를 고쳤다. 새 줄을 세우지 않고 그 줄을 옮긴다 —
            # 신규가 아니므로 `added` 도 세지 않는다.
            moved = {**prior, **row,
                     "first_seen": min(prior.get("first_seen") or row["first_seen"],
                                       row["first_seen"])}
            by_id.pop(prior["id"], None)
            by_id[moved["id"]] = moved
            by_post[key] = moved
            continue
        by_id[row["id"]] = row
        if key:
            by_post[key] = row
        added += 1
    return list(by_id.values()), added


def prune(events: list[dict], today: date) -> list[dict]:
    floor = (today - timedelta(days=KEEP_PAST_DAYS)).isoformat()
    ceiling = (today + timedelta(days=HORIZON_DAYS)).isoformat()
    alive = [row for row in events
             if str(row.get("end_date") or row.get("date") or "") >= floor
             and str(row.get("date") or "") <= ceiling]
    alive.sort(key=lambda row: (row.get("date") or "", row.get("label") or ""))
    return alive[:MAX_EVENTS]


def run(sources=None, *, once_per_day: bool = False,
        today: date | None = None) -> bool:
    store = load_store()
    if once_per_day and collected_today(store):
        print("[event_sources] 오늘 이미 수집함 — 스킵")
        return False
    now = datetime.now(KST)
    today = today or now.date()
    total_new = 0
    for source in (sources if sources is not None else SOURCES):
        source_id = source["id"]
        try:
            fetched = source["fetch"](today)
        except Exception as exc:  # 소스 격리 — 게시판 하나가 나머지를 못 막는다
            store["last_checked"][source_id] = {
                "at": now.isoformat(timespec="seconds"), "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:200]}
            print(f"[event_sources] {source['name']} 실패 — 격리: "
                  f"{type(exc).__name__}: {exc}")
            continue
        dropped: dict[str, int] = {}
        keep: list[dict] = []
        for row in fetched:
            reason = row.get("_dropped")
            if reason:
                dropped[reason] = dropped.get(reason, 0) + 1
            else:
                keep.append(row)
        store["events"], added = merge_events(store["events"], keep)
        store["last_checked"][source_id] = {
            "at": now.isoformat(timespec="seconds"), "ok": True,
            # "0건"과 "파서가 죽어 아무것도 못 읽음"을 가르는 신호. 게시판 개편은
            # 200 을 주면서 목록만 사라지므로 응답 코드로는 못 가린다.
            "parsed": len(fetched), "kept": len(keep), "new": added,
            "dropped": dropped}
        print(f"[event_sources] {source['name']}: 후보 {len(fetched)}건 → "
              f"통과 {len(keep)}건(신규 {added}건)"
              + (f" · 버림 {dropped}" if dropped else ""))
        total_new += added
    store["events"] = prune(store["events"], today)
    store["generated_at"] = now.isoformat(timespec="seconds")
    save_store(store)
    print(f"[event_sources] 신규 {total_new}건, 보관 {len(store['events'])}건 "
          f"→ {OUT_FILE.name}")
    return True


if __name__ == "__main__":
    run(once_per_day="--once-per-day" in sys.argv)
