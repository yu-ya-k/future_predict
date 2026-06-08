from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from api.config import Settings
from api.research.artifacts import ArtifactStore
from api.research.azure_responses import AzureResponsesClient
from api.research.repository import ResearchRepository
from api.research.schemas import (
    FailureMode,
    ItemAssessment,
    ItemStatus,
    RecommendedAction,
    ReviewResult,
    Severity,
    Verdict,
)
from api.research.service import ResearchOrchestrator


def _default_recommended_action(verdict: Verdict) -> RecommendedAction:
    if verdict == Verdict.PASS:
        return RecommendedAction.NONE
    if verdict == Verdict.NEEDS_LLM_PATCH:
        return RecommendedAction.LLM_PATCH
    if verdict == Verdict.NEEDS_VERIFICATION:
        return RecommendedAction.VERIFY
    if verdict == Verdict.NEEDS_FULL_RERUN:
        return RecommendedAction.FULL_RERUN
    return RecommendedAction.TARGETED_RERUN


def _default_item_assessments(
    *,
    research_items: list[dict[str, Any]] | None,
    verdict: Verdict,
) -> list[ItemAssessment]:
    item_ids = [
        str(item["item_id"])
        for item in research_items or []
        if isinstance(item.get("item_id"), str) and item["item_id"]
    ]
    if not item_ids:
        item_ids = ["RI-001"]
    return [
        ItemAssessment(
            item_id=item_id,
            status=ItemStatus.ANSWERED if verdict == Verdict.PASS else ItemStatus.PARTIAL,
            severity=Severity.MAJOR,
            failure_mode=(
                FailureMode.NONE
                if verdict == Verdict.PASS
                else FailureMode.NEEDS_DIFFERENT_SOURCES
            ),
            failure_mode_confidence=90,
            recommended_action=_default_recommended_action(verdict),
            evidence_summary="covered" if verdict == Verdict.PASS else None,
            missing_evidence=[] if verdict == Verdict.PASS else ["official source"],
            rationale="integration fake assessment",
        )
        for item_id in item_ids
    ]


class IntegrationFakeAzure:
    deep_research_deployment = "o3-deep-research"
    reviewer_deployment = "gpt-5.5"

    def __init__(
        self,
        *,
        retrieve_statuses: list[str] | None = None,
        verdicts: list[Verdict] | None = None,
        deep_research_usage: dict[str, int] | None = None,
        review_usage: dict[str, int] | None = None,
        llm_finalize_usage: dict[str, int] | None = None,
        item_assessments: list[ItemAssessment] | None = None,
        review_gaps: list[str] | None = None,
        review_factuality_concerns: list[str] | None = None,
        review_source_quality_concerns: list[str] | None = None,
        review_next_instructions: str | None = None,
        submit_raises: Exception | None = None,
        retrieve_raises: Exception | None = None,
    ) -> None:
        self.retrieve_statuses = retrieve_statuses or ["completed"]
        self.verdicts = verdicts or [Verdict.PASS]
        self.deep_research_usage = deep_research_usage or {}
        self.review_usage = review_usage or {}
        self.llm_finalize_usage = llm_finalize_usage or {}
        self.review_gaps = review_gaps or ["source coverage gap"]
        self.item_assessments = item_assessments
        self.review_factuality_concerns = review_factuality_concerns or []
        self.review_source_quality_concerns = review_source_quality_concerns or []
        self.review_next_instructions = review_next_instructions
        self.submit_raises = submit_raises
        self.retrieve_raises = retrieve_raises
        self.submit_calls: list[dict[str, object]] = []
        self.retrieve_calls: list[str] = []
        self.review_calls: list[dict[str, Any]] = []
        self.llm_finalize_prompts: list[str] = []
        self.verify_prompts: list[str] = []
        self.cancelled: list[str] = []

    def submit_deep_research(
        self,
        *,
        prompt: str,
        max_tool_calls: int,
        tool_profile: str = "public",
        background: bool = True,
        policy_decision_id: str | None = None,
        **_: object,
    ) -> dict[str, object]:
        if self.submit_raises is not None:
            raise self.submit_raises

        self.submit_calls.append(
            {
                "prompt": prompt,
                "max_tool_calls": max_tool_calls,
                "tool_profile": tool_profile,
                "background": background,
                "policy_decision_id": policy_decision_id,
            }
        )
        return {
            "id": f"resp_deep_{len(self.submit_calls)}",
            "status": "queued",
            "output": [],
        }

    def retrieve_response(self, response_id: str) -> dict[str, object]:
        if self.retrieve_raises is not None:
            raise self.retrieve_raises

        self.retrieve_calls.append(response_id)
        status_index = min(len(self.retrieve_calls) - 1, len(self.retrieve_statuses) - 1)
        status = self.retrieve_statuses[status_index]
        if status == "completed":
            return {
                "id": response_id,
                "status": "completed",
                "output_text": f"調査レポート本文 {response_id}",
                "usage": self.deep_research_usage,
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": f"調査レポート本文 {response_id}",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://example.com/source",
                                        "title": "Example Source",
                                        "start_index": 0,
                                        "end_index": 4,
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {"query": "example query"},
                    },
                ],
            }

        return {
            "id": response_id,
            "status": status,
            "error": {"message": f"remote status: {status}"},
            "output": [],
        }

    def review_report(self, **kwargs: Any) -> tuple[ReviewResult, str, dict[str, object]]:
        self.review_calls.append(kwargs)
        verdict_index = min(len(self.review_calls) - 1, len(self.verdicts) - 1)
        verdict = self.verdicts[verdict_index]
        item_assessments = (
            self.item_assessments
            if self.item_assessments is not None
            else _default_item_assessments(
                research_items=kwargs.get("research_items"),
                verdict=verdict,
            )
        )
        response_id = f"resp_review_{len(self.review_calls)}"
        return (
            ReviewResult(
                verdict=verdict,
                goal_achieved=verdict == Verdict.PASS,
                score=92 if verdict == Verdict.PASS else 72,
                rationale=f"review rationale: {verdict.value}",
                item_assessments=item_assessments,
                gaps=[] if verdict == Verdict.PASS else self.review_gaps,
                factuality_concerns=(
                    [] if verdict == Verdict.PASS else self.review_factuality_concerns
                ),
                source_quality_concerns=(
                    [] if verdict == Verdict.PASS else self.review_source_quality_concerns
                ),
                next_instructions=(
                    None if verdict == Verdict.PASS else self.review_next_instructions
                ),
                freshness_concerns=[],
                security_concerns=[],
                reviewer_confidence=90,
                high_risk_flags=[],
                public_web_search_used=False,
                route_rationale=f"route {verdict.value}",
            ),
            response_id,
            {"id": response_id, "status": "completed", "usage": self.review_usage},
        )

    def llm_finalize_report(
        self,
        *,
        prompt: str,
        run: object,
    ) -> tuple[str, str, dict[str, object]]:
        self.llm_finalize_prompts.append(prompt)
        response_id = f"resp_llm_fix_{len(self.llm_finalize_prompts)}"
        return (
            "軽微修正済みレポート本文",
            response_id,
            {"id": response_id, "status": "completed", "usage": self.llm_finalize_usage},
        )

    def verify_report(
        self,
        *,
        prompt: str,
        run: object,
    ) -> tuple[str, str, dict[str, object]]:
        self.verify_prompts.append(prompt)
        response_id = f"resp_verify_{len(self.verify_prompts)}"
        return (
            "検証済みメモ",
            response_id,
            {"id": response_id, "status": "completed", "usage": self.review_usage},
        )

    def cancel_response(self, response_id: str) -> dict[str, object]:
        self.cancelled.append(response_id)
        return {"id": response_id, "status": "cancelled"}


def make_integration_orchestrator(
    tmp_path: Path,
    fake: IntegrationFakeAzure,
) -> ResearchOrchestrator:
    settings = Settings(
        research_db_path=tmp_path / "research.sqlite3",
        research_artifact_dir=tmp_path / "artifacts",
        research_poller_enabled=False,
        research_deep_research_timeout_seconds=7200,
    )
    return ResearchOrchestrator(
        settings=settings,
        repository=ResearchRepository(settings.research_db_path),
        artifacts=ArtifactStore(settings.research_artifact_dir),
        azure=cast(AzureResponsesClient, fake),
    )
