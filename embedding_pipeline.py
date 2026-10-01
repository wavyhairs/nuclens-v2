"""Nuclens 이슈 매칭용 Gemini 임베딩 캐시 계약.

수집기의 의미 중복 제거와 웹의 21일 이슈 연결이 같은 모델·텍스트·캐시
형식을 사용하도록 한곳에 모은다. 캐시는 Git이 아니라 Actions cache로 보존한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import embedding_quota
from embedding_quota import EmbeddingCallsSuspended, EmbeddingQuotaError


EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2")
EMBEDDING_DIMENSION = int(os.environ.get("GEMINI_EMBEDDING_DIMENSION", "768"))


def _positive_int_env(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# 이슈 창과 **보존 기간은 같이 움직여야 한다.** 예전에는 `35 # 21일 이슈 창 + 여유`
# 라고 손으로 적혀 있었는데, 그 결합이 주석에만 있으면 창을 넓히는 순간 조용히
# 깨진다. 벡터가 없으면 `issue_review.in_review_band` 가 `embedding_similarity=None`
# 으로 False 를 돌려주고, 그 쌍은 회색지대에 **들어가지도 못한다** — 창을 넓힌
# 효과가 아니라 보존이 끊긴 효과를 재게 된다.
#
# 기본값은 21 + 14 = 35 로 **지금과 같다.** 창을 옮기면 보존이 따라온다.
ISSUE_WINDOW_DAYS = _positive_int_env("NUCLENS_ISSUE_WINDOW_DAYS", 21)
# 여유 14일의 근거: 벡터는 기사가 창 안에 있는 동안 계속 필요한데, 백필이 놓친
# 회차(API 장애·쿼터)를 다음 실행이 따라잡을 시간이 있어야 한다. 여유가 0이면
# 창 끝자락 기사가 매 빌드 재생성 대상이 되어 호출이 는다.
EMBEDDING_RETENTION_MARGIN_DAYS = 14
EMBEDDING_RETENTION_DAYS = _positive_int_env(
    "EMBEDDING_RETENTION_DAYS", ISSUE_WINDOW_DAYS + EMBEDDING_RETENTION_MARGIN_DAYS
)
DEFAULT_CACHE_FILE = Path("embeddings.json")


def embedding_text(article: dict) -> str:
    """기사별로 안정적인 이슈 표현을 만든다."""
    title = str(article.get("title_kr") or article.get("title") or "").strip()
    summary = str(article.get("summary") or article.get("description") or "").strip()
    tags = " ".join(str(tag).lstrip("#") for tag in article.get("tags") or [])
    topics = " ".join(str(topic) for topic in article.get("topics") or [])
    return (
        "원자력 뉴스의 동일 사건·후속 보도 군집화를 위한 표현입니다.\n"
        f"제목: {title}\n요약: {summary[:600]}\n태그: {tags}\n주제: {topics}"
    )


def text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_cache(path: Path = DEFAULT_CACHE_FILE) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def cached_vector(entry: object, *, model: str = EMBEDDING_MODEL) -> list[float] | None:
    """현행 모델의 정상 벡터만 반환한다.

    구형 캐시는 모델 메타가 없고 이미 서로 다른 벡터 공간일 수 있으므로 새
    모델과 섞지 않는다. 웹 쪽 로더도 같은 규칙을 사용한다.
    """
    if not isinstance(entry, dict) or entry.get("model") != model:
        return None
    vector = entry.get("vec")
    if not isinstance(vector, list) or not vector:
        return None
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector):
        return None
    return [float(value) for value in vector]


def cache_entry_is_current(entry: object, text: str) -> bool:
    vector = cached_vector(entry)
    return bool(vector and entry.get("text_fingerprint") == text_fingerprint(text))


def prune_cache(cache: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=EMBEDDING_RETENTION_DAYS)
    kept = {}
    for key, entry in cache.items():
        if cached_vector(entry) is None:
            continue
        try:
            cached_at = datetime.fromisoformat(str(entry.get("cached_at") or ""))
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if cached_at >= cutoff:
            kept[str(key)] = entry
    return kept


def save_cache(cache: dict, path: Path = DEFAULT_CACHE_FILE) -> None:
    cache = prune_cache(cache)
    path.write_text(
        json.dumps(cache, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def limiter() -> embedding_quota.EmbeddingLimiter:
    """이 프로세스의 임베딩 제한기. 수집 중복 판별과 백필이 같은 것을 쓴다."""
    return embedding_quota.get_limiter(EMBEDDING_MODEL)


def _embed_config(types):
    config = {"output_dimensionality": EMBEDDING_DIMENSION}
    # 재시도는 embedding_quota 한 곳에서만 한다. google-genai 는 클라이언트에
    # retry_options 가 없으면 재시도하지 않지만(2.26 기준 stop_after_attempt(1)),
    # 기본값이 바뀌면 SDK 재시도(최대 5회, 429 포함)가 바깥 재시도와 곱해지고
    # 그 요청들은 우리 60초 창에 잡히지도 않는다. 요청 단위로 1회를 못 박는다.
    # HttpRetryOptions 가 없는 구버전 SDK 는 애초에 재시도 기능이 없다.
    if hasattr(types, "HttpRetryOptions"):
        config["http_options"] = types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1))
    return types.EmbedContentConfig(**config)


def embed_one(client, text: str, *, gate: embedding_quota.EmbeddingLimiter | None = None,
              label: str = "") -> list[float]:
    """임베딩 API 요청 하나. 반드시 제한기를 지난다(재시도 포함)."""
    from google.genai import types

    config = _embed_config(types)
    result = (gate or limiter()).call(
        lambda: client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config=config,
        ),
        label=label,
    )
    if not result.embeddings:
        raise RuntimeError("embedding response is empty")
    vector = [float(value) for value in result.embeddings[0].values]
    if len(vector) != EMBEDDING_DIMENSION:
        raise RuntimeError(
            f"unexpected embedding dimension: {len(vector)} != {EMBEDDING_DIMENSION}"
        )
    return vector


def get_or_compute_embedding(
    client,
    article: dict,
    cache_key: str,
    cache: dict,
    *,
    gate: embedding_quota.EmbeddingLimiter | None = None,
) -> tuple[list[float] | None, bool]:
    """벡터와 신규 생성 여부를 반환한다.

    캐시가 현행이면 API 를 부르지 않는다 — 요청으로 세지도, 페이싱으로 기다리지도
    않는다. 회차 신규 호출이 멈췄으면 ``EmbeddingCallsSuspended`` 가 올라온다.
    """
    gate = gate or limiter()
    text = embedding_text(article)
    entry = cache.get(cache_key)
    if cache_entry_is_current(entry, text):
        gate.record_cache_hit()
        return cached_vector(entry), False
    if client is None:
        return None, False

    vector = embed_one(client, text, gate=gate, label=str(cache_key)[:8])
    cache[cache_key] = {
        "vec": vector,
        "model": EMBEDDING_MODEL,
        "dimension": len(vector),
        "text_fingerprint": text_fingerprint(text),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    return vector, True


def _read_jsonl(path: Path) -> Iterable[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def selected_articles(root: Path, window_days: int = 21) -> list[dict]:
    """최근 브리핑에 실제 사용된 기사만 아카이브에서 찾는다."""
    delivery_rows = list(_read_jsonl(root / "delivery_log.jsonl"))
    dated = []
    for row in delivery_rows:
        try:
            delivered = date.fromisoformat(str(row.get("date") or ""))
        except ValueError:
            continue
        if row.get("hash"):
            dated.append((delivered, str(row["hash"])))
    if not dated:
        return []
    latest = max(day for day, _ in dated)
    cutoff = latest - timedelta(days=max(1, window_days) - 1)
    wanted = {article_hash for day, article_hash in dated if day >= cutoff}

    by_hash = {}
    for path in sorted((root / "archive").glob("*.jsonl")):
        for row in _read_jsonl(path):
            article_hash = str(row.get("hash") or "")
            if article_hash in wanted:
                by_hash[article_hash] = row
    return [by_hash[key] for key in sorted(by_hash)]


def refresh_embeddings(
    articles: list[dict],
    cache: dict,
    client,
    *,
    max_new: int = 150,
    sleep_seconds: float = 0.0,
    cache_path: Path = DEFAULT_CACHE_FILE,
    gate: embedding_quota.EmbeddingLimiter | None = None,
) -> dict:
    """최근 브리핑 기사의 벡터를 채운다.

    속도는 ``embedding_quota`` 가 최근 60초 요청 수로 맞춘다. 예전의 고정
    ``sleep_seconds``(0.15초)는 실제 호출 수를 보지 않아 한도를 못 지켰다 — 인자는
    호환을 위해 남기되 기본 0 이다.
    """
    gate = gate or limiter()
    generated = 0
    failed = 0
    for article in articles:
        article_hash = str(article.get("hash") or "")
        if not article_hash:
            continue
        text = embedding_text(article)
        if cache_entry_is_current(cache.get(article_hash), text):
            gate.record_cache_hit()
            continue
        if generated >= max_new or gate.suspended:
            break
        try:
            _, created = get_or_compute_embedding(
                client, article, article_hash, cache, gate=gate)
            generated += int(created)
        except EmbeddingCallsSuspended:
            break  # 일일 한도·예산 소진. 남은 기사는 다음 실행이 캐시 위에서 잇는다.
        except EmbeddingQuotaError as exc:
            failed += 1  # 상세는 제한기가 이미 남겼다. 회차도 거기서 멈췄다.
            print(f"[embeddings] {article_hash[:8]} 생성 실패: 429 {exc.info.kind}")
        except Exception as exc:  # API 장애는 다음 실행에서 재시도한다.
            failed += 1
            print(f"[embeddings] {article_hash[:8]} 생성 실패: {type(exc).__name__}")
        if generated and generated % 10 == 0:
            save_cache(cache, cache_path)
        if sleep_seconds:
            time.sleep(sleep_seconds)

    save_cache(cache, cache_path)
    current = sum(
        1
        for article in articles
        if cache_entry_is_current(cache.get(str(article.get("hash") or "")), embedding_text(article))
    )
    return {
        "selected": len(articles),
        "current": current,
        "generated": generated,
        "failed": failed,
        "coverage": round(current / len(articles), 4) if articles else 1.0,
        "model": EMBEDDING_MODEL,
        "dimension": EMBEDDING_DIMENSION,
        "stopped": gate.suspended,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="최근 브리핑 임베딩 캐시 백필")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    # 백필 창은 이슈 창을 따라간다. 둘이 어긋나면 창 안에 있는데 벡터가 없는
    # 기사가 생기고, 그 기사는 임베딩 경로에서 통째로 빠진다.
    parser.add_argument("--window-days", type=int, default=ISSUE_WINDOW_DAYS)
    parser.add_argument("--max-new", type=int, default=150)
    parser.add_argument("--require-nonzero", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    cache_path = root / DEFAULT_CACHE_FILE
    articles = selected_articles(root, args.window_days)
    api_key = os.environ.get("GEMINI_API_KEY", "")
    client = None
    if api_key:
        from google import genai

        client = genai.Client(api_key=api_key)
    stats = refresh_embeddings(
        articles,
        load_cache(cache_path),
        client,
        max_new=max(0, args.max_new),
        cache_path=cache_path,
    )
    print("[embeddings] " + json.dumps(stats, ensure_ascii=False))
    print(limiter().format_stats())
    if args.require_nonzero and articles and stats["current"] == 0:
        print("::error::최근 브리핑 임베딩이 0건입니다.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
