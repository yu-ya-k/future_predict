from __future__ import annotations

from pathlib import Path

import pytest

from api.research.repository import ResearchRepository
from api.research.schemas import (
    CreateResearchRunRequest,
    FailureMode,
    ItemAssessment,
    ItemStatus,
    RecommendedAction,
    ReviewRecord,
    RunStatus,
    Severity,
    Verdict,
)
from api.research.service import ResearchOrchestrator


def _replace_reviews_table_with_legacy_columns(
    integration_orchestrator: ResearchOrchestrator,
) -> None:
    with integration_orchestrator.repository.connect() as connection:
        connection.executescript(
            """
            ALTER TABLE research_reviews RENAME TO research_reviews_current;
            CREATE TABLE research_reviews (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                review_no INTEGER NOT NULL,
                response_id TEXT,
                model TEXT NOT NULL,
                verdict TEXT NOT NULL,
                score INTEGER NOT NULL,
                goal_achieved INTEGER NOT NULL,
                can_be_fixed_by_llm INTEGER NOT NULL,
                requires_new_external_research INTEGER NOT NULL,
                reviewer_confidence INTEGER NOT NULL,
                review_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            DROP TABLE research_reviews_current;
            """
        )


def _legacy_review_flags(
    integration_orchestrator: ResearchOrchestrator,
    run_id: object,
) -> tuple[int, int]:
    with integration_orchestrator.repository.connect() as connection:
        legacy_row = connection.execute(
            """
            SELECT can_be_fixed_by_llm, requires_new_external_research
            FROM research_reviews
            WHERE run_id = ?
            """,
            (str(run_id),),
        ).fetchone()
    assert legacy_row is not None
    return (
        legacy_row["can_be_fixed_by_llm"],
        legacy_row["requires_new_external_research"],
    )


def _review_record(
    *,
    verdict: Verdict,
    recommended_action: RecommendedAction,
) -> ReviewRecord:
    return ReviewRecord(
        verdict=verdict,
        goal_achieved=False,
        score=70,
        rationale="Needs follow-up.",
        item_assessments=[
            ItemAssessment(
                item_id="item-1",
                status=ItemStatus.PARTIAL,
                severity=Severity.MAJOR,
                failure_mode=FailureMode.NEEDS_TARGETED_VERIFICATION,
                failure_mode_confidence=80,
                recommended_action=recommended_action,
                rationale="Additional evidence is needed.",
            )
        ],
        gaps=["missing evidence"],
        factuality_concerns=["needs source check"],
        source_quality_concerns=[],
        reviewer_confidence=85,
        high_risk_flags=[],
        public_web_search_used=True,
        review_no=1,
        recommended_route=verdict,
        reviewer_response_id="review-response-1",
        report_hash="report-hash-1",
    )


@pytest.mark.integration
def test_research_artifacts_and_sqlite_state_survive_repository_reopen(
    tmp_path: Path,
    integration_orchestrator: ResearchOrchestrator,
) -> None:
    run = integration_orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="公開情報だけを使って分析してください。",
        )
    )
    completed = integration_orchestrator.collect_deep_research(run.id)

    reopened = ResearchRepository(integration_orchestrator.settings.research_db_path)
    reopened_run = reopened.get_run(run.id)
    attempts = reopened.get_attempts(run.id)
    reviews = reopened.get_reviews(run.id)
    contract = reopened.get_objective_contract(run.id)
    items = reopened.get_research_items(run.id)
    citations = reopened.get_citations(run.id)
    tool_calls = reopened.get_tool_calls(run.id)
    history = reopened.get_history(run.id)

    assert completed.status == RunStatus.COMPLETED
    assert reopened_run.status == RunStatus.COMPLETED
    assert reopened_run.final_report == completed.final_report
    assert attempts[-1].created_at is not None
    assert attempts[-1].raw_response_artifact_path is not None
    assert Path(attempts[-1].raw_response_artifact_path).exists()
    assert (tmp_path / "artifacts" / str(run.id) / "reports" / "final_report.md").exists()
    assert contract is not None
    assert len(items) == 5
    assert reviews[0].item_assessments
    assert citations[0].url == "https://example.com/source"
    assert tool_calls[0].query == "example query"
    assert any(event["step"] == "route_after_review" for event in history)


@pytest.mark.integration
def test_review_insert_supports_legacy_sqlite_review_columns(
    integration_orchestrator: ResearchOrchestrator,
) -> None:
    _replace_reviews_table_with_legacy_columns(integration_orchestrator)

    run = integration_orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="公開情報だけを使って分析してください。",
        )
    )
    completed = integration_orchestrator.collect_deep_research(run.id)
    reviews = integration_orchestrator.repository.get_reviews(run.id)

    assert completed.status == RunStatus.COMPLETED
    assert len(reviews) == 1
    assert _legacy_review_flags(integration_orchestrator, run.id) == (0, 0)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("verdict", "recommended_action", "expected_flags"),
    [
        (
            Verdict.NEEDS_LLM_PATCH,
            RecommendedAction.NONE,
            (1, 0),
        ),
        (
            Verdict.PASS,
            RecommendedAction.LLM_PATCH,
            (1, 0),
        ),
        (
            Verdict.NEEDS_VERIFICATION,
            RecommendedAction.NONE,
            (0, 1),
        ),
        (
            Verdict.PASS,
            RecommendedAction.TARGETED_RERUN,
            (0, 1),
        ),
    ],
)
def test_review_insert_sets_legacy_sqlite_review_columns_for_reroutes(
    integration_orchestrator: ResearchOrchestrator,
    verdict: Verdict,
    recommended_action: RecommendedAction,
    expected_flags: tuple[int, int],
) -> None:
    _replace_reviews_table_with_legacy_columns(integration_orchestrator)
    run = integration_orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="公開情報だけを使って分析してください。",
        )
    )

    integration_orchestrator.repository.add_review(
        run_id=run.id,
        review=_review_record(
            verdict=verdict,
            recommended_action=recommended_action,
        ),
        model="test-reviewer",
    )

    assert _legacy_review_flags(integration_orchestrator, run.id) == expected_flags
