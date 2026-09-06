"""Create deterministic human-label queues without treating cached LLM answers as Gold."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bin(similarity: float) -> str:
    lower = min(0.90, 0.84 + int(max(0.0, similarity - 0.84) / 0.02) * 0.02)
    return f"{lower:.2f}-{lower + 0.02:.2f}"


def build_identity_candidates(cache: dict, *, per_stratum: int = 18,
                              seed: str = "nuclens-reasoning-v1") -> list[dict]:
    strata: dict[str, list[tuple[str, dict]]] = {}
    for pair_id, row in (cache.get("reviews") or {}).items():
        try:
            similarity = float(row["embedding_similarity"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0.84 <= similarity < 0.92:
            continue
        prior = bool(row.get("same_event"))
        stratum = f"similarity:{_bin(similarity)}|prior_llm:{str(prior).lower()}"
        strata.setdefault(stratum, []).append((pair_id, row))

    selected: list[dict] = []
    for stratum, rows in sorted(strata.items()):
        rows.sort(key=lambda item: hashlib.sha256(
            f"{seed}|{item[0]}".encode("utf-8")).hexdigest())
        for pair_id, row in rows[:per_stratum]:
            selected.append({
                "id": pair_id,
                "left_title": row.get("left_title") or "",
                "right_title": row.get("right_title") or "",
                "embedding_similarity": row.get("embedding_similarity"),
                "candidate_stratum": stratum,
                "prior_llm_verdict_not_gold": row.get("same_event"),
                "prior_llm_reason_not_gold": row.get("reason") or "",
                "human_label": None,
                "reason_code": None,
                "label_status": "HUMAN_LABEL_REQUIRED",
            })
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=ROOT / "issue_llm_reviews.json")
    parser.add_argument(
        "--out", type=Path,
        default=ROOT / "tests/fixtures/gemini_reasoning/identity_candidates.json")
    parser.add_argument("--per-stratum", type=int, default=18)
    args = parser.parse_args()
    cache = json.loads(args.cache.read_text(encoding="utf-8"))
    cases = build_identity_candidates(cache, per_stratum=args.per_stratum)
    payload = {
        "schema_version": 1,
        "task": "IDENTITY_REVIEW",
        "label_contract": ["MERGE", "SEPARATE", "AMBIGUOUS"],
        "warning": "Cached Gemini verdicts are context only and MUST NOT become Gold labels.",
        "cases": cases,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"identity candidates: {len(cases)} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
