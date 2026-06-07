from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _strip_string(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


class RunStatus(StrEnum):
    QUEUED = "queued"
    SUBMITTED = "submitted"
    WAITING_DEEP_RESEARCH = "waiting_deep_research"
    COLLECTING = "collecting"
    REVIEWING = "reviewing"
    NEEDS_ACTION = "needs_action"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Verdict(StrEnum):
    PASS = "pass"
    NEEDS_LLM_PATCH = "needs_llm_patch"
    NEEDS_VERIFICATION = "needs_verification"
    NEEDS_TARGETED_RERUN = "needs_targeted_rerun"
    NEEDS_FULL_RERUN = "needs_full_rerun"
    NEEDS_ITEM_REVISION = "needs_item_revision"
    FINALIZE_WITH_LIMITATION = "finalize_with_limitation"
    HUMAN_REVIEW = "human_review"


class HumanReviewAction(StrEnum):
    APPROVE = "approve"
    APPROVE_WITH_LIMITATION = "approve_with_limitation"
    REQUEST_REVIEW = "request_review"
    REQUEST_LLM_PATCH = "request_llm_patch"
    REQUEST_VERIFICATION = "request_verification"
    REQUEST_TARGETED_RERUN = "request_targeted_rerun"
    REQUEST_FULL_RERUN = "request_full_rerun"
    REQUEST_ITEM_REVISION = "request_item_revision"
    REJECT = "reject"


class RerunExecutionMode(StrEnum):
    API = "api"
    MANUAL_CHATGPT = "manual_chatgpt"
    DISABLED = "disabled"


class ResearchRunOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_targeted_rerun_runs: int | None = Field(default=None, ge=0, le=5)
    max_full_rerun_runs: int | None = Field(default=None, ge=0, le=3)
    max_llm_patch_runs: int | None = Field(default=None, ge=0, le=10)
    max_verification_runs: int | None = Field(default=None, ge=0, le=10)
    max_total_iterations: int | None = Field(default=None, ge=1, le=20)
    max_total_tool_calls: int | None = Field(default=None, ge=1)


class CreateResearchRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_prompt: str = Field(min_length=1, max_length=50000)
    options: ResearchRunOptions = Field(default_factory=ResearchRunOptions)

    _strip_user_prompt = field_validator("user_prompt", mode="before")(_strip_string)


class CreateResearchRunResponse(BaseModel):
    run_id: UUID
    thread_id: str
    status: RunStatus
    created_at: datetime


class RunProgress(BaseModel):
    deep_research_runs: int
    targeted_rerun_runs: int
    full_rerun_runs: int
    llm_patch_runs: int
    verification_runs: int
    total_reviews: int
    latest_verdict: Verdict | None
    latest_score: int | None
    items_total: int = 0
    items_answered: int = 0
    items_partial: int = 0
    items_unanswered: int = 0
    items_unverifiable: int = 0
    blockers_unresolved: int = 0
    total_tool_calls: int = 0
    estimated_cost_usd: float = 0.0


class HumanReviewAuditSummary(BaseModel):
    deep_research_runs: int
    targeted_rerun_runs: int
    full_rerun_runs: int
    llm_patch_runs: int
    verification_runs: int
    total_reviews: int
    no_progress_count: int
    total_tool_calls: int = 0
    estimated_cost_usd: float = 0.0


class ResearchRunStatusResponse(BaseModel):
    run_id: UUID
    status: RunStatus
    terminal_status: str | None = None
    done_reason: str | None
    needs_human_review: bool
    deep_research_submitted_at: datetime | None = None
    progress: RunProgress


class ReportResponse(BaseModel):
    run_id: UUID
    status: RunStatus
    final_report: str | None
    report: str | None
    warnings: list[str]


class Citation(BaseModel):
    title: str | None = None
    url: str | None = None
    start_index: int | None = None
    end_index: int | None = None
    source_type: str | None = None
    retrieved_at: str | None = None


class ToolCallSummary(BaseModel):
    type: str
    status: str | None = None
    query: str | None = None
    url: str | None = None
    server_label: str | None = None
    duration_ms: int | None = None
    step: str | None = None
    response_id: str | None = None


class ExpectedAnswerType(StrEnum):
    FACT = "fact"
    COMPARISON = "comparison"
    TIMELINE = "timeline"
    MARKET_SIZE = "market_size"
    PROS_CONS = "pros_cons"
    RISK = "risk"
    RECOMMENDATION = "recommendation"
    SYNTHESIS = "synthesis"
    OTHER = "other"


class ItemStatus(StrEnum):
    NOT_STARTED = "not_started"
    ANSWERED = "answered"
    PARTIAL = "partial"
    UNANSWERED = "unanswered"
    UNVERIFIABLE = "unverifiable"
    OUT_OF_SCOPE = "out_of_scope"


class Severity(StrEnum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


class FailureMode(StrEnum):
    NONE = "none"
    FORMAT_ONLY = "format_only"
    IN_REPORT_BUT_LOST = "in_report_but_lost"
    NEEDS_TARGETED_VERIFICATION = "needs_targeted_verification"
    NEEDS_DIFFERENT_SOURCES = "needs_different_sources"
    NEEDS_DEEPER_SEARCH = "needs_deeper_search"
    NEEDS_QUERY_REFORMULATION = "needs_query_reformulation"
    SOURCE_CONTRADICTION = "source_contradiction"
    LIKELY_NOT_PUBLICLY_AVAILABLE = "likely_not_publicly_available"
    CRITERION_TOO_AMBIGUOUS = "criterion_too_ambiguous"
    REQUIRES_HUMAN_JUDGMENT = "requires_human_judgment"


class RecommendedAction(StrEnum):
    NONE = "none"
    LLM_PATCH = "llm_patch"
    VERIFY = "verify"
    TARGETED_RERUN = "targeted_rerun"
    FULL_RERUN = "full_rerun"
    HUMAN_REVIEW = "human_review"
    FINALIZE_WITH_LIMITATION = "finalize_with_limitation"
    REVISE_ITEMS = "revise_items"


class AcceptanceCriterion(BaseModel):
    criterion_id: str
    description: str
    verification_method: str
    severity: Severity
    required_evidence_type: list[str] = Field(default_factory=list)
    required_freshness: str | None = None
    generated_by: str = "task_template"
    confidence: int = Field(default=80, ge=0, le=100)


class ObjectiveContract(BaseModel):
    contract_id: str
    original_user_prompt: str
    normalized_objective: str
    task_type: str = "other"
    acceptance_criteria: list[AcceptanceCriterion]
    source_policy: dict[str, Any] = Field(default_factory=dict)
    freshness_policy: dict[str, Any] = Field(default_factory=dict)
    security_policy: dict[str, Any] = Field(default_factory=dict)
    output_requirements: list[str] = Field(default_factory=list)
    explicit_out_of_scope: list[str] = Field(default_factory=list)
    contract_confidence: int = Field(default=80, ge=0, le=100)
    contract_frozen: bool = True


class ResearchItem(BaseModel):
    item_id: str
    criterion_id: str
    question: str
    expected_answer_type: ExpectedAnswerType = ExpectedAnswerType.OTHER
    status: ItemStatus = ItemStatus.NOT_STARTED
    severity: Severity = Severity.MAJOR
    confidence: int = Field(default=0, ge=0, le=100)
    evidence_summary: str | None = None
    citation_ids: list[str] = Field(default_factory=list)
    failure_mode: FailureMode | None = None
    failure_mode_confidence: int | None = Field(default=None, ge=0, le=100)
    unresolved_reason: str | None = None
    tried_queries: list[str] = Field(default_factory=list)
    tried_source_types: list[str] = Field(default_factory=list)
    last_attempt_no: int | None = None
    last_review_no: int | None = None


class ItemAssessment(BaseModel):
    item_id: str
    status: ItemStatus
    severity: Severity
    failure_mode: FailureMode = FailureMode.NONE
    failure_mode_confidence: int = Field(ge=0, le=100)
    recommended_action: RecommendedAction
    evidence_summary: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    rationale: str


class RerunPlan(BaseModel):
    rerun_id: str
    scope: str
    target_item_ids: list[str]
    preserve_item_ids: list[str] = Field(default_factory=list)
    target_questions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    preferred_source_types: list[str] = Field(default_factory=list)
    freshness_requirements: list[str] = Field(default_factory=list)
    already_tried_queries: list[str] = Field(default_factory=list)
    already_used_source_domains: list[str] = Field(default_factory=list)
    negative_source_hints: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)
    output_mode: str = "delta_sections_only"
    max_tool_calls: int = Field(default=10, ge=1)
    rerun_reason: str
    created_at: datetime | None = None


class PatchDelta(BaseModel):
    target_item_id: str
    section_id: str
    operation: str
    new_text: str
    citation_ids: list[str] = Field(default_factory=list)
    patch_reason: str


class EvidenceLite(BaseModel):
    evidence_id: str
    item_id: str
    citation_id: str
    title: str | None = None
    url: str | None = None
    source_type: str | None = None
    retrieved_at: str
    evidence_summary: str


class VerificationRequest(BaseModel):
    item_id: str
    verification_question: str
    current_claim_summary: str
    allowed_context: str


class QueryPolicyDecision(BaseModel):
    status: str
    safe_queries: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None


class VerificationQuery(BaseModel):
    item_id: str
    raw_query: str | None = None
    safe_query: str | None = None
    policy_status: str
    blocked_reason: str | None = None
    created_at: datetime | None = None


def _empty_citations() -> list[Citation]:
    return []


def _empty_tool_calls() -> list[ToolCallSummary]:
    return []


def _empty_research_items() -> list[ResearchItem]:
    return []


def _empty_rerun_plans() -> list[RerunPlan]:
    return []


def _empty_verification_queries() -> list[VerificationQuery]:
    return []


def _empty_human_review_items() -> list[ResearchItem]:
    return []


class ResearchAttempt(BaseModel):
    run_no: int
    response_id: str | None = None
    status: str
    model: str
    prompt: str
    source: str = "api"
    report: str = ""
    citations: list[Citation] = Field(default_factory=_empty_citations)
    tool_calls_summary: list[ToolCallSummary] = Field(default_factory=_empty_tool_calls)
    error: str | None = None
    raw_response_artifact_path: str | None = None
    created_at: datetime | None = None


class ReviewResult(BaseModel):
    verdict: Verdict
    goal_achieved: bool
    score: int = Field(ge=0, le=100)
    rationale: str
    item_assessments: list[ItemAssessment]
    gaps: list[str] = Field(default_factory=list)
    factuality_concerns: list[str]
    source_quality_concerns: list[str]
    freshness_concerns: list[str] = Field(default_factory=list)
    security_concerns: list[str] = Field(default_factory=list)
    next_instructions: str | None = None
    reviewer_confidence: int = Field(ge=0, le=100)
    high_risk_flags: list[str]
    public_web_search_used: bool
    route_rationale: str | None = None


class ReviewRecord(ReviewResult):
    review_no: int
    recommended_route: Verdict
    reviewer_response_id: str | None = None
    report_hash: str | None = None


class CostEvent(BaseModel):
    step: str
    model: str
    response_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    estimated_cost_usd: float = 0.0
    created_at: datetime | None = None


class AuditResponse(BaseModel):
    run_id: UUID
    attempts: list[ResearchAttempt]
    reviews: list[ReviewRecord]
    llm_calls: list[CostEvent]
    objective_contract: ObjectiveContract | None = None
    research_items: list[ResearchItem] = Field(default_factory=_empty_research_items)
    rerun_plans: list[RerunPlan] = Field(default_factory=_empty_rerun_plans)
    verification_queries: list[VerificationQuery] = Field(
        default_factory=_empty_verification_queries
    )
    citations: list[Citation]
    tool_calls: list[ToolCallSummary]
    cost_events: list[CostEvent]
    human_decisions: list[HumanReviewDecision]
    history: list[dict[str, Any]]


class ResearchCheckpointChildFork(BaseModel):
    run_id: UUID
    status: RunStatus
    done_reason: str | None = None
    created_at: datetime


def _empty_checkpoint_child_forks() -> list[ResearchCheckpointChildFork]:
    return []


class ResearchCheckpoint(BaseModel):
    checkpoint_id: UUID
    run_id: UUID
    checkpoint_no: int
    kind: str
    node_anchor: str
    forkable: bool
    dedupe_key: str
    source_attempt_no: int | None = None
    source_review_no: int | None = None
    source_response_id: str | None = None
    report_hash: str | None = None
    snapshot_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    forks: list[ResearchCheckpointChildFork] = Field(
        default_factory=_empty_checkpoint_child_forks
    )
    child_forks: list[ResearchCheckpointChildFork] = Field(
        default_factory=_empty_checkpoint_child_forks
    )


class ResearchCheckpointsResponse(BaseModel):
    run_id: UUID
    checkpoints: list[ResearchCheckpoint]


class ResearchRunLineage(BaseModel):
    run_id: UUID
    root_run_id: UUID
    parent_run_id: UUID
    forked_from_checkpoint_id: UUID
    fork_mode: str
    additional_prompt: str
    confirmed_preview_hash: str
    idempotency_key: str
    source_snapshot_json: dict[str, Any] = Field(default_factory=dict)
    source_report_artifact_path: str | None = None
    created_at: datetime


class ResearchRunLineageResponse(BaseModel):
    run_id: UUID
    root_run_id: UUID | None = None
    parent_run_id: UUID | None = None
    forked_from_checkpoint_id: UUID | None = None
    fork_mode: str | None = None
    additional_prompt: str | None = None
    confirmed_preview_hash: str | None = None
    idempotency_key: str | None = None
    source_snapshot_json: dict[str, Any] = Field(default_factory=dict)
    source_report_artifact_path: str | None = None
    created_at: datetime | None = None
    lineage: ResearchRunLineage | None = None
    child_forks: list[ResearchCheckpointChildFork] = Field(
        default_factory=_empty_checkpoint_child_forks
    )


class ForkPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    additional_prompt: str = Field(min_length=1, max_length=20000)

    _strip_additional_prompt = field_validator("additional_prompt", mode="before")(
        _strip_string
    )


class ForkPreviewResponse(BaseModel):
    run_id: UUID
    checkpoint_id: UUID
    composed_prompt: str
    query_policy: QueryPolicyDecision
    policy_decision: QueryPolicyDecision
    source_prompt_excerpt: str
    source_report_excerpt: str
    warnings: list[str] = Field(default_factory=list)
    preview_hash: str


class ForkSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    additional_prompt: str = Field(min_length=1, max_length=20000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    confirmed_preview_hash: str = Field(min_length=1, max_length=128)

    _strip_additional_prompt = field_validator("additional_prompt", mode="before")(
        _strip_string
    )
    _strip_idempotency_key = field_validator("idempotency_key", mode="before")(
        _strip_string
    )


class ForkSubmitResponse(BaseModel):
    run_id: UUID
    parent_run_id: UUID
    forked_from_checkpoint_id: UUID
    child_run_id: UUID
    status: RunStatus
    done_reason: str | None = None
    needs_human_review: bool
    source_snapshot_json: dict[str, Any] = Field(default_factory=dict)
    lineage: ResearchRunLineage


class ObjectiveContractResponse(BaseModel):
    run_id: UUID
    contract: ObjectiveContract | None


class ResearchItemsResponse(BaseModel):
    run_id: UUID
    items: list[ResearchItem]


class RerunPlansResponse(BaseModel):
    run_id: UUID
    rerun_plans: list[RerunPlan]


class ManualRerunPrompt(BaseModel):
    rerun_id: str
    scope: str
    expected_run_no: int
    prompt: str
    prompt_artifact_path: str
    target_item_ids: list[str] = Field(default_factory=list)
    query_policy: QueryPolicyDecision
    base_report_hash: str | None = None
    created_at: datetime


class CancelResponse(BaseModel):
    run_id: UUID
    status: RunStatus


class HumanReviewResumeRequest(BaseModel):
    action: HumanReviewAction
    comment: str | None = Field(default=None, max_length=10000)


class HumanReviewResumeAPIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: HumanReviewAction
    comment: str | None = Field(default=None, max_length=10000)


class HumanReviewResumeResponse(BaseModel):
    run_id: UUID
    status: RunStatus
    done_reason: str | None
    needs_human_review: bool


class HumanReviewQueueItem(BaseModel):
    run_id: UUID
    status: RunStatus
    done_reason: str | None
    latest_verdict: Verdict | None
    latest_score: int | None
    latest_rationale: str | None
    audit_summary: HumanReviewAuditSummary
    created_at: datetime
    updated_at: datetime


class HumanReviewPayload(BaseModel):
    run_id: UUID
    reason: str
    latest_report: str
    latest_review: ReviewRecord | None
    unresolved_items: list[ResearchItem] = Field(default_factory=_empty_human_review_items)
    allowed_actions: list[HumanReviewAction]
    audit_summary: HumanReviewAuditSummary
    warnings: list[str]
    pending_manual_rerun: ManualRerunPrompt | None = None


class HumanReviewDecision(BaseModel):
    decision_no: int
    action: HumanReviewAction
    comment: str | None = None
    reviewer_id: str | None = None
    created_at: datetime


class ResearchRunRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: UUID
    thread_id: str
    user_prompt: str
    optimized_prompt: str | None
    status: RunStatus
    report: str | None
    final_report: str | None
    done_reason: str | None
    needs_human_review: bool
    pending_deep_research_response_id: str | None
    deep_research_status: str | None
    deep_research_runs: int
    targeted_rerun_runs: int
    full_rerun_runs: int
    llm_patch_runs: int
    verification_runs: int
    total_reviews: int
    no_progress_count: int
    max_targeted_rerun_runs: int
    max_full_rerun_runs: int
    max_llm_patch_runs: int
    max_verification_runs: int
    max_total_iterations: int
    max_total_tool_calls: int
    total_tool_calls: int
    estimated_cost_usd: float
    rerun_execution_mode: RerunExecutionMode = RerunExecutionMode.API
    terminal_status: str | None = None
    review_claim_token: str | None = None
    review_claim_operation: str | None = None
    review_claim_expires_at: datetime | None = None
    deep_research_submitted_at: datetime | None = None
    poll_error_count: int = 0
    poll_claimed_until: datetime | None = None
    poll_claim_owner: str | None = None
    warnings: list[str]
    created_at: datetime
    updated_at: datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


REVIEW_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "verdict",
        "goal_achieved",
        "score",
        "rationale",
        "item_assessments",
        "gaps",
        "factuality_concerns",
        "source_quality_concerns",
        "freshness_concerns",
        "security_concerns",
        "next_instructions",
        "reviewer_confidence",
        "high_risk_flags",
        "public_web_search_used",
        "route_rationale",
    ],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": [item.value for item in Verdict],
        },
        "goal_achieved": {"type": "boolean"},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "rationale": {"type": "string"},
        "item_assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "item_id",
                    "status",
                    "severity",
                    "failure_mode",
                    "failure_mode_confidence",
                    "recommended_action",
                    "evidence_summary",
                    "missing_evidence",
                    "rationale",
                ],
                "properties": {
                    "item_id": {"type": "string"},
                    "status": {"type": "string", "enum": [item.value for item in ItemStatus]},
                    "severity": {"type": "string", "enum": [item.value for item in Severity]},
                    "failure_mode": {
                        "type": "string",
                        "enum": [item.value for item in FailureMode],
                    },
                    "failure_mode_confidence": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "recommended_action": {
                        "type": "string",
                        "enum": [item.value for item in RecommendedAction],
                    },
                    "evidence_summary": {"type": ["string", "null"]},
                    "missing_evidence": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
            },
        },
        "gaps": {"type": "array", "items": {"type": "string"}},
        "factuality_concerns": {"type": "array", "items": {"type": "string"}},
        "source_quality_concerns": {"type": "array", "items": {"type": "string"}},
        "freshness_concerns": {"type": "array", "items": {"type": "string"}},
        "security_concerns": {"type": "array", "items": {"type": "string"}},
        "next_instructions": {"type": ["string", "null"]},
        "reviewer_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "high_risk_flags": {"type": "array", "items": {"type": "string"}},
        "public_web_search_used": {"type": "boolean"},
        "route_rationale": {"type": ["string", "null"]},
    },
}
