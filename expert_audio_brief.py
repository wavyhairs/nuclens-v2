"""Nuclens 전문가 오디오 브리핑 — story/issue를 1인 수석 분석가가 약 10분 해설한다.

설계 원칙
- 뉴스 탐색·story dedup·선정은 nuclear-news-main의 결과를 그대로 신뢰한다.
- NucBrief의 강점(Article Dossier → 시간배분 → Episode Plan → 대본 → 독립검증/수정)을
  별도 프레임워크 의존성 없이 기존 gemini_client 호출 방식으로 이식한다.
- '분석은 다중 관점, 전달은 단일 화자': 정책·사업 / 기술·운영 렌즈는 dossier에서
  각각 점검하되 최종 음성은 수석 원자력 분석가(Kore) 한 명만 말한다.
- TTS는 audio_brief의 검증된 900자 청크·무음 trim·450ms gap·dynaudnorm+loudnorm을
  재사용하되, 긴 프로그램의 완주율을 위해 모델 전환을 초반/후반에 다르게 처리한다.
- 빠른 브리핑과 독립적으로 실패한다. audio/audio.json의 variants.expert만 갱신한다.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import gemini_client
from gemini_client import GeminiError, call_json, is_available
from audio_brief import (
    AUDIO_DIR,
    CHUNK_GAP_SEC,
    CHUNK_SPOKEN,
    KST,
    SPEAKER_RE,
    VOICES,
    WEB_DATA,
    _audio_manifest,
    _check_not_truncated,
    _script_models,
    _tts_models,
    _write_audio_variant,
    call_tts,
    load_briefing,
    split_script,
    to_mp3,
    trim_silence,
)

EXPERT_VARIANT = "expert"
EXPERT_TARGET_SECONDS = 600
EXPERT_BODY_SECONDS = 540
EXPERT_MAX_ISSUES = 6
EXPERT_MIN_ISSUE_SECONDS = 55
EXPERT_MAX_ISSUE_SECONDS = 150
EXPERT_MIN_SPOKEN = 3600
EXPERT_MAX_SPOKEN = 5200
EXPERT_TTS_RETRIES = 2
EXPERT_EARLY_RESTART_SEGMENTS = 2

# 단일 화자에 남으면 안 되는 대화 흔적. 문장 중간의 정상적인 '맞습니다'는 건드리지 않는다.
_SINGLE_FILLER_RE = re.compile(
    r"^(?:(?:네|예|그렇군요|그렇죠|맞습니다|좋습니다|알겠습니다)[,.!]?\s*)+",
    re.IGNORECASE,
)
_ANY_SPEAKER_RE = re.compile(r"^[A-Za-z가-힣 _-]{1,30}:\s*(.+)$")

DOSSIER_SYSTEM = """당신은 원자력 정책·기술·운영·사업을 함께 보는 선임 원자력 분석가입니다.
Nuclens가 이미 선정·중복제거한 briefing story들을 전문가 오디오용 dossier로 구조화합니다.
입력에 없는 사실을 만들지 말고, 기사사실과 분석을 엄격히 구분하십시오.
모든 출력은 요청한 JSON 형식만 반환하십시오."""

PLAN_SYSTEM = """당신은 원자력 전문가용 오디오 편집자입니다. 주어진 dossier와 결정론적
시간배분을 바꾸지 말고, 약 10분 브리핑의 논리적 순서와 전환을 설계하십시오.
연관성이 약한 사건을 억지로 인과관계로 묶지 마십시오. JSON만 반환하십시오."""

SCRIPT_SYSTEM = """당신은 한수원 임직원이 듣는 Nuclens의 수석 원자력 분석가입니다.
정책·사업과 기술·운영의 두 관점을 내부적으로 통합해 한 명이 설명합니다.
뉴스 앵커처럼 제목만 읽지 말고, 자료를 보고 판단의 경계를 설명하는 전문가 문체로 말합니다.
입력 dossier와 episode plan에 없는 숫자·기관·일정·인과관계를 만들지 마십시오.
모든 대사는 HOST: 로 시작하고 JSON만 반환하십시오."""

VERIFY_SYSTEM = """당신은 오디오 대본의 독립 팩트체커입니다. 대본을 제공된 dossier와만
대조하십시오. 발표·협의·후보선정·허가·착공·운전 같은 사업단계를 특히 엄격히 구분하고,
근거 없는 중대한 주장은 반드시 unsupported_critical_claims에 기록하십시오. JSON만 반환하십시오."""

REPAIR_SYSTEM = """당신은 검증 지시만 반영하는 원자력 오디오 대본 편집자입니다.
근거 없는 주장은 삭제하거나 입력 dossier가 허용하는 범위의 조건형 표현으로 낮추고,
검증에 문제없는 정보는 가능한 유지하십시오. 한 명의 HOST만 사용하고 JSON만 반환하십시오."""


def _call_structured(system: str, message: str, *, label: str, temperature: float = 0.2,
                     max_output_tokens: int = 8192) -> dict:
    """기존 nuclear-news-main의 Gemini 사다리를 그대로 이용한다."""
    models: list[str] = []
    # 분석/검증은 공용 큐레이션 모델을 우선한다. 없으면 audio script 사다리를 쓴다.
    if gemini_client.MODEL:
        models.append(gemini_client.MODEL)
    for model in _script_models():
        if model not in models:
            models.append(model)
    last: Exception | None = None
    for model in models:
        try:
            return call_json(
                system,
                message,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                timeout=150.0,
                thinking_budget=0,
                model=model,
                retries=4,
                label=label,
            )
        except GeminiError as exc:
            last = exc
            print(f"[expert-audio] {label} {model} 실패 — 폴백: {str(exc)[:180]}")
    raise last or GeminiError(f"{label} 모델 전부 실패")


def _compact_article(article: dict) -> dict:
    """LLM에 필요한 증거만 남긴다. 원문 전문 대신 archive의 구조화 사실을 보낸다."""
    return {
        "hash": article.get("hash") or "",
        "date": article.get("article_date") or article.get("date") or "",
        "title": article.get("title_kr") or article.get("title") or "",
        "publisher": article.get("publisher") or article.get("domain") or "",
        "url": article.get("url") or "",
        "summary": article.get("summary") or "",
        "detail": article.get("detail") or "",
        "latest_change": article.get("latest_change") or "",
        "event_type": article.get("event_type") or (article.get("features") or {}).get("event_type") or "",
        "event_date": article.get("event_date") or "",
        "tags": article.get("canonical_tags") or article.get("tags") or [],
        "topics": article.get("topics") or [],
        "countries": article.get("countries") or [],
        "source_tier": article.get("source_tier"),
    }


def issue_material(issue: dict) -> dict:
    """웹 issue를 NucBrief ArticleDossier 입력과 같은 증거 묶음으로 만든다."""
    related = issue.get("related_articles") or []
    articles = [_compact_article(row) for row in related[:5] if isinstance(row, dict)]
    if not articles:
        articles = [_compact_article(issue)]
    return {
        "issue_id": issue.get("issue_id") or "",
        "title": issue.get("title") or issue.get("title_kr") or "",
        "summary": issue.get("summary") or "",
        "detail": issue.get("detail") or "",
        "latest_change": issue.get("latest_change") or "",
        "why_important": issue.get("why_important") or "",
        "implication": issue.get("implication") or "",
        "open_question": issue.get("open_question") or "",
        "selection_score": issue.get("selection_score") or 0,
        "selection_reasons": issue.get("selection_reasons") or [],
        "report_pick": bool(issue.get("report_pick")),
        "verification": issue.get("verification") or {},
        "story": {
            "article_count": issue.get("story_article_count") or 1,
            "outlet_count": issue.get("story_outlet_count") or 1,
            "tier1_count": issue.get("story_tier1_count") or 0,
            "relation": issue.get("story_relation") or "",
            "reason": issue.get("story_reason") or "",
            "fingerprint": issue.get("story_fingerprint") or {},
            "related_titles": issue.get("story_related_titles") or [],
        },
        "articles": articles,
    }


def selected_issues(briefing: dict, by_id: dict, limit: int = EXPERT_MAX_ISSUES) -> list[dict]:
    """하이라이트 우선 + 나머지 순서를 보존. 같은 issue_id는 한 번만."""
    ids: list[str] = []
    for row in briefing.get("highlight_issues") or []:
        if isinstance(row, dict) and row.get("issue_id"):
            ids.append(str(row["issue_id"]))
    for row in briefing.get("issues") or []:
        if isinstance(row, dict) and row.get("issue_id"):
            ids.append(str(row["issue_id"]))
    unique: list[dict] = []
    seen: set[str] = set()
    for issue_id in ids:
        if issue_id in seen or issue_id not in by_id:
            continue
        seen.add(issue_id)
        unique.append(by_id[issue_id])
        if len(unique) >= limit:
            break
    return unique


def dossier_prompt(briefing: dict, issues: list[dict]) -> str:
    material = [issue_material(issue) for issue in issues]
    return f"""다음 {len(material)}개 briefing story를 각각 dossier로 구조화하십시오.

[두 분석 렌즈 — 하나의 호출 안에서 각각 점검]
1. 정책·사업 렌즈: 정책결정, 규제·인허가/사업 단계, 계약, 경제성·시장, 기관 역할,
   확정사항과 미확정사항을 구분합니다.
2. 기술·운영 렌즈: 원자로·연료·안전·정비·운영·계통 메커니즘과 기술적 제약을 봅니다.
해당하지 않는 렌즈는 억지로 채우지 말고 빈 배열 또는 '해당 없음'으로 둡니다.

[강제 규칙]
- confirmed_facts에는 입력 story/articles에서 직접 확인되는 내용만 둡니다.
- 분석·시사점은 article fact처럼 표현하지 않습니다.
- 발표/검토/협의/후보선정/부지허가/건설허가/착공/최초콘크리트/상업운전을 혼용하지 않습니다.
- 자동정지/수동정지/예방정지/출력감발도 구분합니다.
- URL과 source hash는 입력에 있는 것만 사용합니다.
- 같은 story에 여러 기사가 있으면 추가 정보는 합치되 서로 모순되면 uncertainties에 기록합니다.
- story metadata의 outlet_count는 사실 자체가 아니라 '복수 매체 확인 신호'로만 사용합니다.

[출력 JSON]
{{"dossiers":[{{
 "issue_id":"...",
 "title":"...",
 "confirmed_facts":[{{"fact":"...","source_hashes":["..."],"source_urls":["..."]}}],
 "current_stage":"...",
 "stage_basis":"...",
 "not_yet_confirmed":["..."],
 "policy_business_implications":["..."],
 "technical_operations_implications":["..."],
 "company_relevance":["..."],
 "watchpoints":["..."],
 "uncertainties":["..."]
}}]}}

[브리핑 날짜]
{briefing.get('date','')}

[story 입력]
{json.dumps(material, ensure_ascii=False, indent=2)}"""


def normalize_dossiers(payload: dict, issues: list[dict]) -> list[dict]:
    raw = payload.get("dossiers") if isinstance(payload, dict) else None
    rows = raw if isinstance(raw, list) else []
    by_id = {str(row.get("issue_id")): row for row in rows if isinstance(row, dict) and row.get("issue_id")}
    result: list[dict] = []
    for issue in issues:
        issue_id = str(issue.get("issue_id") or "")
        dossier = by_id.get(issue_id)
        if not dossier:
            # LLM이 하나를 빼먹어도 전체 오디오를 잃지 않는다. 구조화된 웹 사실로 최소 dossier 생성.
            material = issue_material(issue)
            dossier = {
                "issue_id": issue_id,
                "title": material["title"],
                "confirmed_facts": [
                    {"fact": text, "source_hashes": [a["hash"] for a in material["articles"] if a.get("hash")],
                     "source_urls": [a["url"] for a in material["articles"] if a.get("url")]}
                    for text in [material.get("summary"), material.get("latest_change")]
                    if text
                ],
                "current_stage": "입력자료에서 명확히 특정되지 않음",
                "stage_basis": "",
                "not_yet_confirmed": [material["open_question"]] if material.get("open_question") else [],
                "policy_business_implications": [material["implication"]] if material.get("implication") else [],
                "technical_operations_implications": [],
                "company_relevance": [],
                "watchpoints": [],
                "uncertainties": [],
            }
        result.append(dossier)
    return result


def _weight(issue: dict, highlight_ids: set[str]) -> float:
    score = float(issue.get("selection_score") or 0)
    weight = 1.0 + min(3.0, max(0.0, score) / 12.0)
    if str(issue.get("issue_id") or "") in highlight_ids:
        weight += 1.6
    if issue.get("report_pick"):
        weight += 0.5
    outlets = int(issue.get("story_outlet_count") or 1)
    if outlets >= 2:
        weight += min(0.7, 0.18 * (outlets - 1))
    if int(issue.get("story_tier1_count") or 0) >= 1:
        weight += 0.35
    return weight


def allocate_seconds(briefing: dict, issues: list[dict], total: int = EXPERT_BODY_SECONDS) -> list[dict]:
    """NucBrief의 중요도 기반 시간배분을 deterministic하게 이식한다."""
    if not issues:
        return []
    highlight_ids = {
        str(row.get("issue_id")) for row in briefing.get("highlight_issues") or []
        if isinstance(row, dict) and row.get("issue_id")
    }
    n = len(issues)
    floor = min(EXPERT_MIN_ISSUE_SECONDS, max(30, total // max(1, n * 2)))
    base = floor * n
    remaining = max(0, total - base)
    weights = [_weight(issue, highlight_ids) for issue in issues]
    total_weight = sum(weights) or float(n)
    seconds = [floor + round(remaining * w / total_weight) for w in weights]

    # 한 기사에 프로그램이 잠식되지 않게 cap한 뒤 남은 초를 cap 미달 기사에 재분배.
    overflow = 0
    for index, value in enumerate(seconds):
        if value > EXPERT_MAX_ISSUE_SECONDS:
            overflow += value - EXPERT_MAX_ISSUE_SECONDS
            seconds[index] = EXPERT_MAX_ISSUE_SECONDS
    while overflow > 0:
        candidates = [i for i, value in enumerate(seconds) if value < EXPERT_MAX_ISSUE_SECONDS]
        if not candidates:
            break
        moved = False
        for i in candidates:
            if overflow <= 0:
                break
            seconds[i] += 1
            overflow -= 1
            moved = True
        if not moved:
            break

    # 반올림 오차를 total에 최대한 맞춘다.
    diff = total - sum(seconds)
    direction = 1 if diff > 0 else -1
    for i in range(abs(diff)):
        idx = i % n
        candidate = seconds[idx] + direction
        if 30 <= candidate <= EXPERT_MAX_ISSUE_SECONDS:
            seconds[idx] = candidate
    return [
        {"issue_id": issue.get("issue_id"), "seconds": seconds[i], "weight": round(weights[i], 2)}
        for i, issue in enumerate(issues)
    ]


def plan_prompt(briefing: dict, dossiers: list[dict], allocations: list[dict]) -> str:
    return f"""약 10분짜리 1인 전문가 브리핑 EpisodePlan을 작성하십시오.
- allocations의 seconds는 바꾸지 않습니다.
- 공통 주제가 강하면 integrated_theme, 아니면 expert_news_magazine을 선택합니다.
- 오프닝은 오늘의 2~3개 핵심 흐름을 예고하고, 본문은 각 핵심 story에 대해 가능한 범위에서
  '무슨 일 → 현재 단계 → 기술·운영 의미 → 정책·사업 의미 → 다음 관찰점'으로 연결합니다.
- 연관 없는 뉴스를 억지로 하나의 흐름으로 만들지 않습니다.
- 클로징은 기사 반복이 아니라 공통 변화와 아직 확인할 사항을 종합합니다.

[출력 JSON]
{{"structure":"integrated_theme|expert_news_magazine","opening_focus":["..."],
 "segments":[{{"issue_id":"...","seconds":90,"focus":["..."],"transition":"..."}}],
 "closing_synthesis":["..."]}}

[날짜/헤드라인]
{briefing.get('date','')} / {briefing.get('headline','')}
[시간배분]
{json.dumps(allocations, ensure_ascii=False, indent=2)}
[Dossiers]
{json.dumps(dossiers, ensure_ascii=False, indent=2)}"""


def script_prompt(briefing: dict, dossiers: list[dict], plan: dict) -> str:
    return f"""EpisodePlan과 dossiers만 근거로 약 10분의 1인 전문가 Script를 작성하십시오.

[전달 방식]
- 화자는 수석 원자력 분석가 한 명뿐이며 모든 줄은 HOST: 로 시작합니다.
- 가상의 질문자, 자문자답, '네/그렇군요/맞습니다' 같은 대화형 추임새를 금지합니다.
- 뉴스 제목을 연속 낭독하지 말고 전문가가 자료를 보며 설명하는 구어체 존댓말로 씁니다.
- 문단 하나는 2~5문장. 기사마다 기계적으로 같은 서식을 반복하지 않습니다.
- 정책·사업과 기술·운영의 두 관점은 필요할 때 자연스럽게 통합합니다.

[정확성]
- confirmed_facts 밖의 사실을 새로 만들지 않습니다.
- 분석은 분석임이 드러나도록 쓰고 회사의 확정입장처럼 표현하지 않습니다.
- 현재 단계와 미확정사항을 명확히 구분합니다.
- 수치·기관·날짜·호기명은 dossier와 일치해야 합니다.
- 막연한 '중요합니다/기대됩니다/귀추가 주목됩니다'를 쓰지 않습니다.
- 오프닝과 클로징은 시스템이 붙이므로 인사/마무리를 쓰지 않습니다.
- 본문 대사 합계 {EXPERT_MIN_SPOKEN:,}~{EXPERT_MAX_SPOKEN:,}자를 목표로 합니다.

[출력 JSON]
{{"script":"HOST: ...\\nHOST: ..."}}

[브리핑]
{json.dumps({'date': briefing.get('date'), 'headline': briefing.get('headline')}, ensure_ascii=False)}
[EpisodePlan]
{json.dumps(plan, ensure_ascii=False, indent=2)}
[Dossiers]
{json.dumps(dossiers, ensure_ascii=False, indent=2)}"""


def normalize_script(text: str) -> tuple[str, int]:
    lines: list[str] = []
    spoken = 0
    for raw in str(text or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        match = SPEAKER_RE.match(raw)
        if match:
            body = match.group(2).strip()
        else:
            generic = _ANY_SPEAKER_RE.match(raw)
            body = generic.group(1).strip() if generic else raw
        body = _SINGLE_FILLER_RE.sub("", body, count=1).strip()
        if not body:
            continue
        # 모델의 자체 오프닝/클로징은 고정 frame과 중복되므로 제거한다.
        if re.search(r"안녕하십니까|안녕하세요|브리핑을 시작|브리핑은 여기까지|감사합니다", body):
            continue
        lines.append(f"HOST: {body}")
        spoken += len(body)
    if len(lines) < 8:
        raise ValueError(f"전문가 대본 문단 {len(lines)}개 — 형식 미달")
    return "\n".join(lines), spoken


def expert_frame(briefing: dict, plan: dict) -> tuple[str, str]:
    date = datetime.strptime(str(briefing["date"]), "%Y-%m-%d")
    weekday = "월화수목금토일"[date.weekday()]
    opening = (
        f"HOST: {date.month}월 {date.day}일 {weekday}요일 Nuclens 전문가 브리핑입니다. "
        "오늘 주요 이슈의 현재 단계와 기술·정책적 의미를 함께 짚겠습니다."
    )
    closing = "HOST: 지금까지 Nuclens 전문가 브리핑이었습니다."
    return opening, closing


def apply_expert_frame(script: str, briefing: dict, plan: dict) -> str:
    opening, closing = expert_frame(briefing, plan)
    return "\n".join([opening, script, closing])


def verification_prompt(briefing: dict, dossiers: list[dict], script: str) -> str:
    return f"""다음 대본을 dossiers와만 대조해 독립 검증하십시오.

[점수]
- coverage_score: 선정된 모든 dossier 핵심이 적절한 깊이로 반영됐는가
- factual_support_score: 숫자·기관·일정·사실이 dossier 근거 안에 있는가
- stage_precision_score: 발표/검토/허가/착공/운영 등의 단계를 정확히 구분했는가
- expert_depth_score: 기술·정책·사업 의미가 반복/일반론 없이 실질적인가
- single_speaker_score: 한 명의 분석가로 일관되고 가상대화·맞장구가 없는가

중대한 근거없는 사실, 확정되지 않은 일정, 과장된 인과관계는 unsupported_critical_claims에
{{"claim":"...","reason":"...","repair":"..."}}로 기록하십시오.
점수가 coverage>=92, factual>=96, stage>=96, depth>=88, single>=98이고 critical claim이 없을 때만 passed=true.

[출력 JSON]
{{"passed":true,"coverage_score":0,"factual_support_score":0,"stage_precision_score":0,
 "expert_depth_score":0,"single_speaker_score":0,"unsupported_critical_claims":[],
 "repair_instructions":["..."]}}

[브리핑 날짜] {briefing.get('date','')}
[Dossiers]
{json.dumps(dossiers, ensure_ascii=False, indent=2)}
[Script]
{script}"""


def verification_passed(report: dict) -> bool:
    if not isinstance(report, dict) or report.get("unsupported_critical_claims"):
        return False
    thresholds = {
        "coverage_score": 92,
        "factual_support_score": 96,
        "stage_precision_score": 96,
        "expert_depth_score": 88,
        "single_speaker_score": 98,
    }
    return bool(report.get("passed")) and all(float(report.get(k) or 0) >= v for k, v in thresholds.items())


def repair_prompt(dossiers: list[dict], script: str, report: dict) -> str:
    return f"""검증보고서의 지적만 반영해 전체 대본을 수정하십시오.
- unsupported claim은 삭제하거나 dossier가 직접 허용하는 조건형 문장으로 낮춥니다.
- 사업단계 오류를 우선 수정합니다.
- 정보량과 전문가 깊이는 유지합니다.
- 수석 원자력 분석가 한 명만 말하며 모든 줄은 HOST: 로 시작합니다.
- 본문 대사 {EXPERT_MIN_SPOKEN:,}~{EXPERT_MAX_SPOKEN:,}자 범위를 지킵니다.

[출력 JSON] {{"script":"HOST: ...\\nHOST: ..."}}
[검증보고서]
{json.dumps(report, ensure_ascii=False, indent=2)}
[Dossiers]
{json.dumps(dossiers, ensure_ascii=False, indent=2)}
[기존 Script]
{script}"""


def generate_expert_script(briefing: dict, issues: list[dict]) -> tuple[str, list[dict], dict, dict]:
    dossier_payload = _call_structured(
        DOSSIER_SYSTEM, dossier_prompt(briefing, issues), label="expert_dossiers",
        temperature=0.1, max_output_tokens=10000,
    )
    dossiers = normalize_dossiers(dossier_payload, issues)
    allocations = allocate_seconds(briefing, issues)
    plan = _call_structured(
        PLAN_SYSTEM, plan_prompt(briefing, dossiers, allocations), label="expert_plan",
        temperature=0.2, max_output_tokens=5000,
    )
    draft = _call_structured(
        SCRIPT_SYSTEM, script_prompt(briefing, dossiers, plan), label="expert_script",
        temperature=0.35, max_output_tokens=12000,
    )
    script, spoken = normalize_script(draft.get("script"))
    if spoken < EXPERT_MIN_SPOKEN or spoken > EXPERT_MAX_SPOKEN:
        retry = _call_structured(
            SCRIPT_SYSTEM,
            script_prompt(briefing, dossiers, plan)
            + f"\n\n[재요청] 직전 원고는 {spoken:,}자였습니다. 목표 범위를 반드시 지켜 전체를 다시 쓰십시오.",
            label="expert_script_length_retry", temperature=0.3, max_output_tokens=12000,
        )
        script, spoken = normalize_script(retry.get("script"))
        if spoken < EXPERT_MIN_SPOKEN * 0.85 or spoken > EXPERT_MAX_SPOKEN * 1.08:
            raise ValueError(f"전문가 대본 분량 {spoken:,}자 — 허용범위 이탈")

    report = _call_structured(
        VERIFY_SYSTEM, verification_prompt(briefing, dossiers, script), label="expert_verify",
        temperature=0.0, max_output_tokens=6000,
    )
    if not verification_passed(report):
        repaired = _call_structured(
            REPAIR_SYSTEM, repair_prompt(dossiers, script, report), label="expert_repair",
            temperature=0.15, max_output_tokens=12000,
        )
        script, _ = normalize_script(repaired.get("script"))
        report = _call_structured(
            VERIFY_SYSTEM, verification_prompt(briefing, dossiers, script), label="expert_verify_after_repair",
            temperature=0.0, max_output_tokens=6000,
        )
    if not verification_passed(report):
        claims = report.get("unsupported_critical_claims") if isinstance(report, dict) else []
        raise ValueError(f"전문가 대본 검증 미통과 — critical={len(claims or [])}, scores={_score_summary(report)}")
    return apply_expert_frame(script, briefing, plan), dossiers, plan, report


def _score_summary(report: dict) -> str:
    if not isinstance(report, dict):
        return "invalid"
    return "/".join(str(report.get(k) or 0) for k in (
        "coverage_score", "factual_support_score", "stage_precision_score", "expert_depth_score"))


def _tts_chunk_retry(index: int, chunk: str, model: str) -> tuple[bytes, int]:
    """HTTP 성공인데 짧게 잘린 TTS는 절대 채택하지 않고 동일 model로 한 번 재생성."""
    last: Exception | None = None
    for attempt in range(1, EXPERT_TTS_RETRIES + 1):
        try:
            pcm, rate = call_tts(chunk, models=[model])
            _check_not_truncated(index, chunk, pcm, rate)
            return pcm, rate
        except GeminiError as exc:
            last = exc
            if attempt < EXPERT_TTS_RETRIES:
                print(f"[expert-audio] 청크 {index} 품질 실패 — {model} 동일 청크 재생성 {attempt+1}/{EXPERT_TTS_RETRIES}: {exc}")
    raise last or GeminiError(f"청크 {index} 생성 실패")


def synthesize_expert(script: str) -> tuple[bytes, int, list[str], list[str]]:
    """긴 10분 대본용 hybrid fallback.

    초반(완료<=2청크)에 모델이 바뀌면 음색 일관성을 위해 처음부터 다시 만든다.
    후반부에는 완주율/쿼터를 위해 정상 청크를 보존하고 실패한 지점부터 다음 모델로 잇는다.
    모든 청크는 trim 후 정확히 450ms gap으로 연결한다.
    """
    chunks = split_script(script, limit=CHUNK_SPOKEN)
    print(f"[expert-audio] 대본 {len(script):,}자 → TTS 청크 {len(chunks)}개")
    pieces: list[bytes] = []
    segment_models: list[str] = []
    rate = 0
    warnings: list[str] = []
    last: Exception | None = None

    for model_index, model in enumerate(_tts_models()):
        if len(pieces) >= len(chunks):
            break
        completed = len(pieces)
        if model_index > 0 and 0 < completed <= EXPERT_EARLY_RESTART_SEGMENTS:
            previous = segment_models[-1] if segment_models else "이전 모델"
            print(f"[expert-audio] 초반 모델 전환 {previous} → {model}: {completed}청크 폐기 후 전체 재생성")
            pieces = []
            segment_models = []
            rate = 0
            completed = 0
            warnings.append(f"초반 TTS 모델 전환으로 {model}에서 전체 음색을 통일했습니다.")

        try:
            for index in range(len(pieces), len(chunks)):
                pcm, chunk_rate = _tts_chunk_retry(index + 1, chunks[index], model)
                if rate and chunk_rate != rate:
                    raise GeminiError(f"청크 {index+1} sample rate {chunk_rate} != {rate}")
                rate = chunk_rate
                pieces.append(trim_silence(pcm, rate))
                segment_models.append(model)
            break
        except GeminiError as exc:
            last = exc
            print(f"[expert-audio] {model} 실패 ({len(pieces)}/{len(chunks)} 완료) — 다음 모델: {exc}")
            continue

    if len(pieces) != len(chunks):
        raise last or GeminiError(f"전문가 TTS 미완성 {len(pieces)}/{len(chunks)}")
    if len(set(segment_models)) > 1:
        warnings.append(
            "후반 TTS 모델 전환 시 완주를 위해 정상 구간을 유지했습니다. "
            "구간 경계에서 음색이 미세하게 달라질 수 있습니다."
        )
    gap = b"\x00" * (int(rate * CHUNK_GAP_SEC) * 2)
    merged: list[bytes] = []
    for i, pcm in enumerate(pieces):
        if i:
            merged.append(gap)
        merged.append(pcm)
    return b"".join(merged), rate, segment_models, warnings


def generate(force: bool = False) -> bool:
    if not is_available():
        print("[expert-audio] GEMINI_API_KEY 없음 — 스킵")
        return False
    briefing, by_id = load_briefing(WEB_DATA)
    if not briefing:
        print("[expert-audio] briefings.json 없음/비어 있음 — build_data 이후 실행 필요")
        return False
    date = str(briefing["date"])
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"briefing-expert-{date}.mp3"
    mp3_path = AUDIO_DIR / file_name
    manifest = _audio_manifest()
    existing = ((manifest.get("variants") or {}).get(EXPERT_VARIANT) or {}) if manifest.get("date") == date else {}
    if not force and existing.get("file") and (AUDIO_DIR / existing["file"]).exists():
        print(f"[expert-audio] {date} 전문가 브리핑 이미 생성됨 — 스킵")
        return True

    issues = selected_issues(briefing, by_id)
    if not issues:
        print("[expert-audio] 전문가 브리핑 대상 이슈 없음")
        return False
    try:
        script, dossiers, plan, verification = generate_expert_script(briefing, issues)
        pcm, rate, tts_models, warnings = synthesize_expert(script)
        to_mp3(pcm, rate, mp3_path, bitrate="128k")
    except (GeminiError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[expert-audio] 생성 실패 — 기존 variant 유지: {exc}")
        return False

    duration = int(len(pcm) / 2 / rate)
    spoken = sum(len(match.group(2)) for match in
                 (SPEAKER_RE.match(line) for line in script.splitlines()) if match)
    meta = {
        "date": date,
        "key": EXPERT_VARIANT,
        "label": "전문가 브리핑",
        "description": "선정된 핵심 이슈를 수석 원자력 분석가가 사업 단계·기술·정책 의미까지 약 10분간 통합 해설합니다.",
        "file": file_name,
        "duration_sec": duration,
        "generated_at": datetime.now(KST).isoformat(),
        "script_chars": spoken,
        "voices": VOICES,
        "format_version": 2,
        "delivery_mode": "expert_single",
        "issue_count": len(issues),
        "dossier_count": len(dossiers),
        "verification": {
            key: verification.get(key) for key in (
                "coverage_score", "factual_support_score", "stage_precision_score",
                "expert_depth_score", "single_speaker_score", "passed"
            )
        },
        "tts_models": list(dict.fromkeys(tts_models)),
        "warnings": warnings,
    }
    _write_audio_variant(date, EXPERT_VARIANT, meta)
    (AUDIO_DIR / f"script-expert-{date}.txt").write_text(script, encoding="utf-8")
    (AUDIO_DIR / f"verification-expert-{date}.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (AUDIO_DIR / f"dossiers-expert-{date}.json").write_text(
        json.dumps({"plan": plan, "dossiers": dossiers}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for pattern, current in [
        ("briefing-expert-*.mp3", file_name),
        ("script-expert-*.txt", f"script-expert-{date}.txt"),
        ("verification-expert-*.json", f"verification-expert-{date}.json"),
        ("dossiers-expert-*.json", f"dossiers-expert-{date}.json"),
    ]:
        for old in AUDIO_DIR.glob(pattern):
            if old.name != current:
                old.unlink(missing_ok=True)
    print(f"[expert-audio] {date} 완료 — {file_name} ({duration}초, {mp3_path.stat().st_size/1024:.0f} KB)")
    return True


if __name__ == "__main__":
    ok = False
    try:
        ok = generate(force="--force" in sys.argv)
    except Exception as exc:  # noqa: BLE001 — 오디오는 웹 배포 비치명 기능
        import traceback
        traceback.print_exc()
        print(f"[expert-audio] 예상 밖 실패 — 비치명: {exc}")
    sys.exit(0 if ok else 1)
