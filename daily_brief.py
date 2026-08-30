"""
일일 통합 브리핑 (daily_brief) — digest_queue 를 투자 관점 카드로 발송.

배경:
    news_bot 이 RSS(WNN·IAEA·정책 피드 등)를 매시간 긁어 분석해 digest_queue.json 에
    쌓는다. 이 봇이 그 큐를 '무슨 일 / 왜 중요 / 💰 투자 관점 / 🇰🇷 한수원 시사점'
    카드로 하루 1회 발송한다.

2026-07 개편 (설명 가능한 랭킹 + 발송 원자성):
    - 랭킹: ranking.py (LLM feature × ranking_config.json 가중치, 내역 로깅).
      features 없는 옛 큐 항목은 기존 rank_item 공식으로 하위 호환.
    - 투자 관점: 문장 생성이 아니라 구조화 필드(theme/mechanism/수혜유형/시계/확신)를
      뽑고 Python 이 문장을 조립. 근거 약하면(confidence 0) 줄 생략.
    - 보고서 추천: features 로 Python 이 후보를 먼저 거른 뒤에만 LLM 호출 (0건이 정상).
    - 발송 원자성 (outbox 패턴):
        --plan    선별→브리핑 생성→outbox.json(pending) 기록→큐에서 해당 항목 제거
        (워크플로가 outbox+큐를 먼저 push = delivery claim. push 실패 시 발송 안 함)
        --send    outbox 의 pending 브리핑만 발송, 결과를 outbox_result.json 에 기록
        --confirm outbox 에 발송 결과 병합 + delivery_log.jsonl 적재 (멱등)
      같은 날 재실행 시 이미 sent 인 브리핑은 재발송하지 않는다.

가드레일:
    stdlib + gemini_client(REST) + ranking + sources + telegram_send.
    GEMINI 실패 시: 투자 줄 없이 발송(graceful). 큐 비었으면 발송 스킵.
    telegram_send 는 lazy import (--plan 은 토큰 없이 동작해야 함).
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import hmac
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from gemini_client import GeminiError, call_json, is_available, synthesis_model
from sources import credibility
import article_quality_gate
import issue_continuity
# 반복 알림 억제 규칙을 여기서 다시 쓰지 않는다 — 규칙이 두 곳에 있으면 어긋난다.
import operational_monitoring
import khnp_relevance
import ranking
import story_cluster

ROOT = Path(__file__).parent
QUEUE_FILE = ROOT / "digest_queue.json"
SOCIAL_TOPICS_FILE = ROOT / "social_topics.json"
OUTBOX_FILE = ROOT / "outbox.json"
OUTBOX_RESULT_FILE = ROOT / "outbox_result.json"  # .gitignore — 같은 job 안 전달용
DELIVERY_LOG_FILE = ROOT / "delivery_log.jsonl"
KST = timezone(timedelta(hours=9))

MAX_ITEMS = 10  # 소셜 섹션 상한

# 국내/해외 분리 발송 — 둘 다 양이 많아 각각 별도 브리핑 1개씩.
# 국내는 사용자가 다른 경로로도 접하므로 적게(핵심만), 해외가 메인.
DOMESTIC_CAP = 3
FOREIGN_CAP = 6

# 발송 재시도 허용 창(시간). claim 후 발송 실패한 브리핑은 이 시간 안에만 재발송.
# 넘기면 stale_skipped — 낡은 브리핑 재발송·중복 발송 방지.
RESEND_WINDOW_H = 36

# Outbox 는 claim 뒤 별도 workflow 단계에서 발송된다. 품질 게이트가 강화되기
# 전에 만들어진 pending outbox 를 새 발송 코드가 그대로 보내면 최종 검증을
# 우회하므로, 계획 시점의 게이트 계약을 명시하고 발송 시 정확히 일치시킨다.
QUALITY_GATE_VERSION = 1
QUALITY_PAYLOAD_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

_KR_HINTS = (".kr", "khnp", "nssc", "motie", "kaeri", "kins", "korad", "yna", "korea")

# 명백한 외국 출처 — 429 분류실패로 domestic 태그가 붙어도 해외로 교정.
# 해외 보도자료 와이어(prnewswire 등)도 포함 — 해외 SMR 기업 발표가 이 경로로 들어온다.
_FOREIGN_NEWS = ("world-nuclear-news", "world-nuclear.org", "ans.org", "iaea.org",
                 "nrc.gov", "energy.gov", "oecd-nea", "nucnet", "neimagazine",
                 "reuters", "bloomberg", "powermag", "utilitydive", "spectrum.ieee",
                 "prnewswire", "globenewswire", "businesswire", "accesswire",
                 "newswire.ca")


_HANGUL = re.compile(r"[가-힣]")


def region(art: dict) -> str:
    """기사를 국내/해외 브리핑으로 분류. 기준은 '기사가 다루는 대상'(매체 국적 아님).

    1) scope('kr'|'overseas') — 큐레이션 LLM이 직접 판정한 값이 있으면 최우선
    2) section='khnp'(한수원이 주체) → 출처 불문 국내 (체코 수주 등)
    3) section='international' → 해외 (한국 매체가 쓴 해외 기사도 해외)
    4) section='domestic' → 국내. 단 명백한 외국 뉴스 도메인이면 오분류로 보고 해외 교정
    5) 지역 신호 없는 section(smr 등) → 도메인·제목 언어로 판단
    6) 아무 신호 없으면 해외 — 기본값을 국내로 두면 미국 SMR 기사가 국내로 섞임
    """
    scope = (art.get("scope") or "").lower()
    if scope == "kr":
        return "국내"
    if scope == "overseas":
        return "해외"

    dom = (art.get("domain") or "").lower()
    sec = art.get("section") or ""
    foreign_dom = any(f in dom for f in _FOREIGN_NEWS)
    if sec == "khnp":
        return "국내"
    if sec == "international":
        return "해외"
    if sec == "domestic":
        return "해외" if foreign_dom else "국내"
    if foreign_dom:
        return "해외"
    if any(h in dom for h in _KR_HINTS) or _HANGUL.search(art.get("title") or ""):
        return "국내"
    return "해외"


# ---- 큐/상태 입출력 -----------------------------------------------------------

# 경로 인자는 None 이면 호출 시점의 모듈 상수를 사용 (테스트에서 monkeypatch 가능하게)

def load_queue(path: Path | None = None) -> list[dict]:
    path = path or QUEUE_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_queue(items: list[dict], path: Path | None = None) -> None:
    path = path or QUEUE_FILE
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def load_outbox(path: Path | None = None) -> dict | None:
    path = path or OUTBOX_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_outbox(outbox: dict, path: Path | None = None) -> None:
    path = path or OUTBOX_FILE
    path.write_text(json.dumps(outbox, ensure_ascii=False, indent=2), encoding="utf-8")


def get_importance(item: dict) -> str:
    if "importance" in item:
        return item["importance"]
    cat = item.get("category", "")
    return cat if cat in {"must_read", "nice_to_know", "market", "noise"} else "nice_to_know"


# ---- 투자 관점 (구조화 추출 → Python 이 문장 조립) ----------------------------
#
# 제목+요약만으로 깊은 분석은 불가능 → 문장을 길게 만들지 않고 구조를 강제한다.
# LLM 은 '어떤 메커니즘으로 돈의 흐름이 바뀌는지'만 답하고, 렌더링·생략 판단은 Python.

INVEST_THEMES = {"uranium", "smr", "export", "life_extension", "fuel_cycle", "waste",
                 "regulation", "supply_chain", "construction", "financing",
                 "decommissioning", "safety", "grid_demand", "none"}
INVEST_BENEFICIARIES = {"reactor_vendor", "epc", "fuel_supplier", "utility",
                        "uranium_miner", "smr_developer", "grid_equipment", "none"}
INVEST_HORIZONS = {"near", "mid", "long"}

_THEME_KR = {"uranium": "우라늄", "smr": "SMR", "export": "수출", "life_extension": "계속운전",
             "fuel_cycle": "핵연료주기", "waste": "방폐물", "regulation": "규제",
             "supply_chain": "공급망", "construction": "신규건설", "financing": "자금조달",
             "decommissioning": "해체", "safety": "안전", "grid_demand": "전력수요"}
_BEN_KR = {"reactor_vendor": "원자로 공급사", "epc": "EPC", "fuel_supplier": "핵연료 공급사",
           "utility": "발전사업자", "uranium_miner": "우라늄 생산자",
           "smr_developer": "SMR 개발사", "grid_equipment": "전력기기"}
_HORIZON_KR = {"near": "단기", "mid": "중기", "long": "장기"}

INVEST_SYSTEM_PROMPT = """당신은 원자력·에너지 뉴스를 투자 관점으로 번역하는 분석가입니다.
독자는 원자력 업계를 아는 투자자(한수원 실무자)입니다. Doomberg 같은 냉정한 톤.

기사 항목 N개를 받습니다. 각 항목에 대해 **구조화된 투자 판단 필드**만 답하세요.
문장 생성은 시스템이 합니다 — 당신은 필드만.

⚠️ 출력은 정확히 아래 JSON. 다른 텍스트(설명, 펜스 ```)는 금지.
{"investments": [{"idx": 0, "theme": "...", "mechanism": "...", "beneficiary_type": "...", "risk_side": "...", "time_horizon": "...", "confidence": 0}]}

필드 규칙:
1. theme: uranium|smr|export|life_extension|fuel_cycle|waste|regulation|supply_chain|construction|financing|decommissioning|safety|grid_demand|none
   — 투자적으로 해석할 게 없으면 반드시 "none" (지어내지 말 것).
2. mechanism: **돈의 흐름이 왜 바뀌는지** 한국어 1문장(90자 이내). 비용·수주·공급 제약·
   규제·자본지출·연료 수요·프로젝트 일정 중 무엇을 통해 경제적 영향이 생기는지.
   "테마 강화" 같은 추상어 금지. 제목·요약에서 확인 안 되는 인과는 금지.
3. beneficiary_type: reactor_vendor|epc|fuel_supplier|utility|uranium_miner|smr_developer|grid_equipment|none
   — 기업명 아님, 유형만. ⚠️ 특정 종목·매수·매도 언급 절대 금지.
4. risk_side: 불리해지는 쪽(한국어 짧게, 예: "가스 피크발전") 또는 "none".
5. time_horizon: near(1년 내)|mid(1~3년)|long(3년+).
6. confidence: 2=확정 사실 기반 / 1=합리적 해석 / 0=근거 약함(이 항목은 발송에서 생략됨).
7. 모든 idx 가 정확히 한 번씩.

입력: 각 줄이 `[idx] 한국어제목 | 왜중요 | 요약`."""


def _sanitize_invest(raw: dict) -> dict | None:
    """투자 구조화 필드 방어적 파싱. 쓸 수 없으면 None."""
    if not isinstance(raw, dict):
        return None
    theme = raw.get("theme")
    theme = theme if isinstance(theme, str) and theme in INVEST_THEMES else "none"
    mech = str(raw.get("mechanism") or "").strip()[:180]
    ben = raw.get("beneficiary_type")
    ben = ben if isinstance(ben, str) and ben in INVEST_BENEFICIARIES else "none"
    risk = str(raw.get("risk_side") or "").strip()[:60]
    if risk.lower() == "none":
        risk = ""
    hor = raw.get("time_horizon")
    hor = hor if isinstance(hor, str) and hor in INVEST_HORIZONS else "mid"
    try:
        conf = int(raw.get("confidence"))
    except (TypeError, ValueError):
        conf = 0
    conf = max(0, min(2, conf))
    return {"theme": theme, "mechanism": mech, "beneficiary_type": ben,
            "risk_side": risk, "time_horizon": hor, "confidence": conf}


def render_investment(struct: dict | None) -> str | None:
    """구조화 필드 → 한국어 투자 관점 한 줄. 근거 약하면 None (줄 생략).

    생략 조건: struct 없음 / theme none / mechanism 비어있음 / confidence 0.
    confidence 1 이면 단정 대신 관찰 수준임을 표기.
    """
    if not struct:
        return None
    if struct["theme"] == "none" or not struct["mechanism"] or struct["confidence"] == 0:
        return None
    parts = [struct["mechanism"].rstrip(".")]
    if struct["beneficiary_type"] != "none":
        parts.append(f"— {_BEN_KR[struct['beneficiary_type']]} 수혜")
    if struct["risk_side"]:
        parts.append(f"/ {struct['risk_side']} 부담")
    tail = f"({_THEME_KR.get(struct['theme'], struct['theme'])}·{_HORIZON_KR[struct['time_horizon']]}"
    if struct["confidence"] == 1:
        tail += "·확신 낮음"
    tail += ")"
    parts.append(tail)
    return " ".join(parts)[:300]


def enrich_investment(items: list[dict]) -> dict[int, dict]:
    """선별된 항목들에 구조화 투자 필드 부여. 실패/키없음 시 빈 dict(보강 없이 진행)."""
    if not is_available() or not items:
        if not is_available():
            print("[daily_brief] GEMINI_API_KEY 없음 → 투자 관점 보강 건너뜀")
        return {}

    lines = []
    for i, art in enumerate(items):
        title = (art.get("title_kr") or art.get("title") or "").replace("\n", " ")[:120]
        why = (art.get("why_important") or art.get("implication") or "").replace("\n", " ")[:160]
        summ = (art.get("summary") or "").replace("\n", " ")[:80]
        lines.append(f"[{i}] {title} | {why} | {summ}")

    try:
        result = call_json(
            INVEST_SYSTEM_PROMPT, "\n".join(lines),
            temperature=0.2, max_output_tokens=4096, timeout=120.0,
            label="daily_brief",
        )
    except GeminiError as e:
        print(f"[daily_brief] 투자 보강 실패 → 투자 줄 없이 발송: {e}")
        return {}

    if not isinstance(result, dict) or not isinstance(result.get("investments"), list):
        print("[daily_brief] 투자 보강 응답 형식 오류 → 투자 줄 없이 발송")
        return {}

    out: dict[int, dict] = {}
    for it in result["investments"]:
        if not isinstance(it, dict):
            continue
        idx = it.get("idx")
        if not isinstance(idx, int) or not (0 <= idx < len(items)):
            continue
        struct = _sanitize_invest(it)
        if struct:
            out[idx] = struct
    return out


# ---- 조건부 필수 항목 보완 (한수원 시사점) ------------------------------------
#
# 카드의 `🇰🇷 한수원 시사점` 은 수집 단계 큐레이션의 `implication` 이다. 그런데
# 그쪽 프롬프트에서 이 필드의 이름은 "AI 해석 1문장"이고 한수원이라는 말이 어디에도
# 없다 — 라벨과 생성기가 다른 것을 말하고 있었다. 게다가 뒤에서 세 개의 게이트가
# 이 값을 비울 수 있는데(본문 없음·빈껍데기·실명 불일치), 비운 뒤 "이 기사라면
# 있어야 하지 않나"를 되묻는 자리가 없었다.
#
# 여기가 그 자리다. **모든 기사를 채우지 않는다** — 채우면 지금 걷어내고 있는
# 빈껍데기가 그대로 돌아온다. `khnp_relevance` 가 필요성을 판정하고, 필요성이
# 높은데 비어 있는 것만 한 번 더 묻는다. 그래도 근거가 없으면 빈칸이 정답이다.

IMPLICATION_SYSTEM_PROMPT = """당신은 한국수력원자력(한수원) 정책 부서에 에너지 뉴스를
정리해 주는 분석관입니다.

기사 N건을 받습니다. 각 기사마다 **한수원 시사점** 한 문장을 씁니다.
한수원 시사점이란 이 뉴스가 한수원의 사업·정책 환경·경쟁 구도·공급망·전력시장에서의
원전의 위치 중 **무엇을 어떻게 바꾸는가**입니다.

⚠️ 출력은 정확히 JSON 하나. 설명·코드펜스 금지.
{"items": [{"idx": 0, "implication": "..."}]}

규칙:
1. 90자 이내, 완결형 서술문 **한 문장**. 문자열을 자르지 말 것.
2. **입력 BODY 에 있는 사실만 쓴다.** 없는 수치·기관·일정·인과를 만들지 말 것.
3. **제목·요약을 바꿔 말하지 않는다.** 제목이 '무엇'이면 이 문장은
   '그래서 한수원의 무엇이 달라지나'다. 다음 중 하나는 반드시 담을 것:
   ①원전·무탄소 전원의 역할이 어떻게 달라지는가 ②전력시장·계통·수급 구조에서
   무엇이 바뀌는가 ③한수원의 사업(건설·계속운전·수출·공급망)에 걸리는 것
   ④경쟁·대체 전원과의 관계 변화. 근거가 되는 사실을 함께 적을 것.
4. **한수원을 억지로 등장시키지 말 것.** 회사 이름을 붙였다고 시사점이 되지 않는다.
   기사 사실과 한수원 사업 환경을 잇는 인과가 BODY 에서 확인되지 않으면 빈 문자열.
5. 아래 어미로 끝내는 문장은 금지다 — 전부 정보량 0이다:
   "…을 시사한다 / …을 보여준다 / …이 기대된다 / …이 전망된다 / …에 기여할 것이다 /
    …이 중요하다 / …이 필요하다 / …을 주목할 필요가 있다".
6. **쓸 사실이 없으면 빈 문자열 "" 로 둔다.** 빈칸이 빈껍데기보다 낫다 —
   빈 문자열은 실패가 아니라 정상 응답이다.
7. 예측·투자 권고 금지. 평서체(–다)로 끝낼 것.
8. 모든 idx 가 정확히 한 번씩.

나쁨: "정부의 에너지 정책 변화가 원자력 산업에 미칠 영향을 시사한다." (내용 0)
좋음: "재생에너지 100GW 확대의 간헐성을 ESS 로 메우는 구도라, 무탄소 기저 전원으로서
      원전의 역할 규정이 12차 전기본 논의의 쟁점이 된다."

입력은 기사마다 `[idx]` 로 시작하는 블록이며 TITLE / SUMMARY / BODY 를 담습니다."""


def _implication_acceptable(text: str, article: dict) -> tuple[bool, str]:
    """생성된 시사점을 받을지 판정. (통과 여부, 사유).

    기존 게이트를 그대로 재사용한다 — 여기서만 무르면 발송 경로에 다른 기준이
    하나 더 생기고, 그 순간 "카드마다 문장 품질이 다른 이유"를 아무도 설명 못 한다.
    """
    from data_quality import IMPLICATION_LIMIT, clean_text, implication_is_hollow, is_complete_sentence

    text = clean_text(text)
    if not text:
        return False, "empty"
    if len(text) > IMPLICATION_LIMIT:
        return False, "too_long"
    if not is_complete_sentence(text):
        return False, "incomplete"
    if implication_is_hollow(text):
        return False, "hollow"
    # 제목 재진술 차단. 제목을 늘려 쓴 문장은 카드 두 번째 줄을 낭비한다.
    title = (article.get("title_kr") or article.get("title") or "").strip()
    if title and re.sub(r"\W", "", text)[:40] and \
            difflib.SequenceMatcher(None, re.sub(r"\W", "", title),
                                    re.sub(r"\W", "", text)).ratio() >= 0.72:
        return False, "restates_title"
    return True, "accepted"


def complete_required_fields(items: list[dict]) -> dict:
    """선정분 중 '한수원 시사점이 있어야 하는데 빈' 항목만 한 번 더 생성한다.

    발송 직전이 이 일을 할 수 있는 유일한 자리다 — 수집 단계는 기사 하나만 보고
    (그래서 프롬프트에 한수원 축이 없다), 웹 빌드는 발송 **뒤**다.

    Returns:
        진단 dict (candidates/filled/rejected/samples). 실패해도 발송은 계속한다.
    """
    from data_quality import clean_text

    checks = [(i, khnp_relevance.implication_requirement(a)) for i, a in enumerate(items)]
    for i, verdict in checks:
        # 판정 자체는 로그·웹이 읽을 수 있게 항상 남긴다 — 생성 여부와 무관하게
        # "이 기사에 시사점이 왜 없나"에 답할 수 있어야 한다.
        items[i]["implication_requirement"] = verdict["level"]
    targets = [(i, v) for i, v in checks if v["regenerate"]]
    diag = {
        "candidates": len(targets),
        "filled": 0,
        "rejected": [],
        "required_without_body": sum(
            1 for _i, v in checks
            if v["level"] == "required" and not v["current"] and "no_body" in v["reasons"]),
    }
    if not targets:
        return diag
    if not is_available():
        print(f"[daily_brief] GEMINI_API_KEY 없음 → 한수원 시사점 보완 {len(targets)}건 건너뜀")
        diag["skipped"] = "no_api_key"
        return diag

    blocks = []
    for order, (i, _v) in enumerate(targets):
        art = items[i]
        blocks.append("\n".join([
            f"[{order}]",
            f"TITLE: {(art.get('title_kr') or art.get('title') or '')[:150]}",
            f"SUMMARY: {(art.get('summary') or '')[:200]}",
            f"BODY: {(art.get('detail') or '')[:900]}",
        ]))
    try:
        result = call_json(
            IMPLICATION_SYSTEM_PROMPT, "\n\n---\n\n".join(blocks),
            temperature=0.2, max_output_tokens=4096, timeout=120.0,
            model=synthesis_model(), label="daily_brief_implication",
        )
    except GeminiError as e:
        print(f"[daily_brief] 한수원 시사점 보완 실패 → 빈칸 유지: {e}")
        diag["skipped"] = "gemini_error"
        return diag

    # Gemini JSON 모드는 보통 객체를 돌려주지만, 2026-08-27 실운영에서 최상위
    # 배열이 한 번 반환됐다. 외부 응답의 모양 때문에 Plan 전체를 죽이면 claim과
    # 발송이 모두 생략된다. 보강 필드는 선택 사항이므로 잘못된 모양은 빈 응답으로
    # 취급하고 원래 기사로 계속 진행한다.
    if not isinstance(result, dict):
        print(f"[daily_brief] 한수원 시사점 보완 응답 형식 오류 "
              f"({type(result).__name__}) → 빈칸 유지")
        diag["skipped"] = "invalid_response"
        return diag

    rows = result.get("items")
    if not isinstance(rows, list):
        print("[daily_brief] 한수원 시사점 보완 items 형식 오류 → 빈칸 유지")
        diag["skipped"] = "invalid_response"
        return diag

    for row in rows:
        if not isinstance(row, dict):
            continue
        order = row.get("idx")
        if not isinstance(order, int) or not (0 <= order < len(targets)):
            continue
        i, _v = targets[order]
        text = clean_text(row.get("implication"))
        if not text:
            continue                       # 빈 문자열은 정상 응답이다
        ok, reason = _implication_acceptable(text, items[i])
        if not ok:
            diag["rejected"].append({
                "title": (items[i].get("title_kr") or "")[:60], "reason": reason,
                "text": text[:80]})
            continue
        items[i]["implication"] = text
        # 어디서 온 문장인지 남긴다. 웹·아카이브가 수집 단계 해석과 구분해야
        # "왜 어제 카드에는 없던 줄이 생겼나"를 되짚을 수 있다.
        items[i]["implication_source"] = "khnp_backfill"
        diag["filled"] += 1

    unresolved = len(targets) - diag["filled"] - len(diag["rejected"])
    print(f"[daily_brief] 한수원 시사점 보완: 대상 {len(targets)}건 → "
          f"생성 {diag['filled']}건 / 반려 {len(diag['rejected'])}건 / 근거부족 {unresolved}건")
    return diag


# ---- 보고서 검토 추천 (Python 게이트 → 후보 있을 때만 LLM) --------------------
#
# 기존엔 선별 전체를 매일 LLM에 물었다. 이제 features 로 후보를 먼저 거른다:
# 후보 0건이면 Gemini 호출 자체가 없다 (0건이 정상 상태).

REPORT_MAX_PER_DAY = 2
REPORTS_KB_FILE = ROOT / "reports_kb.json"
_REPORT_STRONG_EVENTS = {"policy_decision", "regulatory_action", "contract_award",
                         "incident_safety"}
_NEG_EXAMPLE_SIM = 0.7  # negative example 과 이 이상 비슷한 제목은 후보 제외


def _load_kb_negative_examples() -> list[str]:
    """reports_kb.json 의 negative_examples — '예전엔 이 정도는 보고서감이 아니었다'.

    파일 없으면 빈 리스트 (게이트는 features 만으로 동작).
    """
    try:
        kb = json.loads(REPORTS_KB_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for report in kb if isinstance(kb, list) else []:
        for ex in (report.get("negative_examples") or []):
            if isinstance(ex, str) and len(ex.strip()) >= 6:
                out.append(ex.strip())
    return out


def _matches_negative_example(title: str, negatives: list[str]) -> bool:
    import difflib
    t = (title or "").strip()
    if not t:
        return False
    return any(difflib.SequenceMatcher(None, t, neg).ratio() >= _NEG_EXAMPLE_SIM
               for neg in negatives)


def gate_report_candidates(items: list[dict],
                           negatives: list[str] | None = None) -> list[dict]:
    """보고서감 후보 게이트 (Python, 테스트 가능).

    - features 있는 항목: report_worthiness>=2 AND (must_read 또는 정책영향>=2 또는
      강한 이벤트 유형)일 때만 후보. features 없는 옛 항목: must_read 만 후보 (과도기).
    - reports_kb.json 의 negative_examples 와 유사한 제목은 제외 (과잉 추천 억제).
    """
    negatives = _load_kb_negative_examples() if negatives is None else negatives
    out = []
    for a in items:
        title = a.get("title_kr") or a.get("title") or ""
        if negatives and _matches_negative_example(title, negatives):
            continue
        f = ranking.sanitize_features(a.get("features"))
        if f is None:
            if get_importance(a) == "must_read":
                out.append(a)
            continue
        strong = (get_importance(a) == "must_read"
                  or f["policy_materiality"] >= 2
                  or f["event_type"] in _REPORT_STRONG_EVENTS)
        if f["report_worthiness"] >= 2 and strong:
            out.append(a)
    return out


REPORT_SYSTEM_PROMPT = """당신은 한국수력원자력 원자력정책실 정책개발부의 시니어 분석관입니다.
아래는 이미 1차 심사를 통과한 '보고서 후보' 사안들입니다. 이 중 **부서가 별도 보고서
(심층 분석)로 다룰 만큼 큼직한 사안**만 최종 추천하세요. 조건:
- 한수원·한국 원전 정책에 직접적 의사결정 영향이 있는가
- 단순 반복·전망·의견 기사가 아니라 새로운 상황 변화인가
- 공식 발표·규제 결정·계약·법 개정 등 검토할 근거가 충분한가

⚠️ 출력은 정확히 아래 JSON. 추천할 게 없으면 반드시 {"reports": []}.
다른 텍스트(설명, 펜스 ```)는 금지. **각 문자열 값 안에 줄바꿈 절대 금지 — 모두 한 줄로.**

{"reports": [{"idx": 0, "topic": "보고서 주제", "why": "왜 지금 보고서감인지 1-2문장", "angles": ["추천 각도1", "각도2"]}]}

규칙:
1. idx: 입력 항목 번호. 입력에 없는 idx 금지.
2. topic: 보고서 제목처럼 (한국어, 핵심 고유명사 포함).
3. why: 전략적·정책적 함의 중심. 부서 분석관 톤.
4. angles: 2-3개. 보고서에서 다룰 구체적 관점.
5. 최대 2건. 없으면 빈 배열을 두려워 말 것 — 0건이 정상.

입력: 각 줄이 `[idx] 제목 | 왜중요 | 섹션`."""


def verify_report_recs(reports: list[tuple[int, dict]],
                       candidates: list[dict]) -> tuple[list[tuple[int, dict]], list[dict]]:
    """추천 문구가 후보 기사에 없는 구체적 사실을 말하면 그 건만 뺀다.

    이 섹션은 브리핑 맨 위에 붙고 부서가 실제 보고서를 착수하는 근거가 된다.
    그런데 지금까지 검증은 '후보를 고르는 Python 게이트'까지였고, 그 뒤 LLM 이
    쓴 topic·why·angles 는 아무도 대조하지 않았다.

    판정 기준은 그날 후보 **전체**다. 추천은 여러 후보를 묶어 한 주제를 말할 수
    있으므로 idx 하나에 묶어 보면 정상 추천이 대량으로 걸린다. 대신 그날 후보
    어디에도 없는 기관·국가·수치·날짜는 통과시키지 않는다. 사업단계는 보지
    않는다 — '보고서로 다룰 만한가'는 사건이 아니라 판단이다.
    """
    if not reports or not candidates:
        return reports, []
    contracts = article_quality_gate.build_evidence_contracts(
        [{"key": str(article.get("hash") or "") or f"c{index}",
          "articles": [article]}
         for index, article in enumerate(candidates)])
    kept: list[tuple[int, dict]] = []
    dropped: list[dict] = []
    for idx, report in reports:
        angles = [str(x).strip() for x in (report.get("angles") or []) if str(x).strip()]
        text = " ".join(filter(None, [
            str(report.get("topic") or ""), str(report.get("why") or ""), *angles]))
        problems = article_quality_gate.unsupported_facts(
            text, contracts, checks=article_quality_gate.ANALYSIS_FACT_CHECKS)
        if problems:
            dropped.append({"topic": str(report.get("topic") or "")[:80],
                            "hash": candidates[idx].get("hash", "")[:8],
                            **problems})
            continue
        kept.append((idx, report))
    return kept, dropped


def build_report_recs(items: list[dict]) -> tuple[str, dict]:
    """보고서감 추천 메시지 + 판단 근거 diag. 후보 0건이면 LLM 호출 없이 빈 결과."""
    candidates = gate_report_candidates(items)
    diag = {"candidates": [c.get("hash", "")[:8] for c in candidates], "recommended": []}
    if not candidates:
        print("[daily_brief] 보고서 후보 0건 (게이트) → 추천 섹션·LLM 호출 생략")
        return "", diag
    if not is_available():
        return "", diag

    lines = []
    for i, a in enumerate(candidates):
        t = (a.get("title_kr") or a.get("title") or "").replace("\n", " ")[:100]
        why = (a.get("why_important") or a.get("implication") or "").replace("\n", " ")[:140]
        lines.append(f"[{i}] {t} | {why} | {a.get('section','')}")

    try:
        result = call_json(REPORT_SYSTEM_PROMPT, "\n".join(lines),
                           temperature=0.2, max_output_tokens=4096, timeout=90.0,
            model=synthesis_model(), label="daily_brief_report",
        )
    except GeminiError as e:
        print(f"[daily_brief] 보고서 추천 실패 → 섹션 생략: {e}")
        return "", diag

    if not isinstance(result, dict) or not isinstance(result.get("reports"), list):
        print("[daily_brief] 보고서 추천 응답 형식 오류 → 섹션 생략")
        diag["skipped"] = "invalid_response"
        return "", diag

    reports = []
    for r in result["reports"]:
        if not isinstance(r, dict) or not r.get("topic"):
            continue
        idx = r.get("idx")
        if not isinstance(idx, int) or not (0 <= idx < len(candidates)):
            continue
        reports.append((idx, r))
    reports = reports[:REPORT_MAX_PER_DAY]  # 하루 0~2건 강제
    reports, unsupported = verify_report_recs(reports, candidates)
    if unsupported:
        diag["unsupported"] = unsupported
        print(f"[daily_brief] 보고서 추천 {len(unsupported)}건 제외 — 후보 기사에 없는 사실")
    if not reports:
        return "", diag

    from html import escape
    today = datetime.now(KST).date().isoformat()
    out = [f"<b>📝 보고서 검토 추천 ({today})</b>",
           "<i>오늘 동향 중 부서 보고서로 다룰 만한 사안</i>", ""]
    for n, (idx, r) in enumerate(reports, 1):
        out.append(f"<b>{n}. {escape(str(r['topic']).strip())}</b>")
        if r.get("why"):
            out.append(f"   • <b>왜:</b> {escape(str(r['why']).strip())}")
        angles = [str(x).strip() for x in (r.get("angles") or []) if str(x).strip()]
        if angles:
            out.append(f"   • <b>추천 각도:</b> {escape(' / '.join(angles[:3]))}")
        out.append("")
        # hash 는 자르지 않는다 — 이 목록은 사람이 읽는 진단이자 웹이 기사에
        # 배지를 다는 조인 키다(plan_briefs → delivery_log → build_data).
        # candidates 쪽은 눈으로 훑는 용도라 8자로 둔다.
        diag["recommended"].append({
            "hash": candidates[idx].get("hash", ""),
            "topic": str(r["topic"]).strip()[:80],
            "why": str(r.get("why") or "").strip()[:400],
            "angles": angles[:3],
        })
    print(f"[daily_brief] 보고서 추천 {len(reports)}건 (후보 {len(candidates)}건 중)")
    return "\n".join(out).strip(), diag


# ---- 항목 → 카드 -------------------------------------------------------------

def _korean_or_none(s: str | None) -> str | None:
    """한글이 포함된 실제 한국어 문자열일 때만 반환 (영문·빈값·깨진 fallback 차단)."""
    s = (s or "").strip()
    return s if s and any("가" <= c <= "힣" for c in s) else None


def item_to_card(art: dict, investment: str | None) -> dict:
    """curated 항목을 synthesize.format_cards_message 호환 카드로."""
    link = art.get("link", "")
    # 매체명(전기신문)이 있으면 도메인보다 우선 — Google News 경유 기사는 도메인만
    # 보면 news.google.co.kr 이라 어느 매체인지 알 수 없다.
    label = art.get("publisher") or art.get("domain") or art.get("feed") or "RSS"
    cluster = {
        "url": link,
        "sources": [label],
        "title": art.get("title", ""),
        "meta": label,
    }
    return {
        "topic": art.get("section", ""),
        "cluster": cluster,
        "headline": art.get("title_kr") or art.get("title", ""),
        "what": _korean_or_none(art.get("summary")),
        "why": (art.get("why_important") or "").strip() or None,
        "investment": investment,
        "kr_takeaway": (art.get("implication") or "").strip() or None,
        "cred": credibility(cluster),
    }


def _quality_source(art: dict) -> dict:
    """큐에 보존된 최소 원문 근거. 기사 본문은 저장하지 않는다."""
    return {
        "title": art.get("title", ""),
        "description": art.get("source_excerpt", ""),
        "published_at": art.get("published_at") or art.get("queued_at"),
    }


def screen_auto_delivery(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """랭킹 전에 잘못 연결된 기사와 미검증 fallback을 자동 발송 풀에서 뺀다."""
    eligible: list[dict] = []
    held: list[dict] = []
    for art in items:
        integrity = article_quality_gate.audit_article_integrity(
            art, source=_quality_source(art),
            reference_date=art.get("published_at") or art.get("queued_at"))
        # 사건일만 비운 sanitize 결과는 원래 dict에도 반영한다. 선택되지 않아 큐에
        # 남는 경우에도 다음날 같은 잘못된 날짜를 다시 쓰지 않게 하기 위해서다.
        art.update(integrity.value)
        decision = article_quality_gate.assess_delivery_eligibility(
            art, integrity=integrity, legacy_compat=True,
            allow_primary_fallback=False)
        if decision.eligible:
            eligible.append(art)
            continue
        held.append({
            "hash": art.get("hash", ""),
            "title": (art.get("title_kr") or art.get("title") or "")[:120],
            "region": region(art),
            "status": decision.status,
            "action": decision.action,
            "reasons": list(decision.reasons),
            "findings": [finding.as_dict() for finding in integrity.findings],
        })
    return eligible, held


def verify_final_cards(articles: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """최종 Telegram 카드의 핵심 사실을 재검사하고 선택 필드만 보수적으로 제거한다."""
    safe_articles: list[dict] = []
    cards: list[dict] = []
    audits: list[dict] = []
    for art in articles:
        card = item_to_card(art, render_investment(art.get("investment_struct")))
        result = article_quality_gate.validate_final_card(
            card, art, source=_quality_source(art))
        audits.append({
            "hash": art.get("hash", ""),
            "title": (art.get("title_kr") or art.get("title") or "")[:120],
            **result.as_dict(),
        })
        if not result.eligible:
            continue
        # 카드에서 근거 부족으로 지운 문장을 article 쪽에도 되돌려 쓴다. 이후
        # delivery_log·사이트·전문가 오디오가 검증 전 값을 다시 읽어 부활시키면
        # 텔레그램과 다른 사실이 노출된다.
        cleaned_card = result.value
        art["summary"] = cleaned_card.get("what") or ""
        art["why_important"] = cleaned_card.get("why") or ""
        art["implication"] = cleaned_card.get("kr_takeaway") or ""
        if cleaned_card.get("investment") is None:
            art["investment_struct"] = None
        safe_articles.append(art)
        cards.append(cleaned_card)
    return safe_articles, cards, audits


def verify_social_cards(cards: list[dict]) -> tuple[list[dict], list[dict]]:
    """수동 소셜 카드도 원문 cluster와 대조해 핵심 충돌을 발송 전에 막는다.

    구현은 카드를 만드는 자리(synthesize)로 옮겼다 — 같은 build_cards 결과를
    send_research 가 검사 없이 보내던 경로가 있었고, 검사를 생성기 옆에 두면
    새 소비자가 생겨도 우회가 기본값이 되지 않는다.
    """
    from synthesize import verify_cards

    return verify_cards(cards)


# (피드백 inline keyboard 기능은 2026-07-16 사용자 결정으로 완전 삭제 — 브리핑을
#  어지럽혔고 수집된 이벤트도 0건. 재도입 시 git 히스토리의 feedback_ingest.py 참조.)


# ---- 소셜 수집 (원자력정책실 동향봇 통합, 수동 실행 전용) ----------------------

def collect_social(saved_raw: list[Path] | None = None,
                   top_per_topic: int = 5) -> list[tuple[str, dict]]:
    """소셜(Reddit/X/YT) 클러스터 수집 → (label, cluster) 페어.

    saved_raw 주면 그 raw 파일들 파싱(테스트), 아니면 social_topics.json 토픽마다
    last30days 실제 실행. Evidence 텍스트가 cluster['fulltext'] 로 들어가 grounding 됨.
    """
    import send_research as sr

    pairs: list[tuple[str, dict]] = []
    if saved_raw:
        for p in saved_raw:
            clusters = sr.parse_clusters(Path(p).read_text(encoding="utf-8"))
            kept, _ = sr.filter_and_rank(clusters, limit=top_per_topic)
            pairs += [("소셜", c) for c in kept]
        return pairs

    if not SOCIAL_TOPICS_FILE.exists():
        return pairs
    topics = json.loads(SOCIAL_TOPICS_FILE.read_text(encoding="utf-8")).get("topics", [])
    for t in topics:
        try:
            raw = sr.run_research(t["label"], t["subqueries"],
                                  t.get("subreddits", "nuclear,energy"))
            clusters = sr.parse_clusters(raw.read_text(encoding="utf-8"))
            kept, _ = sr.filter_and_rank(clusters, limit=top_per_topic)
            pairs += [(t["label"], c) for c in kept]
        except Exception as e:  # noqa: BLE001 — 토픽 1개 실패가 전체를 막지 않게
            print(f"[daily_brief] 소셜 '{t['label']}' 수집 실패: {e}")
    return pairs


# ---- 계획 수립 (선별 → 브리핑 텍스트 → outbox dict) ----------------------------

def empty_reason(diag: dict) -> str:
    """선정 0건일 때의 사유 문장. '수집이 없었다'와 '기준 미달'은 다른 상태다."""
    below = len(diag.get("dropped_below_floor") or [])
    if below:
        return (f"오늘은 브리핑 기준을 넘는 이슈가 없습니다. "
                f"검토한 후보 {below}건은 웹 아카이브에서 확인할 수 있습니다.")
    return "오늘 새로 확인된 브리핑 이슈가 없습니다."


def region_stats(diag: dict, selected: list[dict], pool: list[dict] | None = None,
                 continuity: dict | None = None) -> dict:
    """그날 그 지역의 선정 통계.

    features 결손은 하한 판정에서 면제되므로(ranking.floor_verdict) 컷오프 수치만
    봐서는 보이지 않는다. 결손이 줄고 있는지를 회차 단위로 남긴다 —
    수집 로그(news_bot)는 전체 큐 기준이고 이건 그날 후보 풀 기준이다.
    """
    stats = {
        "candidate_count": int(diag.get("candidate_count") or 0),
        "selected_count": len(selected),
        "below_floor_count": len(diag.get("dropped_below_floor") or []),
        "features_missing": sum(
            1 for a in (pool or []) if not isinstance(a.get("features"), dict)),
    }
    # 캡 내역은 사유 문자열이 아니라 구조로 남긴다 — "캡에 걸림" 한 줄로는 base 를
    # 올릴지 max 를 올릴지 수집을 늘릴지 사후에 못 가른다.
    if diag.get("cap"):
        stats["cap"] = diag["cap"]
    # 연속일 반복 판정. 감점만 받고 살아남은 건까지 남겨야 "게이트가 세긴 한데
    # 아무것도 안 걸렀다"와 "판정 자체가 안 돌았다"를 사후에 가를 수 있다.
    if continuity is not None:
        verdicts = continuity.get("verdicts") or []
        stats["continuity"] = {
            "checked": continuity.get("checked", 0),
            "matched": continuity.get("matched", 0),
            "dropped": len(diag.get("dropped_repeat") or []),
            "by_progression": {
                key: sum(1 for v in verdicts if v.get("progression") == key)
                for key in ("material", "minor", "none")
            },
            "samples": verdicts[:6],
        }
        # story 가 접힌 뒤의 재판정. 삭제를 실제로 정한 것은 이쪽이므로 위 숫자와
        # 따로 남긴다 — 둘이 갈리면 "근거 교집합이 판정을 뒤집었다"는 뜻이고,
        # 그 사실은 첫 판정만 봐서는 보이지 않는다.
        recheck = continuity.get("recheck")
        if isinstance(recheck, dict):
            again = recheck.get("verdicts") or []
            stats["continuity"]["recheck"] = {
                "checked": recheck.get("checked", 0),
                "matched": recheck.get("matched", 0),
                "by_progression": {
                    key: sum(1 for v in again if v.get("progression") == key)
                    for key in ("material", "minor", "none")
                },
                # 문턱을 **넘은** 건수와 그냥 겹친 건수를 따로 센다. 둘의 간격이
                # 곧 "문턱이 제자리인가"에 대한 답이다 — 실측 2026-08-22 국내
                # 풀에서 겹침은 5건인데 확정은 1건이었고, 나머지 넷은 1~2건짜리
                # 우연한 겹침이라 판정을 전혀 건드리지 않았다. 겹침만 세면
                # 게이트가 실제보다 다섯 배 활발해 보인다.
                "evidence_confirmed": sum(1 for v in again
                                          if v.get("evidence_confirmed")),
                "evidence_overlapping": sum(1 for v in again
                                            if v.get("evidence_shared")),
            }
    return stats


def plan_briefs(queue: list[dict],
                social_pairs: list[tuple[str, dict]] | None = None,
                now: datetime | None = None) -> dict:
    """큐(+소셜) → outbox dict (아직 파일로 저장 안 함).

    반환 outbox 구조:
        status: "empty" | "pending"
        briefs: [{name, text, keyboard, status}]
        items:  선별 항목 메타 (delivery_log 용)
        prune_hashes: 큐에서 제거할 hash (선별 + 그 중복 + noise/market)
    """
    from synthesize import format_cards_message, build_cards

    now = now or datetime.now(timezone.utc)
    today = datetime.now(KST).date().isoformat()
    base = {
        "schema_version": 1,
        "quality_gate_version": QUALITY_GATE_VERSION,
        "date": today,
        "created_at": now.isoformat(),
    }

    if not queue and not social_pairs:
        # 파이프라인은 정상적으로 돌았고 후보가 없었을 뿐이다 — 웹이 이걸
        # '데이터 갱신 실패'와 구분할 수 있도록 0 통계를 남긴다.
        zero = {"candidate_count": 0, "selected_count": 0, "below_floor_count": 0,
                "features_missing": 0}
        return _seal_quality_payload({
            **base, "status": "empty", "briefs": [], "items": [],
            "selection_stats": {"domestic": dict(zero), "overseas": dict(zero)},
            "prune_hashes": [],
        })

    cfg = ranking.load_config()
    junk_hashes = [a.get("hash", "") for a in queue
                   if get_importance(a) in ("noise", "market")]
    candidates = [a for a in queue if get_importance(a) not in ("noise", "market")]
    items, quality_held = screen_auto_delivery(candidates)
    quality_held_hashes = {row.get("hash", "") for row in quality_held if row.get("hash")}
    if quality_held:
        fallbacks = sum(1 for row in quality_held if row.get("status") == "fallback")
        quarantined = sum(1 for row in quality_held if row.get("action") == "quarantine")
        print(f"[daily_brief] 자동 발송 전 품질 보류 {len(quality_held)}건 "
              f"(fallback {fallbacks} / 무결성 격리 {quarantined})")

    dom_pool = [a for a in items if region(a) == "국내"]
    forn_pool = [a for a in items if region(a) == "해외"]
    # 캡은 ranking_config.json 의 selection_caps 가 정한다. 설정이 없으면
    # 아래 상수(국내3/해외6)로 돌아간다 — 설정 파일이 깨져도 어제처럼 동작해야 한다.
    # 제목 유사도가 못 넘는 표기 요동을 의미로 잡는다 — 국내·해외 각 1회, 하루 2회.
    # 국내와 해외를 한 번에 보내지 않는 이유: 지역이 다른 기사가 한 사건으로 묶이면
    # 한쪽 브리핑이 통째로 비는 사고가 난다.
    from dedup import dedup_articles, editorial_dedup_articles

    # 연속일 반복 게이트 — 선정 **전에** 어제 발송분과 대조한다. 그날 큐 안의
    # 중복은 세 단계(제목·의미·편집)가 이미 잡지만, 어제와 대조하는 자리는
    # 파이프라인에 없었다(웹은 발송 뒤에 잇는다).
    continuity_cfg = issue_continuity.resolve_config(cfg)
    recent_sent = issue_continuity.load_recent_sent(
        int(continuity_cfg.get("lookback_days", 5)))

    # 판정을 두 번 받는다. 처음은 여기(점수에 감점을 싣는다), 두 번째는
    # rank_and_select 안에서 story 가 다 접힌 뒤다 — 재료 하나(근거 교집합)가
    # 거기서야 생기기 때문이다. 두 번 다 같은 판정기·같은 발송 이력을 쓴다.
    #
    # 흔한 말 집합은 **처음 풀에서 한 번만** 센다. 두 번째 입력은 이미 걸러진
    # 소수라 안에서 다시 세면 generic_anchors 의 문턱이 최소값으로 떨어져,
    # 같은 하루 안에서 '흔한 말'의 정의가 바뀐다.
    dom_generic = issue_continuity.generic_anchors(list(dom_pool) + recent_sent)
    dom_cont = issue_continuity.annotate(dom_pool, recent_sent, cfg, today,
                                         generic=dom_generic)

    def dom_recheck(rows: list[dict]) -> None:
        # 두 번째 판정은 **덮어쓰지 않고 따로** 남긴다. 첫 판정의 checked/matched 는
        # '그날 후보 풀 전체에서 몇 건이 반복이었나'라는 별개의 사실이고, 그것을
        # 접힌 뒤의 수로 바꿔 버리면 회차 간 비교가 끊긴다.
        dom_cont["recheck"] = issue_continuity.annotate(
            rows, recent_sent, cfg, today, generic=dom_generic)

    dom, dom_diag = ranking.rank_and_select(
        dom_pool, DOMESTIC_CAP, cfg, now, ranking.resolve_floor(cfg, "domestic"),
        cap_spec=ranking.resolve_caps(cfg, "domestic"),
        semantic_dedup=dedup_articles,
        editorial_dedup=editorial_dedup_articles,
        continuity_recheck=dom_recheck)

    # 해외 풀은 **국내 선정 결과까지** 어제분에 얹어서 본다. 두 지역이 각자
    # 풀에서 따로 랭킹되므로, 같은 이슈가 국내 1번과 해외 3번을 동시에 차지하는
    # 일이 실제로 있었다(2026-08-16 테라파워 SMR 두 건). 규칙을 새로 만들지 않고
    # 같은 장치에 오늘치 발송분을 넣는다.
    forn_recent = recent_sent + [issue_continuity.as_sent_record(a, today) for a in dom]
    forn_generic = issue_continuity.generic_anchors(list(forn_pool) + forn_recent)
    forn_cont = issue_continuity.annotate(forn_pool, forn_recent, cfg, today,
                                          generic=forn_generic)

    def forn_recheck(rows: list[dict]) -> None:
        forn_cont["recheck"] = issue_continuity.annotate(
            rows, forn_recent, cfg, today, generic=forn_generic)

    forn, forn_diag = ranking.rank_and_select(
        forn_pool, FOREIGN_CAP, cfg, now, ranking.resolve_floor(cfg, "overseas"),
        cap_spec=ranking.resolve_caps(cfg, "overseas"),
        semantic_dedup=dedup_articles,
        editorial_dedup=editorial_dedup_articles,
        continuity_recheck=forn_recheck)
    print(f"[daily_brief] 국내 {len(dom)}건 / 해외 {len(forn)}건 선별 "
          f"(중복 제거 {len(dom_diag['dropped_duplicates']) + len(forn_diag['dropped_duplicates'])}건, "
          f"하한 미달 {len(dom_diag['dropped_below_floor']) + len(forn_diag['dropped_below_floor'])}건, "
          f"연속일 반복 {len(dom_diag.get('dropped_repeat') or []) + len(forn_diag.get('dropped_repeat') or [])}건 제외 "
          f"/ 감점 {dom_cont['matched'] + forn_cont['matched']}건 판정)")

    # 투자 보강 — 양쪽 선별분 한 번에 (무료 티어 호출 절감)
    allsel = dom + forn
    inv = enrich_investment(allsel)
    for i, art in enumerate(allsel):
        art["investment_struct"] = inv.get(i)  # 다양성·weekly 집계에서도 사용

    # 조건부 필수 항목 보완 — '한수원 시사점이 있어야 하는데 빈' 카드만.
    # 투자 보강 뒤에 두는 이유는 없다(서로 독립). 카드 조립 **앞**이어야 한다는
    # 것만이 조건이다 — item_to_card 가 implication 을 읽어 카드를 만든다.
    field_diag = complete_required_fields(allsel)

    # 투자·조건부 필수 항목까지 모두 조립된 **최종 카드**를 다시 원문 근거와 대조한다.
    # 핵심 headline/what 충돌은 카드 전체를 빼고, 선택 해석 필드의 새 주장은 그 줄만 뺀다.
    dom, dom_cards, dom_card_audits = verify_final_cards(dom)
    forn, forn_cards, forn_card_audits = verify_final_cards(forn)
    card_audits = dom_card_audits + forn_card_audits
    final_quarantine_hashes = {
        row.get("hash", "") for row in card_audits
        if row.get("action") == "quarantine" and row.get("hash")
    }
    sanitized_fields = sum(len(row.get("removed_fields") or []) for row in card_audits
                           if row.get("action") == "sanitize")
    if final_quarantine_hashes or sanitized_fields:
        print(f"[daily_brief] 최종 카드 사실검증: 카드 격리 {len(final_quarantine_hashes)}건 / "
              f"근거 없는 선택 필드 {sanitized_fields}개 제거")
    allsel = dom + forn
    n_omitted = sum(1 for card in (dom_cards + forn_cards) if not card.get("investment"))
    if allsel:
        print(f"[daily_brief] 투자 관점: {len(allsel) - n_omitted}건 표기 / {n_omitted}건 근거 부족 생략")

    briefs: list[dict] = []
    social_card_audits: list[dict] = []
    # 국내·해외 둘 다 항상 발송 — 사용자가 같은 시간에 둘 다 기대. 없으면 안내 메시지.
    if dom_cards:
        dom_msg = format_cards_message(dom_cards, header="🇰🇷 원자력 국내 브리핑")
    else:
        dom_msg = (f"<b>📰 🇰🇷 원자력 국내 브리핑 ({today})</b>\n\n"
                   f"<i>{empty_reason(dom_diag)}</i>")
    briefs.append({"name": "국내", "text": dom_msg, "status": "pending"})

    forn_msg = (format_cards_message(forn_cards, header="🌐 원자력 해외 브리핑")
                if forn_cards else "")
    if social_pairs:
        # 소셜은 정규 기사 큐를 거치지 않으므로 synthesize의 2차 검사와 공통
        # 최종 카드 게이트를 모두 통과한 카드만 수동 발송에 붙인다.
        social_cards = build_cards(social_pairs[:MAX_ITEMS], self_check=True) or []
        social_cards, social_card_audits = verify_social_cards(social_cards)
        social_quarantined = sum(
            1 for row in social_card_audits if row.get("action") == "quarantine")
        social_removed = sum(
            len(row.get("removed_fields") or []) for row in social_card_audits)
        if social_quarantined or social_removed:
            print(f"[daily_brief] 소셜 최종 카드 검증: 격리 {social_quarantined}건 / "
                  f"선택 필드 {social_removed}개 제거")
        if social_cards:
            sec = format_cards_message(
                social_cards, header="━━ 🔥 소셜 화제 (Reddit·X) ━━", show_header=False)
            forn_msg = (forn_msg + "\n" + sec) if forn_msg else sec
            print(f"[daily_brief] 소셜 카드 {len(social_cards)}개 (해외 브리핑에 추가)")
    if not forn_msg:
        forn_msg = (f"<b>📰 🌐 원자력 해외 브리핑 ({today})</b>\n\n"
                    f"<i>{empty_reason(forn_diag)}</i>")
    briefs.append({"name": "해외", "text": forn_msg, "status": "pending"})

    # 보고서 검토 추천 — Python 게이트 통과 후보 있을 때만 LLM (없으면 미발송)
    rec, report_diag = build_report_recs(allsel)
    if rec:
        briefs.insert(0, {"name": "보고서추천", "text": rec, "status": "pending"})
    # 추천 결과를 기사 메타에 실어 delivery_log 로 흘려보낸다. 웹이 배지를 다는
    # 유일한 경로다 — outbox.json 은 매일 덮어써서 어제 추천을 알 방법이 없다.
    report_picks = {r["hash"]: r
                    for r in report_diag.get("recommended", []) if r.get("hash")}

    # delivery_log 용 항목 메타 (점수 내역 = '왜 이 기사가 올라왔나' 증거)
    def _item_meta(a: dict, reg: str, diag: dict, rank: int) -> dict:
        h = a.get("hash", "")
        meta = {
            "hash": h,
            "title_kr": (a.get("title_kr") or a.get("title") or "")[:100],
            "region": reg,
            # 텔레그램 카드에 실제로 찍힌 번호(지역별 1부터). 오디오 브리핑이
            # 기사를 설명하는 순서의 기준이다 — 웹의 이슈 정렬은 점수를 다시
            # 줄 세우고 운영 콘솔의 승격·숨김까지 반영하므로 발송 순서와 다르다.
            # 여기서 못 박지 않으면 그 순서를 사후에 복원할 방법이 없다.
            "brief_rank": rank,
            "brief_region": reg,
            # 내일의 연속일 게이트가 읽을 재료. 제목만으로는 단계 판정이
            # 얇아진다 — '협의'와 '본계약'이 요약에만 있는 날이 흔하다.
            # (issue_continuity.load_recent_sent 가 이 줄을 그대로 읽는다.)
            "summary": (a.get("summary") or "")[:200],
            "tags": (a.get("tags") or [])[:6],
            "event_date": a.get("event_date"),
            "published_at": a.get("published_at", ""),
            "curation_status": a.get("curation_status", ""),
            "section": a.get("section", ""),
            # LLM 판정 scope (없으면 region()이 휴리스틱으로 결정한 것 — 오분류 추적용)
            "scope": a.get("scope", ""),
            "domain": a.get("domain", ""),
            "theme": (a.get("investment_struct") or {}).get("theme", ""),
            "score": diag["scores"].get(h),
            "breakdown": diag["breakdowns"].get(h),
        }
        # 선정된 **모든** briefing story의 사건 계약을 보존한다. 다중보도 때만
        # 저장하면 single 공식발표의 fingerprint가 웹 issue clustering에서 사라져
        # 뉴스 선정과 사이트의 사건 정의가 다시 갈라진다.
        meta["story_article_count"] = max(1, int(a.get("story_article_count") or 1))
        meta["story_outlet_count"] = max(1, int(a.get("story_outlet_count") or 1))
        meta["story_tier1_count"] = max(0, int(a.get("story_tier1_count") or 0))
        meta["story_independent_outlet_count"] = max(
            0, int(a.get("story_independent_outlet_count") or 0))
        meta["story_relation"] = a.get("story_relation", "single") or "single"
        meta["story_reason"] = a.get("story_reason", "")
        meta["story_dedup_stage"] = a.get("story_dedup_stage", "")
        meta["story_fingerprint"] = a.get("story_fingerprint", {})
        meta["story_id"] = story_cluster.ensure_story_id(a)
        meta["story_id_source"] = a.get("story_id_source", "generated")
        meta["story_article_hashes"] = (a.get("story_article_hashes") or [])[:12]
        meta["story_related_titles"] = (a.get("story_related_titles") or [])[:12]
        # 접힌 기사의 hash↔제목 짝. 운영 콘솔의 수동 분리가 이것 없이는 "어느
        # 기사를 떼는가"를 지정할 수 없다(제목만으로는 재현되지 않는다).
        meta["story_members"] = (a.get("story_members") or [])[:16]
        meta["story_sources"] = (a.get("story_sources") or [])[:12]
        meta["story_context"] = (a.get("story_context") or [])[:8]
        # 수집 단계에서 접힌 근거. 예전에는 이 자리에 아무것도 없었다 — 그 기사들이
        # story 가 만들어지기 전에 삭제됐기 때문이다. 이제 여기까지 온다.
        meta["story_raw_sources"] = (a.get("raw_sources") or [])[:12]
        meta["story_raw_source_count"] = len(a.get("raw_sources") or [])
        # 화면 대표를 story 완성 뒤에 골랐다는 사실과 그 사유.
        meta["story_display_reason"] = a.get("story_display_reason", "")
        meta["story_display_candidates"] = int(a.get("story_display_candidates") or 1)
        if a.get("story_display_swapped_from"):
            meta["story_display_swapped_from"] = a.get("story_display_swapped_from")
            meta["story_display_swapped_from_title"] = a.get(
                "story_display_swapped_from_title", "")
        # 빈 값은 넣지 않는다 — 하루 0~2건짜리 표식이라 나머지 전 줄에
        # report_pick:"" 이 붙으면 로그가 그만큼 읽기 어려워진다.
        if report_picks.get(h):
            pick = report_picks[h]
            meta["report_pick"] = pick["topic"]
            meta["report_pick_why"] = pick.get("why", "")
            meta["report_pick_angles"] = pick.get("angles", [])
        # 조건부 필수 항목 판정과 그 결과. 빈 값도 남긴다 — "왜 이 카드에는
        # 시사점이 없나"는 판정 등급을 봐야만 답할 수 있다.
        meta["implication_requirement"] = a.get("implication_requirement", "")
        if a.get("implication_source"):
            meta["implication_source"] = a["implication_source"]
        # 연속일 반복 판정이 붙은 기사는 그 근거를 남긴다. 감점을 받고도 살아남은
        # 후속 보도가 '왜 살아남았나'(단계 진전)를 사후에 확인할 유일한 자리다.
        if isinstance(a.get("continuity"), dict):
            cont = a["continuity"]
            meta["continuity"] = {k: cont.get(k) for k in
                                  ("prior_title", "prior_date", "days_ago", "similarity",
                                   "progression", "progression_kind", "progression_detail",
                                   "window_days", "repeat_streak", "penalty", "story_id",
                                   "identity_confirmed", "identity_method",
                                   "story_id_inheritable")}
        return meta

    out_items = ([_item_meta(a, "국내", dom_diag, i) for i, a in enumerate(dom, 1)]
                 + [_item_meta(a, "해외", forn_diag, i) for i, a in enumerate(forn, 1)])

    # 큐 정리 대상: 선별본 + 선별본의 중복(후속보도) + noise/market.
    # 선별 안 된 항목은 큐에 남아 다음날 재경쟁 (시간 감쇠·3일 자동정리로 상한).
    selected_hashes = {a.get("hash", "") for a in allsel}
    dup_hashes = [d["hash"] for d in
                  dom_diag["dropped_duplicates"] + forn_diag["dropped_duplicates"]
                  if d.get("dup_of") in selected_hashes]
    # A confirmed cross-day repeat has already been incorporated into the prior canonical story.
    # Leaving it in the queue makes the same source compete again until the three-day sweeper.
    repeat_hashes = {
        str(row.get("hash") or "")
        for row in (dom_diag.get("dropped_repeat") or [])
                   + (forn_diag.get("dropped_repeat") or [])
    }
    prune = sorted((selected_hashes | set(dup_hashes) | set(junk_hashes)
                    | repeat_hashes | quality_held_hashes | final_quarantine_hashes) - {""})

    quality_diag = {
        "held_before_ranking": quality_held,
        "final_cards": card_audits + social_card_audits,
        "summary": {
            "held": len(quality_held),
            "fallback_held": sum(1 for row in quality_held if row.get("status") == "fallback"),
            "integrity_quarantined": sum(
                1 for row in quality_held if row.get("action") == "quarantine"),
            "final_quarantined": len(final_quarantine_hashes) + sum(
                1 for row in social_card_audits if row.get("action") == "quarantine"),
            "final_fields_removed": sanitized_fields + sum(
                len(row.get("removed_fields") or []) for row in social_card_audits),
        },
    }

    return _seal_quality_payload({
        **base, "status": "pending", "briefs": briefs, "items": out_items,
        "report_diag": report_diag,
        "field_diag": field_diag,
        "quality_diag": quality_diag,
        "selection_stats": {
            "domestic": region_stats(dom_diag, dom, dom_pool, dom_cont),
            "overseas": region_stats(forn_diag, forn, forn_pool, forn_cont),
        },
        "dropped_duplicates": dom_diag["dropped_duplicates"] + forn_diag["dropped_duplicates"],
        # 병합만 기록하면 진단 화면은 반쪽이다. "왜 붙었나"의 짝은 "왜 안 붙었나"인데,
        # 분리는 결과물에 아무 흔적을 남기지 않아 여기서 잡지 않으면 영영 안 보인다.
        "story_audit": {
            "stage_vetoes": (dom_diag.get("stage_vetoes") or [])
                            + (forn_diag.get("stage_vetoes") or []),
            "display_promotions": (dom_diag.get("display_promotions") or [])
                                  + (forn_diag.get("display_promotions") or []),
            "ownership_conflicts": (dom_diag.get("story_ownership_conflicts") or [])
                                   + (forn_diag.get("story_ownership_conflicts") or []),
            "invariant_violations": (dom_diag.get("selection_invariant_violations") or [])
                                    + (forn_diag.get("selection_invariant_violations") or []),
        },
        "prune_hashes": prune,
    })


def prune_queue(queue: list[dict], prune_hashes: set[str]) -> list[dict]:
    return [a for a in queue if a.get("hash", "") not in prune_hashes]


# ---- 발송/확정 ----------------------------------------------------------------

def _outbox_age_hours(outbox: dict, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    try:
        created = datetime.fromisoformat(outbox.get("created_at", ""))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0.0
    return (now - created).total_seconds() / 3600


def _quality_payload(outbox: dict) -> dict:
    """발송 의미를 결정하는 불변 부분만 canonical digest 입력으로 만든다.

    status/sent_at/failure_reason은 정상 재시도에서 변하므로 제외한다. 반면 실제
    Telegram payload(name/text)와 그 검증 근거(items/quality_diag)는 통째로
    묶어 claim 뒤 문구나 근거만 바뀌는 경우를 모두 잡는다.
    """
    briefs = outbox.get("briefs")
    canonical_briefs = []
    if isinstance(briefs, list):
        canonical_briefs = [
            {
                "name": brief.get("name"),
                "text": brief.get("text"),
            }
            if isinstance(brief, dict) else {"invalid": brief}
            for brief in briefs
        ]
    else:
        canonical_briefs = [{"invalid_briefs": briefs}]
    return {
        "schema_version": outbox.get("schema_version"),
        "quality_gate_version": outbox.get("quality_gate_version"),
        "date": outbox.get("date"),
        "briefs": canonical_briefs,
        "items": outbox.get("items"),
        "quality_diag": outbox.get("quality_diag"),
        "field_diag": outbox.get("field_diag"),
    }


def _quality_payload_digest(outbox: dict) -> str:
    canonical = json.dumps(
        _quality_payload(outbox), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _seal_quality_payload(outbox: dict) -> dict:
    outbox["quality_payload_digest"] = _quality_payload_digest(outbox)
    return outbox


def _reject_incompatible_quality_gate(outbox: dict, now: datetime) -> list[dict] | None:
    """검증 계약이 다른 미발송 outbox 를 실패 처리하고 발송을 차단한다.

    ``None`` 은 호환됨을 뜻한다. 빈 리스트와 구분해야, 호환 outbox 에 실제
    pending 브리핑이 없을 때도 정상 발송 경로를 유지할 수 있다.
    """
    pending = [
        (idx, brief)
        for idx, brief in enumerate(outbox.get("briefs", []))
        if brief.get("status") in ("pending", "failed")
    ]
    if not pending:
        return None

    found = outbox.get("quality_gate_version")
    reason = ""
    detail: dict = {}
    if type(found) is not int or found != QUALITY_GATE_VERSION:  # bool(1) 은 버전이 아님
        reason = "quality_gate_version_mismatch"
        detail = {"found_version": found}
    else:
        stored_digest = outbox.get("quality_payload_digest")
        if stored_digest is None:
            reason = "quality_payload_digest_missing"
        elif not isinstance(stored_digest, str) or not QUALITY_PAYLOAD_DIGEST_RE.fullmatch(
                stored_digest):
            reason = "quality_payload_digest_invalid"
            detail = {"found_digest_type": type(stored_digest).__name__}
        else:
            try:
                expected_digest = _quality_payload_digest(outbox)
            except (TypeError, ValueError):
                reason = "quality_payload_unserializable"
            else:
                if not hmac.compare_digest(stored_digest, expected_digest):
                    reason = "quality_payload_digest_mismatch"
    if not reason:
        outbox.pop("quality_gate_error", None)
        return None

    outbox["quality_gate_error"] = {
        "code": reason,
        "required_version": QUALITY_GATE_VERSION,
        "detected_at": now.isoformat(),
        **detail,
    }
    results: list[dict] = []
    for idx, brief in pending:
        brief["status"] = "failed"
        brief["failure_reason"] = reason
        results.append({
            "idx": idx,
            "status": "failed",
            "failure_reason": reason,
            "required_quality_gate_version": QUALITY_GATE_VERSION,
            "found_quality_gate_version": found,
        })
    _update_overall_status(outbox)
    # failed 는 평소 Telegram 일시 장애 때 재시도하는 상태다. 버전 불일치는
    # 재시도해도 절대 회복되지 않으므로 별도 종결 상태로 가른다. 그렇지 않으면
    # cmd_plan 이 36시간 동안 이 outbox 만 붙들고 새 브리핑도 만들지 못한다.
    outbox["status"] = "quality_rejected"
    print("[daily_brief] 발송 차단 — outbox 품질 claim 불일치 "
          f"({reason}, 저장 버전={found!r}, 필요 버전={QUALITY_GATE_VERSION}); "
          "새로 계획해야 합니다")
    return results


def send_outbox(outbox: dict, now: datetime | None = None) -> list[dict]:
    """outbox 의 pending/failed 브리핑 발송. 결과 리스트 반환 + outbox 상태 갱신.

    - 이미 sent 인 브리핑은 건드리지 않음 → 같은 날 재실행해도 중복 발송 없음.
    - RESEND_WINDOW_H 를 넘긴 outbox 는 stale_skipped (낡은 브리핑 중복 방지).
    """
    now = now or datetime.now(timezone.utc)
    blocked = _reject_incompatible_quality_gate(outbox, now)
    if blocked is not None:
        return blocked

    import time
    from telegram_send import send_long_text  # lazy — plan 은 토큰 없이 동작

    stale = _outbox_age_hours(outbox, now) > RESEND_WINDOW_H
    results: list[dict] = []
    first_send = True
    for i, brief in enumerate(outbox.get("briefs", [])):
        if brief.get("status") not in ("pending", "failed"):
            continue
        if stale:
            brief["status"] = "stale_skipped"
            results.append({"idx": i, "status": "stale_skipped"})
            print(f"[daily_brief] {brief.get('name')} 브리핑 — {RESEND_WINDOW_H}h 초과, 재발송 생략")
            continue
        if not first_send:
            time.sleep(2)  # 텔레그램 rate limit
        first_send = False
        try:
            resp = send_long_text(brief["text"], parse_mode="HTML")
            ok = sum(1 for r in resp if r.get("ok"))
            success = ok == len(resp) and ok > 0
        except Exception as e:  # noqa: BLE001 — 브리핑 1개 실패가 나머지를 막지 않게
            print(f"[daily_brief] {brief.get('name')} 발송 오류: {type(e).__name__}: {str(e)[:150]}")
            success = False
        brief["status"] = "sent" if success else "failed"
        if success:
            brief["sent_at"] = now.isoformat()
            brief.pop("failure_reason", None)
        results.append({"idx": i, "status": brief["status"],
                        "sent_at": brief.get("sent_at")})
        print(f"[daily_brief] {brief.get('name')} 브리핑 발송 → {brief['status']}")
    _update_overall_status(outbox)
    return results


def _update_overall_status(outbox: dict) -> None:
    statuses = [b.get("status") for b in outbox.get("briefs", [])]
    if not statuses:
        outbox["status"] = outbox.get("status", "empty")
    elif all(s in ("sent", "stale_skipped") for s in statuses):
        outbox["status"] = "sent"
    elif any(s == "sent" for s in statuses):
        outbox["status"] = "partial"
    else:
        outbox["status"] = "pending"


def apply_send_results(outbox: dict, results: list[dict]) -> dict:
    """outbox_result.json 의 결과를 outbox 에 병합 (멱등 — 재적용해도 동일)."""
    briefs = outbox.get("briefs", [])
    for r in results:
        idx = r.get("idx")
        if isinstance(idx, int) and 0 <= idx < len(briefs):
            briefs[idx]["status"] = r.get("status", briefs[idx].get("status"))
            if r.get("sent_at"):
                briefs[idx]["sent_at"] = r["sent_at"]
            if r.get("failure_reason"):
                briefs[idx]["failure_reason"] = r["failure_reason"]
    _update_overall_status(outbox)
    return outbox


def append_selection_stats(outbox: dict, path: Path | None = None,
                           now: datetime | None = None) -> bool:
    """그날의 선정 통계를 delivery_log.jsonl 에 한 줄 남긴다.

    웹이 '오늘 0건'을 안전하게 렌더하려면 후보가 몇 건이었고 하한에서 몇 건이
    빠졌는지 알아야 하는데, rank_and_select 의 진단은 런타임 값이라 나중에 도는
    build_data 가 알 방법이 없다. 그래서 여기서 계약으로 남긴다.

    기사 레코드와 달리 hash 가 없어 (date, hash) 멱등이 안 걸린다. 재실행하면
    줄이 늘어나는데, 로그는 지우지 않고 **읽는 쪽이** generated_at 최신 +
    pipeline_status 우선순위로 고른다(build_data.pick_selection_stats).
    """
    stats = outbox.get("selection_stats")
    if not isinstance(stats, dict):
        return False
    path = path or DELIVERY_LOG_FILE
    now = now or datetime.now(timezone.utc)
    failed = any(b.get("status") == "failed" for b in outbox.get("briefs", []))
    rec = {
        "record_type": "selection_stats",
        "date": outbox.get("date", ""),
        "generated_at": now.astimezone(KST).isoformat(),
        "pipeline_status": "partial" if failed else "ok",
        **{k: v for k, v in stats.items()},
    }
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def append_field_audit(outbox: dict, path: Path | None = None,
                       now: datetime | None = None) -> bool:
    """조건부 필수 항목 보완 결과를 delivery_log.jsonl 에 한 줄 남긴다.

    "이 기사에 한수원 시사점이 왜 없나"는 세 가지 다른 상태다 — 관련성이 낮아
    애초에 안 물었다 / 물었는데 근거가 없어 모델이 빈 문자열을 냈다 / 만들었는데
    빈껍데기라 반려했다. 셋을 구분해 남기지 않으면 프롬프트가 망가진 것과
    '오늘은 그런 기사가 없었다'가 같은 모습으로 보인다.
    """
    diag = outbox.get("field_diag")
    if not isinstance(diag, dict) or not diag.get("candidates"):
        return False
    path = path or DELIVERY_LOG_FILE
    now = now or datetime.now(timezone.utc)
    rec = {
        "record_type": "field_completion",
        "date": outbox.get("date", ""),
        "generated_at": now.astimezone(KST).isoformat(),
        "field": "implication",
        **diag,
    }
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def append_quality_audit(outbox: dict, path: Path | None = None,
                         now: datetime | None = None) -> int:
    """자동 발송 보류·최종 카드 수정 결과를 관리자 알림 계약으로 남긴다."""
    diag = outbox.get("quality_diag")
    gate_error = outbox.get("quality_gate_error")
    if not isinstance(diag, dict) and not isinstance(gate_error, dict):
        return 0
    diag = diag if isinstance(diag, dict) else {}
    held = [row for row in (diag.get("held_before_ranking") or [])
            if isinstance(row, dict)]
    cards = [row for row in (diag.get("final_cards") or []) if isinstance(row, dict)]
    path = path or DELIVERY_LOG_FILE
    now = now or datetime.now(timezone.utc)
    generated_at = outbox.get("created_at") or now.astimezone(KST).isoformat()

    specs: list[dict] = []
    if isinstance(gate_error, dict) and gate_error.get("code"):
        # 여기까지 왔다는 것은 claim 이후에 봉인된 발송 payload 가 달라졌거나,
        # 게이트 계약이 다른 outbox 를 보내려 했다는 뜻이다. 발송은 이미
        # 막혔지만 그 사실이 로그 한 줄로 끝나면 아무도 모르고, 그날 브리핑은
        # 조용히 통째로 빠진다. 재시도로 회복되지 않으므로 첫 관측에 알린다.
        code = str(gate_error.get("code"))
        specs.append({
            "alert_key": f"outbox-quality-claim:{code}",
            "title": "오늘 브리핑 발송이 차단됐습니다",
            "detail": "발송 직전 안전 점검에서 준비된 브리핑이 계약과 달라 멈췄습니다.",
            "impact": "오늘 텔레그램 브리핑이 나가지 않았습니다. 사이트는 그대로 유지됩니다.",
            "action": ("워크플로를 다시 실행해 브리핑을 새로 계획해야 합니다 — "
                       "재시도만으로는 회복되지 않습니다."),
            "technical": (
                f"{code} — 저장 버전={gate_error.get('found_version')!r}, "
                f"필요 버전={gate_error.get('required_version')}"),
            "level": "action",
            "severity": "critical", "min_occurrences": 1,
            "items": [{
                "date": outbox.get("date", ""),
                "code": code,
                "status": outbox.get("status", ""),
                "blocked_briefs": [
                    brief.get("name") for brief in outbox.get("briefs") or []
                    if isinstance(brief, dict)
                    and brief.get("failure_reason") == code
                ],
                **{key: value for key, value in gate_error.items()
                   if key not in {"code"}},
            }],
        })
    fallback = [row for row in held if row.get("status") == "fallback"]
    integrity = [row for row in held if row.get("action") == "quarantine"]
    other_held = [row for row in held if row not in fallback and row not in integrity]
    final_quarantine = [row for row in cards if row.get("action") == "quarantine"]
    sanitized = [row for row in cards if row.get("action") == "sanitize"]

    # 아래 넷은 전부 **안전장치가 제대로 작동한 결과**다. 문제 데이터를 빼고
    # 나머지는 정상 발송했다는 뜻이므로 장애로 표시하지 않는다. severity 는
    # 내부 에스컬레이션 계약이라 그대로 두고, 운영자 등급만 '확인 필요'로 둔다.
    if fallback:
        specs.append({
            "alert_key": "unverified-fallback-held",
            "title": "검증이 끝나지 않은 기사를 발송에서 뺐습니다",
            "detail": f"요약 근거가 확인되지 않은 기사 {len(fallback)}건을 오늘 브리핑에서 제외했습니다.",
            "impact": "없음 — 나머지 기사와 서비스는 정상 발송됐습니다.",
            "action": "필요 없음 — 자동으로 보류됐습니다.",
            "technical": f"held_before_ranking status=fallback count={len(fallback)}",
            "level": "attention",
            "severity": "warning", "min_occurrences": 1, "items": fallback,
            "fingerprint": operational_monitoring.count_fingerprint("fallback", len(fallback)),
        })
    if integrity:
        specs.append({
            "alert_key": "delivery-integrity-quarantine",
            "title": "원문과 다른 기사를 발송 전에 걸렀습니다",
            "detail": f"요약이 원문과 다르게 만들어진 기사 {len(integrity)}건을 발송 대상에서 뺐습니다.",
            "impact": "없음 — 제외된 기사만 빠지고, 나머지 기사와 서비스는 정상입니다.",
            "action": "필요 없음 — 자동으로 차단됐습니다. 건수가 계속 늘면 확인해 주세요.",
            "technical": f"held_before_ranking action=quarantine count={len(integrity)}",
            "level": "attention",
            "severity": "critical", "min_occurrences": 1, "items": integrity,
            "fingerprint": operational_monitoring.count_fingerprint("integrity", len(integrity)),
        })
    if other_held:
        specs.append({
            "alert_key": "unreviewed-delivery-held",
            "title": "검토 상태를 확인하지 못한 기사를 보류했습니다",
            "detail": f"필수 근거나 검토 기록이 없는 기사 {len(other_held)}건을 발송하지 않았습니다.",
            "impact": "없음 — 나머지 기사와 서비스는 정상 발송됐습니다.",
            "action": "필요 없음 — 다음 회차에 자동으로 다시 확인합니다.",
            "technical": f"held_before_ranking reason=unreviewed count={len(other_held)}",
            "level": "attention",
            "severity": "warning", "min_occurrences": 2, "items": other_held,
            "fingerprint": operational_monitoring.count_fingerprint("unreviewed", len(other_held)),
        })
    if final_quarantine:
        specs.append({
            "alert_key": "final-card-quarantine",
            "title": "사실이 어긋난 카드를 발송에서 뺐습니다",
            "detail": (f"제목·핵심 문장이 기사 근거와 맞지 않는 카드 "
                       f"{len(final_quarantine)}개를 오늘 브리핑에서 제외했습니다."),
            "impact": "없음 — 제외된 카드만 빠지고, 나머지 브리핑은 정상 발송됐습니다.",
            "action": "필요 없음 — 자동으로 차단됐습니다. 건수가 계속 늘면 확인해 주세요.",
            "technical": f"final_cards action=quarantine count={len(final_quarantine)}",
            "level": "attention",
            "severity": "critical", "min_occurrences": 1, "items": final_quarantine,
            "fingerprint": operational_monitoring.count_fingerprint("cards", len(final_quarantine)),
        })
    if sanitized:
        removed = sum(len(row.get("removed_fields") or []) for row in sanitized)
        specs.append({
            "alert_key": "final-card-field-removed",
            "title": "근거가 없는 해석 문장을 카드에서 지웠습니다",
            "detail": (f"기사 근거로 확인되지 않는 항목 {removed}개를 카드 "
                       f"{len(sanitized)}개에서 빼고 발송했습니다."),
            "impact": "없음 — 사실 정보는 그대로이고, 브리핑은 정상 발송됐습니다.",
            "action": "필요 없음 — 자동으로 정리됐습니다.",
            "technical": f"final_cards action=sanitize fields={removed} cards={len(sanitized)}",
            "level": "attention",
            "severity": "warning", "min_occurrences": 2, "items": sanitized,
            "fingerprint": operational_monitoring.count_fingerprint(
                "card-fields", removed),
        })

    if not specs:
        return 0
    added = 0
    try:
        with path.open("a", encoding="utf-8") as fp:
            for spec in specs:
                rec = {
                    "record_type": "quality_event",
                    "date": outbox.get("date") or now.astimezone(KST).date().isoformat(),
                    # confirm 충돌 재시도에서도 같은 관측 ID를 써 streak가 두 번
                    # 오르지 않게 한다.
                    "generated_at": generated_at,
                    **spec,
                }
                rec["items"] = rec["items"][:20]
                fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
                added += 1
    except OSError as exc:
        print(f"[daily_brief] 품질 감사 기록 실패(비치명): {exc}")
        return 0
    return added


def append_story_audit(outbox: dict, path: Path | None = None,
                       now: datetime | None = None) -> bool:
    """그날의 story 병합/분리 판단을 delivery_log.jsonl 에 한 줄 남긴다.

    기사 레코드에는 '이 카드가 무엇을 접었나'만 실린다. 그 반대편 — 제목이 닮았는데
    **사건 단계가 달라 일부러 갈라 둔 쌍**과, story 완성 뒤 대표를 바꾼 판단 — 은
    어느 기사에도 남지 않는다. 남기지 않으면 운영 콘솔이 "왜 분리됐나"에 답할 수
    없으므로 selection_stats 와 같은 방식으로 계약에 못 박는다.
    """
    audit = outbox.get("story_audit")
    if not isinstance(audit, dict):
        return False
    vetoes = audit.get("stage_vetoes") or []
    promotions = audit.get("display_promotions") or []
    ownership = audit.get("ownership_conflicts") or []
    invariants = audit.get("invariant_violations") or []
    if not vetoes and not promotions and not ownership and not invariants:
        return False
    path = path or DELIVERY_LOG_FILE
    now = now or datetime.now(timezone.utc)
    rec = {
        "record_type": "story_audit",
        "date": outbox.get("date", ""),
        "generated_at": now.astimezone(KST).isoformat(),
        # 상한을 둔다 — 이 줄은 매일 붙고 로그는 지우지 않는다.
        "stage_vetoes": vetoes[:60],
        "display_promotions": promotions[:40],
        "ownership_conflicts": ownership[:60],
        "invariant_violations": invariants[:40],
        "stage_veto_count": len(vetoes),
        "display_promotion_count": len(promotions),
        "ownership_conflict_count": len(ownership),
        "invariant_violation_count": len(invariants),
    }
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def append_delivery_log(outbox: dict, path: Path | None = None) -> int:
    """발송 성공(sent)한 브리핑의 항목들을 delivery_log.jsonl 에 적재 (멱등).

    이미 (date, hash) 가 기록돼 있으면 건너뜀. 적재 건수 반환.
    """
    path = path or DELIVERY_LOG_FILE
    sent_regions = {b.get("name") for b in outbox.get("briefs", [])
                    if b.get("status") == "sent"}
    if not sent_regions:
        return 0
    existing: set[tuple[str, str]] = set()
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                    existing.add((rec.get("date", ""), rec.get("hash", "")))
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
    added = 0
    with path.open("a", encoding="utf-8") as fp:
        for item in outbox.get("items", []):
            if item.get("region") not in sent_regions:
                continue
            key = (outbox.get("date", ""), item.get("hash", ""))
            if key in existing:
                continue
            rec = {"date": outbox.get("date", ""), **item}
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            existing.add(key)
            added += 1
    return added


# ---- CLI 서브커맨드 ------------------------------------------------------------

def _sync_channel_batch(outbox: dict) -> None:
    """오늘 브리핑을 구독 채널 배치에 적재한다 (발송하지 않는다).

    **plan 에서** 부른다. claim push 가 outbox 와 함께 channel_outbox.json 을
    커밋하므로, 뒤 스텝들의 `git reset --hard origin/main` 이 지나가도 본문이
    살아남는다. send 에서 적재하면 confirm push 가 실패한 날 텍스트가 통째로
    지워지고 채널에는 오디오만 뜬다.

    실패해도 계획·발송을 막지 않는다 — 다만 조용히 넘기면 '채널만 비는' 상태가
    오래 사니 로그에 오류로 남긴다.
    """
    try:
        import channel_queue
        batch = channel_queue.sync_daily_batch(outbox)
    except Exception as exc:  # noqa: BLE001 — 큐 적재 실패가 브리핑을 죽이면 안 된다
        print(f"::error::채널 배치 적재 실패 — 오늘 구독 채널 공개가 비어 있을 수 있습니다: "
              f"{type(exc).__name__}: {exc}")
        return
    if batch:
        print(f"[daily_brief] 채널 배치 {batch['id']} — 항목 {len(batch['items'])}개")


def cmd_plan() -> int:
    """선별→outbox(pending) 기록→큐 정리. 발송은 하지 않는다 (claim 단계).

    멱등성:
        - 오늘 outbox 가 이미 있으면(충돌 재시도·재실행) 재계획 없이 재사용,
          큐 정리만 다시 적용 (git reset 으로 큐가 되돌아온 경우 대비). Gemini 재호출 0.
        - 어제 outbox 가 아직 pending(발송 실패)이고 36h 이내면 덮어쓰지 않고 보존
          → 이번 실행은 그 재발송에 사용. 오늘 기사는 큐에 남아 내일 발송.
    """
    today = datetime.now(KST).date().isoformat()
    existing = load_outbox()
    if existing:
        # 강화 전 claim 이 남아 있으면 Send까지 끌고 가지 않는다. 여기서 종결하고
        # 현재 큐로 새 계획을 만들어야 "버전 불일치 → failed → 36h 재시도" 교착이
        # 생기지 않는다. 이미 sent 인 브리핑만 있는 outbox 는 아래 멱등 경로가 맡는다.
        incompatible = _reject_incompatible_quality_gate(
            existing, datetime.now(timezone.utc))
        if incompatible is not None:
            save_outbox(existing)
            print("[daily_brief] 호환되지 않는 미발송 outbox 폐기 → 현재 큐로 재계획")
            existing = None
    if existing:
        if existing.get("date") == today and existing.get("status") != "empty":
            queue = load_queue()
            pruned = prune_queue(queue, set(existing.get("prune_hashes", [])))
            if len(pruned) != len(queue):
                save_queue(pruned)
                print(f"[daily_brief] plan 재사용 — 큐 정리 재적용 {len(queue)}→{len(pruned)}")
            else:
                print("[daily_brief] plan 재사용 (오늘 outbox 이미 존재)")
            # 재사용 경로에서도 적재한다 — claim 재시도의 reset 으로 큐 파일만
            # 되돌아간 경우, 여기서 다시 채우지 않으면 채널이 통째로 빈다.
            _sync_channel_batch(existing)
            return 0
        if (existing.get("status") in ("pending", "partial")
                and _outbox_age_hours(existing) <= RESEND_WINDOW_H):
            print("[daily_brief] 직전 outbox 미발송분 있음 → 새 계획 생략, 재발송 대기")
            _sync_channel_batch(existing)
            return 0

    queue = load_queue()
    outbox = plan_briefs(queue)
    save_outbox(outbox)
    if outbox["status"] == "empty":
        print("[daily_brief] 큐 비어있음 → outbox=empty (발송 스킵)")
        return 0
    _sync_channel_batch(outbox)
    pruned = prune_queue(queue, set(outbox["prune_hashes"]))
    save_queue(pruned)
    print(f"[daily_brief] plan 완료 — 브리핑 {len(outbox['briefs'])}개, "
          f"큐 {len(queue)}→{len(pruned)}")
    return 0


def cmd_send() -> int:
    """outbox 의 pending 브리핑 발송, 결과를 outbox + outbox_result.json 에 기록."""
    outbox = load_outbox()
    if not outbox or outbox.get("status") == "empty":
        print("[daily_brief] outbox 없음/empty → 발송 스킵")
        return 0
    if outbox.get("status") == "sent":
        print("[daily_brief] 이미 전부 발송됨 → 스킵 (중복 방지)")
        return 0
    results = send_outbox(outbox)
    save_outbox(outbox)
    OUTBOX_RESULT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    failed = [r for r in results if r["status"] == "failed"]
    return 1 if failed else 0


def cmd_confirm() -> int:
    """발송 결과를 outbox 에 병합(멱등) + delivery_log 적재. git reset 후 재적용용."""
    outbox = load_outbox()
    if not outbox:
        print("[daily_brief] outbox 없음 → confirm 스킵")
        return 0
    if OUTBOX_RESULT_FILE.exists():
        try:
            results = json.loads(OUTBOX_RESULT_FILE.read_text(encoding="utf-8"))
            if isinstance(results, list):
                apply_send_results(outbox, results)
        except (OSError, json.JSONDecodeError):
            print("[daily_brief] outbox_result 파싱 실패 → outbox 자체 상태 사용")
    n = append_delivery_log(outbox)
    # 발송이 전부 실패해도 통계는 남긴다 — 웹이 '조용한 날'과 '파이프라인 실패'를
    # 구분하려면 오늘 파이프라인이 돌았다는 사실 자체가 필요하다.
    append_selection_stats(outbox)
    append_story_audit(outbox)
    append_field_audit(outbox)
    append_quality_audit(outbox)
    save_outbox(outbox)
    print(f"[daily_brief] confirm — 상태 {outbox.get('status')}, delivery_log +{n}건")
    return 0


# ---- 메인 (수동/로컬 실행 — 기존 UX 유지) --------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="일일 통합 카드 브리핑")
    parser.add_argument("--plan", action="store_true", help="선별→outbox 기록 (발송 없음)")
    parser.add_argument("--send", action="store_true", help="outbox pending 발송")
    parser.add_argument("--confirm", action="store_true", help="발송 결과 병합+delivery_log")
    parser.add_argument("--dry-run", action="store_true", help="발송 없이 출력")
    parser.add_argument("--from-curated", action="store_true",
                        help="테스트용: digest_queue 대신 curated.json 전체를 입력으로")
    parser.add_argument("--keep-queue", action="store_true",
                        help="발송 후 큐를 비우지 않음 (테스트용)")
    parser.add_argument("--with-social", action="store_true",
                        help="소셜(last30days) 실제 수집해 합침 (느림, 스킬 필요)")
    parser.add_argument("--social-raw", nargs="*", default=None,
                        help="테스트용: 저장된 raw 파일로 소셜 섹션 구성")
    args = parser.parse_args()

    if args.plan:
        return cmd_plan()
    if args.send:
        return cmd_send()
    if args.confirm:
        return cmd_confirm()

    # ---- 수동 실행 경로: 계획+발송+로그를 한 번에 (git claim 없음) ----
    if args.from_curated:
        raw = json.loads((ROOT / "curated.json").read_text(encoding="utf-8"))
        queue = list(raw.values()) if isinstance(raw, dict) else raw
        # curated 항목엔 hash 필드가 없음 (key 가 hash) — 보강
        if isinstance(raw, dict):
            for h, item in raw.items():
                if isinstance(item, dict):
                    item.setdefault("hash", h)
    else:
        queue = load_queue()

    social_pairs = None
    if args.social_raw:
        social_pairs = collect_social(saved_raw=[Path(p) for p in args.social_raw])
        print(f"[daily_brief] 소셜(저장본) {len(social_pairs)}건")
    elif args.with_social:
        social_pairs = collect_social()
        print(f"[daily_brief] 소셜(라이브) {len(social_pairs)}건")

    if not queue and not social_pairs:
        print("[daily_brief] 큐·소셜 모두 비어있음 → 발송 스킵")
        return 0

    print(f"[daily_brief] curated 입력 {len(queue)}건")
    outbox = plan_briefs(queue, social_pairs=social_pairs)
    if outbox["status"] == "empty" or not outbox["briefs"]:
        print("[daily_brief] 발송할 내용 없음 → 스킵")
        return 0

    if args.dry_run:
        for brief in outbox["briefs"]:
            print("\n" + "=" * 60 + f"  [{brief['name']} 브리핑]")
            print(brief["text"])
        # 점수 내역 미리보기 — '왜 이 기사가 올라왔나'
        for item in outbox["items"]:
            print(f"  · [{item['region']}] {item['score']} {item['title_kr'][:50]} "
                  f"{item.get('breakdown')}")
        print(f"\n[dry-run] 브리핑 {len(outbox['briefs'])}개 (발송·큐 정리 생략)")
        return 0

    send_outbox(outbox)
    append_delivery_log(outbox)
    append_selection_stats(outbox)
    append_story_audit(outbox)
    append_field_audit(outbox)
    append_quality_audit(outbox)
    if not args.from_curated and not args.keep_queue:
        pruned = prune_queue(queue, set(outbox["prune_hashes"]))
        save_queue(pruned)
        print(f"[daily_brief] 큐 정리 {len(queue)}→{len(pruned)}")
    return 0 if outbox.get("status") == "sent" else 1


if __name__ == "__main__":
    sys.exit(main())
