"""Production-shaped Curation replay support without production side effects.

The existing Curation Gold labels judge ``current_output``.  They are not labels
for a newly generated output, so this module deliberately refuses to score a
replay with ``human_label``.  A replay becomes scorable only after a human adds
the separate ``replay_human_label`` field.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llm_policy
import news_bot

REPLAY_POLICY_VERSION = 1
OLD_CHECKPOINT_STATUS = "INVALIDATED_BY_EVALUATOR_CHANGE"
HUMAN_LABEL_REQUIRED = "HUMAN_LABEL_REQUIRED"


def article_from_case(case: dict) -> dict:
    """Translate stored source fields, never generated output, into an article."""
    source = case.get("source_input") or {}
    url = source.get("url") or case.get("source_url") or ""
    title = source.get("title") or case.get("source_title") or ""
    source_hash = case.get("source_hash") or hashlib.sha256(
        url.encode("utf-8")).hexdigest()
    return {
        "hash": source_hash,
        "title": title,
        "description": source.get("description") or "",
        "url": url,
        "published_at": source.get("published_at") or case.get("published_at") or "",
        "publisher": source.get("publisher") or "",
        "domain": source.get("domain") or urlparse(url).netloc.lower(),
    }


def source_body(case: dict) -> str:
    source = case.get("source_input") or {}
    return str(source.get("body") or "")


def readiness(case: dict) -> dict:
    """Explain whether a replay can be generated and validly Human-scored."""
    article = article_from_case(case)
    has_source_context = bool(article["description"].strip() or source_body(case).strip())
    replay_labelled = (
        case.get("replay_human_label") in {"PASS", "REPAIR", "BLOCK"}
        and case.get("replay_label_status") in {"USER_SPECIFIED", "HUMAN_LABELLED"}
    )
    reasons = []
    if not article["title"] or not article["hash"]:
        reasons.append("SOURCE_IDENTITY_MISSING")
    if not has_source_context:
        reasons.append("SOURCE_DESCRIPTION_OR_BODY_MISSING")
    if not replay_labelled:
        reasons.append("REPLAY_OUTPUT_HUMAN_LABEL_MISSING")
    source_identity_ready = not any(
        reason == "SOURCE_IDENTITY_MISSING" for reason in reasons)
    return {
        "case_id": case.get("id"),
        "can_construct_request": source_identity_ready,
        # A title-only request is executable, but it cannot reproduce the
        # historical production input whose description/body was discarded.
        "can_generate": source_identity_ready and has_source_context,
        "can_score": not reasons,
        "status": "READY" if not reasons else HUMAN_LABEL_REQUIRED,
        "reasons": reasons,
        "existing_human_label_scope": "current_output_only",
    }


def request_for_case(case: dict, reports_kb: list[dict] | None = None) -> tuple[str, str]:
    article = article_from_case(case)
    body = source_body(case)
    bodies = {article["hash"]: body} if body else None
    return news_bot.build_curation_batch_request(
        [article], reports_kb or [], bodies=bodies)


def parse_case_response(case: dict, response: object) -> tuple[dict, list[str]]:
    article = article_from_case(case)
    body = source_body(case)
    bodies = {article["hash"]: body} if body else None
    valid, failures = news_bot.parse_curation_batch_response(
        response, [article], bodies=bodies)
    return valid.get(article["hash"], {}), failures.get(article["hash"], [])


def call_options(config: str = "unspecified") -> dict:
    if config in {"unspecified", "none", "current"}:
        reasoning = {}
    else:
        level = config.removeprefix("level:")
        if level not in {"minimal", "low", "medium", "high"}:
            raise ValueError(f"unsupported reasoning config: {config}")
        reasoning = {"thinking_level": level}
    return {
        "temperature": 0.2,
        "max_output_tokens": news_bot.BATCH_MAX_OUTPUT_TOKENS,
        "timeout": 150.0,
        "retries": 3,
        "model": llm_policy.profile("curation").model(),
        **reasoning,
    }


def policy_fingerprint(case: dict, config: str = "unspecified") -> str:
    system, user = request_for_case(case)
    payload = {
        "version": REPLAY_POLICY_VERSION,
        "system": system,
        "user": user,
        "call_options": call_options(config),
        "parser": "news_bot.parse_curation_batch_response",
        "cache": "isolated_evaluation_only",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def audit_fixture(payload: dict) -> dict:
    rows = [readiness(case) for case in payload.get("cases") or []]
    return {
        "replay_policy_version": REPLAY_POLICY_VERSION,
        "prior_checkpoint_status": OLD_CHECKPOINT_STATUS,
        "cases": len(rows),
        "generation_ready": sum(row["can_generate"] for row in rows),
        "evaluation_ready": sum(row["can_score"] for row in rows),
        "human_label_required": sum(not row["can_score"] for row in rows),
        "rows": rows,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = audit_fixture(json.loads(args.fixtures.read_text(encoding="utf-8")))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
