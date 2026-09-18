"""Create, verify, and restore the last production web-data snapshot."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_SCHEMA = "nuclens-production-web-snapshot-v1"
DATA_CONTRACT_VERSION = 1
META_NAME = "snapshot.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != META_NAME):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _module_path(module: str) -> Path | None:
    candidate = ROOT.joinpath(*module.split(".")).with_suffix(".py")
    if candidate.is_file():
        return candidate
    package = ROOT.joinpath(*module.split("."), "__init__.py")
    return package if package.is_file() else None


def builder_sources(entry: Path | None = None) -> list[Path]:
    pending = [(entry or ROOT / "web" / "build_data.py").resolve()]
    found: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in found or not path.is_file():
            continue
        found.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            for name in names:
                resolved = _module_path(name)
                if resolved is not None:
                    pending.append(resolved.resolve())
    return sorted(found)


def builder_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in builder_sources():
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"object JSON expected: {path}")
    return value


def _live_meta(site_url: str) -> dict:
    url = f"{site_url.rstrip('/')}/data/meta.json?cb={int(datetime.now().timestamp())}"
    request = urllib.request.Request(url, headers={"User-Agent": "nuclens-snapshot/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _wait_for_live_generation(site_url: str, generation_id: str,
                              attempts: int = 6, delay_seconds: float = 5.0) -> str:
    last_generation = ""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            last_generation = str(_live_meta(site_url).get("generation_id") or "")
            last_error = None
            if last_generation == generation_id:
                return last_generation
        except Exception as exc:  # propagation can briefly return an edge/network error
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    detail = f"live={last_generation}" if last_error is None else f"error={last_error}"
    raise RuntimeError(
        f"live generation did not converge: local={generation_id} {detail}"
    )


def _copy_payload(public_dir: Path, snapshot_dir: Path) -> None:
    data = public_dir / "data"
    if not (data / "meta.json").is_file() or not (data / "_pages.json").is_file():
        raise FileNotFoundError("meta.json 또는 _pages.json 없는 산출물은 스냅샷으로 저장하지 않음")
    shutil.copytree(data, snapshot_dir / "data")
    admin_data = public_dir / "admin" / "data"
    if admin_data.is_dir():
        shutil.copytree(admin_data, snapshot_dir / "admin" / "data")
    rss = public_dir / "rss.xml"
    if rss.is_file():
        shutil.copy2(rss, snapshot_dir / "rss.xml")


def _source_commit(explicit: str) -> str:
    if explicit:
        return explicit
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


def create(public_dir: Path, snapshot_dir: Path, site_url: str, source_commit: str) -> dict:
    meta = _read_json(public_dir / "data" / "meta.json")
    contract_version = meta.get("data_contract_version")
    if contract_version != DATA_CONTRACT_VERSION:
        raise ValueError(
            "meta.json data_contract_version mismatch: "
            f"expected={DATA_CONTRACT_VERSION} actual={contract_version!r}"
        )
    generation_id = str(meta.get("generation_id") or "")
    if not generation_id:
        raise ValueError("meta.json generation_id 누락")
    if site_url:
        _wait_for_live_generation(site_url, generation_id)
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir(parents=True)
    _copy_payload(public_dir, snapshot_dir)
    metadata = {
        "schema": SNAPSHOT_SCHEMA,
        "data_contract_version": DATA_CONTRACT_VERSION,
        "generation_id": generation_id,
        "generated_at": meta.get("generated_at") or "",
        "build_mode": meta.get("build_mode") or "unknown",
        "source_commit": _source_commit(source_commit),
        "builder_fingerprint": builder_fingerprint(),
        "production_verified": bool(site_url),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "content_sha256": tree_digest(snapshot_dir),
    }
    (snapshot_dir / META_NAME).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return metadata


def inspect(snapshot_dir: Path) -> tuple[bool, list[str], dict]:
    reasons: list[str] = []
    path = snapshot_dir / META_NAME
    if not path.is_file():
        return False, ["snapshot.json 없음"], {}
    try:
        metadata = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, [f"snapshot.json 손상: {exc}"], {}
    if metadata.get("schema") != SNAPSHOT_SCHEMA:
        reasons.append("snapshot schema 불일치")
    if metadata.get("data_contract_version") != DATA_CONTRACT_VERSION:
        reasons.append("data contract version 불일치")
    if not metadata.get("production_verified"):
        reasons.append("production 검증 표식 없음")
    expected = str(metadata.get("content_sha256") or "")
    actual = tree_digest(snapshot_dir)
    if not expected or expected != actual:
        reasons.append("snapshot content hash 불일치")
    current_builder = builder_fingerprint()
    if metadata.get("builder_fingerprint") != current_builder:
        reasons.append("builder fingerprint 불일치")
    return not reasons, reasons, metadata


def restore(snapshot_dir: Path, public_dir: Path) -> dict:
    compatible, reasons, metadata = inspect(snapshot_dir)
    if not compatible:
        raise RuntimeError("호환되지 않는 production snapshot: " + "; ".join(reasons))
    for relative in (Path("data"), Path("admin") / "data"):
        target = public_dir / relative
        source = snapshot_dir / relative
        if target.exists():
            shutil.rmtree(target)
        if source.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
    rss_source = snapshot_dir / "rss.xml"
    rss_target = public_dir / "rss.xml"
    if rss_target.exists():
        rss_target.unlink()
    if rss_source.is_file():
        shutil.copy2(rss_source, rss_target)
    return metadata


def guard_live(public_dir: Path, site_url: str, policy: str) -> tuple[str, str]:
    local = str(_read_json(public_dir / "data" / "meta.json").get("generation_id") or "")
    live = str(_live_meta(site_url).get("generation_id") or "")
    if not local or not live:
        raise RuntimeError(f"generation_id 누락: local={local!r} live={live!r}")
    if policy == "equal" and local != live:
        raise RuntimeError(f"production snapshot != live: local={local} live={live}")
    if policy == "not-older" and local < live:
        raise RuntimeError(f"stale generation publish 차단: local={local} live={live}")
    print(f"[generation-guard] policy={policy} local={local} live={live}")
    return local, live


def _write_outputs(path: str, values: dict[str, object]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            if isinstance(value, bool):
                value = str(value).lower()
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    sub = parser.add_subparsers(dest="command", required=True)

    create_parser = sub.add_parser("create")
    create_parser.add_argument("--public-dir", type=Path, default=ROOT / "web" / "public")
    create_parser.add_argument("--snapshot-dir", type=Path, default=ROOT / "web" / "_production_snapshot")
    create_parser.add_argument("--site-url", default="")
    create_parser.add_argument("--source-commit", default="")

    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("--snapshot-dir", type=Path, default=ROOT / "web" / "_production_snapshot")

    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("--snapshot-dir", type=Path, default=ROOT / "web" / "_production_snapshot")
    restore_parser.add_argument("--public-dir", type=Path, default=ROOT / "web" / "public")

    guard_parser = sub.add_parser("guard-live")
    guard_parser.add_argument("--public-dir", type=Path, default=ROOT / "web" / "public")
    guard_parser.add_argument("--site-url", required=True)
    guard_parser.add_argument("--policy", choices=("equal", "not-older"), required=True)

    sub.add_parser("fingerprint")
    args = parser.parse_args()

    if args.command == "create":
        metadata = create(args.public_dir, args.snapshot_dir, args.site_url, args.source_commit)
        print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        _write_outputs(args.github_output, {
            "generation_id": metadata["generation_id"],
            "content_sha256": metadata["content_sha256"],
        })
    elif args.command == "inspect":
        compatible, reasons, metadata = inspect(args.snapshot_dir)
        print(json.dumps({"compatible": compatible, "reasons": reasons, **metadata},
                         ensure_ascii=False, sort_keys=True))
        _write_outputs(args.github_output, {
            "compatible": compatible,
            "generation_id": metadata.get("generation_id", ""),
        })
    elif args.command == "restore":
        metadata = restore(args.snapshot_dir, args.public_dir)
        print(f"[snapshot] restored generation={metadata['generation_id']}")
        _write_outputs(args.github_output, {"generation_id": metadata["generation_id"]})
    elif args.command == "guard-live":
        local, live = guard_live(args.public_dir, args.site_url, args.policy)
        _write_outputs(args.github_output, {"local_generation": local, "live_generation": live})
    else:
        print(builder_fingerprint())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # one concise Actions annotation
        print(f"::error::{exc}", file=sys.stderr)
        raise
