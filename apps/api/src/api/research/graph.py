from __future__ import annotations

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from api.research.routing import route_after_review


class GraphState(TypedDict, total=False):
    run_id: str
    review: dict[str, Any]
    route: str
    human_decision: dict[str, Any]


def build_phase_3_graph() -> Any:
    builder = StateGraph(GraphState)
    builder.add_node("deep_research_collect", lambda state: state)
    builder.add_node("review", lambda state: state)
    builder.add_node("llm_finalize", lambda state: state)
    builder.add_node("deep_research_submit", lambda state: state)
    builder.add_node("finalize", lambda state: state)
    builder.add_node("human_review", lambda state: state)
    builder.add_node("partial_finalize", lambda state: state)

    builder.add_edge(START, "deep_research_collect")
    builder.add_edge("deep_research_collect", "review")
    builder.add_conditional_edges(
        "review",
        route_after_review,  # type: ignore[arg-type]
        {
            "finalize": "finalize",
            "human_review": "human_review",
            "llm_finalize": "llm_finalize",
            "deep_research_submit": "deep_research_submit",
        },
    )
    builder.add_edge("llm_finalize", "review")
    builder.add_edge("deep_research_submit", END)
    builder.add_conditional_edges(
        "human_review",
        _graph_route_after_human_review,
        {
            "finalize": "finalize",
            "llm_finalize": "llm_finalize",
            "deep_research_submit": "deep_research_submit",
            "partial_finalize": "partial_finalize",
        },
    )
    builder.add_edge("finalize", END)
    builder.add_edge("partial_finalize", END)
    return builder.compile()


def build_phase_1_2_graph() -> Any:
    return build_phase_3_graph()


def _graph_route_after_human_review(state: GraphState) -> str:
    decision = state.get("human_decision", {}).get("action")
    if decision == "approve":
        return "finalize"
    if decision == "request_llm_fix":
        return "llm_finalize"
    if decision == "request_deep_research":
        return "deep_research_submit"
    return "partial_finalize"
