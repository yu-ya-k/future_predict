from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from api.research.dependencies import get_research_orchestrator
from api.research.schemas import (
    AuditResponse,
    CancelResponse,
    Citation,
    CreateResearchRunRequest,
    CreateResearchRunResponse,
    HumanReviewResumeRequest,
    HumanReviewResumeResponse,
    ReportResponse,
    ResearchAttempt,
    ResearchRunStatusResponse,
    ReviewRecord,
    RunProgress,
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
    return ResearchRunStatusResponse(
        run_id=run.id,
        status=run.status,
        done_reason=run.done_reason,
        needs_human_review=run.needs_human_review,
        progress=RunProgress(
            deep_research_runs=run.deep_research_runs,
            llm_fix_runs=run.llm_fix_runs,
            total_reviews=run.total_reviews,
            latest_verdict=latest.verdict if latest else None,
            latest_score=latest.score if latest else None,
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
        citations=orchestrator.repository.get_citations(run.id),
        tool_calls=orchestrator.repository.get_tool_calls(run.id),
        history=orchestrator.repository.get_history(run.id),
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


@router.post("/{run_id}/cancel", response_model=CancelResponse)
def cancel_run(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> CancelResponse:
    _get_run_or_404(orchestrator, run_id)
    run = orchestrator.cancel_run(run_id)
    return CancelResponse(run_id=run.id, status=run.status)


@router.post("/{run_id}/resume", response_model=HumanReviewResumeResponse)
def resume_run(
    run_id: UUID,
    request: HumanReviewResumeRequest,
    orchestrator: OrchestratorDependency,
) -> HumanReviewResumeResponse:
    _get_run_or_404(orchestrator, run_id)
    try:
        run = orchestrator.resume_run(run_id, request)
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
