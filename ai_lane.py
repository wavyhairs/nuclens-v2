"""해외 AI 레인 — "해외 원전이 AI 를 활용했다"는 기사를 메인과 **별도 기준**으로 고른다.

왜 따로인가
-----------
메인 큐레이션은 "원자력 정책·시장에서 무엇이 중요한가"를 묻는다. 0단계 gold
(아카이브 2026-07-17~10-14, 진짜 사례 25건) 실측으로 그 기준은 이 기사들을 이렇게 다뤘다:

    noise 로 버림          6건 (24%) — 「Tianwan puts AI to work」·「NIVA takes nuclear AI fleetwide」 등.
                                      noise 는 즉시 ``state["sent"]`` 로 찍혀 14일간 재수집도 막힌다.
    nice_to_know·must_read 19건 (76%)
    아침 브리핑에 실림     6건 (24%) — 나머지 19건은 독자에게 닿지 않았다.

수집은 되는데 **노출이 안 되는** 것이 문제의 본체다. 그래서 이 레인은 메인 등급과
무관하게 수집 직후의 후보 풀에서 따로 판정하고 따로 보여 준다.

이 파일이 지금 하는 일 (0단계)
-------------------------------
  1. ``prefilter`` — 결정적. AI 어휘 ∧ 원자력 어휘. **재현율 쪽으로 넓게** 둔다.
     거르는 일은 판정이 한다. 판정 전 단계에서 잘못 버린 기사는 되돌릴 길이 없다.
  2. ``hints`` — 제외 사유 후보(데이터센터·로봇·핵융합…)를 표시만 한다. 버리지 않는다.
     0단계 표본 층화와 규칙 기준선의 재료다.
  3. 판정 프롬프트·요청 생성기·응답 정규화 — **1단계 production 이 그대로 쓸 것**이다.
     0단계 평가기(tools/ai_lane_stage0.py)는 이 요청 생성기를 import 해서 쓴다.
     평가용 프롬프트를 따로 두면 production 과 다른 숫자가 나온다
     (tools/llm_eval.py 머리 주석의 교훈).

판정 기준 (사용자 결정 2026-10-14, docs/2026-10-14-overseas-ai-in-nuclear-tab-direction.md)
  · 해외만. 해외 사례를 다룬 국내 매체 기사는 포함.
  · 순수 AI 기술만. 로봇·드론·VR 자체는 제외하되 AI 기술이 쓰인 경우(AI 비전 판독
    검사 로봇)는 포함.
  · 핵융합 제외. 연구소 뉴스룸 기사는 포함, 논문 자체는 제외.
  · AI 데이터센터 **전력수요**는 제외 — "AI 가 전력을 쓰는" 이야기이지
    "원전이 AI 를 쓰는" 이야기가 아니다.

가드레일
  · 모듈 수준은 stdlib 만. gemini_client 는 판정 호출 때만 늦게 import 한다.
  · news_bot 을 import 하지 않는다(필수 secret 을 읽는 모듈이다).
"""
from __future__ import annotations

import json
import re
from typing import Callable, Iterable

PROMPT_VERSION = 2
POLICY_TASK = "ai_lane_judge"

# ── 1. 프리필터 어휘 ─────────────────────────────────────────────────────────
#
# 대문자 약어(AI·IA·LLM·NRC)는 **대소문자를 지켜** 라틴 문자 경계로만 본다.
# `\b` 를 쓰면 안 된다 — 파이썬 정규식에서 한글은 \w 라 "AI가"·"AI를" 사이에 경계가
# 없다. 반대로 경계를 안 두면 SAID·FAIL·MAIL·OpenAI·IAEA 에 걸린다.
_LATIN = "A-Za-zÀ-ÿ"
# ML 은 0단계 테스트가 잡은 구멍이다 — 「Argonne … with ML-based monitoring」 영어 제목이
# 안 걸렸다(아카이브에선 한글 요약의 "AI 기반" 덕에 통과했을 뿐). 소문자 mL(밀리리터)은 안 걸린다.
_AI_UPPER = re.compile(rf"(?<![{_LATIN}])(?:AI|A\.I\.|ML|LLMs?|GenAI)(?![{_LATIN}])")
# 프랑스어 IA. IAEA 는 뒤에 라틴 문자가 붙어 빠진다.
_IA_FR = re.compile(rf"(?<![{_LATIN}])IA(?![{_LATIN}])")
_AI_PHRASES = (
    # 영어
    "artificial intelligence", "machine learning", "machine-learning", "deep learning",
    "neural network", "large language model", "generative ai", "computer vision",
    "digital twin", "predictive maintenance", "predictive analytics", "anomaly detection",
    "virtual assistant", "chatbot", "copilot", "agentic",
    # 원전 특화 AI 벤더 — 이름 자체가 신호다. 기사에 AI 라는 말이 안 나와도 들어온다.
    "nuclearn", "atomic canyon", "blue wave ai", "palantir",
    # 한국어
    "인공지능", "머신러닝", "기계학습", "딥러닝", "생성형", "디지털 트윈", "디지털트윈",
    "예측정비", "예측 정비", "챗gpt", "거대언어모델", "대규모 언어모델", "대형언어모델",
    "컴퓨터 비전", "이상 징후 탐지", "이상징후 탐지",
    # 일본어
    "人工知能", "機械学習", "ディープラーニング", "デジタルツイン", "生成ai",
    # 프랑스어
    "intelligence artificielle", "apprentissage automatique", "jumeau numérique",
)

_NUCLEAR_UPPER = re.compile(rf"(?<![{_LATIN}])(?:SMRs?|NRC|IAEA|INPO|WANO|ONR|ASN|KHNP)(?![{_LATIN}])")
_NUCLEAR_PHRASES = (
    "nuclear", "reactor", "uranium", "atomic", "radioactive", "fission",
    "nucléaire",
    "원전", "원자력", "원자로", "핵연료", "사용후핵연료", "방사성", "방사능", "방폐",
    "우라늄", "소형모듈", "경수로", "중수로", "한수원", "원안위",
    "原子力", "原発", "原子炉",
)

# ── 2. 제외 사유 후보 표시 (버리지 않는다) ────────────────────────────────────
HINT_TERMS: dict[str, tuple[str, ...]] = {
    "datacenter_power": (
        "data center", "data centre", "datacenter", "datacentre", "hyperscaler",
        "power demand", "electricity demand", "power purchase", "power-hungry",
        "데이터센터", "데이터 센터", "전력수요", "전력 수요", "전력난", "전력망",
        "データセンター", "centre de données", "centres de données",
    ),
    "robotics": (
        "robot", "drone", "virtual reality", "augmented reality", "immersive",
        "exoskeleton", "로봇", "드론", "가상현실", "증강현실", "메타버스",
        "ロボット", "ドローン", "robotique",
    ),
    "fusion": (
        "fusion", "tokamak", "stellarator", "핵융합", "토카막", "인공태양", "核融合",
    ),
    "academic": (
        "arxiv", "journal", "peer-reviewed", "preprint", "논문", "학술지", "学術",
    ),
    "defense": (
        "weapon", "warhead", "submarine", "military", "missile", "drone strike",
        "핵무기", "핵잠", "잠수함", "군사", "미사일", "핵탄두", "兵器",
    ),
    "diplomatic": (
        "summit", "state visit", "memorandum of understanding", "bilateral",
        "정상회담", "국빈", "양해각서", "협력 강화", "협력강화", "외교",
    ),
    "ai_industry": (
        "openai", "오픈ai", "anthropic", "앤트로픽", "nvidia", "엔비디아",
        "chatgpt", "챗gpt", "ai stocks", "ai 주", "ai 거품", "ai 투자",
    ),
}
_HINT_UPPER = {
    # 짧은 약어는 대문자·경계로만 (VR 이 "VRAM", AR 이 "ARE" 에 걸리지 않게).
    "robotics": re.compile(rf"(?<![{_LATIN}])(?:VR|AR|XR)(?![{_LATIN}])"),
    "diplomatic": re.compile(rf"(?<![{_LATIN}])MOU(?![{_LATIN}])"),
    "datacenter_power": re.compile(rf"(?<![{_LATIN}])PPAs?(?![{_LATIN}])"),
}

TEXT_FIELDS = ("title", "title_kr", "summary", "detail", "description")


def article_text(article: dict) -> str:
    """판정 근거 텍스트. 태그·topics 는 넣지 않는다 — 메인 LLM 이 붙인 값이라
    '메인이 AI 라고 봤는가'를 다시 세는 꼴이 된다."""
    return " ".join(str(article.get(field) or "") for field in TEXT_FIELDS)


def _terms_in(text: str, lowered: str, phrases: Iterable[str],
              upper: re.Pattern | None = None) -> list[str]:
    found = [p for p in phrases if p in lowered]
    if upper is not None:
        found.extend(m.group(0) for m in upper.finditer(text))
    return list(dict.fromkeys(found))


def ai_terms(text: str) -> list[str]:
    lowered = text.lower()
    found = _terms_in(text, lowered, _AI_PHRASES, _AI_UPPER)
    found.extend(m.group(0) for m in _IA_FR.finditer(text))
    return list(dict.fromkeys(found))


def nuclear_terms(text: str) -> list[str]:
    lowered = text.lower()
    return _terms_in(text, lowered, _NUCLEAR_PHRASES, _NUCLEAR_UPPER)


def hints(text: str) -> list[str]:
    lowered = text.lower()
    out = []
    for name, phrases in HINT_TERMS.items():
        if _terms_in(text, lowered, phrases, _HINT_UPPER.get(name)):
            out.append(name)
    return out


def prefilter(article: dict) -> dict:
    """{"passed", "ai_terms", "nuclear_terms", "hints"}.

    통과 조건은 AI 어휘 ∧ 원자력 어휘 **둘 다**다. hints 는 통과를 바꾸지 않는다.
    """
    text = article_text(article)
    ai = ai_terms(text)
    nuclear = nuclear_terms(text)
    return {
        "passed": bool(ai and nuclear),
        "ai_terms": ai,
        "nuclear_terms": nuclear,
        "hints": hints(text) if ai and nuclear else [],
    }


# ── 3. 판정 스키마 ───────────────────────────────────────────────────────────
EXCLUDE_REASONS = (
    "none", "datacenter_power", "robotics_only", "ai_industry_general",
    "diplomatic_mention", "domestic", "fusion", "academic_only", "defense_weapons",
    "not_ai_use", "not_nuclear",
)
APPLICATION_AREAS = (
    "operations_maintenance", "inspection_diagnostics", "licensing_regulation",
    "design_engineering", "fuel_cycle", "decommissioning", "safeguards_security",
    "workforce_governance",
)
AI_TECH = ("ml_predictive", "genai_llm", "computer_vision", "digital_twin_ai", "other")
ACTOR_TYPES = ("utility", "vendor", "regulator", "national_lab", "ai_startup",
               "international_org", "other")
# discussion 은 0단계 라벨링에서 더했다 — ANS 기고·웨비나처럼 주제는 정확히 맞지만
# "적용했다"는 사건이 없는 해설·행사 기사가 담길 칸이 없었다.
MATURITY = ("research", "pilot", "announced", "contract", "deployed", "regulatory", "discussion")

# 화면 라벨 (docs 방향성 v4 §5).
LABELS_KR = {
    "operations_maintenance": "운영·정비", "inspection_diagnostics": "검사·진단",
    "licensing_regulation": "인허가·규제", "design_engineering": "설계·엔지니어링",
    "fuel_cycle": "핵연료", "decommissioning": "해체",
    "safeguards_security": "안전조치·보안", "workforce_governance": "인력·거버넌스",
    "research": "연구", "pilot": "시범", "announced": "발표", "contract": "계약",
    "deployed": "실적용", "regulatory": "규제", "discussion": "논의·해설",
    "ml_predictive": "기계학습·예측", "genai_llm": "생성형 AI", "computer_vision": "컴퓨터 비전",
    "digital_twin_ai": "AI 디지털 트윈", "other": "기타",
    "utility": "사업자", "vendor": "벤더", "regulator": "규제기관", "national_lab": "연구소",
    "ai_startup": "AI 스타트업", "international_org": "국제기구",
    "none": "포함", "datacenter_power": "데이터센터 전력", "robotics_only": "로봇·드론·VR",
    "ai_industry_general": "AI 산업 일반", "diplomatic_mention": "외교 나열",
    "domestic": "국내", "fusion": "핵융합", "academic_only": "논문",
    "defense_weapons": "군사", "not_ai_use": "AI 적용 아님", "not_nuclear": "원전 아님",
}

SYSTEM_PROMPT = """당신은 '해외 원전의 AI 활용' 기사 선별기입니다.

각 기사가 아래 [포함] 정의에 맞는지 판정하고, 맞는 기사에만 분류값을 붙입니다.
이 판정은 원자력 정책·시장 중요도와 **무관**합니다. 작은 시범 사업이라도 정의에 맞으면 포함입니다.

[포함] 한국 밖의 원전 사업(설계·건설·운영·정비·검사·인허가·규제·해체·핵연료·안전조치·인력)에
AI 기술을 적용·시범·계약·승인·발표한 기사. 주체는 사업자·벤더·규제기관·국립연구소·국제기구·AI 기업.
AI 기술이란: 기계학습·예측분석(예측정비, 이상징후 탐지, 가상계측) / 생성형 AI·LLM(절차서·인허가 문서
검색·작성 보조, 운전원 지식 보조) / 컴퓨터 비전(검사 영상·용접부·연료 결함 자동 판독) /
ML 로 학습·갱신되는 디지털 트윈 / 규제기관의 AI 지침·심사 / 사업자의 AI 도입 전략·인력 양성.

경계 규칙 — AI 기술이 기사의 주어·목적어이거나, 보도된 성과를 만든 **핵심 수단**이어야 한다.
  · "AI 비전으로 용접부를 판독하는 검사 로봇" → 포함 (AI 판독이 핵심)
  · "원격 해체 로봇 투입", "VR 훈련 시설 개소" → 제외 robotics_only (AI 가 없거나 수식어)
  · "물리 기반 3D 시뮬레이터" → 제외 not_ai_use (ML 학습이 없는 디지털 트윈)
  · 한국 매체가 쓴 해외 사례(예: 일본 도쿄전력 AI 도입 보도) → 포함

[제외] exclude_reason 하나를 고른다.
  datacenter_power    AI 데이터센터 전력수요·전력구매계약·전력난 (AI 가 전력을 쓰는 이야기)
  robotics_only       로봇·드론·VR/AR·원격조작이 주제이고 AI 가 핵심이 아님
  ai_industry_general AI 기업·AI 산업 일반 뉴스, "원전처럼 안전하게" 같은 비유
  diplomatic_mention  정상회담·MOU 에서 "AI·원자력 협력"을 나열만 함
  domestic            한국이 주체이거나 무대 (한수원·국내 기업·국내 원전의 AI)
  fusion              핵융합
  academic_only       학술 논문·프리프린트 자체 (연구소의 보도자료·뉴스 기사는 해당 없음)
  defense_weapons     핵무기·핵잠수함·군사 AI
  not_ai_use          원전 기사이지만 AI 기술 적용이 아님 (AI 가 배경 언급뿐)
  not_nuclear         AI 기사이지만 원전 사업이 아님 (발전소 일반·석유·가스·송전 등)
포함이면 exclude_reason = "none".

[포함 기사에만 채우는 분류]
  application_area  operations_maintenance(운영·정비) | inspection_diagnostics(검사·진단) |
                    licensing_regulation(인허가·규제) | design_engineering(설계·엔지니어링) |
                    fuel_cycle(핵연료) | decommissioning(해체) | safeguards_security(안전조치·보안) |
                    workforce_governance(인력·거버넌스)
  ai_tech           ml_predictive | genai_llm | computer_vision | digital_twin_ai | other
  actor_type        utility | vendor | regulator | national_lab | ai_startup | international_org | other
  actor_names       기사에 나온 주체 이름 1~3개 (원문 표기)
  maturity          research(연구) | pilot(시범) | announced(발표·출시) | contract(계약) |
                    deployed(실적용·상용 운영) | regulatory(규제 결정·지침) |
                    discussion(해설·기고·웨비나·행사 — 원전 AI 가 주제이지만 적용 사건은 없음)
                    기사가 말하는 단계를 올려 쓰지 말 것. "도입 계획"은 announced, "시범"은 pilot.
  adoption_significance  0~3 정수. 3=함대 전체 실적용·규제 결정·최초 인허가 /
                    2=단일 호기·단일 사업 실적용·계약 / 1=시범·발표·MOU·연구 / 0=해설·기고·행사
  countries         ISO 3166-1 alpha-2, 사업이 일어나는 나라 (예: US, JP, FR, GB, CA, CN)

[출력] JSON 하나. 다른 텍스트·코드펜스 금지.
{"items": [{"idx": 0, "in_scope": true, "exclude_reason": "none",
  "application_area": "...", "ai_tech": "...", "actor_type": "...", "actor_names": ["..."],
  "maturity": "...", "adoption_significance": 0, "countries": ["US"], "reason": "30자 이내 근거"}]}
제외 기사는 {"idx": 3, "in_scope": false, "exclude_reason": "...", "reason": "..."} 만 쓴다.
모든 idx 가 정확히 한 번씩 등장해야 한다. 확신이 없으면 제외 쪽으로 판정한다."""


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def request_lines(article: dict, idx: int) -> str:
    """기사 한 건의 입력 블록. 평가기와 production 이 **같은 문자열**을 만든다."""
    lines = [f"[{idx}] {_clip(article.get('title'), 240)}"]
    if article.get("title_kr") and article.get("title_kr") != article.get("title"):
        lines.append(f"한글 제목: {_clip(article.get('title_kr'), 160)}")
    if article.get("summary"):
        lines.append(f"요약: {_clip(article.get('summary'), 400)}")
    if article.get("detail"):
        lines.append(f"본문 발췌: {_clip(article.get('detail'), 600)}")
    lines.append(f"출처: {_clip(article.get('publisher') or article.get('domain'), 60)}")
    return "\n".join(lines)


def build_request(articles: list[dict]) -> tuple[str, str]:
    user = "\n\n".join(request_lines(article, idx) for idx, article in enumerate(articles))
    return SYSTEM_PROMPT, user


def _enum(value: object, allowed: tuple[str, ...], default: str = "") -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def normalize_judgment(item: dict) -> dict:
    """모델 응답 한 건 → 통제 값만 남긴 판정. 목록 밖 값은 버린다.

    in_scope=true 인데 exclude_reason 이 none 이 아니면 **제외로 본다** — 두 칸이
    엇갈리면 보수적인 쪽(확신 없으면 제외)이 규칙이다.
    """
    reason = _enum(item.get("exclude_reason"), EXCLUDE_REASONS, "")
    in_scope = bool(item.get("in_scope")) and reason in ("", "none")
    if not in_scope:
        return {
            "in_scope": False,
            "exclude_reason": reason if reason not in ("", "none") else "not_ai_use",
            "reason": _clip(item.get("reason"), 60),
        }
    try:
        significance = max(0, min(3, int(item.get("adoption_significance") or 0)))
    except (TypeError, ValueError):
        significance = 0
    countries = [str(c).strip().upper() for c in (item.get("countries") or [])
                 if isinstance(c, str) and str(c).strip()][:3]
    return {
        "in_scope": True,
        "exclude_reason": "none",
        "application_area": _enum(item.get("application_area"), APPLICATION_AREAS),
        "ai_tech": _enum(item.get("ai_tech"), AI_TECH, "other"),
        "actor_type": _enum(item.get("actor_type"), ACTOR_TYPES, "other"),
        "actor_names": [_clip(n, 60) for n in (item.get("actor_names") or [])
                        if isinstance(n, str) and n.strip()][:3],
        "maturity": _enum(item.get("maturity"), MATURITY, "announced"),
        "adoption_significance": significance,
        "countries": countries,
        "reason": _clip(item.get("reason"), 60),
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


def judge(articles: list[dict], *, call: Callable | None = None,
          batch_size: int = BATCH_SIZE) -> list[dict | None]:
    """기사 목록을 판정한다. ``call`` 은 gemini_client.call_json 과 같은 모양.

    호출 실패한 배치는 None 으로 남긴다 — 실패를 '제외'로 바꾸면 그 기사는
    다시 묻지 않게 되고, 메인 레인의 sent 마킹과 같은 사고가 된다.
    """
    if call is None:
        import gemini_client  # noqa: PLC0415
        import llm_policy  # noqa: PLC0415
        profile = llm_policy.profile(POLICY_TASK)

        def call(system: str, user: str):  # type: ignore[misc]
            return gemini_client.call_json(
                system, user, temperature=0.1, max_output_tokens=4096,
                model=profile.model(), label=POLICY_TASK, **profile.reasoning_kwargs())

    results: list[dict | None] = []
    for start in range(0, len(articles), batch_size):
        chunk = articles[start:start + batch_size]
        system, user = build_request(chunk)
        try:
            payload = call(system, user)
        except Exception as exc:  # noqa: BLE001 — 실패는 None 으로 남긴다
            print(f"  ! ai_lane judge chunk {start // batch_size + 1} failed: {exc}")
            results.extend([None] * len(chunk))
            continue
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        results.extend(parse_response(payload if isinstance(payload, dict) else {}, len(chunk)))
    return results
