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
    if verdict == Verdict.NEEDS_VERIFICATION:
        return RecommendedAction.VERIFY
    if verdict == Verdict.NEEDS_FULL_RERUN:
        return RecommendedAction.FULL_RERUN
    if verdict == Verdict.NEEDS_LLM_PATCH:
        return RecommendedAction.LLM_PATCH
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
            rationale="v2 fake review",
        )
        for item_id in item_ids
    ]


class V2FakeAzure:
    deep_research_deployment = "o3-deep-research"
    reviewer_deployment = "gpt-5.5"

    def __init__(
        self,
        *,
        verdict: Verdict = Verdict.PASS,
        verdicts: list[Verdict] | None = None,
        item_assessments: list[ItemAssessment] | None = None,
        reviewer_confidence: int = 90,
        high_risk_flags: list[str] | None = None,
        security_concerns: list[str] | None = None,
        cancel_raises: Exception | None = None,
    ) -> None:
        self.verdict = verdict
        self.verdicts = verdicts or []
        self.item_assessments = item_assessments
        self.reviewer_confidence = reviewer_confidence
        self.high_risk_flags = high_risk_flags or []
        self.security_concerns = security_concerns or []
        self.cancel_raises = cancel_raises
        self.review_calls = 0
        self.submitted_prompts: list[str] = []
        self.submitted_max_tool_calls: list[int] = []
        self.llm_finalize_prompts: list[str] = []
        self.verify_prompts: list[str] = []
        self.cancelled: list[str] = []

    def submit_deep_research(
        self,
        *,
        prompt: str,
        max_tool_calls: int,
    ) -> dict[str, object]:
        self.submitted_prompts.append(prompt)
        self.submitted_max_tool_calls.append(max_tool_calls)
        return {
            "id": f"resp_deep_{len(self.submitted_prompts)}",
            "status": "queued",
            "output": [],
        }

    def retrieve_response(self, response_id: str) -> dict[str, object]:
        return {
            "id": response_id,
            "status": "completed",
            "output_text": "調査レポート本文",
            "usage": {},
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "調査レポート本文",
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
                    "query": "example query",
                },
            ],
        }

    def review_report(self, **kwargs: Any) -> tuple[ReviewResult, str, dict[str, object]]:
        verdict = (
            self.verdicts[self.review_calls]
            if self.review_calls < len(self.verdicts)
            else self.verdict
        )
        self.review_calls += 1
        item_assessments = (
            self.item_assessments
            if self.item_assessments is not None
            else _default_item_assessments(
                research_items=kwargs.get("research_items"),
                verdict=verdict,
            )
        )
        response_id = f"resp_review_{self.review_calls}"
        return (
            ReviewResult(
                verdict=verdict,
                goal_achieved=verdict == Verdict.PASS,
                score=92 if verdict == Verdict.PASS else 72,
                rationale="review rationale",
                item_assessments=item_assessments,
                factuality_concerns=[],
                source_quality_concerns=[],
                freshness_concerns=[],
                security_concerns=self.security_concerns,
                next_instructions=None,
                reviewer_confidence=self.reviewer_confidence,
                high_risk_flags=self.high_risk_flags,
                public_web_search_used=False,
                route_rationale="v2 fake route rationale",
            ),
            response_id,
            {
                "id": response_id,
                "status": "completed",
                "usage": {},
            },
        )

    def llm_finalize_report(
        self,
        *,
        prompt: str,
        run: object,
    ) -> tuple[str, str, dict[str, object]]:
        self.llm_finalize_prompts.append(prompt)
        response_id = f"resp_llm_patch_{len(self.llm_finalize_prompts)}"
        return (
            "軽微修正済みレポート本文",
            response_id,
            {"id": response_id, "status": "completed", "usage": {}},
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
            {"id": response_id, "status": "completed", "usage": {}},
        )

    def cancel_response(self, response_id: str) -> dict[str, object]:
        if self.cancel_raises is not None:
            raise self.cancel_raises
        self.cancelled.append(response_id)
        return {"id": response_id, "status": "cancelled"}


def make_v2_orchestrator(tmp_path: Path, fake: V2FakeAzure) -> ResearchOrchestrator:
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
