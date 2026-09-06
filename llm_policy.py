"""Central task policy for production Gemini calls.

This module deliberately contains no domain imports.  A policy entry describes the
task's model and reasoning contract; it does not own quota, retries, prompts, or
failure fallbacks.  The initial policy is a request-body no-op: every production
task leaves thinking unspecified and keeps explicit caller sampling.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

import gemini_client

SIMPLE_EXTRACT = "SIMPLE_EXTRACT"
BULK_CURATION = "BULK_CURATION"
IDENTITY_REVIEW = "IDENTITY_REVIEW"
CONTEXT_SYNTHESIS = "CONTEXT_SYNTHESIS"
NARRATIVE_GENERATION = "NARRATIVE_GENERATION"
FINAL_SEMANTIC_VERIFY = "FINAL_SEMANTIC_VERIFY"


@dataclass(frozen=True)
class TaskProfile:
    task: str
    model_resolver: Callable[[], str]
    thinking_level: str | None = None
    sampling_mode: str = "explicit"
    strict_reasoning: bool = False
    activation: str = "IMPLEMENTED_BUT_NOT_ACTIVATED"

    def model(self) -> str:
        return self.model_resolver()

    def reasoning_kwargs(self) -> dict[str, str]:
        """Only emit a keyword when a level is explicitly activated."""
        if self.thinking_level is None:
            return {}
        return {"thinking_level": self.thinking_level}


def _main_model() -> str:
    return gemini_client.MODEL or "gemini-3.1-flash-lite"


def _synthesis_model() -> str:
    return gemini_client.synthesis_model()


def _review_model() -> str:
    return gemini_client._resolve(
        "GEMINI_REVIEW_MODEL", "gemini-3.5-flash-lite") or "gemini-3.5-flash-lite"


def _insight_model() -> str:
    return gemini_client._resolve(
        "GEMINI_INSIGHT_MODEL", "gemini-3.5-flash-lite") or "gemini-3.5-flash-lite"


def _script_model() -> str:
    return gemini_client._resolve(
        "GEMINI_SCRIPT_MODEL", "gemini-3.5-flash-lite") or "gemini-3.5-flash-lite"


def _verify_model() -> str:
    # Independent selection is available for offline Semantic Gold evaluation.
    # The unset production default remains the current curation model.
    return gemini_client._resolve("GEMINI_VERIFY_MODEL", _main_model()) or _main_model()


def _entry(task: str, resolver: Callable[[], str], *, strict: bool = False) -> TaskProfile:
    return TaskProfile(task=task, model_resolver=resolver, strict_reasoning=strict)


_PROFILES: dict[str, TaskProfile] = {
    "curation": _entry(BULK_CURATION, _main_model),
    "issue_review": _entry(IDENTITY_REVIEW, _review_model),
    "keei_match": _entry(IDENTITY_REVIEW, _main_model),
    "dedup": _entry(IDENTITY_REVIEW, _main_model),
    "dedup_final": _entry(IDENTITY_REVIEW, _main_model),
    "issue_insight": _entry(CONTEXT_SYNTHESIS, _insight_model),
    "daily_brief": _entry(CONTEXT_SYNTHESIS, _main_model),
    "daily_brief_implication": _entry(CONTEXT_SYNTHESIS, _synthesis_model),
    "daily_brief_report": _entry(CONTEXT_SYNTHESIS, _synthesis_model),
    "trend_insights": _entry(CONTEXT_SYNTHESIS, _synthesis_model),
    "weekly_bot": _entry(CONTEXT_SYNTHESIS, _synthesis_model),
    "daily_lead": _entry(NARRATIVE_GENERATION, _synthesis_model),
    "audio_brief": _entry(NARRATIVE_GENERATION, _script_model),
    "pubs_translate": _entry(SIMPLE_EXTRACT, _main_model),
    "expert_dossiers": _entry(SIMPLE_EXTRACT, _main_model),
    "expert_plan": _entry(CONTEXT_SYNTHESIS, _synthesis_model),
    "expert_script": _entry(NARRATIVE_GENERATION, _synthesis_model),
    "expert_repair": _entry(NARRATIVE_GENERATION, _synthesis_model),
    "expert_reorder": _entry(NARRATIVE_GENERATION, _synthesis_model),
    "expert_intro_repair": _entry(NARRATIVE_GENERATION, _synthesis_model),
    "expert_verify": _entry(FINAL_SEMANTIC_VERIFY, _verify_model, strict=True),
    "fast_verify": _entry(FINAL_SEMANTIC_VERIFY, _verify_model, strict=True),
    "fast_semantic_repair": _entry(NARRATIVE_GENERATION, _script_model),
}


def _canonical_name(name: str) -> str:
    for prefix in (
        "expert_verify_after_repair", "expert_verify", "expert_dossiers",
        "expert_script_retry", "expert_script", "expert_intro_repair",
        "expert_repair", "expert_reorder",
    ):
        if name.startswith(prefix):
            return "expert_script" if prefix == "expert_script_retry" else prefix.replace(
                "expert_verify_after_repair", "expert_verify")
    return name


def profile(name: str) -> TaskProfile:
    canonical = _canonical_name(name)
    try:
        return _PROFILES[canonical]
    except KeyError as exc:
        raise KeyError(f"등록되지 않은 LLM task profile: {name}") from exc


def generation_policy_fingerprint(task_profile: TaskProfile, prompt_version: int | str) -> str:
    effective = (f"level:{task_profile.thinking_level}"
                 if task_profile.thinking_level is not None else "unspecified")
    raw = "|".join((task_profile.model(), effective,
                    task_profile.sampling_mode, str(prompt_version)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def production_policy_snapshot() -> dict[str, dict[str, object]]:
    return {
        name: {
            "task": item.task,
            "model": item.model(),
            "thinking": item.thinking_level,
            "sampling_mode": item.sampling_mode,
            "strict_reasoning": item.strict_reasoning,
            "activation": item.activation,
        }
        for name, item in sorted(_PROFILES.items())
    }
