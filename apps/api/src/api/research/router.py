from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import ValidationError

from api.research.dependencies import get_research_orchestrator, require_research_api_key
from api.research.schemas import (
    AuditResponse,
    CancelResponse,
    Citation,
    CostEvent,
    CreateResearchRunRequest,
    CreateResearchRunResponse,
    ForkPreviewRequest,
    ForkPreviewResponse,
    ForkSubmitRequest,
    ForkSubmitResponse,
    HumanReviewDecision,
    HumanReviewPayload,
    HumanReviewQueueItem,
    HumanReviewResumeAPIRequest,
    HumanReviewResumeRequest,
    HumanReviewResumeResponse,
    ItemStatus,
    ObjectiveContractResponse,
    ReportResponse,
    RerunExecutionMode,
    RerunPlansResponse,
    ResearchAttempt,
    ResearchCheckpoint,
    ResearchCheckpointsResponse,
    ResearchItemsResponse,
    ResearchRunLineageResponse,
    ResearchRunOptions,
    ResearchRunStatusResponse,
    ReviewRecord,
    RunProgress,
    Severity,
    ToolCallSummary,
)
from api.research.service import ResearchOrchestrator

router = APIRouter(
    prefix="/research-runs",
    tags=["research-runs"],
    dependencies=[Depends(require_research_api_key)],
)
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


@router.post(
    "/manual-import",
    response_model=CreateResearchRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_manual_import_run(
    background_tasks: BackgroundTasks,
    orchestrator: OrchestratorDependency,
    allow_remote_review: Annotated[bool, Form()],
    allow_api_reruns: Annotated[bool, Form()],
    rerun_execution_mode: Annotated[str | None, Form()] = None,
    input_prompt_file: Annotated[UploadFile | None, File()] = None,
    input_prompt_text: Annotated[str | None, Form(max_length=50000)] = None,
    report_file: Annotated[UploadFile | None, File()] = None,
    report_text: Annotated[str | None, Form()] = None,
    options_json: Annotated[str | None, Form()] = None,
    idempotency_key: Annotated[str | None, Form(max_length=200)] = None,
) -> CreateResearchRunResponse:
    input_prompt, input_meta = await _read_manual_import_text(
        label="input_prompt",
        file=input_prompt_file,
        text=input_prompt_text,
        max_chars=50000,
        max_file_bytes=orchestrator.settings.research_manual_import_max_file_bytes,
    )
    report, report_meta = await _read_manual_import_text(
        label="report",
        file=report_file,
        text=report_text,
        max_chars=orchestrator.settings.research_manual_import_max_report_chars,
        max_file_bytes=orchestrator.settings.research_manual_import_max_file_bytes,
    )
    options = _parse_manual_options(options_json)
    mode = _parse_rerun_execution_mode(
        rerun_execution_mode,
        allow_api_reruns=allow_api_reruns,
    )
    normalized_idempotency_key = idempotency_key.strip() if idempotency_key else None
    if normalized_idempotency_key == "":
        normalized_idempotency_key = None

    try:
        run, dispatch_review = orchestrator.create_manual_import_run(
            input_prompt=input_prompt,
            report=report,
            options=options,
            allow_remote_review=allow_remote_review,
            allow_api_reruns=allow_api_reruns,
            rerun_execution_mode=mode,
            idempotency_key=normalized_idempotency_key,
            metadata={
                "input_prompt": input_meta,
                "report": report_meta,
            },
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    if dispatch_review:
        background_tasks.add_task(orchestrator.review_run, run.id)

    return CreateResearchRunResponse(
        run_id=run.id,
        thread_id=run.thread_id,
        status=run.status,
        created_at=run.created_at,
    )


@router.post(
    "/{run_id}/manual-rerun-result",
    response_model=ResearchRunStatusResponse,
)
async def upload_manual_rerun_result(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
    rerun_id: Annotated[str, Form(max_length=200)],
    report_file: Annotated[UploadFile | None, File()] = None,
    report_text: Annotated[str | None, Form()] = None,
) -> ResearchRunStatusResponse:
    _get_run_or_404(orchestrator, run_id)
    report, _report_meta = await _read_manual_import_text(
        label="report",
        file=report_file,
        text=report_text,
        max_chars=orchestrator.settings.research_manual_import_max_report_chars,
        max_file_bytes=orchestrator.settings.research_manual_import_max_file_bytes,
    )
    normalized_rerun_id = rerun_id.strip()
    if not normalized_rerun_id:
        raise HTTPException(status_code=422, detail="rerun_id must not be blank.")
    try:
        orchestrator.accept_manual_rerun_result(
            run_id,
            rerun_id=normalized_rerun_id,
            report_text=report,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return _status_response(orchestrator, run_id)


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
    cost_events = orchestrator.get_cost_events(run.id)
    return AuditResponse(
        run_id=run.id,
        attempts=orchestrator.repository.get_attempts(run.id),
        reviews=orchestrator.repository.get_reviews(run.id),
        llm_calls=_llm_calls(cost_events),
        objective_contract=orchestrator.repository.get_objective_contract(run.id),
        research_items=orchestrator.repository.get_research_items(run.id),
        rerun_plans=orchestrator.repository.get_rerun_plans(run.id),
        verification_queries=orchestrator.repository.get_verification_queries(run.id),
        citations=orchestrator.repository.get_citations(run.id),
        tool_calls=orchestrator.repository.get_tool_calls(run.id),
        cost_events=cost_events,
        human_decisions=orchestrator.repository.get_human_decisions(run.id),
        history=orchestrator.repository.get_history(run.id),
    )


@router.get("/{run_id}/checkpoints", response_model=ResearchCheckpointsResponse)
def list_checkpoints(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
    include_forks: bool = True,
) -> ResearchCheckpointsResponse:
    run = _get_run_or_404(orchestrator, run_id)
    return ResearchCheckpointsResponse(
        run_id=run.id,
        checkpoints=orchestrator.repository.list_checkpoints(
            run.id,
            include_forks=include_forks,
        ),
    )


@router.get("/{run_id}/checkpoints/{checkpoint_id}", response_model=ResearchCheckpoint)
def get_checkpoint(
    run_id: UUID,
    checkpoint_id: UUID,
    orchestrator: OrchestratorDependency,
) -> ResearchCheckpoint:
    run = _get_run_or_404(orchestrator, run_id)
    try:
        return orchestrator.repository.get_checkpoint(
            run.id,
            checkpoint_id,
            include_forks=True,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Checkpoint not found.",
        ) from error


@router.post(
    "/{run_id}/checkpoints/{checkpoint_id}/fork-preview",
    response_model=ForkPreviewResponse,
)
def fork_preview(
    run_id: UUID,
    checkpoint_id: UUID,
    request: ForkPreviewRequest,
    orchestrator: OrchestratorDependency,
) -> ForkPreviewResponse:
    _get_run_or_404(orchestrator, run_id)
    try:
        return orchestrator.build_fork_preview(
            run_id,
            checkpoint_id,
            additional_prompt=request.additional_prompt,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Checkpoint not found.",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


@router.post(
    "/{run_id}/checkpoints/{checkpoint_id}/forks",
    response_model=ForkSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_fork(
    run_id: UUID,
    checkpoint_id: UUID,
    request: ForkSubmitRequest,
    orchestrator: OrchestratorDependency,
) -> ForkSubmitResponse:
    _get_run_or_404(orchestrator, run_id)
    try:
        return orchestrator.fork_from_checkpoint(
            run_id,
            checkpoint_id,
            additional_prompt=request.additional_prompt,
            idempotency_key=request.idempotency_key,
            confirmed_preview_hash=request.confirmed_preview_hash,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Checkpoint not found.",
        ) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


@router.get("/{run_id}/lineage", response_model=ResearchRunLineageResponse)
def get_lineage(
    run_id: UUID,
    orchestrator: OrchestratorDependency,
) -> ResearchRunLineageResponse:
    run = _get_run_or_404(orchestrator, run_id)
    lineage = orchestrator.repository.get_lineage(run.id)
    if lineage is None:
        return ResearchRunLineageResponse(
            run_id=run.id,
            lineage=None,
            child_forks=orchestrator.repository.list_child_forks(run.id),
        )
    return ResearchRunLineageResponse(
        run_id=run.id,
        root_run_id=lineage.root_run_id,
        parent_run_id=lineage.parent_run_id,
        forked_from_checkpoint_id=lineage.forked_from_checkpoint_id,
        fork_mode=lineage.fork_mode,
        additional_prompt=lineage.additional_prompt,
        confirmed_preview_hash=lineage.confirmed_preview_hash,
        idempotency_key=lineage.idempotency_key,
        source_snapshot_json=lineage.source_snapshot_json,
        source_report_artifact_path=lineage.source_report_artifact_path,
        created_at=lineage.created_at,
        lineage=lineage,
        child_forks=orchestrator.repository.list_child_forks(run.id),
    )


LLM_CALL_STEPS = {
    "deep_research",
    "review",
    "review_failed",
    "review_ignored",
    "llm_finalize",
    "llm_finalize_ignored",
    "verification",
}


def _llm_calls(cost_events: list[CostEvent]) -> list[CostEvent]:
    return [event for event in cost_events if event.step in LLM_CALL_STEPS]


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
    try:
        run = orchestrator.cancel_run(run_id)
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
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


async def _read_manual_import_text(
    *,
    label: str,
    file: UploadFile | None,
    text: str | None,
    max_chars: int,
    max_file_bytes: int,
) -> tuple[str, dict[str, object]]:
    has_file = file is not None
    has_text = text is not None
    if has_file == has_text:
        raise HTTPException(
            status_code=422,
            detail=f"Provide exactly one of {label}_file or {label}_text.",
        )

    if has_text:
        assert text is not None
        stripped = text.strip()
        if not stripped:
            raise HTTPException(
                status_code=422,
                detail=f"{label}_text must not be blank.",
            )
        if len(stripped) > max_chars:
            raise HTTPException(
                status_code=422,
                detail=f"{label} exceeds the {max_chars} character limit.",
            )
        return stripped, {
            "source": "text",
            "chars": len(stripped),
            "sha256": _sha256_text(stripped),
        }

    assert file is not None
    filename = file.filename or ""
    if not filename.lower().endswith((".md", ".txt")):
        raise HTTPException(
            status_code=422,
            detail=f"{label}_file must be a .md or .txt file.",
        )
    data = await file.read(max_file_bytes + 1)
    if len(data) > max_file_bytes:
        raise HTTPException(
            status_code=422,
            detail=f"{label}_file exceeds the {max_file_bytes} byte limit.",
        )
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=422,
            detail=f"{label}_file must be valid UTF-8.",
        ) from error
    stripped = decoded.strip()
    if not stripped:
        raise HTTPException(
            status_code=422,
            detail=f"{label}_file must not be blank.",
        )
    if len(stripped) > max_chars:
        raise HTTPException(
            status_code=422,
            detail=f"{label} exceeds the {max_chars} character limit.",
        )
    return stripped, {
        "source": "file",
        "filename": filename,
        "content_type": file.content_type,
        "bytes": len(data),
        "chars": len(stripped),
        "sha256": _sha256_text(stripped),
    }


def _parse_manual_options(options_json: str | None) -> ResearchRunOptions:
    if options_json is None or not options_json.strip():
        return ResearchRunOptions()
    try:
        return ResearchRunOptions.model_validate_json(options_json)
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail=error.errors(),
        ) from error


def _parse_rerun_execution_mode(
    value: str | None,
    *,
    allow_api_reruns: bool,
) -> RerunExecutionMode:
    if value is None or not value.strip():
        return RerunExecutionMode.API if allow_api_reruns else RerunExecutionMode.DISABLED
    try:
        mode = RerunExecutionMode(value.strip())
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail="rerun_execution_mode must be one of: api, manual_chatgpt, disabled.",
        ) from error
    if not allow_api_reruns and mode == RerunExecutionMode.API:
        return RerunExecutionMode.DISABLED
    return mode


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
