from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.encoders import jsonable_encoder

from api.forecast.dependencies import get_forecast_orchestrator
from api.forecast.errors import ForecastConflict, forecast_http_error
from api.forecast.repository import IDEMPOTENCY_IN_PROGRESS
from api.forecast.schemas import (
    CommitVersionRequest,
    CommitVersionResponse,
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
    ResearchPackRequest,
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
IdempotencyKeyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.get("/human-reviews", response_model=list[dict[str, str]])
def list_forecast_human_reviews() -> list[dict[str, str]]:
    return []


@router.get("", response_model=list[ForecastSummary])
def list_forecasts(orchestrator: ForecastDependency) -> list[ForecastSummary]:
    return orchestrator.list_forecasts()


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
            payload=request.model_dump(mode="json"),
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
) -> dict[str, object]:
    try:
        return _run_idempotent(
            orchestrator,
            scope="forecast:probabilities_compute",
            resource_id=str(forecast_id),
            idempotency_key=_normalize_idempotency_key(idempotency_key),
            payload={},
            action=lambda: orchestrator.compute_probabilities(forecast_id),
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
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Estimate set not found.") from error


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
                expected_input_snapshot_hash=request.expected_input_snapshot_hash,
            ),
        )
    except ForecastConflict as error:
        raise forecast_http_error(error) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Forecast not found.") from error


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
    return payload


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
