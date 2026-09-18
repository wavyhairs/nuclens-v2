"""Validate the deployed news manifest and every referenced shard."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request


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


def check(site_url: str) -> int:
    base = site_url.rstrip("/") + "/data/"
    cache_buster = f"?cb={int(time.time())}"
    headers = {"User-Agent": "nuclens-smoke/1.0"}

    def load(name: str):
        request = urllib.request.Request(base + name + cache_buster, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "")
            if "json" not in content_type:
                raise ValueError(f"{name}: Content-Type={content_type}")
            return json.load(response)

    return validate_manifest(load("news-manifest.json"), load)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("site_url")
    args = parser.parse_args()
    count = check(args.site_url)
    print(f"[news-smoke] OK articles={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
