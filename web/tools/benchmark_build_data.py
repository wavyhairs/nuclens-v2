"""Run a reproducible, network-disabled Full Build benchmark.

The generated web payload stays in its normal ignored location.  Only the profile and
canonical semantic signature are written to the requested artifact directory.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True, help="Frozen ISO-8601 build clock")
    parser.add_argument("--label", required=True, help="Artifact filename prefix")
    parser.add_argument(
        "--artifact-dir", type=Path, default=ROOT / "web" / "_benchmark"
    )
    args = parser.parse_args()
    args.artifact_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update({
        "NUCLENS_BUILD_AS_OF": args.as_of,
        "NUCLENS_BUILD_PROFILE": str(args.artifact_dir / f"{args.label}.profile.json"),
        "NUCLENS_SEMANTIC_SIGNATURE": str(
            args.artifact_dir / f"{args.label}.semantic.json"
        ),
        "GENERATION_ID": f"benchmark-{args.label}",
        # Empty-but-present values prevent dotenv from restoring local credentials.
        "GEMINI_API_KEY": "",
        "GOOGLE_API_KEY": "",
    })
    completed = subprocess.run(
        [sys.executable, "-u", str(ROOT / "web" / "build_data.py")],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if completed.returncode:
        return completed.returncode
    profile_path = args.artifact_dir / f"{args.label}.profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    print(
        "[benchmark] "
        f"label={args.label} wall={profile['wall_seconds']:.3f}s "
        f"semantic={profile['semantic_signature_sha256']} "
        f"peak_rss={profile.get('peak_rss_bytes')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
