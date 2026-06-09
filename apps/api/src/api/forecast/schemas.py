from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    confidentiality_class: Literal["public", "restricted"] = "public"
    outcomes: list[str] = Field(default_factory=list, max_length=8)

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


class ForecastCreateResponse(BaseModel):
    forecast_id: UUID
    status: ForecastStatus
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


class ForecastCurrentResearchPack(BaseModel):
    pack_id: UUID
    research_run_id: UUID
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


class ForecastDetail(ForecastSummary):
    original_execution_prompt: str | None
    target_population: str | None
    unit_of_analysis: str | None
    resolution_criteria: str
    resolution_sources: list[str]
    decision_context: str | None
    confidentiality_class: str
    outcomes: list[ForecastOutcome]
    current_research_pack: ForecastCurrentResearchPack | None = None
    current_research_pack_status: str | None = None
    approved_claim_target_link_count: int = 0


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
    approved: bool = False
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
