from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from api.config import Settings
from api.research.artifacts import ArtifactStore
from api.research.azure_responses import AzureResponsesClient
from api.research.repository import ResearchRepository
from api.research.schemas import ReviewResult, Verdict
from api.research.service import ResearchOrchestrator


class IntegrationFakeAzure:
    deep_research_deployment = "o3-deep-research"
    reviewer_deployment = "gpt-5.5"

    def __init__(
        self,
        *,
        retrieve_statuses: list[str] | None = None,
        verdicts: list[Verdict] | None = None,
        submit_raises: Exception | None = None,
        retrieve_raises: Exception | None = None,
    ) -> None:
        self.retrieve_statuses = retrieve_statuses or ["completed"]
        self.verdicts = verdicts or [Verdict.PASS]
        self.submit_raises = submit_raises
        self.retrieve_raises = retrieve_raises
        self.submit_calls: list[dict[str, object]] = []
        self.retrieve_calls: list[str] = []
        self.review_calls: list[dict[str, Any]] = []
        self.llm_finalize_prompts: list[str] = []
        self.cancelled: list[str] = []

    def submit_deep_research(
        self,
        *,
        prompt: str,
        max_tool_calls: int,
        web_search_enabled: bool | None = None,
        context_classification: object = "public",
        contains_confidential_context: bool | None = None,
        web_search_allowed: bool = True,
    ) -> dict[str, object]:
        if self.submit_raises is not None:
            raise self.submit_raises

        self.submit_calls.append(
            {
                "prompt": prompt,
                "max_tool_calls": max_tool_calls,
                "web_search_enabled": web_search_enabled,
                "context_classification": context_classification,
                "contains_confidential_context": contains_confidential_context,
                "web_search_allowed": web_search_allowed,
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
        response_id = f"resp_review_{len(self.review_calls)}"
        return (
            ReviewResult(
                verdict=verdict,
                goal_achieved=verdict == Verdict.PASS,
                score=92 if verdict == Verdict.PASS else 72,
                rationale=f"review rationale: {verdict.value}",
                gaps=[] if verdict == Verdict.PASS else ["source coverage gap"],
                factuality_concerns=[],
                source_quality_concerns=[],
                next_instructions=None,
                can_be_fixed_by_llm=verdict == Verdict.NEEDS_LLM_FIX,
                requires_new_external_research=verdict == Verdict.NEEDS_DEEP_RESEARCH,
                reviewer_confidence=90,
                high_risk_flags=[],
                public_web_search_used=bool(kwargs["web_search_enabled"]),
            ),
            response_id,
            {"id": response_id, "status": "completed"},
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
            {"id": response_id, "status": "completed"},
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
        research_deep_research_timeout_seconds=1800,
    )
    return ResearchOrchestrator(
        settings=settings,
        repository=ResearchRepository(settings.research_db_path),
        artifacts=ArtifactStore(settings.research_artifact_dir),
        azure=cast(AzureResponsesClient, fake),
    )
