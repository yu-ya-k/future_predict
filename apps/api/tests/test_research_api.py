from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import create_app
from api.research.dependencies import get_research_orchestrator
from api.research.schemas import CreateResearchRunRequest, HumanReviewAction, Verdict
from test_research_core import FakeAzure, make_orchestrator

REVIEWER_HEADERS = {"X-Reviewer-Id": "reviewer-1"}


@pytest.mark.anyio
async def test_research_run_api_flow(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={"user_prompt": "市場調査をしてください"},
        )

        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]

        orchestrator.collect_deep_research(run_id)

        status_response = await client.get(f"/research-runs/{run_id}")
        assert status_response.status_code == 200
        status_json = status_response.json()
        assert status_json["status"] == "completed"
        assert status_json["progress"]["latest_verdict"] == "pass"

        report_response = await client.get(f"/research-runs/{run_id}/report")
        assert report_response.status_code == 200
        assert report_response.json()["final_report"] == "調査レポート本文"

        audit_response = await client.get(f"/research-runs/{run_id}/audit")
        assert audit_response.status_code == 200
        audit_json = audit_response.json()
        assert audit_json["attempts"]
        assert audit_json["reviews"]
        assert audit_json["citations"]


@pytest.mark.anyio
async def test_unknown_research_run_returns_404(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/research-runs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


@pytest.mark.anyio
async def test_resume_api_approves_human_review_run(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure(verdict=Verdict.HUMAN_REVIEW))
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={"user_prompt": "市場調査をしてください"},
        )
        run_id = create_response.json()["run_id"]
        orchestrator.collect_deep_research(run_id)

        queue_response = await client.get(
            "/research-runs/human-reviews",
            headers=REVIEWER_HEADERS,
        )
        payload_response = await client.get(
            f"/research-runs/{run_id}/human-review",
            headers=REVIEWER_HEADERS,
        )
        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            headers=REVIEWER_HEADERS,
            json={
                "action": HumanReviewAction.APPROVE.value,
                "comment": "承認します。",
            },
        )
        decisions_response = await client.get(
            f"/research-runs/{run_id}/human-decisions",
            headers=REVIEWER_HEADERS,
        )
        audit_response = await client.get(f"/research-runs/{run_id}/audit")
        queue_after_resume_response = await client.get(
            "/research-runs/human-reviews",
            headers=REVIEWER_HEADERS,
        )
        payload_after_resume_response = await client.get(
            f"/research-runs/{run_id}/human-review",
            headers=REVIEWER_HEADERS,
        )

    assert queue_response.status_code == 200
    assert queue_response.json()[0]["run_id"] == run_id
    assert queue_response.json()[0]["latest_verdict"] == "human_review"
    assert payload_response.status_code == 200
    assert payload_response.json()["latest_report"] == "調査レポート本文"
    assert resume_response.status_code == 200
    payload = resume_response.json()
    assert payload["status"] == "completed"
    assert payload["done_reason"] == "human_approved"
    assert payload["needs_human_review"] is False
    assert decisions_response.status_code == 200
    assert decisions_response.json()[0]["reviewer_id"] == "reviewer-1"
    assert audit_response.status_code == 200
    assert audit_response.json()["human_decisions"][0]["comment"] == "承認します。"
    assert queue_after_resume_response.status_code == 200
    assert queue_after_resume_response.json() == []
    assert payload_after_resume_response.status_code == 409


@pytest.mark.anyio
async def test_human_review_payload_filters_non_executable_deep_research_action(
    tmp_path: Path,
) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "市場調査をしてください",
                "options": {"allow_web_search": False},
            },
        )
        run_id = create_response.json()["run_id"]
        payload_response = await client.get(
            f"/research-runs/{run_id}/human-review",
            headers=REVIEWER_HEADERS,
        )

    assert payload_response.status_code == 200
    allowed_actions = payload_response.json()["allowed_actions"]
    assert HumanReviewAction.REQUEST_DEEP_RESEARCH.value not in allowed_actions
    assert HumanReviewAction.APPROVE.value in allowed_actions
    assert HumanReviewAction.REJECT.value in allowed_actions


@pytest.mark.anyio
async def test_human_review_api_requires_reviewer_identity(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure(verdict=Verdict.HUMAN_REVIEW))
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={"user_prompt": "市場調査をしてください"},
        )
        run_id = create_response.json()["run_id"]
        orchestrator.collect_deep_research(run_id)

        queue_response = await client.get("/research-runs/human-reviews")
        payload_response = await client.get(f"/research-runs/{run_id}/human-review")
        decisions_response = await client.get(f"/research-runs/{run_id}/human-decisions")
        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={"action": HumanReviewAction.APPROVE.value},
        )

    assert queue_response.status_code == 401
    assert payload_response.status_code == 401
    assert decisions_response.status_code == 401
    assert resume_response.status_code == 401


@pytest.mark.anyio
async def test_resume_api_rejects_body_reviewer_id(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure(verdict=Verdict.HUMAN_REVIEW))
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={"user_prompt": "市場調査をしてください"},
        )
        run_id = create_response.json()["run_id"]
        orchestrator.collect_deep_research(run_id)

        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            headers=REVIEWER_HEADERS,
            json={
                "action": HumanReviewAction.APPROVE.value,
                "reviewer_id": "spoofed-reviewer",
            },
        )

    assert resume_response.status_code == 422


@pytest.mark.anyio
async def test_resume_api_rejects_run_not_waiting_for_human(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={"user_prompt": "市場調査をしてください"},
        )
        run_id = create_response.json()["run_id"]
        orchestrator.collect_deep_research(run_id)

        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            headers=REVIEWER_HEADERS,
            json={"action": "approve"},
        )

    assert resume_response.status_code == 409


def test_create_request_keeps_options_defaultable() -> None:
    request = CreateResearchRunRequest(user_prompt="調査してください")

    assert request.options.allow_web_search is True
    assert request.options.context_classification == "public"
