from __future__ import annotations

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from api.research.routing import route_after_review
from api.research.schemas import HumanReviewAction


class GraphState(TypedDict, total=False):
    run_id: str
    report: str
    review: dict[str, Any]
    route: str
    human_decision: dict[str, Any]
    audit_summary: dict[str, Any]
    warnings: list[str]
    graph_terminal: str
    visited_review: bool
    visited_llm_finalize: bool
    visited_deep_research_submit: bool
    visited_deep_research_collect: bool
    post_llm_review: dict[str, Any]
    post_deep_research_review: dict[str, Any]


def build_phase_3_graph(*, checkpointer: Any | None = None) -> Any:
    builder = StateGraph(GraphState)
    builder.add_node("deep_research_collect", _deep_research_collect_node)
    builder.add_node("review", _review_node)
    builder.add_node("llm_finalize", _llm_finalize_node)
    builder.add_node("deep_research_submit", _deep_research_submit_node)
    builder.add_node("finalize", _terminal_node("finalize"))
    builder.add_node("human_review", node_human_review)
    builder.add_node("partial_finalize", _terminal_node("partial_finalize"))

    builder.add_edge(START, "deep_research_collect")
    builder.add_edge("deep_research_collect", "review")
    builder.add_conditional_edges(
        "review",
        route_after_review,  # type: ignore[arg-type]
        {
            "finalize": "finalize",
            "human_review": "human_review",
            "llm_patch": "llm_finalize",
            "verify_items": "llm_finalize",
            "build_targeted_rerun_plan": "deep_research_submit",
            "full_rerun_submit": "deep_research_submit",
            "revise_research_items": "human_review",
            "finalize_with_limitation": "partial_finalize",
        },
    )
    builder.add_edge("llm_finalize", "review")
    builder.add_edge("deep_research_submit", "deep_research_collect")
    builder.add_conditional_edges(
        "human_review",
        _graph_route_after_human_review,
        {
            "finalize": "finalize",
            "review": "review",
            "llm_patch": "llm_finalize",
            "verify_items": "llm_finalize",
            "build_targeted_rerun_plan": "deep_research_submit",
            "full_rerun_submit": "deep_research_submit",
            "revise_research_items": "human_review",
            "finalize_with_limitation": "partial_finalize",
            "partial_finalize": "partial_finalize",
        },
    )
    builder.add_edge("finalize", END)
    builder.add_edge("partial_finalize", END)
    return builder.compile(checkpointer=checkpointer)


def build_phase_1_2_graph(*, checkpointer: Any | None = None) -> Any:
    return build_phase_3_graph(checkpointer=checkpointer)


def build_phase_4_graph(*, checkpointer: Any) -> Any:
    if checkpointer is None:
        raise ValueError("build_phase_4_graph requires a checkpointer for interrupt resume.")

    builder = StateGraph(GraphState)
    builder.add_node("human_review", node_human_review)
    builder.add_node("review", _review_node)
    builder.add_node("finalize", _terminal_node("finalize"))
    builder.add_node("llm_finalize", _llm_finalize_node)
    builder.add_node("deep_research_submit", _deep_research_submit_node)
    builder.add_node("deep_research_collect", _deep_research_collect_node)
    builder.add_node("partial_finalize", _terminal_node("partial_finalize"))

    builder.add_edge(START, "human_review")
    builder.add_conditional_edges(
        "human_review",
        _graph_route_after_human_review,
        {
            "finalize": "finalize",
            "review": "review",
            "llm_patch": "llm_finalize",
            "verify_items": "llm_finalize",
            "build_targeted_rerun_plan": "deep_research_submit",
            "full_rerun_submit": "deep_research_submit",
            "revise_research_items": "human_review",
            "finalize_with_limitation": "partial_finalize",
            "partial_finalize": "partial_finalize",
        },
    )
    builder.add_edge("llm_finalize", "review")
    builder.add_edge("deep_research_submit", "deep_research_collect")
    builder.add_edge("deep_research_collect", "review")
    builder.add_conditional_edges(
        "review",
        route_after_review,  # type: ignore[arg-type]
        {
            "finalize": "finalize",
            "human_review": "human_review",
            "llm_patch": "llm_finalize",
            "verify_items": "llm_finalize",
            "build_targeted_rerun_plan": "deep_research_submit",
            "full_rerun_submit": "deep_research_submit",
            "revise_research_items": "human_review",
            "finalize_with_limitation": "partial_finalize",
        },
    )
    builder.add_edge("finalize", END)
    builder.add_edge("partial_finalize", END)
    return builder.compile(checkpointer=checkpointer)


def node_human_review(state: GraphState) -> dict[str, Any]:
    review = state.get("review", {})
    decision = interrupt(
        {
            "reason": review.get("rationale", ""),
            "latest_report": state.get("report", ""),
            "latest_review": review,
            "allowed_actions": [action.value for action in HumanReviewAction],
            "audit_summary": state.get("audit_summary", {}),
            "warnings": state.get("warnings", []),
        }
    )
    return {"human_decision": decision}


def _terminal_node(name: str) -> Any:
    def node(state: GraphState) -> dict[str, str]:
        return {"graph_terminal": name}

    return node


def _review_node(state: GraphState) -> dict[str, bool]:
    return {"visited_review": True}


def _llm_finalize_node(state: GraphState) -> dict[str, Any]:
    review = state.get("post_llm_review")
    if not isinstance(review, dict):
        review = {"verdict": "pass"}
    return {"visited_llm_finalize": True, "review": review}


def _deep_research_submit_node(state: GraphState) -> dict[str, bool]:
    return {"visited_deep_research_submit": True}


def _deep_research_collect_node(state: GraphState) -> dict[str, Any]:
    review = state.get("post_deep_research_review")
    if not isinstance(review, dict):
        review = state.get("review")
        if (
            state.get("visited_deep_research_submit")
            or not isinstance(review, dict)
        ):
            review = {"verdict": "pass"}
    return {"visited_deep_research_collect": True, "review": review}


def _graph_route_after_human_review(state: GraphState) -> str:
    decision = state.get("human_decision")
    if not isinstance(decision, dict):
        raise ValueError("human_decision must be a dict containing an action.")

    action_value = decision.get("action")
    try:
        action = HumanReviewAction(action_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid human review action: {action_value!r}") from exc

    if action == HumanReviewAction.APPROVE:
        return "finalize"
    if action == HumanReviewAction.APPROVE_WITH_LIMITATION:
        return "partial_finalize"
    if action == HumanReviewAction.REQUEST_REVIEW:
        return "review"
    if action == HumanReviewAction.REQUEST_LLM_PATCH:
        return "llm_patch"
    if action == HumanReviewAction.REQUEST_VERIFICATION:
        return "verify_items"
    if action == HumanReviewAction.REQUEST_TARGETED_RERUN:
        return "build_targeted_rerun_plan"
    if action == HumanReviewAction.REQUEST_ITEM_REVISION:
        return "revise_research_items"
    if action == HumanReviewAction.REJECT:
        return "partial_finalize"
    raise ValueError(f"Invalid human review action: {action_value!r}")
