from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import create_app
from api.research.azure_responses import ReviewRequestTimeout
from api.research.dependencies import get_research_orchestrator
from api.research.schemas import (
    CreateResearchRunRequest,
    HumanReviewAction,
    ReviewResult,
    Verdict,
)
from test_research_core import FakeAzure, make_orchestrator


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

        initial_status_response = await client.get(f"/research-runs/{run_id}")
        assert initial_status_response.status_code == 200
        initial_status_json = initial_status_response.json()
        assert initial_status_json["status"] == "waiting_deep_research"
        assert initial_status_json["deep_research_submitted_at"] is not None

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
async def test_delete_research_run_removes_api_access(tmp_path: Path) -> None:
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

        delete_response = await client.delete(f"/research-runs/{run_id}")
        status_response = await client.get(f"/research-runs/{run_id}")
        report_response = await client.get(f"/research-runs/{run_id}/report")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    assert delete_response.status_code == 204
    assert status_response.status_code == 404
    assert report_response.status_code == 404
    assert audit_response.status_code == 404


@pytest.mark.anyio
async def test_delete_unknown_research_run_returns_404(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.delete(
            "/research-runs/00000000-0000-0000-0000-000000000000"
        )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_delete_waiting_run_returns_409_when_remote_cancel_fails(
    tmp_path: Path,
) -> None:
    orchestrator = make_orchestrator(
        tmp_path,
        FakeAzure(cancel_raises=RuntimeError("remote cancel unavailable")),
    )
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

        response = await client.delete(f"/research-runs/{run_id}")
        status_response = await client.get(f"/research-runs/{run_id}")

    assert response.status_code == 409
    assert "Remote Deep Research cancel failed" in response.json()["detail"]
    assert status_response.status_code == 200


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
        )
        payload_response = await client.get(
            f"/research-runs/{run_id}/human-review",
        )
        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={
                "action": HumanReviewAction.APPROVE.value,
                "comment": "承認します。",
            },
        )
        decisions_response = await client.get(
            f"/research-runs/{run_id}/human-decisions",
        )
        audit_response = await client.get(f"/research-runs/{run_id}/audit")
        queue_after_resume_response = await client.get(
            "/research-runs/human-reviews",
        )
        payload_after_resume_response = await client.get(
            f"/research-runs/{run_id}/human-review",
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
    assert decisions_response.json()[0]["reviewer_id"] is None
    assert audit_response.status_code == 200
    assert audit_response.json()["human_decisions"][0]["comment"] == "承認します。"
    assert queue_after_resume_response.status_code == 200
    assert queue_after_resume_response.json() == []
    assert payload_after_resume_response.status_code == 409


@pytest.mark.anyio
async def test_resume_api_can_retry_review_after_review_timeout(tmp_path: Path) -> None:
    class TimeoutThenPassAzure(FakeAzure):
        def review_report(self, **kwargs: Any) -> tuple[ReviewResult, str, dict[str, object]]:
            if self.review_calls == 0:
                self.review_calls += 1
                raise ReviewRequestTimeout("review timed out")
            return super().review_report(**kwargs)

    fake = TimeoutThenPassAzure()
    orchestrator = make_orchestrator(tmp_path, fake)
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

        payload_response = await client.get(f"/research-runs/{run_id}/human-review")
        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={
                "action": HumanReviewAction.REQUEST_REVIEW.value,
                "comment": "レビューを再実行してください。",
            },
        )

    assert payload_response.status_code == 200
    assert HumanReviewAction.REQUEST_REVIEW.value in payload_response.json()["allowed_actions"]
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "completed"
    assert resume_response.json()["done_reason"] == "passed_review"
    assert fake.review_calls == 2


@pytest.mark.anyio
async def test_create_run_rejects_removed_context_and_web_search_options(
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
                "options": {
                    "allow_web_search": False,
                    "context_classification": "confidential",
                },
            },
        )

    assert create_response.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allow_web_search", False),
        ("context_classification", "confidential"),
    ],
)
async def test_create_run_rejects_removed_top_level_context_and_web_search_fields(
    tmp_path: Path,
    field: str,
    value: object,
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
                field: value,
            },
        )

    assert create_response.status_code == 422


@pytest.mark.anyio
async def test_openapi_create_request_does_not_expose_removed_options() -> None:
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    schemas = response.json()["components"]["schemas"]
    assert schemas["CreateResearchRunRequest"]["additionalProperties"] is False
    option_properties = schemas["ResearchRunOptions"].get("properties", {})
    assert "allow_web_search" not in option_properties
    assert "context_classification" not in option_properties


@pytest.mark.anyio
async def test_human_review_api_does_not_require_reviewer_identity(tmp_path: Path) -> None:
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

    assert queue_response.status_code == 200
    assert payload_response.status_code == 200
    assert decisions_response.status_code == 200
    assert resume_response.status_code == 200


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
            json={"action": "approve"},
        )

    assert resume_response.status_code == 409


def test_create_request_keeps_options_defaultable() -> None:
    request = CreateResearchRunRequest(user_prompt="調査してください")

    assert request.options.model_dump() == {
        "max_deep_research_runs": None,
        "max_llm_fix_runs": None,
        "max_total_iterations": None,
        "max_no_progress_rounds": None,
        "max_total_tool_calls": None,
    }
