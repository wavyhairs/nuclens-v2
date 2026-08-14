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
                         prompt: str, label: str, stage: str) -> tuple[list[dict], list[dict]]:
    if len(articles) < 2:
        return list(articles), []
    if not is_available():
        print(f"[dedup] GEMINI_API_KEY 없음 → {stage} dedup 건너뜀")
        return list(articles), []

    payload = "\n\n---\n\n".join(_article_block(i, a) for i, a in enumerate(articles))
    try:
        result = call_json(prompt, payload, temperature=0.05, max_output_tokens=6144,
                           timeout=120.0, label=label)
    except GeminiError as e:
        print(f"[dedup] Gemini {stage} 실패 → 전량 유지: {e}")
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
        win = max(indices, key=score_of)
        winner = articles[win]
        members = [articles[i] for i in indices]
        consolidate_story_metadata(
            winner, members,
            relation=group.get("relation") or "merge",
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
            d["dup_reason"] = group.get("relation") or "merge"
            d["dup_explanation"] = group.get("reason") or ""
            dropped.append(d)
    return kept, dropped


def dedup_articles(articles: list[dict],
                   scores: dict[str, float]) -> tuple[list[dict], list[dict]]:
    """기사 근거를 읽어 동일 briefing story를 묶는다.

    기존의 '제목 + 매체' 비교보다 넓은 개념이다. 동일 사건의 원인분석/수치보강은 하나의
    story로 합치되, 새로운 승인·계약·재가동 같은 독립 action은 후속 기사로 유지한다.
    """
    return _dedup_articles_impl(
        articles, scores, prompt=ARTICLE_STORY_PROMPT,
        label="dedup", stage="semantic_story",
    )


def editorial_dedup_articles(articles: list[dict],
                              scores: dict[str, float]) -> tuple[list[dict], list[dict]]:
    """최종 후보에 대한 2차 편집 중복검사.

    1차 story clustering이 놓친 경우를 최종 출력 전에 다시 잡는다. 결과는 제거로 끝내지
    않고 ranking이 남은 후보에서 다시 채우므로 Daily News식 '중복이 자리를 먹는' 문제를
    만들지 않는다.
    """
    return _dedup_articles_impl(
        articles, scores, prompt=EDITORIAL_REDUNDANCY_PROMPT,
        label="dedup_final", stage="editorial_final",
    )


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
