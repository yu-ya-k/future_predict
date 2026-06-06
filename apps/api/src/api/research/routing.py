from __future__ import annotations

from typing import Literal, TypedDict

from api.research.schemas import Verdict

ReviewRoute = Literal["deep_research_submit", "llm_finalize", "finalize", "human_review"]
MIN_REVIEWER_CONFIDENCE_FOR_AUTO_FINALIZE = 70


class RouteState(TypedDict, total=False):
    review: dict[str, object]
    total_reviews: int
    deep_research_runs: int
    llm_fix_runs: int
    no_progress_count: int
    max_total_iterations: int
    max_deep_research_runs: int
    max_llm_fix_runs: int
    max_no_progress_rounds: int
    estimated_cost_usd: float
    max_cost_usd: float
    total_tool_calls: int
    max_total_tool_calls: int
    contains_confidential_context: bool
    web_search_allowed: bool


def route_after_review(state: RouteState) -> ReviewRoute:
    review = state.get("review", {})
    verdict = review.get("verdict", Verdict.HUMAN_REVIEW)

    if isinstance(verdict, Verdict):
        verdict_value = verdict.value
    else:
        verdict_value = str(verdict)

    high_risk_flags = review.get("high_risk_flags", [])
    if isinstance(high_risk_flags, list) and high_risk_flags:
        return "human_review"

    reviewer_confidence = review.get("reviewer_confidence")
    if (
        isinstance(reviewer_confidence, int | float)
        and reviewer_confidence < MIN_REVIEWER_CONFIDENCE_FOR_AUTO_FINALIZE
    ):
        return "human_review"

    if verdict_value == Verdict.PASS.value:
        return "finalize"

    if verdict_value == Verdict.HUMAN_REVIEW.value:
        return "human_review"

    total_reviews = state.get("total_reviews", 0)
    deep_runs = state.get("deep_research_runs", 0)
    llm_fix_runs = state.get("llm_fix_runs", 0)
    no_progress = state.get("no_progress_count", 0)

    max_total = state.get("max_total_iterations", 5)
    max_deep = state.get("max_deep_research_runs", 2)
    max_llm_fix = state.get("max_llm_fix_runs", 3)
    max_no_progress = state.get("max_no_progress_rounds", 2)

    estimated_cost = state.get("estimated_cost_usd", 0.0)
    max_cost = state.get("max_cost_usd", 999.0)
    total_tool_calls = state.get("total_tool_calls", 0)
    max_total_tool_calls = state.get("max_total_tool_calls", 999)

    if total_reviews >= max_total:
        return "human_review"

    if no_progress >= max_no_progress:
        return "human_review"

    if estimated_cost >= max_cost:
        return "human_review"

    if total_tool_calls >= max_total_tool_calls:
        return "human_review"

    if state.get("contains_confidential_context", False) and state.get("web_search_allowed", False):
        return "human_review"

    if verdict_value == Verdict.NEEDS_DEEP_RESEARCH.value:
        if deep_runs < max_deep:
            return "deep_research_submit"

        if bool(review.get("can_be_fixed_by_llm", False)) and llm_fix_runs < max_llm_fix:
            return "llm_finalize"

        return "human_review"

    if verdict_value == Verdict.NEEDS_LLM_FIX.value:
        if review.get("requires_new_external_research", False):
            return "human_review"

        if not bool(review.get("can_be_fixed_by_llm", False)):
            return "human_review"

        if llm_fix_runs < max_llm_fix:
            return "llm_finalize"

        return "human_review"

    return "human_review"
