from __future__ import annotations

from api.forecast.schemas import ForecastDetail, PackRole

CURRENT_STATE_PROMPT_VERSION = "current_state_phase_a_v1"


def build_current_state_prompt(forecast: ForecastDetail) -> str:
    outcomes = "\n".join(
        f"- {outcome.label}: {outcome.definition}" for outcome in forecast.outcomes
    )
    return f"""You are collecting public evidence for a forecast.

Forecast question:
{forecast.question}

Resolution criteria:
{forecast.resolution_criteria or "Use the approved resolution criteria."}

Outcomes:
{outcomes}

Pack role: {PackRole.CURRENT_STATE.value}

Collect public current-state evidence, base facts, recent signals, and credible counterpoints.
Do not provide final probabilities. Cite public sources.
"""
