from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.research.poller import ResearchPoller
from api.research.schemas import CreateResearchRunRequest, HumanReviewAction, RunStatus, Verdict
from api.research.service import ResearchOrchestrator
from research_fakes import IntegrationFakeAzure


@pytest.mark.integration
@pytest.mark.anyio
async def test_api_poller_workflow_persists_report_and_audit(
    integration_app: FastAPI,
    integration_orchestrator: ResearchOrchestrator,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=integration_app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "公開情報だけを使って短い市場調査をしてください。",
                "options": {"max_total_tool_calls": 5},
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]

        poller = ResearchPoller(orchestrator=integration_orchestrator, interval_seconds=0.01)
        await poller.tick()

        status_response = await client.get(f"/research-runs/{run_id}")
        report_response = await client.get(f"/research-runs/{run_id}/report")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    assert status_response.status_code == 200
    assert status_response.json()["status"] == RunStatus.COMPLETED.value
    assert status_response.json()["progress"]["latest_verdict"] == Verdict.PASS.value
    assert report_response.status_code == 200
    assert report_response.json()["final_report"].startswith("調査レポート本文")

    audit = audit_response.json()
    assert audit_response.status_code == 200
    assert len(audit["attempts"]) == 2
    assert len(audit["reviews"]) == 1
    assert len(audit["citations"]) == 1
    assert len(audit["tool_calls"]) == 1
    assert "cost_events" in audit
    assert {event["step"] for event in audit["cost_events"]} >= {
        "deep_research",
        "review",
    }
    assert any(event["step"] == "deep_research_collected" for event in audit["history"])


@pytest.mark.integration
def test_llm_fix_path_revises_report_and_re_reviews(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(verdicts=[Verdict.NEEDS_LLM_FIX, Verdict.PASS])
    orchestrator = integration_orchestrator_factory(fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報に基づく技術調査をしてください。")
    )
    completed = orchestrator.collect_deep_research(run.id)

    assert completed.status == RunStatus.COMPLETED
    assert completed.final_report == "軽微修正済みレポート本文"
    assert completed.llm_fix_runs == 1
    assert len(orchestrator.repository.get_reviews(run.id)) == 2
    assert fake.llm_finalize_prompts


@pytest.mark.integration
def test_deep_research_rerun_route_creates_second_pending_attempt(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(verdicts=[Verdict.NEEDS_DEEP_RESEARCH])
    orchestrator = integration_orchestrator_factory(fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="公開情報に基づく競合調査をしてください。")
    )
    rerun = orchestrator.collect_deep_research(run.id)
    attempts = orchestrator.repository.get_attempts(run.id)

    assert rerun.status == RunStatus.WAITING_DEEP_RESEARCH
    assert rerun.deep_research_runs == 2
    assert len(attempts) == 3
    assert fake.submit_calls[-1]["prompt"]
    assert "# Rerun Policy" in str(fake.submit_calls[-1]["prompt"])


@pytest.mark.integration
@pytest.mark.anyio
async def test_human_review_resume_from_api_continues_workflow(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(verdicts=[Verdict.HUMAN_REVIEW, Verdict.PASS])
    orchestrator = integration_orchestrator_factory(fake)

    from api.main import create_app
    from api.research.dependencies import get_research_orchestrator

    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={"user_prompt": "公開情報だけで調査してください。"},
        )
        run_id = create_response.json()["run_id"]
        needs_human = orchestrator.collect_deep_research(run_id)
        assert needs_human.status == RunStatus.NEEDS_HUMAN_REVIEW

        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            headers={"X-Reviewer-Id": "integration-reviewer"},
            json={
                "action": HumanReviewAction.REQUEST_LLM_FIX.value,
                "comment": "章立てだけ整えてください。",
            },
        )
        status_response = await client.get(f"/research-runs/{run_id}")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == RunStatus.COMPLETED.value
    assert status_response.json()["progress"]["total_reviews"] == 2
    assert audit_response.json()["human_decisions"][0]["reviewer_id"] == "integration-reviewer"
    assert "章立てだけ整えてください。" in fake.llm_finalize_prompts[-1]
