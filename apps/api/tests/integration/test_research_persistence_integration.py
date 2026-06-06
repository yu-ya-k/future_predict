from __future__ import annotations

from pathlib import Path

import pytest

from api.research.repository import ResearchRepository
from api.research.schemas import CreateResearchRunRequest, RunStatus
from api.research.service import ResearchOrchestrator


@pytest.mark.integration
def test_research_artifacts_and_sqlite_state_survive_repository_reopen(
    tmp_path: Path,
    integration_orchestrator: ResearchOrchestrator,
) -> None:
    run = integration_orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報だけを使って分析してください。")
    )
    completed = integration_orchestrator.collect_deep_research(run.id)

    reopened = ResearchRepository(integration_orchestrator.settings.research_db_path)
    reopened_run = reopened.get_run(run.id)
    attempts = reopened.get_attempts(run.id)
    reviews = reopened.get_reviews(run.id)
    citations = reopened.get_citations(run.id)
    tool_calls = reopened.get_tool_calls(run.id)
    history = reopened.get_history(run.id)

    assert completed.status == RunStatus.COMPLETED
    assert reopened_run.status == RunStatus.COMPLETED
    assert reopened_run.final_report == completed.final_report
    assert attempts[-1].raw_response_artifact_path is not None
    assert Path(attempts[-1].raw_response_artifact_path).exists()
    assert (tmp_path / "artifacts" / str(run.id) / "reports" / "final_report.md").exists()
    assert reviews[0].can_be_fixed_by_llm is False
    assert reviews[0].requires_new_external_research is False
    assert citations[0].url == "https://example.com/source"
    assert tool_calls[0].query == "example query"
    assert any(event["step"] == "route_after_review" for event in history)
