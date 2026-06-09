from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from api.config import Settings
from api.forecast import probability
from api.forecast.artifacts import ForecastArtifactStore
from api.forecast.errors import ForecastConflict
from api.forecast.pack_orchestration import (
    cache_key_for_pack,
    default_pack_requests,
    resolve_forecast_tools,
)
from api.forecast.policy import evaluate_forecast_policy
from api.forecast.repository import ForecastRepository, ResearchPackAlreadyExists
from api.forecast.research_packs import (
    CURRENT_STATE_PROMPT_VERSION,
    PHASE_B_PACK_PROMPT_VERSION,
    build_current_state_prompt,
    build_research_pack_prompt,
)
from api.forecast.schemas import (
    ClaimRecord,
    CommitVersionResponse,
    ComputeProbabilitiesRequest,
    ConfidentialityClass,
    ForecastAuditEvent,
    ForecastAuditResponse,
    ForecastCreateRequest,
    ForecastCreateResponse,
    ForecastCurrentResearchPack,
    ForecastDetail,
    ForecastFramingDraft,
    ForecastFramingDraftRequest,
    ForecastFramingDraftResponse,
    ForecastOutcome,
    ForecastStatus,
    ForecastSummary,
    ManualResearchPackPromptResponse,
    PackRole,
    ProbabilityEstimateRecord,
    ResearchPackDefaultsResponse,
    ResearchPackRequest,
    ResearchPackRerunRequest,
    ResearchPackResponse,
    ResolveForecastResponse,
    ScenarioRecord,
    SourceRecord,
    ToolProfile,
)
from api.forecast.trust import (
    ensure_reviewer_for_action,
    ensure_trusted_sources_allowed,
    require_policy_tools_match,
)
from api.research.query_policy import contains_sensitive_terms
from api.research.schemas import (
    CreateResearchRunRequest,
    ResearchAttempt,
    ResearchRunOptions,
    RunStatus,
    utc_now,
)
from api.research.service import ResearchOrchestrator

_MANUAL_RECOVERABLE_RUN_STATUSES = {
    RunStatus.NEEDS_HUMAN_REVIEW,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


class ForecastOrchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: ForecastRepository,
        artifacts: ForecastArtifactStore,
        research_orchestrator: ResearchOrchestrator,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.artifacts = artifacts
        self.research = research_orchestrator

    def _ensure_enabled(self) -> None:
        if not self.settings.forecast_enabled:
            raise ForecastConflict("forecast_disabled", "Forecast API is disabled.")

    @staticmethod
    def _ensure_not_resolved(forecast: ForecastDetail) -> None:
        if forecast.resolved_at is not None or forecast.status == ForecastStatus.RESOLVED:
            raise ForecastConflict(
                "forecast_already_resolved",
                "Forecast has already been resolved.",
            )

    @staticmethod
    def _ensure_not_committed(forecast: ForecastDetail) -> None:
        if forecast.committed_version_id is not None or forecast.status == ForecastStatus.COMMITTED:
            raise ForecastConflict(
                "estimate_set_already_committed",
                "Committed forecasts cannot be modified in PhaseA.",
            )

    def _ensure_mutable(
        self,
        forecast: ForecastDetail,
        *,
        allow_committed: bool = False,
    ) -> None:
        self._ensure_enabled()
        self._ensure_not_resolved(forecast)
        if not allow_committed:
            self._ensure_not_committed(forecast)

    def create_forecast(
        self,
        request: ForecastCreateRequest,
        *,
        idempotency_key: str | None,
    ) -> ForecastCreateResponse:
        self._ensure_enabled()
        row = self.repository.create_forecast(
            question=request.question,
            original_execution_prompt=request.original_execution_prompt,
            resolution_date=request.resolution_date,
            target_population=request.target_population,
            unit_of_analysis=request.unit_of_analysis,
            resolution_criteria=request.resolution_criteria,
            resolution_sources=request.resolution_sources,
            decision_context=request.decision_context,
            confidentiality_class=request.confidentiality_class,
            outcome_labels=request.outcomes,
            idempotency_key=idempotency_key,
        )
        return ForecastCreateResponse(
            forecast_id=UUID(row["id"]),
            status=ForecastStatus(row["status"]),
            framing_version=row["current_framing_version"],
            created_at=ForecastRepository.forecast_row_to_dict(row)["created_at"],
        )

    def draft_framing(
        self,
        request: ForecastFramingDraftRequest,
    ) -> ForecastFramingDraftResponse:
        self._ensure_enabled()
        _ensure_framing_inputs_are_public(request)
        model = getattr(self.research.azure, "reviewer_deployment", None)
        if not model:
            raise ForecastConflict(
                "framing_draft_unavailable",
                "Forecast framing draft generation is not configured.",
            )
        prompt = _build_framing_draft_prompt(request)
        try:
            parsed, response_id, _raw_response = self.research.azure.parse_structured(
                model=model,
                prompt=prompt,
                text_format=ForecastFramingDraft,
                tool_profile="synthesis",
            )
        except Exception as error:
            raise ForecastConflict(
                "framing_draft_unavailable",
                "Forecast framing draft generation is temporarily unavailable.",
            ) from error

        try:
            draft = _coerce_framing_draft(parsed)
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
            raise ForecastConflict(
                "framing_draft_invalid_response",
                "Forecast framing draft generation returned an invalid response.",
            ) from error
        draft = _framing_draft_without_answered_questions(
            draft,
            answers=request.answers,
            previous_draft=request.previous_draft,
        )

        ready_to_create = _framing_draft_ready_to_create(
            draft,
        )
        warnings: list[str] = []
        if not ready_to_create:
            warnings.append("required_clarifying_answers_missing")
        create_payload = (
            _framing_create_payload(draft, original_execution_prompt=request.rough_question)
            if ready_to_create
            else None
        )
        return ForecastFramingDraftResponse(
            draft=draft,
            create_payload=create_payload,
            ready_to_create=ready_to_create,
            model=model,
            response_id=response_id,
            warnings=warnings,
        )

    def list_forecasts(self) -> list[ForecastSummary]:
        return [
            ForecastSummary(**ForecastRepository.forecast_row_to_dict(row))
            for row in self.repository.list_forecasts()
        ]

    def get_forecast(self, forecast_id: UUID) -> ForecastDetail:
        row = self.repository.get_forecast(forecast_id)
        base = ForecastRepository.forecast_row_to_dict(row)
        outcomes = [
            _outcome_response(outcome)
            for outcome in self.repository.get_outcomes(
                forecast_id,
                framing_version=row["current_framing_version"],
            )
        ]
        packs = self.repository.list_packs(forecast_id)
        reconciled_packs = [self._reconcile_research_pack(pack) for pack in packs]
        current_pack_row = reconciled_packs[-1] if reconciled_packs else None
        current_pack = (
            self._current_research_pack_summary(current_pack_row)
            if current_pack_row is not None
            else None
        )
        return ForecastDetail(
            **base,
            outcomes=outcomes,
            current_research_pack=current_pack,
            current_research_pack_status=(
                current_pack.effective_status if current_pack is not None else None
            ),
            research_packs=[
                self._current_research_pack_summary(pack) for pack in reconciled_packs
            ],
            approved_claim_target_link_count=len(
                self.repository.get_approved_target_links(forecast_id)
            ),
        )

    def _current_research_pack_status(self, pack: Any) -> str | None:
        return self._current_research_pack_summary(pack).effective_status

    def existing_research_pack_response(
        self,
        forecast_id: UUID,
        *,
        include_fresh_submitting: bool = True,
    ) -> ResearchPackResponse | None:
        packs = self.repository.list_packs(forecast_id)
        if not packs:
            return None
        pack = self._reconcile_research_pack(packs[-1])
        if not include_fresh_submitting and pack["status"] == "submitting":
            return None
        return _pack_response(pack)

    def existing_manual_research_pack_response(
        self,
        forecast_id: UUID,
        *,
        report_sha256: str,
    ) -> ResearchPackResponse | None:
        packs = self.repository.list_packs(forecast_id)
        if not packs:
            return None
        pack = self._reconcile_research_pack(packs[-1])
        if pack["status"] != "completed":
            return None
        if pack["report_artifact_hash"] != report_sha256:
            return None
        return _pack_response(pack)

    def _reconcile_research_pack(self, pack: Any) -> Any:
        pack_status = pack["status"]
        if pack_status == "completed":
            return pack
        run = self.research.repository.get_run(UUID(pack["research_run_id"]))
        if (
            pack_status == "submitting"
            and run.status in {RunStatus.QUEUED, RunStatus.SUBMITTED}
            and run.pending_deep_research_response_id is None
            and _is_stale_timestamp(
                pack["updated_at"],
                seconds=self.settings.research_deep_research_submit_stale_seconds,
            )
        ):
            run = self.research.mark_submit_stalled(run.id)
            if run.status == RunStatus.NEEDS_HUMAN_REVIEW:
                return self.repository.update_research_pack_status(
                    pack_id=UUID(pack["pack_id"]),
                    status=run.status.value,
                )
        if (
            run.status == RunStatus.WAITING_DEEP_RESEARCH
            and run.pending_deep_research_response_id is None
        ):
            run = (
                self.research.repository.update_run_if_status(
                    run.id,
                    {RunStatus.WAITING_DEEP_RESEARCH},
                    status=RunStatus.NEEDS_HUMAN_REVIEW,
                    needs_human_review=True,
                    pending_deep_research_response_id=None,
                    done_reason="missing_deep_research_response_id",
                )
                or self.research.repository.get_run(run.id)
            )
        if run.status == RunStatus.COMPLETED:
            report = (run.final_report or run.report or "").strip()
            return self.repository.update_research_pack_status(
                pack_id=UUID(pack["pack_id"]),
                status="completed",
                report_artifact_hash=(
                    hashlib.sha256(report.encode("utf-8")).hexdigest()
                    if report
                    else None
                ),
            )
        if run.status in {
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.NEEDS_HUMAN_REVIEW,
        } and pack_status != run.status.value:
            return self.repository.update_research_pack_status(
                pack_id=UUID(pack["pack_id"]),
                status=run.status.value,
            )
        if (
            pack_status == "submitting"
            and run.status in {RunStatus.WAITING_DEEP_RESEARCH, RunStatus.COLLECTING}
            and run.pending_deep_research_response_id
        ):
            return self.repository.update_research_pack_status(
                pack_id=UUID(pack["pack_id"]),
                status="running",
            )
        return pack

    def _current_research_pack_summary(self, pack: Any) -> ForecastCurrentResearchPack:
        pack_status = pack["status"]
        run_id = UUID(pack["research_run_id"])
        run = self.research.repository.get_run(run_id)
        attempts = self.research.repository.get_attempts(run_id)
        if pack_status == "completed":
            effective_status = "completed"
        elif run.status == RunStatus.COMPLETED:
            effective_status = "completed"
        elif run.status in {
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.NEEDS_HUMAN_REVIEW,
        }:
            effective_status = run.status.value
        else:
            effective_status = pack_status

        return ForecastCurrentResearchPack(
            pack_id=UUID(pack["pack_id"]),
            research_run_id=run_id,
            pack_role=PackRole(pack["pack_role"]),
            tool_profile=ToolProfile(pack["tool_profile"]),
            pack_status=pack_status,
            effective_status=effective_status,
            research_run_status=run.status.value,
            pack_created_at=_parse_dt_required(pack["created_at"]),
            pack_updated_at=_parse_dt_required(pack["updated_at"]),
            research_run_created_at=getattr(run, "created_at", None),
            research_run_updated_at=getattr(run, "updated_at", None),
            deep_research_started_at=(
                self.research.repository.get_deep_research_submitted_at(run_id)
            ),
            total_tool_calls=run.total_tool_calls,
            estimated_cost_usd=self.research.estimate_run_cost_usd(
                run_id,
                fallback=run.estimated_cost_usd,
            ),
            done_reason=run.done_reason,
            last_error=_current_research_attempt_error(attempts, effective_status),
            needs_human_review=run.needs_human_review,
        )

    def approve_framing(self, forecast_id: UUID, *, comment: str | None) -> ForecastDetail:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        if forecast.status not in {
            ForecastStatus.FRAMING_PENDING,
            ForecastStatus.FRAMING_APPROVED,
        }:
            raise ForecastConflict(
                "forecast_already_started",
                "Framing cannot be changed after the forecast has started.",
            )
        self.repository.approve_framing(forecast_id, comment=comment)
        return self.get_forecast(forecast_id)

    def approve_estimate_set(
        self,
        forecast_id: UUID,
        *,
        estimate_set_id: UUID,
        comment: str | None,
    ) -> ForecastDetail:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        if forecast.status != ForecastStatus.DRAFT_READY:
            raise ForecastConflict(
                "approval_required",
                "Compute a PhaseA draft before approving it.",
            )
        estimate_set = self.repository.get_estimate_set(estimate_set_id)
        if UUID(estimate_set["forecast_id"]) != forecast.forecast_id:
            raise ForecastConflict(
                "approval_required",
                "Estimate set does not belong to this forecast.",
                {"estimate_set_id": str(estimate_set_id)},
            )
        if estimate_set["engine_version"] != "phase_a_v1":
            raise ForecastConflict(
                "reviewer_required",
                "Phase B estimate sets require approve_probability_publication with reviewer.",
                {"estimate_set_id": str(estimate_set_id)},
            )
        self.repository.approve_estimate_set(
            forecast_id,
            estimate_set_id=estimate_set_id,
            comment=comment,
        )
        return self.get_forecast(forecast_id)

    def approve_claim_target_links(
        self,
        forecast_id: UUID,
        *,
        comment: str | None,
    ) -> ForecastDetail:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        if not self.repository.get_claims(forecast.forecast_id):
            raise ForecastConflict("evidence_not_ready", "Extract evidence first.")
        self.repository.approve_claim_target_links(forecast.forecast_id, comment=comment)
        return self.get_forecast(forecast_id)

    def record_phase_b_review(
        self,
        forecast_id: UUID,
        *,
        action: str,
        comment: str | None,
        reviewer: str | None,
        reviewer_auth_subject: str | None,
        policy_decision_id: UUID | None,
        review_reason: str | None,
        estimate_set_id: UUID | None,
        version_id: UUID | None,
    ) -> ForecastDetail:
        forecast = self.get_forecast(forecast_id)
        self._ensure_enabled()
        ensure_reviewer_for_action(action, reviewer)
        if estimate_set_id is not None:
            estimate_set = self.repository.get_estimate_set(estimate_set_id)
            if UUID(estimate_set["forecast_id"]) != forecast.forecast_id:
                raise ForecastConflict(
                    "approval_required",
                    "Estimate set does not belong to this forecast.",
                    {"estimate_set_id": str(estimate_set_id)},
                )
        self.repository.add_review_record(
            forecast_id=forecast.forecast_id,
            action=action,
            comment=comment,
            reviewer=reviewer,
            reviewer_auth_subject=reviewer_auth_subject,
            policy_decision_id=policy_decision_id,
            review_reason=review_reason,
            estimate_set_id=estimate_set_id,
            version_id=version_id,
        )
        return self.get_forecast(forecast_id)

    def dispatch_research_pack(
        self,
        forecast_id: UUID,
        request: ResearchPackRequest,
        *,
        idempotency_key: str | None = None,
    ) -> ResearchPackResponse:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        _ensure_forecast_has_outcomes(forecast)
        if forecast.approved_framing_version != forecast.current_framing_version:
            raise ForecastConflict(
                "framing_not_approved",
                "Approve the latest framing before dispatching research packs.",
            )
        if request.tool_profile == ToolProfile.SYNTHESIS:
            raise ForecastConflict(
                "synthesis_not_dispatchable",
                "Synthesis profile cannot submit Deep Research packs.",
            )
        existing = [
            pack
            for pack in self.repository.list_packs(forecast_id)
            if pack["pack_role"] == request.pack_role.value
            and pack["tool_profile"] == request.tool_profile.value
            and bool(pack["is_active"])
        ]
        if existing:
            return _pack_response(self._reconcile_research_pack(existing[-1]))
        if forecast.status not in {
            ForecastStatus.FRAMING_APPROVED,
            ForecastStatus.PACK_RUNNING,
        }:
            raise ForecastConflict(
                "forecast_already_started",
                "Research packs can only be dispatched before evidence extraction.",
            )
        return self._dispatch_research_pack_after_gates(forecast, request)

    def dispatch_default_research_packs(
        self,
        forecast_id: UUID,
    ) -> ResearchPackDefaultsResponse:
        packs = [
            self.dispatch_research_pack(forecast_id, request, idempotency_key=None)
            for request in default_pack_requests()
        ]
        return ResearchPackDefaultsResponse(packs=packs)

    def rerun_research_pack(
        self,
        forecast_id: UUID,
        pack_id: UUID,
        request: ResearchPackRerunRequest,
    ) -> ResearchPackResponse:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        if request.expected_active_pack_id != pack_id:
            raise ForecastConflict(
                "active_pack_changed",
                "Expected active pack does not match the requested pack.",
            )
        current = self.repository.get_pack(pack_id)
        if UUID(current["forecast_id"]) != forecast_id or not current["is_active"]:
            raise ForecastConflict(
                "active_pack_changed",
                "Active pack changed before rerun.",
                {"expected_active_pack_id": str(pack_id)},
            )
        pack_request = ResearchPackRequest(
            pack_role=PackRole(current["pack_role"]),
            tool_profile=ToolProfile(current["tool_profile"]),
            max_tool_calls=request.max_tool_calls,
            background=request.background,
            data_classification=current["data_classification"],
            vector_store_ids=json.loads(current["vector_store_ids_json"] or "[]"),
            mcp_server_ids=json.loads(current["mcp_server_ids_json"] or "[]"),
            timeout_sec=request.timeout_sec,
            estimated_cost_budget_usd=request.estimated_cost_budget_usd,
        )
        return self._dispatch_research_pack_after_gates(
            forecast,
            pack_request,
            rerun_of_pack_id=pack_id,
            attempt_no=int(current["attempt_no"]) + 1,
            rerun_policy="explicit_rerun",
            replace_active_pack_id=pack_id,
        )

    def list_research_packs(self, forecast_id: UUID) -> list[ResearchPackResponse]:
        self.repository.get_forecast(forecast_id)
        return [
            _pack_response(self._reconcile_research_pack(pack))
            for pack in self.repository.list_packs(forecast_id)
        ]

    def _record_blocked_pack_request(
        self,
        forecast: ForecastDetail,
        request: ResearchPackRequest,
        *,
        reason: str,
        policy_decision_id: UUID | None = None,
    ) -> None:
        self.repository.add_pack_request(
            forecast_id=forecast.forecast_id,
            pack_role=request.pack_role.value,
            tool_profile=request.tool_profile.value,
            data_classification=request.data_classification.value,
            status="blocked",
            reason=reason,
            policy_decision_id=policy_decision_id,
            request_payload=request.model_dump(mode="json"),
        )

    def _check_pack_timeout_and_cost(
        self,
        forecast: ForecastDetail,
        request: ResearchPackRequest,
    ) -> None:
        if (
            request.timeout_sec is not None
            and request.timeout_sec > self.settings.research_deep_research_timeout_seconds
        ):
            self._record_blocked_pack_request(
                forecast,
                request,
                reason="timeout_budget_exceeded",
            )
            raise ForecastConflict(
                "timeout_budget_exceeded",
                "Requested Forecast pack timeout exceeds the configured limit.",
                {
                    "timeout_sec": request.timeout_sec,
                    "max_timeout_sec": self.settings.research_deep_research_timeout_seconds,
                },
            )
        if request.estimated_cost_budget_usd is None:
            return
        estimated_tool_cost = (
            request.max_tool_calls * self.settings.research_web_search_cost_per_call
        )
        if estimated_tool_cost > request.estimated_cost_budget_usd:
            self._record_blocked_pack_request(
                forecast,
                request,
                reason="cost_budget_exceeded",
            )
            raise ForecastConflict(
                "cost_budget_exceeded",
                "Estimated Forecast pack tool cost exceeds the requested budget.",
                {
                    "estimated_cost_usd": estimated_tool_cost,
                    "estimated_cost_budget_usd": request.estimated_cost_budget_usd,
                },
            )

    def _dispatch_research_pack_after_gates(
        self,
        forecast: ForecastDetail,
        request: ResearchPackRequest,
        *,
        rerun_of_pack_id: UUID | None = None,
        attempt_no: int = 1,
        rerun_policy: str | None = None,
        replace_active_pack_id: UUID | None = None,
    ) -> ResearchPackResponse:
        if (
            request.data_classification == "restricted"
            and request.background
        ):
            self._record_blocked_pack_request(
                forecast,
                request,
                reason="background_mode_violates_zdr",
            )
            raise ForecastConflict(
                "background_mode_violates_zdr",
                "Restricted Forecast packs cannot use background Deep Research.",
            )
        resolved_tools = resolve_forecast_tools(request)
        if request.tool_profile == ToolProfile.PRIVATE and not request.vector_store_ids:
            self._record_blocked_pack_request(
                forecast,
                request,
                reason="private_vector_store_required",
            )
            raise ForecastConflict(
                "private_vector_store_required",
                "Private Forecast packs require allowlisted vector_store_ids.",
            )
        self._check_pack_timeout_and_cost(forecast, request)
        try:
            ensure_trusted_sources_allowed(
                self.repository,
                identifiers=request.trusted_source_identifiers,
                tool_profile=request.tool_profile,
                pack_role=request.pack_role,
                tool_names=[
                    str(tool.get("type", ""))
                    for tool in resolved_tools.tools
                    if tool.get("type")
                ],
            )
        except ForecastConflict as error:
            self._record_blocked_pack_request(
                forecast,
                request,
                reason=error.code,
            )
            raise
        prompt = build_research_pack_prompt(
            forecast,
            pack_role=request.pack_role,
            tool_profile=request.tool_profile.value,
        )
        policy = evaluate_forecast_policy(
            prompt,
            profile=request.tool_profile.value,
            data_classification=request.data_classification.value,
            resolved_tools=resolved_tools.tools,
            background=request.background,
        )
        policy_decision_id = self.repository.add_policy_decision(
            forecast_id=forecast.forecast_id,
            profile=request.tool_profile.value,
            status=policy.status,
            reason=policy.reason,
            prompt_hash=policy.prompt_hash,
            decision=policy.status,
            policy_version="phase_b_v1",
            data_classification=request.data_classification.value,
            resolved_tools=resolved_tools.tools,
            vector_store_ids=resolved_tools.vector_store_ids,
            mcp_server_ids=resolved_tools.mcp_server_ids,
            background=request.background,
            blocked_terms=getattr(policy, "blocked_terms", []),
        )
        if policy.status == "blocked":
            self.repository.add_pack_request(
                forecast_id=forecast.forecast_id,
                pack_role=request.pack_role.value,
                tool_profile=request.tool_profile.value,
                data_classification=request.data_classification.value,
                status="blocked",
                reason=policy.reason,
                policy_decision_id=policy_decision_id,
                request_payload=request.model_dump(mode="json"),
            )
            raise ForecastConflict(
                "policy_blocked",
                policy.reason or "Forecast research pack was blocked by policy.",
                {"policy_decision_id": str(policy_decision_id)},
            )
        if policy.status == "require_human_review":
            self.repository.add_pack_request(
                forecast_id=forecast.forecast_id,
                pack_role=request.pack_role.value,
                tool_profile=request.tool_profile.value,
                data_classification=request.data_classification.value,
                status="awaiting_review",
                reason=policy.reason,
                policy_decision_id=policy_decision_id,
                request_payload=request.model_dump(mode="json"),
            )
            raise ForecastConflict(
                "policy_requires_revision",
                policy.reason or "Policy requires Forecast review.",
                {"policy_decision_id": str(policy_decision_id)},
            )

        run = self.research.create_run_record(
            CreateResearchRunRequest(
                user_prompt=prompt,
                options=ResearchRunOptions(max_total_tool_calls=request.max_tool_calls),
            ),
            forecast_mode=True,
        )
        try:
            pack = self.repository.add_research_pack(
                forecast_id=forecast.forecast_id,
                research_run_id=run.id,
                pack_role=request.pack_role.value,
                tool_profile=request.tool_profile.value,
                status="submitting",
                model_deployment=getattr(
                    self.research.azure,
                    "deep_research_deployment",
                    None,
                ),
                prompt_version=(
                    CURRENT_STATE_PROMPT_VERSION
                    if request.pack_role == PackRole.CURRENT_STATE
                    else PHASE_B_PACK_PROMPT_VERSION
                ),
                max_tool_calls=request.max_tool_calls,
                policy_decision_id=policy_decision_id,
                attempt_no=attempt_no,
                rerun_of_pack_id=rerun_of_pack_id,
                timeout_sec=request.timeout_sec,
                estimated_cost_budget_usd=request.estimated_cost_budget_usd,
                vector_store_ids=resolved_tools.vector_store_ids,
                mcp_server_ids=resolved_tools.mcp_server_ids,
                cache_key=cache_key_for_pack(
                    forecast_id=str(forecast.forecast_id),
                    pack_role=request.pack_role,
                    tool_profile=request.tool_profile,
                    data_classification=request.data_classification,
                    prompt_hash=policy.prompt_hash,
                ),
                rerun_policy=rerun_policy,
                data_classification=request.data_classification.value,
                replace_active_pack_id=replace_active_pack_id,
            )
        except ResearchPackAlreadyExists as error:
            self.research.repository.delete_run(run.id)
            return _pack_response(self._reconcile_research_pack(error.existing_pack))
        except ValueError as error:
            self.research.repository.delete_run(run.id)
            if str(error) == "active_pack_changed":
                raise ForecastConflict(
                    "active_pack_changed",
                    "Active pack changed before rerun.",
                    {
                        "expected_active_pack_id": (
                            str(replace_active_pack_id)
                            if replace_active_pack_id is not None
                            else None
                        )
                    },
                ) from error
            raise
        require_policy_tools_match(
            self.repository,
            policy_decision_id=policy_decision_id,
            resolved_tools=resolved_tools.tools,
        )
        submitted = self.research.submit_deep_research(
            run.id,
            tool_profile=request.tool_profile.value,
            background=request.background,
            policy_decision_id=str(policy_decision_id),
            vector_store_ids=resolved_tools.vector_store_ids,
        )
        if submitted.status == RunStatus.WAITING_DEEP_RESEARCH:
            if submitted.pending_deep_research_response_id:
                pack = self.repository.update_research_pack_status(
                    pack_id=UUID(pack["pack_id"]),
                    status="running",
                )
            else:
                submitted = (
                    self.research.repository.update_run_if_status(
                        submitted.id,
                        {RunStatus.WAITING_DEEP_RESEARCH},
                        status=RunStatus.NEEDS_HUMAN_REVIEW,
                        needs_human_review=True,
                        pending_deep_research_response_id=None,
                        done_reason="missing_deep_research_response_id",
                    )
                    or self.research.repository.get_run(submitted.id)
                )
        if submitted.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.NEEDS_HUMAN_REVIEW,
        }:
            pack = self.repository.update_research_pack_status(
                pack_id=UUID(pack["pack_id"]),
                status=submitted.status.value,
            )
        return _pack_response(pack)

    def get_manual_research_pack_prompt(
        self,
        forecast_id: UUID,
    ) -> ManualResearchPackPromptResponse:
        forecast, prompt, existing_pack, existing_run = (
            self._manual_current_state_pack_context(
                forecast_id,
                allow_recoverable_pack=True,
            )
        )
        policy = evaluate_forecast_policy(prompt, profile=ToolProfile.PUBLIC.value)
        if policy.status == "blocked":
            raise ForecastConflict(
                "policy_blocked",
                policy.reason or "Forecast research pack was blocked by policy.",
            )
        if policy.status == "require_human_review":
            raise ForecastConflict(
                "policy_requires_revision",
                policy.reason or "Policy requires revision for PhaseA.",
            )
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return ManualResearchPackPromptResponse(
            forecast_id=forecast_id,
            framing_version=forecast.current_framing_version,
            prompt=prompt,
            prompt_sha256=prompt_sha256,
            prompt_version=(
                existing_pack["prompt_version"]
                if existing_pack is not None
                else CURRENT_STATE_PROMPT_VERSION
            ),
            pack_role=PackRole.CURRENT_STATE,
            tool_profile=ToolProfile.PUBLIC,
            max_report_chars=self.settings.research_manual_import_max_report_chars,
            max_file_bytes=self.settings.research_manual_import_max_file_bytes,
            pack_id=UUID(existing_pack["pack_id"]) if existing_pack is not None else None,
            research_run_id=existing_run.id if existing_run is not None else None,
            recovering_existing_pack=existing_pack is not None,
            recoverable_status=(
                existing_run.status.value if existing_run is not None else None
            ),
        )

    def import_manual_research_pack(
        self,
        forecast_id: UUID,
        *,
        prompt_sha256: str,
        report: str,
        source_kind: str,
        source_filename: str | None = None,
    ) -> ResearchPackResponse:
        forecast, prompt, existing_pack, existing_run = (
            self._manual_current_state_pack_context(
                forecast_id,
                allow_recoverable_pack=True,
                allow_completed_pack=True,
            )
        )
        expected_prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if prompt_sha256 != expected_prompt_sha256:
            raise ForecastConflict(
                "prompt_stale",
                "Manual research prompt is stale. Regenerate the prompt and try again.",
                {
                    "expected_prompt_sha256": expected_prompt_sha256,
                    "received_prompt_sha256": prompt_sha256,
                },
            )
        if contains_sensitive_terms(report):
            raise ForecastConflict(
                "policy_requires_revision",
                "Manual research report contains sensitive or non-public terms.",
            )
        report_sha256 = hashlib.sha256(report.encode("utf-8")).hexdigest()
        policy = evaluate_forecast_policy(prompt, profile=ToolProfile.PUBLIC.value)
        policy_decision_id = None
        if existing_pack is None:
            policy_decision_id = self.repository.add_policy_decision(
                forecast_id=forecast_id,
                profile=ToolProfile.PUBLIC.value,
                status=policy.status,
                reason=policy.reason,
                prompt_hash=policy.prompt_hash,
            )
        if policy.status == "blocked":
            raise ForecastConflict(
                "policy_blocked",
                policy.reason or "Forecast research pack was blocked by policy.",
                (
                    {"policy_decision_id": str(policy_decision_id)}
                    if policy_decision_id is not None
                    else None
                ),
            )
        if policy.status == "require_human_review":
            raise ForecastConflict(
                "policy_requires_revision",
                policy.reason or "Policy requires revision for PhaseA.",
                (
                    {"policy_decision_id": str(policy_decision_id)}
                    if policy_decision_id is not None
                    else None
                ),
            )
        if existing_pack is not None and existing_run is not None:
            if existing_run.status == RunStatus.COMPLETED or existing_pack["status"] == "completed":
                if existing_pack["report_artifact_hash"] == report_sha256:
                    return _pack_response(existing_pack)
                raise ForecastConflict(
                    "research_pack_already_exists",
                    "A current-state research pack already exists for this Forecast.",
                    {"pack_id": str(existing_pack["pack_id"])},
                )
            return self._recover_manual_research_pack(
                forecast=forecast,
                pack=existing_pack,
                run_id=existing_run.id,
                prompt=prompt,
                prompt_sha256=expected_prompt_sha256,
                report=report,
                report_sha256=report_sha256,
                source_kind=source_kind,
                source_filename=source_filename,
            )
        if policy_decision_id is None:
            raise RuntimeError("policy_decision_id was not created for manual import.")

        run = self.research.create_forecast_manual_import_run(
            input_prompt=prompt,
            report=report,
            metadata={
                "forecast_id": str(forecast.forecast_id),
                "framing_version": forecast.current_framing_version,
                "pack_role": PackRole.CURRENT_STATE.value,
                "tool_profile": ToolProfile.PUBLIC.value,
                "prompt_version": CURRENT_STATE_PROMPT_VERSION,
                "prompt_sha256": expected_prompt_sha256,
                "report_sha256": report_sha256,
                "source_kind": source_kind,
                "source_filename": source_filename,
            },
        )
        pack_id: UUID | None = None
        try:
            pack = self.repository.add_research_pack(
                forecast_id=forecast_id,
                research_run_id=run.id,
                pack_role=PackRole.CURRENT_STATE.value,
                tool_profile=ToolProfile.PUBLIC.value,
                status="completed",
                model_deployment="chatgpt-deep-research-manual",
                prompt_version=CURRENT_STATE_PROMPT_VERSION,
                max_tool_calls=0,
                policy_decision_id=policy_decision_id,
            )
            pack_id = UUID(pack["pack_id"])
            pack = self.repository.update_research_pack_status(
                pack_id=pack_id,
                status="completed",
                report_artifact_hash=report_sha256,
            )
            with self.repository.connect() as connection:
                self.repository.append_audit(
                    connection,
                    forecast_id,
                    "manual_research_pack_imported",
                    {
                        "pack_id": str(pack["pack_id"]),
                        "research_run_id": str(run.id),
                        "prompt_sha256": expected_prompt_sha256,
                        "report_sha256": report_sha256,
                        "report_chars": len(report),
                        "source_kind": source_kind,
                        "source_filename": source_filename,
                    },
                )
        except ResearchPackAlreadyExists as error:
            self._cleanup_failed_manual_import(run_id=run.id)
            raise ForecastConflict(
                "research_pack_already_exists",
                "A current-state research pack already exists for this Forecast.",
                {"pack_id": str(error.existing_pack["pack_id"])},
            ) from error
        except Exception as error:
            try:
                self._cleanup_failed_manual_import(
                    run_id=run.id,
                    forecast_id=forecast_id,
                    pack_id=pack_id,
                )
            except Exception as cleanup_error:
                error.add_note(
                    f"manual import compensation failed: {cleanup_error!r}"
                )
            raise
        return _pack_response(pack)

    def _cleanup_failed_manual_import(
        self,
        *,
        run_id: UUID,
        forecast_id: UUID | None = None,
        pack_id: UUID | None = None,
    ) -> None:
        if pack_id is not None:
            with self.repository.connect() as connection:
                connection.execute(
                    """
                    DELETE FROM forecast_research_packs
                    WHERE pack_id = ? AND research_run_id = ?
                    """,
                    (str(pack_id), str(run_id)),
                )
                if forecast_id is not None:
                    connection.execute(
                        """
                        UPDATE forecast_forecasts
                        SET status = ?, updated_at = ?
                        WHERE id = ?
                          AND NOT EXISTS (
                              SELECT 1 FROM forecast_research_packs
                              WHERE forecast_id = ?
                          )
                        """,
                        (
                            ForecastStatus.FRAMING_APPROVED.value,
                            utc_now().isoformat(),
                            str(forecast_id),
                            str(forecast_id),
                        ),
                    )
        self.research.artifacts.delete_run(run_id)
        self.research.repository.delete_run(run_id)

    def _manual_current_state_pack_context(
        self,
        forecast_id: UUID,
        *,
        allow_recoverable_pack: bool,
        allow_completed_pack: bool = False,
    ) -> tuple[ForecastDetail, str, Any | None, Any | None]:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        _ensure_forecast_has_outcomes(forecast)
        if forecast.approved_framing_version != forecast.current_framing_version:
            raise ForecastConflict(
                "framing_not_approved",
                "Approve the latest framing before dispatching research packs.",
            )
        if forecast.confidentiality_class != "public":
            raise ForecastConflict(
                "policy_requires_revision",
                "PhaseA only supports public forecasts.",
            )
        packs = self.repository.list_packs(forecast_id)
        if not packs:
            if forecast.status != ForecastStatus.FRAMING_APPROVED:
                raise ForecastConflict(
                    "forecast_already_started",
                    "A PhaseA research pack can only be dispatched once.",
                )
            return forecast, build_current_state_prompt(forecast), None, None

        pack = self._reconcile_research_pack(packs[-1])
        run = self.research.repository.get_run(UUID(pack["research_run_id"]))
        if (
            pack["pack_role"] != PackRole.CURRENT_STATE.value
            or pack["tool_profile"] != ToolProfile.PUBLIC.value
        ):
            raise ForecastConflict(
                "policy_requires_revision",
                "PhaseA only supports public current_state research packs.",
            )
        if run.status == RunStatus.COMPLETED or pack["status"] == "completed":
            if allow_completed_pack:
                return forecast, (run.optimized_prompt or run.user_prompt), pack, run
            raise ForecastConflict(
                "research_pack_already_exists",
                "A current-state research pack already exists for this Forecast.",
                {"pack_id": str(pack["pack_id"])},
            )
        if allow_recoverable_pack and run.status in _MANUAL_RECOVERABLE_RUN_STATUSES:
            return forecast, (run.optimized_prompt or run.user_prompt), pack, run
        raise ForecastConflict(
            "research_pack_manual_recovery_not_allowed",
            "Manual recovery is only available after public information collection "
            "has failed or requires review.",
            {
                "pack_id": str(pack["pack_id"]),
                "pack_status": pack["status"],
                "research_run_id": str(run.id),
                "research_run_status": run.status.value,
            },
        )

    def _recover_manual_research_pack(
        self,
        *,
        forecast: ForecastDetail,
        pack: Any,
        run_id: UUID,
        prompt: str,
        prompt_sha256: str,
        report: str,
        report_sha256: str,
        source_kind: str,
        source_filename: str | None,
    ) -> ResearchPackResponse:
        old_status = pack["status"]
        old_run = self.research.repository.get_run(run_id)
        try:
            updated_run = self.research.complete_existing_forecast_manual_import_run(
                run_id,
                input_prompt=prompt,
                report=report,
                metadata={
                    "forecast_id": str(forecast.forecast_id),
                    "framing_version": forecast.current_framing_version,
                    "pack_id": str(pack["pack_id"]),
                    "pack_role": PackRole.CURRENT_STATE.value,
                    "tool_profile": ToolProfile.PUBLIC.value,
                    "prompt_version": pack["prompt_version"],
                    "prompt_sha256": prompt_sha256,
                    "report_sha256": report_sha256,
                    "source_kind": source_kind,
                    "source_filename": source_filename,
                    "recovering_existing_pack": True,
                    "old_pack_status": old_status,
                    "old_run_status": old_run.status.value,
                    "old_done_reason": old_run.done_reason,
                },
            )
        except ValueError as error:
            raise ForecastConflict(
                "research_pack_manual_recovery_not_allowed",
                str(error),
                {
                    "pack_id": str(pack["pack_id"]),
                    "research_run_id": str(run_id),
                },
            ) from error
        recovered_pack = self.repository.update_research_pack_status(
            pack_id=UUID(pack["pack_id"]),
            status="completed",
            report_artifact_hash=report_sha256,
        )
        with self.repository.connect() as connection:
            self.repository.append_audit(
                connection,
                forecast.forecast_id,
                "manual_research_pack_recovered",
                {
                    "pack_id": str(pack["pack_id"]),
                    "research_run_id": str(updated_run.id),
                    "prompt_sha256": prompt_sha256,
                    "report_sha256": report_sha256,
                    "report_chars": len(report),
                    "source_kind": source_kind,
                    "source_filename": source_filename,
                    "old_pack_status": old_status,
                    "old_run_status": old_run.status.value,
                    "old_done_reason": old_run.done_reason,
                },
            )
        return _pack_response(recovered_pack)

    def _prepare_current_state_pack_prompt(
        self,
        forecast_id: UUID,
        *,
        pack_role: PackRole,
        tool_profile: ToolProfile,
        allow_existing_pack: bool,
    ) -> tuple[ForecastDetail, str]:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        _ensure_forecast_has_outcomes(forecast)
        if forecast.approved_framing_version != forecast.current_framing_version:
            raise ForecastConflict(
                "framing_not_approved",
                "Approve the latest framing before dispatching research packs.",
            )
        if forecast.confidentiality_class != "public":
            raise ForecastConflict(
                "policy_requires_revision",
                "PhaseA only supports public forecasts.",
            )
        if pack_role != PackRole.CURRENT_STATE or tool_profile != ToolProfile.PUBLIC:
            raise ForecastConflict(
                "policy_requires_revision",
                "PhaseA only supports public current_state research packs.",
            )
        existing = self.repository.list_packs(forecast_id)
        if existing and not allow_existing_pack:
            raise ForecastConflict(
                "research_pack_already_exists",
                "A current-state research pack already exists for this Forecast.",
                {"pack_id": str(existing[-1]["pack_id"])},
            )
        if not existing and forecast.status != ForecastStatus.FRAMING_APPROVED:
            raise ForecastConflict(
                "forecast_already_started",
                "A PhaseA research pack can only be dispatched once.",
            )
        return forecast, build_current_state_prompt(forecast)

    def extract_evidence(self, forecast_id: UUID) -> tuple[list[SourceRecord], list[ClaimRecord]]:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        _ensure_forecast_has_outcomes(forecast)
        if forecast.status not in {ForecastStatus.PACK_RUNNING, ForecastStatus.EVIDENCE_READY}:
            raise ForecastConflict(
                "pack_not_completed",
                "Research pack must complete before evidence extraction.",
            )
        completed_packs = self._completed_evidence_packs(forecast.forecast_id)
        reports: list[tuple[Any, Any, str, str]] = []
        for pack in completed_packs:
            run = self.research.repository.get_run(UUID(pack["research_run_id"]))
            report = (run.final_report or run.report or "").strip()
            if not report:
                raise ForecastConflict(
                    "pack_not_completed",
                    "Research pack completed without a report.",
                    {"research_run_id": str(run.id)},
                )
            report_hash = hashlib.sha256(report.encode("utf-8")).hexdigest()
            reports.append((pack, run, report, report_hash))

        sources: list[dict[str, Any]] = []
        outcomes = forecast.outcomes
        claims: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        for pack, run, report, report_hash in reports:
            pack_id = UUID(pack["pack_id"])
            source_id = str(uuid5(NAMESPACE_URL, f"forecast-source:{pack_id}:{report_hash}"))
            sources.append(
                {
                    "source_id": source_id,
                    "pack_id": str(pack_id),
                    "title": f"{pack['pack_role']} Deep Research report",
                    "publisher": "Deep Research",
                    "url": None,
                    "source_type": "research_report",
                    "source_classification": pack["data_classification"],
                    "data_classification": pack["data_classification"],
                    "origin_tool_profile": pack["tool_profile"],
                    "reliability_score": 0.72,
                    "report_artifact_hash": report_hash,
                    "metadata": {
                        "research_run_id": str(run.id),
                        "pack_id": str(pack_id),
                        "pack_role": pack["pack_role"],
                    },
                }
            )
            lines = _claim_lines(report)
            for index, line in enumerate(lines[: max(1, min(len(lines), 12))]):
                outcome = outcomes[index % len(outcomes)]
                independence_group = _publisher_group(line, index)
                cluster_id = hashlib.sha256(
                    f"{_fingerprint(line)}:{independence_group}".encode()
                ).hexdigest()
                claim_id = str(
                    uuid5(NAMESPACE_URL, f"forecast-claim:{pack_id}:{report_hash}:{index}")
                )
                claims.append(
                    {
                        "claim_id": claim_id,
                        "text": line,
                        "claim_type": pack["pack_role"],
                        "polarity": 1,
                        "evidence_strength": 0.65,
                        "reliability_score": 0.72,
                        "cluster_id": cluster_id,
                        "independence_group": independence_group,
                        "source_classification": pack["data_classification"],
                        "data_classification": pack["data_classification"],
                        "origin_tool_profile": pack["tool_profile"],
                        "pack_id": str(pack_id),
                        "report_artifact_hash": report_hash,
                        "extraction_model": "deterministic_phase_b_extractor",
                        "extraction_prompt_version": "phase_b_claims_v1",
                        "review_status": "approved",
                        "source_ids": [source_id],
                    }
                )
                links.append(
                    {
                        "link_id": str(
                            uuid5(
                                NAMESPACE_URL,
                                f"forecast-link:{claim_id}:outcome:{outcome.outcome_id}",
                            )
                        ),
                        "claim_id": claim_id,
                        "target_kind": "outcome",
                        "target_id": str(outcome.outcome_id),
                        "direction": 1,
                        "relevance_weight": 1.0,
                        "review_status": "pending",
                    }
                )
        if not claims:
            raise ForecastConflict(
                "pack_not_completed",
                "No source-linked claims could be extracted from the completed pack.",
            )
        for pack, _run, _report, report_hash in reports:
            self.repository.mark_pack_completed(
                pack_id=UUID(pack["pack_id"]),
                report_artifact_hash=report_hash,
            )
        extraction_batch_id = hashlib.sha256(
            ":".join(sorted(item[3] for item in reports)).encode("utf-8")
        ).hexdigest()
        source_rows, claim_rows = self.repository.upsert_evidence_batch(
            forecast_id=forecast.forecast_id,
            pack_id=UUID(reports[0][0]["pack_id"]),
            extraction_batch_id=extraction_batch_id,
            report_artifact_hash=extraction_batch_id,
            sources=sources,
            claims=claims,
            links=links,
        )
        self._derive_phase_b_structures_from_reports(forecast, reports)
        return (
            [_source_response(row) for row in source_rows],
            [_claim_response(self.repository, row) for row in claim_rows],
        )

    def _completed_evidence_packs(self, forecast_id: UUID) -> list[Any]:
        active = [
            self._reconcile_research_pack(pack)
            for pack in self.repository.list_active_packs(forecast_id)
        ]
        if not active:
            raise ForecastConflict(
                "pack_not_completed",
                "Dispatch and complete research packs first.",
            )
        active_pairs = {(pack["pack_role"], pack["tool_profile"]) for pack in active}
        required_pairs = {
            (role.value, ToolProfile.PUBLIC.value)
            for role in (
                PackRole.CURRENT_STATE,
                PackRole.BASE_RATE,
                PackRole.DRIVERS,
                PackRole.COUNTER_EVIDENCE,
                PackRole.SIGNALS,
            )
        }
        phase_a_pair = {(PackRole.CURRENT_STATE.value, ToolProfile.PUBLIC.value)}
        if active_pairs == phase_a_pair:
            required = [active[-1]]
        else:
            missing_pairs = sorted(required_pairs - active_pairs)
            if missing_pairs:
                raise ForecastConflict(
                    "pack_not_completed",
                    "Required active Phase B default packs must exist before evidence extraction.",
                    {
                        "missing_packs": [
                            {"pack_role": role, "tool_profile": profile}
                            for role, profile in missing_pairs
                        ]
                    },
                )
            required = [
                pack
                for pack in active
                if (pack["pack_role"], pack["tool_profile"]) in required_pairs
            ]
        incomplete: list[dict[str, str]] = []
        for pack in required:
            run = self.research.repository.get_run(UUID(pack["research_run_id"]))
            if pack["status"] != "completed" and run.status != RunStatus.COMPLETED:
                incomplete.append(
                    {
                        "pack_id": pack["pack_id"],
                        "pack_role": pack["pack_role"],
                        "status": pack["status"],
                        "research_run_status": run.status.value,
                    }
                )
        if incomplete:
            raise ForecastConflict(
                "pack_not_completed",
                "Required active research packs must complete before evidence extraction.",
                {"incomplete_packs": incomplete},
            )
        return required

    def _derive_phase_b_structures_from_reports(
        self,
        forecast: ForecastDetail,
        reports: list[tuple[Any, Any, str, str]],
    ) -> None:
        by_role = {pack["pack_role"]: (pack, report) for pack, _run, report, _hash in reports}
        if PackRole.BASE_RATE.value in by_role:
            pack, report = by_role[PackRole.BASE_RATE.value]
            lines = _claim_lines(report) or ["Base-rate analog"]
            analog_events = [
                {
                    "analog_event_id": str(
                        uuid5(
                            NAMESPACE_URL,
                            f"forecast-analog:{forecast.forecast_id}:{index}:{outcome.outcome_id}",
                        )
                    ),
                    "title": lines[index % len(lines)][:200],
                    "matched_outcome_id": str(outcome.outcome_id),
                    "weight": 1.0,
                    "rationale": "Deterministic base-rate analog extracted from base_rate pack.",
                }
                for index, outcome in enumerate(forecast.outcomes)
            ]
            self.repository.replace_analog_events(
                forecast_id=forecast.forecast_id,
                pack_id=UUID(pack["pack_id"]),
                analog_events=analog_events,
            )
        if PackRole.DRIVERS.value in by_role:
            _pack, report = by_role[PackRole.DRIVERS.value]
            lines = _claim_lines(report)[:3] or [forecast.question]
            drivers: list[dict[str, Any]] = []
            for index, line in enumerate(lines):
                driver_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"forecast-driver:{forecast.forecast_id}:{_fingerprint(line)}:{index}",
                    )
                )
                drivers.append(
                    {
                        "driver_id": driver_id,
                        "name": line[:80],
                        "description": line[:1000],
                        "sort_order": index,
                        "states": [
                            {
                                "state_id": str(
                                    uuid5(NAMESPACE_URL, f"{driver_id}:low")
                                ),
                                "label": "Low",
                                "description": f"Low intensity for {line[:120]}",
                                "sort_order": 0,
                            },
                            {
                                "state_id": str(
                                    uuid5(NAMESPACE_URL, f"{driver_id}:high")
                                ),
                                "label": "High",
                                "description": f"High intensity for {line[:120]}",
                                "sort_order": 1,
                            },
                        ],
                    }
                )
            self.repository.replace_drivers(
                forecast_id=forecast.forecast_id,
                drivers=drivers,
            )

    def generate_scenarios(self, forecast_id: UUID) -> list[ScenarioRecord]:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        _ensure_forecast_has_outcomes(forecast)
        if forecast.status not in {ForecastStatus.EVIDENCE_READY, ForecastStatus.SCENARIOS_READY}:
            raise ForecastConflict(
                "evidence_not_ready",
                "Extract evidence before generating scenarios.",
            )
        if not self.repository.get_claims(forecast.forecast_id):
            raise ForecastConflict(
                "evidence_not_ready",
                "Extract evidence before generating scenarios.",
            )
        driver_states = self.repository.get_driver_states(forecast.forecast_id)
        scenario_driver_links: list[dict[str, str]] = []
        if driver_states:
            states_by_driver: dict[str, list[Any]] = {}
            for state in driver_states:
                states_by_driver.setdefault(state["driver_id"], []).append(state)
            selected_state_groups = list(states_by_driver.values())[:2]
            combinations: list[list[Any]] = [[]]
            for group in selected_state_groups:
                combinations = [prefix + [state] for prefix in combinations for state in group[:2]]
            combinations = combinations[:4] or [[driver_states[0]]]
            scenarios: list[dict[str, Any]] = []
            for outcome in forecast.outcomes:
                for index, states in enumerate(combinations):
                    state_label = " / ".join(state["label"] for state in states)
                    scenario_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"forecast-scenario:{forecast.forecast_id}:{outcome.outcome_id}:{index}:{state_label}",
                        )
                    )
                    scenarios.append(
                        {
                            "scenario_id": scenario_id,
                            "outcome_id": str(outcome.outcome_id),
                            "label": f"{outcome.label}: {state_label}",
                            "description": (
                                f"{outcome.definition} Driver states: {state_label}."
                            ),
                            "normalized_weight": 1.0,
                            "validity_status": "valid",
                        }
                    )
                    for state in states:
                        scenario_driver_links.append(
                            {"scenario_id": scenario_id, "state_id": state["state_id"]}
                        )
        else:
            scenarios = [
                {
                    "scenario_id": str(
                        uuid5(
                            NAMESPACE_URL,
                            f"forecast-scenario:{forecast.forecast_id}:{outcome.outcome_id}:phase-a",
                        )
                    ),
                    "outcome_id": str(outcome.outcome_id),
                    "label": outcome.label,
                    "description": (
                        "Scenario in which the forecast resolves as: "
                        f"{outcome.definition}"
                    ),
                    "normalized_weight": 1.0,
                    "validity_status": "valid",
                }
                for outcome in forecast.outcomes
            ]
        _validate_scenarios_for_outcomes(forecast, scenarios, scenario_driver_links)
        rows = self.repository.upsert_scenarios(
            forecast_id=forecast.forecast_id,
            scenarios=scenarios,
        )
        if scenario_driver_links:
            self.repository.replace_scenario_driver_links(links=scenario_driver_links)
        return [_scenario_response_with_links(self.repository, row) for row in rows]

    def compute_probabilities(
        self,
        forecast_id: UUID,
        request: ComputeProbabilitiesRequest | None = None,
    ) -> dict[str, Any]:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        _ensure_forecast_has_outcomes(forecast)
        if not self.repository.get_claims(forecast.forecast_id):
            raise ForecastConflict("evidence_not_ready", "Extract evidence first.")
        if not self.repository.get_scenarios(forecast.forecast_id):
            raise ForecastConflict("scenarios_not_ready", "Generate scenarios first.")
        approved_links = self.repository.get_approved_target_links(forecast.forecast_id)
        if not approved_links:
            raise ForecastConflict(
                "claim_targets_not_approved",
                "Approve claim-target links before computing probabilities.",
            )
        engine = probability.get_engine(request.engine_version if request else None)
        snapshot = self._canonical_snapshot(forecast, engine_version=engine.engine_version)
        input_hash = engine.snapshot_hash(snapshot)
        existing = self.repository.get_draft_estimate_set(forecast.forecast_id)
        if existing is not None:
            if (
                existing["input_snapshot_hash"] == input_hash
                and existing["engine_version"] == engine.engine_version
            ):
                return self.estimate_set_response(UUID(existing["estimate_set_id"]))
            raise ForecastConflict(
                "draft_estimate_set_exists",
                "A draft estimate set already exists for different inputs.",
                {
                    "estimate_set_id": existing["estimate_set_id"],
                    "existing_input_snapshot_hash": existing["input_snapshot_hash"],
                    "new_input_snapshot_hash": input_hash,
                    "existing_engine_version": existing["engine_version"],
                    "new_engine_version": engine.engine_version,
                },
            )
        estimates = engine.compute(snapshot=snapshot)
        estimate_set = self.repository.create_draft_estimate_set(
            forecast_id=forecast.forecast_id,
            engine_version=engine.engine_version,
            input_snapshot_hash=input_hash,
            engine_code_hash=engine.engine_code_hash(),
            random_seed=engine.random_seed,
            normalization_group_id=forecast.outcomes[0].normalization_group_id,
            snapshot=snapshot,
            estimates=estimates,
        )
        return self.estimate_set_response(UUID(estimate_set["estimate_set_id"]))

    def estimate_set_response(self, estimate_set_id: UUID) -> dict[str, Any]:
        estimate_set = self.repository.get_estimate_set(estimate_set_id)
        estimates = [
            ProbabilityEstimateRecord(
                estimate_id=UUID(row["estimate_id"]),
                target_kind=row["target_kind"],
                target_id=UUID(row["target_id"]),
                prior=row["prior"],
                evidence_update=row["evidence_update"],
                cross_impact_adjustment=row["cross_impact_adjustment"],
                simulation_adjustment=row["simulation_adjustment"],
                calibration_adjustment=row["calibration_adjustment"],
                human_adjustment=row["human_adjustment"],
                final_probability=row["final_probability"],
                uncertainty_range=_json_load(row["uncertainty_range_json"]),
                components=_json_load(row["components_json"]),
            )
            for row in self.repository.get_estimates(estimate_set_id)
        ]
        return {
            "estimate_set_id": UUID(estimate_set["estimate_set_id"]),
            "forecast_id": UUID(estimate_set["forecast_id"]),
            "status": estimate_set["status"],
            "approved": self.repository.estimate_set_has_approval(
                UUID(estimate_set["forecast_id"]),
                UUID(estimate_set["estimate_set_id"]),
            ),
            "engine_version": estimate_set["engine_version"],
            "input_snapshot_hash": estimate_set["input_snapshot_hash"],
            "engine_code_hash": estimate_set["engine_code_hash"],
            "random_seed": estimate_set["random_seed"],
            "normalization_group_id": estimate_set["normalization_group_id"],
            "estimates": estimates,
        }

    def current_estimate_set_response(self, forecast_id: UUID) -> dict[str, Any]:
        self.repository.get_forecast(forecast_id)
        estimate_set = self.repository.get_current_estimate_set(forecast_id)
        if estimate_set is None:
            raise KeyError(str(forecast_id))
        return self.estimate_set_response(UUID(estimate_set["estimate_set_id"]))

    def commit_version(
        self,
        forecast_id: UUID,
        *,
        estimate_set_id: UUID,
        expected_input_snapshot_hash: str,
    ) -> CommitVersionResponse:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        if forecast.status != ForecastStatus.DRAFT_READY:
            raise ForecastConflict(
                "approval_required",
                "Compute and approve a PhaseA draft before committing.",
            )
        if not self.repository.estimate_set_has_approval(forecast_id, estimate_set_id):
            raise ForecastConflict(
                "approval_required",
                "Approve the PhaseA estimate set before committing a version.",
            )
        estimate_set = self.repository.get_estimate_set(estimate_set_id)
        if estimate_set["status"] != "draft":
            raise ForecastConflict(
                "estimate_set_already_committed",
                "Estimate set is already committed.",
            )
        if estimate_set["input_snapshot_hash"] != expected_input_snapshot_hash:
            raise ForecastConflict(
                "approval_required",
                "Expected input snapshot hash does not match the draft.",
                {
                    "expected_input_snapshot_hash": expected_input_snapshot_hash,
                    "actual_input_snapshot_hash": estimate_set["input_snapshot_hash"],
                },
            )
        snapshot = _json_load(estimate_set["snapshot_json"])
        if forecast.confidentiality_class == "public" and _snapshot_has_non_public_evidence(
            snapshot
        ):
            raise ForecastConflict(
                "classification_mismatch",
                "Public Forecast versions cannot include internal or restricted evidence.",
            )
        path, digest, _bytes = self.artifacts.save_bytes(
            forecast_id,
            "public",
            f"versions/{estimate_set_id}.snapshot.json",
            probability.canonical_json_bytes(
                snapshot,
                engine_version=estimate_set["engine_version"],
            ),
        )
        if digest != estimate_set["input_snapshot_hash"]:
            Path(path).unlink(missing_ok=True)
            raise ForecastConflict(
                "approval_required",
                "Snapshot artifact hash does not match the approved draft.",
                {
                    "input_snapshot_hash": estimate_set["input_snapshot_hash"],
                    "artifact_hash": digest,
                },
            )
        try:
            version = self.repository.commit_estimate_set(
                forecast_id=forecast_id,
                estimate_set_id=estimate_set_id,
                expected_input_snapshot_hash=expected_input_snapshot_hash,
                snapshot_artifact_path=path,
            )
        except ValueError as error:
            Path(path).unlink(missing_ok=True)
            if str(error) == "estimate_set_already_committed":
                raise ForecastConflict(
                    "estimate_set_already_committed",
                    "Estimate set is already committed.",
                ) from error
            raise
        except Exception:
            Path(path).unlink(missing_ok=True)
            raise
        return CommitVersionResponse(
            version_id=UUID(version["version_id"]),
            forecast_id=UUID(version["forecast_id"]),
            estimate_set_id=UUID(version["estimate_set_id"]),
            input_snapshot_hash=version["input_snapshot_hash"],
            snapshot_artifact_path=version["snapshot_artifact_path"],
            committed_at=_parse_dt_required(version["created_at"]),
        )

    def resolve_forecast(
        self,
        forecast_id: UUID,
        *,
        outcome_id: UUID,
        resolution_notes: str | None,
    ) -> ResolveForecastResponse:
        forecast = self.get_forecast(forecast_id)
        self._ensure_enabled()
        if forecast.resolved_at is not None:
            raise ForecastConflict(
                "forecast_already_resolved",
                "Forecast has already been resolved.",
            )
        if forecast.committed_version_id is None:
            raise ForecastConflict(
                "approval_required",
                "Commit a PhaseA version before resolving the forecast.",
            )
        if outcome_id not in {outcome.outcome_id for outcome in forecast.outcomes}:
            raise ForecastConflict(
                "approval_required",
                "Resolution outcome does not belong to the forecast.",
            )
        version = self.repository.get_versions(forecast.forecast_id)[-1]
        snapshot_bytes = Path(version["snapshot_artifact_path"]).read_bytes()
        snapshot = json.loads(snapshot_bytes.decode("utf-8"))
        estimate_set = self.repository.get_estimate_set(UUID(version["estimate_set_id"]))
        engine = probability.get_engine(estimate_set["engine_version"])
        if engine.snapshot_hash(snapshot) != version["input_snapshot_hash"]:
            raise ForecastConflict(
                "approval_required",
                "Committed snapshot artifact hash does not match the version record.",
            )
        estimates = engine.compute(snapshot=snapshot)
        brier, log, scorer_version = probability.score(
            estimates=estimates,
            actual_outcome_id=str(outcome_id),
            engine_version=engine.engine_version,
        )
        try:
            resolution = self.repository.resolve_forecast(
                forecast_id=forecast.forecast_id,
                version_id=UUID(version["version_id"]),
                outcome_id=outcome_id,
                multiclass_brier=brier,
                log_score=log,
                scorer_version=scorer_version,
                notes=resolution_notes,
            )
        except ValueError as error:
            if str(error) == "forecast_already_resolved":
                raise ForecastConflict(
                    "forecast_already_resolved",
                    "Forecast has already been resolved.",
                ) from error
            raise
        return ResolveForecastResponse(
            forecast_id=forecast.forecast_id,
            outcome_id=UUID(resolution["outcome_id"]),
            multiclass_brier=resolution["multiclass_brier"],
            log_score=resolution["log_score"],
            scorer_version=resolution["scorer_version"],
            resolved_at=_parse_dt_required(resolution["created_at"]),
        )

    def get_audit(self, forecast_id: UUID) -> ForecastAuditResponse:
        self.repository.get_forecast(forecast_id)
        audit = self.repository.get_audit(forecast_id)
        return ForecastAuditResponse(
            forecast_id=forecast_id,
            reviews=[dict(row) for row in audit["reviews"]],
            versions=[dict(row) for row in audit["versions"]],
            policy_decisions=[dict(row) for row in audit["policy_decisions"]],
            events=[
                ForecastAuditEvent(
                    event_id=UUID(row["event_id"]),
                    forecast_id=UUID(row["forecast_id"]),
                    event_type=row["event_type"],
                    event_json=_json_load(row["event_json"]),
                    created_at=_parse_dt_required(row["created_at"]),
                )
                for row in audit["events"]
            ],
        )

    def _single_completed_pack(self, forecast_id: UUID) -> Any:
        packs = self.repository.list_packs(forecast_id)
        if not packs:
            raise ForecastConflict(
                "pack_not_completed",
                "Dispatch and complete a research pack first.",
            )
        return packs[-1]

    def _canonical_snapshot(
        self,
        forecast: ForecastDetail,
        *,
        engine_version: str,
    ) -> dict[str, Any]:
        engine = probability.get_engine(engine_version)
        outcomes = [
            {
                "outcome_id": str(outcome.outcome_id),
                "label": outcome.label,
                "definition": outcome.definition,
                "resolution_rule": outcome.resolution_rule,
                "normalization_group_id": outcome.normalization_group_id,
                "sort_order": outcome.sort_order,
            }
            for outcome in forecast.outcomes
        ]
        outcomes = sorted(outcomes, key=lambda item: (item["sort_order"], item["outcome_id"]))
        scenarios = [
            {
                "scenario_id": row["scenario_id"],
                "outcome_id": row["outcome_id"],
                "label": row["label"],
                "description": row["description"],
                "normalized_weight": row["normalized_weight"],
                "validity_status": row["validity_status"],
                "driver_state_ids": [
                    str(state_id)
                    for state_id in self.repository.get_scenario_driver_state_ids(
                        UUID(row["scenario_id"])
                    )
                ],
            }
            for row in self.repository.get_scenarios(forecast.forecast_id)
        ]
        scenarios = sorted(
            scenarios,
            key=lambda item: (item["outcome_id"], item["scenario_id"]),
        )
        claims = [
            {
                "claim_id": row["claim_id"],
                "text": row["text"],
                "claim_type": row["claim_type"],
                "polarity": row["polarity"],
                "evidence_strength": row["evidence_strength"],
                "reliability_score": row["reliability_score"],
                "cluster_id": row["cluster_id"],
                "independence_group": row["independence_group"],
                "source_ids": [
                    str(source_id)
                    for source_id in self.repository.get_claim_source_ids(UUID(row["claim_id"]))
                ],
                "review_status": row["review_status"],
                "data_classification": row["data_classification"],
                "origin_tool_profile": row["origin_tool_profile"],
                "pack_id": row["pack_id"],
                "extraction_batch_id": row["extraction_batch_id"],
                "report_artifact_hash": row["report_artifact_hash"],
            }
            for row in self.repository.get_claims(forecast.forecast_id)
        ]
        claims = sorted(claims, key=lambda item: item["claim_id"])
        sources = [
            {
                "source_id": row["source_id"],
                "title": row["title"],
                "publisher": row["publisher"],
                "url": row["url"],
                "source_type": row["source_type"],
                "source_classification": row["source_classification"],
                "data_classification": row["data_classification"],
                "origin_tool_profile": row["origin_tool_profile"],
                "reliability_score": row["reliability_score"],
            }
            for row in self.repository.get_sources(forecast.forecast_id)
        ]
        sources = sorted(sources, key=lambda item: item["source_id"])
        links = [
            {
                "claim_id": row["claim_id"],
                "target_kind": row["target_kind"],
                "target_id": row["target_id"],
                "direction": row["direction"],
                "relevance_weight": row["relevance_weight"],
                "review_status": row["review_status"],
            }
            for row in self.repository.get_approved_target_links(forecast.forecast_id)
            if row["target_kind"] == "outcome"
        ]
        links = sorted(
            links,
            key=lambda item: (
                item["target_kind"],
                item["target_id"],
                item["claim_id"],
                item["direction"],
            ),
        )
        packs = [
            {
                "pack_id": row["pack_id"],
                "research_run_id": row["research_run_id"],
                "pack_role": row["pack_role"],
                "tool_profile": row["tool_profile"],
                "prompt_version": row["prompt_version"],
                "report_artifact_hash": row["report_artifact_hash"],
                "attempt_no": row["attempt_no"],
                "is_active": bool(row["is_active"]),
                "data_classification": row["data_classification"],
            }
            for row in self.repository.list_active_packs(forecast.forecast_id)
        ]
        packs = sorted(packs, key=lambda item: item["pack_id"])
        drivers = [
            {
                "driver_id": row["driver_id"],
                "name": row["name"],
                "description": row["description"],
                "sort_order": row["sort_order"],
            }
            for row in self.repository.get_drivers(forecast.forecast_id)
        ]
        driver_states = [
            {
                "state_id": row["state_id"],
                "driver_id": row["driver_id"],
                "label": row["label"],
                "description": row["description"],
                "sort_order": row["sort_order"],
            }
            for row in self.repository.get_driver_states(forecast.forecast_id)
        ]
        analog_events = [
            {
                "analog_event_id": row["analog_event_id"],
                "pack_id": row["pack_id"],
                "title": row["title"],
                "matched_outcome_id": row["matched_outcome_id"],
                "weight": row["weight"],
                "rationale": row["rationale"],
                "active": True,
            }
            for row in self.repository.get_analog_events(forecast.forecast_id)
        ]
        cross_impact = [
            {
                "cross_impact_id": row["cross_impact_id"],
                "source_outcome_id": row["source_outcome_id"],
                "target_outcome_id": row["target_outcome_id"],
                "delta": row["delta"],
            }
            for row in self.repository.get_cross_impact(forecast.forecast_id)
        ]
        return {
            "engine_version": engine.engine_version,
            "engine_code_hash": engine.engine_code_hash(),
            "prompt_versions": {
                "current_state": CURRENT_STATE_PROMPT_VERSION,
                "extractor": "phase_a_claims_v1",
                "scenario": (
                    "phase_b_morphological_scenarios_v1"
                    if engine.engine_version == "phase_b_v1"
                    else "phase_a_scenarios_v1"
                ),
            },
            "kappa": 1.0,
            "kappa_evidence": 1.0,
            "kappa_cross_impact": 1.0,
            "clamp": 3.0,
            "epsilon_floor": 1e-9,
            "random_seed": engine.random_seed,
            "perturbation_runs": 200,
            "forecast": {
                "forecast_id": str(forecast.forecast_id),
                "question": forecast.question,
                "resolution_criteria": forecast.resolution_criteria,
                "approved_framing_version": forecast.approved_framing_version,
            },
            "outcomes": outcomes,
            "scenarios": scenarios,
            "claims": claims,
            "sources": sources,
            "approved_target_links": links,
            "packs": packs,
            "drivers": drivers,
            "driver_states": driver_states,
            "analog_events": analog_events,
            "cross_impact": cross_impact,
        }


def _outcome_response(row: Any) -> ForecastOutcome:
    return ForecastOutcome(
        outcome_id=UUID(row["outcome_id"]),
        label=row["label"],
        definition=row["definition"],
        resolution_rule=row["resolution_rule"],
        normalization_group_id=row["normalization_group_id"],
        sort_order=row["sort_order"],
    )


def _row_keys(row: Any) -> set[str]:
    if not hasattr(row, "keys"):
        return set()
    return set(cast(list[str], row.keys()))


def _pack_response(row: Any) -> ResearchPackResponse:
    keys = _row_keys(row)
    return ResearchPackResponse(
        pack_id=UUID(row["pack_id"]),
        forecast_id=UUID(row["forecast_id"]),
        research_run_id=UUID(row["research_run_id"]),
        pack_role=PackRole(row["pack_role"]),
        tool_profile=ToolProfile(row["tool_profile"]),
        status=row["status"],
        policy_decision_id=UUID(row["policy_decision_id"]),
        attempt_no=int(row["attempt_no"]) if "attempt_no" in keys else 1,
        is_active=bool(row["is_active"]) if "is_active" in keys else True,
        data_classification=ConfidentialityClass(
            row["data_classification"]
            if "data_classification" in keys
            else ConfidentialityClass.PUBLIC.value
        ),
    )


def _source_response(row: Any) -> SourceRecord:
    keys = _row_keys(row)
    return SourceRecord(
        source_id=UUID(row["source_id"]),
        title=row["title"],
        publisher=row["publisher"],
        url=row["url"],
        source_type=row["source_type"],
        source_classification=row["source_classification"],
        data_classification=ConfidentialityClass(
            row["data_classification"]
            if "data_classification" in keys
            else ConfidentialityClass.PUBLIC.value
        ),
        origin_tool_profile=ToolProfile(
            row["origin_tool_profile"]
            if "origin_tool_profile" in keys
            else row["source_classification"]
        ),
        reliability_score=row["reliability_score"],
    )


def _claim_response(repository: ForecastRepository, row: Any) -> ClaimRecord:
    keys = _row_keys(row)
    return ClaimRecord(
        claim_id=UUID(row["claim_id"]),
        text=row["text"],
        claim_type=row["claim_type"],
        polarity=row["polarity"],
        evidence_strength=row["evidence_strength"],
        reliability_score=row["reliability_score"],
        cluster_id=row["cluster_id"],
        independence_group=row["independence_group"],
        source_ids=repository.get_claim_source_ids(UUID(row["claim_id"])),
        review_status=row["review_status"],
        data_classification=ConfidentialityClass(
            row["data_classification"]
            if "data_classification" in keys
            else ConfidentialityClass.PUBLIC.value
        ),
        origin_tool_profile=ToolProfile(
            row["origin_tool_profile"]
            if "origin_tool_profile" in keys
            else row["source_classification"]
        ),
    )


def _scenario_response(row: Any) -> ScenarioRecord:
    return ScenarioRecord(
        scenario_id=UUID(row["scenario_id"]),
        outcome_id=UUID(row["outcome_id"]),
        label=row["label"],
        description=row["description"],
        probability=None,
        normalized_weight=row["normalized_weight"],
        validity_status=row["validity_status"],
    )


def _scenario_response_with_links(
    repository: ForecastRepository,
    row: Any,
) -> ScenarioRecord:
    response = _scenario_response(row)
    return response.model_copy(
        update={
            "driver_state_ids": repository.get_scenario_driver_state_ids(
                UUID(row["scenario_id"])
            )
        }
    )


def _validate_scenarios_for_outcomes(
    forecast: ForecastDetail,
    scenarios: list[dict[str, Any]],
    scenario_driver_links: list[dict[str, str]],
) -> None:
    outcome_ids = {str(outcome.outcome_id) for outcome in forecast.outcomes}
    valid_by_outcome: dict[str, list[dict[str, Any]]] = {
        outcome_id: [] for outcome_id in outcome_ids
    }
    for scenario in scenarios:
        outcome_id = str(scenario["outcome_id"])
        if outcome_id not in outcome_ids:
            raise ForecastConflict(
                "scenario_validation_failed",
                "Scenario outcome link is invalid.",
                {"outcome_id": outcome_id},
            )
        if scenario.get("validity_status", "valid") == "valid":
            valid_by_outcome[outcome_id].append(scenario)
    missing = [
        outcome_id for outcome_id, valid in valid_by_outcome.items() if not valid
    ]
    if missing:
        raise ForecastConflict(
            "scenario_validation_failed",
            "Each outcome requires at least one valid scenario.",
            {"outcome_ids": missing},
        )
    for outcome_id, valid in valid_by_outcome.items():
        if sum(float(item.get("normalized_weight", 0.0)) for item in valid) <= 0:
            raise ForecastConflict(
                "scenario_validation_failed",
                "Valid scenario weights must be positive within each outcome.",
                {"outcome_id": outcome_id},
            )
    if scenario_driver_links:
        linked_scenarios = {link["scenario_id"] for link in scenario_driver_links}
        unlinked = [
            scenario["scenario_id"]
            for scenario in scenarios
            if scenario.get("validity_status", "valid") == "valid"
            and scenario["scenario_id"] not in linked_scenarios
        ]
        if unlinked:
            raise ForecastConflict(
                "scenario_validation_failed",
                "Morphological scenarios require at least one driver-state link.",
                {"scenario_ids": unlinked},
            )


def _ensure_framing_inputs_are_public(request: ForecastFramingDraftRequest) -> None:
    candidate_inputs: list[str] = [
        request.rough_question,
        *[answer.answer for answer in request.answers],
    ]
    if request.previous_draft is not None:
        candidate_inputs.append(
            json.dumps(
                request.previous_draft.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    candidate_text = "\n".join(candidate_inputs)
    if contains_sensitive_terms(candidate_text):
        raise ForecastConflict(
            "policy_requires_revision",
            "Revise sensitive or non-public terms before generating a framing draft.",
        )


def _build_framing_draft_prompt(request: ForecastFramingDraftRequest) -> str:
    locale_instruction = (
        "Write extracted metadata and clarifying questions in Japanese."
        if request.locale == "ja"
        else "Write extracted metadata and clarifying questions in English."
    )
    payload = {
        "rough_question": request.rough_question,
        "answers": [answer.model_dump(mode="json") for answer in request.answers],
        "previous_draft": (
            request.previous_draft.model_dump(mode="json")
            if request.previous_draft is not None
            else None
        ),
        "locale": request.locale,
    }
    return (
        "You extract metadata from a user's public Forecast PhaseA prompt. "
        "Use only the information in the request; do not browse, search, retrieve "
        "files, or ask tools. Return exactly one structured ForecastFramingDraft.\n\n"
        f"{locale_instruction}\n\n"
        "Extraction rules:\n"
        "- Preserve rough_question conceptually as the user's primary execution "
        "prompt. Do not replace, rewrite, refine, summarize, normalize, translate, "
        "or improve it.\n"
        "- question is short Forecast metadata: extract a concise, resolvable "
        "forecast question without changing the user's research intent.\n"
        "- forecast_prompt is only a short UI helper. It is not the primary execution "
        "prompt and must not replace, rewrite, or compress rough_question.\n"
        "- Extract resolution_criteria, resolution_sources, target_population, "
        "unit_of_analysis, decision_context, and outcomes only from explicit "
        "request information, answers, or previous_draft context.\n"
        "- outcomes are resolution outcome labels / 解決時の結果状態: the possible "
        "states selected when the forecast is resolved. They are not the model's "
        "final Yes/No judgment or prediction.\n"
        "- Do not convert the original prompt into a binary Yes/No forecast, do "
        "not ask the user for a final Yes/No answer, and do not use default "
        "Yes/No outcomes unless the user explicitly provided those labels.\n"
        "- If explicit resolution outcome labels or an outcome axis are present, "
        "extract them. Otherwise leave outcomes empty and ask a required "
        "clarifying question about the resolution outcome labels or axis.\n"
        "- Do not invent missing metadata. If required metadata is missing, leave "
        "string fields empty, nullable fields null, and list fields empty; ask for "
        "the missing metadata in clarifying_questions instead of filling fields "
        "with assumptions.\n"
        "- Core metadata required to create a Forecast is question, "
        "resolution_criteria, and outcomes. If any of those are missing, ask a "
        "required clarifying question for the missing field.\n"
        "- Treat answers as already supplied by the user. Do not repeat answered "
        "clarifying questions; keep only unanswered clarifying questions in "
        "clarifying_questions.\n"
        "- When question, resolution_criteria, and outcomes are all non-empty, "
        "return clarifying_questions as an empty list. Additional useful context "
        "can be reflected in decision_context instead of blocking creation.\n"
        "- Ask at most five clarifying questions and ask only for missing metadata "
        "needed to create the forecast. Mark essential questions required.\n"
        "- Do not include private, confidential, or sensitive material.\n\n"
        "Request JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    )


def _coerce_framing_draft(value: Any) -> ForecastFramingDraft:
    if isinstance(value, ForecastFramingDraft):
        return value
    if isinstance(value, str):
        return ForecastFramingDraft.model_validate_json(_extract_json_object(value))
    if hasattr(value, "model_dump"):
        return ForecastFramingDraft.model_validate(value.model_dump(mode="json"))
    return ForecastFramingDraft.model_validate(value)


def _extract_json_object(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("empty structured response")
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        candidate = stripped[start : end + 1]
        json.loads(candidate)
        return candidate


def _framing_draft_ready_to_create(
    draft: ForecastFramingDraft,
) -> bool:
    has_required_metadata = bool(
        draft.question.strip() and draft.resolution_criteria.strip() and draft.outcomes
    )
    return has_required_metadata


def _framing_draft_without_answered_questions(
    draft: ForecastFramingDraft,
    *,
    answers: list[Any],
    previous_draft: ForecastFramingDraft | None,
) -> ForecastFramingDraft:
    answered_ids = _answered_framing_question_ids(answers)
    if not answered_ids:
        return draft
    answered_prompts_by_id = _answered_framing_question_prompts_by_id(
        answered_ids,
        previous_draft,
    )
    clarifying_questions = [
        question
        for question in draft.clarifying_questions
        if not _is_answered_framing_question(
            question,
            answered_ids=answered_ids,
            answered_prompts_by_id=answered_prompts_by_id,
        )
    ]
    if len(clarifying_questions) == len(draft.clarifying_questions):
        return draft
    return draft.model_copy(update={"clarifying_questions": clarifying_questions})


def _answered_framing_question_ids(answers: list[Any]) -> set[str]:
    return {
        answer.question_id
        for answer in answers
        if getattr(answer, "answer", "").strip()
    }


def _answered_framing_question_prompts_by_id(
    answered_ids: set[str],
    previous_draft: ForecastFramingDraft | None,
) -> dict[str, set[str]] | None:
    if previous_draft is None:
        return None
    prompts_by_id: dict[str, set[str]] = {}
    for question in previous_draft.clarifying_questions:
        if question.question_id in answered_ids:
            prompts_by_id.setdefault(question.question_id, set()).add(question.prompt)
    return prompts_by_id


def _is_answered_framing_question(
    question: Any,
    *,
    answered_ids: set[str],
    answered_prompts_by_id: dict[str, set[str]] | None,
) -> bool:
    if question.question_id not in answered_ids:
        return False
    if answered_prompts_by_id is None:
        return True
    answered_prompts = answered_prompts_by_id.get(question.question_id)
    if not answered_prompts:
        return True
    return question.prompt in answered_prompts


def _ensure_forecast_has_outcomes(forecast: ForecastDetail) -> None:
    if forecast.outcomes:
        return
    raise ForecastConflict(
        "forecast_outcomes_required",
        "Forecast PhaseA requires at least one resolution outcome state.",
    )


def _framing_create_payload(
    draft: ForecastFramingDraft,
    *,
    original_execution_prompt: str | None = None,
) -> ForecastCreateRequest:
    return ForecastCreateRequest(
        question=draft.question,
        original_execution_prompt=original_execution_prompt,
        target_population=draft.target_population,
        unit_of_analysis=draft.unit_of_analysis,
        resolution_criteria=draft.resolution_criteria,
        resolution_sources=draft.resolution_sources,
        decision_context=draft.decision_context,
        confidentiality_class=ConfidentialityClass.PUBLIC,
        outcomes=draft.outcomes,
    )


def _claim_lines(report: str) -> list[str]:
    candidates = [
        re.sub(r"^[#*\-\d.\s]+", "", line).strip()
        for line in report.splitlines()
        if line.strip()
    ]
    if len(candidates) <= 1:
        candidates = [
            item.strip()
            for item in re.split(r"(?<=[。.!?])\s+", report)
            if item.strip()
        ]
    return [item[:1000] for item in candidates if len(item) >= 8]


def _fingerprint(text: str) -> str:
    return re.sub(r"\W+", " ", text.casefold()).strip()


def _publisher_group(text: str, index: int) -> str:
    match = re.search(r"https?://([^/\s]+)", text)
    if match:
        return match.group(1).lower()
    return f"report_line_{index + 1}"


def _parse_dt_required(value: str) -> Any:
    from datetime import UTC, datetime

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _is_stale_timestamp(value: str, *, seconds: int) -> bool:
    return (utc_now() - _parse_dt_required(value)).total_seconds() >= seconds


def _current_research_attempt_error(
    attempts: list[ResearchAttempt],
    effective_status: str,
) -> str | None:
    if effective_status not in {
        RunStatus.CANCELLED.value,
        RunStatus.FAILED.value,
        RunStatus.NEEDS_HUMAN_REVIEW.value,
    }:
        return None
    if not attempts or not attempts[-1].error:
        return None
    return attempts[-1].error[:1000]


def _json_load(value: str | None) -> Any:
    if not value:
        return {}
    return json.loads(value)


def _snapshot_has_non_public_evidence(snapshot: dict[str, Any]) -> bool:
    for collection_name in ("sources", "claims"):
        for item in snapshot.get(collection_name, []):
            classification = item.get("data_classification") or item.get(
                "source_classification"
            )
            if classification in {"internal", "restricted"}:
                return True
            if (
                item.get("origin_tool_profile") == "synthesis"
                and classification != "public"
            ):
                return True
    return False
