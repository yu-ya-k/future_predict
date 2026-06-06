from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.main import create_app
from api.research.dependencies import get_research_orchestrator
from api.research.schemas import (
    FailureMode,
    ItemAssessment,
    ItemStatus,
    RecommendedAction,
    RunStatus,
    Severity,
    Verdict,
)
from api.research.service import ResearchOrchestrator
from research_fakes import IntegrationFakeAzure


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "context_classification",
    ["internal", "confidential", "mixed"],
)
async def test_initial_deep_research_submit_is_blocked_for_non_public_contexts(
    integration_app: FastAPI,
    integration_fake_azure: IntegrationFakeAzure,
    context_classification: str,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=integration_app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "社内情報を含む可能性がある調査をしてください。",
                "context_classification": context_classification,
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]

        status_response = await client.get(f"/research-runs/{run_id}")
        contract_response = await client.get(f"/research-runs/{run_id}/contract")
        items_response = await client.get(f"/research-runs/{run_id}/items")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")
        human_review_response = await client.get(
            f"/research-runs/{run_id}/human-review",
            headers={"X-Reviewer-Id": "integration-policy-test"},
        )

    assert create_response.json()["status"] == RunStatus.NEEDS_HUMAN_REVIEW.value
    assert status_response.json()["status"] == RunStatus.NEEDS_HUMAN_REVIEW.value
    assert status_response.json()["needs_human_review"] is True
    assert status_response.json()["done_reason"] == "deep_research_blocked_by_query_policy"

    assert integration_fake_azure.submit_calls == []
    assert integration_fake_azure.retrieve_calls == []

    assert contract_response.status_code == 200
    assert contract_response.json()["contract"]["contract_frozen"] is True

    assert items_response.status_code == 200
    assert len(items_response.json()["items"]) == 5

    audit = audit_response.json()
    history_steps = [event["step"] for event in audit["history"]]
    assert "deep_research_submit_blocked" in history_steps
    assert "human_review_required" in history_steps
    assert audit["objective_contract"] is not None
    assert len(audit["research_items"]) == 5

    assert human_review_response.status_code == 200
    human_review_payload = human_review_response.json()
    assert human_review_payload["reason"] == "deep_research_blocked_by_query_policy"
    assert human_review_payload["unresolved_items"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_verification_route_blocks_sensitive_queries_and_records_policy_decision(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    sensitive_assessment = ItemAssessment(
        item_id="RI-001",
        status=ItemStatus.PARTIAL,
        severity=Severity.MAJOR,
        failure_mode=FailureMode.NEEDS_TARGETED_VERIFICATION,
        failure_mode_confidence=91,
        recommended_action=RecommendedAction.VERIFY,
        evidence_summary=None,
        missing_evidence=[
            "internal project Codename Atlas launch memo",
            "confidential codename Project Phoenix source",
        ],
        rationale="Verify against internal project Codename Atlas before finalizing.",
    )
    fake = IntegrationFakeAzure(
        verdicts=[Verdict.NEEDS_VERIFICATION],
        item_assessments=[sensitive_assessment],
    )
    orchestrator = integration_orchestrator_factory(fake)

    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "公開情報だけで事実確認してください。",
                "context_classification": "public",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]

        blocked = orchestrator.collect_deep_research(run_id)
        assert blocked.status == RunStatus.NEEDS_HUMAN_REVIEW

        status_response = await client.get(f"/research-runs/{run_id}")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")
        human_review_response = await client.get(
            f"/research-runs/{run_id}/human-review",
            headers={"X-Reviewer-Id": "integration-policy-test"},
        )

    assert fake.submit_calls
    assert fake.verify_prompts == []

    status = status_response.json()
    assert status["status"] == RunStatus.NEEDS_HUMAN_REVIEW.value
    assert status["needs_human_review"] is True
    assert status["done_reason"] == "verification_blocked_by_query_policy"
    assert status["progress"]["verification_runs"] == 0

    audit = audit_response.json()
    history_steps = [event["step"] for event in audit["history"]]
    assert "verification_blocked" in history_steps
    assert "human_review_required" in history_steps

    verification_queries = audit["verification_queries"]
    assert len(verification_queries) == 1
    assert verification_queries[0]["policy_status"] == "blocked"
    assert verification_queries[0]["safe_query"] is None
    assert "internal project Codename Atlas" in verification_queries[0]["raw_query"]

    human_review_payload = human_review_response.json()
    assert human_review_payload["reason"] == "verification_blocked_by_query_policy"
    assert human_review_payload["unresolved_items"]
