"""Classify a web change as SKIP, FAST, or FULL.

The classifier is intentionally conservative.  FAST is an allow-list: only files
that can consume an already-published data snapshot are admitted.  Unknown web
Python files fall back to FULL instead of risking a UI/data contract mismatch.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


FAST_PREFIXES = (
    "functions/",
    "web/public/",
)
FAST_EXACT = {
    ".github/workflows/deploy-web.yml",
    "tools/render_static_pages.py",
    "tools/web_deploy_mode.py",
    "tools/web_snapshot.py",
}
FULL_EXACT = {
    "entity_registry.json",
    "weekly_reports.json",
    "web/build_data.py",
    "web/publication_policy.py",
    "web/publication_title.py",
}
SKIP_PREFIXES = (
    "docs/",
    "web/brand/",
    "web/tests/",
    "web/tools/",
)
SKIP_EXACT = {
    "web/README.md",
}


def normalize(path: str) -> str:
    return path.strip().replace("\\", "/").removeprefix("./")


def classify(paths: list[str]) -> tuple[str, list[str]]:
    files = sorted({normalize(path) for path in paths if normalize(path)})
    if not files:
        return "full", ["변경 목록 없음 — 수동/재사용 호출은 안전하게 full"]

    reasons: list[str] = []
    saw_fast = False
    for path in files:
        if path in FULL_EXACT:
            reasons.append(f"full 입력: {path}")
            return "full", reasons
        if path in FAST_EXACT or path.startswith(FAST_PREFIXES):
            saw_fast = True
            reasons.append(f"fast 입력: {path}")
            continue
        if path in SKIP_EXACT or path.startswith(SKIP_PREFIXES):
            reasons.append(f"배포 무관: {path}")
            continue
        if path.startswith("web/"):
            reasons.append(f"알 수 없는 web 입력: {path}")
            return "full", reasons
        reasons.append(f"배포 무관: {path}")
    return ("fast" if saw_fast else "skip"), reasons


def changed_files(base: str, head: str) -> list[str]:
    if not base or set(base) == {"0"}:
        base = f"{head}^"
    result = subprocess.run(
        ["git", "diff", "--name-only", base, head],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.splitlines()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*")
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--mode-hint", choices=("auto", "skip", "fast", "full"),
                        default="auto")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = parser.parse_args()

    if args.mode_hint != "auto":
        mode, reasons = args.mode_hint, [f"호출자가 mode={args.mode_hint} 지정"]
        files = []
    else:
        files = args.files or changed_files(args.base, args.head)
        mode, reasons = classify(files)

    print(f"[web-deploy-mode] {mode.upper()}")
    for reason in reasons:
        print(f"  - {reason}")
    if args.github_output:
        output = Path(args.github_output)
        with output.open("a", encoding="utf-8") as handle:
            handle.write(f"mode={mode}\n")
            handle.write(f"changed_count={len(files)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
