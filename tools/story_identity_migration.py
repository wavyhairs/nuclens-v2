"""Audit/rebuild plan for legacy story identity state.

The default mode is read-only.  It joins delivery history to immutable archive rows by
article hash, audits a recent replay window, and emits a deterministic split plan for
only identities with concrete conflicts.  Applying source-data changes is intentionally
separate: ambiguous legacy URLs cannot be redirected safely without reviewing the plan.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import story_identity  # noqa: E402


def _jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_replay_rows(root: Path, days: int) -> list[dict]:
    deliveries = [row for row in _jsonl(root / "delivery_log.jsonl")
                  if not row.get("record_type") and row.get("hash")]
    latest = max((str(row.get("date") or "")[:10] for row in deliveries), default="")
    if latest:
        cutoff = (date.fromisoformat(latest) - timedelta(days=max(0, days - 1))).isoformat()
        deliveries = [row for row in deliveries if str(row.get("date") or "")[:10] >= cutoff]

    archive_by_hash: dict[str, dict] = {}
    for path in sorted((root / "archive").glob("*.jsonl")):
        for row in _jsonl(path):
            article_hash = str(row.get("hash") or "")
            if article_hash:
                archive_by_hash[article_hash] = row
    out = []
    for delivery in deliveries:
        archive = archive_by_hash.get(str(delivery.get("hash") or ""), {})
        out.append({**archive, **delivery})
    return out


def build_report(root: Path, days: int) -> dict:
    rows = load_replay_rows(root, days)
    audit = story_identity.audit_registry(rows)
    split_hashes = _conflicted_non_owner_hashes(audit["conflicts"])
    split_plan = []
    for row in rows:
        story_id = str(row.get("story_id") or "")
        article_hash = str(row.get("hash") or "")
        if article_hash not in split_hashes.get(story_id, set()):
            continue
        split_plan.append({
            "legacy_story_id": story_id,
            "article_hash": article_hash,
            "safe_story_id": f"story-{article_hash}",
            "alias_safe": False,
            "reason": "ambiguous_legacy_identity",
        })
    return {
        "mode": "dry-run",
        "window_days": days,
        "delivery_rows": len(rows),
        **audit,
        "split_plan": split_plan,
    }


def _conflicted_non_owner_hashes(conflicts: list[dict]) -> dict[str, set[str]]:
    """Choose the smallest safe split while preserving a compatible owner set.

    A conflict ID can have no representative row in the replay window.  Treating
    every hash other than the ID suffix as a non-owner then destroys normal
    follow-ups (the live Zaporizhzhia pair exposed this).  Instead, greedily retain
    a deterministic conflict-free set: prefer the hash named by the ID when present,
    then lower conflict degree, then lexical hash.  Only the remaining vertices are
    migrated.
    """
    edges_by_id: dict[str, set[frozenset[str]]] = {}
    nodes_by_id: dict[str, set[str]] = {}
    for conflict in conflicts:
        story_id = str(conflict.get("story_id") or "")
        left = str(conflict.get("left_hash") or "")
        right = str(conflict.get("right_hash") or "")
        if not story_id or not left or not right or left == right:
            continue
        edges_by_id.setdefault(story_id, set()).add(frozenset((left, right)))
        nodes_by_id.setdefault(story_id, set()).update((left, right))

    split_by_id: dict[str, set[str]] = {}
    for story_id, nodes in nodes_by_id.items():
        edges = edges_by_id[story_id]
        named_owner = story_id.removeprefix("story-")
        degree = {
            node: sum(node in edge for edge in edges)
            for node in nodes
        }
        ordered = sorted(
            nodes,
            key=lambda node: (node != named_owner, degree[node], node),
        )
        keep: list[str] = []
        for node in ordered:
            if any(frozenset((node, owner)) in edges for owner in keep):
                continue
            keep.append(node)
        split_by_id[story_id] = nodes - set(keep)
    return split_by_id


def apply_delivery_migration(root: Path, report: dict) -> int:
    """Split only non-owner rows of concretely conflicted legacy IDs, atomically."""
    conflict_ids = {row["story_id"] for row in report["conflicts"]}
    split_by_id: dict[str, set[str]] = {}
    for row in report.get("split_plan") or []:
        split_by_id.setdefault(str(row.get("legacy_story_id") or ""), set()).add(
            str(row.get("article_hash") or ""))
    if not split_by_id:
        split_by_id = _conflicted_non_owner_hashes(report["conflicts"])
    path = root / "delivery_log.jsonl"
    original = path.read_text(encoding="utf-8")
    output: list[str] = []
    changed = 0
    for line in original.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            output.append(line)
            continue
        article_hash = str(row.get("hash") or "") if isinstance(row, dict) else ""
        safe_id = f"story-{article_hash}" if article_hash else ""
        current_id = str(row.get("story_id") or "") if isinstance(row, dict) else ""
        legacy_id = str(row.get("legacy_story_id") or "") if isinstance(row, dict) else ""
        effective_id = current_id if current_id in conflict_ids else legacy_id
        should_split = article_hash in split_by_id.get(effective_id, set())
        if (isinstance(row, dict) and not row.get("record_type") and should_split
                and safe_id and current_id != safe_id):
            row["legacy_story_id"] = effective_id
            row["story_id"] = safe_id
            row["story_id_source"] = "migration_forced_split"
            row["story_id_trust"] = "canonical"
            row["story_identity_version"] = story_identity.IDENTITY_VERSION
            row["story_identity_decision"] = "legacy_conflict_forced_split"
            line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            changed += 1
        elif (isinstance(row, dict) and legacy_id in conflict_ids and not should_split
              and row.get("story_identity_decision") == "legacy_conflict_forced_split"):
            # Self-heal output from the pre-owner-set migration implementation.
            row["story_id"] = legacy_id
            continuity = row.get("continuity") if isinstance(row.get("continuity"), dict) else {}
            row["story_id_source"] = (
                "history" if continuity.get("identity_confirmed")
                and continuity.get("story_id") == legacy_id else "generated"
            )
            for key in ("legacy_story_id", "story_id_trust", "story_identity_version",
                        "story_identity_decision"):
                row.pop(key, None)
            line = json.dumps(row, ensure_ascii=False)
            changed += 1
        output.append(line)
    if changed:
        temporary = path.with_suffix(path.suffix + ".story-identity.tmp")
        temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
        temporary.replace(path)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write-plan", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_report(args.root.resolve(), max(1, args.days))
    if args.apply:
        changed = apply_delivery_migration(args.root.resolve(), report)
        report["mode"] = "apply"
        report["delivery_rows_corrected"] = changed
    if args.write_plan:
        args.write_plan.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        keys = (
            "delivery_rows", "legacy_story_count", "preservable_story_count",
            "identity_conflict_story_count", "country_conflict_count",
            "facility_conflict_count", "project_actor_conflict_count",
            "heterogeneous_story_count", "automatic_split_count",
            "uncertain_story_count",
        )
        for key in keys:
            print(f"{key}={report[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
