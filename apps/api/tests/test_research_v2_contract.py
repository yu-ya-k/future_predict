from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import create_app
from api.research.dependencies import get_research_orchestrator
from api.research.schemas import (
    REVIEW_RESULT_SCHEMA,
    CreateResearchRunRequest,
    HumanReviewAction,
    ResearchRunOptions,
    Verdict,
)
from research_v2_fakes import V2FakeAzure, make_v2_orchestrator

V2_VERDICTS = {
    "pass",
    "needs_llm_patch",
    "needs_verification",
    "needs_targeted_rerun",
    "needs_full_rerun",
    "needs_item_revision",
    "finalize_with_limitation",
    "human_review",
}

V2_HUMAN_ACTIONS = {
    "approve",
    "approve_with_limitation",
    "request_review",
    "request_llm_patch",
    "request_verification",
    "request_targeted_rerun",
    "request_item_revision",
    "reject",
}

V2_OPTION_FIELDS = {
    "max_targeted_rerun_runs",
    "max_full_rerun_runs",
    "max_llm_patch_runs",
    "max_verification_runs",
    "max_total_iterations",
    "max_total_tool_calls",
}


def _item_assessment(
    *,
    failure_mode: str,
    severity: str,
    recommended_action: str | None = None,
    confidence: int = 90,
) -> dict[str, Any]:
    assessment: dict[str, Any] = {
        "item_id": "RI-001",
        "status": "partial",
        "severity": severity,
        "failure_mode": failure_mode,
        "failure_mode_confidence": confidence,
        "evidence_summary": None,
        "missing_evidence": [],
        "rationale": "focused v2 routing test",
    }
    if recommended_action is not None:
        assessment["recommended_action"] = recommended_action
    return assessment


def _resolve_json_schema_ref(schema: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    ref = value.get("$ref")
    if not isinstance(ref, str):
        return value
    name = ref.removeprefix("#/$defs/")
    return schema["$defs"][name]


def test_v2_review_schema_replaces_legacy_deep_research_verdict() -> None:
    enum_values = set(REVIEW_RESULT_SCHEMA["properties"]["verdict"]["enum"])

    assert enum_values == V2_VERDICTS
    assert "needs_deep_research" not in enum_values
    assert "needs_llm_fix" not in enum_values


def test_v2_verdict_enum_rejects_removed_values() -> None:
    assert {verdict.value for verdict in Verdict} == V2_VERDICTS


def test_v2_human_review_actions_replace_deep_research_resume_action() -> None:
    action_values = {action.value for action in HumanReviewAction}

    assert action_values == V2_HUMAN_ACTIONS
    assert "request_deep_research" not in action_values


def test_v2_create_request_requires_context_classification() -> None:
    schema = CreateResearchRunRequest.model_json_schema()

    assert "context_classification" in schema["required"]
    context_schema = _resolve_json_schema_ref(
        schema,
        schema["properties"]["context_classification"],
    )
    assert set(context_schema["enum"]) == {"public", "internal", "confidential", "mixed"}


def test_v2_options_replace_deep_research_and_no_progress_limits() -> None:
    option_fields = set(ResearchRunOptions.model_json_schema()["properties"])

    assert option_fields == V2_OPTION_FIELDS
    assert "max_deep_research_runs" not in option_fields
    assert "max_no_progress_rounds" not in option_fields


@pytest.mark.anyio
async def test_v2_create_run_accepts_required_context_and_action_budgets(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "Research public battery recycling market data.",
                "context_classification": "public",
                "options": {
                    "max_targeted_rerun_runs": 2,
                    "max_full_rerun_runs": 1,
                    "max_llm_patch_runs": 3,
                    "max_verification_runs": 2,
                    "max_total_iterations": 8,
                    "max_total_tool_calls": 120,
                },
            },
        )

    assert create_response.status_code == 202


@pytest.mark.anyio
async def test_v2_create_run_rejects_missing_context_classification(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={"user_prompt": "Research public battery recycling market data."},
        )

    assert create_response.status_code == 422


@pytest.mark.anyio
async def test_v2_openapi_exposes_contract_items_and_rerun_plan_endpoints() -> None:
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    paths = set(response.json()["paths"])
    assert "/research-runs/{run_id}/contract" in paths
    assert "/research-runs/{run_id}/items" in paths
    assert "/research-runs/{run_id}/rerun-plans" in paths


@pytest.mark.anyio
async def test_v2_status_response_includes_item_progress_schema() -> None:
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    schemas = response.json()["components"]["schemas"]
    status_properties = schemas["ResearchRunStatusResponse"]["properties"]
    progress_properties = schemas["RunProgress"]["properties"]

    assert "terminal_status" in status_properties

    for field in [
        "items_total",
        "items_answered",
        "items_partial",
        "items_unanswered",
        "items_unverifiable",
        "blockers_unresolved",
        "targeted_rerun_runs",
        "full_rerun_runs",
        "llm_patch_runs",
        "verification_runs",
    ]:
        assert field in progress_properties


@pytest.mark.anyio
async def test_v2_human_review_payload_includes_unresolved_items_schema() -> None:
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    payload_properties = response.json()["components"]["schemas"]["HumanReviewPayload"][
        "properties"
    ]
    assert "unresolved_items" in payload_properties


@pytest.mark.parametrize(
    ("failure_mode", "severity", "expected_route"),
    [
        ("format_only", "minor", "llm_patch"),
        ("in_report_but_lost", "major", "llm_patch"),
        ("needs_targeted_verification", "blocker", "verify_items"),
        ("needs_different_sources", "major", "build_targeted_rerun_plan"),
        ("needs_deeper_search", "blocker", "build_targeted_rerun_plan"),
        ("needs_query_reformulation", "minor", "build_targeted_rerun_plan"),
        ("source_contradiction", "minor", "verify_items"),
        ("source_contradiction", "blocker", "build_targeted_rerun_plan"),
        ("likely_not_publicly_available", "minor", "finalize_with_limitation"),
        ("likely_not_publicly_available", "blocker", "human_review"),
        ("criterion_too_ambiguous", "minor", "revise_research_items"),
        ("requires_human_judgment", "major", "human_review"),
    ],
)
def test_v2_route_table_uses_item_failure_modes(
    failure_mode: str,
    severity: str,
    expected_route: str,
) -> None:
    from api.research.routing import route_after_review

    route = route_after_review(
        {
            "review": {
                "verdict": "needs_targeted_rerun",
                "item_assessments": [
                    _item_assessment(failure_mode=failure_mode, severity=severity)
                ],
            },
            "targeted_rerun_runs": 0,
            "max_targeted_rerun_runs": 2,
            "full_rerun_runs": 0,
            "max_full_rerun_runs": 1,
            "llm_patch_runs": 0,
            "max_llm_patch_runs": 3,
            "verification_runs": 0,
            "max_verification_runs": 2,
            "total_reviews": 1,
            "max_total_iterations": 8,
        }
    )

    assert route == expected_route


def test_v2_query_policy_blocks_confidential_public_web_search() -> None:
    from api.research.query_policy import query_policy_gate

    decision = query_policy_gate(
        {
            "item_id": "RI-001",
            "candidate_queries": ["internal project codename market launch date"],
            "contains_sensitive_terms": True,
            "sensitive_terms": ["internal project codename"],
        },
        {"context_classification": "confidential"},
    )

    assert decision.status == "blocked"
    assert decision.safe_queries == []
    assert "confidential" in (decision.blocked_reason or "")


def test_v2_deterministic_merge_rejects_preserved_section_changes() -> None:
    from api.research.merge import (
        PatchDelta,
        RegressionError,
        ReportDocument,
        deterministic_merge,
    )

    report = ReportDocument(
        sections={
            "executive-summary": "Accepted summary",
            "gap-ri-001": "Old unresolved item text",
        },
        mutable_sections={"gap-ri-001"},
        preserve_section_ids={"executive-summary"},
    )
    patch = PatchDelta(
        target_item_id="RI-001",
        section_id="executive-summary",
        operation="replace_section",
        new_text="Changed accepted summary",
        citation_ids=[],
        patch_reason="invalid preserved edit",
    )

    with pytest.raises(RegressionError):
        deterministic_merge(report, [patch])


def test_v2_no_progress_uses_item_status_transitions() -> None:
    from api.research.progress import compute_item_progress

    progress = compute_item_progress(
        previous_items=[
            {
                "item_id": "RI-001",
                "status": "partial",
                "confidence": 50,
                "failure_mode": "needs_different_sources",
            }
        ],
        current_items=[
            {
                "item_id": "RI-001",
                "status": "answered",
                "confidence": 75,
                "failure_mode": "none",
            }
        ],
    )

    assert progress.has_progress is True
    assert progress.newly_answered == 1
    assert progress.no_progress_reason is None


def test_v2_item_progress_treats_answered_to_unverifiable_as_regression() -> None:
    from api.research.progress import compute_item_progress

    progress = compute_item_progress(
        previous_items=[
            {
                "item_id": "RI-001",
                "status": "answered",
                "confidence": 80,
                "failure_mode": "none",
            },
            {
                "item_id": "RI-002",
                "status": "partial",
                "confidence": 50,
                "failure_mode": "needs_different_sources",
            },
        ],
        current_items=[
            {
                "item_id": "RI-001",
                "status": "unverifiable",
                "confidence": 70,
                "failure_mode": "likely_not_publicly_available",
            },
            {
                "item_id": "RI-002",
                "status": "answered",
                "confidence": 80,
                "failure_mode": "none",
            },
        ],
    )

    assert progress.regressions == 1
    assert progress.newly_answered == 1
    assert progress.has_progress is False


def test_v2_no_progress_count_does_not_reset_when_same_review_has_regression() -> None:
    from api.research.progress import compute_no_progress_count
    from api.research.schemas import ReviewRecord

    def review(
        *,
        review_no: int,
        first_status: str,
        second_status: str,
    ) -> ReviewRecord:
        return ReviewRecord.model_validate(
            {
                "verdict": "needs_targeted_rerun",
                "goal_achieved": False,
                "score": 60 + review_no,
                "rationale": "progress regression test",
                "item_assessments": [
                    {
                        "item_id": "RI-001",
                        "status": first_status,
                        "severity": "major",
                        "failure_mode": "none",
                        "failure_mode_confidence": 80,
                        "recommended_action": "none",
                        "evidence_summary": None,
                        "missing_evidence": [],
                        "rationale": "first item",
                    },
                    {
                        "item_id": "RI-002",
                        "status": second_status,
                        "severity": "major",
                        "failure_mode": "needs_different_sources",
                        "failure_mode_confidence": 80,
                        "recommended_action": "targeted_rerun",
                        "evidence_summary": None,
                        "missing_evidence": [],
                        "rationale": "second item",
                    },
                ],
                "gaps": [],
                "factuality_concerns": [],
                "source_quality_concerns": [],
                "freshness_concerns": [],
                "security_concerns": [],
                "reviewer_confidence": 90,
                "high_risk_flags": [],
                "public_web_search_used": True,
                "review_no": review_no,
                "recommended_route": "needs_targeted_rerun",
                "report_hash": f"hash-{review_no}",
            }
        )

    count = compute_no_progress_count(
        previous_reviews=[review(review_no=1, first_status="answered", second_status="partial")],
        current_review=review(review_no=2, first_status="unverifiable", second_status="answered"),
        current_no_progress_count=1,
    )

    assert count == 2
