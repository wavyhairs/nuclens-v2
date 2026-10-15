"""신규원전 공론화 레인 — 12차 전력수급기본계획을 위한 원전 공론화 기사를 메인과 **별도 기준**으로 모은다.

왜 따로인가
-----------
메인 큐레이션은 "오늘 의사결정자가 알아야 할 원자력 뉴스"를 고른다. 0단계 gold(메인 아카이브
2026-09-01~10-15, 프리필터 후보 171건 전량 손라벨) 실측:

    공론화 기사(포함)         140건 · 사건 35개
    아침 브리핑에 닿은 기사    74건 · 사건 16개 — 사건의 절반 이상이 독자에게 닿지 않았다
    아카이브 한 줄의 실제 기사  사건당 20~30건(「의제 확정」 1줄 = 접힌 22건) — 접기 전 기사는
                              아카이브에 남지 않는다

그래서 이 레인은 **놓치지 않는 것**을 목표로 한다(사용자 결정 2026-10-15). 메인 필터(매체 점수·
홍보성·요약 길이)를 타지 않고, 접힌 기사까지 받으며, 판정은 "확신이 없으면 포함"이다 —
해외 AI 레인(``ai_lane``)의 "확신이 없으면 제외"와 반대다. 오포함은 화면의 「검토」 표시와
관리자 교정으로 고칠 수 있지만, 누락은 다시 찾아야 한다.

범위 (사용자 결정 2026-10-15, docs/2026-10-15-new-build-deliberation-tab-direction.md §3-1)
  · 포함: 12차 전기본을 위한 신규원전 **공론화 자체** — 추진 결정, 공론화위 구성·회의·의제,
    시민참여단·숙의, 결과·권고, 정부안 반영, 그리고 공론화에 대한 각 주체의 입장·반발·사설.
  · 제외: 전기본 신규원전 규모·일정 자체(공론화를 다루지 않으면), 부지·지역 수용성(공론화와 무관하면),
    고준위 방폐장 공론화, 계속운전, 다른 분야 공론화, 해외 공론화.

이 파일은 stdlib 만 쓴다. gemini_client 는 판정 호출 때만 늦게 import 한다.
news_bot 을 import 하지 않는다(필수 secret 을 읽는 모듈이다).
"""
from __future__ import annotations

import html
import json
import re
from typing import Callable, Iterable

PROMPT_VERSION = 1
POLICY_TASK = "deliberation_judge"

# ── 1. 프리필터 어휘 ─────────────────────────────────────────────────────────
#
# 통과 = 공론화 어휘 ∧ 원전 어휘. 0단계 실측: 메인 아카이브 9/1~10/15 에서 이 조건이 171건을 올렸고
# 그중 140건이 포함이었다(정밀도 82%). '공론위·국민참여 조사·공론장' 같은 다른 표현으로만 쓴
# 원전 공론화 기사는 같은 기간 0건이었다 — 어휘를 넓혀도 더 잡히는 것이 없었다.
# '공론화' 단독은 올리지 않는다: 교정시설·제2공항·데이터센터·약국 공론화가 섞인다.
DELIB_TERMS = ("공론화", "공론조사", "공론위", "시민참여단", "숙의")
NUCLEAR_TERMS = (
    "원전", "원자력", "원자로", "핵발전", "한수원", "한국수력원자력", "기후에너지환경부", "기후부",
    "원안위", "전기본", "전력수급기본계획", "신규 원전", "신규원전", "대형원전", "smr", "소형모듈원자로",
)
# '원전' 앵커에 걸리지만 원자력이 아닌 말(news_bot._HOMONYM_NOISE 와 같은 목록).
_HOMONYM = re.compile(r"기원전|원전\s*\(\s*原典\s*\)|原典")

# 판정에 넘기는 표시. **버리지 않는다** — 0단계 실측: '고준위 표시 ∧ 신규원전 어휘 없음'을 결정적으로
# 제외하면 고준위 공론화 14건 중 13건을 거르지만 포함 기사 2건(「원전 경제성 재평가…방폐장 비용」,
# 「고준위 방폐물 및 한미 원전 협력으로 중대 전환점」)도 함께 잃는다. 재현율 우선이라 판정이 가른다.
HINT_TERMS: dict[str, tuple[str, ...]] = {
    "hlw": ("고준위", "방폐", "사용후핵연료", "사용후 핵연료", "건식저장", "건식 저장", "방사성폐기물"),
    "continued_operation": ("계속운전", "수명연장", "수명 연장"),
    "egbp": ("전기본", "전력수급기본계획", "전력수요"),
    "siting": ("부지", "유치", "주민투표", "범대위", "군의회"),
}
# 제목 머리의 의견 기사 표지. 메인의 article_type 은 'opinion' 하나라 사설·칼럼·기고가 섞인다.
_EDITORIAL = re.compile(r"^\s*[\[【(〈<]?\s*(사설|칼럼|기고|시론|논설|오피니언|데스크|기자수첩|기자의\s*눈|"
                        r"특별기고|아침을\s*열며|여적|만물상|분수대)")

# 목록 기사 — 일정·인사·부고·사설 모음은 본문 한 줄에 공론화가 걸려도 기사가 아니다. 프리필터에서 뺀다.
# 백필 실측(2026-10-15, 9/1~): 「[오늘의 주요일정]」「[인사] 기후에너지환경부」「[전국 주요 신문 사설]」
# 「[산업부·기후부 장차관 일정]」이 Gemini 판정에서 포함으로 올라왔다. 사설 모음의 사설은 따로 잡힌다.
_LISTING = re.compile(r"^\s*[\[【]\s*(?:오늘의\s*)?(?:주요\s*)?일정|^\s*[\[【]\s*(?:인사|부고|동정)\s*[\]】]"
                      r"|^\s*[\[【][^\]】]*(?:주요\s*일정|장차관\s*일정|주요\s*신문\s*사설|오늘의\s*(?:신문|사설))")

TEXT_FIELDS = ("title", "title_kr", "summary", "detail", "description")


def strip_html(text: object) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", str(text or ""))).strip()


def article_text(article: dict) -> str:
    return " ".join(str(article.get(field) or "") for field in TEXT_FIELDS)


def _terms(text: str, phrases: Iterable[str]) -> list[str]:
    lowered = text.lower()
    return [p for p in phrases if p in lowered]


def is_editorial_title(title: object) -> bool:
    return bool(_EDITORIAL.match(str(title or "")))


def is_listing_title(title: object) -> bool:
    return bool(_LISTING.search(str(title or "")))


def title_on_topic(title: object) -> bool:
    """제목에 공론화 어휘나 원전 어휘가 하나라도 있나. 없으면 본문 한 단락에서 걸린 기사일 수 있다."""
    text = _HOMONYM.sub(" ", str(title or ""))
    return bool(_terms(text, DELIB_TERMS) or _terms(text, NUCLEAR_TERMS))


def hints(text: str) -> list[str]:
    return [name for name, phrases in HINT_TERMS.items() if _terms(text, phrases)]


def prefilter(article: dict) -> dict:
    """{"passed", "delib_terms", "nuclear_terms", "hints"}. 넓게 — 거르는 일은 판정이 한다."""
    text = _HOMONYM.sub(" ", article_text(article))
    delib = _terms(text, DELIB_TERMS)
    nuclear = _terms(text, NUCLEAR_TERMS)
    passed = bool(delib and nuclear) and not is_listing_title(article.get("title"))
    return {"passed": passed, "delib_terms": delib, "nuclear_terms": nuclear,
            "hints": hints(text) if passed else []}


# ── 2. 판정 스키마 ───────────────────────────────────────────────────────────
EXCLUDE_REASONS = (
    "none", "egbp_scale", "siting_local", "hlw_deliberation", "continued_operation",
    "egbp_non_nuclear", "other_deliberation", "overseas", "background_mention", "not_nuclear",
)
# 국면. 공론화는 시한부 절차(추진 결정 → 위원회 → 의제 → 숙의 → 권고 → 정부안)라 이 축이 탭의 대분류다.
STAGES = ("pre_launch", "committee_setup", "agenda_design", "panel_deliberation", "result",
          "plan_reflection", "aftermath", "reaction")
ACTOR_TYPES = ("government", "committee", "assembly", "local", "industry", "civic", "expert", "media")
STANCES = ("expand", "reduce", "process_critique", "neutral")
ARTICLE_TYPES = ("report", "analysis", "editorial", "interview", "press_release")
CONFIDENCE = ("high", "low")

LABELS_KR = {
    "pre_launch": "추진 결정", "committee_setup": "위원회 구성", "agenda_design": "의제·절차",
    "panel_deliberation": "시민참여단 숙의", "result": "결과·권고", "plan_reflection": "정부안 반영",
    "aftermath": "후속", "reaction": "입장·반응",
    "government": "정부", "committee": "공론화위", "assembly": "국회", "local": "지자체·지역",
    "industry": "원자력계", "civic": "시민·환경단체", "expert": "학계·전문가", "media": "언론",
    "expand": "확대·찬성", "reduce": "축소·반대", "process_critique": "절차 비판", "neutral": "사실 보도",
    "report": "보도", "analysis": "해설", "editorial": "사설·칼럼", "interview": "인터뷰",
    "press_release": "보도자료",
    "none": "포함", "egbp_scale": "전기본 규모·일정(공론화 무관)", "siting_local": "부지·지역(공론화 무관)",
    "hlw_deliberation": "고준위 방폐장 공론화", "continued_operation": "계속운전",
    "egbp_non_nuclear": "전기본 비원전", "other_deliberation": "다른 분야 공론화", "overseas": "해외",
    "background_mention": "배경 언급", "not_nuclear": "원전 아님",
    "high": "확신", "low": "검토",
}

SYSTEM_PROMPT = """당신은 '12차 전력수급기본계획을 위한 신규원전 공론화' 기사 수집기입니다.

목표는 **이 공론화에 관한 국내 기사를 하나도 놓치지 않는 것**입니다. 지역지·전문지·사설·칼럼·기고도 똑같이
중요합니다. 판정이 애매하면 **포함**하고 confidence 를 "low" 로 적습니다. 제외는 아래 [제외] 사유가 분명할 때만 합니다.

[포함] 12차 전기본을 위한 (신규)원전 공론화 자체를 다룬 기사.
  · 공론화 추진 결정·계획 발표, 공론화위원회 구성·출범·회의, 의제·절차 설계, 시민참여단 모집·숙의토론·공론조사,
    결과·권고안, 공론화 결과의 정부안 반영·국회 보고, 공론화 때문에 생긴 일정 변화(전기본 확정 연기 등)
  · 공론화에 대한 정부·국회·지자체·원자력계·환경단체·학계·언론의 입장·반발·요구·비판·해설·사설·칼럼·기고
  · 사설·칼럼·기고는 공론화를 한 단락이라도 다루면 포함한다(배경 언급 제외를 적용하지 않는다)
  · 지역지가 지역 시각(영덕·울진·삼척 등)으로 공론화를 다룬 기사도 포함한다

[제외] exclude_reason 하나를 고른다. 사유가 분명할 때만.
  egbp_scale          전기본의 전력수요·신규원전 규모·일정 기사인데 공론화를 다루지 않음(한 줄 언급뿐)
  siting_local        원전 부지·유치·지역 수용성 기사인데 공론화와 무관함
  hlw_deliberation    고준위 방폐장·사용후핵연료 저장시설 공론화(신규원전 공론화가 아님)
  continued_operation 계속운전·수명연장 수용성(신규원전 공론화가 아님)
  egbp_non_nuclear    전기본의 석탄·LNG·재생에너지·송전망 기사(원전 공론화 아님)
  other_deliberation  다른 분야 공론화(교정시설·공항·데이터센터·의료 등)
  overseas            해외의 원전 공론화·국민투표
  background_mention  보도 기사에서 공론화가 배경 한 줄로만 나옴(사설·칼럼·기고에는 쓰지 않는다)
  not_nuclear         원전과 무관
포함이면 exclude_reason = "none".

[포함 기사에만 채우는 분류]
  stage          pre_launch(추진 결정·계획 발표) | committee_setup(공론화위 구성·출범) |
                 agenda_design(의제·절차·운영 방식 설계, 1차 회의 의결) | panel_deliberation(시민참여단 모집·숙의토론·조사) |
                 result(결과·권고안) | plan_reflection(정부안 반영·국회 보고·확정) | aftermath(후속·법·소송) |
                 reaction(특정 국면에 속하지 않는 입장 표명·반발·사설·해설)
  actor_type     기사의 주체. government(기후에너지환경부·대통령·총리) | committee(공론화위원회) | assembly(국회·의원·국정감사) |
                 local(지자체·지역사회·지방의회) | industry(한수원·원자력계·노조·산업계) | civic(환경·시민단체) |
                 expert(학회·연구기관·교수) | media(사설·칼럼·해설의 필자·언론사)
  actor_names    주체 이름 1~3개(예: 기후에너지환경부, 은재호 위원장, 한국원자력학회, 탈핵부산시민연대)
  stance         expand(원전 확대·공론화 조기 종결) | reduce(원전 축소·반대) | process_critique(절차·공정성·의제 범위 비판) |
                 neutral(사실 보도)
  article_type   report(보도) | analysis(해설·분석) | editorial(사설·칼럼·기고·시론) | interview | press_release(보도자료 전재)
  significance   0~3 정수. 3=공식 결정·일정 확정(추진계획 의결, 위원회 출범, 의제 확정, 권고안, 정부안 확정) /
                 2=공식 회의·절차 진행, 주요 주체의 공식 입장(국정감사 질의, 장관·위원장 발언, 단체 성명) /
                 1=개별 입장·지역 동향·해설 / 0=사설·칼럼·일정 안내·배경
  event_key      이 기사가 다룬 **사건** 이름, 한국어 25자 이내. 같은 사건을 다룬 다른 매체 기사와 같은 이름이 되도록
                 '누가 무엇을' 형태로 쓴다(예: "원전 공론화위원회 출범", "공론화위 1차 회의 시민참여단 300명 의결",
                 "원전 공론화 의제 확정"). 아래 [최근 사건] 목록에 같은 사건이 있으면 **그 이름을 그대로** 쓴다.
                 사설·칼럼은 그 글의 제목을 줄여 쓴다.
  event_date     사건이 일어난 날 YYYY-MM-DD. 기사에 없으면 빈 문자열.
  confidence     high | low (범위에 드는지 확신이 없으면 low)

[포함 기사에만 쓰는 문장 — 한국어. 원문에 없는 사실·수치를 만들지 말 것]
  title_kr       30~60자 헤드라인. `주체 + 핵심 행동`. 입력 제목이 이미 좋으면 다듬기만 한다.
  summary        한 줄 결론 1문장(80자 안팎).
  why_important  공론화 절차에서 이 사건이 갖는 의미 1문장. 근거 없으면 빈 문자열.
  implication    공론화 일정·의제·여론 지형에 미치는 영향 1문장. 근거 없으면 빈 문자열.

[출력] JSON 하나. 다른 텍스트·코드펜스 금지.
{"items": [{"idx": 0, "in_scope": true, "exclude_reason": "none", "stage": "...", "actor_type": "...",
  "actor_names": ["..."], "stance": "...", "article_type": "...", "significance": 2, "event_key": "...",
  "event_date": "", "confidence": "high", "reason": "30자 이내 근거",
  "title_kr": "...", "summary": "...", "why_important": "...", "implication": "..."}]}
제외 기사는 {"idx": 3, "in_scope": false, "exclude_reason": "...", "reason": "..."} 만 쓴다.
모든 idx 가 정확히 한 번씩 등장해야 한다. 확신이 없으면 포함 쪽으로 판정한다."""


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def request_lines(article: dict, idx: int) -> str:
    """기사 한 건의 입력 블록. 0단계 평가기와 production 이 **같은 문자열**을 만든다."""
    title = strip_html(article.get("title"))
    lines = [f"[{idx}] {_clip(title, 240)}"]
    if article.get("title_kr") and article.get("title_kr") != title:
        lines.append(f"정리 제목: {_clip(article.get('title_kr'), 160)}")
    if article.get("summary"):
        lines.append(f"요약: {_clip(article.get('summary'), 400)}")
    excerpt = article.get("detail") or article.get("body_excerpt") or strip_html(article.get("description"))
    if excerpt:
        lines.append(f"본문 발췌: {_clip(excerpt, 900)}")
    lines.append(f"출처: {_clip(article.get('publisher') or article.get('domain'), 60)}")
    return "\n".join(lines)


def build_request(articles: list[dict], known_keys: Iterable[str] = ()) -> tuple[str, str]:
    keys = [k for k in dict.fromkeys(str(k).strip() for k in known_keys) if k][:40]
    head = ("[최근 사건] 같은 사건이면 이 이름을 그대로 쓴다.\n" + "\n".join(f"- {k}" for k in keys) + "\n\n"
            if keys else "")
    user = head + "\n\n".join(request_lines(article, idx) for idx, article in enumerate(articles))
    return SYSTEM_PROMPT, user


def _enum(value: object, allowed: tuple[str, ...], default: str = "") -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_judgment(item: dict) -> dict:
    """모델 응답 한 건 → 통제 값만 남긴 판정.

    두 칸이 엇갈리면(in_scope=true 인데 제외 사유가 있거나, false 인데 사유가 none) **포함·검토**로 본다 —
    이 레인의 규칙은 "확신 없으면 포함"이다(ai_lane.normalize_judgment 는 반대로 제외).
    """
    reason = _enum(item.get("exclude_reason"), EXCLUDE_REASONS, "")
    said_in = bool(item.get("in_scope"))
    clear_out = (not said_in) and reason not in ("", "none")
    if clear_out:
        return {"in_scope": False, "exclude_reason": reason, "reason": _clip(item.get("reason"), 60)}
    conflicted = (said_in and reason not in ("", "none")) or (not said_in)
    try:
        significance = max(0, min(3, int(item.get("significance") or 0)))
    except (TypeError, ValueError):
        significance = 0
    event_date = str(item.get("event_date") or "").strip()
    return {
        "in_scope": True,
        "exclude_reason": "none",
        "stage": _enum(item.get("stage"), STAGES, "reaction"),
        "actor_type": _enum(item.get("actor_type"), ACTOR_TYPES, "media"),
        "actor_names": [_clip(n, 40) for n in (item.get("actor_names") or [])
                        if isinstance(n, str) and n.strip()][:3],
        "stance": _enum(item.get("stance"), STANCES, "neutral"),
        "article_type": _enum(item.get("article_type"), ARTICLE_TYPES, "report"),
        "significance": significance,
        "event_key": _clip(item.get("event_key"), 40),
        "event_date": event_date if _DATE.match(event_date) else "",
        "confidence": "low" if conflicted else _enum(item.get("confidence"), CONFIDENCE, "high"),
        "reason": _clip(item.get("reason"), 60),
        "title_kr": _clip(item.get("title_kr"), 90),
        "summary": _clip(item.get("summary"), 220),
        "why_important": _clip(item.get("why_important"), 220),
        "implication": _clip(item.get("implication"), 220),
    }


def parse_response(payload: dict, count: int) -> list[dict | None]:
    """응답 → 입력 순서의 판정 목록. 빠진 idx 는 None (호출자가 재시도를 정한다)."""
    out: list[dict | None] = [None] * count
    for item in (payload or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < count and out[idx] is None:
            out[idx] = normalize_judgment(item)
    return out


BATCH_SIZE = 8


def judge(articles: list[dict], *, known_keys: Iterable[str] = (), call: Callable | None = None,
          batch_size: int = BATCH_SIZE) -> list[dict | None]:
    """기사 목록을 판정한다. ``call`` 은 gemini_client.call_json 과 같은 모양.

    호출 실패한 배치는 None 으로 남긴다 — 실패를 '제외'로 바꾸면 그 기사는 다시 묻지 않게 된다.
    """
    if call is None:
        import gemini_client  # noqa: PLC0415
        import llm_policy  # noqa: PLC0415
        profile = llm_policy.profile(POLICY_TASK)

        def call(system: str, user: str):  # type: ignore[misc]
            return gemini_client.call_json(
                system, user, temperature=0.1,
                max_output_tokens=8192, model=profile.model(), label=POLICY_TASK,
                **profile.reasoning_kwargs())

    keys = list(known_keys)
    results: list[dict | None] = []
    for start in range(0, len(articles), batch_size):
        chunk = articles[start:start + batch_size]
        system, user = build_request(chunk, keys)
        try:
            payload = call(system, user)
        except Exception as exc:  # noqa: BLE001 — 실패는 None 으로 남긴다
            print(f"  ! deliberation judge chunk {start // batch_size + 1} failed: {exc}")
            results.extend([None] * len(chunk))
            continue
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        parsed = parse_response(payload if isinstance(payload, dict) else {}, len(chunk))
        # 한 배치에서 새로 나온 사건 이름을 다음 배치가 재사용하게 한다 — 같은 회차 안에서
        # 「의제 확정」이 배치마다 다른 이름으로 갈라지지 않게.
        for verdict in parsed:
            if verdict and verdict.get("in_scope") and verdict.get("event_key"):
                if verdict["event_key"] not in keys:
                    keys.insert(0, verdict["event_key"])
        results.extend(parsed)
    return results


def event_key_norm(key: object) -> str:
    """사건 이름 비교 키 — 공백·문장부호·따옴표를 지운다. 의미 유사도는 쓰지 않는다(오병합 교훈)."""
    return re.sub(r"[\s\W_]+", "", str(key or "")).lower()
