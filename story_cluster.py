"""Story-level metadata consolidation for daily nuclear news selection.

This module is deliberately deterministic and contains no LLM calls.  It is shared by
``news_bot.py`` (collection-time folding), ``ranking.py`` (fast title/facility duplicate
detection) and ``dedup.py`` (Gemini story clustering) so every duplicate path produces the
same coverage metadata.

The key distinction is between an *article* and a *briefing story*: multiple articles can
cover the same underlying development.  We retain the best representative article while
preserving the other outlets/titles/evidence as metadata instead of silently discarding it.

두 계층의 근거를 구분해서 담는다
--------------------------------
``raw_sources``   수집 단계에서 접힌 기사. 큐레이션을 받기 전이라 제목·URL·매체만
                  있다. 예전에는 여기서 **삭제**됐고, 그래서 story 가 만들어질 때는
                  이미 매체 수·근거 수가 실제보다 줄어 있었다. 지우지 않고 대표에
                  달아 story 단계까지 들려 보낸다.
``story_sources`` 위 raw_sources 와 선정 단계에서 접힌 기사의 매체를 합친 최종 집합.
                  ``story_outlet_count`` 가 이 집합의 크기이므로, raw_sources 를
                  보존한 뒤에야 그 숫자가 '실제 복수 출처'를 뜻하게 된다.

화면용 대표는 story 가 완성된 **뒤에** 고른다 (``choose_display_representative``).
수집 시점의 원점수로 미리 못 박으면, 아직 존재하지도 않는 story 를 근거로 대표를
정하는 셈이 된다.
"""

from __future__ import annotations

from urllib.parse import urlparse

# 대표 하나에 매달 수 있는 수집 단계 근거의 상한. 큐(JSON)에 그대로 실려 나가므로
# 무한정 쌓으면 digest_queue.json 이 부푼다. 실측상 한 사건의 국내 재전재는 많아야
# 10여 건이라 24 면 잘리는 일이 거의 없고, 잘려도 카운트는 별도로 누적된다.
RAW_SOURCE_LIMIT = 24


def _clean(value) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def source_identity(article: dict) -> str:
    """Return a stable outlet identity; one outlet counts once toward coverage."""
    publisher = _clean(article.get("publisher"))
    domain = _clean(article.get("domain"))
    if not domain:
        link = article.get("link") or article.get("url") or ""
        try:
            domain = urlparse(str(link)).netloc.lower().removeprefix("www.")
        except Exception:
            domain = ""
    feed = _clean(article.get("feed"))
    return (publisher or domain or feed or "unknown").lower()


def source_label(article: dict) -> str:
    """Human-readable outlet label for diagnostics."""
    return (_clean(article.get("publisher")) or _clean(article.get("domain"))
            or _clean(article.get("feed")) or "unknown")[:100]


def source_tier(article: dict) -> int | None:
    """Use the existing nuclear-news-main source-tier system; never introduce a second map."""
    try:
        tier = int(article.get("source_tier"))
        if tier in (1, 2, 3):
            return tier
    except (TypeError, ValueError):
        pass

    # Older queue items may not carry source_tier.  Reuse sources.py's existing authority map.
    try:
        from sources import credibility

        result = credibility({
            "title": article.get("title") or article.get("title_kr") or "",
            "url": article.get("link") or article.get("url") or "",
            "meta": article.get("publisher") or article.get("domain") or "",
        })
        tier = result.get("tier")
        return int(tier) if tier in (1, 2) else None
    except Exception:
        return None


def raw_sources_of(article: dict) -> list[dict]:
    """수집 단계에서 이 기사에 접힌 근거들. 없으면 빈 목록."""
    vals = article.get("raw_sources")
    return [v for v in vals if isinstance(v, dict)] if isinstance(vals, list) else []


def raw_source_record(article: dict, *, stage: str, reason: str = "",
                      similarity: float | None = None) -> dict:
    """접히는 기사를 '지워도 되는 중복'이 아니라 '근거 한 건'으로 적는다.

    수집 단계라 큐레이션 필드(summary/detail/features)는 아직 없다. 나중에
    story 를 설명할 때 필요한 것만 담는다 — 누가 어디에 무엇을 썼고, 어느 단계에서
    무슨 이유로 접혔나.
    """
    record = {
        "hash": str(article.get("hash") or ""),
        "title": _clean(article.get("title_kr") or article.get("title"))[:180],
        "link": _clean(article.get("link") or article.get("url"))[:400],
        "identity": source_identity(article),
        "publisher": source_label(article),
        "domain": _clean(article.get("domain"))[:120],
        "feed": _clean(article.get("feed"))[:80],
        "tier": source_tier(article),
        "evidence_role": _clean(article.get("evidence_role"))[:40],
        "pub": _clean(article.get("pub"))[:40],
        "fold_stage": _clean(stage)[:40],
        "fold_reason": _clean(reason)[:120],
    }
    if similarity is not None:
        record["similarity"] = round(float(similarity), 4)
    return record


def attach_raw_source(representative: dict, member: dict, *, stage: str,
                      reason: str = "", similarity: float | None = None) -> dict:
    """수집 단계 중복을 삭제하지 않고 대표에 근거로 매단다.

    member 가 이미 다른 기사를 접어 두었다면 그 근거까지 함께 옮긴다 — A→B→C 로
    이어지는 접힘에서 A 의 매체가 중간에 증발하지 않도록.
    """
    bucket = representative.setdefault("raw_sources", [])
    if not isinstance(bucket, list):
        bucket = []
        representative["raw_sources"] = bucket

    rep_hash = str(representative.get("hash") or "")
    seen = {str(r.get("hash") or "") for r in bucket if isinstance(r, dict)}
    seen.add(rep_hash)

    incoming = [raw_source_record(member, stage=stage, reason=reason, similarity=similarity)]
    incoming.extend(raw_sources_of(member))

    # 상한에 걸려 잘린 근거도 '있었다'는 사실은 남긴다 — 카운트가 조용히 줄면
    # story_outlet_count 가 다시 실제보다 작아진다.
    dropped = int(representative.get("raw_sources_truncated") or 0)
    for record in incoming:
        key = str(record.get("hash") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        if len(bucket) >= RAW_SOURCE_LIMIT:
            dropped += 1
            continue
        bucket.append(record)
    if dropped:
        representative["raw_sources_truncated"] = dropped
    return representative


def _self_source_record(article: dict) -> dict:
    return {
        "identity": source_identity(article),
        "publisher": source_label(article),
        "domain": _clean(article.get("domain"))[:120],
        "tier": source_tier(article),
        "evidence_role": _clean(article.get("evidence_role"))[:40],
    }


def _source_records(article: dict) -> list[dict]:
    """Expand an article plus any story metadata it already inherited from prior dedup."""
    out: list[dict] = []
    existing = article.get("story_sources")
    if isinstance(existing, list) and existing:
        for src in existing:
            if not isinstance(src, dict):
                continue
            ident = _clean(src.get("identity")) or _clean(src.get("publisher")) or _clean(src.get("domain"))
            if not ident:
                continue
            out.append({
                "identity": ident.lower(),
                "publisher": _clean(src.get("publisher"))[:100],
                "domain": _clean(src.get("domain"))[:120],
                "tier": src.get("tier"),
                "evidence_role": _clean(src.get("evidence_role"))[:40],
            })
    if not out:
        out.append(_self_source_record(article))

    # 수집 단계에서 접힌 매체를 여기서 합류시킨다. 이 한 줄이 없으면
    # story_outlet_count 는 '선정 단계에서 본 매체 수'에 머물고, 실제 보도 매체
    # 수는 수집 단계에서 이미 잘린 채 영영 복구되지 않는다.
    for raw in raw_sources_of(article):
        ident = _clean(raw.get("identity")) or _clean(raw.get("publisher")) or _clean(raw.get("domain"))
        if not ident:
            continue
        out.append({
            "identity": ident.lower(),
            "publisher": _clean(raw.get("publisher"))[:100],
            "domain": _clean(raw.get("domain"))[:120],
            "tier": raw.get("tier"),
            "evidence_role": _clean(raw.get("evidence_role"))[:40],
        })
    return out


def _article_hashes(article: dict) -> list[str]:
    out: list[str] = []
    vals = article.get("story_article_hashes")
    if isinstance(vals, list) and vals:
        out.extend(str(v) for v in vals if str(v))
    else:
        h = str(article.get("hash") or "")
        if h:
            out.append(h)
    out.extend(str(raw.get("hash") or "") for raw in raw_sources_of(article)
               if str(raw.get("hash") or ""))
    return out


def _related_titles(article: dict) -> list[str]:
    out = []
    vals = article.get("story_related_titles")
    if isinstance(vals, list):
        out.extend(_clean(v)[:180] for v in vals if _clean(v))
    title = _clean(article.get("title_kr") or article.get("title"))[:180]
    if title:
        out.append(title)
    out.extend(_clean(raw.get("title"))[:180] for raw in raw_sources_of(article)
               if _clean(raw.get("title")))
    return out


def _context_records(article: dict) -> list[dict]:
    existing = article.get("story_context")
    if isinstance(existing, list) and existing:
        return [x for x in existing if isinstance(x, dict)][:8]
    summary = _clean(article.get("summary"))[:500]
    detail = _clean(article.get("detail"))[:900]
    if not summary and not detail:
        return []
    return [{
        "hash": str(article.get("hash") or ""),
        "title": _clean(article.get("title_kr") or article.get("title"))[:180],
        "summary": summary,
        "detail": detail,
    }]


def consolidate_story_metadata(
    representative: dict,
    members: list[dict],
    *,
    relation: str = "duplicate",
    reason: str = "",
    fingerprint: dict | None = None,
    stage: str = "",
) -> dict:
    """Mutate and return the representative with merged story/coverage metadata.

    ``members`` may themselves already be representatives from an earlier dedup stage; their
    inherited metadata is recursively respected.  Counts therefore survive the chain
    title-dedup -> semantic-dedup -> final editorial-dedup.
    """
    all_members = [representative] + [m for m in members if m is not representative]

    sources_by_id: dict[str, dict] = {}
    hashes: list[str] = []
    titles: list[str] = []
    contexts: list[dict] = []
    article_count = 0

    for art in all_members:
        try:
            article_count += max(1, int(art.get("story_article_count") or 1))
        except (TypeError, ValueError):
            article_count += 1
        for src in _source_records(art):
            ident = src.get("identity") or "unknown"
            prev = sources_by_id.get(ident)
            # Preserve the strongest tier if two aliases collapse to one identity.
            if prev is None:
                sources_by_id[ident] = src
            else:
                pt, nt = prev.get("tier"), src.get("tier")
                if nt and (not pt or int(nt) < int(pt)):
                    sources_by_id[ident] = src
        hashes.extend(_article_hashes(art))
        titles.extend(_related_titles(art))
        contexts.extend(_context_records(art))

    # Stable de-duplication while preserving order.
    def uniq(seq):
        seen = set()
        out = []
        for value in seq:
            key = str(value)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(value)
        return out

    hashes = uniq(hashes)
    titles = uniq(titles)[:12]
    # Context de-dup by hash/title, capped to avoid queue bloat.
    ctx_seen = set()
    ctx_out = []
    for ctx in contexts:
        key = ctx.get("hash") or ctx.get("title") or str(ctx)
        if key in ctx_seen:
            continue
        ctx_seen.add(key)
        ctx_out.append(ctx)
        if len(ctx_out) >= 8:
            break

    source_list = sorted(sources_by_id.values(), key=lambda x: x.get("identity") or "")
    tier1_count = sum(1 for s in source_list if s.get("tier") == 1)
    independent_count = sum(1 for s in source_list if s.get("evidence_role") == "independent")

    # 접힌 쪽이 들고 온 수집 단계 근거를 대표로 옮긴다. 위 집계는 멤버별로 읽어서
    # 이미 반영돼 있지만, 대표가 그 목록 자체를 들고 있어야 다음 단계와 진단
    # 화면에서 "무엇이 접혔나"를 볼 수 있다.
    for art in all_members:
        if art is representative:
            continue
        for raw in raw_sources_of(art):
            attach_raw_source(representative, {
                "hash": raw.get("hash"),
                "title": raw.get("title"),
                "link": raw.get("link"),
                "publisher": raw.get("publisher"),
                "domain": raw.get("domain"),
                "feed": raw.get("feed"),
                "source_tier": raw.get("tier"),
                "evidence_role": raw.get("evidence_role"),
                "pub": raw.get("pub"),
            }, stage=str(raw.get("fold_stage") or "collect"),
                reason=str(raw.get("fold_reason") or ""))

    representative["story_article_count"] = max(article_count, len(hashes), 1)
    representative["story_article_hashes"] = hashes
    representative["story_outlet_count"] = len(source_list)
    representative["story_tier1_count"] = tier1_count
    representative["story_independent_outlet_count"] = independent_count
    representative["story_sources"] = source_list
    representative["story_related_titles"] = titles
    representative["story_context"] = ctx_out
    existing_relation = str(representative.get("story_relation") or "")
    if relation in {"single", "collected"} and existing_relation in {"duplicate", "merge"}:
        representative["story_relation"] = existing_relation
    else:
        representative["story_relation"] = relation or existing_relation or "duplicate"
    if reason:
        representative["story_reason"] = _clean(reason)[:300]
    if stage:
        representative["story_dedup_stage"] = stage
    if isinstance(fingerprint, dict) and fingerprint:
        representative["story_fingerprint"] = fingerprint
    representative["story_raw_source_count"] = len(raw_sources_of(representative))
    return representative


# ---- 화면용 대표 선정 (story 가 완성된 뒤) --------------------------------------
#
# 예전에는 대표가 **수집 시점**에 정해졌다. 그 시점에는 story 가 아직 없고 원점수만
# 있어서, 사실상 '먼저 들어온 기사'가 화면을 차지했다. story 를 다 접은 뒤에 다시
# 고르면 근거가 훨씬 많다 — 어느 매체가 tier1 인지, 어느 기사에 본문이 붙었는지,
# 랭킹 점수가 얼마인지가 그때는 전부 확정돼 있다.
#
# 다만 **점수를 뒤집지는 않는다.** 아래 gap 을 넘어서까지 바꾸면 '중요도가 아니라
# 본문 유무로 상단이 정해지는' 다른 문제가 생긴다. 품질은 비슷한 점수 안에서만
# 우선한다.
DISPLAY_SWAP_MAX_SCORE_GAP = 3.0


def _content_rank(article: dict) -> int:
    """카드에 실을 내용이 얼마나 갖춰졌나. 본문 요지 > 요약 > 없음."""
    if len(_clean(article.get("detail"))) >= 40:
        return 2
    if _clean(article.get("summary")):
        return 1
    return 0


def _tier_rank(article: dict) -> int:
    tier = source_tier(article)
    return {1: 3, 2: 2, 3: 1}.get(tier, 0)


def _role_rank(article: dict) -> int:
    role = _clean(article.get("evidence_role")).lower()
    return {"primary": 2, "independent": 1}.get(role, 0)


def display_rank_key(article: dict, score: float = 0.0) -> tuple:
    """화면 대표 후보의 품질 순위.

    hash 를 넣지 않는다 — 동점일 때 hash 로 승부를 가르면 '무엇도 더 낫지 않은데'
    대표가 바뀐다. 교체는 **더 나은 이유가 있을 때만** 일어나야 하고, 동점은 유지가
    정답이다. 결정성은 호출부가 hash 를 보조 키로 써서 따로 확보한다.
    """
    return (_content_rank(article), _tier_rank(article), _role_rank(article),
            round(float(score), 4))


def _display_reason(winner: dict, loser: dict) -> str:
    if _content_rank(winner) > _content_rank(loser):
        return "본문 요지가 있는 기사로 교체"
    if _tier_rank(winner) > _tier_rank(loser):
        return "출처 등급이 더 높은 기사로 교체"
    if _role_rank(winner) > _role_rank(loser):
        return "근거 역할(공식·독립)이 더 강한 기사로 교체"
    return "동일 조건에서 랭킹 점수가 높은 기사로 교체"


def choose_display_representative(
    candidates: list[dict],
    scores: dict[str, float] | None = None,
    *,
    current: dict | None = None,
    max_score_gap: float = DISPLAY_SWAP_MAX_SCORE_GAP,
) -> tuple[dict | None, str]:
    """story 에 접힌 기사들 중 화면에 세울 한 건을 고른다.

    Returns:
        (대표 기사, 사유). 후보가 없으면 (None, "").  현재 대표를 유지하기로 한
        경우 현재 대표와 사유 ``"keep"`` 을 돌려준다.
    """
    pool = [c for c in candidates if isinstance(c, dict)]
    if not pool:
        return (current, "") if current is not None else (None, "")
    scores = scores or {}
    current = current if current is not None else pool[0]
    if current not in pool:
        pool = [current] + pool

    def score_of(art: dict) -> float:
        return float(scores.get(str(art.get("hash") or ""), 0.0))

    # hash 는 **동점 후보 사이의 결정성**에만 쓴다. 아래 비교는 hash 없는 품질
    # 키로만 하므로, 동점이면 현재 대표가 그대로 남는다.
    best = max(pool, key=lambda art: (display_rank_key(art, score_of(art)),
                                      str(art.get("hash") or "")))
    if best is current:
        return current, "keep"
    if display_rank_key(best, score_of(best)) <= display_rank_key(current, score_of(current)):
        return current, "keep"
    # 점수를 크게 거스르면서까지 바꾸지 않는다.
    if score_of(current) - score_of(best) > max_score_gap:
        return current, "keep_score_gap"
    return best, _display_reason(best, current)


def promote_representative(old: dict, new: dict, *, reason: str = "") -> dict:
    """접혀 있던 기사를 새 대표로 세우고 story 근거를 통째로 넘긴다.

    카운트를 다시 계산하지 않는다 — old 와 new 는 이미 같은 story 의 멤버라
    old 의 집계에 new 가 들어 있다. 여기서 재집계하면 같은 기사를 두 번 세게 된다.
    """
    carried = {k: v for k, v in old.items()
               if k.startswith("story_") or k.startswith("raw_source")}
    # 거부권 기록은 양쪽 것을 합친다 — 진단은 지워질 이유가 없다.
    vetoes = [v for v in (new.get("story_stage_vetoes") or []) if isinstance(v, dict)]
    for key in [k for k in new if k.startswith("story_") or k.startswith("raw_source")]:
        new.pop(key, None)
    new.update(carried)
    if vetoes:
        merged = [v for v in (new.get("story_stage_vetoes") or []) if isinstance(v, dict)]
        new["story_stage_vetoes"] = merged + vetoes
    new["story_display_swapped_from"] = str(old.get("hash") or "")
    new["story_display_swapped_from_title"] = _clean(
        old.get("title_kr") or old.get("title"))[:180]
    if reason:
        new["story_display_reason"] = _clean(reason)[:200]
    return new


def mark_display_representative(article: dict, *, candidates: int, reason: str) -> dict:
    """대표 선정 결과를 기사에 못 박는다 — 진단 화면이 읽는 유일한 흔적."""
    article["story_display_hash"] = str(article.get("hash") or "")
    article["story_display_candidates"] = max(1, int(candidates))
    if reason and not (reason.startswith("keep") and article.get("story_display_reason")):
        article["story_display_reason"] = _clean(
            "story 완성 후 유지" if reason == "keep"
            else "점수 차가 커서 유지" if reason == "keep_score_gap"
            else reason)[:200]
    return article
