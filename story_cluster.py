"""Story-level metadata consolidation for daily nuclear news selection.

This module is deliberately deterministic and contains no LLM calls.  It is shared by
``ranking.py`` (fast title/facility duplicate detection) and ``dedup.py`` (Gemini story
clustering) so every duplicate path produces the same coverage metadata.

The key distinction is between an *article* and a *briefing story*: multiple articles can
cover the same underlying development.  We retain the best representative article while
preserving the other outlets/titles/evidence as metadata instead of silently discarding it.
"""

from __future__ import annotations

from urllib.parse import urlparse


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


def _source_records(article: dict) -> list[dict]:
    """Expand an article plus any story metadata it already inherited from prior dedup."""
    existing = article.get("story_sources")
    if isinstance(existing, list) and existing:
        out = []
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
        if out:
            return out

    return [{
        "identity": source_identity(article),
        "publisher": source_label(article),
        "domain": _clean(article.get("domain"))[:120],
        "tier": source_tier(article),
        "evidence_role": _clean(article.get("evidence_role"))[:40],
    }]


def _article_hashes(article: dict) -> list[str]:
    vals = article.get("story_article_hashes")
    if isinstance(vals, list) and vals:
        return [str(v) for v in vals if str(v)]
    h = str(article.get("hash") or "")
    return [h] if h else []


def _related_titles(article: dict) -> list[str]:
    out = []
    vals = article.get("story_related_titles")
    if isinstance(vals, list):
        out.extend(_clean(v)[:180] for v in vals if _clean(v))
    title = _clean(article.get("title_kr") or article.get("title"))[:180]
    if title:
        out.append(title)
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

    representative["story_article_count"] = max(article_count, len(hashes), 1)
    representative["story_article_hashes"] = hashes
    representative["story_outlet_count"] = len(source_list)
    representative["story_tier1_count"] = tier1_count
    representative["story_independent_outlet_count"] = independent_count
    representative["story_sources"] = source_list
    representative["story_related_titles"] = titles
    representative["story_context"] = ctx_out
    existing_relation = str(representative.get("story_relation") or "")
    if relation == "single" and existing_relation in {"duplicate", "merge"}:
        representative["story_relation"] = existing_relation
    else:
        representative["story_relation"] = relation or existing_relation or "duplicate"
    if reason:
        representative["story_reason"] = _clean(reason)[:300]
    if stage:
        representative["story_dedup_stage"] = stage
    if isinstance(fingerprint, dict) and fingerprint:
        representative["story_fingerprint"] = fingerprint
    return representative
