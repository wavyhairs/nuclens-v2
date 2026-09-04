"""Emit the exact V5.1 characterization payload for three-seed comparison."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import channel_queue
import daily_brief
import issue_continuity
import ranking
import story_identity
import weekly_bot
from v5_harness import canonical_json, load_fixture


def _load_build_data():
    spec = importlib.util.spec_from_file_location("v5_build_data", ROOT / "web" / "build_data.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load web/build_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def characterize() -> dict:
    fixture = load_fixture()

    identities = copy.deepcopy(fixture["identity_cases"])
    identity_ids = [story_identity.ensure(row) for row in identities]
    display_id = story_identity.display_group_id(identities[:3])

    rank_items = copy.deepcopy(fixture["ranking_items"])
    selected, rank_diag = ranking.rank_and_select(
        rank_items,
        3,
        ranking.load_config(),
        datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc),
    )

    continuity_items = copy.deepcopy(fixture["continuity"]["candidates"])
    continuity_diag = issue_continuity.annotate(
        continuity_items,
        copy.deepcopy(fixture["continuity"]["recent"]),
        ranking.load_config(),
        "2026-08-17",
    )

    build_data = _load_build_data()
    overrides = {
        "approved": set(fixture["admin_override"]["approved"]),
        "rejected": set(fixture["admin_override"]["rejected"]),
    }
    web_issues = build_data.cluster_selected_articles(
        copy.deepcopy(fixture["web_articles"]), copy.deepcopy(fixture["embeddings"]),
        copy.deepcopy(fixture["embeddings"]), overrides, []
    )

    weekly_rows = weekly_bot.weekly_stories(copy.deepcopy(fixture["web_articles"]))
    week_start, week_end = weekly_bot.week_window(
        datetime(2026, 9, 4, 8, 7, tzinfo=timezone.utc)
    )
    cfg = ranking.load_config()

    queue = {"schema_version": 1, "batches": []}
    batch = channel_queue.ensure_batch(
        queue, "daily-2026-09-04", "daily", "2026-09-04",
        now=datetime(2026, 9, 3, 19, 25, tzinfo=timezone.utc),
    )
    first_add = channel_queue.add_item(
        batch, {"kind": "text", "name": "국내", "text": "첫 본문"}
    )
    duplicate_add = channel_queue.add_item(
        batch, {"kind": "text", "name": "국내", "text": "수정 본문"}
    )

    jsonl_rows = fixture["jsonl_rows"]
    jsonl_bytes = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in jsonl_rows)

    return {
        "identity": {"ids": identity_ids, "rows": identities, "display_id": display_id},
        "ranking": {
            "selected_hashes": [row.get("hash") for row in selected],
            "selected": selected,
            "diagnostics": rank_diag,
        },
        "continuity": {"items": continuity_items, "diagnostics": continuity_diag},
        "web_issues": web_issues,
        "daily_core": {
            "region_by_hash": {row["hash"]: daily_brief.region(row)
                               for row in fixture["ranking_items"]},
            "selected_hashes": [row.get("hash") for row in selected],
        },
        "weekly_core": {
            "selected_story_hashes": [row.get("hash") for row in weekly_rows],
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "week_id": weekly_bot.week_id(week_end),
        },
        "thresholds": {
            "duplicate_similarity": cfg["duplicate_similarity"],
            "domestic_floor": ranking.resolve_floor(cfg, "domestic"),
            "overseas_floor": ranking.resolve_floor(cfg, "overseas"),
            "continuity_title_similarity": cfg["continuity"]["title_similarity"],
            "continuity_fingerprint_similarity": cfg["continuity"]["fingerprint_similarity"],
        },
        "channel_transition": {
            "first_add": first_add,
            "duplicate_add": duplicate_add,
            "queue": queue,
        },
        "jsonl_bytes": jsonl_bytes,
        "time_boundaries": fixture["time_boundaries"],
    }


if __name__ == "__main__":
    print(canonical_json(characterize()))
