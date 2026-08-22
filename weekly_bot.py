"""
주간 판세 리포트 — 일일 브리핑(개별 사건 카드)의 상위 레이어.

역할 재정의 (2026-07):
    일일 브리핑이 '카드'라면 주간은 '판세'. 기사 재나열을 최소화하고
    ① 정책 변화 ② 투자 테마 강약 ③ 한국/한수원 직접 영향 ④ 다음 주 watchlist
    ⑤ 보고서 검토 후보 ⑥ 소스 coverage gap 을 종합한다.
    집계(섹션·테마·이벤트 유형·소스 커버리지)는 Python 이 계산해 프롬프트에 제공,
    LLM 은 그 위에서 서사만 쓴다. Gemini 호출은 기존과 동일하게 주 1회 1번.

2026-07 버그 수정:
    curated 스키마가 importance(등급)/category(정책·기술·시장·규제)로 분리된 뒤에도
    옛 필드(category)에서 등급을 찾고 있어 매주 0건 → 리포트가 조용히 스킵되던 회귀.
    이제 importance 우선, 옛 스키마(category 에 등급)도 하위 호환.

2026-08 분석 단위 정정:
    주간의 단위는 기사가 아니라 **사건**이다. 기사 1000건을 그대로 넣으면 같은
    발표의 반복 보도가 입력을 채워 다른 축을 밀어내고(실측: theme_moves 1~2개),
    근거 대조도 기사 하나로 좁혀져 여러 매체가 나눠 쓴 사실이 '근거 없음'이 됐다
    (실측: 같은 주 6개 기사에 있는 '원전 19기 분량'이 삭제).
    이제 `ranking.cluster_duplicates` 로 사건을 묶어 입력·근거 계약·이슈 수를
    같은 단위로 맞춘다. 대신 강약 판단에는 **서로 다른 사건 2건 이상**이라는
    요건이 새로 붙는다 — 반복 보도가 '강화'가 되지 않도록.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from ranking import cluster_duplicates
import article_quality_gate
import news_archive
import weekly_sections

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).parent
CURATED_CACHE_FILE = ROOT / "curated.json"
SOURCES_FILE = ROOT / "sources.json"
DELIVERY_LOG_FILE = ROOT / "delivery_log.jsonl"
WEEK_DAYS = 7

_GRADES = {"must_read", "nice_to_know", "market", "noise"}
SECTION_KR = {"smr": "SMR", "khnp": "한수원", "domestic": "국내 정책", "international": "해외"}

WEEKLY_PROMPT = """당신은 한국수력원자력 전략경영단 정책개발부의 시니어 정책분석관입니다.
지난 7일의 **사건 목록**과 시스템 집계를 받아 의사결정자용 **주간 판세 보고**를 씁니다.
같은 발표를 여러 매체가 쓴 것은 시스템이 이미 한 사건으로 묶어 두었습니다.

기사 요약의 나열이 아닙니다. 다음에 답하십시오.
① 이번 한 주에 무엇이 바뀌었는가 ② 어떤 주제가 강해지거나 약해졌는가
③ 실제로 발생한 정책 변화는 무엇인가 ④ 한국·한수원에 직접 영향을 줄 흐름은 무엇인가
⑤ 다음 주에 무엇을 확인해야 하는가

[출력 형식] - 반드시 JSON 한 객체만. 다른 텍스트·펜스 금지. 문자열 값 안 줄바꿈 금지.
모든 문장은 격식체(~다)로 끝낸다. 존댓말(~습니다) 금지.
{
  "weekly_intro": "이번 주 핵심 흐름 3~4문장 (400자 이내, 분석관 보고 톤)",
  "policy_shifts": [{"what": "정책 변화 1문장", "so_what": "함의 1문장", "evidence_hashes": ["hash8"]}],
  "theme_moves": [{"theme": "주제명", "direction": "강화|약화|유지", "why": "근거 1문장", "evidence_hashes": ["hash8"]}],
  "khnp_direct": "한국·한수원 직접 영향 종합 1~3문장 (없으면 빈 문자열)",
  "watchpoints": ["다음 주 모니터링 포인트 (각 1문장, 3~5개)"],
  "report_candidates": [{"topic": "보고서 주제", "basis": "누적 근거 1문장"}],
  "key_events": [{"hash": "...", "headline": "기사 원문 제목 그대로", "implication": "1문장"}]
}

[판세 판단]
- policy_shifts 2~4개, report_candidates 0~3개.
- theme_moves 는 **개수를 정해 두지 않는다(0~5개)**. 근거가 되는 독립 사건이
  있는 축은 빠뜨리지 말고, 없는 축은 만들지 마라. 넷을 채우려고 근거가 얇은
  축을 넣는 것과, 세 개만 쓰고 근거가 뚜렷한 축을 버리는 것은 똑같이 틀렸다.
- theme_moves 의 direction 은 이번 주에 **새로 발생한 독립 사건**으로 정한다.
  · 강화 — 서로 다른 사건이 같은 방향으로 여러 건 (정책 결정·계약·투자·인허가 진전 등)
  · 약화 — 지연·취소·반대·규제 강화 같은 후퇴 사건이 여러 건
  · 유지 — 사건은 여러 건 있으나 방향이 한쪽으로 기울지 않았다
- **'유지'는 정상적인 결과다.** 판을 바꾸지 않았을 뿐 그 주에도 계속 움직인
  구조적 축은 '유지'로 남겨라. 방향이 센 축만 쓰면, 매주 조금씩 진행되는
  계속운전·방폐·규제 같은 축이 화면에서 통째로 사라진다. 다만 이번 주 사건이
  없는 주제를 '유지'로 채우지는 마라.
- **같은 사건의 반복 보도는 근거 1건이다.** 보도 매체가 많다는 것은 강화가 아니다.
- 한 사건을 테마 두 개로 쪼개지 말 것. 두 테마가 같은 사건만 지목하고 있다면
  하나로 합쳐라 — 표현만 다른 축은 축이 아니다.
- 반대로 **정책 질문이 다르면 다른 축이다.** 특히 아래 셋은 서로 겹쳐 보이지만
  각자 독립 사건이 있으면 따로 써라.
  · AI·데이터센터 — 왜, 얼마나 수요가 느는가 (입지·전력계약·수요 전망 상향)
  · 전력망 — 그 수요를 어디로 어떻게 보내는가 (송전망·계통접속·입지 갈등)
  · 전력수요 — 수급계획 자체의 수치·구성이 어떻게 바뀌는가
- policy_shifts 와 theme_moves 는 **다른 질문에 답한다.** 앞은 "이번 주에 무슨
  일이 있었는가", 뒤는 "그 축이 어디로 기울었는가"다. 어떤 사건을 policy_shifts
  에 썼다는 이유로 그 축을 theme_moves 에서 빼지 마라 — 규제·법 개정이 여러 건
  있었다면 그것은 '규제' 축의 방향을 말해 주는 근거다.
- [주제별 후보 축]은 어떤 축이 후보인지와 그 축의 사건 몇 건을 보여 준다.
  **핵심진전이 2 이상인 축은 전부 한 번씩 검토하라.** 검토한 뒤 방향을 말할
  근거가 없으면 빼는 것이 맞고, 방향이 한쪽으로 기울지 않았을 뿐이면 '유지'다.
  목록을 그대로 옮기지 말고 사건을 읽고 방향을 직접 판단하라.
  핵심진전이 0 인 주제를 '강화'라고 쓰지 마라.
- theme 은 우라늄/SMR/수출/계속운전/핵연료/방폐/규제/공급망/신규건설/전력수요/
  전력망/AI·데이터센터/원전운영 등 주제어로. 이번 주 사건이 실제로 그 주제인 것만.

[근거 규칙]
- **evidence_hashes**: 입력 목록에 **실제로 있는 hash 만**. 지어내지 말 것.
- policy_shifts — 그 문장의 **수치·기관·날짜가 확인되는 사건을 전부** 지목한다(최대 4).
  수치를 말하면서 그 수치가 있는 사건을 지목하지 않으면 그 항목은 버려진다.
- theme_moves — 강화·약화는 **서로 다른 사건 2건 이상**을 지목해야 한다(최대 5).
  지목한 사건이 그 주제와 무관하면 그 항목은 버려진다.
- key_events 는 **최대 5건** — 주간 판세를 대표하는 사건만. 일일 브리핑 재탕 금지.
- khnp_direct 는 [한수원이 직접 당사자인 사건] 목록을 근거로 쓴다. 그 목록에
  없는 일을 한수원 이야기로 옮기지 마라 — 국내 기업의 해외 SMR 참여는 한수원의
  사업기회가 **아니다**. 산업 차원의 간접 영향까지 말해야 한다면 직접 영향을
  먼저 쓰고, 간접이라는 것을 문장에서 밝혀라.
- 원문·집계에 없는 정보 추가 금지 (환각 금지). 격식체(~다) 분석관 톤."""

# 판세 문장 하나가 지목할 수 있는 근거 사건 수. 한 문장이 여러 독립 사건을
# 종합하는 것이 주간의 일이므로 2건 상한은 근거를 잘라 내는 쪽으로 작용했다
# (실측: '원전 19기 분량' 정책 변화가 같은 주 6개 기사에 있는데도, LLM 이 지목한
# 2건에 그 수치가 없어 삭제됐다).
WEEKLY_EVIDENCE_LIMIT = 5
# 강화·약화는 서로 다른 사건 2건 이상이 있어야 한다. 같은 발표의 반복 보도는
# 사건 1건이므로 여기서 걸린다.
THEME_MOVE_MIN_STORIES = 2


def load_curated() -> dict:
    if CURATED_CACHE_FILE.exists():
        try:
            return json.loads(CURATED_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _grade(data: dict) -> str:
    """등급 추출 — 현행 스키마는 importance, 옛 스키마는 category 에 등급이 있었음."""
    imp = data.get("importance")
    if imp in _GRADES:
        return imp
    cat = data.get("category")
    if cat in _GRADES:
        return cat
    return "nice_to_know"


def parse_moment(value: object) -> datetime | None:
    """curated 의 시각 문자열 → aware datetime.

    published_at 은 KST 오프셋(+09:00), cached_at 은 UTC 로 저장된다. 문자열로
    비교하면 두 표기가 섞여 경계가 어긋나므로 실제로 해석해서 비교한다.
    오프셋이 없는 옛 값은 KST 로 본다 (수집기가 KST 로 돌았다).
    """
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)


def week_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """리포트가 말하는 구간 — KST 달력으로 `WEEK_DAYS` 일(양끝 포함).

    라벨과 수집 구간이 어긋나 있었다: 저장본은 KST 로 `now-6일 ~ now` 라고
    적으면서 기사는 UTC 문자열로 `now-7일` 이후를 담아, 화면의 '8/15~8/21'
    안에 8/14 저녁 기사가 섞였다. 판세도 코너도 이 창 하나만 쓴다 — 한 화면에
    두 기간이 섞이면 독자가 읽은 '이번 주'가 어느 주인지 알 수 없다.
    """
    now = (now or datetime.now(KST)).astimezone(KST)
    first = (now - timedelta(days=WEEK_DAYS - 1)).date()
    return datetime.combine(first, time.min, tzinfo=KST), now


def get_week_articles(curated: dict, now: datetime | None = None) -> list[dict]:
    since, until = week_window(now)
    items: list[dict] = []
    for h, data in curated.items():
        if not isinstance(data, dict):
            continue
        # 재수집·재큐레이션 시각이 아니라 실제 발행 시각으로 주간 창을 자른다.
        # 옛 캐시에는 published_at이 없으므로 그 경우에만 cached_at으로 호환한다.
        moment = parse_moment(data.get("published_at") or data.get("cached_at"))
        if moment is None or not (since <= moment <= until):
            continue
        if _grade(data) not in ("must_read", "nice_to_know"):
            continue
        if not data.get("title") or not data.get("link"):
            continue
        # Daily에서 막은 미검증 fallback이 curated 캐시를 통해 주간 Telegram
        # 서사로 우회하지 못하게 한다. 옛 정상 스키마(unreviewed)는 호환하되,
        # 명시적/추론 fallback과 원제목이 다른 사건인 레코드는 제외한다.
        status = article_quality_gate.infer_curation_status(data)
        integrity = article_quality_gate.audit_article_integrity(
            data,
            source={"title": data.get("title", ""),
                    "published_at": data.get("published_at") or data.get("cached_at")},
            reference_date=data.get("published_at") or data.get("cached_at"),
        )
        if status in {"fallback", "quarantined"} or not integrity.eligible:
            continue
        data = integrity.value
        items.append({
            "hash": h,
            "title": data["title"],
            "title_kr": data.get("title_kr", ""),
            "link": data["link"],
            "domain": data.get("domain", ""),
            "feed": data.get("feed", ""),
            "section": data.get("section", ""),
            "grade": _grade(data),
            "summary": data.get("summary", ""),
            "tags": data.get("tags", []),
            "features": data.get("features"),
            "curation_status": status,
            "cached_at": data["cached_at"],
            "published_at": data.get("published_at", ""),
            # 아래는 결정적 코너(`weekly_sections`)가 쓰는 구조화 값이다.
            # 판세 문장의 근거 계약에는 들어가지 않는다 — detail 은
            # `weekly_contracts` 가 명시적으로 뺀다(검증 범위 유지).
            "detail": data.get("detail", ""),
            "topics": data.get("topics", []),
            "countries": data.get("countries", []),
            "event_date": data.get("event_date"),
            "event_date_type": data.get("event_date_type", "unknown"),
            "event_date_precision": data.get("event_date_precision", "unknown"),
        })
    items.sort(key=lambda x: x.get("published_at") or x["cached_at"])
    return items


# ---- Python 집계 (LLM 은 이 위에서 서사만) -------------------------------------

def build_aggregates(items: list[dict]) -> dict:
    sections = Counter(SECTION_KR.get(a.get("section"), a.get("section") or "기타")
                       for a in items)
    events = Counter()
    report_cands = []
    for a in items:
        f = a.get("features") or {}
        if isinstance(f, dict):
            et = f.get("event_type")
            if et:
                events[et] += 1
            try:
                rw = int(f.get("report_worthiness", 0))
            except (TypeError, ValueError):
                rw = 0
            if rw >= 2:
                report_cands.append((a.get("title_kr") or a.get("title", ""))[:80])
    tags = Counter(t for a in items for t in (a.get("tags") or []) if isinstance(t, str))
    return {
        "total": len(items),
        "must_read": sum(1 for a in items if a["grade"] == "must_read"),
        "sections": dict(sections.most_common()),
        "event_types": dict(events.most_common(6)),
        "top_tags": [t for t, _ in tags.most_common(8)],
        "report_candidates": report_cands[:5],
    }


def coverage_gaps(items: list[dict]) -> list[str]:
    """sources.json tier1 매체 중 이번 주 0건인 곳 — 소스 공백 표시 (LLM 안 씀)."""
    try:
        cfg = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    seen = " ".join((a.get("domain") or "").lower() for a in items)
    gaps = []
    for entry in cfg.get("tier1", []):
        dom = (entry.get("domain") or "").lower()
        if dom and dom not in seen:
            gaps.append(entry.get("name") or dom)
    return gaps


def followup_hits(items: list[dict]) -> list[str]:
    """지난주 watchpoint 사후 검증은 상태가 없어 불가 — 대신 이번 주 배송된 기사와
    겹치는 후속 흐름(동일 태그 3회 이상)을 반복 노출 신호로 표시."""
    tags = Counter(t for a in items for t in (a.get("tags") or []) if isinstance(t, str))
    return [f"{t} ({n}회)" for t, n in tags.most_common(5) if n >= 3]


# ---- 사건(story) 단위 — 같은 발표의 반복 보도를 하나로 ---------------------------
#
# 주간의 분석 단위는 기사가 아니라 **사건**이다. 같은 발표를 열 곳이 쓰면 입력의
# 열 줄을 차지하고 다른 사건을 뒤로 밀어내며, 그 자체로 '많이 보도됐으니 강한
# 흐름'이라는 착시를 만든다. 일일 브리핑이 이미 쓰는 `ranking.cluster_duplicates`
# 를 그대로 재사용한다 — 결정적이고 LLM 을 타지 않으므로 '주 1회 1호출' 계약이
# 그대로다. 새 clustering 을 만들지 않는다.


def _story_score(article: dict) -> float:
    """대표 기사 선정 점수 — 등급이 먼저, 보고서 가치가 동점을 가른다.

    `cluster_duplicates` 는 점수 내림차순으로 대표를 고른다. 빈 점수를 넘기면
    입력 순서(=발행 시각)가 대표를 정해, 같은 사건의 가장 얕은 첫 보도가
    대표가 되고 must_read 원문이 접힌다.
    """
    score = 2.0 if article.get("grade") == "must_read" else 1.0
    features = article.get("features")
    if isinstance(features, dict):
        try:
            score += min(max(int(features.get("report_worthiness", 0)), 0), 3) * 0.1
        except (TypeError, ValueError):
            pass
    return score


_STORY_CACHE: tuple[tuple, list[dict]] | None = None


def weekly_stories(items: list[dict]) -> list[dict]:
    """이번 주 기사를 사건 단위로 묶는다. 대표 + 접힌 기사 전부를 들고 있다.

    대표 하나만 남기지 않는다 — 접힌 기사의 수치·기관이 근거에서 빠지면
    "여러 매체가 나눠 쓴 사실"이 통째로 미검증이 된다. 근거 계약은 사건에 속한
    **모든** 기사에서 만든다.

    호출자(입력 구성·근거 계약·이슈 수 세기)가 셋이라 한 번 계산해 재사용한다 —
    같은 주 1000건 클러스터링이 5초쯤 걸린다.
    """
    global _STORY_CACHE
    # 지문에 제목까지 넣는다 — hash 만으로 재사용하면, 같은 hash 를 다른 제목으로
    # 부르는 호출(테스트 픽스처·재큐레이션)이 옛 묶음을 그대로 돌려받는다.
    signature = tuple((str(item.get("hash") or ""), str(item.get("title_kr") or ""),
                       str(item.get("title") or "")) for item in items)
    if _STORY_CACHE is not None and _STORY_CACHE[0] == signature:
        return _STORY_CACHE[1]

    by_hash = {str(item.get("hash") or ""): item for item in items}
    # 얕은 복사를 넘긴다 — `cluster_duplicates` 가 대표에 story_* 메타데이터를
    # 덧쓰기 때문에, 묶는 행위만으로 호출자의 항목이 오염되면 안 된다.
    copies = [dict(item) for item in items]
    scores = {str(item.get("hash") or ""): _story_score(item) for item in copies}
    kept, dropped = cluster_duplicates(copies, scores)

    members: dict[str, list[dict]] = {}
    for rep in kept:
        rep_hash = str(rep.get("hash") or "")
        if rep_hash in by_hash:
            members[rep_hash] = [by_hash[rep_hash]]
    for row in dropped:
        rep_hash = str(row.get("dup_of") or "")
        member = by_hash.get(str(row.get("hash") or ""))
        if member is not None and rep_hash in members:
            members[rep_hash].append(member)

    stories: list[dict] = []
    for rep in kept:
        rep_hash = str(rep.get("hash") or "")
        articles = members.get(rep_hash)
        if not articles:
            continue
        outlets = {(a.get("domain") or a.get("feed") or a.get("hash") or "").lower()
                   for a in articles}
        stories.append({
            "key": rep_hash[:8],
            "hash": rep_hash,
            "articles": articles,
            "outlets": len(outlets),
            "related": [(a.get("title_kr") or a.get("title") or "")
                        for a in articles[1:] if a.get("title_kr") or a.get("title")],
        })
    stories.sort(key=lambda s: (s["articles"][0].get("published_at")
                                or s["articles"][0].get("cached_at") or ""))
    _STORY_CACHE = (signature, stories)
    return stories


def story_lines(stories: list[dict]) -> list[str]:
    """LLM 입력 — 사건 한 줄. 접힌 보도는 개수와 제목으로 남긴다(정보 손실 금지)."""
    lines: list[str] = []
    for story in stories:
        rep = story["articles"][0]
        title = (rep.get("title_kr") or rep.get("title") or "")[:80]
        line = (f"hash:{story['key']} | [{rep.get('section', '')}/"
                f"{rep.get('grade', '')}] {title} | {rep.get('summary', '')[:60]}")
        if len(story["articles"]) > 1:
            line += (f" | 같은 사건 보도 {len(story['articles'])}건"
                     f"·매체 {story['outlets']}곳")
        lines.append(line)
        for extra in story["related"][:2]:
            lines.append(f"    · {extra[:70]}")
    return lines


# 프롬프트가 쓰라고 이름을 대는 주제어들. 기사는 주제를 그 이름 그대로 쓰지
# 않는다 — '신규건설'은 "신규 원전 건설"로, '수출'은 "체코 수주"로, '규제'는
# "원자력안전법 개정안"으로 나온다. 좁게 적으면 근거가 있는 판단이 조용히
# 삭제되므로, 그 주제를 말하는 통상 어휘를 넉넉히 적는다. 여기 없는 주제어는
# `theme_supported_by` 가 아예 판정하지 않는다(모르면 침묵).
_THEME_PATTERNS: dict[str, tuple[str, ...]] = {
    "SMR": ("smr", "소형모듈", "소형원자로", "smallmodular", "sfr", "소듐냉각",
            "나트륨원자로", "나트륨원전", "마이크로원자로"),
    "신규건설": ("신규원전", "신규원자로", "신규건설", "신규노형", "원전건설",
                 "원전추가", "착공", "건설허가", "부지선정", "newbuild",
                 "newreactor"),
    # '재가동'은 여기 없다. 계획예방정비를 마친 호기의 재가동은 수명연장이
    # 아니라 운영 사건인데(실측 W34: 한울 4호기 자동정지·새울 1호기 정기검사가
    # 계속운전 사건으로 세어졌다), 그 낱말 하나 때문에 '계속운전 18건'이
    # 실제 수명연장 사건 6건 위에 얹혀 축의 크기를 잘못 말했다. 운영 사건은
    # 아래 '원전운영'이 이미 센다.
    "계속운전": ("계속운전", "수명연장", "운영연장", "운영허가갱신", "설계수명",
                 "노후원전", "lifetimeextension", "lifeextension"),
    "전력수요": ("전력수요", "전력수급", "수요전망", "최대전력", "전력소비",
                 "전기본", "powerdemand", "데이터센터전력"),
    # 'AI·데이터센터'를 여기 별도 축으로 두려다 되돌렸다. 실측 W34: 어떤
    # 어휘 조합으로 재도 그 주 741 사건 중 150~330 건이 걸리고(45%), 대표
    # 사건으로 뽑히는 것은 전력망 3법·지역별 차등제처럼 다른 축이 이미 가진
    # 사건이었다. 이 코퍼스에서 'AI·데이터센터'는 축이 아니라 배경어라서,
    # 후보로 세워 두면 다른 축을 밀어내면서 같은 사건을 두 번 쓰게 만든다.
    # 축의 분리는 어휘표가 아니라 프롬프트의 정책 질문 구분이 맡는다 — 생성기가
    # 그 이름을 쓰면 여기 없는 주제어로 취급돼 사건 대조·독립 사건 요건으로만
    # 걸러진다(모르면 침묵).
    "전력망": ("전력망", "송전", "배전", "계통", "변전", "grid", "transmission",
               "전력계통"),
    "수출": ("수출", "수주", "해외진출", "해외사업", "export", "mou", "업무협약",
             "낙찰", "우선협상"),
    "우라늄": ("우라늄", "uranium", "농축", "enrichment", "정광", "옐로케이크",
               "광산"),
    "핵연료": ("핵연료", "연료봉", "농축", "nuclearfuel", "사용후핵연료",
               "재처리", "파이로"),
    "방폐": ("방폐", "사용후핵연료", "고준위", "저준위", "처분장", "건식저장",
             "핵폐기물", "방사성폐기물", "radioactivewaste", "spentfuel"),
    "규제": ("규제", "원안위", "원자력안전위", "원자력안전법", "안전법", "nrc",
             "인허가", "안전심사", "규정개정", "개정안", "법률안", "시행령",
             "고시", "안전기준", "제도개선", "regulat", "licens"),
    "공급망": ("공급망", "기자재", "부품", "supplychain", "공급계약", "수주",
               "주기기", "소재", "협력사"),
    "핵융합": ("핵융합", "fusion", "토카막", "tokamak"),
    "원전운영": ("가동", "정비", "정지", "재가동", "이용률", "운영허가",
                 "출력증강"),
}
_THEME_LOOKUP = {key.casefold(): patterns for key, patterns in _THEME_PATTERNS.items()}


def _squeeze(text: object) -> str:
    """공백을 지운 소문자 — 기사는 '전력수요'를 '전력 수요'로 쓴다."""
    return re.sub(r"\s+", "", str(text or "").casefold())


def _theme_terms(theme: object) -> list[str]:
    """테마명을 근거 본문에서 찾을 조각으로 자른다."""
    cleaned = str(theme or "").casefold()
    return [token for token in re.split(r"[^0-9a-z가-힣]+", cleaned) if len(token) >= 2]


def _compound_hit(token: str, haystack: str) -> bool:
    """한글 합성 주제어는 기사에서 갈라져 나온다 — '신규건설' → "신규 원전 건설".

    글자 두 개짜리 조각이 둘 이상 보이면 같은 주제로 본다. 조각 하나로는
    안 된다 ('전력수요'가 '전력망' 기사에 걸리는 것이 그 경우다).
    """
    if len(token) < 4 or not re.fullmatch(r"[가-힣]+", token):
        return False
    pieces = [token[i:i + 2] for i in range(len(token) - 1)]
    return sum(1 for piece in pieces if piece in haystack) >= 2


def theme_supported_by(theme: object, evidence_text: object) -> bool:
    """그 사건이 이 주제에 대한 근거이기는 한가.

    주제어(SMR)와 근거(전력망 기사)가 아예 다른 이야기인 항목을 걸러 낸다.
    문장 대조(`unsupported_facts`)는 '주제어' 한 단어에 대해서는 아무것도
    말해 주지 않으므로 이 검사가 따로 필요하다.

    주제 계열(topics)로 판정하지 않는다 — 원자력 기사는 '가동·운전' 한 마디로도
    reactor_operation·reactor_project 가 붙어, 전력망 기사가 SMR 의 근거로
    통과한다. 여기서 필요한 것은 계열이 아니라 그 주제를 실제로 말하는가다.

    아는 어휘가 없는 주제어에 대해서는 **판정하지 않는다**. 모르는 말을 근거로
    삭제하면, 막으려던 바로 그 일(근거 있는 판단의 조용한 삭제)을 이 검사가
    하게 된다. 그런 항목은 문장 대조와 독립 사건 요건으로만 걸러진다.
    """
    haystack = _squeeze(evidence_text)
    if not haystack:
        return False
    patterns = _THEME_LOOKUP.get(_squeeze(theme), ())
    if any(_squeeze(pattern) in haystack for pattern in patterns):
        return True
    if any(token in haystack or _compound_hit(token, haystack)
           for token in _theme_terms(theme)):
        return True
    return not patterns


# `article_quality_gate.asserts_fact` 는 카드의 환각을 잡으려고 만든 판정기라
# 체결·수주·승인 같은 동사에 맞춰져 있다. 주간이 세려는 '진전'에는 제도가 실제로
# 움직인 표시가 더 있다 — 실측: "원자력안전법 개정안 국회 본회의 통과"가 진전으로
# 세어지지 않아 근거 있는 '규제 강화'가 떨어졌다. 전역 판정기를 넓히면 일일·오디오의
# 검사 의미가 같이 바뀌므로 주간에서만 더한다.
#
# 2026-08-22 두 번째 공백: 정책 **수치·기준이 실제로 바뀌는 것**도 사건이다.
# "제12차 전기본, 2040년 최대전력 165GW로 상향"이 진전으로 안 세어져 근거 있는
# '전력수요 강화'가 삭제됐다 — 그 주 판세의 중심이었는데도.
#
# 어휘는 기계적으로 넓히지 않았다. W34 741개 사건에 후보를 하나씩 대 보고
# **새로 진전이 되는 사건을 전수로 읽어** 골랐다:
#   상향 7건   전부 실제 개정 (165GW 상향 · 27GW 상향 조정 · 목표 상향)  → 채택
#   하향 0건   이번 주 사례는 없다. 상향의 대칭이라 같이 둔다             → 채택
#   재산정 2건 1건이 "재산정하겠다고 밝혔다"(의사 표명) → 꼬리 검사 보강   → 채택
#   수립 10건  "수립 촉구" · "수립을 위한 토론회" · "수립 시 쟁점"        → 기각
#   공개 14건  "내주 공개" · "비공개 회동" · "기업공개"(IPO)             → 기각
#   개정 3건   "개정 추진" · "개정 지연". 정작 필요한 건 통과가 잡는다    → 기각
#   개편 3건   "개편 시" · "개편이 필요"                                  → 기각
_WEEKLY_PROGRESS_RE = re.compile(
    r"통과|의결|가결|공포|착수|개시|지정|출범|신설|합의|서명|발주|낙찰|"
    r"상향|하향|재산정|"
    r"passed|enacted|launched", re.IGNORECASE)
# '통과 가능성'은 통과가 아니고 '상향 예정'도, '재산정하겠다'도 아직 아니다.
# 게이트가 쓰는 것과 같은 모양이되, 앞말이 그 동사를 **직접** 감쌀 때만 무시한다 —
# 문장 뒤쪽 어딘가의 전망 한 마디가 앞의 사실을 세탁하면 안 되기 때문이다.
# '했다·했으나'는 걸리지 않는다('하' 와 '했' 은 다른 글자다).
# 조사 한 글자가 사이에 끼는 것까지만 본다 ("상향이 검토되고 있다"). 그 이상
# 멀어지면 앞의 사실을 세탁하는 쪽이므로 일부러 안 본다.
_HEDGE_TAIL_RE = re.compile(
    r"\s*(?:하|해|할|될|할\s*수|될\s*수|이|가|은|는|을|를|도|만|의|에)?\s*"
    r"(?:가능|전망|예상|잠재|검토|예정|계획|겠|수\s*있|could|may|might)")


def is_development(text: object) -> bool:
    """그 사건이 '일어난 일'을 말하는가 (전망·검토가 아니라)."""
    cleaned = str(text or "")
    if article_quality_gate.asserts_fact(cleaned):
        return True
    for match in _WEEKLY_PROGRESS_RE.finditer(cleaned):
        if not _HEDGE_TAIL_RE.match(cleaned[match.end():match.end() + 24].casefold()):
            return True
    return False


def story_text(story: dict) -> str:
    """사건의 근거 본문 — 계약(`weekly_contracts`)이 보는 것과 같은 필드."""
    return " ".join(
        str(article.get(field) or "")
        for article in story["articles"]
        for field in ("title", "title_kr", "summary")
    )


def weekly_theme_signals(stories: list[dict]) -> list[dict]:
    """주제별 **독립 사건 수** — LLM 이 판세의 축을 놓치지 않도록.

    한 주 1000건을 한 줄씩 늘어놓으면 눈에 띄는 사건 두셋만 남고 나머지 축은
    뒤로 밀린다 (실측: 기사 1028건을 그대로 넣었을 때 theme_moves 가 1~2개).
    어떤 주제에 서로 다른 사건이 몇 건 있었는지는 Python 이 결정적으로 셀 수
    있고, 그것이 곧 `audit_theme_moves` 의 통과 조건이다. 생성기에게 채점
    기준을 보여 주는 것이지 판단을 대신해 주는 것이 아니다 — 방향과 근거는
    여전히 LLM 이 쓰고, 지목한 사건과 대조된다.

    세 수를 함께 낸다. 원자력 기사는 어느 주제에나 조금씩 걸려서 `사건` 하나만
    보면 모든 주제가 커 보인다 (실측 759건 주간: 최소 주제도 16건).
      · 사건   — 그 주제를 말한 서로 다른 사건
      · 진전   — 그중 '일어난 일'(계약·승인·착공·투자…)을 말한 사건
      · 핵심진전 — 그중 must_read 로 뽑힌 것. 판세의 축은 여기서 갈린다
                   (같은 주 실측: SMR 4 · 계속운전 2 · 전력망 2 · 나머지 0~2).
    """
    rows = [(story,
             story_text(story),
             str(story["articles"][0].get("grade") or "")) for story in stories]
    views = [(text, is_development(text), grade) for _story, text, grade in rows]
    signals: list[dict] = []
    for theme in _THEME_PATTERNS:
        matched = [(is_event, grade) for text, is_event, grade in views
                   if theme_supported_by(theme, text)]
        if len(matched) < THEME_MOVE_MIN_STORIES:
            continue
        signals.append({
            "theme": theme,
            "사건": len(matched),
            "진전": sum(1 for is_event, _grade in matched if is_event),
            "핵심진전": sum(1 for is_event, grade in matched
                            if is_event and grade == "must_read"),
        })
    # 상한을 10 으로 두었더니 must_read 가 없는 축이 잘려 나갔다 (실측 W34:
    # 계속운전 — 스페인 알마라즈 연장 승인·정부 수명만료 9기 방침 등 서로 다른
    # 사건 12건이 있는데도 후보 목록에 없어, 생성기가 그 축을 볼 방법이 없었다).
    # '유지'가 정상적인 판세 결과라면 조용한 축도 후보에는 서 있어야 한다.
    # 어차피 요건(`THEME_MOVE_MIN_STORIES`)을 통과한 주제어만 남으므로 열몇 줄이다.
    signals.sort(key=lambda row: (-row["핵심진전"], -row["진전"], row["theme"]))
    return signals


# 후보 축마다 **그 축의 사건 몇 건**을 이름으로 보여 준다.
#
# 왜 필요한가: 수만 주면 생성기는 그 수를 확인할 방법이 없다. 사건 목록은
# 발행순 700여 줄이라 어느 축의 사건이 어디 있는지 훑어서는 안 보이고,
# 결과적으로 눈에 띄는 두셋만 쓰고 끝난다(실측 W34: 후보 축은 규제 8·SMR 4·
# 전력수요 3·전력망 3·원전운영 3·계속운전 2 였는데 theme_moves 는 3개, 규제·
# 원전운영·계속운전은 아예 언급되지 않았다 — 검증에서 떨어진 것이 아니라
# 애초에 쓰이지 않았다).
#
# 판단을 대신해 주는 것이 아니다. 여기 실리는 것은 Python 이 결정적으로 고른
# **후보**이고, 방향과 근거는 여전히 생성기가 쓰며 그 뒤 사건 대조를 지난다.
THEME_DIGEST_STORIES = 4


def theme_signal_lines(stories: list[dict], signals: list[dict]) -> list[str]:
    """후보 축 + 그 축의 대표 사건 몇 줄."""
    rows = [(story, story_text(story)) for story in stories]
    lines: list[str] = []
    for signal in signals:
        theme = signal["theme"]
        lines.append(f"{theme} | 사건 {signal['사건']} · 진전 {signal['진전']}"
                     f" · 핵심진전 {signal['핵심진전']}")
        matched = [(story, text) for story, text in rows
                   if theme_supported_by(theme, text) and is_development(text)]
        # must_read 를 앞에 둔다 — 축의 방향은 거기서 갈린다.
        matched.sort(key=lambda row: 0 if row[0]["articles"][0].get("grade")
                     == "must_read" else 1)
        for story, _text in matched[:THEME_DIGEST_STORIES]:
            rep = story["articles"][0]
            title = (rep.get("title_kr") or rep.get("title") or "")[:70]
            lines.append(f"    · hash:{story['key']} {title}")
    return lines


# ---- 합성 + 포맷 ---------------------------------------------------------------

def batch_synthesize(items: list[dict], agg: dict) -> dict:
    fallback = {"weekly_intro": "", "policy_shifts": [], "theme_moves": [],
                "khnp_direct": "", "watchpoints": [], "report_candidates": [],
                "key_events": []}
    if not items or not os.environ.get("GEMINI_API_KEY", ""):
        return fallback

    stories = weekly_stories(items)
    lines = story_lines(stories)
    signals = weekly_theme_signals(stories)
    khnp = weekly_sections.khnp_stories(stories)
    khnp_block = ""
    if khnp:
        khnp_block = ("[한수원이 직접 당사자인 사건 — khnp_direct 의 근거]\n"
                      + "\n".join(f"    · hash:{row['key']} {row['title'][:70]}"
                                  for row in khnp) + "\n\n")
    # 후보 축을 **맨 뒤**에 둔다. 앞에 두면 사건 목록 700여 줄이 그 사이에
    # 끼어, 정작 축을 고를 때 목록의 눈에 띄는 사건만 남는다 (실측 W34: 앞에
    # 두었을 때 핵심진전 8인 '규제' 축이 세 번 연속 언급되지 않았다).
    user_text = (f"[시스템 집계]\n{json.dumps(agg, ensure_ascii=False)}\n\n"
                 + f"[지난 7일 사건 {len(stories)}건 (기사 {len(items)}건 병합)]\n"
                 + "\n".join(lines) + "\n\n"
                 + khnp_block
                 + f"[주제별 후보 축 — 사건=서로 다른 사건, 진전=실제로 일어난 일, "
                 f"핵심진전=그중 must_read. 들여쓴 줄은 그 축의 사건 예시]\n"
                 + "\n".join(theme_signal_lines(stories, signals)) + "\n\n"
                 + "theme_moves 는 위 후보 축을 **위에서부터 차례로** 검토해 쓴다. "
                 "핵심진전이 2 이상인 축은 하나도 건너뛰지 말고 판단하라 — "
                 "쓸지 말지는 그 축의 사건을 읽고 정한다.")

    try:
        from gemini_client import call_json
        result = call_json(WEEKLY_PROMPT, user_text,
                           temperature=0.3, max_output_tokens=10000, timeout=120.0,
            label="weekly_bot",
        )
    except Exception as e:  # noqa: BLE001
        print(f"  ! weekly synthesis failed: {type(e).__name__}: {e}")
        return fallback

    out = dict(fallback)
    for key in out:
        v = result.get(key)
        if isinstance(fallback[key], list):
            out[key] = v if isinstance(v, list) else []
        else:
            out[key] = str(v or "")
    out["key_events"] = out["key_events"][:5]
    out["report_candidates"] = out["report_candidates"][:3]
    prune_evidence_hashes(out, items)
    # 지어낸 hash 를 걷어낸 **뒤에** 문장을 본다. 살아남은 hash 가 진짜 기사를
    # 가리키는지와, 그 기사가 실제로 그 문장을 뒷받침하는지는 다른 질문이다.
    # 텔레그램·웹·저장본이 같은 결과를 쓰도록 여기 한 곳에서 끝낸다.
    return verify_synthesis(out, items)


def prune_evidence_hashes(synthesis: dict, items: list[dict]) -> None:
    """근거 hash 를 이번 주 입력에 실제로 있는 것만 남긴다.

    전역 key_events 만으로는 어떤 hash 가 어느 문장의 근거인지 알 수 없어 모든
    문장에 같은 칩이 붙는다. 문장별 evidence_hashes 를 받되, LLM 이 지어낸 hash 는
    화면에서 죽은 칩이 되므로 여기서 잘라낸다.

    상한은 `WEEKLY_EVIDENCE_LIMIT`. 한 판세 문장이 여러 독립 사건을 종합하는 것이
    주간의 일이므로, 상한이 2 면 근거가 있는데도 지목을 못 해 검증에서 떨어진다.
    화면 칩은 웹이 다시 2개로 자르므로 표시 계약은 그대로다.
    """
    known = {str(item["hash"])[:8] for item in items if item.get("hash")}
    for key in ("policy_shifts", "theme_moves"):
        for row in synthesis.get(key) or []:
            if not isinstance(row, dict):
                continue
            raw = row.get("evidence_hashes")
            # 순서를 보존하며 중복 제거한다. set 으로 걸러 내면 순서가 실행마다
            # 달라져 dirty 판정이 항상 참이 되고, 같은 리포트를 무한히 다시 쓴다.
            kept: list[str] = []
            for value in raw if isinstance(raw, list) else []:
                short = str(value)[:8]
                if short in known and short not in kept:
                    kept.append(short)
            row["evidence_hashes"] = kept[:WEEKLY_EVIDENCE_LIMIT]


# ---- 문장 단위 근거 검증 --------------------------------------------------------
#
# evidence_hashes 가 이번 주 목록에 있는 hash 인지만 보던 것으로는, 진짜 기사를
# 가리키면서 그 기사와 다른 이야기를 하는 문장을 못 잡는다. 화면에는 살아 있는
# 근거 칩이 붙어 있으니 오히려 더 믿음직해 보인다.
#
# 그래서 hash 로 지목된 **그 사건**과 문장을 대조한다. 기준은 오디오와 같은
# EvidenceContract 이며, LLM 이 만든 다른 문장은 근거가 되지 않는다.
#
# 다만 근거의 단위는 기사가 아니라 사건이다. 같은 발표를 여러 매체가 나눠 쓰면
# 수치는 A 매체에, 기관명은 B 매체에 있다. 기사 하나로 좁혀 대조하면 그 주에
# 분명히 있었던 사실이 "근거 없음"이 된다 — 검증을 약하게 한 것이 아니라
# 검증 단위를 분석 단위에 맞춘 것이고, 그 대가로 강약 판단에는 아래
# `audit_theme_moves` 의 독립 사건 요건이 새로 붙는다.


_CONTRACT_CACHE: tuple[list[dict], dict] | None = None


def weekly_contracts(items: list[dict]) -> dict:
    """hash8 → 그 기사가 속한 **사건 하나**의 근거 계약.

    사건에 접힌 모든 기사(+ 그 기사들의 검인된 manifest)가 한 계약을 이룬다.
    어느 매체의 hash 를 지목하든 같은 사건의 근거 전체와 대조된다.

    검증과 결정적 코너가 같은 계약을 봐야 하므로 한 번 지어 재사용한다 —
    manifest 로딩이 붙어 있어 두 번 지으면 그만큼 디스크를 다시 읽는다.
    """
    global _CONTRACT_CACHE
    stories = weekly_stories(items)
    # 캐시가 그 목록을 붙들고 있으므로 동일성 비교가 안전하다 (id 재사용 없음).
    if _CONTRACT_CACHE is not None and _CONTRACT_CACHE[0] is stories:
        return _CONTRACT_CACHE[1]
    hashes = {str(article.get("hash") or "")
              for story in stories for article in story["articles"]
              if article.get("hash")}
    manifests = news_archive.load_evidence_manifests(hashes)
    specs = []
    for story in stories:
        # `detail` 은 코너용으로만 들고 다닌다. 계약 본문에 넣으면 판세 문장이
        # 대조되는 근거의 범위가 조용히 넓어진다 — 이번 변경은 코너를 더하는
        # 것이지 검증을 느슨하게 하는 것이 아니다.
        articles = [{**{k: v for k, v in article.items() if k != "detail"},
                     "hash": str(article["hash"])}
                    for article in story["articles"] if article.get("hash")]
        if not articles:
            continue
        specs.append({
            "key": story["key"],
            "articles": articles,
            "manifests": [manifests[article["hash"]] for article in articles
                          if article["hash"] in manifests],
            # 사건의 기준일은 가장 이른 보도 — 뒤늦은 재보도가 날짜 검사의
            # 기준을 뒤로 밀면 그 사이의 오기를 못 잡는다.
            "reference_date": min(
                (article.get("published_at") or article.get("cached_at") or ""
                 for article in articles), default=""),
        })
    by_story = {c.key: c for c in article_quality_gate.build_evidence_contracts(specs)}
    index: dict = {}
    for story in stories:
        contract = by_story.get(story["key"])
        if contract is None:
            continue
        for article in story["articles"]:
            short = str(article.get("hash") or "")[:8]
            if short:
                index[short] = contract
    # 사건 키(`story["key"]`)는 대표 기사의 hash8 이라 위 색인에 이미 들어 있다 —
    # 코너가 사건 키로 찾아도 같은 계약이 나온다.
    _CONTRACT_CACHE = (stories, index)
    return index


def _unique_contracts(contracts: dict) -> list:
    """사건 계약은 멤버 hash 마다 색인돼 있다 — 같은 것을 여러 번 세지 않는다."""
    return list({contract.key: contract for contract in contracts.values()}.values())


def audit_theme_moves(rows: list, contracts: dict) -> tuple[list[dict], list[dict]]:
    """강약 판단이 서로 다른 사건 위에 서 있는지 본다.

    · 강화/약화 — 주제와 실제로 관련된 **서로 다른 사건 2건 이상**, 그 중 2건
      이상이 '일어난 일'(계약·승인·착공·투자…)을 말해야 한다. 같은 발표의 반복
      보도는 사건 1건이므로 여기서 걸린다. 전망 기사만 모은 '강화'도 걸린다.
    · 유지 — 방향을 주장하지 않으므로 관련 사건 1건이면 된다.
    · 두 축이 **같은 사건들만** 지목하면 뒤엣것을 뺀다. 표현만 다른 축은 축이
      아니고, 화면에서는 같은 근거 칩이 두 줄에 붙어 폭이 두 배로 보인다.

    완화가 아니라 추가 검사다. 문장 대조를 통과한 항목만 여기 온다.
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    seen_sets: list[tuple[str, frozenset[str]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        theme = str(row.get("theme") or "")
        direction = str(row.get("direction") or "").strip()
        raw = row.get("evidence_hashes")
        if isinstance(raw, str):
            raw = [raw]
        cited: dict = {}
        for value in raw or []:
            contract = contracts.get(str(value)[:8])
            if contract is not None:
                cited.setdefault(contract.key, contract)
        relevant = [c for c in cited.values()
                    if theme_supported_by(theme, c.text)]
        needed = THEME_MOVE_MIN_STORIES if direction in ("강화", "약화") else 1
        events = [c for c in relevant if is_development(c.text)]
        if len(relevant) < needed or (needed > 1
                                      and len(events) < THEME_MOVE_MIN_STORIES):
            dropped.append({"theme": theme, "direction": direction,
                            "stories": len(relevant), "events": len(events)})
            continue
        basis = frozenset(contract.key for contract in relevant)
        echo = next((name for name, other in seen_sets if other == basis), "")
        if echo:
            dropped.append({"theme": theme, "direction": direction,
                            "stories": len(relevant), "events": len(events),
                            "same_stories_as": echo})
            continue
        seen_sets.append((theme, basis))
        kept.append(row)
    return kept, dropped


def verify_synthesis(synthesis: dict, items: list[dict]) -> dict:
    """근거와 어긋나는 항목만 덜어낸다 — 주간 브리핑 전체를 죽이지 않는다.

    한 문장이 틀렸다고 그 주의 판세 보고를 통째로 실패시키면, 다음부터는
    아무도 게이트를 켜 두지 않는다. 항목 단위로만 뺀다.
    """
    contracts = weekly_contracts(items)
    everything = _unique_contracts(contracts)
    dropped: dict[str, int] = {}

    def prune(key: str, fact: tuple[str, ...], analysis: tuple[str, ...],
              hash_field: str = "evidence_hashes",
              require_evidence: bool = True) -> None:
        rows = synthesis.get(key) or []
        if not isinstance(rows, list):
            synthesis[key] = []
            return
        kept, findings = article_quality_gate.audit_evidence_items(
            rows, contracts, text_fields=fact, analysis_fields=analysis,
            hash_field=hash_field, require_evidence=require_evidence,
            fallback_contracts=everything,
        )
        if findings:
            dropped[key] = len(findings)
        synthesis[key] = kept

    # 구체적인 사실을 말하는 항목은 근거 기사를 지목해야 한다. 각 쌍의 앞이
    # '무슨 일이 있었는가', 뒤가 '그래서 무엇인가'라 검사 강도가 다르다.
    prune("policy_shifts", ("what",), ("so_what",))
    prune("theme_moves", ("theme",), ("why",))
    prune("key_events", ("headline",), ("implication",), hash_field="hash")
    # 보고서 후보·관찰 포인트는 다음 주를 보는 항목이라 개별 근거 기사를
    # 요구하지 않는다. 다만 그 주 기사에 없는 기관·수치를 지어내는 것은 막는다.
    prune("report_candidates", (), ("topic", "basis"), require_evidence=False)

    # 문장이 근거와 맞는지와, 그 강약 판단이 몇 개의 독립 사건 위에 서 있는지는
    # 다른 질문이다. 뒤쪽은 문장 대조로 볼 수 없어 여기서 따로 본다.
    kept_themes, thin = audit_theme_moves(synthesis.get("theme_moves") or [], contracts)
    if thin:
        dropped["theme_moves_thin"] = len(thin)
        print(f"  ! weekly 독립 근거 부족한 강약 판단 제거: "
              f"{json.dumps(thin, ensure_ascii=False)}")
    synthesis["theme_moves"] = kept_themes

    watchpoints = synthesis.get("watchpoints")
    if isinstance(watchpoints, list):
        kept = [row for row in watchpoints
                if not article_quality_gate.unsupported_facts(str(row or ""), everything)]
        if len(kept) != len(watchpoints):
            dropped["watchpoints"] = len(watchpoints) - len(kept)
        synthesis["watchpoints"] = kept

    for key in ("weekly_intro", "khnp_direct"):
        text = str(synthesis.get(key) or "")
        if text and article_quality_gate.unsupported_facts(text, everything):
            synthesis[key] = ""
            dropped[key] = 1
    if dropped:
        print(f"  ! weekly 근거 미확인 항목 제거: "
              f"{json.dumps(dropped, ensure_ascii=False)}")
    return synthesis


def article_by_hash8(items: list[dict], h8: str) -> dict | None:
    for art in items:
        if art["hash"][:8] == (h8 or "")[:8]:
            return art
    return None


# ---- 웹용 주간 리포트 저장 -----------------------------------------------------
#
# 지금까지 주간 리포트는 텔레그램 텍스트로만 나가고 사라졌다. 웹 '주간 흐름'
# 탭은 키워드·slope 같은 정량 관찰뿐이라, 정책 변화와 한수원 직접 영향을 해석하는
# 문단이 붙으면 뉴스 사이트에서 정책 브리핑 도구로 넘어간다.
# batch_synthesize 결과를 그대로 재사용하므로 Gemini 호출은 늘지 않는다.
WEEKLY_REPORTS_FILE = ROOT / "weekly_reports.json"


def week_id(day: datetime) -> str:
    """Asia/Seoul 기준 ISO 주차. UTC 로 계산하면 연말·주말 경계가 엇갈린다."""
    year, week, _ = day.astimezone(KST).isocalendar()
    return f"{year}-W{week:02d}"


def load_weekly_reports(path: Path | None = None) -> dict:
    path = path or WEEKLY_REPORTS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "reports": {}}
    reports = raw.get("reports")
    return {"schema_version": 1,
            "reports": reports if isinstance(reports, dict) else {}}


def build_week_sections(items: list[dict], now: datetime | None = None) -> dict:
    """결정적 코너 네 개 — 판세 합성과 **같은** 사건 묶음·근거 계약 위에서 만든다.

    LLM 을 타지 않으므로 '주 1회 1호출' 계약이 그대로다. 기간도 판세와 같은
    `week_window` 하나를 쓴다 — '예정'만 그 창의 끝 이후를 본다.
    """
    start, end = week_window(now)
    return weekly_sections.build_sections(
        weekly_stories(items), weekly_contracts(items),
        is_development=is_development,
        week_start=start.date().isoformat(), week_end=end.date().isoformat())


def save_weekly_report(synthesis: dict, agg: dict, items: list[dict],
                       now: datetime | None = None,
                       path: Path | None = None,
                       sections: dict | None = None) -> bool:
    """이번 주 리포트를 저장. 저장했으면 True.

    저장 여부를 len(reports) 증가로 판정하면 안 된다 — 같은 주차 덮어쓰기는
    크기가 그대로라 영영 저장 안 된 것처럼 보인다. 명시적 dirty 플래그를 쓴다.
    """
    now = (now or datetime.now(KST)).astimezone(KST)
    path = path or WEEKLY_REPORTS_FILE
    store = load_weekly_reports(path)
    key = week_id(now)
    # 라벨과 수집 구간을 한 함수에서 낸다 — 둘이 갈리면 화면의 '이번 주'가
    # 실제로 담긴 기사의 주와 하루 어긋난다.
    start, end = week_window(now)
    sections = sections if sections is not None else build_week_sections(items, now)
    entry = {
        "week_id": key,
        "week_start": start.date().isoformat(),
        "week_end": end.date().isoformat(),
        "generated_at": now.isoformat(),
        "timezone": "Asia/Seoul",
        "schema_version": 1,
        # 기사 수가 아니라 병합된 고유 이슈 수. 기사 수를 쓰면 후속 보도가 많은
        # 주가 실제보다 풍성해 보인다.
        "source_issue_count": count_unique_issues(items),
        "article_count": agg.get("total", len(items)),
        **{key_name: synthesis.get(key_name) for key_name in (
            "weekly_intro", "policy_shifts", "theme_moves", "khnp_direct",
            "watchpoints", "report_candidates", "key_events")},
        # 결정적 코너. 저장본이 유일한 원본이라 텔레그램·웹이 같은 값을 쓴다.
        **{key_name: sections.get(key_name) or [] for key_name in (
            "top_stories", "country_briefs", "publications", "upcoming")},
    }
    # 내용 비교에서 generated_at 은 뺀다 — 매 실행마다 달라지므로 포함하면
    # dirty 가 항상 참이 되고 같은 리포트를 무한히 다시 쓴다.
    def content(row: dict | None) -> dict:
        return {k: v for k, v in (row or {}).items() if k != "generated_at"}

    if content(store["reports"].get(key)) == content(entry):
        print(f"[weekly] {key} 리포트 변경 없음")
        return False
    store["reports"][key] = entry
    # 최근 26주만 보관 — 반년치면 화면·빌드에 충분하다.
    for stale in sorted(store["reports"])[:-26]:
        store["reports"].pop(stale, None)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print(f"[weekly] {key} 리포트 저장 (이슈 {entry['source_issue_count']}건)")
    return True


def count_unique_issues(items: list[dict]) -> int:
    """같은 사건을 하나로 센다 — '후속 보도가 많은 주 = 풍성한 주' 착시 차단.

    2026-08-15: 제목 앞 40자 정규화로 세던 옛 구현은 **실질 no-op 이었다.**
    상류가 완전일치 제목을 이미 걷어낸 뒤라 앞 40자가 겹치는 쌍이 남지 않는다
    (실측 852건 입력 → 848). 매체마다 같은 발표에 다른 표현을 쓰는 게 문제인데
    접두사 비교로는 그걸 못 잡는다.

    일일 브리핑의 `ranking.cluster_duplicates` 를 그대로 쓴다. 문자열 ratio +
    토큰 자카드에 호기 충돌 거부권까지 아카이브로 조정된 판정기이고, LLM 을
    타지 않아 '주 1회 1호출' 계약도 깨지 않는다. 같은 입력 852건 → 649.

    2026-08-22: 같은 묶음을 `weekly_stories` 도 쓰게 되면서 여기서 한 번 더
    묶지 않는다. 세는 수와 LLM 이 본 사건 수, 근거 계약의 단위가 서로 다르면
    "이슈 729건"이라고 적어 놓고 다른 개수 위에서 판세를 쓰게 된다.
    """
    return len(weekly_stories(items))


def format_weekly(items: list[dict], synthesis: dict | None = None,
                  sections: dict | None = None,
                  now: datetime | None = None) -> str:
    start, today = week_window(now)
    agg = build_aggregates(items)
    synthesis = synthesis if synthesis is not None else batch_synthesize(items, agg)
    sections = sections if sections is not None else build_week_sections(items, now)

    parts: list[str] = []
    parts.append(f"📅 <b>{start.month}/{start.day}-{today.month}/{today.day} "
                 f"원자력 주간 판세</b>")
    parts.append(f"<i>총 {agg['total']}건 검토 · must_read {agg['must_read']}건</i>")
    parts.append("")

    if synthesis["weekly_intro"]:
        parts.append("<b>이번 주 핵심</b>")
        parts.append(html.escape(synthesis["weekly_intro"]))
        parts.append("")

    # 결정적 코너가 먼저 온다. "무슨 일이 있었나"를 읽은 다음에 "그래서 판이
    # 어디로 기울었나"를 읽는 순서가 판세 보고의 순서다.
    if sections.get("top_stories"):
        parts.append(f"━━ <b>📌 이번 주</b> ({start.month}월 {start.day}일–"
                     f"{today.month}월 {today.day}일 · 주요 사건) ━━")
        for row in sections["top_stories"]:
            parts.append(f"• <b>{html.escape(row['title'])}</b>")
            if row.get("summary"):
                parts.append(f"  {html.escape(row['summary'])}")
            if row.get("articles", 1) > 1:
                parts.append(f"  이어지는 이슈 · {row['articles']}건"
                             f" · 매체 {row['outlets']}곳")
            if row.get("link"):
                parts.append(f"  🔗 {row['link']}")
        parts.append("")

    if sections.get("country_briefs"):
        parts.append("━━ <b>🌍 국가별 단신</b> ━━")
        for row in sections["country_briefs"]:
            # 국가명과 제목 사이에 구분자를 둔다. 붙여 쓰면 '한국정부'처럼
            # 한 낱말로 읽힌다.
            parts.append(f"• <b>{html.escape(row['country_kr'])}</b> — "
                         f"{html.escape(row['title'])}")
        parts.append("")

    if sections.get("publications"):
        parts.append("━━ <b>📚 이번 주 발간물</b> ━━")
        for row in sections["publications"]:
            head = f"{html.escape(row['org'])} · " if row.get("org") else ""
            parts.append(f"• {head}<b>{html.escape(row['title'])}</b>")
            parts.append(f"  🔗 {row['url']}")
        parts.append("")

    # 예정 코너는 flag 로 꺼 둔다(weekly_sections.SHOW_WEEKLY_UPCOMING).
    # 저장본에는 계속 실리므로 sections["upcoming"] 은 비어 있지 않을 수 있다 —
    # 여기서 막는 것은 독자에게 나가는 문장뿐이다.
    if weekly_sections.SHOW_WEEKLY_UPCOMING and sections.get("upcoming"):
        parts.append("━━ <b>🗓 예정</b> ━━")
        for row in sections["upcoming"]:
            when = row["date"]
            # 정밀도가 '월'이면 날짜를 지어내지 않는다 — 저장본이 말하는 만큼만.
            label = (f"{int(when[5:7])}월" if row.get("precision") == "month"
                     else f"{int(when[5:7])}월 {int(when[8:10])}일")
            parts.append(f"• <b>{label}</b> — {html.escape(row['title'])}")
        parts.append("")

    if synthesis["policy_shifts"]:
        parts.append("━━ <b>🏛 정책 변화</b> ━━")
        for p in synthesis["policy_shifts"][:4]:
            if not isinstance(p, dict) or not p.get("what"):
                continue
            parts.append(f"• <b>{html.escape(str(p['what']))}</b>")
            if p.get("so_what"):
                parts.append(f"  → {html.escape(str(p['so_what']))}")
        parts.append("")

    if synthesis["theme_moves"]:
        # 웹(흐름 탭)과 같은 중립 표기를 쓴다(2026-08-11 사용자 결정). 같은 독자에게
        # 가는 두 표면이 다른 이름으로 같은 것을 부르면 그것도 어긋남이고, 한수원
        # 임직원용 서비스가 투자 시그널을 주는 모양새는 기획 단계부터의 우려였다.
        # 담는 내용(theme_moves)은 그대로 — 뜨는 이름은 SMR·계속운전처럼 주제어다.
        parts.append("━━ <b>주제별 강약</b> ━━")
        arrow = {"강화": "▲", "약화": "▼", "유지": "―"}
        for t in synthesis["theme_moves"][:4]:
            if not isinstance(t, dict) or not t.get("theme"):
                continue
            d = arrow.get(str(t.get("direction", "")), "―")
            line = f"{d} <b>{html.escape(str(t['theme']))}</b>"
            if t.get("why"):
                line += f" — {html.escape(str(t['why']))}"
            parts.append(line)
        parts.append("")

    if synthesis["khnp_direct"]:
        parts.append("━━ <b>🇰🇷 한국·한수원 직접 영향</b> ━━")
        parts.append(html.escape(synthesis["khnp_direct"]))
        parts.append("")

    if synthesis["key_events"]:
        # 위 '이번 주'·'국가별 단신'이 이미 낸 사건은 여기서 다시 내지 않는다.
        # 두 코너가 각각 결정적 선정과 LLM 선정이라 겹치는 것이 정상인데,
        # 겹친 채로 두면 한 메시지 안에서 같은 헤드라인을 두 번 읽게 된다.
        # 남는 것은 그 사건들이 못 실은 **해석**이 붙은 나머지다.
        shown = [weekly_sections.title_tokens(row["title"])
                 for key in ("top_stories", "country_briefs")
                 for row in sections.get(key) or []]
        lines: list[str] = []
        for ev in synthesis["key_events"][:5]:  # 렌더링에서도 방어 (LLM 초과 응답 컷)
            if not isinstance(ev, dict):
                continue
            art = article_by_hash8(items, ev.get("hash", ""))
            headline = ev.get("headline") or (art["title"] if art else "")
            if not headline:
                continue
            tokens = weekly_sections.title_tokens(headline)
            if weekly_sections.echoes(tokens, shown):
                continue
            shown.append(tokens)
            lines.append(f"• <b>{html.escape(str(headline))}</b>")
            if ev.get("implication"):
                lines.append(f"  → {html.escape(str(ev['implication']))}")
            if art and art.get("link"):
                lines.append(f"  🔗 {art['link']}")
        if lines:
            parts.append("━━ <b>📌 그 밖에 짚어 둘 사건</b> ━━")
            parts.extend(lines)
            parts.append("")

    if synthesis["report_candidates"]:
        parts.append("━━ <b>📝 보고서 검토 후보</b> ━━")
        for r in synthesis["report_candidates"]:
            if not isinstance(r, dict) or not r.get("topic"):
                continue
            line = f"• <b>{html.escape(str(r['topic']))}</b>"
            if r.get("basis"):
                line += f" — {html.escape(str(r['basis']))}"
            parts.append(line)
        parts.append("")

    if synthesis["watchpoints"]:
        parts.append("📋 <b>다음 주 모니터링 포인트</b>")
        for wp in synthesis["watchpoints"][:5]:
            parts.append(f"• {html.escape(str(wp))}")
        parts.append("")

    # ---- Python 계산 부록 (LLM 무관 — 항상 사실) ----
    repeats = followup_hits(items)
    if repeats:
        parts.append(f"🔁 <b>반복 등장</b>: {html.escape(', '.join(repeats))}")
    gaps = coverage_gaps(items)
    if gaps:
        parts.append(f"🕳 <b>이번 주 소스 공백</b>: {html.escape(', '.join(gaps[:6]))}")

    return "\n".join(parts).strip()


def main() -> None:
    curated = load_curated()
    items = get_week_articles(curated)
    if not items:
        print("No articles in past week. Skipping weekly report.")
        return

    print(f"Weekly report: {len(items)} articles from past {WEEK_DAYS} days")
    # 합성을 한 번만 돌려 텔레그램과 웹이 같은 결과를 쓴다 (Gemini 호출 +0).
    # 코너도 한 번만 만든다 — 두 번 만들면 같은 주에 다른 목록이 나갈 수 있다.
    agg = build_aggregates(items)
    synthesis = batch_synthesize(items, agg)
    sections = build_week_sections(items)
    message = format_weekly(items, synthesis, sections)
    save_weekly_report(synthesis, agg, items, sections=sections)

    from telegram_send import send_long_text  # lazy — 토큰 없는 로컬 테스트 대비
    results = send_long_text(message, parse_mode="HTML", disable_preview=True)
    ok = sum(1 for r in results if r.get("ok"))
    print(f"Weekly report sent ({ok}/{len(results)}).")

    # 주간 판세는 뜨는 즉시 그것 하나만 채널로 — 일일 배치에 태우지 않는다.
    # 금요일 저녁 자료를 토요일 아침까지 붙들면 '주간'이라는 말이 무색해진다.
    # 발송 실패해도 리포트 커밋(웹 '주간 흐름' 탭 재료)까지는 가야 하므로 비치명.
    try:
        import channel_queue
        channel_queue.publish_weekly(message)
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::주간 판세 채널 공개 실패 — {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
