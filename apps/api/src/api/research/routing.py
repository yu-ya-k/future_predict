from __future__ import annotations

from typing import Literal, TypedDict, cast

from api.research.schemas import FailureMode, RecommendedAction, Severity, Verdict

ReviewRoute = Literal[
    "finalize",
    "llm_patch",
    "verify_items",
    "build_targeted_rerun_plan",
    "full_rerun_submit",
    "revise_research_items",
    "finalize_with_limitation",
    "human_review",
]
MIN_REVIEWER_CONFIDENCE_FOR_AUTO_FINALIZE = 70
ACTION_PRIORITY = [
    RecommendedAction.HUMAN_REVIEW.value,
    RecommendedAction.REVISE_ITEMS.value,
    RecommendedAction.FULL_RERUN.value,
    RecommendedAction.TARGETED_RERUN.value,
    RecommendedAction.LLM_PATCH.value,
    RecommendedAction.VERIFY.value,
    RecommendedAction.FINALIZE_WITH_LIMITATION.value,
    RecommendedAction.NONE.value,
]


class RouteState(TypedDict, total=False):
    review: dict[str, object]
    total_reviews: int
    targeted_rerun_runs: int
    full_rerun_runs: int
    llm_patch_runs: int
    verification_runs: int
    no_progress_count: int
    max_total_iterations: int
    max_targeted_rerun_runs: int
    max_full_rerun_runs: int
    max_llm_patch_runs: int
    max_verification_runs: int
    total_tool_calls: int
    max_total_tool_calls: int


class RouteDecision(TypedDict):
    candidate_route: ReviewRoute
    selected_route: ReviewRoute
    blocked_reason: str | None
    dominant_actions: list[str]


def route_after_review(state: RouteState) -> ReviewRoute:
    return route_after_review_decision(state)["selected_route"]


def route_after_review_decision(state: RouteState) -> RouteDecision:
    candidate_route, dominant_actions = _candidate_route_after_review(state)
    selected_route, blocked_reason = _apply_route_guards(candidate_route, state)
    return {
        "candidate_route": candidate_route,
        "selected_route": selected_route,
        "blocked_reason": blocked_reason,
        "dominant_actions": dominant_actions,
    }


def _candidate_route_after_review(
    state: RouteState,
) -> tuple[ReviewRoute, list[str]]:
    review = state.get("review", {})
    verdict = _string_value(review.get("verdict", Verdict.HUMAN_REVIEW))

    if verdict == Verdict.HUMAN_REVIEW.value:
        return "human_review", []

    if _list_has_values(review.get("security_concerns")) or _list_has_values(
        review.get("high_risk_flags")
    ):
        return "human_review", []

    reviewer_confidence = review.get("reviewer_confidence")
    if (
        isinstance(reviewer_confidence, int | float)
        and reviewer_confidence < MIN_REVIEWER_CONFIDENCE_FOR_AUTO_FINALIZE
    ):
        return "human_review", []

    actions = _aggregate_actions(review.get("item_assessments"))
    dominant_actions = _dominant_actions(actions)
    if RecommendedAction.HUMAN_REVIEW.value in actions:
        return "human_review", dominant_actions

    if verdict == Verdict.PASS.value:
        if actions and actions <= {RecommendedAction.NONE.value}:
            return "finalize", dominant_actions
        if actions:
            return "human_review", dominant_actions
        return "finalize", dominant_actions

    if verdict == Verdict.NEEDS_ITEM_REVISION.value:
        return "revise_research_items", dominant_actions

    if verdict == Verdict.FINALIZE_WITH_LIMITATION.value:
        return "finalize_with_limitation", dominant_actions

    if not actions:
        return _route_from_verdict_fallback(verdict), dominant_actions

    if RecommendedAction.REVISE_ITEMS.value in actions:
        return "revise_research_items", dominant_actions

    if RecommendedAction.FULL_RERUN.value in actions:
        return "full_rerun_submit", dominant_actions

    if RecommendedAction.TARGETED_RERUN.value in actions:
        return "build_targeted_rerun_plan", dominant_actions

    if RecommendedAction.LLM_PATCH.value in actions:
        return "llm_patch", dominant_actions

    if RecommendedAction.VERIFY.value in actions:
        return "verify_items", dominant_actions

    if RecommendedAction.FINALIZE_WITH_LIMITATION.value in actions:
        return "finalize_with_limitation", dominant_actions

    if actions <= {RecommendedAction.NONE.value}:
        return "finalize", dominant_actions

    return "human_review", dominant_actions


def _route_from_verdict_fallback(verdict: str) -> ReviewRoute:
    if verdict == Verdict.NEEDS_LLM_PATCH.value:
        return "llm_patch"
    if verdict == Verdict.NEEDS_VERIFICATION.value:
        return "verify_items"
    if verdict == Verdict.NEEDS_TARGETED_RERUN.value:
        return "build_targeted_rerun_plan"
    if verdict == Verdict.NEEDS_FULL_RERUN.value:
        return "full_rerun_submit"
    return "human_review"


def _apply_route_guards(
    route: ReviewRoute,
    state: RouteState,
) -> tuple[ReviewRoute, str | None]:
    global_blocked_reason = _global_blocked_reason(state)
    if global_blocked_reason is not None:
        return "human_review", global_blocked_reason

    if route in {"llm_patch", "verify_items"} and state.get("no_progress_count", 0) >= 2:
        return "human_review", "max_no_progress_count_reached"

    if route == "llm_patch" and state.get("llm_patch_runs", 0) >= state.get(
        "max_llm_patch_runs", 3
    ):
        return "human_review", "max_llm_patch_runs_reached"
    if route == "verify_items" and state.get("verification_runs", 0) >= state.get(
        "max_verification_runs", 3
    ):
        return "human_review", "max_verification_runs_reached"
    if route == "build_targeted_rerun_plan" and state.get(
        "targeted_rerun_runs", 0
    ) >= state.get("max_targeted_rerun_runs", 2):
        return "human_review", "max_targeted_rerun_runs_reached"
    if route == "full_rerun_submit" and state.get("full_rerun_runs", 0) >= state.get(
        "max_full_rerun_runs", 1
    ):
        return "human_review", "max_full_rerun_runs_reached"
    return route, None


def _global_blocked_reason(state: RouteState) -> str | None:
    if state.get("total_reviews", 0) >= state.get("max_total_iterations", 5):
        return "max_total_iterations_reached"
    if state.get("total_tool_calls", 0) >= state.get("max_total_tool_calls", 999):
        return "max_total_tool_calls_reached"
    return None


def _aggregate_actions(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()

    actions: set[str] = set()
    items = cast(list[object], value)
    for item in items:
        if isinstance(item, dict):
            item_dict = cast(dict[str, object], item)
            action = item_dict.get("recommended_action")
            if action is not None:
                actions.add(_string_value(action))
                continue
            inferred = _action_from_failure_mode(
                failure_mode=_string_value(item_dict.get("failure_mode", "")),
                severity=_string_value(item_dict.get("severity", "")),
                confidence=item_dict.get("failure_mode_confidence"),
            )
            if inferred is not None:
                actions.add(inferred)
    return actions


def _dominant_actions(actions: set[str]) -> list[str]:
    ordered = [action for action in ACTION_PRIORITY if action in actions]
    ordered.extend(sorted(actions - set(ordered)))
    return ordered


def _action_from_failure_mode(
    *,
    failure_mode: str,
    severity: str,
    confidence: object,
) -> str | None:
    if failure_mode in {
        FailureMode.FORMAT_ONLY.value,
        FailureMode.IN_REPORT_BUT_LOST.value,
    }:
        return RecommendedAction.LLM_PATCH.value
    if failure_mode == FailureMode.NEEDS_TARGETED_VERIFICATION.value:
        return RecommendedAction.VERIFY.value
    if failure_mode in {
        FailureMode.NEEDS_DIFFERENT_SOURCES.value,
        FailureMode.NEEDS_DEEPER_SEARCH.value,
        FailureMode.NEEDS_QUERY_REFORMULATION.value,
    }:
        return RecommendedAction.TARGETED_RERUN.value
    if failure_mode == FailureMode.SOURCE_CONTRADICTION.value:
        if severity == Severity.BLOCKER.value:
            return RecommendedAction.TARGETED_RERUN.value
        return RecommendedAction.VERIFY.value
    if failure_mode == FailureMode.LIKELY_NOT_PUBLICLY_AVAILABLE.value:
        if severity == Severity.BLOCKER.value:
            return RecommendedAction.HUMAN_REVIEW.value
        if isinstance(confidence, int | float) and confidence < 80:
            return RecommendedAction.VERIFY.value
        return RecommendedAction.FINALIZE_WITH_LIMITATION.value
    if failure_mode == FailureMode.CRITERION_TOO_AMBIGUOUS.value:
        if severity == Severity.MINOR.value:
            return RecommendedAction.REVISE_ITEMS.value
        return RecommendedAction.HUMAN_REVIEW.value
    if failure_mode == FailureMode.REQUIRES_HUMAN_JUDGMENT.value:
        return RecommendedAction.HUMAN_REVIEW.value
    if failure_mode == FailureMode.NONE.value:
        return RecommendedAction.NONE.value
    return None


def _string_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _list_has_values(value: object) -> bool:
    if not isinstance(value, list):
        return False
    items = cast(list[object], value)
    return any(bool(item) for item in items)
