from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from api.research.dependencies import get_research_orchestrator
from api.research.schemas import (
    AuditResponse,
    CancelResponse,
    Citation,
    CostEvent,
    CreateResearchRunRequest,
    CreateResearchRunResponse,
    HumanReviewDecision,
    HumanReviewPayload,
    HumanReviewQueueItem,
    HumanReviewResumeAPIRequest,
    HumanReviewResumeRequest,
    HumanReviewResumeResponse,
    ItemStatus,
    ObjectiveContractResponse,
    ReportResponse,
    RerunPlansResponse,
    ResearchAttempt,
    ResearchItemsResponse,
    ResearchRunStatusResponse,
    ReviewRecord,
    RunProgress,
    Severity,
    ToolCallSummary,
)
from api.research.service import ResearchOrchestrator

router = APIRouter(prefix="/research-runs", tags=["research-runs"])
OrchestratorDependency = Annotated[ResearchOrchestrator, Depends(get_research_orchestrator)]


@router.post("", response_model=CreateResearchRunResponse, status_code=status.HTTP_202_ACCEPTED)
def create_research_run(
    request: CreateResearchRunRequest,
    orchestrator: OrchestratorDependency,
) -> CreateResearchRunResponse:
    run = orchestrator.create_run(request)
    return CreateResearchRunResponse(
        run_id=run.id,
        thread_id=run.thread_id,
        status=run.status,
        created_at=run.created_at,
    )


@router.get("/human-reviews", response_model=list[HumanReviewQueueItem])
def list_human_reviews(
    orchestrator: OrchestratorDependency,
) -> list[HumanReviewQueueItem]:
    return orchestrator.list_human_reviews()


@router.get("/{run_id}", response_model=ResearchRunStatusResponse)
def get_research_run(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> ResearchRunStatusResponse:
    run = _get_run_or_404(orchestrator, run_id)
    return _status_response(orchestrator, run.id)


def _status_response(
    orchestrator: ResearchOrchestrator,
    run_id: UUID,
) -> ResearchRunStatusResponse:
    run = orchestrator.repository.get_run(run_id)
    reviews = orchestrator.repository.get_reviews(run.id)
    latest = reviews[-1] if reviews else None
    estimated_cost_usd = orchestrator.estimate_run_cost_usd(
        run.id,
        fallback=run.estimated_cost_usd,
    )
    return ResearchRunStatusResponse(
        run_id=run.id,
        status=run.status,
        terminal_status=run.terminal_status,
        done_reason=run.done_reason,
        needs_human_review=run.needs_human_review,
        deep_research_submitted_at=orchestrator.repository.get_deep_research_submitted_at(
            run.id
        ),
        progress=RunProgress(
            deep_research_runs=run.deep_research_runs,
            targeted_rerun_runs=run.targeted_rerun_runs,
            full_rerun_runs=run.full_rerun_runs,
            llm_patch_runs=run.llm_patch_runs,
            verification_runs=run.verification_runs,
            total_reviews=run.total_reviews,
            latest_verdict=latest.verdict if latest else None,
            latest_score=latest.score if latest else None,
            **_item_progress_fields(orchestrator, run.id),
            total_tool_calls=run.total_tool_calls,
            estimated_cost_usd=estimated_cost_usd,
        ),
    )


@router.get("/{run_id}/report", response_model=ReportResponse)
def get_report(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> ReportResponse:
    run = _get_run_or_404(orchestrator, run_id)
    return ReportResponse(
        run_id=run.id,
        status=run.status,
        final_report=run.final_report,
        report=run.report,
        warnings=run.warnings,
    )


@router.get("/{run_id}/audit", response_model=AuditResponse)
def get_audit(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> AuditResponse:
    run = _get_run_or_404(orchestrator, run_id)
    return AuditResponse(
        run_id=run.id,
        attempts=orchestrator.repository.get_attempts(run.id),
        reviews=orchestrator.repository.get_reviews(run.id),
        objective_contract=orchestrator.repository.get_objective_contract(run.id),
        research_items=orchestrator.repository.get_research_items(run.id),
        rerun_plans=orchestrator.repository.get_rerun_plans(run.id),
        verification_queries=orchestrator.repository.get_verification_queries(run.id),
        citations=orchestrator.repository.get_citations(run.id),
        tool_calls=orchestrator.repository.get_tool_calls(run.id),
        cost_events=orchestrator.get_cost_events(run.id),
        human_decisions=orchestrator.repository.get_human_decisions(run.id),
        history=orchestrator.repository.get_history(run.id),
    )


@router.get("/{run_id}/contract", response_model=ObjectiveContractResponse)
def get_contract(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> ObjectiveContractResponse:
    run = _get_run_or_404(orchestrator, run_id)
    return ObjectiveContractResponse(
        run_id=run.id,
        contract=orchestrator.repository.get_objective_contract(run.id),
    )


@router.get("/{run_id}/items", response_model=ResearchItemsResponse)
def get_research_items(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> ResearchItemsResponse:
    run = _get_run_or_404(orchestrator, run_id)
    return ResearchItemsResponse(
        run_id=run.id,
        items=orchestrator.repository.get_research_items(run.id),
    )


@router.get("/{run_id}/rerun-plans", response_model=RerunPlansResponse)
def get_rerun_plans(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> RerunPlansResponse:
    run = _get_run_or_404(orchestrator, run_id)
    return RerunPlansResponse(
        run_id=run.id,
        rerun_plans=orchestrator.repository.get_rerun_plans(run.id),
    )


@router.get("/{run_id}/citations", response_model=list[Citation])
def get_citations(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> list[Citation]:
    run = _get_run_or_404(orchestrator, run_id)
    return orchestrator.repository.get_citations(run.id)


@router.get("/{run_id}/reviews", response_model=list[ReviewRecord])
def get_reviews(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> list[ReviewRecord]:
    run = _get_run_or_404(orchestrator, run_id)
    return orchestrator.repository.get_reviews(run.id)


@router.get("/{run_id}/attempts", response_model=list[ResearchAttempt])
def get_attempts(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> list[ResearchAttempt]:
    run = _get_run_or_404(orchestrator, run_id)
    return orchestrator.repository.get_attempts(run.id)


@router.get("/{run_id}/tool-calls", response_model=list[ToolCallSummary])
def get_tool_calls(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> list[ToolCallSummary]:
    run = _get_run_or_404(orchestrator, run_id)
    return orchestrator.repository.get_tool_calls(run.id)


@router.get("/{run_id}/cost-events", response_model=list[CostEvent])
def get_cost_events(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> list[CostEvent]:
    run = _get_run_or_404(orchestrator, run_id)
    return orchestrator.get_cost_events(run.id)


@router.get("/{run_id}/human-review", response_model=HumanReviewPayload)
def get_human_review_payload(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> HumanReviewPayload:
    _get_run_or_404(orchestrator, run_id)
    try:
        return orchestrator.get_human_review_payload(run_id)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


@router.get("/{run_id}/human-decisions", response_model=list[HumanReviewDecision])
def get_human_decisions(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> list[HumanReviewDecision]:
    run = _get_run_or_404(orchestrator, run_id)
    return orchestrator.repository.get_human_decisions(run.id)


@router.post("/{run_id}/cancel", response_model=CancelResponse)
def cancel_run(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> CancelResponse:
    _get_run_or_404(orchestrator, run_id)
    run = orchestrator.cancel_run(run_id)
    return CancelResponse(run_id=run.id, status=run.status)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_research_run(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> None:
    try:
        orchestrator.delete_run(run_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found."
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


@router.post("/{run_id}/resume", response_model=HumanReviewResumeResponse)
def resume_run(
    run_id: UUID,
    request: HumanReviewResumeAPIRequest,
    orchestrator: OrchestratorDependency,
) -> HumanReviewResumeResponse:
    _get_run_or_404(orchestrator, run_id)
    try:
        run = orchestrator.resume_run(
            run_id,
            HumanReviewResumeRequest(
                action=request.action,
                comment=request.comment,
            ),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return HumanReviewResumeResponse(
        run_id=run.id,
        status=run.status,
        done_reason=run.done_reason,
        needs_human_review=run.needs_human_review,
    )


def _get_run_or_404(orchestrator: ResearchOrchestrator, run_id: UUID):
    try:
        return orchestrator.repository.get_run(run_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found."
        ) from error


def _item_progress_fields(
    orchestrator: ResearchOrchestrator,
    run_id: UUID,
) -> dict[str, int]:
    items = orchestrator.repository.get_research_items(run_id)
    return {
        "items_total": len(items),
        "items_answered": sum(item.status == ItemStatus.ANSWERED for item in items),
        "items_partial": sum(item.status == ItemStatus.PARTIAL for item in items),
        "items_unanswered": sum(
            item.status in {ItemStatus.NOT_STARTED, ItemStatus.UNANSWERED}
            for item in items
        ),
        "items_unverifiable": sum(item.status == ItemStatus.UNVERIFIABLE for item in items),
        "blockers_unresolved": sum(
            item.severity == Severity.BLOCKER
            and item.status not in {ItemStatus.ANSWERED, ItemStatus.OUT_OF_SCOPE}
            for item in items
        ),
    }
