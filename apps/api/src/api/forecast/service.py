from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from api.config import Settings
from api.forecast.artifacts import ForecastArtifactStore
from api.forecast.errors import ForecastConflict
from api.forecast.policy import evaluate_forecast_policy
from api.forecast.probability.phase_a_v1 import (
    ENGINE_VERSION,
    RANDOM_SEED,
    SCORER_VERSION,
    canonical_json_bytes,
    compute_phase_a_estimates,
    engine_code_hash,
    log_score,
    multiclass_brier,
    snapshot_hash,
)
from api.forecast.repository import ForecastRepository
from api.forecast.research_packs import (
    CURRENT_STATE_PROMPT_VERSION,
    build_current_state_prompt,
)
from api.forecast.schemas import (
    ClaimRecord,
    CommitVersionResponse,
    ForecastAuditEvent,
    ForecastAuditResponse,
    ForecastCreateRequest,
    ForecastCreateResponse,
    ForecastDetail,
    ForecastOutcome,
    ForecastStatus,
    ForecastSummary,
    PackRole,
    ProbabilityEstimateRecord,
    ResearchPackRequest,
    ResearchPackResponse,
    ResolveForecastResponse,
    ScenarioRecord,
    SourceRecord,
    ToolProfile,
)
from api.research.schemas import CreateResearchRunRequest, ResearchRunOptions, RunStatus
from api.research.service import ResearchOrchestrator


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
        return ForecastDetail(**base, outcomes=outcomes)

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

    def dispatch_research_pack(
        self,
        forecast_id: UUID,
        request: ResearchPackRequest,
        *,
        idempotency_key: str | None = None,
    ) -> ResearchPackResponse:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        if forecast.approved_framing_version != forecast.current_framing_version:
            raise ForecastConflict(
                "framing_not_approved",
                "Approve the latest framing before dispatching research packs.",
            )
        if forecast.status != ForecastStatus.FRAMING_APPROVED:
            raise ForecastConflict(
                "forecast_already_started",
                "A PhaseA research pack can only be dispatched once.",
            )
        if forecast.confidentiality_class != "public":
            raise ForecastConflict(
                "policy_requires_revision",
                "PhaseA only supports public forecasts.",
            )
        if (
            request.pack_role != PackRole.CURRENT_STATE
            or request.tool_profile != ToolProfile.PUBLIC
        ):
            raise ForecastConflict(
                "policy_requires_revision",
                "PhaseA only supports public current_state research packs.",
            )
        existing = self.repository.list_packs(forecast_id)
        if existing:
            raise ForecastConflict(
                "forecast_already_started",
                "A PhaseA research pack already exists for this forecast.",
            )
        prompt = build_current_state_prompt(forecast)
        policy = evaluate_forecast_policy(prompt, profile=request.tool_profile.value)
        policy_decision_id = self.repository.add_policy_decision(
            forecast_id=forecast_id,
            profile=request.tool_profile.value,
            status=policy.status,
            reason=policy.reason,
            prompt_hash=policy.prompt_hash,
        )
        if policy.status == "blocked":
            raise ForecastConflict(
                "policy_blocked",
                policy.reason or "Forecast research pack was blocked by policy.",
                {"policy_decision_id": str(policy_decision_id)},
            )
        if policy.status == "require_human_review":
            raise ForecastConflict(
                "policy_requires_revision",
                policy.reason or "Policy requires revision for PhaseA.",
                {"policy_decision_id": str(policy_decision_id)},
            )

        run = self.research.create_run(
            CreateResearchRunRequest(
                user_prompt=prompt,
                options=ResearchRunOptions(max_total_tool_calls=request.max_tool_calls),
            ),
            forecast_mode=True,
            tool_profile="public",
            background=self.settings.forecast_background_mode_enabled,
            policy_decision_id=str(policy_decision_id),
        )
        if (
            run.status != RunStatus.WAITING_DEEP_RESEARCH
            or run.pending_deep_research_response_id is None
        ):
            raise ForecastConflict(
                "policy_requires_revision",
                "Forecast research pack did not enter Deep Research collection.",
                {
                    "research_run_id": str(run.id),
                    "status": run.status.value,
                    "done_reason": run.done_reason,
                },
            )
        pack = self.repository.add_research_pack(
            forecast_id=forecast_id,
            research_run_id=run.id,
            pack_role=request.pack_role.value,
            tool_profile=request.tool_profile.value,
            status="running",
            model_deployment=getattr(self.research.azure, "deep_research_deployment", None),
            prompt_version=CURRENT_STATE_PROMPT_VERSION,
            max_tool_calls=request.max_tool_calls,
            policy_decision_id=policy_decision_id,
        )
        return _pack_response(pack)

    def extract_evidence(self, forecast_id: UUID) -> tuple[list[SourceRecord], list[ClaimRecord]]:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
        if forecast.status not in {ForecastStatus.PACK_RUNNING, ForecastStatus.EVIDENCE_READY}:
            raise ForecastConflict(
                "pack_not_completed",
                "Research pack must complete before evidence extraction.",
            )
        pack = self._single_completed_pack(forecast.forecast_id)
        run = self.research.repository.get_run(UUID(pack["research_run_id"]))
        if run.status != RunStatus.COMPLETED:
            raise ForecastConflict(
                "pack_not_completed",
                "Research pack must complete before evidence extraction.",
                {"research_run_id": str(run.id), "status": run.status.value},
            )
        report = (run.final_report or run.report or "").strip()
        if not report:
            raise ForecastConflict(
                "pack_not_completed",
                "Research pack completed without a report.",
                {"research_run_id": str(run.id)},
            )
        source_id = str(uuid4())
        sources = [
            {
                "source_id": source_id,
                "title": "Current-state Deep Research report",
                "publisher": "Deep Research",
                "url": None,
                "source_type": "research_report",
                "source_classification": ToolProfile.PUBLIC.value,
                "reliability_score": 0.72,
                "metadata": {"research_run_id": str(run.id)},
            }
        ]
        outcomes = forecast.outcomes
        lines = _claim_lines(report)
        claims: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        for index, line in enumerate(lines[: max(1, min(len(lines), 12))]):
            outcome = outcomes[index % len(outcomes)]
            independence_group = _publisher_group(line, index)
            cluster_id = hashlib.sha256(
                f"{_fingerprint(line)}:{independence_group}".encode()
            ).hexdigest()
            claim_id = str(uuid4())
            claims.append(
                {
                    "claim_id": claim_id,
                    "text": line,
                    "claim_type": "current_state",
                    "polarity": 1,
                    "evidence_strength": 0.65,
                    "reliability_score": 0.72,
                    "cluster_id": cluster_id,
                    "independence_group": independence_group,
                    "source_classification": ToolProfile.PUBLIC.value,
                    "extraction_model": "deterministic_phase_a_extractor",
                    "extraction_prompt_version": "phase_a_claims_v1",
                    "review_status": "approved",
                    "source_ids": [source_id],
                }
            )
            links.append(
                {
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
        self.repository.mark_pack_completed(
            pack_id=UUID(pack["pack_id"]),
            report_artifact_hash=hashlib.sha256(report.encode("utf-8")).hexdigest(),
        )
        source_rows, claim_rows = self.repository.replace_evidence(
            forecast_id=forecast.forecast_id,
            pack_id=UUID(pack["pack_id"]),
            sources=sources,
            claims=claims,
            links=links,
        )
        return (
            [_source_response(row) for row in source_rows],
            [_claim_response(self.repository, row) for row in claim_rows],
        )

    def generate_scenarios(self, forecast_id: UUID) -> list[ScenarioRecord]:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
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
        scenarios = [
            {
                "scenario_id": str(uuid4()),
                "outcome_id": str(outcome.outcome_id),
                "label": outcome.label,
                "description": f"Scenario in which the forecast resolves as: {outcome.definition}",
                "normalized_weight": 1.0,
                "validity_status": "valid",
            }
            for outcome in forecast.outcomes
        ]
        rows = self.repository.replace_scenarios(
            forecast_id=forecast.forecast_id,
            scenarios=scenarios,
        )
        return [_scenario_response(row) for row in rows]

    def compute_probabilities(self, forecast_id: UUID) -> dict[str, Any]:
        forecast = self.get_forecast(forecast_id)
        self._ensure_mutable(forecast)
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
        snapshot = self._canonical_snapshot(forecast)
        input_hash = snapshot_hash(snapshot)
        existing = self.repository.get_draft_estimate_set(forecast.forecast_id)
        if existing is not None:
            if existing["input_snapshot_hash"] == input_hash:
                return self.estimate_set_response(UUID(existing["estimate_set_id"]))
            raise ForecastConflict(
                "draft_estimate_set_exists",
                "A draft estimate set already exists for different inputs.",
                {
                    "estimate_set_id": existing["estimate_set_id"],
                    "existing_input_snapshot_hash": existing["input_snapshot_hash"],
                    "new_input_snapshot_hash": input_hash,
                },
            )
        estimates = compute_phase_a_estimates(snapshot=snapshot)
        estimate_set = self.repository.create_draft_estimate_set(
            forecast_id=forecast.forecast_id,
            engine_version=ENGINE_VERSION,
            input_snapshot_hash=input_hash,
            engine_code_hash=engine_code_hash(),
            random_seed=RANDOM_SEED,
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
        path, digest, _bytes = self.artifacts.save_bytes(
            forecast_id,
            "public",
            f"versions/{estimate_set_id}.snapshot.json",
            canonical_json_bytes(snapshot),
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
        if snapshot_hash(snapshot) != version["input_snapshot_hash"]:
            raise ForecastConflict(
                "approval_required",
                "Committed snapshot artifact hash does not match the version record.",
            )
        estimates = compute_phase_a_estimates(snapshot=snapshot)
        brier = multiclass_brier(estimates, actual_outcome_id=str(outcome_id))
        log = log_score(estimates, actual_outcome_id=str(outcome_id))
        try:
            resolution = self.repository.resolve_forecast(
                forecast_id=forecast.forecast_id,
                version_id=UUID(version["version_id"]),
                outcome_id=outcome_id,
                multiclass_brier=brier,
                log_score=log,
                scorer_version=SCORER_VERSION,
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

    def _canonical_snapshot(self, forecast: ForecastDetail) -> dict[str, Any]:
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
            }
            for row in self.repository.list_packs(forecast.forecast_id)
        ]
        packs = sorted(packs, key=lambda item: item["pack_id"])
        return {
            "engine_version": ENGINE_VERSION,
            "engine_code_hash": engine_code_hash(),
            "prompt_versions": {
                "current_state": CURRENT_STATE_PROMPT_VERSION,
                "extractor": "phase_a_claims_v1",
                "scenario": "phase_a_scenarios_v1",
            },
            "kappa": 1.0,
            "clamp": 3.0,
            "epsilon_floor": 1e-9,
            "random_seed": RANDOM_SEED,
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


def _pack_response(row: Any) -> ResearchPackResponse:
    return ResearchPackResponse(
        pack_id=UUID(row["pack_id"]),
        forecast_id=UUID(row["forecast_id"]),
        research_run_id=UUID(row["research_run_id"]),
        pack_role=PackRole(row["pack_role"]),
        tool_profile=ToolProfile(row["tool_profile"]),
        status=row["status"],
        policy_decision_id=UUID(row["policy_decision_id"]),
    )


def _source_response(row: Any) -> SourceRecord:
    return SourceRecord(
        source_id=UUID(row["source_id"]),
        title=row["title"],
        publisher=row["publisher"],
        url=row["url"],
        source_type=row["source_type"],
        source_classification=ToolProfile(row["source_classification"]),
        reliability_score=row["reliability_score"],
    )


def _claim_response(repository: ForecastRepository, row: Any) -> ClaimRecord:
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


def _json_load(value: str | None) -> Any:
    if not value:
        return {}
    return json.loads(value)
