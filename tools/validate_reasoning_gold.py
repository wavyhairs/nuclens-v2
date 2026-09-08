"""Audit reasoning Gold candidates before they are handed to human labelers."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "gemini_reasoning"
ERROR_TYPES = {
    "FACT_ERROR", "NUMBER_ERROR", "ENTITY_ERROR", "DATE_ERROR", "SCOPE_ERROR",
    "STAGE_ERROR", "CAUSALITY_ERROR", "TEMPORAL_ERROR", "CERTAINTY_ERROR",
    "UNSUPPORTED_INFERENCE",
}
REQUIRED_EDGES = {
    "paks_continuity", "gori_lto_continuity", "saeul_stop_vs_project_period",
    "same_plant_different_stage", "same_meeting_multi_source",
    "same_policy_separate_announcement", "broader_issue_vs_specific_event",
}


# 사람이 값을 정한 상태들. 값이 있는데 이 중 하나가 아니면 출처를 모르는 라벨이다.
#
# ``HUMAN_REVIEWED_AI_ASSISTED`` 는 사람이 읽고 승인했지만 **AI 판정을 먼저 본 뒤의**
# 판단이다. 리뷰 UI 가 Sol 의 판정·확신도·근거를 케이스와 함께 보여 주고 한 키로
# 승인하게 했기 때문에, 이것은 독립 판정이 아니라 비준이다. 폐기하지 않는 이유는
# 사람이 실제로 읽었기 때문이고, 독립 Gold 와 같은 칸에 두지 않는 이유는 anchoring
# 크기를 아직 재지 않았기 때문이다(tools/gold_provenance.py).
INDEPENDENT_STATUSES = frozenset({"USER_SPECIFIED", "HUMAN_LABELLED"})
AI_ASSISTED_STATUS = "HUMAN_REVIEWED_AI_ASSISTED"
LABELLED_STATUSES = INDEPENDENT_STATUSES | {AI_ASSISTED_STATUS}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _label_errors(cases: list[dict], allowed: set[str], task: str) -> list[str]:
    errors: list[str] = []
    for case in cases:
        label = case.get("human_label")
        status = case.get("label_status")
        if label is None and status != "HUMAN_LABEL_REQUIRED":
            errors.append(f"{task}:{case.get('id')}: null label without HUMAN_LABEL_REQUIRED")
        if label is not None:
            if label not in allowed:
                errors.append(f"{task}:{case.get('id')}: invalid label {label!r}")
            if status not in LABELLED_STATUSES:
                errors.append(f"{task}:{case.get('id')}: labelled without human status")
    return errors


def audit(fixtures: Path) -> dict:
    identity_payload = _read(fixtures / "identity_candidates.json")
    identity = identity_payload["cases"]
    curation = _read(fixtures / "curation_gold.json")["cases"]
    semantic = _read(fixtures / "semantic_gold.json")["cases"]
    errors: list[str] = []
    warnings: list[str] = []

    if not 120 <= len(identity) <= 160:
        errors.append(f"identity count outside 120..160: {len(identity)}")
    if not 30 <= len(curation) <= 50:
        errors.append(f"curation count outside 30..50: {len(curation)}")
    if not 50 <= len(semantic) <= 100:
        errors.append(f"semantic count outside 50..100: {len(semantic)}")

    for name, cases in (("identity", identity), ("curation", curation),
                        ("semantic", semantic)):
        ids = [case.get("id") for case in cases]
        duplicates = [item for item, count in Counter(ids).items() if count > 1]
        if duplicates:
            errors.append(f"{name} duplicate ids: {duplicates[:5]}")

    errors += _label_errors(identity, {"MERGE", "SEPARATE", "AMBIGUOUS"}, "identity")
    errors += _label_errors(curation, {"PASS", "REPAIR", "BLOCK"}, "curation")
    errors += _label_errors(semantic, {"PASS", "REPAIR", "UNVERIFIABLE", "BLOCK"},
                            "semantic")

    unordered: set[tuple[str, str]] = set()
    identity_reasons = set(identity_payload.get("reason_codes") or [])
    for case in identity:
        if (case.get("label_status") == "HUMAN_LABEL_REQUIRED"
                and case.get("reason_code") is not None):
            errors.append(f"identity:{case.get('id')} pending reason_code must be null")
        if case.get("human_label") is not None:
            reason = case.get("reason_code")
            if not reason:
                errors.append(f"identity:{case.get('id')} labelled without reason_code")
            elif reason not in identity_reasons:
                errors.append(f"identity:{case.get('id')} invalid reason_code {reason!r}")
        cards = []
        for side in ("a", "b"):
            card = case.get(side) or {}
            key = f"{card.get('source_hash')}:{card.get('source_segment') or ''}"
            cards.append(key)
            for field in ("source_hash", "title", "published_at", "publisher", "url"):
                if not card.get(field):
                    errors.append(f"identity:{case.get('id')}:{side} missing {field}")
        pair = tuple(sorted(cards))
        if pair in unordered:
            errors.append(f"identity reverse/duplicate pair: {case.get('id')}")
        unordered.add(pair)
    found_edges = {case.get("metadata", {}).get("known_edge_case") for case in identity}
    missing_edges = sorted(REQUIRED_EDGES - found_edges)
    if missing_edges:
        errors.append(f"identity missing known edge cases: {missing_edges}")

    dimension_keys = {"event_boundary", "scope", "stage", "date", "causality"}
    for case in curation:
        for field in ("source_hash", "source_title", "source_url", "published_at",
                      "generated_title", "current_output", "human_dimensions"):
            if not case.get(field):
                errors.append(f"curation:{case.get('id')} missing {field}")
        if set((case.get("human_dimensions") or {}).keys()) != dimension_keys:
            errors.append(f"curation:{case.get('id')} dimension contract mismatch")
        if (case.get("label_status") == "HUMAN_LABEL_REQUIRED"
                and any(value is not None
                        for value in (case.get("human_dimensions") or {}).values())):
            errors.append(f"curation:{case.get('id')} pending dimensions must be null")
    regression = [case for case in curation
                  if case.get("source_hash") == "4da5b7ab6c225c78"]
    if len(regression) != 1 or regression[0].get("label_status") != "USER_SPECIFIED":
        errors.append("curation Saeul 4da5b7ab6c225c78 contract missing or altered")

    semantic_kinds = Counter(case.get("candidate_kind") for case in semantic)
    for kind in ("source_aligned", "controlled_perturbation", "unsupported_inference"):
        if semantic_kinds[kind] < 20:
            errors.append(f"semantic underrepresented kind {kind}: {semantic_kinds[kind]}")
    focuses = {case.get("selection_metadata_not_gold", {}).get("review_focus")
               for case in semantic}
    if missing := sorted(ERROR_TYPES - focuses):
        errors.append(f"semantic missing error-type focus: {missing}")
    for case in semantic:
        if (case.get("label_status") == "HUMAN_LABEL_REQUIRED"
                and case.get("human_error_types") is not None):
            errors.append(f"semantic:{case.get('id')} pending error types must be null")
        source = case.get("source_evidence") or {}
        if case.get("candidate_kind") == "user_specified_contract":
            if not source.get("contract_facts"):
                errors.append(f"semantic:{case.get('id')} missing contract facts")
        else:
            for field in ("source_hash", "original_title", "url", "published_at",
                          "verified_evidence"):
                if not source.get(field):
                    errors.append(f"semantic:{case.get('id')} missing source {field}")

    curation_domains = Counter(
        case.get("selection_metadata_not_gold", {}).get("domain") for case in curation)
    semantic_domains = Counter(case.get("domain") for case in semantic if case.get("domain"))
    if len(curation_domains) < 5:
        warnings.append(f"curation domain diversity is low: {dict(curation_domains)}")
    if curation_domains and max(curation_domains.values()) - min(curation_domains.values()) > 4:
        warnings.append(f"curation domain imbalance: {dict(curation_domains)}")
    if len(semantic_domains) < 5:
        warnings.append(f"semantic domain diversity is low: {dict(semantic_domains)}")

    identity_groups = Counter(
        case.get("metadata", {}).get("selection_group") for case in identity)
    curation_risks = Counter(
        risk for case in curation
        for risk in case.get("selection_metadata_not_gold", {}).get("risk_dimensions", []))
    if set(curation_risks) != dimension_keys:
        warnings.append(f"curation selection risks do not cover all dimensions: {dict(curation_risks)}")

    def label_summary(cases: list[dict]) -> dict:
        return {
            "candidate_count": len(cases),
            "human_labelled": sum(case.get("human_label") is not None for case in cases),
            "human_label_required": sum(
                case.get("label_status") == "HUMAN_LABEL_REQUIRED" for case in cases),
            "status": dict(Counter(case.get("label_status") for case in cases)),
        }

    return {
        "schema_version": 1,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "identity": {
            **label_summary(identity), "selection_groups": dict(identity_groups),
            "known_edge_cases": sorted(edge for edge in found_edges if edge),
            "unique_unordered_pairs": len(unordered),
        },
        "curation": {
            **label_summary(curation), "domains": dict(curation_domains),
            "risk_dimensions": dict(curation_risks),
        },
        "semantic": {
            **label_summary(semantic), "candidate_kinds": dict(semantic_kinds),
            "domains": dict(semantic_domains),
            "covered_error_types": sorted(ERROR_TYPES & focuses),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures-dir", type=Path, default=FIXTURES)
    parser.add_argument("--out", type=Path,
                        default=ROOT / "docs" / "gold-labeling" /
                        "gold-candidate-audit.json")
    args = parser.parse_args()
    report = audit(args.fixtures_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
