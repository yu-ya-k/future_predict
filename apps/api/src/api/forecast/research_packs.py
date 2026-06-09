from __future__ import annotations

from api.forecast.schemas import ForecastDetail, PackRole

CURRENT_STATE_PROMPT_VERSION = "current_state_phase_a_v1"
PHASE_B_PACK_PROMPT_VERSION = "phase_b_pack_v1"


def build_current_state_prompt(forecast: ForecastDetail) -> str:
    outcomes = "\n".join(
        f"- {outcome.label}: {outcome.definition}" for outcome in forecast.outcomes
    )
    sources = "\n".join(f"- {source}" for source in forecast.resolution_sources)
    original_prompt = (
        forecast.original_execution_prompt
        if forecast.original_execution_prompt
        and forecast.original_execution_prompt.strip()
        else None
    )
    primary_task = original_prompt or forecast.question
    fallback_note = (
        ""
        if original_prompt
        else (
            "\nOriginal execution prompt was not stored for this forecast; "
            "use the metadata appendix as the task framing.\n"
        )
    )
    return f"""You are collecting public evidence for a forecast.

Primary execution prompt:
{primary_task}
{fallback_note}
Treat the primary execution prompt above as the user's source-of-truth task.
The metadata appendix below is operational framing for Forecast resolution and must not
replace, compress, or override the primary execution prompt.

Forecast metadata appendix:
- Forecast question: {forecast.question}
- Target population: {forecast.target_population or "Not specified."}
- Unit of analysis: {forecast.unit_of_analysis or "Not specified."}
- Decision context: {forecast.decision_context or "Not specified."}

Resolution criteria:
{forecast.resolution_criteria or "Use the approved resolution criteria."}

Resolution sources:
{sources or "- Not specified."}

Resolution outcome metadata:
{outcomes}

Pack role: {PackRole.CURRENT_STATE.value}

Collect public current-state evidence, base facts, recent signals, and credible counterpoints.
Do not provide final probabilities. Cite public sources.
"""


def build_research_pack_prompt(
    forecast: ForecastDetail,
    *,
    pack_role: PackRole,
    tool_profile: str,
) -> str:
    if pack_role == PackRole.CURRENT_STATE:
        return build_current_state_prompt(forecast).replace(
            "Pack role: current_state",
            f"Pack role: {pack_role.value}\nTool profile: {tool_profile}",
        )

    role_instructions = {
        PackRole.BASE_RATE: (
            "Find comparable historical or analog events. For each analog, explain "
            "which resolution outcome it resembles and why. Include enough detail "
            "for a deterministic extractor to create weighted analog events."
        ),
        PackRole.DRIVERS: (
            "Identify major causal drivers and plausible driver states. Focus on "
            "factors that distinguish forecast outcomes and can support a "
            "morphological scenario map."
        ),
        PackRole.COUNTER_EVIDENCE: (
            "Collect credible evidence against the currently most intuitive or "
            "consensus outcome. Include disconfirming signals, bottlenecks, and "
            "sources of base-rate neglect."
        ),
        PackRole.SIGNALS: (
            "Collect recent observable signals, leading indicators, and monitoring "
            "metrics that would move the forecast before the resolution date."
        ),
    }[pack_role]
    outcomes = "\n".join(
        f"- {outcome.label}: {outcome.definition}" for outcome in forecast.outcomes
    )
    sources = "\n".join(f"- {source}" for source in forecast.resolution_sources)
    primary_task = (
        forecast.original_execution_prompt
        if forecast.original_execution_prompt
        and forecast.original_execution_prompt.strip()
        else forecast.question
    )
    return f"""You are collecting evidence for Forecast Phase B.

Primary execution prompt:
{primary_task}

Forecast metadata:
- Forecast question: {forecast.question}
- Target population: {forecast.target_population or "Not specified."}
- Unit of analysis: {forecast.unit_of_analysis or "Not specified."}
- Decision context: {forecast.decision_context or "Not specified."}
- Tool profile: {tool_profile}
- Pack role: {pack_role.value}

Resolution criteria:
{forecast.resolution_criteria or "Use the approved resolution criteria."}

Resolution sources:
{sources or "- Not specified."}

Resolution outcome metadata:
{outcomes}

Role-specific task:
{role_instructions}

Do not provide final probabilities. Cite sources. Separate factual claims from
analysis and clearly mark uncertainty.
"""
