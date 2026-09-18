"""Keep only the newest GitHub Actions caches for one narrow key prefix."""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from dataclasses import dataclass


API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class CacheEntry:
    cache_id: int
    key: str
    created_at: str


def deletions(entries: list[CacheEntry], prefix: str, keep: int) -> list[CacheEntry]:
    matching = sorted(
        (entry for entry in entries if entry.key.startswith(prefix)),
        key=lambda entry: (entry.created_at, entry.cache_id),
        reverse=True,
    )
    return matching[max(0, keep):]


def _request(url: str, token: str, method: str = "GET") -> dict:
    request = urllib.request.Request(
        url,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "nuclens-cache-pruner/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    return json.loads(body) if body else {}


def list_caches(repository: str, token: str, prefix: str) -> list[CacheEntry]:
    base = f"https://api.github.com/repos/{repository}/actions/caches"
    page = 1
    result: list[CacheEntry] = []
    while True:
        payload = _request(
            f"{base}?per_page=100&page={page}", token
        )
        rows = payload.get("actions_caches") or []
        result.extend(
            CacheEntry(int(row["id"]), str(row["key"]), str(row["created_at"]))
            for row in rows
        )
        if len(rows) < 100:
            break
        page += 1
    return result


def prune(repository: str, token: str, prefix: str, keep: int, dry_run: bool) -> int:
    entries = list_caches(repository, token, prefix)
    doomed = deletions(entries, prefix, keep)
    for entry in doomed:
        print(f"[cache-prune] delete id={entry.cache_id} key={entry.key}")
        if not dry_run:
            _request(
                f"https://api.github.com/repos/{repository}/actions/caches/{entry.cache_id}",
                token,
                method="DELETE",
            )
    print(
        f"[cache-prune] prefix={prefix} found={len(entries)} "
        f"kept={min(keep, len(entries))} deleted={len(doomed)}"
    )
    return len(doomed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--keep", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.repository or not args.token:
        parser.error("--repository and --token (or GitHub environment variables) are required")
    if args.keep < 1:
        parser.error("--keep must be at least 1")
    prune(args.repository, args.token, args.prefix, args.keep, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
