from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from api.research.query_policy import evaluate


@dataclass(frozen=True)
class ForecastPolicyDecision:
    status: Literal["allowed", "blocked", "require_human_review"]
    reason: str | None
    prompt_hash: str
    blocked_terms: list[str]


def evaluate_forecast_policy(
    prompt: str,
    *,
    profile: str,
    data_classification: str = "public",
    resolved_tools: list[dict[str, Any]] | None = None,
    background: bool = True,
) -> ForecastPolicyDecision:
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if profile == "synthesis":
        return ForecastPolicyDecision(
            status="require_human_review",
            reason="Synthesis profile cannot submit Deep Research.",
            prompt_hash=prompt_hash,
            blocked_terms=[],
        )
    if data_classification == "restricted" and background:
        return ForecastPolicyDecision(
            status="blocked",
            reason="background_mode_violates_zdr",
            prompt_hash=prompt_hash,
            blocked_terms=[],
        )
    if profile == "private":
        if not resolved_tools:
            return ForecastPolicyDecision(
                status="require_human_review",
                reason="Private profile requires resolved tools.",
                prompt_hash=prompt_hash,
                blocked_terms=[],
            )
        return ForecastPolicyDecision(
            status="allowed",
            reason=None,
            prompt_hash=prompt_hash,
            blocked_terms=[],
        )
    decision = evaluate(prompt, profile="public")
    if decision.decision == "block":
        return ForecastPolicyDecision(
            status="blocked",
            reason=decision.reason,
            prompt_hash=prompt_hash,
            blocked_terms=[],
        )
    if decision.decision == "require_human_review":
        return ForecastPolicyDecision(
            status="require_human_review",
            reason=decision.reason,
            prompt_hash=prompt_hash,
            blocked_terms=[],
        )
    return ForecastPolicyDecision(
        status="allowed",
        reason=decision.reason,
        prompt_hash=prompt_hash,
        blocked_terms=[],
    )
