from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _strip(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


class ForecastStatus(StrEnum):
    FRAMING_PENDING = "framing_pending"
    FRAMING_APPROVED = "framing_approved"
    PACK_RUNNING = "pack_running"
    EVIDENCE_READY = "evidence_ready"
    SCENARIOS_READY = "scenarios_ready"
    DRAFT_READY = "draft_ready"
    COMMITTED = "committed"
    RESOLVED = "resolved"


class PackRole(StrEnum):
    CURRENT_STATE = "current_state"


class ToolProfile(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SYNTHESIS = "synthesis"


class ReviewAction(StrEnum):
    APPROVE_FRAMING = "approve_framing"
    APPROVE_PHASE_A_VERSION = "approve_phase_a_version"
    APPROVE_CLAIM_TARGET_LINKS = "approve_claim_target_links"


class ForecastCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=5000)
    resolution_date: date | None = None
    target_population: str | None = Field(default=None, max_length=1000)
    unit_of_analysis: str | None = Field(default=None, max_length=1000)
    resolution_criteria: str = Field(default="", max_length=5000)
    resolution_sources: list[str] = Field(default_factory=list, max_length=20)
    decision_context: str | None = Field(default=None, max_length=5000)
    confidentiality_class: Literal["public", "restricted"] = "public"
    outcomes: list[str] = Field(default_factory=list, max_length=8)

    _strip_question = field_validator("question", mode="before")(_strip)
    _strip_resolution_criteria = field_validator("resolution_criteria", mode="before")(
        _strip
    )


class ForecastCreateResponse(BaseModel):
    forecast_id: UUID
    status: ForecastStatus
    framing_version: int
    created_at: datetime


class ForecastOutcome(BaseModel):
    outcome_id: UUID
    label: str
    definition: str
    resolution_rule: str
    normalization_group_id: str
    sort_order: int


class ForecastSummary(BaseModel):
    forecast_id: UUID
    question: str
    status: ForecastStatus
    resolution_date: date | None
    current_framing_version: int
    approved_framing_version: int | None
    committed_version_id: UUID | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ForecastDetail(ForecastSummary):
    target_population: str | None
    unit_of_analysis: str | None
    resolution_criteria: str
    resolution_sources: list[str]
    decision_context: str | None
    confidentiality_class: str
    outcomes: list[ForecastOutcome]


class ForecastReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ReviewAction
    comment: str | None = Field(default=None, max_length=5000)
    estimate_set_id: UUID | None = None
    version_id: UUID | None = None


class ForecastReviewResponse(BaseModel):
    forecast_id: UUID
    action: ReviewAction
    status: ForecastStatus
    approved_framing_version: int | None = None
    estimate_set_id: UUID | None = None


class ResearchPackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_role: PackRole = PackRole.CURRENT_STATE
    tool_profile: ToolProfile = ToolProfile.PUBLIC
    max_tool_calls: int = Field(default=40, ge=1, le=120)


class ResearchPackResponse(BaseModel):
    pack_id: UUID
    forecast_id: UUID
    research_run_id: UUID
    pack_role: PackRole
    tool_profile: ToolProfile
    status: str
    policy_decision_id: UUID


class SourceRecord(BaseModel):
    source_id: UUID
    title: str
    publisher: str | None
    url: str | None
    source_type: str
    source_classification: ToolProfile
    reliability_score: float


class ClaimRecord(BaseModel):
    claim_id: UUID
    text: str
    claim_type: str
    polarity: int
    evidence_strength: float
    reliability_score: float
    cluster_id: str
    independence_group: str
    source_ids: list[UUID]
    review_status: str


class EvidenceExtractResponse(BaseModel):
    forecast_id: UUID
    sources: list[SourceRecord]
    claims: list[ClaimRecord]
    quarantine_artifact_path: str | None = None


class ScenarioRecord(BaseModel):
    scenario_id: UUID
    outcome_id: UUID
    label: str
    description: str
    probability: float | None = None
    normalized_weight: float
    validity_status: str


class ScenarioGenerateResponse(BaseModel):
    forecast_id: UUID
    scenarios: list[ScenarioRecord]


class ProbabilityEstimateRecord(BaseModel):
    estimate_id: UUID
    target_kind: str
    target_id: UUID
    prior: float
    evidence_update: float
    cross_impact_adjustment: float
    simulation_adjustment: float
    calibration_adjustment: float
    human_adjustment: float
    final_probability: float
    uncertainty_range: dict[str, float]
    components: dict[str, Any]


class EstimateSetResponse(BaseModel):
    estimate_set_id: UUID
    forecast_id: UUID
    status: str
    engine_version: str
    input_snapshot_hash: str
    engine_code_hash: str
    random_seed: int
    normalization_group_id: str
    estimates: list[ProbabilityEstimateRecord]


class CommitVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimate_set_id: UUID
    expected_input_snapshot_hash: str


class CommitVersionResponse(BaseModel):
    version_id: UUID
    forecast_id: UUID
    estimate_set_id: UUID
    input_snapshot_hash: str
    snapshot_artifact_path: str
    committed_at: datetime


class ResolveForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome_id: UUID
    resolution_notes: str | None = Field(default=None, max_length=5000)


class ResolveForecastResponse(BaseModel):
    forecast_id: UUID
    outcome_id: UUID
    multiclass_brier: float
    log_score: float
    scorer_version: str
    resolved_at: datetime


class ForecastAuditEvent(BaseModel):
    event_id: UUID
    forecast_id: UUID
    event_type: str
    event_json: dict[str, Any]
    created_at: datetime


class ForecastAuditResponse(BaseModel):
    forecast_id: UUID
    reviews: list[dict[str, Any]]
    versions: list[dict[str, Any]]
    policy_decisions: list[dict[str, Any]]
    events: list[ForecastAuditEvent]

