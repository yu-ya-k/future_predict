from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from api.config import Settings
from api.research.artifacts import ArtifactStore
from api.research.azure_responses import AzureResponsesClient
from api.research.poller import ResearchPoller
from api.research.repository import ResearchRepository
from api.research.routing import route_after_review
from api.research.schemas import (
    REVIEW_RESULT_SCHEMA,
    CreateResearchRunRequest,
    HumanReviewAction,
    HumanReviewResumeRequest,
    ResearchRunOptions,
    ReviewResult,
    RunStatus,
    Verdict,
    utc_now,
)
from api.research.security import should_enable_reviewer_web_search
from api.research.service import ResearchOrchestrator


class FakeAzure:
    deep_research_deployment = "o3-deep-research"
    reviewer_deployment = "gpt-5.5"

    def __init__(
        self,
        *,
        retrieve_status: str = "completed",
        verdict: Verdict = Verdict.PASS,
        verdicts: list[Verdict] | None = None,
        submit_raises: Exception | None = None,
        retrieve_raises: Exception | None = None,
    ) -> None:
        self.retrieve_status = retrieve_status
        self.verdict = verdict
        self.verdicts = verdicts or []
        self.review_calls = 0
        self.submit_raises = submit_raises
        self.retrieve_raises = retrieve_raises
        self.review_web_search_enabled: bool | None = None
        self.deep_research_web_search_enabled: bool | None = None
        self.cancelled: list[str] = []
        self.submitted_prompts: list[str] = []
        self.llm_finalize_prompts: list[str] = []

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
        self.deep_research_web_search_enabled = bool(web_search_enabled)
        self.submitted_prompts.append(prompt)
        _ = (
            max_tool_calls,
            web_search_enabled,
            context_classification,
            contains_confidential_context,
            web_search_allowed,
        )
        return {
            "id": f"resp_deep_{len(self.submitted_prompts)}",
            "status": "queued",
            "output": list[object](),
        }

    def retrieve_response(self, response_id: str) -> dict[str, object]:
        if self.retrieve_raises is not None:
            raise self.retrieve_raises
        if self.retrieve_status == "completed":
            return {
                "id": response_id,
                "status": "completed",
                "output_text": "調査レポート本文",
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
        return {
            "id": response_id,
            "status": self.retrieve_status,
            "error": {"message": "remote failed"},
            "output": list[object](),
        }

    def review_report(self, **kwargs: Any) -> tuple[ReviewResult, str, dict[str, object]]:
        self.review_web_search_enabled = bool(kwargs["web_search_enabled"])
        verdict = (
            self.verdicts[self.review_calls]
            if self.review_calls < len(self.verdicts)
            else self.verdict
        )
        self.review_calls += 1
        return (
            ReviewResult(
                verdict=verdict,
                goal_achieved=verdict == Verdict.PASS,
                score=92 if verdict == Verdict.PASS else 72,
                rationale="review rationale",
                gaps=[] if verdict == Verdict.PASS else ["gap"],
                factuality_concerns=[],
                source_quality_concerns=[],
                next_instructions=None,
                can_be_fixed_by_llm=verdict == Verdict.NEEDS_LLM_FIX,
                requires_new_external_research=verdict == Verdict.NEEDS_DEEP_RESEARCH,
                reviewer_confidence=90,
                high_risk_flags=[],
                public_web_search_used=bool(kwargs["web_search_enabled"]),
            ),
            f"resp_review_{self.review_calls}",
            {"id": f"resp_review_{self.review_calls}", "status": "completed"},
        )

    def llm_finalize_report(
        self, *, prompt: str, run: object
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


def make_orchestrator(tmp_path: Path, fake: FakeAzure) -> ResearchOrchestrator:
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


def test_strict_review_schema_is_phase_1_2_compatible() -> None:
    assert REVIEW_RESULT_SCHEMA["type"] == "object"
    assert REVIEW_RESULT_SCHEMA["additionalProperties"] is False
    required = set(REVIEW_RESULT_SCHEMA["required"])
    assert "can_be_fixed_by_llm" in required
    assert "requires_new_external_research" in required
    next_instructions = REVIEW_RESULT_SCHEMA["properties"]["next_instructions"]
    assert next_instructions == {"type": ["string", "null"]}


def test_route_pass_wins_over_hard_stop() -> None:
    route = route_after_review(
        {
            "review": {"verdict": "pass"},
            "total_reviews": 5,
            "max_total_iterations": 5,
        }
    )

    assert route == "finalize"


def test_route_deep_research_can_downgrade_to_llm_fix_after_deep_limit() -> None:
    route = route_after_review(
        {
            "review": {"verdict": "needs_deep_research", "can_be_fixed_by_llm": True},
            "deep_research_runs": 2,
            "max_deep_research_runs": 2,
            "llm_fix_runs": 0,
            "max_llm_fix_runs": 3,
            "total_reviews": 1,
            "max_total_iterations": 5,
        }
    )

    assert route == "llm_finalize"


def test_confidential_context_disables_reviewer_web_search() -> None:
    assert not should_enable_reviewer_web_search(
        context_classification="confidential",
        contains_confidential_context=False,
        web_search_allowed=True,
    )
    assert not should_enable_reviewer_web_search(
        context_classification="public",
        contains_confidential_context=True,
        web_search_allowed=True,
    )
    assert should_enable_reviewer_web_search(
        context_classification="public",
        contains_confidential_context=False,
        web_search_allowed=True,
    )


def test_completed_deep_research_is_reviewed_and_finalized(tmp_path: Path) -> None:
    fake = FakeAzure()
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(CreateResearchRunRequest(user_prompt="市場調査をしてください"))
    assert run.status == RunStatus.WAITING_DEEP_RESEARCH

    completed = orchestrator.collect_deep_research(run.id)

    assert completed.status == RunStatus.COMPLETED
    assert completed.done_reason == "passed_review"
    assert completed.final_report == "調査レポート本文"
    assert fake.review_web_search_enabled is True
    assert len(orchestrator.repository.get_citations(run.id)) == 1
    assert len(orchestrator.repository.get_tool_calls(run.id)) == 1
    with orchestrator.repository.connect() as connection:
        citation_row = connection.execute(
            "SELECT attempt_id FROM research_citations WHERE run_id = ?",
            (str(run.id),),
        ).fetchone()
    assert citation_row is not None
    assert citation_row["attempt_id"] is not None
    reviews = orchestrator.repository.get_reviews(run.id)
    assert reviews[0].can_be_fixed_by_llm is False
    assert reviews[0].requires_new_external_research is False


def test_needs_llm_fix_runs_finalize_and_returns_to_review(tmp_path: Path) -> None:
    fake = FakeAzure(verdicts=[Verdict.NEEDS_LLM_FIX, Verdict.PASS])
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(CreateResearchRunRequest(user_prompt="技術調査をしてください"))
    reviewed = orchestrator.collect_deep_research(run.id)

    assert reviewed.status == RunStatus.COMPLETED
    assert reviewed.done_reason == "passed_review"
    assert reviewed.final_report == "軽微修正済みレポート本文"
    assert reviewed.llm_fix_runs == 1
    assert fake.llm_finalize_prompts
    assert len(orchestrator.repository.get_reviews(run.id)) == 2


def test_needs_deep_research_submits_rerun_brief(tmp_path: Path) -> None:
    fake = FakeAzure(verdict=Verdict.NEEDS_DEEP_RESEARCH)
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(CreateResearchRunRequest(user_prompt="技術調査をしてください"))
    rerun = orchestrator.collect_deep_research(run.id)

    assert rerun.status == RunStatus.WAITING_DEEP_RESEARCH
    assert rerun.deep_research_runs == 2
    assert len(fake.submitted_prompts) == 2
    assert "# Rerun Policy" in fake.submitted_prompts[-1]
    assert "gap" in fake.submitted_prompts[-1]


def test_failed_deep_research_records_attempt_error_and_human_review(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure(retrieve_status="failed"))

    run = orchestrator.create_run(CreateResearchRunRequest(user_prompt="競合調査をしてください"))
    failed = orchestrator.collect_deep_research(run.id)

    assert failed.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert failed.done_reason == "deep_research_failed"
    attempts = orchestrator.repository.get_attempts(run.id)
    assert attempts[-1].status == "failed"
    assert attempts[-1].error is not None


@pytest.mark.anyio
async def test_poller_collects_waiting_runs(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure())
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    poller = ResearchPoller(orchestrator=orchestrator, interval_seconds=0.01)

    await poller.tick()

    completed = orchestrator.repository.get_run(run.id)
    assert completed.status == RunStatus.COMPLETED


def test_deep_research_timeout_uses_attempt_created_at_not_run_updated_at(
    tmp_path: Path,
) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure(retrieve_status="in_progress"))
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    old = (utc_now() - timedelta(seconds=3600)).isoformat()
    now = utc_now().isoformat()
    with orchestrator.repository.connect() as connection:
        connection.execute(
            "UPDATE research_attempts SET created_at = ? WHERE run_id = ?",
            (old, str(run.id)),
        )
        connection.execute(
            "UPDATE research_runs SET updated_at = ? WHERE id = ?",
            (now, str(run.id)),
        )

    timed_out = orchestrator.repository.list_timed_out_runs(timeout_seconds=1800)

    assert [item.id for item in timed_out] == [run.id]


def test_waiting_run_claim_is_single_winner(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure(retrieve_status="in_progress"))
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )

    first_claim = orchestrator.repository.claim_deep_research_run(run.id)
    second_claim = orchestrator.repository.claim_deep_research_run(run.id)

    assert first_claim is not None
    assert first_claim.status == RunStatus.COLLECTING
    assert second_claim is None


def test_retrieve_failure_returns_to_waiting_for_repoll(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(
        tmp_path,
        FakeAzure(retrieve_raises=RuntimeError("temporary 503")),
    )
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )

    retried = orchestrator.collect_deep_research(run.id)

    assert retried.status == RunStatus.WAITING_DEEP_RESEARCH
    assert retried.needs_human_review is False
    assert retried.done_reason is None
    assert retried.deep_research_status == "retrieve_retryable_error"
    history = orchestrator.repository.get_history(run.id)
    assert history[-1]["step"] == "deep_research_retrieve_retryable_error"


@pytest.mark.anyio
async def test_poller_tick_survives_repository_exception(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure())
    poller = ResearchPoller(orchestrator=orchestrator, interval_seconds=0.01)

    def raise_once(*, timeout_seconds: int) -> list[object]:
        raise RuntimeError(f"db unavailable: {timeout_seconds}")

    orchestrator.repository.list_timed_out_runs = raise_once  # type: ignore[method-assign]

    await poller.tick()


def test_confidential_run_records_no_public_review_search(tmp_path: Path) -> None:
    fake = FakeAzure()
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="社外秘: internal strategy を調査してください",
            options=ResearchRunOptions(context_classification="confidential"),
        )
    )
    orchestrator.collect_deep_research(run.id)

    assert fake.review_web_search_enabled is False
    assert fake.deep_research_web_search_enabled is False


def test_human_resume_approve_finalizes_report(tmp_path: Path) -> None:
    fake = FakeAzure(verdict=Verdict.HUMAN_REVIEW)
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    needs_human = orchestrator.collect_deep_research(run.id)
    resumed = orchestrator.resume_run(
        needs_human.id,
        HumanReviewResumeRequest(action=HumanReviewAction.APPROVE),
    )

    assert resumed.status == RunStatus.COMPLETED
    assert resumed.done_reason == "human_approved"
    assert resumed.final_report == "調査レポート本文"


def test_human_resume_request_llm_fix_continues_workflow(tmp_path: Path) -> None:
    fake = FakeAzure(verdicts=[Verdict.HUMAN_REVIEW, Verdict.PASS])
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    needs_human = orchestrator.collect_deep_research(run.id)
    resumed = orchestrator.resume_run(
        needs_human.id,
        HumanReviewResumeRequest(
            action=HumanReviewAction.REQUEST_LLM_FIX,
            comment="章立てを直してください。",
        ),
    )

    assert resumed.status == RunStatus.COMPLETED
    assert resumed.llm_fix_runs == 1
    assert "章立てを直してください。" in fake.llm_finalize_prompts[-1]


def test_human_resume_request_deep_research_submits_rerun(tmp_path: Path) -> None:
    fake = FakeAzure(verdict=Verdict.HUMAN_REVIEW)
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    needs_human = orchestrator.collect_deep_research(run.id)
    resumed = orchestrator.resume_run(
        needs_human.id,
        HumanReviewResumeRequest(
            action=HumanReviewAction.REQUEST_DEEP_RESEARCH,
            comment="一次情報を追加してください。",
        ),
    )

    assert resumed.status == RunStatus.WAITING_DEEP_RESEARCH
    assert resumed.deep_research_runs == 2
    assert "一次情報を追加してください。" in fake.submitted_prompts[-1]


def test_human_resume_reject_stops_run(tmp_path: Path) -> None:
    fake = FakeAzure(verdict=Verdict.HUMAN_REVIEW)
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    needs_human = orchestrator.collect_deep_research(run.id)
    resumed = orchestrator.resume_run(
        needs_human.id,
        HumanReviewResumeRequest(action=HumanReviewAction.REJECT, comment="不採用"),
    )

    assert resumed.status == RunStatus.FAILED
    assert resumed.done_reason == "human_rejected"
    assert resumed.needs_human_review is False
