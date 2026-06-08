from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from api.research.query_policy import evaluate


@dataclass(frozen=True)
class ForecastPolicyDecision:
    status: Literal["allowed", "blocked", "require_human_review"]
    reason: str | None
    prompt_hash: str


def evaluate_forecast_policy(prompt: str, *, profile: str) -> ForecastPolicyDecision:
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if profile != "public":
        return ForecastPolicyDecision(
            status="require_human_review",
            reason="PhaseA supports public current_state packs only.",
            prompt_hash=prompt_hash,
        )
    decision = evaluate(prompt, profile="public")
    if decision.decision == "block":
        return ForecastPolicyDecision(
            status="blocked",
            reason=decision.reason,
            prompt_hash=prompt_hash,
        )
    if decision.decision == "require_human_review":
        return ForecastPolicyDecision(
            status="require_human_review",
            reason=decision.reason,
            prompt_hash=prompt_hash,
        )
    return ForecastPolicyDecision(
        status="allowed",
        reason=decision.reason,
        prompt_hash=prompt_hash,
    )

