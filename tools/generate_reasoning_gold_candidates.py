"""Build deterministic, source-backed human-label queues for reasoning evaluation.

Candidate metadata may contain cached/model opinions for sampling, but this module
never copies those opinions into a human label. Existing human labels are preserved
when a queue is regenerated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "gemini_reasoning"
LABEL_DOCS = ROOT / "docs" / "gold-labeling"

IDENTITY_LABELS = ["MERGE", "SEPARATE", "AMBIGUOUS"]
IDENTITY_REASONS = [
    "same_action", "same_meeting", "same_announcement",
    "multi_source_same_event", "follow_up_same_event", "same_entity_only",
    "different_stage", "different_unit", "different_action", "broader_topic",
    "different_time", "insufficient_context", "unclear_event_boundary", "other",
]
CURATION_DIMENSIONS = {
    "event_boundary": ["PASS", "FAIL", "AMBIGUOUS"],
    "scope": ["PASS", "FAIL"],
    "stage": ["PASS", "FAIL"],
    "date": ["PASS", "FAIL"],
    "causality": ["PASS", "FAIL", "UNVERIFIABLE"],
}
SEMANTIC_LABELS = ["PASS", "REPAIR", "UNVERIFIABLE", "BLOCK"]
ERROR_TYPES = [
    "FACT_ERROR", "NUMBER_ERROR", "ENTITY_ERROR", "DATE_ERROR", "SCOPE_ERROR",
    "STAGE_ERROR", "CAUSALITY_ERROR", "TEMPORAL_ERROR", "CERTAINTY_ERROR",
    "UNSUPPORTED_INFERENCE",
]
NUCLEAR_TOPICS = {
    "reactor_operation", "reactor_project", "grid", "power_market", "smr",
    "regulation", "waste", "fusion", "restart_lto", "fuel_cycle", "uranium",
    "supply_chain",
}
NUCLEAR_KEYWORDS = (
    "원전", "원자력", "핵연료", "핵융합", "방사성", "사용후핵연료", "전력",
    "송전", "배전", "smr", "nuclear", "reactor", "uranium", "fusion",
    "radioactive", "grid", "electricity", "plutonium", "enrichment",
)


def _stable(value: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}|{value}".encode("utf-8")).hexdigest()


def _bin(similarity: float) -> str:
    lower = min(0.90, 0.84 + int(max(0.0, similarity - 0.84) / 0.02) * 0.02)
    return f"{lower:.2f}-{lower + 0.02:.2f}"


def build_identity_candidates(cache: dict, *, per_stratum: int = 18,
                              seed: str = "nuclens-reasoning-v1") -> list[dict]:
    """Return cache-stratified candidates without promoting the cached verdict."""
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
        rows.sort(key=lambda item: _stable(item[0], seed))
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


def load_archive(archive_dir: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for path in sorted(archive_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON: {path}:{line_number}") from exc
                article_hash = row.get("hash")
                if article_hash:
                    records[article_hash] = row
    return records


def _published(row: dict) -> str:
    return (row.get("published_at") or row.get("pub") or row.get("archived_at") or "")


def _date_gap_days(a: dict, b: dict) -> int | None:
    try:
        left = datetime.fromisoformat(_published(a).replace("Z", "+00:00"))
        right = datetime.fromisoformat(_published(b).replace("Z", "+00:00"))
        return abs((left - right).days)
    except (TypeError, ValueError):
        return None


def _entities(row: dict) -> set[str]:
    return set((row.get("verified_evidence") or {}).get("entities") or [])


def _source_card(row: dict, *, title_override: str | None = None,
                 segment: str | None = None) -> dict:
    evidence = row.get("verified_evidence") or {}
    card = {
        "source_hash": row.get("hash") or "",
        "title": title_override or row.get("title_kr") or row.get("title") or "",
        "original_title": row.get("title") or "",
        "published_at": _published(row),
        "publisher": row.get("publisher") or row.get("domain") or "",
        "url": row.get("resolved_url") or row.get("url") or "",
        "summary": row.get("summary") or "",
        "source_type": row.get("source_type") or "unknown",
        "verified_manifest": evidence.get("manifest_fingerprint") or "",
    }
    if segment:
        card["source_segment"] = segment
    return card


def _domain(row: dict) -> str:
    tokens = set(row.get("topics") or [])
    verified = row.get("verified_evidence") or {}
    tokens.update(verified.get("topics") or [])
    if "grid" in tokens or "power_market" in tokens:
        return "power_market"
    if "smr" in tokens:
        return "smr"
    if "waste" in tokens or "fusion" in tokens:
        return "waste_or_fusion"
    event_type = str((row.get("features") or {}).get("event_type") or "")
    if event_type in {"policy_decision", "regulatory_action"} or "regulation" in tokens:
        return "policy"
    if tokens & {"reactor_operation", "reactor_project", "restart_lto"}:
        return "reactor"
    return "supply_chain_or_general"


def _load_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {case["id"]: case for case in payload.get("cases") or []}


def _preserve_human_fields(case: dict, old: dict | None, fields: Iterable[str]) -> dict:
    if not old or old.get("label_status") not in {"USER_SPECIFIED", "HUMAN_LABELLED"}:
        return case
    for field in fields:
        if field in old:
            case[field] = old[field]
    return case


def _identity_case(pair_id: str, archive: dict[str, dict], cache_row: dict | None,
                   *, selection_group: str, edge_case: str | None = None,
                   a_override: dict | None = None, b_override: dict | None = None) -> dict:
    a_hash, b_hash = pair_id.split("--", 1)
    a_row = archive[a_hash]
    b_row = archive[b_hash]
    a = a_override or _source_card(archive[a_hash])
    b = b_override or _source_card(archive[b_hash])
    similarity = cache_row.get("embedding_similarity") if cache_row else None
    prior = {
        "status": "REFERENCE_ONLY_NOT_GOLD" if cache_row else "NOT_AVAILABLE",
        "same_event": cache_row.get("same_event") if cache_row else None,
        "reason": cache_row.get("reason") if cache_row else None,
        "model": cache_row.get("model") if cache_row else None,
    }
    case = {
        "id": pair_id,
        "left_title": a["title"],
        "right_title": b["title"],
        "a": a,
        "b": b,
        "metadata": {
            "selection_group": selection_group,
            "known_edge_case": edge_case,
            "embedding_similarity": similarity,
            "date_gap_days": _date_gap_days(a_row, b_row),
            "shared_entities": sorted(_entities(a_row) & _entities(b_row)),
            "domains": sorted({_domain(a_row), _domain(b_row)}),
            "cached_review_not_gold": prior,
            "model_comparison": {"status": "NOT_EVALUATED", "disagreement": None},
        },
        # Backward-compatible fields used by the offline evaluator and old tooling.
        "embedding_similarity": similarity,
        "candidate_stratum": selection_group,
        "prior_llm_verdict_not_gold": prior["same_event"],
        "prior_llm_reason_not_gold": prior["reason"] or "",
        "human_label": None,
        "reason_code": None,
        "human_notes": None,
        "label_status": "HUMAN_LABEL_REQUIRED",
    }
    return case


def build_enriched_identity(cache: dict, archive: dict[str, dict], *, target: int = 150,
                            existing: dict[str, dict] | None = None) -> list[dict]:
    reviews = cache.get("reviews") or {}
    # Concrete regressions are pinned by source hash; no expected answer is encoded.
    pinned = [
        ("01d97e5a989b4f96--139ef867c400ece2", "paks_continuity"),
        ("756c2e6e80bbd36e--ad6ee254489e5bc3", "gori_lto_continuity"),
        ("27690c176d3ab1e1--f762199f53581f88", "same_meeting_multi_source"),
        ("b0cdd490f0e1afe3--ba855ef18b483232", "same_plant_different_stage"),
        ("27690c176d3ab1e1--2f08f02b160d0594", "same_policy_separate_announcement"),
        ("75854021de34f332--a152ea74be0b0e74", "broader_issue_vs_specific_event"),
    ]
    cases: list[dict] = []
    used: set[tuple[str, str]] = set()
    for pair_id, edge in pinned:
        a_hash, b_hash = pair_id.split("--", 1)
        if a_hash not in archive or b_hash not in archive:
            continue
        canonical = tuple(sorted((a_hash, b_hash)))
        used.add(canonical)
        cases.append(_identity_case(pair_id, archive, reviews.get(pair_id),
                                    selection_group="known_edge_case", edge_case=edge))

    mixed = archive.get("4da5b7ab6c225c78")
    if mixed:
        pair_id = "4da5b7ab6c225c78:stop--4da5b7ab6c225c78:project-period"
        a = _source_card(
            mixed,
            title_override="새울 3호기 시운전 중 자동정지",
            segment="source headline/body: reactor trip on 2026-08-11",
        )
        b = _source_card(
            mixed,
            title_override="새울 3·4호기 건설사업 시행기간 10개월 연장",
            segment="source headline/body: project-period change effective 2026-07-31",
        )
        cases.append(_identity_case(
            pair_id, {**archive, pair_id.split("--")[0]: mixed,
                      pair_id.split("--")[1]: mixed}, None,
            selection_group="known_edge_case",
            edge_case="saeul_stop_vs_project_period", a_override=a, b_override=b,
        ))
        used.add(tuple(sorted(pair_id.split("--", 1))))

    pool = build_identity_candidates(cache, per_stratum=30)
    pool.sort(key=lambda item: _stable(item["id"], "identity-enriched-v2"))
    for item in pool:
        if len(cases) >= target:
            break
        try:
            a_hash, b_hash = item["id"].split("--", 1)
        except ValueError:
            continue
        canonical = tuple(sorted((a_hash, b_hash)))
        if canonical in used or a_hash not in archive or b_hash not in archive:
            continue
        used.add(canonical)
        group = ("disagreement_probe" if len(cases) % 5 == 0 else "stratified_control")
        cases.append(_identity_case(item["id"], archive, reviews.get(item["id"]),
                                    selection_group=group))

    existing = existing or {}
    return [
        _preserve_human_fields(case, existing.get(case["id"]),
                               ("human_label", "reason_code", "human_notes", "label_status"))
        for case in cases
    ]


def _risk_dimensions(row: dict) -> list[str]:
    risks: list[str] = []
    evidence = row.get("verified_evidence") or {}
    if len(evidence.get("topics") or []) > 1 or len(evidence.get("stages") or []) > 1:
        risks.append("event_boundary")
    if evidence.get("entities") or (evidence.get("quantities") or {}).get("호기"):
        risks.append("scope")
    if evidence.get("stages"):
        risks.append("stage")
    if _published(row) or evidence.get("quantities"):
        risks.append("date")
    if row.get("implication") or row.get("why_important"):
        risks.append("causality")
    return risks or ["event_boundary"]


def _eligible_records(archive: dict[str, dict]) -> list[dict]:
    rows = []
    for row in archive.values():
        topics = set(row.get("topics") or [])
        topics.update((row.get("verified_evidence") or {}).get("topics") or [])
        source_text = " ".join(str(row.get(key) or "") for key in
                               ("title", "title_kr", "summary", "detail")).lower()
        if (row.get("hash") and row.get("url") and row.get("title")
                and row.get("title_kr") and _published(row)
                and topics & NUCLEAR_TOPICS
                and any(keyword in source_text for keyword in NUCLEAR_KEYWORDS)):
            rows.append(row)
    rows.sort(key=lambda row: _stable(row["hash"], "gold-source-v2"))
    return rows


def _round_robin_sources(archive: dict[str, dict], count: int, *, seed: str) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in _eligible_records(archive):
        groups[_domain(row)].append(row)
    for name, rows in groups.items():
        rows.sort(key=lambda row: _stable(row["hash"], f"{seed}|{name}"))
    order = ["reactor", "power_market", "policy", "smr", "waste_or_fusion",
             "supply_chain_or_general"]
    selected: list[dict] = []
    seen: set[str] = set()
    while len(selected) < count:
        progressed = False
        for name in order:
            rows = groups.get(name) or []
            while rows and rows[0]["hash"] in seen:
                rows.pop(0)
            if rows and len(selected) < count:
                row = rows.pop(0)
                selected.append(row)
                seen.add(row["hash"])
                progressed = True
        if not progressed:
            break
    return selected


def build_curation(archive: dict[str, dict], *, target: int = 40,
                   existing: dict[str, dict] | None = None) -> list[dict]:
    selected: list[dict] = []
    regression = archive["4da5b7ab6c225c78"]
    selected.append({
        "id": "saeul-title-merge-4da5b7ab6c225c78",
        "source_kind": "archive_regression",
        "source_hash": regression["hash"],
        "source_title": regression["title"],
        "source_url": regression["url"],
        "published_at": _published(regression),
        "source_input": {
            "title": regression["title"], "url": regression["url"],
            "published_at": _published(regression),
        },
        "generated_title": regression["title_kr"],
        "current_output": {key: regression.get(key) or "" for key in
                           ("title_kr", "summary", "detail", "implication", "why_important")},
        "selection_metadata_not_gold": {
            "risk_dimensions": ["event_boundary", "scope", "stage", "causality"],
            "domain": _domain(regression),
        },
        "human_label": "REPAIR",
        "human_dimensions": {
            "event_boundary": "FAIL", "scope": "FAIL", "stage": "FAIL",
            "date": None, "causality": None,
        },
        "label_status": "USER_SPECIFIED",
        "error_types": ["STAGE_ERROR", "SCOPE_ERROR"],
        "required_repair": "단일 headline event로 결합하지 않고 자동정지와 사업기간 변경을 분리한다.",
        "human_notes": None,
    })
    for row in _round_robin_sources(archive, target * 2, seed="curation-v2"):
        if len(selected) >= target:
            break
        if row["hash"] == regression["hash"]:
            continue
        case = {
            "id": f"curation-{row['hash']}",
            "source_kind": "archive_candidate",
            "source_hash": row["hash"],
            "source_title": row["title"],
            "source_url": row["url"],
            "published_at": _published(row),
            "source_input": {
                "title": row["title"], "url": row["url"],
                "published_at": _published(row),
            },
            "generated_title": row["title_kr"],
            "current_output": {key: row.get(key) or "" for key in
                               ("title_kr", "summary", "detail", "implication", "why_important")},
            "selection_metadata_not_gold": {
                "risk_dimensions": _risk_dimensions(row), "domain": _domain(row),
            },
            "human_label": None,
            "human_dimensions": {key: None for key in CURATION_DIMENSIONS},
            "human_notes": None,
            "label_status": "HUMAN_LABEL_REQUIRED",
        }
        selected.append(case)
    existing = existing or {}
    return [
        _preserve_human_fields(case, existing.get(case["id"]),
                               ("human_label", "human_dimensions", "human_notes",
                                "label_status", "error_types", "required_repair"))
        for case in selected
    ]


def _perturb_claim(source_title: str, error_type: str) -> str:
    if error_type == "FACT_ERROR":
        return f"원문이 보도한 '{source_title}' 사건은 실제로 발생하지 않았다."
    if error_type == "NUMBER_ERROR":
        match = re.search(r"\d+", source_title)
        if match:
            wrong = str(int(match.group()) + 7)
            return source_title[:match.start()] + wrong + source_title[match.end():]
        return f"{source_title} 관련 공식 수치는 999건이다."
    if error_type == "ENTITY_ERROR":
        return f"한국수력원자력이 '{source_title}'를 직접 결정하고 발표했다."
    if error_type == "DATE_ERROR":
        return f"'{source_title}' 사건은 2099년 1월 1일 발생했다."
    if error_type == "SCOPE_ERROR":
        return f"'{source_title}' 조치는 전 세계 모든 원전에 동일하게 적용된다."
    if error_type == "STAGE_ERROR":
        return f"'{source_title}' 관련 사업은 이미 상업운전과 최종 준공을 완료했다."
    if error_type == "CAUSALITY_ERROR":
        return f"'{source_title}' 때문에 국내 전기요금이 즉시 인상됐다."
    if error_type == "TEMPORAL_ERROR":
        return f"'{source_title}'의 후속 조치는 원문 보도 전에 모두 완료됐다."
    if error_type == "CERTAINTY_ERROR":
        return f"'{source_title}'의 전망과 계획은 예외 없이 확정됐다."
    return f"'{source_title}'은 정부의 공개되지 않은 비밀 지시 때문에 발생했다."


def build_semantic(archive: dict[str, dict], saeul_contract: dict, *, base_count: int = 24,
                   existing: dict[str, dict] | None = None) -> list[dict]:
    cases: list[dict] = []
    sources = _round_robin_sources(archive, base_count, seed="semantic-v2")
    for index, row in enumerate(sources):
        source = {
            "source_hash": row["hash"], "original_title": row["title"],
            "url": row["url"], "published_at": _published(row),
            "verified_evidence": row.get("verified_evidence") or {},
        }
        generated = {key: row.get(key) or "" for key in
                     ("title_kr", "summary", "detail", "implication", "why_important")}
        common = {
            "source_evidence": source,
            "generated_context_not_source_evidence": generated,
            "domain": _domain(row),
            "human_label": None,
            "human_error_types": None,
            "human_notes": None,
            "label_status": "HUMAN_LABEL_REQUIRED",
        }
        cases.append({
            **common, "id": f"semantic-{row['hash']}-aligned",
            "candidate_kind": "source_aligned",
            "claim": row["title"],
            "selection_metadata_not_gold": {"review_focus": "normal_claim"},
        })
        error_type = ERROR_TYPES[index % len(ERROR_TYPES)]
        cases.append({
            **common, "id": f"semantic-{row['hash']}-perturbed",
            "candidate_kind": "controlled_perturbation",
            "claim": _perturb_claim(row["title"], error_type),
            "selection_metadata_not_gold": {"review_focus": error_type},
        })
        cases.append({
            **common, "id": f"semantic-{row['hash']}-unsupported",
            "candidate_kind": "unsupported_inference",
            "claim": f"'{row['title']}'은 향후 5년간 산업 전체의 수익성을 확실히 높일 것이다.",
            "selection_metadata_not_gold": {"review_focus": "UNSUPPORTED_INFERENCE"},
        })

    for source_case in saeul_contract.get("cases") or []:
        case = dict(source_case)
        case["candidate_kind"] = "user_specified_contract"
        case["human_error_types"] = list(case.get("error_types") or [])
        case["source_evidence"] = {
            "source_hash": "4da5b7ab6c225c78",
            "contract_facts": saeul_contract.get("source_facts") or [],
        }
        case["selection_metadata_not_gold"] = {"review_focus": "saeul_contract"}
        cases.append(case)

    existing = existing or {}
    return [
        _preserve_human_fields(case, existing.get(case["id"]),
                               ("human_label", "human_error_types", "human_notes",
                                "label_status", "error_types"))
        for case in cases
    ]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _checkboxes(values: Iterable[str], current: str | Iterable[str] | None = None) -> str:
    selected = ({current} if isinstance(current, str) else set(current or []))
    return "\n".join(f"- [{'x' if value in selected else ' '}] {value}" for value in values)


def identity_markdown(cases: list[dict]) -> str:
    lines = [
        "# Identity Gold human labeling sheet", "",
        "콘텐츠를 먼저 읽고 답을 선택하세요. 접힌 참고정보의 캐시/모델 값은 정답이 아닙니다.", "",
    ]
    for index, case in enumerate(cases, 1):
        lines += [
            f"## ID-{index:03d} · `{case['id']}`", "",
            "### A", "", f"- 제목: {case['a']['title']}",
            f"- 날짜: {case['a']['published_at']}",
            f"- 출처: [{case['a']['publisher']}]({case['a']['url']})",
            f"- 요약: {case['a']['summary'] or '(없음)'}", "",
            "### B", "", f"- 제목: {case['b']['title']}",
            f"- 날짜: {case['b']['published_at']}",
            f"- 출처: [{case['b']['publisher']}]({case['b']['url']})",
            f"- 요약: {case['b']['summary'] or '(없음)'}", "",
            "### 사람 판정", "", _checkboxes(IDENTITY_LABELS, case.get("human_label")), "",
            "Reason code:", "", _checkboxes(IDENTITY_REASONS, case.get("reason_code")), "",
            "메모:", "", "", "<details><summary>선정 참고정보(정답 아님)</summary>", "",
            f"- selection_group: {case['metadata']['selection_group']}",
            f"- edge_case: {case['metadata']['known_edge_case']}",
            f"- similarity: {case['metadata']['embedding_similarity']}",
            f"- date_gap_days: {case['metadata']['date_gap_days']}",
            f"- shared_entities: {case['metadata']['shared_entities']}",
            f"- cached review: {json.dumps(case['metadata']['cached_review_not_gold'], ensure_ascii=False)}",
            f"- model comparison: {json.dumps(case['metadata']['model_comparison'], ensure_ascii=False)}",
            "", "</details>", "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def curation_markdown(cases: list[dict]) -> str:
    lines = [
        "# Curation Gold human labeling sheet", "",
        "원문 입력과 현재 Nuclens 출력을 비교해 각 차원을 사람이 판정합니다.", "",
    ]
    for index, case in enumerate(cases, 1):
        output = case["current_output"]
        lines += [
            f"## CUR-{index:03d} · `{case['id']}`", "",
            f"- 원문: [{case['source_title']}]({case['source_url']})",
            f"- 날짜: {case['published_at']}", f"- 해시: `{case['source_hash']}`", "",
            "### 현재 출력", "",
            f"- title_kr: {output['title_kr']}", f"- summary: {output['summary']}",
            f"- detail: {output['detail'] or '(없음)'}",
            f"- implication: {output['implication'] or '(없음)'}",
            f"- why_important: {output['why_important'] or '(없음)'}", "",
            "### 사람 판정", "",
        ]
        for dimension, labels in CURATION_DIMENSIONS.items():
            current = (case.get("human_dimensions") or {}).get(dimension)
            lines += [f"**{dimension}**", "", _checkboxes(labels, current), ""]
        lines += ["**Overall**", "", _checkboxes(("PASS", "REPAIR", "BLOCK"),
                                                   case.get("human_label")), "",
                  "메모:", "", "",
                  "<details><summary>선정 참고정보(정답 아님)</summary>", "",
                  json.dumps(case["selection_metadata_not_gold"], ensure_ascii=False),
                  "", "</details>", ""]
    return "\n".join(lines).rstrip() + "\n"


def semantic_markdown(cases: list[dict]) -> str:
    lines = [
        "# Semantic Gold human labeling sheet", "",
        "claim을 source evidence만으로 검증하세요. generated context는 현재 출력 참고일 뿐 근거가 아닙니다.", "",
    ]
    for index, case in enumerate(cases, 1):
        source = case.get("source_evidence") or {}
        lines += [f"## SEM-{index:03d} · `{case['id']}`", "",
                  f"**Claim:** {case['claim']}", "", "### Source evidence", ""]
        if source.get("url"):
            lines += [f"- 원문: [{source.get('original_title')}]({source['url']})",
                      f"- 날짜: {source.get('published_at')}",
                      f"- 해시: `{source.get('source_hash')}`"]
        else:
            lines += [f"- 계약 사실: {json.dumps(source.get('contract_facts') or [], ensure_ascii=False)}"]
        lines += ["", "### 사람 판정", "",
                  _checkboxes(SEMANTIC_LABELS, case.get("human_label")), "",
                  "Error types (해당 항목 모두 선택):", "",
                  _checkboxes(ERROR_TYPES, case.get("human_error_types") or
                              case.get("error_types")), "",
                  "메모:", "", "", "<details><summary>선정/생성 참고정보(정답·근거 아님)</summary>", "",
                  f"- kind: {case.get('candidate_kind')}",
                  f"- focus: {json.dumps(case.get('selection_metadata_not_gold'), ensure_ascii=False)}",
                  f"- generated context: {json.dumps(case.get('generated_context_not_source_evidence'), ensure_ascii=False)}",
                  "", "</details>", ""]
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=ROOT / "issue_llm_reviews.json")
    parser.add_argument("--archive-dir", type=Path, default=ROOT / "archive")
    parser.add_argument("--fixtures-dir", type=Path, default=FIXTURES)
    parser.add_argument("--docs-dir", type=Path, default=LABEL_DOCS)
    parser.add_argument("--identity-count", type=int, default=150)
    parser.add_argument("--curation-count", type=int, default=40)
    parser.add_argument("--semantic-base-count", type=int, default=24)
    # Retained as a harmless compatibility alias for the old identity-only command.
    parser.add_argument("--out", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    cache = json.loads(args.cache.read_text(encoding="utf-8"))
    archive = load_archive(args.archive_dir)
    identity_path = args.out or args.fixtures_dir / "identity_candidates.json"
    curation_path = args.fixtures_dir / "curation_gold.json"
    semantic_path = args.fixtures_dir / "semantic_gold.json"
    saeul_path = args.fixtures_dir / "saeul_contract.json"
    saeul = json.loads(saeul_path.read_text(encoding="utf-8"))

    identity = build_enriched_identity(
        cache, archive, target=args.identity_count, existing=_load_existing(identity_path))
    curation = build_curation(
        archive, target=args.curation_count, existing=_load_existing(curation_path))
    semantic = build_semantic(
        archive, saeul, base_count=args.semantic_base_count,
        existing=_load_existing(semantic_path))

    _write_json(identity_path, {
        "schema_version": 2, "task": "IDENTITY_REVIEW",
        "label_contract": IDENTITY_LABELS, "reason_codes": IDENTITY_REASONS,
        "warning": "Cached/model verdicts are reference only and MUST NOT become Gold labels.",
        "cases": identity,
    })
    _write_json(curation_path, {
        "schema_version": 2, "task": "CURATION",
        "dimension_contract": CURATION_DIMENSIONS,
        "warning": "Selection metadata is not a Gold answer.", "cases": curation,
    })
    _write_json(semantic_path, {
        "schema_version": 2, "task": "SEMANTIC",
        "label_contract": SEMANTIC_LABELS, "error_type_contract": ERROR_TYPES,
        "warning": "Candidate kind and review focus are not Gold answers.", "cases": semantic,
    })

    args.docs_dir.mkdir(parents=True, exist_ok=True)
    (args.docs_dir / "identity.md").write_text(identity_markdown(identity), encoding="utf-8")
    (args.docs_dir / "curation.md").write_text(curation_markdown(curation), encoding="utf-8")
    (args.docs_dir / "semantic.md").write_text(semantic_markdown(semantic), encoding="utf-8")
    print(f"identity={len(identity)} curation={len(curation)} semantic={len(semantic)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
