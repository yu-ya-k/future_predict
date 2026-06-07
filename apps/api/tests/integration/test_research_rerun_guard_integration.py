from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.main import create_app
from api.research.dependencies import get_research_orchestrator
from api.research.poller import ResearchPoller
from api.research.schemas import (
    FailureMode,
    ItemAssessment,
    ItemStatus,
    RecommendedAction,
    RunStatus,
    Severity,
    Verdict,
)
from api.research.service import ResearchOrchestrator
from research_fakes import IntegrationFakeAzure


class FullMergedTargetedRerunFake(IntegrationFakeAzure):
    def retrieve_response(self, response_id: str) -> dict[str, object]:
        response = super().retrieve_response(response_id)
        if response.get("status") != "completed" or response_id != "resp_deep_2":
            return response

        full_merged_report = (
            "調査レポート本文 resp_deep_1\n\n"
            "## Existing Accepted Findings\n\n"
            "これは targeted rerun delta ではなく、既存本文を含む full merged report です。"
        )
        response["output_text"] = full_merged_report
        return response


def _app_for(orchestrator: ResearchOrchestrator) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    return app


def _history_steps(audit: dict[str, Any]) -> list[str]:
    return [
        str(cast(dict[str, Any], event).get("step"))
        for event in audit["history"]
        if isinstance(event, dict)
    ]


def _answered_assessment(item_id: str) -> ItemAssessment:
    return ItemAssessment(
        item_id=item_id,
        status=ItemStatus.ANSWERED,
        severity=Severity.MAJOR,
        failure_mode=FailureMode.NONE,
        failure_mode_confidence=90,
        recommended_action=RecommendedAction.NONE,
        evidence_summary=f"{item_id} is covered.",
        missing_evidence=[],
        rationale=f"{item_id} is sufficiently answered.",
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_targeted_rerun_rejects_full_merged_report_and_preserves_report(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = FullMergedTargetedRerunFake(
        verdicts=[Verdict.NEEDS_TARGETED_RERUN, Verdict.PASS]
    )
    orchestrator = integration_orchestrator_factory(fake)
    app = _app_for(orchestrator)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "公開情報に基づく競合調査をしてください。",
            },
        )
        run_id = create_response.json()["run_id"]

        waiting = orchestrator.collect_deep_research(run_id)
        assert waiting.status == RunStatus.WAITING_DEEP_RESEARCH
        original_report = waiting.report

        poller = ResearchPoller(orchestrator=orchestrator, interval_seconds=0.01)
        await poller.tick()

        status_response = await client.get(f"/research-runs/{run_id}")
        report_response = await client.get(f"/research-runs/{run_id}/report")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    status = status_response.json()
    report = report_response.json()
    audit = audit_response.json()
    persisted = orchestrator.repository.get_run(run_id)

    assert status["status"] == RunStatus.NEEDS_HUMAN_REVIEW.value
    assert status["done_reason"] == "targeted_rerun_merge_rejected"
    assert persisted.report == original_report
    assert report["report"] == original_report
    assert "Existing Accepted Findings" not in (report["report"] or "")
    assert "targeted_rerun_merge_rejected" in _history_steps(audit)


@pytest.mark.integration
@pytest.mark.anyio
async def test_finalize_with_limitation_completes_with_terminal_status_and_audit(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(
        verdicts=[Verdict.FINALIZE_WITH_LIMITATION],
        item_assessments=[
            ItemAssessment(
                item_id="RI-001",
                status=ItemStatus.ANSWERED,
                severity=Severity.BLOCKER,
                failure_mode=FailureMode.NONE,
                failure_mode_confidence=95,
                recommended_action=RecommendedAction.NONE,
                evidence_summary="The core user question is answered.",
                missing_evidence=[],
                rationale="Blocker criterion is satisfied.",
            ),
            ItemAssessment(
                item_id="RI-002",
                status=ItemStatus.ANSWERED,
                severity=Severity.MAJOR,
                failure_mode=FailureMode.NONE,
                failure_mode_confidence=92,
                recommended_action=RecommendedAction.NONE,
                evidence_summary="Decision-critical claims have citations.",
                missing_evidence=[],
                rationale="Citation coverage is sufficient.",
            ),
            ItemAssessment(
                item_id="RI-003",
                status=ItemStatus.ANSWERED,
                severity=Severity.MAJOR,
                failure_mode=FailureMode.NONE,
                failure_mode_confidence=91,
                recommended_action=RecommendedAction.NONE,
                evidence_summary="Volatile facts have dates.",
                missing_evidence=[],
                rationale="Freshness criterion is satisfied.",
            ),
            ItemAssessment(
                item_id="RI-004",
                status=ItemStatus.ANSWERED,
                severity=Severity.MAJOR,
                failure_mode=FailureMode.NONE,
                failure_mode_confidence=90,
                recommended_action=RecommendedAction.NONE,
                evidence_summary="Uncertainty and limitations are stated.",
                missing_evidence=[],
                rationale="Risk review criterion is satisfied.",
            ),
            ItemAssessment(
                item_id="RI-005",
                status=ItemStatus.UNVERIFIABLE,
                severity=Severity.MINOR,
                failure_mode=FailureMode.LIKELY_NOT_PUBLICLY_AVAILABLE,
                failure_mode_confidence=90,
                recommended_action=RecommendedAction.FINALIZE_WITH_LIMITATION,
                evidence_summary=None,
                missing_evidence=["public official source"],
                rationale="Reliable public evidence was not available after targeted checks.",
            ),
        ],
    )
    orchestrator = integration_orchestrator_factory(fake)
    app = _app_for(orchestrator)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "公開情報に基づく市場調査をしてください。",
            },
        )
        run_id = create_response.json()["run_id"]

        completed = orchestrator.collect_deep_research(run_id)

        status_response = await client.get(f"/research-runs/{run_id}")
        report_response = await client.get(f"/research-runs/{run_id}/report")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    status = status_response.json()
    report = report_response.json()
    audit = audit_response.json()

    assert completed.status == RunStatus.COMPLETED
    assert status["status"] == RunStatus.COMPLETED.value
    assert status["terminal_status"] == "completed_with_limitations"
    assert report["final_report"]
    assert "## Limitations" in report["final_report"]
    assert "RI-005" in report["final_report"]
    assert "Reliable public evidence was not available" in report["final_report"]
    assert "finalized_with_limitation" in _history_steps(audit)


@pytest.mark.integration
@pytest.mark.anyio
async def test_finalize_with_limitation_routes_to_human_review_when_required_items_unreviewed(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(
        verdicts=[Verdict.FINALIZE_WITH_LIMITATION],
        item_assessments=[
            ItemAssessment(
                item_id="RI-001",
                status=ItemStatus.PARTIAL,
                severity=Severity.BLOCKER,
                failure_mode=FailureMode.NEEDS_DEEPER_SEARCH,
                failure_mode_confidence=90,
                recommended_action=RecommendedAction.FINALIZE_WITH_LIMITATION,
                evidence_summary=None,
                missing_evidence=["required blocker evidence"],
                rationale="The blocker criterion is still unresolved.",
            ),
            *[_answered_assessment(item_id) for item_id in ("RI-002", "RI-003", "RI-004")],
            ItemAssessment(
                item_id="RI-005",
                status=ItemStatus.UNVERIFIABLE,
                severity=Severity.MINOR,
                failure_mode=FailureMode.LIKELY_NOT_PUBLICLY_AVAILABLE,
                failure_mode_confidence=90,
                recommended_action=RecommendedAction.FINALIZE_WITH_LIMITATION,
                evidence_summary=None,
                missing_evidence=["public official source"],
                rationale="Only the minor recommendation item is unresolved.",
            )
        ],
    )
    orchestrator = integration_orchestrator_factory(fake)
    app = _app_for(orchestrator)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "公開情報に基づく市場調査をしてください。",
            },
        )
        run_id = create_response.json()["run_id"]

        needs_human = orchestrator.collect_deep_research(run_id)

        status_response = await client.get(f"/research-runs/{run_id}")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    status = status_response.json()
    audit = audit_response.json()

    assert needs_human.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert status["status"] == RunStatus.NEEDS_HUMAN_REVIEW.value
    assert status["done_reason"] == "limitation_blocker_unresolved"
    assert status["terminal_status"] is None
    assert "finalize_with_limitation_blocked" in _history_steps(audit)
    assert "finalized_with_limitation" not in _history_steps(audit)


@pytest.mark.integration
@pytest.mark.anyio
async def test_targeted_rerun_budget_guard_routes_to_human_review_without_submit(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(verdicts=[Verdict.NEEDS_TARGETED_RERUN])
    orchestrator = integration_orchestrator_factory(fake)
    app = _app_for(orchestrator)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "公開情報に基づく競合調査をしてください。",
                "options": {"max_targeted_rerun_runs": 0},
            },
        )
        run_id = create_response.json()["run_id"]

        needs_human = orchestrator.collect_deep_research(run_id)

        status_response = await client.get(f"/research-runs/{run_id}")
        human_review_response = await client.get(f"/research-runs/{run_id}/human-review")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    status = status_response.json()
    human_review = human_review_response.json()
    audit = audit_response.json()
    history = cast(list[dict[str, Any]], audit["history"])
    route_events = [
        event for event in history if event.get("step") == "route_after_review"
    ]

    assert needs_human.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert len(fake.submit_calls) == 1
    assert status["status"] == RunStatus.NEEDS_HUMAN_REVIEW.value
    assert status["done_reason"] == "max_targeted_rerun_runs_reached"
    assert human_review["reason"] == "max_targeted_rerun_runs_reached"
    assert status["progress"]["targeted_rerun_runs"] == 0
    assert route_events[-1]["route"] == "human_review"
    assert route_events[-1]["candidate_route"] == "build_targeted_rerun_plan"
    assert route_events[-1]["selected_route"] == "human_review"
    assert route_events[-1]["blocked_reason"] == "max_targeted_rerun_runs_reached"
    assert route_events[-1]["dominant_actions"] == ["targeted_rerun"]
    assert route_events[-1]["budget_snapshot"]["targeted_rerun_runs"] == 0
    assert route_events[-1]["budget_snapshot"]["max_targeted_rerun_runs"] == 0
