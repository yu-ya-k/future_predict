from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.encoders import jsonable_encoder

from api.forecast.dependencies import get_forecast_orchestrator
from api.forecast.errors import ForecastConflict, forecast_http_error
from api.forecast.repository import IDEMPOTENCY_IN_PROGRESS
from api.forecast.schemas import (
    CommitVersionRequest,
    CommitVersionResponse,
    ComputeProbabilitiesRequest,
    ComputeProjectionRequest,
    EstimateSetResponse,
    EvidenceExtractResponse,
    ForecastAuditResponse,
    ForecastCreateRequest,
    ForecastCreateResponse,
    ForecastDetail,
    ForecastFramingDraftRequest,
    ForecastFramingDraftResponse,
    ForecastReviewRequest,
    ForecastReviewResponse,
    ForecastSummary,
    ManualResearchPackPromptResponse,
    ProjectionSetResponse,
    ResearchPackDefaultsResponse,
    ResearchPackRequest,
    ResearchPackRerunRequest,
    ResearchPackResponse,
    ResolveForecastRequest,
    ResolveForecastResponse,
    ReviewAction,
    ScenarioGenerateResponse,
)
from api.forecast.service import ForecastOrchestrator
from api.research.dependencies import require_research_api_key

router = APIRouter(
    prefix="/forecasts",
    tags=["forecasts"],
    dependencies=[Depends(require_research_api_key)],
)

ForecastDependency = Annotated[ForecastOrchestrator, Depends(get_forecast_orchestrator)]
IdempotencyKeyHeader = Annotated[
    str | None,
    Header(alias="Idempotency-Key", max_length=200),
]


@router.get("/human-reviews", response_model=list[dict[str, str]])
def list_forecast_human_reviews(
    orchestrator: ForecastDependency,
) -> list[dict[str, str]]:
    try:
        orchestrator.ensure_enabled()
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    return []


@router.get("", response_model=list[ForecastSummary])
def list_forecasts(orchestrator: ForecastDependency) -> list[ForecastSummary]:
    try:
        return orchestrator.list_forecasts()
    except ForecastConflict as error:
        raise forecast_http_error(error) from error


@router.post("", response_model=ForecastCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_forecast(
    request: ForecastCreateRequest,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ForecastCreateResponse:
    normalized_key = _normalize_idempotency_key(idempotency_key)
    try:
        return _run_idempotent(
            orchestrator,
            scope="forecast:create",
            resource_id="",
            idempotency_key=normalized_key,
            payload=_forecast_create_idempotency_payload(request),
            action=lambda: orchestrator.create_forecast(
                request,
                idempotency_key=normalized_key,
            ),
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error


@router.post("/framing-drafts", response_model=ForecastFramingDraftResponse)
def create_framing_draft(
    request: ForecastFramingDraftRequest,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ForecastFramingDraftResponse:
    try:
        return _run_idempotent(
            orchestrator,
            scope="forecast:framing_draft",
            resource_id="",
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload=request.model_dump(mode="json"),
            action=lambda: orchestrator.draft_framing(request),
        )
    except ForecastConflict as error:
        error_status = {
            "framing_draft_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
            "framing_draft_invalid_response": status.HTTP_502_BAD_GATEWAY,
        }.get(error.code, status.HTTP_409_CONFLICT)
        raise forecast_http_error(error, status_code=error_status) from error


@router.get("/{forecast_id}", response_model=ForecastDetail)
def get_forecast(
    forecast_id: UUID,
    orchestrator: ForecastDependency,
) -> ForecastDetail:
    try:
        return orchestrator.get_forecast(forecast_id)
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.post("/{forecast_id}/review", response_model=ForecastReviewResponse)
def review_forecast(
    forecast_id: UUID,
    request: ForecastReviewRequest,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ForecastReviewResponse:
    try:
        def action() -> ForecastReviewResponse:
            if request.action == ReviewAction.APPROVE_FRAMING:
                forecast = orchestrator.approve_framing(
                    forecast_id,
                    comment=request.comment,
                )
                return ForecastReviewResponse(
                    forecast_id=forecast.forecast_id,
                    action=request.action,
                    status=forecast.status,
                    approved_framing_version=forecast.approved_framing_version,
                )
            if request.action == ReviewAction.APPROVE_PHASE_A_VERSION:
                if request.estimate_set_id is None:
                    raise ForecastConflict(
                        "approval_required",
                        "estimate_set_id is required for approve_phase_a_version.",
                    )
                forecast = orchestrator.approve_estimate_set(
                    forecast_id,
                    estimate_set_id=request.estimate_set_id,
                    comment=request.comment,
                )
                return ForecastReviewResponse(
                    forecast_id=forecast.forecast_id,
                    action=request.action,
                    status=forecast.status,
                    approved_framing_version=forecast.approved_framing_version,
                    estimate_set_id=request.estimate_set_id,
                )
            if request.action == ReviewAction.APPROVE_CLAIM_TARGET_LINKS:
                forecast = orchestrator.approve_claim_target_links(
                    forecast_id,
                    comment=request.comment,
                )
                return ForecastReviewResponse(
                    forecast_id=forecast.forecast_id,
                    action=request.action,
                    status=forecast.status,
                    approved_framing_version=forecast.approved_framing_version,
                )
            if request.action == ReviewAction.APPROVE_PROJECTION_PUBLICATION:
                if request.projection_set_id is None:
                    raise ForecastConflict(
                        "approval_required",
                        "projection_set_id is required for approve_projection_publication.",
                    )
                forecast = orchestrator.approve_projection_set(
                    forecast_id,
                    projection_set_id=request.projection_set_id,
                    comment=request.comment,
                )
                return ForecastReviewResponse(
                    forecast_id=forecast.forecast_id,
                    action=request.action,
                    status=forecast.status,
                    approved_framing_version=forecast.approved_framing_version,
                    projection_set_id=request.projection_set_id,
                )
            if request.action in {
                ReviewAction.APPROVE_PRIVATE_DATA_USE,
                ReviewAction.APPROVE_PROBABILITY_PUBLICATION,
                ReviewAction.OVERRIDE_PROBABILITY_WITH_REASON,
                ReviewAction.APPROVE_EXTERNAL_REPORT,
                ReviewAction.APPROVE_TRUSTED_SOURCE,
            }:
                forecast = orchestrator.record_phase_b_review(
                    forecast_id,
                    action=request.action.value,
                    comment=request.comment,
                    reviewer=request.reviewer,
                    reviewer_auth_subject=request.reviewer_auth_subject,
                    policy_decision_id=request.policy_decision_id,
                    review_reason=request.review_reason,
                    estimate_set_id=request.estimate_set_id,
                    version_id=request.version_id,
                )
                return ForecastReviewResponse(
                    forecast_id=forecast.forecast_id,
                    action=request.action,
                    status=forecast.status,
                    approved_framing_version=forecast.approved_framing_version,
                    estimate_set_id=request.estimate_set_id,
                )
            raise HTTPException(status_code=422, detail="Unsupported review action.")

        return _run_idempotent(
            orchestrator,
            scope=f"forecast:review:{request.action.value}",
            resource_id=str(forecast_id),
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload=request.model_dump(mode="json"),
            action=action,
        )
    except ForecastConflict as error:
        if error.code == "reviewer_required":
            raise forecast_http_error(
                error,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from error
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.post("/{forecast_id}/research-packs", response_model=ResearchPackResponse)
def create_research_pack(
    forecast_id: UUID,
    request: ResearchPackRequest,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ResearchPackResponse:
    try:
        return _run_idempotent(
            orchestrator,
            scope="forecast:research_pack",
            resource_id=str(forecast_id),
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload=_research_pack_idempotency_payload(request),
            action=lambda: orchestrator.dispatch_research_pack(
                forecast_id,
                request,
                idempotency_key=None,
            ),
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.get("/{forecast_id}/research-packs", response_model=list[ResearchPackResponse])
def list_research_packs(
    forecast_id: UUID,
    orchestrator: ForecastDependency,
) -> list[ResearchPackResponse]:
    try:
        return orchestrator.list_research_packs(forecast_id)
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.post(
    "/{forecast_id}/research-packs/defaults",
    response_model=ResearchPackDefaultsResponse,
)
def create_default_research_packs(
    forecast_id: UUID,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ResearchPackDefaultsResponse:
    try:
        return _run_idempotent(
            orchestrator,
            scope="forecast:research_pack_defaults",
            resource_id=str(forecast_id),
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload={},
            action=lambda: orchestrator.dispatch_default_research_packs(forecast_id),
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.post(
    "/{forecast_id}/research-packs/{pack_id}/rerun",
    response_model=ResearchPackResponse,
)
def rerun_research_pack(
    forecast_id: UUID,
    pack_id: UUID,
    request: ResearchPackRerunRequest,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ResearchPackResponse:
    try:
        return _run_idempotent(
            orchestrator,
            scope="forecast:research_pack_rerun",
            resource_id=f"{forecast_id}:{pack_id}",
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload=request.model_dump(mode="json"),
            action=lambda: orchestrator.rerun_research_pack(
                forecast_id,
                pack_id,
                request,
            ),
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.get(
    "/{forecast_id}/research-packs/manual-prompt",
    response_model=ManualResearchPackPromptResponse,
)
def get_manual_research_pack_prompt(
    forecast_id: UUID,
    orchestrator: ForecastDependency,
) -> ManualResearchPackPromptResponse:
    try:
        return orchestrator.get_manual_research_pack_prompt(forecast_id)
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.post(
    "/{forecast_id}/research-packs/manual-import",
    response_model=ResearchPackResponse,
)
async def import_manual_research_pack(
    forecast_id: UUID,
    orchestrator: ForecastDependency,
    prompt_sha256: Annotated[
        str,
        Form(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    ],
    idempotency_key: IdempotencyKeyHeader = None,
    report_file: Annotated[UploadFile | None, File()] = None,
    report_text: Annotated[str | None, Form()] = None,
) -> ResearchPackResponse:
    report, report_meta = await _read_manual_research_report(
        file=report_file,
        text=report_text,
        max_chars=orchestrator.settings.research_manual_import_max_report_chars,
        max_file_bytes=orchestrator.settings.research_manual_import_max_file_bytes,
    )
    normalized_prompt_sha256 = prompt_sha256.strip()
    report_sha256 = hashlib.sha256(report.encode("utf-8")).hexdigest()
    source_kind = str(report_meta["source"])
    source_filename = report_meta.get("filename")
    try:
        return _run_idempotent(
            orchestrator,
            scope="forecast:research_pack_manual_import",
            resource_id=str(forecast_id),
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload={
                "prompt_sha256": normalized_prompt_sha256,
                "report_sha256": report_sha256,
                "source_kind": source_kind,
                "source_filename": str(source_filename) if source_filename else None,
            },
            action=lambda: orchestrator.import_manual_research_pack(
                forecast_id,
                prompt_sha256=normalized_prompt_sha256,
                report=report,
                source_kind=source_kind,
                source_filename=str(source_filename) if source_filename else None,
            ),
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.post("/{forecast_id}/evidence/extract", response_model=EvidenceExtractResponse)
def extract_evidence(
    forecast_id: UUID,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> EvidenceExtractResponse:
    try:
        def action() -> EvidenceExtractResponse:
            sources, claims = orchestrator.extract_evidence(forecast_id)
            return EvidenceExtractResponse(
                forecast_id=forecast_id,
                sources=sources,
                claims=claims,
            )

        return _run_idempotent(
            orchestrator,
            scope="forecast:evidence_extract",
            resource_id=str(forecast_id),
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload={},
            action=action,
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.post("/{forecast_id}/scenarios/generate", response_model=ScenarioGenerateResponse)
def generate_scenarios(
    forecast_id: UUID,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ScenarioGenerateResponse:
    try:
        return _run_idempotent(
            orchestrator,
            scope="forecast:scenarios_generate",
            resource_id=str(forecast_id),
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload={},
            action=lambda: ScenarioGenerateResponse(
                forecast_id=forecast_id,
                scenarios=orchestrator.generate_scenarios(forecast_id),
            ),
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.post("/{forecast_id}/probabilities/compute", response_model=EstimateSetResponse)
def compute_probabilities(
    forecast_id: UUID,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
    request: ComputeProbabilitiesRequest | None = None,
) -> dict[str, object]:
    try:
        body = request or ComputeProbabilitiesRequest()
        return _run_idempotent(
            orchestrator,
            scope="forecast:probabilities_compute",
            resource_id=str(forecast_id),
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload=body.model_dump(mode="json"),
            action=lambda: orchestrator.compute_probabilities(forecast_id, body),
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.get("/{forecast_id}/estimate-set", response_model=EstimateSetResponse)
def get_current_estimate_set(
    forecast_id: UUID,
    orchestrator: ForecastDependency,
) -> dict[str, object]:
    try:
        return orchestrator.current_estimate_set_response(forecast_id)
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Estimate set not found.") from error


@router.post("/{forecast_id}/projections/compute", response_model=ProjectionSetResponse)
def compute_projection(
    forecast_id: UUID,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
    request: ComputeProjectionRequest | None = None,
) -> ProjectionSetResponse:
    try:
        body = request or ComputeProjectionRequest()
        return _run_idempotent(
            orchestrator,
            scope="forecast:projections_compute",
            resource_id=str(forecast_id),
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload=body.model_dump(mode="json"),
            action=lambda: orchestrator.compute_projection(forecast_id, body),
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.get("/{forecast_id}/projections/current", response_model=ProjectionSetResponse)
def get_current_projection(
    forecast_id: UUID,
    orchestrator: ForecastDependency,
) -> ProjectionSetResponse:
    try:
        return orchestrator.current_projection_set_response(forecast_id)
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Projection set not found.") from error


@router.post(
    "/{forecast_id}/projections/{projection_set_id}/approve",
    response_model=ForecastReviewResponse,
)
def approve_projection(
    forecast_id: UUID,
    projection_set_id: UUID,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ForecastReviewResponse:
    request = ForecastReviewRequest(
        action=ReviewAction.APPROVE_PROJECTION_PUBLICATION,
        projection_set_id=projection_set_id,
    )
    try:
        def action() -> ForecastReviewResponse:
            forecast = orchestrator.approve_projection_set(
                forecast_id,
                projection_set_id=projection_set_id,
                comment=None,
            )
            return ForecastReviewResponse(
                forecast_id=forecast.forecast_id,
                action=request.action,
                status=forecast.status,
                approved_framing_version=forecast.approved_framing_version,
                projection_set_id=projection_set_id,
            )

        return _run_idempotent(
            orchestrator,
            scope="forecast:projection_approve",
            resource_id=f"{forecast_id}:{projection_set_id}",
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload=request.model_dump(mode="json"),
            action=action,
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Projection set not found.") from error


@router.post("/{forecast_id}/versions/commit", response_model=CommitVersionResponse)
def commit_version(
    forecast_id: UUID,
    request: CommitVersionRequest,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> CommitVersionResponse:
    try:
        return _run_idempotent(
            orchestrator,
            scope="forecast:versions_commit",
            resource_id=str(forecast_id),
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload=request.model_dump(mode="json"),
            action=lambda: orchestrator.commit_version(
                forecast_id,
                estimate_set_id=request.estimate_set_id,
                projection_set_id=request.projection_set_id,
                expected_input_snapshot_hash=request.expected_input_snapshot_hash,
            ),
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        if request.projection_set_id is not None:
            detail = "Projection set not found."
        elif request.estimate_set_id is not None:
            detail = "Estimate set not found."
        else:
            detail = "Forecast not found."
        raise HTTPException(status_code=404, detail=detail) from error


@router.post("/{forecast_id}/resolve", response_model=ResolveForecastResponse)
def resolve_forecast(
    forecast_id: UUID,
    request: ResolveForecastRequest,
    orchestrator: ForecastDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ResolveForecastResponse:
    try:
        return _run_idempotent(
            orchestrator,
            scope="forecast:resolve",
            resource_id=str(forecast_id),
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload=request.model_dump(mode="json"),
            action=lambda: orchestrator.resolve_forecast(
                forecast_id,
                outcome_id=request.outcome_id,
                resolution_notes=request.resolution_notes,
            ),
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


@router.get("/{forecast_id}/audit", response_model=ForecastAuditResponse)
def get_forecast_audit(
    forecast_id: UUID,
    orchestrator: ForecastDependency,
) -> ForecastAuditResponse:
    try:
        return orchestrator.get_audit(forecast_id)
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


def _normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _request_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _forecast_create_idempotency_payload(request: ForecastCreateRequest) -> dict[str, object]:
    payload = request.model_dump(mode="json")
    if payload.get("original_execution_prompt") is None:
        payload.pop("original_execution_prompt", None)
    if payload.get("forecast_mode") == "discrete_outcome":
        payload.pop("forecast_mode", None)
    if payload.get("projection_dimensions") == []:
        payload.pop("projection_dimensions", None)
    return payload


def _research_pack_idempotency_payload(
    request: ResearchPackRequest,
) -> dict[str, object]:
    payload = request.model_dump(mode="json")
    phase_b_defaults: dict[str, object] = {
        "background": True,
        "data_classification": "public",
        "vector_store_ids": [],
        "mcp_server_ids": [],
        "trusted_source_identifiers": [],
        "timeout_sec": None,
        "estimated_cost_budget_usd": None,
    }
    for key, default in phase_b_defaults.items():
        if payload.get(key) == default:
            payload.pop(key, None)
    return payload


async def _read_manual_research_report(
    *,
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
            detail="Provide exactly one of report_file or report_text.",
        )
    if has_text:
        assert text is not None
        stripped = text.strip()
        if not stripped:
            raise HTTPException(status_code=422, detail="report_text must not be blank.")
        if len(stripped) > max_chars:
            raise HTTPException(
                status_code=422,
                detail=f"report exceeds the {max_chars} character limit.",
            )
        return stripped, {
            "source": "text",
            "chars": len(stripped),
            "sha256": hashlib.sha256(stripped.encode("utf-8")).hexdigest(),
        }

    assert file is not None
    filename = file.filename or ""
    if not filename.lower().endswith((".md", ".txt")):
        raise HTTPException(
            status_code=422,
            detail="report_file must be a .md or .txt file.",
        )
    data = await file.read(max_file_bytes + 1)
    if len(data) > max_file_bytes:
        raise HTTPException(
            status_code=422,
            detail=f"report_file exceeds the {max_file_bytes} byte limit.",
        )
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=422, detail="report_file must be valid UTF-8.") from error
    stripped = decoded.strip()
    if not stripped:
        raise HTTPException(status_code=422, detail="report_file must not be blank.")
    if len(stripped) > max_chars:
        raise HTTPException(
            status_code=422,
            detail=f"report exceeds the {max_chars} character limit.",
        )
    return stripped, {
        "source": "file",
        "filename": filename,
        "content_type": file.content_type,
        "bytes": len(data),
        "chars": len(stripped),
        "sha256": hashlib.sha256(stripped.encode("utf-8")).hexdigest(),
    }


def _run_idempotent[T](
    orchestrator: ForecastOrchestrator,
    *,
    scope: str,
    resource_id: str,
    idempotency_key: str | None,
    payload: object,
    action: Callable[[], T],
) -> T:
    if idempotency_key is None:
        return action()
    request_hash = _request_hash(payload)
    existing = orchestrator.repository.reserve_idempotency_record(
        command_scope=scope,
        resource_id=resource_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if existing is not None:
        if existing["request_hash"] != request_hash:
            raise forecast_http_error(
                ForecastConflict(
                    "idempotency_conflict",
                    "Idempotency key was already used with a different request.",
                    {"idempotency_key": idempotency_key, "scope": scope},
                )
            )
        if existing["response_json"] == IDEMPOTENCY_IN_PROGRESS:
            if scope == "forecast:research_pack":
                repaired = orchestrator.existing_research_pack_response(
                    UUID(resource_id),
                    include_fresh_submitting=False,
                )
                if repaired is not None:
                    encoded = jsonable_encoder(repaired)
                    orchestrator.repository.complete_idempotency_record(
                        command_scope=scope,
                        resource_id=resource_id,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        response=encoded,
                    )
                    return cast(T, encoded)
            payload_map = cast(dict[str, object], payload) if isinstance(payload, dict) else None
            report_sha256 = (
                payload_map.get("report_sha256") if payload_map is not None else None
            )
            if (
                scope == "forecast:research_pack_manual_import"
                and isinstance(report_sha256, str)
            ):
                repaired = orchestrator.existing_manual_research_pack_response(
                    UUID(resource_id),
                    report_sha256=report_sha256,
                )
                if repaired is not None:
                    encoded = jsonable_encoder(repaired)
                    orchestrator.repository.complete_idempotency_record(
                        command_scope=scope,
                        resource_id=resource_id,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        response=encoded,
                    )
                    return cast(T, encoded)
            raise forecast_http_error(
                ForecastConflict(
                    "idempotency_in_progress",
                    "Idempotency key is already being processed.",
                    {"idempotency_key": idempotency_key, "scope": scope},
                )
            )
        return json.loads(existing["response_json"])
    try:
        result = action()
    except Exception:
        orchestrator.repository.delete_idempotency_record(
            command_scope=scope,
            resource_id=resource_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        raise
    orchestrator.repository.complete_idempotency_record(
        command_scope=scope,
        resource_id=resource_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        response=jsonable_encoder(result),
    )
    return result
