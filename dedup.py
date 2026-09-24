"""
Cross-topic 중복 제거.

문제: 같은 사건(예: "Microsoft–Constellation TMI 재가동 PPA")이 SMR · 원전일반 ·
AI거래 · 재가동 트렌드 등 여러 토픽에 동시 등장 → 텔레그램에 4번 반복 발송.

해결:
  1. URL 정규화 + 정확 일치 1차 dedup (utm·앵커·트래커 제거)
  2. Gemini가 의미 기반으로 "같은 사건" 그룹핑 (한 번의 API 호출)
  3. 각 그룹에서 boosted_score 최고치인 cluster만 "대표"로 통과,
     나머지는 발송 대상에서 제외

connect-ai의 `ceo-planner.md` 패턴 차용 — JSON-only 출력, 펜스 금지,
규칙 명시(같은 사건 정의), 환각 방지 룰.
"""

from __future__ import annotations

import re
import sys
from typing import Iterable
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# Windows 콘솔 UTF-8 강제 (한국어 print시 cp949 에러 방지)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

import event_stage
import admin_overrides
import llm_policy
from gemini_client import GeminiError, call_json, is_available
from story_cluster import consolidate_story_metadata


# ---- 프롬프트 (connect-ai 스타일 외부화 — 인라인 상수) ----------------------
#
# 향후 프롬프트가 2~3개 늘어나면 prompts/*.md 로 분리. 지금은 1개라 인라인.

DEDUP_SYSTEM_PROMPT = """당신은 원자력·에너지 뉴스 중복 제거 분류기입니다.

입력으로 헤드라인 N개를 받습니다. 같은 사건을 다루는 헤드라인끼리 그룹핑하세요.

⚠️ 출력은 정확히 아래 JSON 형식. 다른 텍스트(설명, 펜스 ```, 머리말, 꼬리말)는 단 한 글자도 금지.

{"groups": [[0, 3, 7], [1], [2, 5], [4], [6]]}

규칙:
1. 각 그룹은 같은 사건(같은 회사·시설·정책에 대한 같은 행동·발표·결정)을 다루는 헤드라인 인덱스의 배열.
2. 혼자인 사건은 단일 원소 그룹 [idx] 으로 표현.
3. 모든 인덱스가 정확히 한 그룹에만 등장해야 함. 빠지거나 중복 금지.
4. "같은 사건" 판정 기준 (엄격):
   - 같은 주체(회사·국가·기관) + 같은 객체(시설·정책·계약) + 같은 행동(발표·승인·취소·재가동)
   - 비슷한 토픽(예: 둘 다 SMR) 이지만 다른 회사·다른 프로젝트면 → 다른 그룹
   - 같은 사건의 후속 업데이트(예: "발표" → "공식 확정")는 → 같은 그룹
5. 확신이 없으면 같은 그룹으로 묶지 말고 분리. (오버그루핑이 더 나쁨)

입력 형식: 각 줄이 `[idx] 제목 | 메타`."""


# ---- URL 정규화 -------------------------------------------------------------

# 트래킹 파라미터 (제거 대상)
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_brand", "utm_social",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid",
    "ref", "ref_src", "ref_url", "source", "share", "shared",
    "_branch_match_id", "_ga", "igshid", "feature",
}


def normalize_url(url: str | None) -> str:
    """URL을 캐노니컬 형태로 변환. None이면 빈 문자열."""
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
    except Exception:
        return url.strip().lower()

    scheme = "https" if p.scheme in ("http", "https", "") else p.scheme
    netloc = p.netloc.lower()
    # m.example.com, www.example.com → example.com
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc.startswith("m."):
        netloc = netloc[2:]
    # 모바일 트위터 → x.com
    if netloc in ("twitter.com", "mobile.twitter.com", "nitter.net"):
        netloc = "x.com"
    if netloc in ("youtu.be",):
        netloc = "youtube.com"

    path = p.path.rstrip("/")

    # 트래킹 쿼리 제거 + 알파벳 순 정렬 (안정적 비교)
    qs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
          if k.lower() not in _TRACKING_PARAMS]
    qs.sort()
    query = urlencode(qs)

    # fragment 제거
    return urlunparse((scheme, netloc, path, "", query, ""))


# ---- 단계 ①: URL 일치 dedup ------------------------------------------------

def _url_groups(clusters: list[dict]) -> dict[str, list[int]]:
    """정규화된 URL이 같은 cluster들을 묶음. URL 없는 건 각자 단독."""
    out: dict[str, list[int]] = {}
    for i, c in enumerate(clusters):
        norm = normalize_url(c.get("url"))
        key = norm if norm else f"__no_url_{i}"
        out.setdefault(key, []).append(i)
    return out


# ---- 단계 ②: LLM 의미 dedup ------------------------------------------------

def _format_cluster_line(idx: int, cluster: dict) -> str:
    title = (cluster.get("title") or "").replace("\n", " ").strip()[:180]
    meta = (cluster.get("meta") or "").replace("\n", " ").strip()[:80]
    return f"[{idx}] {title} | {meta}"


def _llm_semantic_groups(clusters: list[dict]) -> list[list[int]]:
    """Gemini에게 그룹핑 요청. 실패 시 모든 cluster를 단독 그룹으로 fallback."""
    if not is_available():
        # API 키 없으면 LLM 단계 skip — 각자 단독 그룹
        print("[dedup] GEMINI_API_KEY 없음 → 의미 dedup 건너뜀 (URL 단계만 적용)")
        return [[i] for i in range(len(clusters))]

    if len(clusters) <= 1:
        return [[i] for i in range(len(clusters))]

    lines = [_format_cluster_line(i, c) for i, c in enumerate(clusters)]
    payload = "\n".join(lines)

    try:
        result = call_json(
            DEDUP_SYSTEM_PROMPT,
            payload,
            temperature=0.05,
            max_output_tokens=4096,
            timeout=90.0,
            label="dedup",
        )
    except GeminiError as e:
        print(f"[dedup] Gemini 실패 → 단독 그룹 fallback: {e}")
        return [[i] for i in range(len(clusters))]

    groups = result.get("groups")
    if not isinstance(groups, list):
        print(f"[dedup] 응답에 groups 없음 → fallback. payload={result}")
        return [[i] for i in range(len(clusters))]

    # 검증: 모든 인덱스가 정확히 한 번 등장하는지
    seen: set[int] = set()
    cleaned: list[list[int]] = []
    for g in groups:
        if not isinstance(g, list):
            continue
        valid = [i for i in g if isinstance(i, int) and 0 <= i < len(clusters) and i not in seen]
        if valid:
            cleaned.append(valid)
            seen.update(valid)
    # 빠진 인덱스는 단독 그룹으로 보충
    for i in range(len(clusters)):
        if i not in seen:
            cleaned.append([i])

    return cleaned


# ---- 단계 ③: 두 단계 합성 + 대표 선정 -------------------------------------

def dedup_clusters(
    topic_clusters: list[tuple[str, dict]]
) -> tuple[list[tuple[str, dict]], list[tuple[str, dict, str, str]]]:
    """모든 토픽의 (topic_label, cluster) 페어를 받아 dedup.

    Returns:
        kept_pairs: 그룹별 대표 (topic_label, cluster) 리스트
        dropped:    제거된 항목들 (topic_label, cluster, kept_topic, reason)
                    — 로깅·디버깅용
    """
    if not topic_clusters:
        return [], []

    n = len(topic_clusters)
    clusters_only = [c for _, c in topic_clusters]

    # ① URL 정확 일치 dedup으로 1차 그룹 형성
    url_buckets = _url_groups(clusters_only)
    # bucket이 1개짜리뿐이면 LLM 단계로 바로 가도 됨.
    # 다수의 URL 중복이 있어도 LLM 단계에서 일관되게 처리 가능하지만,
    # 비용 절감 위해 이미 같은 URL인 건 LLM에 보낼 필요 없음 → 그룹 대표만 보냄.

    representative_indices: list[int] = []
    url_group_map: dict[int, list[int]] = {}  # rep_idx → all member indices
    for key, members in url_buckets.items():
        # 대표 = 그룹 내 boosted_score 최고
        rep = max(members, key=lambda i: clusters_only[i].get("boosted_score",
                                                              clusters_only[i].get("score", 0)))
        representative_indices.append(rep)
        url_group_map[rep] = members

    # ② 대표들끼리 LLM 의미 dedup
    rep_clusters = [clusters_only[i] for i in representative_indices]
    sem_groups = _llm_semantic_groups(rep_clusters)

    # 의미 그룹 → 원본 인덱스로 펼치기
    final_groups: list[list[int]] = []
    for sem_g in sem_groups:
        merged: list[int] = []
        for local_idx in sem_g:
            global_rep = representative_indices[local_idx]
            merged.extend(url_group_map[global_rep])
        final_groups.append(merged)

    # ③ 각 그룹에서 boosted_score 최고치를 대표로
    kept_pairs: list[tuple[str, dict]] = []
    dropped: list[tuple[str, dict, str, str]] = []
    for g in final_groups:
        winner_idx = max(g, key=lambda i: clusters_only[i].get("boosted_score",
                                                               clusters_only[i].get("score", 0)))
        kept_topic, kept_cluster = topic_clusters[winner_idx]
        kept_pairs.append((kept_topic, kept_cluster))

        # 같은 그룹에 다른 토픽 cluster가 있었으면 dropped 기록
        for j in g:
            if j == winner_idx:
                continue
            t_lbl, c = topic_clusters[j]
            url_same = normalize_url(c.get("url")) == normalize_url(kept_cluster.get("url"))
            reason = "url" if url_same and c.get("url") else "semantic"
            dropped.append((t_lbl, c, kept_topic, reason))

    return kept_pairs, dropped


# ---- 일일 브리핑용: 기사 목록 그대로 받는 얼굴 -----------------------------
#
# 왜 필요한가 — 제목 유사도로는 못 넘는 벽이 실측으로 확정됐다.
#   2026-08-06 웨스팅하우스 한 파트너십이 두 칸: 공유 토큰이 '웨스' 하나(포함 0.143).
#              같은 회사인데 한쪽은 '어멘텀', 다른 쪽은 '아멘텀'이었다.
#   2026-08-07 한수원 필리핀 MOU 두 칸(포함 0.500), 다뉴브강 가뭄 한 사건이 네 칸
#              (포함 0.400~0.444) — 여기도 '팍스'와 '팍시'로 표기가 갈렸다.
# 기준을 그 아래로 내리는 건 답이 아니다: 0.57 에서 이미 '미국-사우디 민간원자력
# 협정'과 '해양광물관리국 NRC MOU'가 붙는다(공유 토큰이 미국·원자·협력·체결 뿐).
# 표기 요동은 글자로 못 넘는다 → 의미로 판정한다.
#
# 계약을 ranking.cluster_duplicates 와 맞춘다 (kept, dropped) + dropped[i]["dup_of"].
# 호출부가 둘을 같은 방식으로 다루고, 큐 정리(prune_hashes)도 그대로 동작한다.

ARTICLE_STORY_PROMPT = """당신은 원자력·에너지 아침 브리핑의 story editor입니다.

입력은 기사 N건입니다. 제목만 보지 말고 TITLE_ORIGINAL, SUMMARY, DETAIL, TAGS,
EVENT_TYPE, EVENT_DATE, 출처를 함께 읽어 **사용자가 하나의 브리핑 이슈로 받아들일 기사**를
같은 그룹으로 묶으세요. 목표는 기사 중복(article duplicate)이 아니라 브리핑 중복
(briefing redundancy) 제거입니다.

⚠️ 출력은 정확히 JSON 하나. 설명·코드펜스 금지.
{
  "groups": [
    {
      "indices": [0, 3],
      "relation": "merge",
      "reason": "같은 EDF 폭염·고수온 원전 가동제약 이슈",
      "fingerprint": {
        "countries": ["France"],
        "actors": ["EDF"],
        "assets": ["French nuclear fleet"],
        "event_family": "operational_constraint",
        "drivers": ["heatwave", "high water temperature"],
        "event_date": "2026-08-13"
      }
    },
    {"indices": [1], "relation": "single", "reason": "", "fingerprint": {}}
  ]
}

판정 규칙:
1. 같은 날짜·국가·기관/기업·시설/정책을 중심으로 같은 원인과 같은 상태변화를 다루면,
   제목의 표현·초점이 달라도 같은 briefing story로 묶는다.
2. 한 기사가 '무슨 일이 발생했는지', 다른 기사가 '왜 발생했고 운영에 어떤 압박인지'를
   분석하더라도 같은 사건/상황을 설명하는 보완 기사라면 relation="merge"로 묶는다.
   예: 'EDF 원전 6기 폭염으로 가동중단' ↔ '장기폭염·고수온·유량감소가 프랑스 원전 운영 위협'.
3. 같은 사건의 단순 재전재·제목변형은 relation="duplicate".
4. **후속 보도라도 새로운 독립적 행동/결정/상태전환**이 있으면 별도 그룹으로 유지한다.
   예: '심사 착수' 이후 '최종 승인', '가동중단' 이후 '재가동 승인', '협상' 이후 '계약 체결'.
   단순 원인분석·배경설명·수치 보강은 새 행동이 아니므로 merge 대상이다.
5. 비슷한 주제(SMR, 우라늄, 폭염)라는 이유만으로 묶지 않는다. 다른 국가·다른 시설·다른
   프로젝트면 별도 그룹이다.
6. 호기·시설이 명시적으로 다르면 원칙적으로 별도 사건이다. 다만 하나의 동일 정부 발표가
   여러 호기를 한 번에 다룬 경우에만 같은 그룹이 가능하다.
7. 모든 인덱스는 정확히 한 번만 등장해야 한다. 확신이 없으면 분리한다.
8. fingerprint는 그룹 판정에 사용한 핵심 사건 지문만 간결하게 기록한다. 모르는 값은 빈 배열/빈 문자열.
"""


EDITORIAL_REDUNDANCY_PROMPT = """당신은 원자력 아침 브리핑의 최종 편집자입니다.

입력은 이미 1차 중복 제거와 중요도 평가를 거친 상위 후보입니다. 제목뿐 아니라 SUMMARY,
DETAIL, STORY_CONTEXT, fingerprint를 읽고, **독자가 두 항목을 연달아 봤을 때 '같은 뉴스를
또 보여준다'고 느낄 조합**만 하나로 묶으세요.

⚠️ 출력은 JSON 하나만:
{"groups": [{"indices": [0, 2], "relation": "merge", "reason": "...", "fingerprint": {}},
            {"indices": [1], "relation": "single", "reason": "", "fingerprint": {}}]}

규칙:
- 같은 underlying event/situation을 사실기사와 분석기사가 각각 다루는 경우 → merge.
- 같은 주제지만 독립적인 정책결정·규제조치·계약·사고·재가동/정지 등 새로운 action이면 분리.
- 한 항목을 없앴을 때 다른 항목만으로 핵심 story를 이해할 수 있고, 없앤 항목의 정보가 배경·원인·
  수치 보강 수준이면 merge.
- 서로 다른 호기/시설/국가/프로젝트는 함부로 묶지 않는다.
- 모든 인덱스 정확히 한 번. 애매하면 분리.
"""


# ---- 실패 기록과 모델 폴백 ------------------------------------------------------
#
# 중복 판정이 실패하면 후보를 전부 유지한다(fail-open). 판정을 못 했다고 뉴스를
# 지울 수는 없으니 그 방향은 맞다. 문제는 **조용하다**는 것이었다 — 2026-09-25
# editorial_final 이 503 으로 죽고 "전량 유지" 한 줄만 남긴 채 브리핑이 나갔다.
# 그래서 ① 반대 버킷 모델로 한 번 더 부르고, ② 그래도 실패하면 여기 적어 두어
# daily_brief 가 운영 알림(quality_event)으로 올린다.
LLM_FAILURES: list[dict] = []


def reset_failures() -> None:
    LLM_FAILURES.clear()


def _record_failure(stage: str, exc: BaseException) -> None:
    LLM_FAILURES.append({"stage": stage, "error": str(exc)[:200]})


def _call_identity_review(prompt: str, payload: str, *, policy_name: str, label: str,
                          client=None, max_output_tokens: int = 6144) -> dict:
    """신원 판정 호출. 기본 모델이 실패하면 반대 버킷 모델로 한 번 더.

    전문가 오디오의 검증 단계와 달리 이 판정에는 '약한 모델로 대신하지 않는다'는
    원자 정책이 없다 — 대신 못 하면 판정 자체가 빠지는 쪽이 더 나쁘다.
    """
    import gemini_client
    policy = llm_policy.profile(policy_name)
    call = client if client is not None else call_json
    models = [policy.model()]
    # 반대 버킷으로 넘긴다 — 기본이 품질 버킷이면 대량 버킷으로, 아니면 그 반대.
    synthesis = gemini_client.synthesis_model()
    fallback = gemini_client.MODEL if models[0] == synthesis else synthesis
    if fallback and fallback not in models:
        models.append(fallback)
    last: GeminiError | None = None
    for index, model in enumerate(models):
        try:
            return call(prompt, payload, temperature=0.05,
                        max_output_tokens=max_output_tokens, timeout=120.0,
                        model=model, label=label if index == 0 else f"{label}:fallback",
                        **policy.reasoning_kwargs())
        except GeminiError as exc:
            last = exc
            if index + 1 < len(models):
                print(f"[dedup] {label} {model} 실패 → {models[index + 1]} 폴백: {str(exc)[:160]}")
    raise last or GeminiError(f"{label} 모델 전부 실패")


def _trim(value, limit: int) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()[:limit]


def _article_block(idx: int, article: dict) -> str:
    """Title-only dedup의 약점을 없애기 위해 이미 수집된 기사 근거를 함께 제공."""
    features = article.get("features") if isinstance(article.get("features"), dict) else {}
    story_ctx = article.get("story_context") if isinstance(article.get("story_context"), list) else []
    ctx_parts = []
    for ctx in story_ctx[:3]:
        if not isinstance(ctx, dict):
            continue
        text = _trim(ctx.get("summary") or ctx.get("detail"), 260)
        if text:
            ctx_parts.append(text)
    tags = ", ".join(str(t) for t in (article.get("tags") or [])[:6])
    return "\n".join([
        f"[{idx}]",
        f"TITLE_KR: {_trim(article.get('title_kr') or article.get('title'), 220)}",
        f"TITLE_ORIGINAL: {_trim(article.get('title'), 220)}",
        f"SOURCE: {_trim(article.get('publisher') or article.get('domain') or article.get('feed'), 100)} | tier={article.get('source_tier', '')}",
        f"SCOPE_SECTION: {_trim(article.get('scope'), 30)} | {_trim(article.get('section'), 40)}",
        f"EVENT: {_trim(features.get('event_type'), 50)} | date={_trim(article.get('event_date'), 30)}",
        f"TAGS: {tags}",
        f"SUMMARY: {_trim(article.get('summary'), 600)}",
        f"DETAIL: {_trim(article.get('detail'), 1000)}",
        f"STORY_CONTEXT: {_trim(' || '.join(ctx_parts), 700)}",
        f"EXISTING_FINGERPRINT: {_trim(article.get('story_fingerprint'), 300)}",
    ])


def _parse_story_groups(result: dict, n: int) -> list[dict]:
    """새 dict 형식과 과거 [[0,1],[2]] 형식을 모두 수용."""
    raw = result.get("groups")
    if not isinstance(raw, list):
        return []

    seen: set[int] = set()
    cleaned: list[dict] = []
    for entry in raw:
        if isinstance(entry, list):
            indices = entry
            relation = "merge" if len(entry) > 1 else "single"
            reason = ""
            fingerprint = {}
        elif isinstance(entry, dict):
            indices = entry.get("indices")
            relation = str(entry.get("relation") or ("merge" if isinstance(indices, list) and len(indices) > 1 else "single"))
            reason = str(entry.get("reason") or "")
            fingerprint = entry.get("fingerprint") if isinstance(entry.get("fingerprint"), dict) else {}
        else:
            continue
        if not isinstance(indices, list):
            continue
        valid = [i for i in indices if isinstance(i, int) and 0 <= i < n and i not in seen]
        if not valid:
            continue
        cleaned.append({
            "indices": valid,
            "relation": relation if relation in {"duplicate", "merge", "single"} else ("merge" if len(valid) > 1 else "single"),
            "reason": reason[:300],
            "fingerprint": fingerprint,
        })
        seen.update(valid)
    for i in range(n):
        if i not in seen:
            cleaned.append({"indices": [i], "relation": "single", "reason": "", "fingerprint": {}})
    return cleaned


def _dedup_articles_impl(articles: list[dict], scores: dict[str, float], *,
                         prompt: str, label: str, stage: str,
                         client=None) -> tuple[list[dict], list[dict]]:
    # ``client`` 는 오프라인 replay 전용 이음매다. 이 모듈은 위에서
    # ``from gemini_client import call_json`` 으로 **이름을 복사해** 들고 있어서,
    # ``gemini_client.call_json`` 을 갈아 끼워도 여기에는 닿지 않는다 — replay 라고
    # 믿으면서 실제 API 를 부르게 된다. 기본값은 예전과 같은 동작이다.
    if len(articles) < 2:
        return list(articles), []
    if not is_available():
        print(f"[dedup] GEMINI_API_KEY 없음 → {stage} dedup 건너뜀")
        return list(articles), []

    payload = "\n\n---\n\n".join(_article_block(i, a) for i, a in enumerate(articles))
    try:
        # label은 테스트/진단에서 자유롭게 바뀔 수 있지만 task 의미는 stage가 정한다.
        result = _call_identity_review(
            prompt, payload,
            policy_name="dedup_final" if stage == "editorial_final" else "dedup",
            label=label, client=client, max_output_tokens=6144)
    except GeminiError as e:
        print(f"[dedup] Gemini {stage} 실패 → 전량 유지: {e}")
        _record_failure(stage, e)
        return list(articles), []

    groups = _parse_story_groups(result, len(articles))
    if not groups:
        print(f"[dedup] {stage} 응답 groups 없음 → 전량 유지: {str(result)[:120]}")
        return list(articles), []

    def score_of(i: int) -> float:
        return scores.get(articles[i].get("hash", ""), 0.0)

    kept: list[dict] = []
    dropped: list[dict] = []
    for group in groups:
        indices = group["indices"]
        relation = group.get("relation") or "merge"
        win = max(indices, key=score_of)
        winner = articles[win]

        # 모델의 묶음을 되돌리는 두 가지 거부권. 적용 범위가 서로 다르다.
        #
        # ① 사건 단계 충돌 — `duplicate` 에만. 단순 재전재라면서 단계가 갈리는 것은
        #    모순이므로 그런 조합만 되돌린다. `merge`(원인분석·수치보강)는 건드리지
        #    않는다: 그 판정은 제목 밖의 맥락을 읽어야 하고, 그건 이 모듈이 아니라
        #    모델이 더 잘한다.
        # ② 운영 콘솔의 사람 판정·학습된 판별축 — relation 을 가리지 않는다.
        #    ①의 유보는 '제목만 보고 판단한다'는 약점에서 오는데, 사람은 두 기사를
        #    다 읽고 판정했으므로 그 유보가 필요 없다.
        vetoed: list[int] = []
        veto_records: list[dict] = []
        if len(indices) > 1:
            win_stages = event_stage.article_stages(winner)
            for i in list(indices):
                if i == win:
                    continue
                if relation == "duplicate" and event_stage.stage_conflict(
                        win_stages, event_stage.article_stages(articles[i])):
                    vetoed.append(i)
                    veto_records.append(
                        event_stage.veto_record(winner, articles[i], stage=stage))
                    continue
                # 사람 판정은 relation 을 가리지 않는다. 단계 거부권이 duplicate 로
                # 좁혀져 있는 것은 "재전재라면서 단계가 넘어가는 건 모순"이라는
                # 좁은 근거에 기대기 때문인데, 관리자가 직접 갈라 둔 조합에는 그
                # 유보가 필요 없다 — 전문가가 두 기사를 보고 다른 사건이라고 말한
                # 것을 모델의 merge 판정이 덮으면 검토 자체가 무의미해진다.
                admin_veto = admin_overrides.merge_blocked(winner, articles[i])
                if admin_veto:
                    vetoed.append(i)
                    veto_records.append({**admin_veto, "stage": stage})
            if vetoed:
                indices = [i for i in indices if i not in vetoed]
                winner.setdefault("story_stage_vetoes", []).extend(veto_records)

        members = [articles[i] for i in indices]
        consolidate_story_metadata(
            winner, members,
            relation=relation,
            reason=group.get("reason") or "",
            fingerprint=group.get("fingerprint") or {},
            stage=stage,
        )
        kept.append(winner)
        for i in indices:
            if i == win:
                continue
            d = dict(articles[i])
            d["dup_of"] = winner.get("hash", "")
            d["dup_reason"] = relation
            d["dup_explanation"] = group.get("reason") or ""
            dropped.append(d)
        # 거부권으로 떨어져 나온 기사는 버리지 않고 독립 story 로 되돌린다.
        for i in vetoed:
            kept.append(articles[i])
    return kept, dropped


def dedup_articles(articles: list[dict], scores: dict[str, float],
                   client=None) -> tuple[list[dict], list[dict]]:
    """기사 근거를 읽어 동일 briefing story를 묶는다.

    기존의 '제목 + 매체' 비교보다 넓은 개념이다. 동일 사건의 원인분석/수치보강은 하나의
    story로 합치되, 새로운 승인·계약·재가동 같은 독립 action은 후속 기사로 유지한다.
    """
    return _dedup_articles_impl(
        articles, scores, prompt=ARTICLE_STORY_PROMPT,
        label="dedup", stage="semantic_story", client=client,
    )


def editorial_dedup_articles(articles: list[dict], scores: dict[str, float],
                             client=None) -> tuple[list[dict], list[dict]]:
    """최종 후보에 대한 2차 편집 중복검사.

    1차 story clustering이 놓친 경우를 최종 출력 전에 다시 잡는다. 결과는 제거로 끝내지
    않고 ranking이 남은 후보에서 다시 채우므로 Daily News식 '중복이 자리를 먹는' 문제를
    만들지 않는다.
    """
    return _dedup_articles_impl(
        articles, scores, prompt=EDITORIAL_REDUNDANCY_PROMPT,
        label="dedup_final", stage="editorial_final", client=client,
    )


# ---- 연속일 반복: 이미 보낸 것과 의미로 대조 ---------------------------------------
#
# 왜 필요한가 (2026-09-25 이탈리아 원전 복귀법)
#   9/24 조선비즈 "원전 부활법 통과" 가 나갔고, 9/25 World Nuclear News "상원, 원자력
#   발전 복귀 법안 가결" 이 해외 1위로 또 나갔다. 같은 사건이다. 연속일 게이트
#   (issue_continuity.same_issue)의 다섯 길이 전부 못 넘었다 — 제목 0.333, 지문
#   0.532(actors 'Italian Senate' ≠ 'Italian Parliament'), 이름 1개, 근거 1/5,
#   story_id 서로 다름. 매체가 바뀌면 표현·호칭이 바뀌고, 어휘로 재는 길은 **정의상**
#   그걸 못 넘는다. 최근 3주에 같은 모양이 넷 더 있었다(한미일 SMR 9/23·9/24,
#   EIB 핀란드 SMR, 중국 GNSI).
#
#   같은 날 안에서는 이미 모델이 의미로 묶는다(ARTICLE_STORY_PROMPT) — 9/25 에도
#   그 기사를 전날 연합 기사와 '동일 사건 보도'로 묶었다. 빠진 것은 **어제 보낸
#   것을 입력에 넣는 일**뿐이었다. 사이트 원장(issue_ledger)은 같은 사건임을
#   알았지만 브리핑 뒤에만 쓰이므로 선정 시점에는 새 기사가 없다.
#
# 삭제 조건은 두 열쇠다: 모델이 '같은 사건 + 새 전개 없음'이라 하고, 어휘 기반
# 진전 판정(issue_continuity.progression)도 material 이 아니어야 한다. 새울 3호기
# (승인 → 배관 누설 정지 → 사건조사 착수)처럼 같은 시설의 진짜 후속을 한쪽
# 판정만으로 지우지 않기 위해서다.
CROSS_DAY_PROMPT = """당신은 원자력 아침 브리핑의 편집 데스크입니다.

입력은 오늘 보낼 후보(CANDIDATE)들이고, 후보마다 최근 며칠 안에 이미 독자에게 보낸 항목 중
비슷해 보이는 것 몇 개(SENT A, B, C)가 붙어 있습니다. 비슷해 보인다는 것은 낱말이 겹친다는
뜻일 뿐, 같은 사건이라는 뜻이 아닙니다.

후보마다 SENT 중 **같은 사건**이 있는지 보고, 있으면 둘의 관계를 고르세요.

⚠️ 출력은 JSON 하나만. 각 verdict 는 **이 순서로** 채웁니다 — 짝지은 SENT 제목을 그대로
옮겨 적고, 이유를 쓴 뒤, 마지막에 기호와 관계를 적습니다.
{"verdicts": [
  {"candidate": 0, "matched_sent_title": "SENT 제목을 그대로", "reason": "...",
   "match": "A", "relation": "same_detail"},
  {"candidate": 1, "matched_sent_title": "", "reason": "...", "match": null, "relation": "different"}
]}

같은 사건의 기준 — **같은 주체가 한 같은 행동**:
- 같은 국가·기관/기업·시설/정책의 같은 결정·사고·계약·발표·표결이면 같은 사건이다.
- 매체, 언어, 제목 표현, 주체 호칭('상원'과 '의회', 'Senate'와 'Parliament')이 달라도 같다.
- 주체가 다르면 다른 사건이다. 한 사건에 대한 다른 주체의 반응·요구·건의도 다른 사건이다.
- 같은 이슈·주제·협상 흐름(SMR, 데이터센터, 대미 투자 협상)에 속한다는 것만으로는 같은
  사건이 아니다. 같은 행사에서 나온 별개의 합의·발표도 서로 다른 사건이다.
- 확신이 없으면 "different".

relation 은 다섯 중 하나:
- "different": 같은 사건인 SENT 가 없다 (match 는 null).
- "same_restated": 같은 사건을 다른 매체·언어·표현으로 다시 보도, 요약·재정리.
- "same_detail": 같은 사건에 세부 사항만 더한 것 — 표결 수, 날짜, 발언 인용, 원인 설명,
  배경 수치, 목표 일정. 예: 'X 법안 통과' 다음 날 'X 법안 찬성 81표로 가결'.
- "same_reaction": 같은 사건을 두고 쓴 분석·전망·해설.
- "next_step": SENT 가 보도한 일 **뒤에 새로 일어난** 행동·결정·단계 전환. 예: 심사→승인,
  상원 통과→하원 통과, 협상→잠정 합의→서명, 협약→구속력 있는 계약, 정지→재가동,
  발표→착공, 사고→조사 착수, 제안→합의·채택, 방문·회담 예정→실제 개최,
  새 당사자 합류, 증액·축소·취소·연기 결정.
"""

# 지울 수 있는 관계 — 같은 사건을 다시 말한 것뿐인 둘. 분석·해설(same_reaction)은
# 남긴다: replay 에서 이 칸이 '같은 주제의 다른 뉴스'를 가장 많이 빨아들였다.
CROSS_DAY_DROP_RELATIONS = frozenset({"same_restated", "same_detail"})
CROSS_DAY_RELATIONS = CROSS_DAY_DROP_RELATIONS | {"same_reaction", "next_step", "different"}
CROSS_DAY_SENT_DAYS = 3
CROSS_DAY_SUSPECTS = 3
_SUSPECT_LABELS = "ABCDEFGH"


def recent_for_cross_day(sent: list[dict], today: str,
                         days: int = CROSS_DAY_SENT_DAYS) -> list[dict]:
    """대조할 발송분 — 최근 `days` 일(오늘 다른 지역에 이미 자리 잡은 것 포함)."""
    from datetime import date, timedelta
    try:
        cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    except ValueError:
        return []
    rows = [r for r in sent if isinstance(r, dict) and cutoff <= str(r.get("date") or "") <= today]
    seen: set[str] = set()
    unique: list[dict] = []
    for row in sorted(rows, key=lambda r: str(r.get("date") or ""), reverse=True):
        key = str(row.get("hash") or row.get("title_kr") or "")
        if key and key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _suspects(candidate: dict, sent: list[dict], generic: frozenset[str],
              limit: int = CROSS_DAY_SUSPECTS) -> list[dict]:
    """후보마다 대조할 발송분을 어휘로 몇 개만 추린다.

    모델에게 후보 19건 × 발송분 50건을 한 번에 맞추게 하면 번호를 헛짚고 주제로
    뭉친다(replay: '러시아 우라늄 수출 제한' ≈ '한수원-오라노 협약'). 어휘는 '같은
    사건'을 못 가려도 '비슷해 보이는 것'을 고르는 데는 충분하다 — 9/25 이탈리아의
    진짜 짝은 이 점수로 1위였다(제목 0.333 + 이름 '이탈리아').
    """
    import issue_continuity
    own = issue_continuity.named_anchors(candidate)
    scored: list[tuple[float, int]] = []
    for index, row in enumerate(sent):
        sim = issue_continuity.title_similarity(candidate, row)
        shared = len((own & issue_continuity.named_anchors(row)) - generic)
        if sim >= 0.25 or shared >= 1:
            scored.append((sim + 0.15 * shared, index))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [sent[index] for _score, index in scored[:limit]]


def _candidate_block(idx: int, article: dict, suspects: list[dict]) -> str:
    lines = [
        f"[CANDIDATE {idx}]",
        f"TITLE: {_trim(article.get('title_kr') or article.get('title'), 200)}",
        f"TITLE_ORIGINAL: {_trim(article.get('title'), 200)}",
        f"SUMMARY: {_trim(article.get('summary'), 500)}",
        f"DETAIL: {_trim(article.get('detail'), 500)}",
    ]
    for label, row in zip(_SUSPECT_LABELS, suspects):
        lines += [
            f"  (SENT {label}) date={_trim(row.get('date'), 10)}",
            f"  TITLE: {_trim(row.get('title_kr') or row.get('title'), 200)}",
            f"  SUMMARY: {_trim(row.get('summary'), 350)}",
        ]
    return "\n".join(lines)


def _title_overlap(left: str, right: str) -> float:
    """글자 2-gram 겹침(짧은 쪽 기준). 모델이 옮겨 적은 제목과 원본을 맞춰 본다."""
    def grams(text: str) -> set[str]:
        compact = re.sub(r"\s+", "", text or "")
        return {compact[i:i + 2] for i in range(max(0, len(compact) - 1))}
    a, b = grams(left), grams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _resolve_suspect(match: object, echoed_title: str, suspects: list[dict],
                     threshold: float = 0.6) -> dict | None:
    """모델이 고른 기호를 **옮겨 적은 제목으로 검증**한다. 어긋나면 제목으로 다시 찾는다."""
    def title_of(row: dict) -> str:
        return str(row.get("title_kr") or row.get("title") or "")

    if not echoed_title:
        return None
    if isinstance(match, str) and len(match) == 1 and match in _SUSPECT_LABELS:
        index = _SUSPECT_LABELS.index(match)
        if index < len(suspects) and _title_overlap(
                echoed_title, title_of(suspects[index])) >= threshold:
            return suspects[index]
    best, best_score = None, 0.0
    for row in suspects:
        score = _title_overlap(echoed_title, title_of(row))
        if score > best_score:
            best, best_score = row, score
    return best if best_score >= threshold else None


CROSS_DAY_CONFIRM_PROMPT = """당신은 원자력 브리핑의 사실 확인 담당입니다.

각 PAIR 는 이미 보낸 기사(SENT)와 오늘 후보(CANDIDATE)입니다. 편집자는 둘이 같은 사건이라
후보를 빼려고 합니다. 빼도 되는지 한 가지만 확인하세요:

**CANDIDATE 가 SENT 이후에 새로 일어난 행동·결정·일정 변화를 보도하는가?**
- 새로 일어난 일의 예: 회담·행사가 실제로 열림, 합의·서명·체결, 승인·허가, 표결 통과,
  연기·취소·중단, 착공·가동·정지, 조사 착수, 새 당사자의 결정.
- SENT 가 '예정'·'추진'·'논의'라고 한 일이 CANDIDATE 에서 실제로 일어났거나 미뤄졌으면
  새로 일어난 일이다.
- 같은 사건의 표결 수·수치·발언·원인·배경·전망을 더 자세히 적은 것은 새로 일어난 일이 아니다.
- 다른 사건이면 new_action=true 로 답한다(빼면 안 된다).

⚠️ 출력은 JSON 하나만:
{"pairs": [{"pair": 0, "new_facts": ["CANDIDATE 에만 있는 사실을 짧게"], "new_action": false}]}
new_facts 를 먼저 적고, 그 가운데 새로 일어난 행동·결정·일정 변화가 하나라도 있으면
new_action=true 입니다.
"""


def _confirm_no_new_action(proposals: list[tuple[dict, dict, dict]], *,
                           label: str, client=None) -> None:
    """지우자는 판정을 좁은 질문으로 한 번 더 확인한다(제자리에서 drop 을 끈다).

    replay 에서 첫 판정이 가장 자주 틀린 모양은 '예정 → 실제 개최·연기'였다
    (방문 예정 → 정상회담 개최, 합의문 발표 예정 → MOU 서명 연기). 관계 다섯 중
    하나를 고르는 질문보다 '새로 일어난 일이 있나'를 묻는 질문이 이 모양을 더 잘
    본다. 확인을 못 하면 지우지 않는다 — 판정 불가는 fail-open 이다.
    """
    if not proposals:
        return
    payload = "\n\n".join(
        "\n".join([
            f"[PAIR {i}]",
            f"SENT date={_trim(prior.get('date'), 10)}",
            f"  TITLE: {_trim(prior.get('title_kr') or prior.get('title'), 200)}",
            f"  SUMMARY: {_trim(prior.get('summary'), 400)}",
            "CANDIDATE",
            f"  TITLE: {_trim(cand.get('title_kr') or cand.get('title'), 200)}",
            f"  SUMMARY: {_trim(cand.get('summary'), 500)}",
            f"  DETAIL: {_trim(cand.get('detail'), 500)}",
        ])
        for i, (_verdict, cand, prior) in enumerate(proposals))
    try:
        result = _call_identity_review(
            CROSS_DAY_CONFIRM_PROMPT, payload, policy_name="dedup_cross_day",
            label=f"{label}_confirm", client=client, max_output_tokens=2048)
    except GeminiError as exc:
        print(f"[dedup] cross_day 확인 실패 → 지우지 않음: {exc}")
        _record_failure("cross_day_confirm", exc)
        for verdict, _cand, _prior in proposals:
            verdict["drop"] = False
            verdict["confirm"] = "failed"
        return
    answers: dict[int, dict] = {}
    raw = result.get("pairs") if isinstance(result, dict) else None
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("pair"), int):
            answers.setdefault(entry["pair"], entry)
    for i, (verdict, _cand, _prior) in enumerate(proposals):
        answer = answers.get(i)
        # 답이 없거나 new_action 이 불리언 false 가 아니면 지우지 않는다.
        confirmed = isinstance(answer, dict) and answer.get("new_action") is False
        verdict["confirm"] = "no_new_action" if confirmed else "new_action_or_unknown"
        if isinstance(answer, dict):
            facts = answer.get("new_facts")
            verdict["new_facts"] = [_trim(f, 80) for f in facts[:3]] if isinstance(facts, list) else []
        if not confirmed:
            verdict["drop"] = False


def cross_day_repeats(candidates: list[dict], sent: list[dict], *,
                      label: str = "dedup_cross_day", client=None) -> list[dict]:
    """이미 보낸 것과 같은 사건이고 새 전개가 없는 후보를 **판정만** 한다.

    지우지 않는다. 걸린 후보의 `continuity` 에 drop 판정을 싣고 판정 목록을
    돌려준다 — 제거와 빈자리 보충은 ranking 이 기존 연속일 경로로 한다.
    실패하면 아무것도 안 건드리고 LLM_FAILURES 에 남긴다.
    """
    import issue_continuity

    if not candidates or not sent or not is_available():
        return []
    generic = issue_continuity.generic_anchors(list(candidates) + list(sent))
    pairs = [(i, cand, _suspects(cand, sent, generic)) for i, cand in enumerate(candidates)]
    pairs = [(i, cand, sus) for i, cand, sus in pairs if sus]
    if not pairs:
        return []
    payload = "\n\n".join(_candidate_block(i, cand, sus) for i, cand, sus in pairs)
    try:
        result = _call_identity_review(
            CROSS_DAY_PROMPT, payload, policy_name="dedup_cross_day",
            label=label, client=client, max_output_tokens=4096)
    except GeminiError as exc:
        print(f"[dedup] cross_day 실패 → 연속일 의미 대조 없이 진행: {exc}")
        _record_failure("cross_day", exc)
        return []

    by_index = {i: (cand, sus) for i, cand, sus in pairs}
    verdicts: list[dict] = []
    proposals: list[tuple[dict, dict, dict]] = []
    decided: set[int] = set()
    raw = result.get("verdicts") if isinstance(result, dict) else None
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        ci = entry.get("candidate")
        relation = str(entry.get("relation") or "")
        if ci not in by_index or ci in decided:
            continue
        if relation not in CROSS_DAY_RELATIONS or relation == "different":
            continue
        cand, suspects = by_index[ci]
        prior = _resolve_suspect(entry.get("match"),
                                 str(entry.get("matched_sent_title") or ""), suspects)
        if prior is None:
            continue
        decided.add(ci)
        prog = issue_continuity.progression(prior, cand)
        drop = (relation in CROSS_DAY_DROP_RELATIONS
                and prog.get("verdict") != "material")
        verdict = {
            "hash": cand.get("hash", ""),
            "title": _trim(cand.get("title_kr") or cand.get("title"), 80),
            "prior_hash": prior.get("hash", ""),
            "prior_title": _trim(prior.get("title_kr") or prior.get("title"), 80),
            "prior_date": prior.get("date", ""),
            "relation": relation,
            "progression": prog.get("verdict"),
            "reason": _trim(entry.get("reason"), 200),
            "drop": drop,
        }
        verdicts.append(verdict)
        if drop:
            proposals.append((verdict, cand, prior))

    # 두 번째 열쇠 — 지우자고 한 쌍만, 좁은 질문 하나로 다시 묻는다.
    _confirm_no_new_action(proposals, label=label, client=client)
    for verdict, cand, _prior in proposals:
        if verdict["drop"]:
            relation = verdict["relation"]
            cont = dict(cand.get("continuity") or {})
            cont.update({
                "matched": True, "drop": True,
                "prior_hash": verdict["prior_hash"], "prior_title": verdict["prior_title"],
                "prior_date": verdict["prior_date"],
                "identity_confirmed": True, "identity_method": "llm_cross_day",
                "progression": "none",
                "match_reasons": (list(cont.get("match_reasons") or [])
                                  + [f"llm_cross_day:{relation}:{verdict['reason'][:80]}"]),
            })
            cand["continuity"] = cont
    return verdicts


# ---- CLI 자가진단 ----------------------------------------------------------

if __name__ == "__main__":
    # 샘플 데이터로 dedup 동작 확인
    samples: list[tuple[str, dict]] = [
        ("SMR 동향", {"title": "Microsoft signs PPA with Constellation for Three Mile Island restart",
                      "url": "https://example.com/tmi-microsoft", "score": 50, "boosted_score": 75, "meta": "r/nuclear"}),
        ("재가동 트렌드", {"title": "TMI restart deal: Microsoft × Constellation 20-year PPA",
                          "url": "https://other.com/tmi-deal?utm_source=x", "score": 40, "boosted_score": 55, "meta": "@MarkNelson"}),
        ("SMR 동향", {"title": "NuScale Romania VOYGR project advances to FEED",
                      "url": "https://example.com/nuscale-romania", "score": 30, "boosted_score": 45, "meta": ""}),
        ("AI-원전 빅테크 거래", {"title": "Hyperscalers race for nuclear: Amazon, Microsoft, Google compared",
                                  "url": "https://news.com/hyperscaler-nuclear", "score": 25, "boosted_score": 40, "meta": ""}),
    ]
    kept, dropped = dedup_clusters(samples)
    print(f"\n=== KEPT ({len(kept)}) ===")
    for t, c in kept:
        print(f"  [{t}] {c['title'][:80]}")
    print(f"\n=== DROPPED ({len(dropped)}) ===")
    for t, c, kept_t, why in dropped:
        print(f"  [{t}] {c['title'][:60]} → merged into [{kept_t}] ({why})")
