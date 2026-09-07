"""Shared semantic-verification contract for Fast and Expert audio.

The Fast gate remains disabled until a broader human-labelled Semantic Gold set
selects its model/reasoning policy.  Expert already has a production verifier and
uses this schema without adding calls.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

import gemini_client
import llm_policy

SEMANTIC_GATE_VERSION = 1
# Broader balanced Semantic Gold is not available yet.  Fast audio must not gain
# a new blocking production call until that evaluation is complete.
FAST_SEMANTIC_GATE_ENABLED = False
ERROR_TYPES = frozenset({
    "FACT_ERROR", "NUMBER_ERROR", "ENTITY_ERROR", "DATE_ERROR", "SCOPE_ERROR",
    "STAGE_ERROR", "CAUSALITY_ERROR", "TEMPORAL_ERROR", "CERTAINTY_ERROR",
    "UNSUPPORTED_INFERENCE",
})
VERDICTS = frozenset({"PASS", "REPAIR", "UNVERIFIABLE", "BLOCK"})

# JSON Schema sent to Gemini in addition to the textual contract.  The parser
# below remains authoritative and rejects malformed values; the schema reduces
# shape drift, it does not create a permissive fallback.
OUTPUT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": sorted(VERDICTS)},
        "passed": {"type": "boolean"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": sorted(ERROR_TYPES)},
                    "line": {"type": "string"},
                    "why": {"type": "string"},
                    "repair": {"type": "string"},
                },
                "required": ["type", "line", "why", "repair"],
            },
        },
    },
    "required": ["verdict", "passed", "findings"],
}

SYSTEM_PROMPT = """당신은 오디오 대본의 독립 semantic verifier입니다.
오직 Source Evidence에 있는 사실만 근거로 사용하십시오. dossier, implication,
why_important, 이전 대본, 이전 verifier 결과는 generated interpretation이며 사실
근거가 아닙니다. 두 사실이 각각 참이라는 이유만으로 둘 사이 인과를 인정하지 마십시오.

오류 유형은 FACT_ERROR, NUMBER_ERROR, ENTITY_ERROR, DATE_ERROR, SCOPE_ERROR,
STAGE_ERROR, CAUSALITY_ERROR, TEMPORAL_ERROR, CERTAINTY_ERROR,
UNSUPPORTED_INFERENCE 중에서만 고릅니다. 근거가 틀렸다고 증명하는 경우와 현재
근거만으로 확인할 수 없는 경우를 구분하십시오. 후자는 UNVERIFIABLE이며 인과
표현을 중립화하는 repair를 제안합니다. JSON만 출력하십시오."""


def output_contract() -> str:
    return json.dumps({
        "verdict": "PASS|REPAIR|UNVERIFIABLE|BLOCK",
        "passed": True,
        "findings": [{"type": "ERROR_TYPE", "line": "...", "why": "...",
                      "repair": "..."}],
    }, ensure_ascii=False)


def verification_prompt(source_evidence: object, script: str, *, context: object = None) -> str:
    return (f"[출력 JSON]\n{output_contract()}\n"
            f"[Source Evidence]\n{json.dumps(source_evidence, ensure_ascii=False, indent=2)}\n"
            f"[Non-evidence Context]\n{json.dumps(context, ensure_ascii=False, indent=2)}\n"
            f"[Script]\n{script}")


def normalize_report(report: object) -> dict:
    if not isinstance(report, dict):
        raise gemini_client.GeminiError("semantic verdict가 JSON object가 아님")
    if not all(key in report for key in ("verdict", "passed", "findings")):
        raise gemini_client.GeminiError("semantic verdict 필수 필드가 빠짐")
    if not isinstance(report.get("passed"), bool):
        raise gemini_client.GeminiError("semantic passed가 boolean이 아님")
    verdict = str(report.get("verdict") or "")
    if verdict not in VERDICTS:
        raise gemini_client.GeminiError(f"semantic verdict 값이 잘못됨: {verdict}")
    findings = report.get("findings")
    if not isinstance(findings, list):
        raise gemini_client.GeminiError("semantic findings가 list가 아님")
    cleaned = []
    for finding in findings:
        if not isinstance(finding, dict) or finding.get("type") not in ERROR_TYPES:
            raise gemini_client.GeminiError("semantic finding error type이 잘못됨")
        cleaned.append({key: str(finding.get(key) or "")[:500]
                        for key in ("type", "line", "why", "repair")})
    passed = verdict == "PASS" and report["passed"] and not cleaned
    return {"verdict": verdict, "passed": passed, "findings": cleaned}


def verify(source_evidence: object, script: str, *, label: str = "fast_verify",
           context: object = None, client=gemini_client) -> dict:
    policy = llm_policy.profile(label)
    result = client.call_json(
        SYSTEM_PROMPT, verification_prompt(source_evidence, script, context=context),
        temperature=0.0, max_output_tokens=6000, timeout=150.0, retries=2,
        model=policy.model(), label=label,
        response_json_schema=OUTPUT_JSON_SCHEMA, **policy.reasoning_kwargs())
    return normalize_report(result)


def repair_prompt(script: str, report: dict, source_evidence: object) -> str:
    return f"""지적된 문장/문단만 최소 수정하십시오. 전체 원고를 재창작하지 마십시오.
UNVERIFIABLE 인과는 인과관계를 주장하지 않는 병렬 사실 표현으로 중립화하십시오.
[출력 JSON] {{"script":"HOST: ..."}}
[Findings] {json.dumps(report.get('findings') or [], ensure_ascii=False)}
[Source Evidence] {json.dumps(source_evidence, ensure_ascii=False)}
[Script] {script}"""


def chronology_finding(cause_date: date | None, effect_date: date | None) -> dict | None:
    """Known dates only: a later cause cannot explain an earlier effect."""
    if cause_date is None or effect_date is None or cause_date <= effect_date:
        return None
    return {
        "type": "TEMPORAL_ERROR",
        "line": "",
        "why": f"원인 사건일 {cause_date.isoformat()}이 결과 결정일 {effect_date.isoformat()}보다 늦음",
        "repair": "인과 표현을 제거하고 두 사실을 별도 사안으로 기술",
    }


def verdict_digest(report: dict) -> str:
    raw = json.dumps(normalize_report(report), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
