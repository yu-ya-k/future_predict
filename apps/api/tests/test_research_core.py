from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from api.config import Settings
from api.research.artifacts import ArtifactStore
from api.research.azure_responses import AzureResponsesClient
from api.research.graph import build_phase_3_graph, build_phase_4_graph
from api.research.poller import ResearchPoller
from api.research.progress import compute_no_progress_count
from api.research.repository import ResearchRepository
from api.research.routing import route_after_review
from api.research.schemas import (
    REVIEW_RESULT_SCHEMA,
    CreateResearchRunRequest,
    HumanReviewAction,
    HumanReviewResumeRequest,
    ResearchRunOptions,
    ReviewRecord,
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
        deep_research_usage: dict[str, int] | None = None,
        review_usage: dict[str, int] | None = None,
        review_gaps: list[str] | None = None,
        review_factuality_concerns: list[str] | None = None,
        review_source_quality_concerns: list[str] | None = None,
        review_next_instructions: str | None = None,
        reviewer_confidence: int = 90,
        high_risk_flags: list[str] | None = None,
        submit_raises: Exception | None = None,
        retrieve_raises: Exception | None = None,
        cancel_raises: Exception | None = None,
    ) -> None:
        self.retrieve_status = retrieve_status
        self.verdict = verdict
        self.verdicts = verdicts or []
        self.deep_research_usage = deep_research_usage or {}
        self.review_usage = review_usage or {}
        self.review_gaps = review_gaps or ["gap"]
        self.review_factuality_concerns = review_factuality_concerns or []
        self.review_source_quality_concerns = review_source_quality_concerns or []
        self.review_next_instructions = review_next_instructions
        self.reviewer_confidence = reviewer_confidence
        self.high_risk_flags = high_risk_flags or []
        self.review_calls = 0
        self.submit_raises = submit_raises
        self.retrieve_raises = retrieve_raises
        self.cancel_raises = cancel_raises
        self.review_web_search_enabled: bool | None = None
        self.deep_research_web_search_enabled: bool | None = None
        self.cancelled: list[str] = []
        self.submitted_prompts: list[str] = []
        self.submitted_max_tool_calls: list[int] = []
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
        self.submitted_max_tool_calls.append(max_tool_calls)
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
                "usage": self.deep_research_usage,
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
            "usage": self.deep_research_usage,
            "error": {"message": "remote failed"},
            "output": [
                {
                    "type": "web_search_call",
                    "status": "failed",
                    "query": "example failed query",
                }
            ],
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
                can_be_fixed_by_llm=verdict == Verdict.NEEDS_LLM_FIX,
                requires_new_external_research=verdict == Verdict.NEEDS_DEEP_RESEARCH,
                reviewer_confidence=self.reviewer_confidence,
                high_risk_flags=self.high_risk_flags,
                public_web_search_used=bool(kwargs["web_search_enabled"]),
            ),
            f"resp_review_{self.review_calls}",
            {
                "id": f"resp_review_{self.review_calls}",
                "status": "completed",
                "usage": self.review_usage,
            },
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
        if self.cancel_raises is not None:
            raise self.cancel_raises
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


def _review_record(
    *,
    review_no: int,
    verdict: Verdict = Verdict.NEEDS_LLM_FIX,
    score: int = 70,
    gaps: list[str] | None = None,
    factuality_concerns: list[str] | None = None,
    source_quality_concerns: list[str] | None = None,
    can_be_fixed_by_llm: bool | None = None,
    requires_new_external_research: bool | None = None,
    report_hash: str | None = None,
) -> ReviewRecord:
    return ReviewRecord(
        review_no=review_no,
        verdict=verdict,
        goal_achieved=False,
        score=score,
        rationale="review rationale",
        gaps=gaps or [],
        factuality_concerns=factuality_concerns or [],
        source_quality_concerns=source_quality_concerns or [],
        next_instructions=None,
        recommended_route=verdict,
        can_be_fixed_by_llm=(
            verdict == Verdict.NEEDS_LLM_FIX
            if can_be_fixed_by_llm is None
            else can_be_fixed_by_llm
        ),
        requires_new_external_research=(
            verdict == Verdict.NEEDS_DEEP_RESEARCH
            if requires_new_external_research is None
            else requires_new_external_research
        ),
        reviewer_confidence=80,
        high_risk_flags=[],
        public_web_search_used=False,
        report_hash=report_hash,
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


def test_route_high_risk_pass_requires_human_review() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": "pass",
                "reviewer_confidence": 95,
                "high_risk_flags": ["legal"],
            },
            "total_reviews": 1,
            "max_total_iterations": 5,
        }
    )

    assert route == "human_review"


def test_route_low_confidence_pass_requires_human_review() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": "pass",
                "reviewer_confidence": 69,
                "high_risk_flags": [],
            },
            "total_reviews": 1,
            "max_total_iterations": 5,
        }
    )

    assert route == "human_review"


def test_route_confident_pass_without_high_risk_finalizes() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": "pass",
                "reviewer_confidence": 70,
                "high_risk_flags": [],
            },
            "total_reviews": 1,
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


def test_route_llm_fix_requires_explicit_llm_fixable_review() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": "needs_llm_fix",
                "can_be_fixed_by_llm": True,
                "requires_new_external_research": False,
            },
            "llm_fix_runs": 0,
            "max_llm_fix_runs": 3,
            "total_reviews": 1,
            "max_total_iterations": 5,
        }
    )

    assert route == "llm_finalize"


@pytest.mark.parametrize(
    "review_flags",
    [
        {"can_be_fixed_by_llm": False, "requires_new_external_research": False},
        {"can_be_fixed_by_llm": True, "requires_new_external_research": True},
    ],
)
def test_route_llm_fix_rejects_contradictory_review_flags(
    review_flags: dict[str, bool],
) -> None:
    route = route_after_review(
        {
            "review": {"verdict": "needs_llm_fix", **review_flags},
            "llm_fix_runs": 0,
            "max_llm_fix_runs": 3,
            "total_reviews": 1,
            "max_total_iterations": 5,
        }
    )

    assert route == "human_review"


def test_no_progress_counts_repeated_factuality_concerns_even_when_gaps_change() -> None:
    previous = _review_record(
        review_no=1,
        score=70,
        gaps=["市場規模の表が不足"],
        factuality_concerns=["2024年の売上数値に根拠がない"],
    )
    current = _review_record(
        review_no=2,
        score=72,
        gaps=["競合比較の粒度が粗い"],
        factuality_concerns=["2024年の売上数値に根拠がない"],
    )

    no_progress = compute_no_progress_count(
        previous_reviews=[previous],
        current_review=current,
        current_no_progress_count=1,
    )

    assert no_progress == 2


def test_no_progress_counts_repeated_source_quality_concerns_even_when_gaps_change() -> None:
    previous = _review_record(
        review_no=1,
        score=68,
        gaps=["採用事例が不足"],
        source_quality_concerns=["公式資料ではなく二次情報に偏っている"],
    )
    current = _review_record(
        review_no=2,
        score=70,
        gaps=["価格情報が不足"],
        source_quality_concerns=["公式資料ではなく二次情報に偏っている"],
    )

    no_progress = compute_no_progress_count(
        previous_reviews=[previous],
        current_review=current,
        current_no_progress_count=0,
    )

    assert no_progress == 1


def test_no_progress_ignores_empty_concern_lists_when_gaps_and_report_change() -> None:
    previous = _review_record(
        review_no=1,
        score=70,
        gaps=["市場規模の表が不足"],
        factuality_concerns=[],
        source_quality_concerns=[],
        report_hash="previous-report",
    )
    current = _review_record(
        review_no=2,
        score=72,
        gaps=["競合比較の粒度が粗い"],
        factuality_concerns=[],
        source_quality_concerns=[],
        report_hash="current-report",
    )

    no_progress = compute_no_progress_count(
        previous_reviews=[previous],
        current_review=current,
        current_no_progress_count=1,
    )

    assert no_progress == 0


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


def test_high_risk_pass_enters_human_review(tmp_path: Path) -> None:
    fake = FakeAzure(verdict=Verdict.PASS, high_risk_flags=["regulated_advice"])
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(CreateResearchRunRequest(user_prompt="市場調査をしてください"))
    needs_human = orchestrator.collect_deep_research(run.id)

    assert needs_human.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert needs_human.done_reason == "review_route_high_risk"
    assert needs_human.final_report is None


def test_low_confidence_pass_enters_human_review(tmp_path: Path) -> None:
    fake = FakeAzure(verdict=Verdict.PASS, reviewer_confidence=69)
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(CreateResearchRunRequest(user_prompt="市場調査をしてください"))
    needs_human = orchestrator.collect_deep_research(run.id)

    assert needs_human.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert needs_human.done_reason == "review_route_low_confidence"
    assert needs_human.final_report is None


def test_review_does_not_overwrite_cancelled_run_after_review_response(
    tmp_path: Path,
) -> None:
    fake = FakeAzure()
    orchestrator = make_orchestrator(tmp_path, fake)
    run = orchestrator.create_run(CreateResearchRunRequest(user_prompt="市場調査をしてください"))
    original_review = fake.review_report

    def review_and_cancel(**kwargs: Any) -> tuple[ReviewResult, str, dict[str, object]]:
        result = original_review(**kwargs)
        orchestrator.cancel_run(run.id)
        return result

    fake.review_report = review_and_cancel  # type: ignore[method-assign]

    cancelled = orchestrator.collect_deep_research(run.id)

    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.done_reason == "cancelled_by_user"
    assert cancelled.final_report is None


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


def test_llm_finalize_does_not_overwrite_cancelled_run_after_response(
    tmp_path: Path,
) -> None:
    fake = FakeAzure(verdicts=[Verdict.NEEDS_LLM_FIX, Verdict.PASS])
    orchestrator = make_orchestrator(tmp_path, fake)
    original_finalize = fake.llm_finalize_report

    run = orchestrator.create_run(CreateResearchRunRequest(user_prompt="技術調査をしてください"))

    def finalize_and_cancel(
        *,
        prompt: str,
        run: object,
    ) -> tuple[str, str, dict[str, object]]:
        result = original_finalize(prompt=prompt, run=run)
        orchestrator.cancel_run(cast(Any, run).id)
        return result

    fake.llm_finalize_report = finalize_and_cancel  # type: ignore[method-assign]

    cancelled = orchestrator.collect_deep_research(run.id)

    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.done_reason == "cancelled_by_user"
    assert cancelled.final_report is None


def test_repeated_needs_llm_fix_stops_at_max_llm_fix_runs(tmp_path: Path) -> None:
    fake = FakeAzure(verdicts=[Verdict.NEEDS_LLM_FIX, Verdict.NEEDS_LLM_FIX])
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="技術調査をしてください",
            options=ResearchRunOptions(
                max_llm_fix_runs=1,
                max_no_progress_rounds=10,
            ),
        )
    )
    guarded = orchestrator.collect_deep_research(run.id)

    assert guarded.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert guarded.done_reason == "review_route_needs_llm_fix"
    assert guarded.llm_fix_runs == 1
    assert guarded.total_reviews == 2
    assert guarded.no_progress_count < guarded.max_no_progress_rounds
    assert len(fake.llm_finalize_prompts) == 1


def test_max_no_progress_rounds_stops_before_next_loop_action(tmp_path: Path) -> None:
    fake = FakeAzure(verdicts=[Verdict.NEEDS_LLM_FIX, Verdict.NEEDS_LLM_FIX])
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="技術調査をしてください",
            options=ResearchRunOptions(
                max_llm_fix_runs=3,
                max_no_progress_rounds=1,
            ),
        )
    )
    guarded = orchestrator.collect_deep_research(run.id)

    assert guarded.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert guarded.done_reason == "review_route_needs_llm_fix"
    assert guarded.no_progress_count == guarded.max_no_progress_rounds
    assert guarded.llm_fix_runs == 1
    assert len(fake.llm_finalize_prompts) == 1


def test_needs_deep_research_submits_rerun_brief(tmp_path: Path) -> None:
    fake = FakeAzure(
        verdict=Verdict.NEEDS_DEEP_RESEARCH,
        review_gaps=["主要競合の価格比較が不足"],
        review_factuality_concerns=["2025年の市場規模が未検証"],
        review_source_quality_concerns=["公式発表ではなくブログに依存"],
        review_next_instructions="一次情報と規制当局資料を優先して再調査してください。",
    )
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(CreateResearchRunRequest(user_prompt="技術調査をしてください"))
    rerun = orchestrator.collect_deep_research(run.id)

    assert rerun.status == RunStatus.WAITING_DEEP_RESEARCH
    assert rerun.deep_research_runs == 2
    assert len(fake.submitted_prompts) == 2
    rerun_brief = fake.submitted_prompts[-1]
    assert "# Review Result" in rerun_brief
    assert "# Gaps To Fix" in rerun_brief
    assert "# Factuality Concerns" in rerun_brief
    assert "# Source Quality Concerns" in rerun_brief
    assert "# Next Instructions" in rerun_brief
    assert "# Rerun Policy" in rerun_brief
    assert "主要競合の価格比較が不足" in rerun_brief
    assert "2025年の市場規模が未検証" in rerun_brief
    assert "公式発表ではなくブログに依存" in rerun_brief
    assert "一次情報と規制当局資料を優先して再調査してください。" in rerun_brief


def test_deep_research_rerun_uses_remaining_tool_call_budget(tmp_path: Path) -> None:
    fake = FakeAzure(verdict=Verdict.NEEDS_DEEP_RESEARCH)
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="技術調査をしてください",
            options=ResearchRunOptions(max_total_tool_calls=2),
        )
    )
    rerun = orchestrator.collect_deep_research(run.id)

    assert rerun.status == RunStatus.WAITING_DEEP_RESEARCH
    assert fake.submitted_max_tool_calls == [2, 1]


def test_over_budget_needs_deep_research_stops_before_rerun_submit(
    tmp_path: Path,
) -> None:
    fake = FakeAzure(
        verdict=Verdict.NEEDS_DEEP_RESEARCH,
        deep_research_usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        review_usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    settings = Settings(
        research_db_path=tmp_path / "research.sqlite3",
        research_artifact_dir=tmp_path / "artifacts",
        research_poller_enabled=False,
        research_deep_research_timeout_seconds=1800,
        research_deep_research_input_cost_per_1m=1.0,
        research_deep_research_output_cost_per_1m=1.0,
        research_reviewer_input_cost_per_1m=1.0,
        research_reviewer_output_cost_per_1m=1.0,
    )
    orchestrator = ResearchOrchestrator(
        settings=settings,
        repository=ResearchRepository(settings.research_db_path),
        artifacts=ArtifactStore(settings.research_artifact_dir),
        azure=cast(AzureResponsesClient, fake),
    )

    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="技術調査をしてください",
            options=ResearchRunOptions(max_cost_usd=0.5),
        )
    )
    guarded = orchestrator.collect_deep_research(run.id)

    assert guarded.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert guarded.done_reason == "review_route_needs_deep_research"
    assert guarded.estimated_cost_usd >= guarded.max_cost_usd
    assert guarded.deep_research_runs == 1
    assert len(fake.submitted_prompts) == 1


def test_estimated_usage_cost_guard_stops_before_llm_fix(tmp_path: Path) -> None:
    fake = FakeAzure(
        verdict=Verdict.NEEDS_LLM_FIX,
        deep_research_usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        review_usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    settings = Settings(
        research_db_path=tmp_path / "research.sqlite3",
        research_artifact_dir=tmp_path / "artifacts",
        research_poller_enabled=False,
        research_deep_research_timeout_seconds=1800,
        research_deep_research_input_cost_per_1m=1.0,
        research_deep_research_output_cost_per_1m=1.0,
        research_reviewer_input_cost_per_1m=1.0,
        research_reviewer_output_cost_per_1m=1.0,
    )
    orchestrator = ResearchOrchestrator(
        settings=settings,
        repository=ResearchRepository(settings.research_db_path),
        artifacts=ArtifactStore(settings.research_artifact_dir),
        azure=cast(AzureResponsesClient, fake),
    )

    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="技術調査をしてください",
            options=ResearchRunOptions(max_cost_usd=0.5),
        )
    )
    guarded = orchestrator.collect_deep_research(run.id)

    assert guarded.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert guarded.done_reason == "review_route_needs_llm_fix"
    assert guarded.estimated_cost_usd >= guarded.max_cost_usd
    assert guarded.llm_fix_runs == 0
    assert not fake.llm_finalize_prompts
    cost_events = orchestrator.repository.get_cost_events(run.id)
    assert {event.step for event in cost_events} >= {"deep_research", "review"}


def test_failed_deep_research_records_attempt_error_and_human_review(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure(retrieve_status="failed"))

    run = orchestrator.create_run(CreateResearchRunRequest(user_prompt="競合調査をしてください"))
    failed = orchestrator.collect_deep_research(run.id)

    assert failed.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert failed.done_reason == "deep_research_failed"
    attempts = orchestrator.repository.get_attempts(run.id)
    assert attempts[-1].status == "failed"
    assert attempts[-1].error is not None


def test_failed_deep_research_records_usage_cost_and_tool_calls(tmp_path: Path) -> None:
    fake = FakeAzure(
        retrieve_status="failed",
        deep_research_usage={"input_tokens": 1_000_000, "output_tokens": 500_000},
    )
    settings = Settings(
        research_db_path=tmp_path / "research.sqlite3",
        research_artifact_dir=tmp_path / "artifacts",
        research_poller_enabled=False,
        research_deep_research_timeout_seconds=1800,
        research_deep_research_input_cost_per_1m=1.0,
        research_deep_research_output_cost_per_1m=2.0,
        research_web_search_cost_per_call=0.25,
    )
    orchestrator = ResearchOrchestrator(
        settings=settings,
        repository=ResearchRepository(settings.research_db_path),
        artifacts=ArtifactStore(settings.research_artifact_dir),
        azure=cast(AzureResponsesClient, fake),
    )

    run = orchestrator.create_run(CreateResearchRunRequest(user_prompt="競合調査をしてください"))
    failed = orchestrator.collect_deep_research(run.id)

    assert failed.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert failed.total_tool_calls == 1
    assert failed.estimated_cost_usd == 2.25
    cost_events = orchestrator.repository.get_cost_events(run.id)
    assert cost_events[-1].step == "deep_research"
    assert cost_events[-1].tool_calls == 1


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


def test_cancel_waiting_run_cancels_remote_response(tmp_path: Path) -> None:
    fake = FakeAzure(retrieve_status="in_progress")
    orchestrator = make_orchestrator(tmp_path, fake)
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )

    cancelled = orchestrator.cancel_run(run.id)
    history = orchestrator.repository.get_history(run.id)

    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.done_reason == "cancelled_by_user"
    assert fake.cancelled == ["resp_deep_1"]
    assert history[-1]["step"] == "cancel_remote_succeeded"


def test_cancel_waiting_run_continues_when_remote_cancel_fails(tmp_path: Path) -> None:
    fake = FakeAzure(
        retrieve_status="in_progress",
        cancel_raises=RuntimeError("remote cancel unavailable"),
    )
    orchestrator = make_orchestrator(tmp_path, fake)
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )

    cancelled = orchestrator.cancel_run(run.id)
    history = orchestrator.repository.get_history(run.id)

    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.done_reason == "cancelled_by_user"
    assert history[-1]["step"] == "cancel_remote_failed"


def test_cancel_collecting_run_cancels_remote_response(tmp_path: Path) -> None:
    fake = FakeAzure(retrieve_status="in_progress")
    orchestrator = make_orchestrator(tmp_path, fake)
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    claimed = orchestrator.repository.claim_deep_research_run(run.id)
    assert claimed is not None

    cancelled = orchestrator.cancel_run(run.id)
    history = orchestrator.repository.get_history(run.id)

    assert cancelled.status == RunStatus.CANCELLED
    assert fake.cancelled == ["resp_deep_1"]
    assert history[-1]["step"] == "cancel_remote_succeeded"


def test_cancel_does_not_overwrite_terminal_status_changed_during_cancel(
    tmp_path: Path,
) -> None:
    fake = FakeAzure(retrieve_status="in_progress")
    orchestrator = make_orchestrator(tmp_path, fake)
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    original_update_if_status = orchestrator.repository.update_run_if_status

    def complete_before_cancel_update(*args: object, **kwargs: object):
        orchestrator.repository.update_run(
            run.id,
            status=RunStatus.COMPLETED,
            done_reason="race_completed",
        )
        return cast(Any, original_update_if_status)(*args, **kwargs)

    orchestrator.repository.update_run_if_status = complete_before_cancel_update  # type: ignore[method-assign]

    after_cancel = orchestrator.cancel_run(run.id)

    assert after_cancel.status == RunStatus.COMPLETED
    assert after_cancel.done_reason == "race_completed"
    assert fake.cancelled == []


def test_cancel_terminal_run_does_not_change_status(tmp_path: Path) -> None:
    fake = FakeAzure()
    orchestrator = make_orchestrator(tmp_path, fake)
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    completed = orchestrator.collect_deep_research(run.id)

    after_cancel = orchestrator.cancel_run(completed.id)
    history = orchestrator.repository.get_history(run.id)

    assert after_cancel.status == RunStatus.COMPLETED
    assert after_cancel.done_reason == "passed_review"
    assert fake.cancelled == []
    assert history[-1]["step"] == "cancel_ignored_terminal_run"


@pytest.mark.parametrize(
    "terminal_status",
    [RunStatus.FAILED, RunStatus.CANCELLED],
)
def test_cancel_other_terminal_runs_does_not_change_status(
    tmp_path: Path,
    terminal_status: RunStatus,
) -> None:
    fake = FakeAzure(retrieve_status="in_progress")
    orchestrator = make_orchestrator(tmp_path, fake)
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    orchestrator.repository.update_run(
        run.id,
        status=terminal_status,
        done_reason=f"already_{terminal_status.value}",
    )

    after_cancel = orchestrator.cancel_run(run.id)

    assert after_cancel.status == terminal_status
    assert after_cancel.done_reason == f"already_{terminal_status.value}"
    assert fake.cancelled == []


def test_timeout_cancels_remote_response_before_human_review(tmp_path: Path) -> None:
    fake = FakeAzure(retrieve_status="in_progress")
    orchestrator = make_orchestrator(tmp_path, fake)
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )

    timed_out = orchestrator.mark_timeout(run.id)
    history = orchestrator.repository.get_history(run.id)

    assert timed_out.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert timed_out.done_reason == "deep_research_timeout"
    assert fake.cancelled == ["resp_deep_1"]
    assert any(event["step"] == "timeout_remote_cancel_succeeded" for event in history)


def test_timeout_continues_when_remote_cancel_fails(tmp_path: Path) -> None:
    fake = FakeAzure(
        retrieve_status="in_progress",
        cancel_raises=RuntimeError("remote cancel unavailable"),
    )
    orchestrator = make_orchestrator(tmp_path, fake)
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )

    timed_out = orchestrator.mark_timeout(run.id)
    history = orchestrator.repository.get_history(run.id)

    assert timed_out.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert timed_out.done_reason == "deep_research_timeout"
    assert any(event["step"] == "timeout_remote_cancel_failed" for event in history)


def test_timeout_does_not_overwrite_cancelled_run_after_remote_cancel(
    tmp_path: Path,
) -> None:
    fake = FakeAzure(retrieve_status="in_progress")
    orchestrator = make_orchestrator(tmp_path, fake)
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    original_cancel = fake.cancel_response

    def cancel_and_mark_local_cancelled(response_id: str) -> dict[str, object]:
        result = original_cancel(response_id)
        orchestrator.repository.update_run(
            run.id,
            status=RunStatus.CANCELLED,
            done_reason="cancelled_by_user",
            needs_human_review=False,
        )
        return result

    fake.cancel_response = cancel_and_mark_local_cancelled  # type: ignore[method-assign]

    timed_out = orchestrator.mark_timeout(run.id)

    assert timed_out.status == RunStatus.CANCELLED
    assert timed_out.done_reason == "cancelled_by_user"


def test_collect_does_not_overwrite_cancelled_run_after_retrieve(tmp_path: Path) -> None:
    fake = FakeAzure()
    orchestrator = make_orchestrator(tmp_path, fake)
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    original_retrieve = fake.retrieve_response

    def retrieve_and_cancel(response_id: str) -> dict[str, object]:
        orchestrator.cancel_run(run.id)
        return original_retrieve(response_id)

    fake.retrieve_response = retrieve_and_cancel  # type: ignore[method-assign]

    collected = orchestrator.collect_deep_research(run.id)
    history = orchestrator.repository.get_history(run.id)

    assert collected.status == RunStatus.CANCELLED
    assert collected.done_reason == "cancelled_by_user"
    assert fake.review_calls == 0
    assert any(
        event["step"] == "deep_research_collect_ignored_terminal_run" for event in history
    )


def test_retrieve_failure_does_not_overwrite_cancelled_run(tmp_path: Path) -> None:
    fake = FakeAzure(retrieve_raises=RuntimeError("temporary 503"))
    orchestrator = make_orchestrator(tmp_path, fake)
    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    original_retrieve = fake.retrieve_response

    def retrieve_and_cancel(response_id: str) -> dict[str, object]:
        orchestrator.cancel_run(run.id)
        return original_retrieve(response_id)

    fake.retrieve_response = retrieve_and_cancel  # type: ignore[method-assign]

    collected = orchestrator.collect_deep_research(run.id)

    assert collected.status == RunStatus.CANCELLED
    assert collected.done_reason == "cancelled_by_user"


@pytest.mark.anyio
async def test_poller_tick_survives_repository_exception(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure())
    poller = ResearchPoller(orchestrator=orchestrator, interval_seconds=0.01)

    def raise_once(*, timeout_seconds: int) -> list[object]:
        raise RuntimeError(f"db unavailable: {timeout_seconds}")

    orchestrator.repository.list_timed_out_runs = raise_once  # type: ignore[method-assign]

    await poller.tick()


@pytest.mark.parametrize(
    ("prompt", "options", "done_reason"),
    [
        (
            "公開情報を調査してください",
            ResearchRunOptions(allow_web_search=False),
            "deep_research_web_search_disabled_by_run_option",
        ),
        (
            "公開情報を調査してください",
            ResearchRunOptions(context_classification="internal"),
            "deep_research_web_search_blocked_internal_context",
        ),
        (
            "公開情報を調査してください",
            ResearchRunOptions(context_classification="confidential"),
            "deep_research_web_search_blocked_confidential_context",
        ),
        (
            "公開情報を調査してください",
            ResearchRunOptions(context_classification="mixed"),
            "deep_research_web_search_blocked_mixed_context",
        ),
        (
            "社外秘: internal strategy を調査してください",
            ResearchRunOptions(),
            "deep_research_web_search_blocked_confidential_detected",
        ),
    ],
)
def test_non_public_or_web_disabled_run_stops_before_deep_research_submit(
    tmp_path: Path,
    prompt: str,
    options: ResearchRunOptions,
    done_reason: str,
) -> None:
    fake = FakeAzure()
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt=prompt,
            options=options,
        )
    )
    history = orchestrator.repository.get_history(run.id)

    assert run.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert run.done_reason == done_reason
    assert run.deep_research_runs == 0
    assert fake.submitted_prompts == []
    assert fake.deep_research_web_search_enabled is None
    assert history[-2]["step"] == "deep_research_submit_blocked"
    assert history[-2]["reason"] == done_reason
    payload = orchestrator.get_human_review_payload(run.id)
    assert HumanReviewAction.APPROVE in payload.allowed_actions
    assert HumanReviewAction.REJECT in payload.allowed_actions
    assert HumanReviewAction.REQUEST_LLM_FIX in payload.allowed_actions
    assert HumanReviewAction.REQUEST_DEEP_RESEARCH not in payload.allowed_actions


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


def test_human_review_queue_and_payload_include_latest_context(tmp_path: Path) -> None:
    fake = FakeAzure(verdict=Verdict.HUMAN_REVIEW)
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    needs_human = orchestrator.collect_deep_research(run.id)

    queue = orchestrator.list_human_reviews()
    payload = orchestrator.get_human_review_payload(needs_human.id)

    assert [item.run_id for item in queue] == [needs_human.id]
    assert queue[0].latest_verdict == Verdict.HUMAN_REVIEW
    assert queue[0].audit_summary.total_reviews == 1
    assert payload.reason == "review_route_human_review"
    assert payload.latest_report == "調査レポート本文"
    assert payload.latest_review is not None
    assert payload.latest_review.verdict == Verdict.HUMAN_REVIEW
    assert HumanReviewAction.APPROVE in payload.allowed_actions


@pytest.mark.parametrize(
    ("limit_fields", "blocked_actions", "allowed_actions"),
    [
        (
            {"estimated_cost_usd": 20.0, "max_cost_usd": 20.0},
            {
                HumanReviewAction.REQUEST_LLM_FIX,
                HumanReviewAction.REQUEST_DEEP_RESEARCH,
            },
            set[HumanReviewAction](),
        ),
        (
            {"total_tool_calls": 120, "max_total_tool_calls": 120},
            {
                HumanReviewAction.REQUEST_LLM_FIX,
                HumanReviewAction.REQUEST_DEEP_RESEARCH,
            },
            set[HumanReviewAction](),
        ),
        (
            {"total_reviews": 5, "max_total_iterations": 5},
            {
                HumanReviewAction.REQUEST_LLM_FIX,
                HumanReviewAction.REQUEST_DEEP_RESEARCH,
            },
            set[HumanReviewAction](),
        ),
        (
            {"no_progress_count": 2, "max_no_progress_rounds": 2},
            {
                HumanReviewAction.REQUEST_LLM_FIX,
                HumanReviewAction.REQUEST_DEEP_RESEARCH,
            },
            set[HumanReviewAction](),
        ),
        (
            {"deep_research_runs": 2, "max_deep_research_runs": 2},
            {HumanReviewAction.REQUEST_DEEP_RESEARCH},
            {HumanReviewAction.REQUEST_LLM_FIX},
        ),
        (
            {"llm_fix_runs": 3, "max_llm_fix_runs": 3},
            {HumanReviewAction.REQUEST_LLM_FIX},
            {HumanReviewAction.REQUEST_DEEP_RESEARCH},
        ),
    ],
)
def test_human_review_payload_filters_blocked_resume_actions(
    tmp_path: Path,
    limit_fields: dict[str, object],
    blocked_actions: set[HumanReviewAction],
    allowed_actions: set[HumanReviewAction],
) -> None:
    fake = FakeAzure(verdict=Verdict.HUMAN_REVIEW)
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    needs_human = orchestrator.collect_deep_research(run.id)
    orchestrator.repository.update_run(needs_human.id, **limit_fields)

    payload = orchestrator.get_human_review_payload(needs_human.id)

    assert HumanReviewAction.APPROVE in payload.allowed_actions
    assert HumanReviewAction.REJECT in payload.allowed_actions
    for action in blocked_actions:
        assert action not in payload.allowed_actions
    for action in allowed_actions:
        assert action in payload.allowed_actions


def test_human_review_payload_rejects_non_waiting_run(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure())

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    completed = orchestrator.collect_deep_research(run.id)

    with pytest.raises(ValueError, match="not waiting for human review"):
        orchestrator.get_human_review_payload(completed.id)


def test_human_resume_records_decision_for_audit(tmp_path: Path) -> None:
    fake = FakeAzure(verdict=Verdict.HUMAN_REVIEW)
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    needs_human = orchestrator.collect_deep_research(run.id)
    orchestrator.resume_run(
        needs_human.id,
        HumanReviewResumeRequest(
            action=HumanReviewAction.APPROVE,
            comment="承認します。",
        ),
        reviewer_id="reviewer-1",
    )

    decisions = orchestrator.repository.get_human_decisions(run.id)

    assert len(decisions) == 1
    assert decisions[0].decision_no == 1
    assert decisions[0].action == HumanReviewAction.APPROVE
    assert decisions[0].comment == "承認します。"
    assert decisions[0].reviewer_id == "reviewer-1"


@pytest.mark.parametrize(
    ("action", "limit_fields", "blocked_reason"),
    [
        (
            HumanReviewAction.REQUEST_DEEP_RESEARCH,
            {"estimated_cost_usd": 20.0, "max_cost_usd": 20.0},
            "max_cost_usd_reached",
        ),
        (
            HumanReviewAction.REQUEST_LLM_FIX,
            {"total_reviews": 5, "max_total_iterations": 5},
            "max_total_iterations_reached",
        ),
        (
            HumanReviewAction.REQUEST_DEEP_RESEARCH,
            {"deep_research_runs": 2, "max_deep_research_runs": 2},
            "max_deep_research_runs_reached",
        ),
        (
            HumanReviewAction.REQUEST_LLM_FIX,
            {"llm_fix_runs": 3, "max_llm_fix_runs": 3},
            "max_llm_fix_runs_reached",
        ),
    ],
)
def test_human_resume_request_actions_do_not_bypass_hard_stops(
    tmp_path: Path,
    action: HumanReviewAction,
    limit_fields: dict[str, object],
    blocked_reason: str,
) -> None:
    fake = FakeAzure(verdict=Verdict.HUMAN_REVIEW)
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    needs_human = orchestrator.collect_deep_research(run.id)
    orchestrator.repository.update_run(needs_human.id, **limit_fields)

    with pytest.raises(ValueError, match=blocked_reason):
        orchestrator.resume_run(
            needs_human.id,
            HumanReviewResumeRequest(action=action, comment="続行してください。"),
            reviewer_id="reviewer-guard",
        )

    still_waiting = orchestrator.repository.get_run(needs_human.id)
    decisions = orchestrator.repository.get_human_decisions(run.id)
    history = orchestrator.repository.get_history(run.id)

    assert still_waiting.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert still_waiting.needs_human_review is True
    assert decisions == []
    assert history[-1]["step"] == "human_review_resume_blocked"
    assert history[-1]["reason"] == blocked_reason


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


def test_human_resume_claim_prevents_second_resume(tmp_path: Path) -> None:
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
            comment="追加調査してください。",
        ),
    )

    with pytest.raises(ValueError, match="not waiting for human review"):
        orchestrator.resume_run(
            needs_human.id,
            HumanReviewResumeRequest(action=HumanReviewAction.APPROVE),
        )

    decisions = orchestrator.repository.get_human_decisions(run.id)
    assert resumed.status == RunStatus.WAITING_DEEP_RESEARCH
    assert len(decisions) == 1
    assert decisions[0].action == HumanReviewAction.REQUEST_DEEP_RESEARCH
    assert len(fake.submitted_prompts) == 2


def test_human_review_eligibility_requires_status_and_flag(tmp_path: Path) -> None:
    fake = FakeAzure(verdict=Verdict.HUMAN_REVIEW)
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    needs_human = orchestrator.collect_deep_research(run.id)
    orchestrator.repository.update_run(
        needs_human.id,
        status=RunStatus.COMPLETED,
        needs_human_review=True,
        done_reason="stale_flag_test",
    )

    assert orchestrator.list_human_reviews() == []
    with pytest.raises(ValueError, match="not waiting for human review"):
        orchestrator.get_human_review_payload(needs_human.id)
    with pytest.raises(ValueError, match="not waiting for human review"):
        orchestrator.resume_run(
            needs_human.id,
            HumanReviewResumeRequest(action=HumanReviewAction.APPROVE),
        )
    assert orchestrator.repository.get_human_decisions(run.id) == []


def test_cancel_human_review_run_clears_human_flag(tmp_path: Path) -> None:
    fake = FakeAzure(verdict=Verdict.HUMAN_REVIEW)
    orchestrator = make_orchestrator(tmp_path, fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報を調査してください")
    )
    needs_human = orchestrator.collect_deep_research(run.id)
    cancelled = orchestrator.cancel_run(needs_human.id)

    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.needs_human_review is False
    assert orchestrator.list_human_reviews() == []
    with pytest.raises(ValueError, match="not waiting for human review"):
        orchestrator.resume_run(
            needs_human.id,
            HumanReviewResumeRequest(action=HumanReviewAction.APPROVE),
        )


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


def test_phase_4_graph_requires_checkpointer() -> None:
    with pytest.raises(ValueError, match="requires a checkpointer"):
        build_phase_4_graph(checkpointer=None)


@pytest.mark.parametrize(
    ("action", "expected_terminal", "expected_visits"),
    [
        (HumanReviewAction.APPROVE, "finalize", []),
        (
            HumanReviewAction.REQUEST_LLM_FIX,
            "finalize",
            ["visited_llm_finalize", "visited_review"],
        ),
        (
            HumanReviewAction.REQUEST_DEEP_RESEARCH,
            "finalize",
            [
                "visited_deep_research_submit",
                "visited_deep_research_collect",
                "visited_review",
            ],
        ),
        (HumanReviewAction.REJECT, "partial_finalize", []),
    ],
)
def test_phase_4_graph_interrupts_and_routes_resume_decision(
    action: HumanReviewAction,
    expected_terminal: str,
    expected_visits: list[str],
) -> None:
    graph = build_phase_4_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": f"phase-4-human-review-{action.value}"}}

    interrupted = graph.invoke(
        {
            "run_id": "run-1",
            "report": "latest report",
            "review": {"rationale": "needs human judgment"},
            "audit_summary": {"total_reviews": 1},
        },
        config=config,
    )
    interrupts = interrupted["__interrupt__"]

    assert interrupts[0].value["reason"] == "needs human judgment"
    assert interrupts[0].value["latest_report"] == "latest report"
    assert "approve" in interrupts[0].value["allowed_actions"]

    resumed = graph.invoke(
        Command(resume={"action": action.value}),
        config=config,
    )

    assert resumed["human_decision"]["action"] == action.value
    assert resumed["graph_terminal"] == expected_terminal
    for visit_key in expected_visits:
        assert resumed[visit_key] is True


def test_phase_3_graph_uses_interrupt_for_human_review_route() -> None:
    graph = build_phase_3_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "phase-3-human-review-interrupt"}}

    interrupted = graph.invoke(
        {
            "run_id": "run-1",
            "report": "latest report",
            "review": {
                "verdict": Verdict.HUMAN_REVIEW.value,
                "rationale": "manual decision required",
            },
        },
        config=config,
    )

    interrupts = interrupted["__interrupt__"]
    assert interrupts[0].value["reason"] == "manual decision required"

    resumed = graph.invoke(
        Command(resume={"action": HumanReviewAction.APPROVE.value}),
        config=config,
    )

    assert resumed["graph_terminal"] == "finalize"
    assert resumed["human_decision"]["action"] == HumanReviewAction.APPROVE.value


@pytest.mark.parametrize(
    "resume_payload",
    [
        {"comment": "missing action"},
        {"action": "typo"},
        [],
    ],
)
def test_phase_4_graph_rejects_malformed_resume_decision(
    resume_payload: object,
) -> None:
    graph = build_phase_4_graph(checkpointer=MemorySaver())
    config = {
        "configurable": {
            "thread_id": f"phase-4-human-review-invalid-{type(resume_payload).__name__}"
        }
    }

    graph.invoke(
        {
            "run_id": "run-1",
            "report": "latest report",
            "review": {"rationale": "needs human judgment"},
        },
        config=config,
    )

    with pytest.raises(ValueError, match="human review action|human_decision"):
        graph.invoke(Command(resume=resume_payload), config=config)
