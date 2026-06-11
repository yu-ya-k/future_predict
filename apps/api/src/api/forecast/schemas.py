from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FRAMING_ROUGH_QUESTION_MAX_LENGTH = 50_000


def _strip(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


def _reject_blank_string(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        raise ValueError("String should not be blank")
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


class ForecastMode(StrEnum):
    DISCRETE_OUTCOME = "discrete_outcome"
    SCENARIO_PROJECTION = "scenario_projection"


class PackRole(StrEnum):
    CURRENT_STATE = "current_state"
    BASE_RATE = "base_rate"
    DRIVERS = "drivers"
    COUNTER_EVIDENCE = "counter_evidence"
    SIGNALS = "signals"


class ToolProfile(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SYNTHESIS = "synthesis"


class ConfidentialityClass(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class ReviewAction(StrEnum):
    APPROVE_FRAMING = "approve_framing"
    APPROVE_PHASE_A_VERSION = "approve_phase_a_version"
    APPROVE_CLAIM_TARGET_LINKS = "approve_claim_target_links"
    APPROVE_PRIVATE_DATA_USE = "approve_private_data_use"
    APPROVE_PROBABILITY_PUBLICATION = "approve_probability_publication"
    APPROVE_PROJECTION_PUBLICATION = "approve_projection_publication"
    OVERRIDE_PROBABILITY_WITH_REASON = "override_probability_with_reason"
    APPROVE_EXTERNAL_REPORT = "approve_external_report"
    APPROVE_TRUSTED_SOURCE = "approve_trusted_source"


class ProjectionDimensionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=300)
    unit: str = Field(min_length=1, max_length=80)
    value_type: Literal["number", "currency", "percentage", "index"] = "number"
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    nominal_or_real: Literal["nominal", "real"] | None = None
    baseline_year: int = Field(ge=1900, le=2200)
    baseline_value: float = Field(ge=0)
    baseline_source_ids: list[UUID] = Field(
        default_factory=lambda: list[UUID](),
        max_length=20,
    )
    horizons: list[int] = Field(
        default_factory=lambda: [2035],
        min_length=1,
        max_length=12,
    )

    _strip_metric_id = field_validator("metric_id", mode="before")(_strip)
    _strip_label = field_validator("label", mode="before")(_strip)
    _strip_unit = field_validator("unit", mode="before")(_strip)

    @field_validator("horizons", mode="after")
    @classmethod
    def _validate_horizons(cls, value: list[int]) -> list[int]:
        normalized = sorted(set(value))
        if not normalized:
            raise ValueError("At least one horizon is required")
        if any(year < 1900 or year > 2200 for year in normalized):
            raise ValueError("Horizon must be between 1900 and 2200")
        return normalized


class ForecastCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=5000)
    original_execution_prompt: str | None = Field(
        default=None,
        min_length=1,
        max_length=FRAMING_ROUGH_QUESTION_MAX_LENGTH,
        description=(
            "User's original execution prompt, preserved separately from extracted "
            "Forecast metadata and used as the primary research-pack task."
        ),
    )
    resolution_date: date | None = None
    target_population: str | None = Field(default=None, max_length=1000)
    unit_of_analysis: str | None = Field(default=None, max_length=1000)
    resolution_criteria: str = Field(default="", max_length=5000)
    resolution_sources: list[str] = Field(default_factory=list, max_length=20)
    decision_context: str | None = Field(default=None, max_length=5000)
    confidentiality_class: ConfidentialityClass = ConfidentialityClass.PUBLIC
    forecast_mode: ForecastMode = ForecastMode.DISCRETE_OUTCOME
    outcomes: list[str] = Field(default_factory=list, max_length=8)
    projection_dimensions: list[ProjectionDimensionInput] = Field(
        default_factory=lambda: list[ProjectionDimensionInput](),
        max_length=12,
    )

    _strip_question = field_validator("question", mode="before")(_strip)
    _strip_resolution_criteria = field_validator("resolution_criteria", mode="before")(
        _strip
    )
    _reject_blank_original_execution_prompt = field_validator(
        "original_execution_prompt", mode="after"
    )(_reject_blank_string)

    @field_validator("outcomes", mode="after")
    @classmethod
    def _strip_outcomes(cls, value: list[str]) -> list[str]:
        return [label.strip() for label in value if label.strip()]

    @model_validator(mode="after")
    def _validate_mode_payload(self) -> ForecastCreateRequest:
        if (
            self.forecast_mode == ForecastMode.DISCRETE_OUTCOME
            and self.projection_dimensions
        ):
            raise ValueError("forecast_mode_payload_mismatch")
        if self.forecast_mode == ForecastMode.SCENARIO_PROJECTION:
            if self.outcomes:
                raise ValueError("forecast_mode_payload_mismatch")
            if not self.projection_dimensions:
                raise ValueError("projection_dimensions_required")
        return self


class ForecastCreateResponse(BaseModel):
    forecast_id: UUID
    status: ForecastStatus
    forecast_mode: ForecastMode
    framing_version: int
    created_at: datetime


class ForecastFramingDraftClarifyingQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=1000)
    why_needed: str = Field(min_length=1, max_length=1000)
    answer_type: Literal[
        "text",
        "single_select",
        "multi_select",
        "number",
        "date",
        "boolean",
    ] = "text"
    required: bool = True
    options: list[str] = Field(default_factory=list, max_length=12)

    _strip_question_id = field_validator("question_id", mode="before")(_strip)
    _strip_label = field_validator("label", mode="before")(_strip)
    _strip_prompt = field_validator("prompt", mode="before")(_strip)
    _strip_why_needed = field_validator("why_needed", mode="before")(_strip)


def _default_clarifying_questions() -> list[ForecastFramingDraftClarifyingQuestion]:
    return []


class ForecastFramingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forecast_prompt: str = Field(
        min_length=1,
        max_length=8000,
        description=(
            "UI helper text only. It must not replace, rewrite, summarize, or "
            "normalize the user's original prompt."
        ),
    )
    question: str = Field(
        min_length=1,
        max_length=5000,
        description=(
            "Short resolvable forecast question metadata. The user's original "
            "execution prompt is stored separately and remains the primary task."
        ),
    )
    resolution_criteria: str = Field(
        default="",
        max_length=5000,
        description=(
            "Extracted resolution metadata based only on explicit user input, "
            "answers, or previous draft context; leave empty when not provided."
        ),
    )
    resolution_sources: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Extracted public source metadata; leave empty when not provided.",
    )
    target_population: str | None = Field(
        default=None,
        max_length=1000,
        description="Extracted metadata; null when the user has not provided it.",
    )
    unit_of_analysis: str | None = Field(
        default=None,
        max_length=1000,
        description="Extracted metadata; null when the user has not provided it.",
    )
    decision_context: str | None = Field(
        default=None,
        max_length=5000,
        description="Extracted metadata; null when the user has not provided it.",
    )
    outcomes: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Resolution outcome labels / 解決時の結果状態 extracted from explicit "
            "user input. These are the possible states selected when the forecast "
            "is resolved, not the model's final Yes/No judgment; leave empty when "
            "not provided."
        ),
    )
    clarifying_questions: list[ForecastFramingDraftClarifyingQuestion] = Field(
        default_factory=_default_clarifying_questions,
        max_length=5,
    )
    confidence: float = Field(ge=0, le=1)

    _strip_forecast_prompt = field_validator("forecast_prompt", mode="before")(_strip)
    _strip_question = field_validator("question", mode="before")(_strip)
    _strip_resolution_criteria = field_validator("resolution_criteria", mode="before")(
        _strip
    )

    @field_validator("outcomes", mode="after")
    @classmethod
    def _strip_outcomes(cls, value: list[str]) -> list[str]:
        return [label.strip() for label in value if label.strip()]


class ForecastFramingDraftAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=100)
    answer: str = Field(min_length=1, max_length=2000)

    _strip_question_id = field_validator("question_id", mode="before")(_strip)
    _strip_answer = field_validator("answer", mode="before")(_strip)


def _default_framing_draft_answers() -> list[ForecastFramingDraftAnswer]:
    return []


class ForecastFramingDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rough_question: str = Field(min_length=1, max_length=FRAMING_ROUGH_QUESTION_MAX_LENGTH)
    answers: list[ForecastFramingDraftAnswer] = Field(
        default_factory=_default_framing_draft_answers,
        max_length=5,
    )
    previous_draft: ForecastFramingDraft | None = None
    locale: Literal["ja", "en"] = "ja"

    _reject_blank_rough_question = field_validator("rough_question", mode="after")(
        _reject_blank_string
    )


class ForecastFramingDraftResponse(BaseModel):
    draft: ForecastFramingDraft
    create_payload: ForecastCreateRequest | None = None
    ready_to_create: bool
    model: str
    response_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ForecastOutcome(BaseModel):
    outcome_id: UUID
    label: str
    definition: str
    resolution_rule: str
    normalization_group_id: str
    sort_order: int


class ProjectionDimensionRecord(BaseModel):
    dimension_id: UUID
    forecast_id: UUID
    framing_version: int
    metric_id: str
    label: str
    unit: str
    value_type: str
    currency: str | None = None
    nominal_or_real: str | None = None
    baseline_year: int
    baseline_value: float
    baseline_source_ids: list[UUID]
    horizons: list[int]
    sort_order: int
    frozen: bool = False


class ProjectionScenarioRecord(BaseModel):
    projection_scenario_id: UUID
    projection_set_id: UUID
    label: str
    description: str
    coverage_role: str
    residual_flag: bool
    probability: float
    probability_logit: float
    driver_vector: dict[str, Any]
    narrative: str
    validity_status: str


class ProjectionMetricPointRecord(BaseModel):
    metric_point_id: UUID
    projection_set_id: UUID
    projection_scenario_id: UUID
    dimension_id: UUID
    metric_id: str
    horizon_year: int
    p10: float
    p50: float
    p90: float
    mean: float
    distribution_family: str
    distribution_params: dict[str, Any]
    baseline_transform: str


class ProjectionCompositeRecord(BaseModel):
    composite_id: UUID
    projection_set_id: UUID
    dimension_id: UUID
    metric_id: str
    horizon_year: int
    p10: float
    p50: float
    p90: float
    mean: float
    mixture_components: list[dict[str, Any]]


class ProjectionSensitivityRecord(BaseModel):
    sensitivity_id: UUID
    projection_set_id: UUID
    sensitivity_kind: str
    target_ref: str
    baseline_snapshot_hash: str
    perturbed_input: dict[str, Any]
    delta_p50: float
    delta_p90: float
    delta_probability: float
    rank: int


class ProjectionSetResponse(BaseModel):
    projection_set_id: UUID
    forecast_id: UUID
    status: str
    approved: bool = False
    engine_version: str
    input_snapshot_hash: str
    engine_code_hash: str
    random_seed: int
    snapshot_artifact_path: str | None = None
    scenarios: list[ProjectionScenarioRecord]
    metric_points: list[ProjectionMetricPointRecord]
    composites: list[ProjectionCompositeRecord]
    sensitivities: list[ProjectionSensitivityRecord]


class ForecastSummary(BaseModel):
    forecast_id: UUID
    forecast_mode: ForecastMode = ForecastMode.DISCRETE_OUTCOME
    question: str
    status: ForecastStatus
    resolution_date: date | None
    current_framing_version: int
    approved_framing_version: int | None
    committed_version_id: UUID | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ForecastCurrentResearchPack(BaseModel):
    pack_id: UUID
    research_run_id: UUID
    pack_role: PackRole = PackRole.CURRENT_STATE
    tool_profile: ToolProfile = ToolProfile.PUBLIC
    pack_status: str
    effective_status: str
    research_run_status: str
    pack_created_at: datetime
    pack_updated_at: datetime
    research_run_created_at: datetime | None = None
    research_run_updated_at: datetime | None = None
    deep_research_started_at: datetime | None = None
    total_tool_calls: int = 0
    estimated_cost_usd: float = 0.0
    done_reason: str | None = None
    last_error: str | None = None
    needs_human_review: bool = False


def _default_research_packs() -> list[ForecastCurrentResearchPack]:
    return []


def _default_projection_dimensions() -> list[ProjectionDimensionRecord]:
    return []


class ForecastDetail(ForecastSummary):
    original_execution_prompt: str | None
    target_population: str | None
    unit_of_analysis: str | None
    resolution_criteria: str
    resolution_sources: list[str]
    decision_context: str | None
    confidentiality_class: str
    outcomes: list[ForecastOutcome]
    projection_dimensions: list[ProjectionDimensionRecord] = Field(
        default_factory=_default_projection_dimensions
    )
    current_projection_set: ProjectionSetResponse | None = None
    current_research_pack: ForecastCurrentResearchPack | None = None
    current_research_pack_status: str | None = None
    research_packs: list[ForecastCurrentResearchPack] = Field(
        default_factory=_default_research_packs
    )
    approved_claim_target_link_count: int = 0


class ForecastReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ReviewAction
    comment: str | None = Field(default=None, max_length=5000)
    estimate_set_id: UUID | None = None
    projection_set_id: UUID | None = None
    version_id: UUID | None = None
    reviewer: str | None = Field(default=None, max_length=500)
    reviewer_auth_subject: str | None = Field(default=None, max_length=500)
    policy_decision_id: UUID | None = None
    review_reason: str | None = Field(default=None, max_length=5000)


class ForecastReviewResponse(BaseModel):
    forecast_id: UUID
    action: ReviewAction
    status: ForecastStatus
    approved_framing_version: int | None = None
    estimate_set_id: UUID | None = None
    projection_set_id: UUID | None = None


class ResearchPackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_role: PackRole = PackRole.CURRENT_STATE
    tool_profile: ToolProfile = ToolProfile.PUBLIC
    max_tool_calls: int = Field(default=40, ge=1, le=120)
    background: bool = True
    data_classification: ConfidentialityClass = ConfidentialityClass.PUBLIC
    vector_store_ids: list[str] = Field(default_factory=list, max_length=2)
    mcp_server_ids: list[str] = Field(default_factory=list, max_length=10)
    trusted_source_identifiers: list[str] = Field(default_factory=list, max_length=20)
    timeout_sec: int | None = Field(default=None, ge=1)
    estimated_cost_budget_usd: float | None = Field(default=None, ge=0)


class ResearchPackResponse(BaseModel):
    pack_id: UUID
    forecast_id: UUID
    research_run_id: UUID
    pack_role: PackRole
    tool_profile: ToolProfile
    status: str
    policy_decision_id: UUID
    attempt_no: int = 1
    is_active: bool = True
    data_classification: ConfidentialityClass = ConfidentialityClass.PUBLIC


class ResearchPackDefaultsResponse(BaseModel):
    packs: list[ResearchPackResponse]


class ResearchPackRerunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_active_pack_id: UUID
    max_tool_calls: int = Field(default=40, ge=1, le=120)
    background: bool = True
    timeout_sec: int | None = Field(default=None, ge=1)
    estimated_cost_budget_usd: float | None = Field(default=None, ge=0)


class ManualResearchPackPromptResponse(BaseModel):
    forecast_id: UUID
    framing_version: int
    prompt: str
    prompt_sha256: str
    prompt_version: str
    pack_role: PackRole
    tool_profile: ToolProfile
    max_report_chars: int
    max_file_bytes: int
    pack_id: UUID | None = None
    research_run_id: UUID | None = None
    recovering_existing_pack: bool = False
    recoverable_status: str | None = None


class SourceRecord(BaseModel):
    source_id: UUID
    title: str
    publisher: str | None
    url: str | None
    source_type: str
    source_classification: str
    data_classification: ConfidentialityClass = ConfidentialityClass.PUBLIC
    origin_tool_profile: ToolProfile = ToolProfile.PUBLIC
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
    data_classification: ConfidentialityClass = ConfidentialityClass.PUBLIC
    origin_tool_profile: ToolProfile = ToolProfile.PUBLIC


class EvidenceExtractResponse(BaseModel):
    forecast_id: UUID
    sources: list[SourceRecord]
    claims: list[ClaimRecord]
    quarantine_artifact_path: str | None = None


def _default_driver_state_ids() -> list[UUID]:
    return []


class ScenarioRecord(BaseModel):
    scenario_id: UUID
    outcome_id: UUID
    label: str
    description: str
    probability: float | None = None
    normalized_weight: float
    validity_status: str
    driver_state_ids: list[UUID] = Field(default_factory=_default_driver_state_ids)


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
    approved: bool = False
    engine_version: str
    input_snapshot_hash: str
    engine_code_hash: str
    random_seed: int
    normalization_group_id: str
    estimates: list[ProbabilityEstimateRecord]


class ComputeProbabilitiesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_version: Literal["phase_a_v1", "phase_b_v1"] | None = None


class ComputeProjectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_version: Literal["phase_c_v1"] | None = None


class ForecastDriverRecord(BaseModel):
    driver_id: UUID
    forecast_id: UUID
    name: str
    description: str
    sort_order: int


class ForecastDriverStateRecord(BaseModel):
    state_id: UUID
    driver_id: UUID
    label: str
    description: str
    sort_order: int


def _default_tool_profiles() -> list[ToolProfile]:
    return []


def _default_pack_roles() -> list[PackRole]:
    return []


def _default_string_list() -> list[str]:
    return []


class ForecastTrustedSourceRecord(BaseModel):
    trusted_source_id: UUID
    identifier: str
    status: Literal["pending", "approved", "revoked", "expired"]
    approved_by: str | None = None
    approved_at: datetime | None = None
    expires_at: datetime | None = None
    allowed_profiles: list[ToolProfile] = Field(default_factory=_default_tool_profiles)
    allowed_pack_roles: list[PackRole] = Field(default_factory=_default_pack_roles)
    allowed_tool_names: list[str] = Field(default_factory=_default_string_list)
    allowed_vector_store_ids: list[str] = Field(default_factory=_default_string_list)
    allowed_mcp_server_ids: list[str] = Field(default_factory=_default_string_list)
    owner_team_id: str | None = None


class CommitVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimate_set_id: UUID | None = None
    projection_set_id: UUID | None = None
    expected_input_snapshot_hash: str

    @model_validator(mode="after")
    def _validate_exactly_one_set(self) -> CommitVersionRequest:
        if (self.estimate_set_id is None) == (self.projection_set_id is None):
            raise ValueError("Provide exactly one of estimate_set_id or projection_set_id")
        return self


class CommitVersionResponse(BaseModel):
    version_id: UUID
    forecast_id: UUID
    version_kind: Literal["estimate", "projection"] = "estimate"
    estimate_set_id: UUID | None = None
    projection_set_id: UUID | None = None
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
