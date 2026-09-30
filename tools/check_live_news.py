"""Validate the deployed news manifest and every referenced shard."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# 배포 직후 manifest 와 shard 가 **서로 다른 세대**로 응답하는 창이 있다
# (2026-09-30 Daily Brief: 업로드는 정상이었는데 `news/000.json declared=2142
# actual=2161` 로 스모크가 떨어졌고, 뒤이은 전체 재배포에서는 재발하지 않았다).
# 지속 오류가 아니라 전파 지연이면 몇십 초 뒤 다시 물으면 맞아진다. 그래서
# 한 번의 불일치로 판정하지 않고 **전체 검사를 처음부터** 몇 번 다시 한다.
# 끝까지 안 맞으면 그때 실패다 — 지속 오류를 가리는 재시도가 아니다.
DEFAULT_ATTEMPTS = 4
DEFAULT_WAIT_SEC = 20.0
# 불일치가 났을 때 원인을 가를 증거. Cloudflare 캐시가 옛 세대를 준 것인지
# (CF-Cache-Status=HIT · Age>0), 원본 자체가 섞인 것인지를 사후에 구별하려면
# 응답 헤더가 로그에 남아 있어야 한다.
EVIDENCE_HEADERS = ("CF-Cache-Status", "Age", "ETag", "Last-Modified", "CF-Ray")


def validate_manifest(manifest: object, load_json) -> int:
    if not isinstance(manifest, dict) or manifest.get("schema") != "nuclens-news-shards-v1":
        raise ValueError("invalid news manifest schema")
    expected = int(manifest.get("count") or 0)
    count = 0
    shards = manifest.get("shards")
    if not isinstance(shards, list) or not shards:
        raise ValueError("news manifest has no shards")
    for descriptor in shards:
        name = str((descriptor or {}).get("file") or "")
        if not name.startswith("news/") or ".." in name or not name.endswith(".json"):
            raise ValueError(f"unsafe news shard path: {name!r}")
        rows = load_json(name)
        if not isinstance(rows, list):
            raise ValueError(f"news shard is not a list: {name}")
        declared = int((descriptor or {}).get("count") or 0)
        if len(rows) != declared:
            raise ValueError(
                f"news shard count mismatch: {name} declared={declared} actual={len(rows)}"
            )
        count += len(rows)
    if count != expected or count <= 0:
        raise ValueError(f"news total mismatch: declared={expected} actual={count}")
    return count


def check(site_url: str, evidence: dict | None = None) -> int:
    base = site_url.rstrip("/") + "/data/"
    cache_buster = f"?cb={int(time.time())}"
    headers = {"User-Agent": "nuclens-smoke/1.0"}

    def load(name: str):
        request = urllib.request.Request(base + name + cache_buster, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            if evidence is not None:
                evidence[name] = {key: response.headers.get(key)
                                  for key in EVIDENCE_HEADERS if response.headers.get(key)}
            content_type = response.headers.get("Content-Type", "")
            if "json" not in content_type:
                raise ValueError(f"{name}: Content-Type={content_type}")
            payload = json.load(response)
            if evidence is not None and isinstance(payload, dict) and payload.get("generation_id"):
                evidence[name]["generation_id"] = payload["generation_id"]
            return payload

    return validate_manifest(load("news-manifest.json"), load)


def check_with_retry(site_url: str, *, attempts: int = DEFAULT_ATTEMPTS,
                     wait_sec: float = DEFAULT_WAIT_SEC, sleep=time.sleep,
                     probe=check) -> int:
    """전파 지연을 넘기는 검사. 마지막 시도의 예외를 그대로 올린다.

    회복돼도 **처음 불일치는 경고로 남긴다** — 조용히 지나가면 이 현상이 얼마나
    자주 나는지, Cloudflare 쪽 원인인지를 다시는 셀 수 없다.
    """
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        evidence: dict = {}
        try:
            count = probe(site_url, evidence)
        except (ValueError, urllib.error.URLError, TimeoutError) as exc:
            shown = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
            if attempt == attempts:
                print(f"::error::[news-smoke] {attempts}회 모두 실패 — {exc} | headers={shown}",
                      file=sys.stderr)
                raise
            wait = wait_sec * attempt
            print(f"::warning::[news-smoke] 시도 {attempt}/{attempts} 실패 — {exc} "
                  f"| headers={shown} — {wait:.0f}초 뒤 다시 (배포 전파 지연 의심)",
                  file=sys.stderr)
            sleep(wait)
            continue
        if attempt > 1:
            print(f"::warning::[news-smoke] {attempt}번째 시도에서 회복 — 배포 직후 "
                  "세대가 잠시 섞였다(일시 전파 지연)", file=sys.stderr)
        return count
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("site_url")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS,
                        help="전체 검사 반복 상한 (1 = 재시도 없음)")
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT_SEC,
                        help="재시도 대기 기본값(초). n번째 실패 뒤 n배를 기다린다")
    args = parser.parse_args()
    count = check_with_retry(args.site_url, attempts=args.attempts, wait_sec=args.wait)
    print(f"[news-smoke] OK articles={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
