from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
    NEEDS_LLM_FIX = "needs_llm_fix"
    NEEDS_DEEP_RESEARCH = "needs_deep_research"
    HUMAN_REVIEW = "human_review"


class HumanReviewAction(StrEnum):
    APPROVE = "approve"
    REQUEST_LLM_FIX = "request_llm_fix"
    REQUEST_DEEP_RESEARCH = "request_deep_research"
    REJECT = "reject"


ContextClassification = Literal["public", "internal", "confidential", "mixed"]


class ResearchRunOptions(BaseModel):
    max_deep_research_runs: int | None = Field(default=None, ge=1, le=5)
    max_llm_fix_runs: int | None = Field(default=None, ge=0, le=10)
    max_total_iterations: int | None = Field(default=None, ge=1, le=20)
    max_no_progress_rounds: int | None = Field(default=None, ge=1, le=10)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_total_tool_calls: int | None = Field(default=None, ge=1)
    allow_web_search: bool = True
    context_classification: ContextClassification = "public"


class CreateResearchRunRequest(BaseModel):
    user_prompt: str = Field(min_length=1, max_length=50000)
    options: ResearchRunOptions = Field(default_factory=ResearchRunOptions)


class CreateResearchRunResponse(BaseModel):
    run_id: UUID
    thread_id: str
    status: RunStatus
    created_at: datetime


class RunProgress(BaseModel):
    deep_research_runs: int
    llm_fix_runs: int
    total_reviews: int
    latest_verdict: Verdict | None
    latest_score: int | None


class ResearchRunStatusResponse(BaseModel):
    run_id: UUID
    status: RunStatus
    done_reason: str | None
    needs_human_review: bool
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


def _empty_citations() -> list[Citation]:
    return []


def _empty_tool_calls() -> list[ToolCallSummary]:
    return []


class ResearchAttempt(BaseModel):
    run_no: int
    response_id: str | None = None
    status: str
    model: str
    prompt: str
    report: str = ""
    citations: list[Citation] = Field(default_factory=_empty_citations)
    tool_calls_summary: list[ToolCallSummary] = Field(default_factory=_empty_tool_calls)
    error: str | None = None
    raw_response_artifact_path: str | None = None


class ReviewResult(BaseModel):
    verdict: Verdict
    goal_achieved: bool
    score: int = Field(ge=0, le=100)
    rationale: str
    gaps: list[str]
    factuality_concerns: list[str]
    source_quality_concerns: list[str]
    next_instructions: str | None
    can_be_fixed_by_llm: bool
    requires_new_external_research: bool
    reviewer_confidence: int = Field(ge=0, le=100)
    high_risk_flags: list[str]
    public_web_search_used: bool


class ReviewRecord(ReviewResult):
    review_no: int
    recommended_route: Verdict
    reviewer_response_id: str | None = None


class AuditResponse(BaseModel):
    run_id: UUID
    attempts: list[ResearchAttempt]
    reviews: list[ReviewRecord]
    citations: list[Citation]
    tool_calls: list[ToolCallSummary]
    history: list[dict[str, Any]]


class CancelResponse(BaseModel):
    run_id: UUID
    status: RunStatus


class HumanReviewResumeRequest(BaseModel):
    action: HumanReviewAction
    comment: str | None = Field(default=None, max_length=10000)


class HumanReviewResumeResponse(BaseModel):
    run_id: UUID
    status: RunStatus
    done_reason: str | None
    needs_human_review: bool


class ResearchRunRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: UUID
    thread_id: str
    user_prompt: str
    optimized_prompt: str | None
    context_classification: ContextClassification
    web_search_allowed: bool
    contains_confidential_context: bool
    status: RunStatus
    report: str | None
    final_report: str | None
    done_reason: str | None
    needs_human_review: bool
    pending_deep_research_response_id: str | None
    deep_research_status: str | None
    deep_research_runs: int
    llm_fix_runs: int
    total_reviews: int
    no_progress_count: int
    max_deep_research_runs: int
    max_llm_fix_runs: int
    max_total_iterations: int
    max_no_progress_rounds: int
    max_total_tool_calls: int
    max_cost_usd: float
    total_tool_calls: int
    estimated_cost_usd: float
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
        "gaps",
        "factuality_concerns",
        "source_quality_concerns",
        "next_instructions",
        "can_be_fixed_by_llm",
        "requires_new_external_research",
        "reviewer_confidence",
        "high_risk_flags",
        "public_web_search_used",
    ],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": [
                Verdict.PASS.value,
                Verdict.NEEDS_LLM_FIX.value,
                Verdict.NEEDS_DEEP_RESEARCH.value,
                Verdict.HUMAN_REVIEW.value,
            ],
        },
        "goal_achieved": {"type": "boolean"},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "rationale": {"type": "string"},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "factuality_concerns": {"type": "array", "items": {"type": "string"}},
        "source_quality_concerns": {"type": "array", "items": {"type": "string"}},
        "next_instructions": {"type": ["string", "null"]},
        "can_be_fixed_by_llm": {"type": "boolean"},
        "requires_new_external_research": {"type": "boolean"},
        "reviewer_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "high_risk_flags": {"type": "array", "items": {"type": "string"}},
        "public_web_search_used": {"type": "boolean"},
    },
}
