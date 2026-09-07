"""Offline audit adapters for production Identity call paths.

No function in this module calls Gemini or reads/writes a production cache.  It
constructs the exact production request and invokes the production parser so a
later, separately authorized live checkpoint can use profile-appropriate data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dedup
import issue_review
import keei_match

PROFILES = ("issue_review", "keei_match", "dedup", "dedup_final", "dedup_legacy")
REPLAY_VERSION = 1


def _pair(case: dict) -> dict:
    return {
        "candidate_id": case.get("id"),
        "left_title": case.get("left_title") or (case.get("a") or {}).get("title") or "",
        "right_title": case.get("right_title") or (case.get("b") or {}).get("title") or "",
        "left_story_fingerprint": case.get("left_story_fingerprint"),
        "right_story_fingerprint": case.get("right_story_fingerprint"),
    }


def _article(side: dict) -> dict:
    return {
        **side,
        "hash": side.get("hash") or side.get("source_hash") or "",
        "title_kr": side.get("title_kr") or side.get("title") or "",
    }


def request(profile: str, case: dict) -> tuple[str, str, dict]:
    """Return production system/user text and behavior-relevant call options."""
    if profile == "issue_review":
        return issue_review.SYSTEM_PROMPT, issue_review.build_user_message([_pair(case)]), {
            "temperature": 0.0, "max_output_tokens": issue_review.MAX_OUTPUT_TOKENS,
            "model_profile": "issue_review", "cache": "isolated_no_read_no_write",
        }
    if profile == "keei_match":
        row = {"pair_id": case.get("id"),
               "issue_title": case.get("issue_title") or "",
               "keei_item": case.get("keei_item") or ""}
        return keei_match.SYSTEM_PROMPT, keei_match.build_user_message([row]), {
            "temperature": 0.0, "max_output_tokens": 8192,
            "model_profile": "keei_match", "cache": "isolated_no_read_no_write",
        }
    if profile in {"dedup", "dedup_final"}:
        articles = [_article(case.get("a") or {}), _article(case.get("b") or {})]
        system = (dedup.EDITORIAL_REDUNDANCY_PROMPT
                  if profile == "dedup_final" else dedup.ARTICLE_STORY_PROMPT)
        user = "\n\n---\n\n".join(dedup._article_block(i, article)
                                      for i, article in enumerate(articles))
        return system, user, {
            "temperature": 0.05, "max_output_tokens": 6144, "timeout": 120.0,
            "model_profile": profile, "cache": "none",
        }
    if profile == "dedup_legacy":
        clusters = [
            {"title": (case.get(side) or {}).get("title") or "",
             "meta": (case.get(side) or {}).get("summary") or ""}
            for side in ("a", "b")
        ]
        return dedup.DEDUP_SYSTEM_PROMPT, "\n".join(
            dedup._format_cluster_line(i, cluster) for i, cluster in enumerate(clusters)), {
                "temperature": 0.05, "max_output_tokens": 4096, "timeout": 90.0,
                "model_profile": "unregistered_legacy_dedup", "cache": "none",
            }
    raise ValueError(f"unknown Identity profile: {profile}")


def parse(profile: str, payload: object) -> str | None:
    """Map the exact production parser result to the Human Gold vocabulary."""
    if profile == "issue_review":
        row = issue_review._parse_response(payload, 1).get(0)
        return ("MERGE" if row[0] else "SEPARATE") if row else None
    if profile == "keei_match":
        row = keei_match._parse_response(payload, 1).get(0)
        return ("MERGE" if row[0] else "SEPARATE") if row else None
    if profile in {"dedup", "dedup_final"}:
        groups = dedup._parse_story_groups(payload, 2)
        together = any(set(group["indices"]) == {0, 1}
                       and group["relation"] in {"merge", "duplicate"}
                       for group in groups)
        return "MERGE" if together else "SEPARATE"
    if profile == "dedup_legacy":
        groups = payload.get("groups") if isinstance(payload, dict) else None
        if not isinstance(groups, list):
            return None
        together = any(isinstance(group, list) and set(group) == {0, 1}
                       for group in groups)
        return "MERGE" if together else "SEPARATE"
    raise ValueError(f"unknown Identity profile: {profile}")


def readiness(profile: str, case: dict) -> dict:
    reasons = []
    if profile == "issue_review":
        pair = _pair(case)
        if not pair["left_story_fingerprint"] or not pair["right_story_fingerprint"]:
            reasons.append("STORY_FINGERPRINTS_MISSING")
        reasons.append("ARTICLE_PAIR_IS_NOT_ISSUE_REVIEW_CANDIDATE_SNAPSHOT")
    elif profile == "keei_match":
        if not case.get("issue_title") or not case.get("keei_item"):
            reasons.append("KEEI_ROLE_SPECIFIC_INPUT_MISSING")
    elif profile in {"dedup", "dedup_final"}:
        for side in (case.get("a") or {}, case.get("b") or {}):
            if not isinstance(side.get("features"), dict):
                reasons.append("PRODUCTION_ARTICLE_FEATURES_MISSING")
                break
        reasons.append("PRODUCTION_BATCH_CONTEXT_MISSING")
    elif profile == "dedup_legacy":
        reasons.append("PRODUCTION_CLUSTER_BATCH_CONTEXT_MISSING")
    else:
        raise ValueError(f"unknown Identity profile: {profile}")
    return {
        "profile": profile,
        "case_id": case.get("id"),
        "ready_for_live_replay": not reasons,
        "status": "READY" if not reasons else "PROFILE_SPECIFIC_INPUT_REQUIRED",
        "reasons": list(dict.fromkeys(reasons)),
        "human_gold_transfer": "relation_label_only",
    }


def audit_fixture(payload: dict) -> dict:
    cases = [case for case in payload.get("cases") or []
             if case.get("human_label") and case.get("label_status") in
             {"USER_SPECIFIED", "HUMAN_LABELLED"}]
    profiles = {}
    for profile in PROFILES:
        rows = [readiness(profile, case) for case in cases]
        profiles[profile] = {
            "human_gold": len(cases),
            "replay_ready": sum(row["ready_for_live_replay"] for row in rows),
            "status": ("READY" if rows and all(row["ready_for_live_replay"] for row in rows)
                       else "IMPLEMENTED_BUT_NOT_ACTIVATED"),
            "blockers": sorted({reason for row in rows for reason in row["reasons"]}),
        }
    return {"replay_version": REPLAY_VERSION, "live_calls": 0, "profiles": profiles}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.fixtures.read_text(encoding="utf-8"))
    print(json.dumps(audit_fixture(payload), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
