from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from api.config import get_settings
from api.main import create_app
from api.research.azure_responses import ReviewRequestTimeout
from api.research.dependencies import get_research_orchestrator
from api.research.poller import ResearchPoller
from api.research.repository import ResearchRepository
from api.research.schemas import (
    CreateResearchRunRequest,
    FailureMode,
    HumanReviewAction,
    ItemAssessment,
    ItemStatus,
    RecommendedAction,
    ResearchRunOptions,
    ReviewResult,
    RunStatus,
    Severity,
    Verdict,
)
from research_v2_fakes import V2FakeAzure, make_v2_orchestrator

CHECKPOINT_FORK_ROUTE_PATHS = {
    "/research-runs/{run_id}/checkpoints",
    "/research-runs/{run_id}/checkpoints/{checkpoint_id}",
    "/research-runs/{run_id}/checkpoints/{checkpoint_id}/fork-preview",
    "/research-runs/{run_id}/checkpoints/{checkpoint_id}/forks",
    "/research-runs/{run_id}/lineage",
}


def _xfail_if_checkpoint_fork_routes_missing(app: Any) -> None:
    paths = {getattr(route, "path", None) for route in app.routes}
    missing = sorted(CHECKPOINT_FORK_ROUTE_PATHS - paths)
    if missing:
        pytest.xfail(
            "Checkpoint fork API routes are not implemented yet: "
            + ", ".join(missing)
        )


async def _create_completed_run(
    client: AsyncClient,
    orchestrator: Any,
) -> str:
    create_response = await client.post(
        "/research-runs",
        json={
            "user_prompt": "市場調査をしてください",
        },
    )
    assert create_response.status_code == 202
    run_id = create_response.json()["run_id"]
    orchestrator.collect_deep_research(run_id)
    return str(run_id)


async def _list_checkpoints(
    client: AsyncClient,
    run_id: str,
) -> list[dict[str, Any]]:
    response = await client.get(
        f"/research-runs/{run_id}/checkpoints",
        params={"include_forks": "true"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    checkpoints = cast(list[dict[str, Any]], payload["checkpoints"])
    assert isinstance(checkpoints, list)
    assert checkpoints
    return checkpoints


def _first_checkpoint(
    checkpoints: list[dict[str, Any]],
    *,
    forkable: bool,
) -> dict[str, Any]:
    for checkpoint in checkpoints:
        if checkpoint["forkable"] is forkable:
            return checkpoint
    expected = "forkable" if forkable else "non-forkable"
    raise AssertionError(f"Completed run did not expose a {expected} checkpoint.")


@pytest.mark.anyio
async def test_research_runs_require_configured_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESEARCH_API_KEY", "test-secret")
    get_settings.cache_clear()

    try:
        orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
        app = create_app()
        app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            missing_response = await client.post(
                "/research-runs",
                json={"user_prompt": "市場調査をしてください"},
            )
            wrong_response = await client.post(
                "/research-runs",
                headers={"X-API-Key": "wrong-secret"},
                json={"user_prompt": "市場調査をしてください"},
            )
            bearer_response = await client.post(
                "/research-runs",
                headers={"Authorization": "Bearer test-secret"},
                json={"user_prompt": "市場調査をしてください"},
            )
            assert bearer_response.status_code == 202
            api_key_response = await client.get(
                f"/research-runs/{bearer_response.json()['run_id']}",
                headers={"X-API-Key": "test-secret"},
            )

        assert missing_response.status_code == 401
        assert wrong_response.status_code == 401
        assert api_key_response.status_code == 200
    finally:
        get_settings.cache_clear()


@pytest.mark.anyio
async def test_research_run_api_flow(tmp_path: Path) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
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
            },
        )

        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]

        initial_status_response = await client.get(f"/research-runs/{run_id}")
        assert initial_status_response.status_code == 200
        initial_status_json = initial_status_response.json()
        assert initial_status_json["status"] == "waiting_deep_research"
        assert initial_status_json["created_at"]
        assert initial_status_json["updated_at"]
        assert initial_status_json["deep_research_submitted_at"] is not None
        assert initial_status_json["forecast_context"] is None

        orchestrator.collect_deep_research(run_id)

        status_response = await client.get(f"/research-runs/{run_id}")
        assert status_response.status_code == 200
        status_json = status_response.json()
        assert status_json["status"] == "completed"
        assert status_json["terminal_status"] == "completed_passed_review"
        assert status_json["progress"]["latest_verdict"] == "pass"
        assert status_json["progress"]["items_answered"] >= 1

        report_response = await client.get(f"/research-runs/{run_id}/report")
        assert report_response.status_code == 200
        assert report_response.json()["final_report"] == "調査レポート本文"

        reviews_response = await client.get(f"/research-runs/{run_id}/reviews")
        assert reviews_response.status_code == 200
        reviews_json = reviews_response.json()
        assert reviews_json
        assert reviews_json[0]["review_no"] == 1
        assert reviews_json[0]["verdict"] == "pass"
        assert reviews_json[0]["recommended_route"] == "pass"
        assert [item["item_id"] for item in reviews_json[0]["item_assessments"]] == [
            "RI-001",
            "RI-002",
            "RI-003",
            "RI-004",
            "RI-005",
        ]

        audit_response = await client.get(f"/research-runs/{run_id}/audit")
        assert audit_response.status_code == 200
        audit_json = audit_response.json()
        assert audit_json["attempts"]
        assert audit_json["reviews"] == reviews_json
        assert audit_json["citations"]
        assert [call["step"] for call in audit_json["llm_calls"]] == [
            "deep_research",
            "review",
        ]


@pytest.mark.anyio
async def test_research_api_rejects_blank_prompt_fields(tmp_path: Path) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    _xfail_if_checkpoint_fork_routes_missing(app)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        blank_create_response = await client.post(
            "/research-runs",
            json={"user_prompt": " \n\t "},
        )

        run_id = await _create_completed_run(client, orchestrator)
        checkpoint = _first_checkpoint(
            await _list_checkpoints(client, run_id),
            forkable=True,
        )
        preview_path = (
            f"/research-runs/{run_id}/checkpoints/"
            f"{checkpoint['checkpoint_id']}/fork-preview"
        )
        fork_path = (
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/forks"
        )

        blank_preview_response = await client.post(
            preview_path,
            json={"additional_prompt": " \n\t "},
        )
        valid_preview_response = await client.post(
            preview_path,
            json={"additional_prompt": "追加調査してください。"},
        )
        preview_hash = valid_preview_response.json()["preview_hash"]
        blank_fork_prompt_response = await client.post(
            fork_path,
            json={
                "additional_prompt": " \n\t ",
                "idempotency_key": "blank-fork-prompt",
                "confirmed_preview_hash": preview_hash,
            },
        )
        blank_idempotency_response = await client.post(
            fork_path,
            json={
                "additional_prompt": "追加調査してください。",
                "idempotency_key": " \n\t ",
                "confirmed_preview_hash": preview_hash,
            },
        )

    assert blank_create_response.status_code == 422
    assert blank_preview_response.status_code == 422
    assert valid_preview_response.status_code == 200
    assert blank_fork_prompt_response.status_code == 422
    assert blank_idempotency_response.status_code == 422


@pytest.mark.anyio
async def test_manual_import_text_dispatches_review_without_deep_research_submit(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "公開情報だけで市場調査をしてください。",
                "report_text": "調査レポート本文\n\n[Example](https://example.com/source)",
                "allow_remote_review": "true",
                "allow_api_reruns": "false",
                "idempotency_key": "manual-import-pass",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]
        status_response = await client.get(f"/research-runs/{run_id}")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    assert fake.submitted_prompts == []
    assert fake.review_calls == 1
    status_json = status_response.json()
    assert status_json["status"] == "completed"
    assert status_json["progress"]["deep_research_runs"] == 1
    assert status_json["progress"]["total_tool_calls"] == 0
    audit_json = audit_response.json()
    assert audit_json["attempts"][0]["source"] == "manual_upload"
    assert audit_json["attempts"][0]["model"] == "chatgpt-deep-research-manual"
    assert audit_json["attempts"][0]["response_id"] is None
    assert audit_json["citations"][0]["source_type"] == "manual_upload_url_unverified"
    assert audit_json["cost_events"] == [
        event for event in audit_json["cost_events"] if event["step"] != "deep_research"
    ]


@pytest.mark.anyio
async def test_manual_import_without_remote_review_enters_human_review(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "公開情報だけで調べてください。",
                "report_text": "調査レポート本文",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]
        status_response = await client.get(f"/research-runs/{run_id}")

    assert fake.submitted_prompts == []
    assert fake.review_calls == 0
    status_json = status_response.json()
    assert status_json["status"] == "needs_human_review"
    assert status_json["done_reason"] == "manual_import_remote_review_not_allowed"


@pytest.mark.anyio
async def test_manual_import_sensitive_terms_still_dispatches_review(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "api_key を含む内容は外部に出さないでください。",
                "report_text": "調査レポート本文",
                "allow_remote_review": "true",
                "allow_api_reruns": "true",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]
        status_response = await client.get(f"/research-runs/{run_id}")

    assert fake.submitted_prompts == []
    assert fake.review_calls == 1
    status_json = status_response.json()
    assert status_json["status"] == "completed"
    assert status_json["done_reason"] == "passed_review"


@pytest.mark.anyio
async def test_manual_import_disables_api_reruns_when_not_allowed(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure(verdict=Verdict.NEEDS_TARGETED_RERUN)
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "公開情報だけで調べてください。",
                "report_text": "追加調査が必要なレポート本文",
                "allow_remote_review": "true",
                "allow_api_reruns": "false",
                "rerun_execution_mode": "api",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]
        status_response = await client.get(f"/research-runs/{run_id}")

    assert fake.review_calls == 1
    assert fake.submitted_prompts == []
    assert orchestrator.repository.get_run(UUID(run_id)).rerun_execution_mode.value == "disabled"
    status_json = status_response.json()
    assert status_json["status"] == "needs_human_review"
    assert status_json["progress"]["targeted_rerun_runs"] == 0


@pytest.mark.anyio
async def test_manual_import_allows_next_deep_research_attempt_when_reruns_allowed(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure(verdict=Verdict.NEEDS_TARGETED_RERUN)
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "公開情報だけで調べてください。",
                "report_text": "追加調査が必要なレポート本文",
                "allow_remote_review": "true",
                "allow_api_reruns": "true",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]
        status_response = await client.get(f"/research-runs/{run_id}")
        attempts_response = await client.get(f"/research-runs/{run_id}/attempts")

    assert fake.review_calls == 1
    assert len(fake.submitted_prompts) == 1
    status_json = status_response.json()
    assert status_json["status"] == "waiting_deep_research"
    assert status_json["progress"]["deep_research_runs"] == 2
    assert status_json["progress"]["targeted_rerun_runs"] == 1
    attempts = attempts_response.json()
    assert attempts[0]["source"] == "manual_upload"
    assert attempts[1]["source"] == "api"
    assert attempts[1]["run_no"] == 2


@pytest.mark.anyio
async def test_manual_import_manual_chatgpt_mode_creates_pending_prompt_without_api_submit(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure(verdict=Verdict.NEEDS_TARGETED_RERUN)
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "公開情報だけで調べてください。",
                "report_text": "追加調査が必要なレポート本文",
                "allow_remote_review": "true",
                "allow_api_reruns": "false",
                "rerun_execution_mode": "manual_chatgpt",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]
        status_response = await client.get(f"/research-runs/{run_id}")
        payload_response = await client.get(f"/research-runs/{run_id}/human-review")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")
        blocked_action = await client.post(
            f"/research-runs/{run_id}/resume",
            json={"action": "approve", "comment": None},
        )

    assert fake.review_calls == 1
    assert fake.submitted_prompts == []
    status_json = status_response.json()
    assert status_json["status"] == "needs_human_review"
    assert status_json["done_reason"] == "manual_chatgpt_rerun_pending"
    assert status_json["progress"]["deep_research_runs"] == 1
    assert status_json["progress"]["targeted_rerun_runs"] == 0
    payload = payload_response.json()
    assert payload["allowed_actions"] == []
    pending = payload["pending_manual_rerun"]
    assert pending["rerun_id"].startswith("RR-")
    assert pending["expected_output_kind"] == "targeted_delta_sections"
    assert pending["expected_run_no"] == 2
    assert pending["prompt"]
    assert "Expected output: targeted_delta_sections" in pending["prompt"]
    assert "Do not return a full merged report." in pending["prompt"]
    assert pending["query_policy"]["status"] == "allowed"
    assert blocked_action.status_code == 409
    audit_json = audit_response.json()
    assert [attempt["source"] for attempt in audit_json["attempts"]] == ["manual_upload"]


@pytest.mark.anyio
async def test_manual_chatgpt_targeted_upload_merges_and_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure(verdicts=[Verdict.NEEDS_TARGETED_RERUN, Verdict.PASS])
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "公開情報だけで調べてください。",
                "report_text": "Base report section.",
                "allow_remote_review": "true",
                "allow_api_reruns": "true",
                "rerun_execution_mode": "manual_chatgpt",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]
        pending_payload = (await client.get(f"/research-runs/{run_id}/human-review")).json()
        rerun_id = pending_payload["pending_manual_rerun"]["rerun_id"]
        stale_response = await client.post(
            f"/research-runs/{run_id}/manual-rerun-result",
            data={
                "rerun_id": "RR-stale",
                "report_text": "Delta with [Source](https://example.com/new).",
            },
        )
        upload_response = await client.post(
            f"/research-runs/{run_id}/manual-rerun-result",
            data={
                "rerun_id": rerun_id,
                "report_text": "Delta with [Source](https://example.com/new).",
            },
        )
        replay_response = await client.post(
            f"/research-runs/{run_id}/manual-rerun-result",
            data={
                "rerun_id": rerun_id,
                "report_text": "Delta with [Source](https://example.com/new).",
            },
        )
        conflict_response = await client.post(
            f"/research-runs/{run_id}/manual-rerun-result",
            data={"rerun_id": rerun_id, "report_text": "Different delta."},
        )
        report_response = await client.get(f"/research-runs/{run_id}/report")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    assert stale_response.status_code == 409
    assert upload_response.status_code == 200
    assert replay_response.status_code == 200
    assert conflict_response.status_code == 409
    assert fake.submitted_prompts == []
    assert fake.review_calls == 2
    upload_json = upload_response.json()
    assert upload_json["status"] == "completed"
    assert upload_json["progress"]["deep_research_runs"] == 2
    assert upload_json["progress"]["targeted_rerun_runs"] == 1
    final_report = report_response.json()["final_report"]
    assert "Base report section." in final_report
    assert "Delta with" in final_report
    audit_json = audit_response.json()
    assert [attempt["source"] for attempt in audit_json["attempts"]] == [
        "manual_upload",
        "manual_chatgpt_rerun",
    ]
    assert audit_json["attempts"][1]["response_id"] is None
    assert audit_json["attempts"][1]["citations"][0]["source_type"] == (
        "manual_chatgpt_rerun_url_unverified"
    )
    assert not [event for event in audit_json["cost_events"] if event["step"] == "deep_research"]
    assert audit_json["tool_calls"] == []


@pytest.mark.anyio
async def test_manual_chatgpt_replay_is_idempotent_with_new_pending_rerun(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure(
        verdicts=[Verdict.NEEDS_TARGETED_RERUN, Verdict.NEEDS_TARGETED_RERUN],
    )
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "公開情報だけで調べてください。",
                "report_text": "Base report section.",
                "allow_remote_review": "true",
                "allow_api_reruns": "true",
                "rerun_execution_mode": "manual_chatgpt",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]
        first_payload = (await client.get(f"/research-runs/{run_id}/human-review")).json()
        first_rerun_id = first_payload["pending_manual_rerun"]["rerun_id"]
        upload_response = await client.post(
            f"/research-runs/{run_id}/manual-rerun-result",
            data={
                "rerun_id": first_rerun_id,
                "report_text": "Delta with [Source](https://example.com/new).",
            },
        )
        second_payload = (await client.get(f"/research-runs/{run_id}/human-review")).json()
        replay_response = await client.post(
            f"/research-runs/{run_id}/manual-rerun-result",
            data={
                "rerun_id": first_rerun_id,
                "report_text": "Delta with [Source](https://example.com/new).",
            },
        )

    assert upload_response.status_code == 200
    assert upload_response.json()["done_reason"] == "manual_chatgpt_rerun_pending"
    second_rerun_id = second_payload["pending_manual_rerun"]["rerun_id"]
    assert second_rerun_id != first_rerun_id
    assert replay_response.status_code == 200
    assert replay_response.json()["done_reason"] == "manual_chatgpt_rerun_pending"
    assert fake.submitted_prompts == []
    assert fake.review_calls == 2


@pytest.mark.anyio
async def test_manual_chatgpt_upload_after_status_change_keeps_pending(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure(verdict=Verdict.NEEDS_TARGETED_RERUN)
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "公開情報だけで調べてください。",
                "report_text": "Base report section.",
                "allow_remote_review": "true",
                "allow_api_reruns": "true",
                "rerun_execution_mode": "manual_chatgpt",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]
        pending_payload = (await client.get(f"/research-runs/{run_id}/human-review")).json()
        rerun_id = pending_payload["pending_manual_rerun"]["rerun_id"]
        cancel_response = await client.post(f"/research-runs/{run_id}/cancel")
        upload_response = await client.post(
            f"/research-runs/{run_id}/manual-rerun-result",
            data={
                "rerun_id": rerun_id,
                "report_text": "Delta with [Source](https://example.com/new).",
            },
        )
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"
    assert upload_response.status_code == 409
    assert "run state changed" in upload_response.json()["detail"]
    assert orchestrator.repository.get_active_manual_rerun_request(UUID(run_id)) is not None
    assert [attempt["source"] for attempt in audit_response.json()["attempts"]] == [
        "manual_upload",
    ]


@pytest.mark.anyio
async def test_manual_chatgpt_full_upload_replaces_report(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure(verdicts=[Verdict.NEEDS_FULL_RERUN, Verdict.PASS])
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "公開情報だけで調べてください。",
                "report_text": "Original defective report.",
                "allow_remote_review": "true",
                "allow_api_reruns": "true",
                "rerun_execution_mode": "manual_chatgpt",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]
        pending_payload = (await client.get(f"/research-runs/{run_id}/human-review")).json()
        rerun_id = pending_payload["pending_manual_rerun"]["rerun_id"]
        assert pending_payload["pending_manual_rerun"]["scope"] == "full_rerun"
        upload_response = await client.post(
            f"/research-runs/{run_id}/manual-rerun-result",
            data={
                "rerun_id": rerun_id,
                "report_text": "Complete replacement report.",
            },
        )
        report_response = await client.get(f"/research-runs/{run_id}/report")

    assert upload_response.status_code == 200
    upload_json = upload_response.json()
    assert upload_json["progress"]["deep_research_runs"] == 2
    assert upload_json["progress"]["full_rerun_runs"] == 1
    assert report_response.json()["final_report"] == "Complete replacement report."


@pytest.mark.anyio
async def test_manual_chatgpt_targeted_upload_rejects_full_report_and_keeps_pending(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure(verdict=Verdict.NEEDS_TARGETED_RERUN)
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "公開情報だけで調べてください。",
                "report_text": "Original report paragraph that should be preserved.",
                "allow_remote_review": "true",
                "allow_api_reruns": "true",
                "rerun_execution_mode": "manual_chatgpt",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]
        pending_payload = (await client.get(f"/research-runs/{run_id}/human-review")).json()
        rerun_id = pending_payload["pending_manual_rerun"]["rerun_id"]
        rejected = await client.post(
            f"/research-runs/{run_id}/manual-rerun-result",
            data={
                "rerun_id": rerun_id,
                "report_text": (
                    "Original report paragraph that should be preserved.\n\n"
                    "This is an attempted full merged report."
                ),
            },
        )
        status_response = await client.get(f"/research-runs/{run_id}")
        payload_response = await client.get(f"/research-runs/{run_id}/human-review")

    assert rejected.status_code == 409
    status_json = status_response.json()
    assert status_json["status"] == "needs_human_review"
    assert status_json["progress"]["deep_research_runs"] == 1
    assert status_json["progress"]["targeted_rerun_runs"] == 0
    assert payload_response.json()["pending_manual_rerun"]["rerun_id"] == rerun_id


@pytest.mark.anyio
async def test_manual_chatgpt_query_policy_blocked_prompt_has_no_pending_rerun(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure(verdict=Verdict.NEEDS_TARGETED_RERUN)
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "api_key を含む内容を公開情報だけで調べてください。",
                "report_text": "追加調査が必要なレポート本文",
                "allow_remote_review": "true",
                "allow_api_reruns": "true",
                "rerun_execution_mode": "manual_chatgpt",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]
        status_response = await client.get(f"/research-runs/{run_id}")
        payload_response = await client.get(f"/research-runs/{run_id}/human-review")

    assert fake.review_calls == 1
    assert fake.submitted_prompts == []
    status_json = status_response.json()
    assert status_json["status"] == "needs_human_review"
    assert status_json["done_reason"] == "manual_rerun_prompt_blocked_by_query_policy"
    payload = payload_response.json()
    assert payload["pending_manual_rerun"] is None
    assert payload["suggested_rerun"] is None
    assert payload["allowed_actions"]
    assert HumanReviewAction.REQUEST_MANUAL_TARGETED_RERUN.value not in payload[
        "allowed_actions"
    ]
    assert HumanReviewAction.REQUEST_MANUAL_FULL_RERUN.value not in payload[
        "allowed_actions"
    ]
    states = {state["action"]: state for state in payload["action_states"]}
    assert states[HumanReviewAction.REQUEST_MANUAL_TARGETED_RERUN.value] == {
        "action": HumanReviewAction.REQUEST_MANUAL_TARGETED_RERUN.value,
        "allowed": False,
        "blocked_reason": "manual_rerun_prompt_blocked_by_query_policy",
    }
    assert states[HumanReviewAction.REQUEST_MANUAL_FULL_RERUN.value] == {
        "action": HumanReviewAction.REQUEST_MANUAL_FULL_RERUN.value,
        "allowed": False,
        "blocked_reason": "manual_rerun_prompt_blocked_by_query_policy",
    }


@pytest.mark.anyio
async def test_resume_api_rejects_query_policy_blocked_manual_rerun(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure(verdict=Verdict.HUMAN_REVIEW)
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "api_key を含む内容を公開情報だけで調べてください。",
                "report_text": "追加調査が必要なレポート本文",
                "allow_remote_review": "true",
                "allow_api_reruns": "true",
                "rerun_execution_mode": "manual_chatgpt",
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]

        payload_response = await client.get(f"/research-runs/{run_id}/human-review")
        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={
                "action": HumanReviewAction.REQUEST_MANUAL_FULL_RERUN.value,
                "comment": "ChatGPTで全面作り直し",
            },
        )
        pending_payload_response = await client.get(
            f"/research-runs/{run_id}/human-review"
        )

    assert payload_response.status_code == 200
    payload = payload_response.json()
    assert HumanReviewAction.REQUEST_MANUAL_FULL_RERUN.value not in payload[
        "allowed_actions"
    ]
    assert resume_response.status_code == 409
    assert (
        "manual_rerun_prompt_blocked_by_query_policy"
        in resume_response.json()["detail"]
    )
    assert pending_payload_response.json()["pending_manual_rerun"] is None


@pytest.mark.anyio
async def test_manual_import_validates_file_text_xor_and_options(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        both_prompt_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "prompt",
                "report_text": "report",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
            },
            files={"input_prompt_file": ("prompt.txt", b"prompt", "text/plain")},
        )
        blank_text_and_file_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": " ",
                "report_text": "report",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
            },
            files={"input_prompt_file": ("prompt.txt", b"prompt", "text/plain")},
        )
        missing_report_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "prompt",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
            },
        )
        blank_prompt_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": " \n\t ",
                "report_text": "report",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
            },
        )
        bad_ext_response = await client.post(
            "/research-runs/manual-import",
            data={
                "report_text": "report",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
            },
            files={"input_prompt_file": ("prompt.pdf", b"prompt", "application/pdf")},
        )
        non_utf_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "prompt",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
            },
            files={"report_file": ("report.md", b"\xff\xfe", "text/markdown")},
        )
        oversize_file_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "prompt",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
            },
            files={
                "report_file": (
                    "report.md",
                    b"a" * (orchestrator.settings.research_manual_import_max_file_bytes + 1),
                    "text/markdown",
                )
            },
        )
        prompt_limit_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "a" * 50001,
                "report_text": "report",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
            },
        )
        orchestrator.settings.research_manual_import_max_report_chars = 10
        report_limit_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "prompt",
                "report_text": "a" * 11,
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
            },
        )
        bad_options_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "prompt",
                "report_text": "report",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
                "options_json": "{not-json",
            },
        )

    assert both_prompt_response.status_code == 422
    assert blank_text_and_file_response.status_code == 422
    assert missing_report_response.status_code == 422
    assert blank_prompt_response.status_code == 422
    assert bad_ext_response.status_code == 422
    assert non_utf_response.status_code == 422
    assert oversize_file_response.status_code == 422
    assert prompt_limit_response.status_code == 422
    assert report_limit_response.status_code == 422
    assert bad_options_response.status_code == 422


@pytest.mark.anyio
async def test_manual_import_idempotency_replay_and_conflict(tmp_path: Path) -> None:
    fake = V2FakeAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "prompt",
                "report_text": "report",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
                "idempotency_key": "same-key",
            },
        )
        replay = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "prompt",
                "report_text": "report",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
                "idempotency_key": "same-key",
            },
        )
        conflict = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "prompt",
                "report_text": "different report",
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
                "idempotency_key": "same-key",
            },
        )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert first.json()["run_id"] == replay.json()["run_id"]
    assert conflict.status_code == 409
    assert fake.review_calls == 0


def test_manual_import_idempotency_race_cleans_unused_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
    metadata = {"input_prompt": {"source": "text"}, "report": {"source": "text"}}

    first, dispatch_review = orchestrator.create_manual_import_run(
        input_prompt="prompt",
        report="report",
        options=ResearchRunOptions(),
        allow_remote_review=False,
        allow_api_reruns=False,
        idempotency_key="race-key",
        metadata=metadata,
    )
    assert dispatch_review is False
    assert (orchestrator.settings.research_artifact_dir / str(first.id)).is_dir()

    def miss_manual_import_idempotency(_idempotency_key: str) -> None:
        return None

    monkeypatch.setattr(
        orchestrator.repository,
        "get_manual_import_idempotency",
        miss_manual_import_idempotency,
    )

    replay, dispatch_review = orchestrator.create_manual_import_run(
        input_prompt="prompt",
        report="report",
        options=ResearchRunOptions(),
        allow_remote_review=False,
        allow_api_reruns=False,
        idempotency_key="race-key",
        metadata=metadata,
    )

    assert replay.id == first.id
    assert dispatch_review is False
    artifact_dirs = sorted(
        path.name
        for path in orchestrator.settings.research_artifact_dir.iterdir()
        if path.is_dir()
    )
    assert artifact_dirs == [str(first.id)]

    with pytest.raises(ValueError, match="different request"):
        orchestrator.create_manual_import_run(
            input_prompt="prompt",
            report="different report",
            options=ResearchRunOptions(),
            allow_remote_review=False,
            allow_api_reruns=False,
            idempotency_key="race-key",
            metadata=metadata,
        )

    artifact_dirs = sorted(
        path.name
        for path in orchestrator.settings.research_artifact_dir.iterdir()
        if path.is_dir()
    )
    assert artifact_dirs == [str(first.id)]


def test_manual_import_artifact_failure_does_not_persist_idempotency(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
    original_save_json = orchestrator.artifacts.save_json

    def fail_save_json(*_args: Any, **_kwargs: Any) -> tuple[str, str]:
        raise RuntimeError("artifact write failed")

    orchestrator.artifacts.save_json = fail_save_json  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="artifact write failed"):
        orchestrator.create_manual_import_run(
            input_prompt="public prompt",
            report="public report",
            options=ResearchRunOptions(),
            allow_remote_review=False,
            allow_api_reruns=False,
            idempotency_key="atomic-key",
            metadata={"input_prompt": {"source": "text"}, "report": {"source": "text"}},
        )

    with orchestrator.repository.connect() as connection:
        run_count = connection.execute("SELECT COUNT(*) AS count FROM research_runs").fetchone()
        request_count = connection.execute(
            "SELECT COUNT(*) AS count FROM manual_import_requests"
        ).fetchone()
    assert run_count["count"] == 0
    assert request_count["count"] == 0

    orchestrator.artifacts.save_json = original_save_json  # type: ignore[method-assign]
    run, dispatch_review = orchestrator.create_manual_import_run(
        input_prompt="public prompt",
        report="public report",
        options=ResearchRunOptions(),
        allow_remote_review=False,
        allow_api_reruns=False,
        idempotency_key="atomic-key",
        metadata={"input_prompt": {"source": "text"}, "report": {"source": "text"}},
    )

    assert dispatch_review is False
    assert run.status == RunStatus.NEEDS_HUMAN_REVIEW


@pytest.mark.anyio
async def test_manual_import_metadata_omits_prompt_and_report_bodies(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    prompt_body = "unique prompt body should not be in metadata"
    report_body = "unique report body should not be in metadata"

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": prompt_body,
                "report_text": report_body,
                "allow_remote_review": "false",
                "allow_api_reruns": "false",
                "idempotency_key": "metadata-key",
            },
        )

    assert create_response.status_code == 202
    run_id = create_response.json()["run_id"]
    attempt = orchestrator.repository.get_attempts(UUID(run_id))[0]
    raw_metadata = json.loads(Path(attempt.raw_response_artifact_path or "").read_text())
    with orchestrator.repository.connect() as connection:
        row = connection.execute(
            """
            SELECT request_metadata_json
            FROM manual_import_requests
            WHERE idempotency_key = ?
            """,
            ("metadata-key",),
        ).fetchone()
    idempotency_metadata = json.loads(row["request_metadata_json"])

    assert prompt_body not in json.dumps(raw_metadata, ensure_ascii=False)
    assert report_body not in json.dumps(raw_metadata, ensure_ascii=False)
    assert prompt_body not in json.dumps(idempotency_metadata, ensure_ascii=False)
    assert report_body not in json.dumps(idempotency_metadata, ensure_ascii=False)
    assert raw_metadata["prompt_sha256"]
    assert raw_metadata["report_sha256"]
    assert idempotency_metadata["prompt_chars"] == len(prompt_body)
    assert idempotency_metadata["report_chars"] == len(report_body)


@pytest.mark.anyio
async def test_manual_import_poller_resumes_pending_review(tmp_path: Path) -> None:
    fake = V2FakeAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    run, dispatch_review = orchestrator.create_manual_import_run(
        input_prompt="公開情報だけで調べてください。",
        report="調査レポート本文",
        options=ResearchRunOptions(),
        allow_remote_review=True,
        allow_api_reruns=False,
        idempotency_key=None,
        metadata={"input_prompt": {"source": "text"}, "report": {"source": "text"}},
    )
    poller = ResearchPoller(orchestrator=orchestrator, interval_seconds=60)

    assert dispatch_review is True
    assert run.status == RunStatus.REVIEWING
    assert fake.review_calls == 0

    await poller.tick()

    updated = orchestrator.repository.get_run(run.id)
    assert fake.review_calls == 1
    assert updated.status == RunStatus.COMPLETED


@pytest.mark.anyio
async def test_manual_import_review_respects_citation_cap(tmp_path: Path) -> None:
    class CitationRecordingAzure(V2FakeAzure):
        def __init__(self) -> None:
            super().__init__()
            self.review_citation_counts: list[int] = []

        def review_report(self, **kwargs: Any) -> tuple[ReviewResult, str, dict[str, object]]:
            self.review_citation_counts.append(len(kwargs.get("citations") or []))
            return super().review_report(**kwargs)

    fake = CitationRecordingAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    orchestrator.settings.research_review_max_citations = 2
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    report = "\n".join(f"[Source {index}](https://example.com/{index})" for index in range(5))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs/manual-import",
            data={
                "input_prompt_text": "公開情報だけで調べてください。",
                "report_text": report,
                "allow_remote_review": "true",
                "allow_api_reruns": "false",
            },
        )

    assert create_response.status_code == 202
    assert fake.review_citation_counts == [2]


def test_research_attempt_source_column_is_added_to_legacy_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    repository = ResearchRepository(db_path)
    with repository.connect() as connection:
        connection.executescript(
            """
            ALTER TABLE research_attempts RENAME TO research_attempts_current;
            CREATE TABLE research_attempts (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                attempt_no INTEGER NOT NULL,
                response_id TEXT,
                model TEXT NOT NULL,
                prompt TEXT NOT NULL,
                output_text TEXT,
                status TEXT NOT NULL,
                error TEXT,
                tool_calls TEXT NOT NULL DEFAULT '[]',
                citations TEXT NOT NULL DEFAULT '[]',
                raw_response_artifact_path TEXT,
                created_at TEXT NOT NULL
            );
            DROP TABLE research_attempts_current;
            """
        )

    reopened = ResearchRepository(db_path)
    with reopened.connect() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(research_attempts)")
        }

    assert "source" in columns


def test_review_missing_research_items_enters_human_review(tmp_path: Path) -> None:
    orchestrator = make_v2_orchestrator(
        tmp_path,
        V2FakeAzure(
            item_assessments=[
                ItemAssessment(
                    item_id="RI-001",
                    status=ItemStatus.ANSWERED,
                    severity=Severity.MAJOR,
                    failure_mode=FailureMode.NONE,
                    failure_mode_confidence=90,
                    recommended_action=RecommendedAction.NONE,
                    evidence_summary="covered",
                    missing_evidence=[],
                    rationale="only one item was reviewed",
                )
            ],
        ),
    )

    run = orchestrator.create_run(
        CreateResearchRunRequest(user_prompt="市場調査をしてください")
    )
    stopped = orchestrator.collect_deep_research(run.id)
    history = orchestrator.repository.get_history(run.id)
    missing_event = next(
        event for event in history if event["step"] == "review_missing_research_items"
    )

    assert stopped.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert stopped.done_reason == "review_missing_research_items"
    assert stopped.needs_human_review is True
    assert missing_event["missing_item_ids"] == ["RI-002", "RI-003", "RI-004", "RI-005"]
    assert missing_event["unknown_item_ids"] == []
    assert missing_event["assessed_item_ids"] == ["RI-001"]


@pytest.mark.anyio
async def test_submit_persistence_failure_cancels_remote_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = V2FakeAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    original_save_json = orchestrator.artifacts.save_json

    def fail_submit_raw_response(
        run_id: UUID,
        relative_path: str,
        payload: object,
    ) -> tuple[str, str]:
        if relative_path == "raw-responses/deep_research_submit_001.json":
            raise OSError("disk full")
        return original_save_json(run_id, relative_path, payload)

    monkeypatch.setattr(orchestrator.artifacts, "save_json", fail_submit_raw_response)
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
        status_response = await client.get(f"/research-runs/{run_id}")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    assert fake.cancelled == ["resp_deep_1"]
    status_json = status_response.json()
    assert status_json["status"] == "needs_human_review"
    assert status_json["done_reason"] == "deep_research_submitted_but_persistence_failed"

    audit_json = audit_response.json()
    assert [attempt["status"] for attempt in audit_json["attempts"]] == [
        "submitted_but_persistence_failed"
    ]
    assert audit_json["attempts"][0]["response_id"] == "resp_deep_1"
    history_steps = [event["step"] for event in audit_json["history"]]
    assert "deep_research_submit_persistence_remote_cancel_succeeded" in history_steps
    assert "deep_research_submit_persistence_failed" in history_steps


@pytest.mark.anyio
async def test_checkpoint_list_and_detail_api_exposes_phase_records(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    _xfail_if_checkpoint_fork_routes_missing(app)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        run_id = await _create_completed_run(client, orchestrator)
        checkpoints = await _list_checkpoints(client, run_id)
        first_checkpoint = checkpoints[0]
        detail_response = await client.get(
            f"/research-runs/{run_id}/checkpoints/{first_checkpoint['checkpoint_id']}"
        )

    assert {checkpoint["kind"] for checkpoint in checkpoints} >= {
        "deep_research_collected",
        "review_recorded",
        "finalized",
    }
    assert all(checkpoint["run_id"] == run_id for checkpoint in checkpoints)
    assert all(checkpoint["checkpoint_no"] > 0 for checkpoint in checkpoints)
    assert all(checkpoint["node_anchor"] for checkpoint in checkpoints)
    assert all("forks" in checkpoint for checkpoint in checkpoints)
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["checkpoint_id"] == first_checkpoint["checkpoint_id"]
    assert detail["run_id"] == run_id
    assert isinstance(detail["snapshot_json"], dict)
    assert "report_hash" in detail


@pytest.mark.anyio
async def test_checkpoint_add_retries_checkpoint_no_collision_without_returning_other_dedupe(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    _xfail_if_checkpoint_fork_routes_missing(app)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        run_id = await _create_completed_run(client, orchestrator)

    with orchestrator.repository.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER force_checkpoint_no_collision_once
            BEFORE INSERT ON research_checkpoints
            WHEN NEW.dedupe_key = 'collision-target'
             AND NOT EXISTS (
                SELECT 1
                FROM research_checkpoints
                WHERE run_id = NEW.run_id
                  AND dedupe_key = 'collision-competitor'
             )
            BEGIN
                INSERT INTO research_checkpoints (
                    checkpoint_id, run_id, checkpoint_no, kind, node_anchor,
                    forkable, dedupe_key, snapshot_json, created_at
                )
                VALUES (
                    '00000000-0000-4000-8000-000000000001',
                    NEW.run_id, NEW.checkpoint_no,
                    'forced_collision', 'forced_collision', 0,
                    'collision-competitor', '{}', NEW.created_at
                );
                SELECT RAISE(IGNORE);
            END;
            """
        )

    checkpoint = orchestrator.repository.add_checkpoint(
        UUID(run_id),
        kind="collision_target",
        node_anchor="collision_target",
        forkable=True,
        dedupe_key="collision-target",
        snapshot_json={"source_prompt": "prompt", "source_report": "report"},
    )

    assert checkpoint.dedupe_key == "collision-target"
    assert checkpoint.kind == "collision_target"
    checkpoints = orchestrator.repository.list_checkpoints(UUID(run_id))
    competitor = next(
        item for item in checkpoints if item.dedupe_key == "collision-competitor"
    )
    assert checkpoint.checkpoint_no == competitor.checkpoint_no + 1


@pytest.mark.anyio
async def test_checkpoint_fork_preview_hash_mismatch_returns_409(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    _xfail_if_checkpoint_fork_routes_missing(app)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        run_id = await _create_completed_run(client, orchestrator)
        checkpoints = await _list_checkpoints(client, run_id)
        checkpoint = _first_checkpoint(checkpoints, forkable=True)
        preview_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/fork-preview",
            json={"additional_prompt": "2026年の前提で差分を再調査してください。"},
        )
        mismatch_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/forks",
            json={
                "additional_prompt": "2026年の前提で差分を再調査してください。",
                "idempotency_key": "fork-preview-mismatch",
                "confirmed_preview_hash": "sha256:not-the-preview",
            },
        )

    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["preview_hash"]
    assert preview["policy_decision"]["status"] == "allowed"
    assert preview["composed_prompt"]
    assert preview["source_prompt_excerpt"]
    assert preview["source_report_excerpt"]
    assert mismatch_response.status_code == 409
    assert len(fake.submitted_prompts) == 1


@pytest.mark.anyio
async def test_checkpoint_fork_rejects_non_forkable_checkpoint(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    _xfail_if_checkpoint_fork_routes_missing(app)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        run_id = await _create_completed_run(client, orchestrator)
        checkpoints = await _list_checkpoints(client, run_id)
        checkpoint = _first_checkpoint(checkpoints, forkable=False)
        response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/fork-preview",
            json={"additional_prompt": "この地点から再調査してください。"},
        )

    assert response.status_code == 409
    assert "not forkable" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_checkpoint_fork_submit_is_idempotent(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    _xfail_if_checkpoint_fork_routes_missing(app)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        run_id = await _create_completed_run(client, orchestrator)
        checkpoint = _first_checkpoint(
            await _list_checkpoints(client, run_id),
            forkable=True,
        )
        additional_prompt = "採用候補を日本市場に絞って追加調査してください。"
        preview_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/fork-preview",
            json={"additional_prompt": additional_prompt},
        )
        assert preview_response.status_code == 200
        fork_payload = {
            "additional_prompt": additional_prompt,
            "idempotency_key": "idempotent-fork-submit",
            "confirmed_preview_hash": preview_response.json()["preview_hash"],
        }
        first_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/forks",
            json=fork_payload,
        )
        second_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/forks",
            json=fork_payload,
        )

    assert first_response.status_code == 202
    assert second_response.status_code == 202
    first_payload = first_response.json()
    second_payload = second_response.json()
    assert second_payload["run_id"] == first_payload["run_id"]
    assert second_payload["parent_run_id"] == run_id
    assert second_payload["forked_from_checkpoint_id"] == checkpoint["checkpoint_id"]
    assert len(fake.submitted_prompts) == 2


@pytest.mark.anyio
async def test_checkpoint_fork_idempotency_conflict_returns_409(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    _xfail_if_checkpoint_fork_routes_missing(app)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        run_id = await _create_completed_run(client, orchestrator)
        checkpoint = _first_checkpoint(
            await _list_checkpoints(client, run_id),
            forkable=True,
        )
        additional_prompt = "採用候補を日本市場に絞って追加調査してください。"
        preview_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/fork-preview",
            json={"additional_prompt": additional_prompt},
        )
        assert preview_response.status_code == 200
        first_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/forks",
            json={
                "additional_prompt": additional_prompt,
                "idempotency_key": "conflicting-fork-submit",
                "confirmed_preview_hash": preview_response.json()["preview_hash"],
            },
        )
        changed_prompt = "採用候補を欧州市場に絞って追加調査してください。"
        changed_preview_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/fork-preview",
            json={"additional_prompt": changed_prompt},
        )
        prompt_conflict_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/forks",
            json={
                "additional_prompt": changed_prompt,
                "idempotency_key": "conflicting-fork-submit",
                "confirmed_preview_hash": changed_preview_response.json()["preview_hash"],
            },
        )
        hash_conflict_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/forks",
            json={
                "additional_prompt": additional_prompt,
                "idempotency_key": "conflicting-fork-submit",
                "confirmed_preview_hash": "sha256:not-the-original-preview",
            },
        )

    assert first_response.status_code == 202
    assert changed_preview_response.status_code == 200
    assert prompt_conflict_response.status_code == 409
    assert hash_conflict_response.status_code == 409
    assert "different prompt or preview hash" in prompt_conflict_response.json()["detail"]
    assert "different prompt or preview hash" in hash_conflict_response.json()["detail"]
    assert len(fake.submitted_prompts) == 2


@pytest.mark.anyio
async def test_policy_blocked_checkpoint_fork_creates_human_review_child(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    _xfail_if_checkpoint_fork_routes_missing(app)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        run_id = await _create_completed_run(client, orchestrator)
        checkpoint = _first_checkpoint(
            await _list_checkpoints(client, run_id),
            forkable=True,
        )
        additional_prompt = "internal project SECRET_TOKEN=abc123 を検索してください。"
        preview_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/fork-preview",
            json={"additional_prompt": additional_prompt},
        )
        assert preview_response.status_code == 200
        fork_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/forks",
            json={
                "additional_prompt": additional_prompt,
                "idempotency_key": "policy-blocked-fork",
                "confirmed_preview_hash": preview_response.json()["preview_hash"],
            },
        )
        assert fork_response.status_code == 202
        child_run_id = fork_response.json()["run_id"]
        child_status_response = await client.get(f"/research-runs/{child_run_id}")
        child_lineage_response = await client.get(
            f"/research-runs/{child_run_id}/lineage"
        )

    assert preview_response.json()["policy_decision"]["status"] == "blocked"
    child_status = child_status_response.json()
    assert child_status["status"] == "needs_human_review"
    assert child_status["needs_human_review"] is True
    assert (
        child_status["done_reason"]
        == "fork_deep_research_blocked_by_query_policy"
    )
    assert len(fake.submitted_prompts) == 1
    lineage = child_lineage_response.json()
    assert lineage["run_id"] == child_run_id
    assert lineage["parent_run_id"] == run_id
    assert lineage["forked_from_checkpoint_id"] == checkpoint["checkpoint_id"]
    assert lineage["source_snapshot_json"]["checkpoint_id"] == checkpoint["checkpoint_id"]


@pytest.mark.anyio
async def test_checkpoint_fork_initialization_failure_keeps_auditable_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = V2FakeAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    _xfail_if_checkpoint_fork_routes_missing(app)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        run_id = await _create_completed_run(client, orchestrator)
        checkpoint = _first_checkpoint(
            await _list_checkpoints(client, run_id),
            forkable=True,
        )
        additional_prompt = "このチェックポイントから差分を追加調査してください。"
        preview_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/fork-preview",
            json={"additional_prompt": additional_prompt},
        )
        assert preview_response.status_code == 200
        original_save_text = orchestrator.artifacts.save_text

        def fail_fork_source_report(
            run_id: UUID,
            relative_path: str,
            text: str,
        ) -> tuple[str, str]:
            if relative_path == "reports/fork_source_report.md":
                raise OSError("artifact write failed")
            return original_save_text(run_id, relative_path, text)

        monkeypatch.setattr(orchestrator.artifacts, "save_text", fail_fork_source_report)
        fork_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/forks",
            json={
                "additional_prompt": additional_prompt,
                "idempotency_key": "artifact-failure-fork",
                "confirmed_preview_hash": preview_response.json()["preview_hash"],
            },
        )
        assert fork_response.status_code == 202
        child_run_id = fork_response.json()["run_id"]
        child_status_response = await client.get(f"/research-runs/{child_run_id}")
        child_audit_response = await client.get(f"/research-runs/{child_run_id}/audit")
        child_lineage_response = await client.get(
            f"/research-runs/{child_run_id}/lineage"
        )

    child_status = child_status_response.json()
    assert child_status["status"] == "needs_human_review"
    assert child_status["needs_human_review"] is True
    assert child_status["done_reason"] == "fork_deep_research_initialization_failed"
    audit = child_audit_response.json()
    assert [attempt["status"] for attempt in audit["attempts"]] == [
        "failed_to_initialize"
    ]
    assert "research_run_forked" in [event["step"] for event in audit["history"]]
    assert "fork_initialization_failed" in [event["step"] for event in audit["history"]]
    lineage = child_lineage_response.json()
    assert lineage["run_id"] == child_run_id
    assert lineage["parent_run_id"] == run_id
    assert lineage["forked_from_checkpoint_id"] == checkpoint["checkpoint_id"]
    assert len(fake.submitted_prompts) == 1


@pytest.mark.anyio
async def test_checkpoint_fork_submit_failure_preserves_failed_attempt(
    tmp_path: Path,
) -> None:
    class ForkSubmitFailingAzure(V2FakeAzure):
        def submit_deep_research(
            self,
            *,
            prompt: str,
            max_tool_calls: int,
            tool_profile: str = "public",
            background: bool = True,
            policy_decision_id: str | None = None,
            **kwargs: object,
        ) -> dict[str, object]:
            if self.submitted_prompts:
                self.submitted_prompts.append(prompt)
                self.submitted_max_tool_calls.append(max_tool_calls)
                raise RuntimeError("remote submit failed")
            return super().submit_deep_research(
                prompt=prompt,
                max_tool_calls=max_tool_calls,
                tool_profile=tool_profile,
                background=background,
                policy_decision_id=policy_decision_id,
                **kwargs,
            )

    fake = ForkSubmitFailingAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    _xfail_if_checkpoint_fork_routes_missing(app)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        run_id = await _create_completed_run(client, orchestrator)
        checkpoint = _first_checkpoint(
            await _list_checkpoints(client, run_id),
            forkable=True,
        )
        additional_prompt = "このチェックポイントから再調査してください。"
        preview_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/fork-preview",
            json={"additional_prompt": additional_prompt},
        )
        assert preview_response.status_code == 200
        fork_response = await client.post(
            f"/research-runs/{run_id}/checkpoints/{checkpoint['checkpoint_id']}/forks",
            json={
                "additional_prompt": additional_prompt,
                "idempotency_key": "submit-failure-fork",
                "confirmed_preview_hash": preview_response.json()["preview_hash"],
            },
        )
        assert fork_response.status_code == 202
        child_run_id = fork_response.json()["run_id"]
        child_status_response = await client.get(f"/research-runs/{child_run_id}")
        child_audit_response = await client.get(f"/research-runs/{child_run_id}/audit")
        child_lineage_response = await client.get(
            f"/research-runs/{child_run_id}/lineage"
        )

    child_status = child_status_response.json()
    assert child_status["status"] == "needs_human_review"
    assert child_status["needs_human_review"] is True
    assert child_status["done_reason"] == "fork_deep_research_submit_failed"
    audit = child_audit_response.json()
    assert [attempt["status"] for attempt in audit["attempts"]] == ["failed_to_submit"]
    assert "research_run_forked" in [event["step"] for event in audit["history"]]
    lineage = child_lineage_response.json()
    assert lineage["run_id"] == child_run_id
    assert lineage["parent_run_id"] == run_id
    assert lineage["forked_from_checkpoint_id"] == checkpoint["checkpoint_id"]
    assert len(fake.submitted_prompts) == 2


@pytest.mark.anyio
async def test_parent_delete_preserves_checkpoint_fork_child_lineage_snapshot(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator
    _xfail_if_checkpoint_fork_routes_missing(app)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        parent_run_id = await _create_completed_run(client, orchestrator)
        checkpoint = _first_checkpoint(
            await _list_checkpoints(client, parent_run_id),
            forkable=True,
        )
        additional_prompt = "secret api_key sk-abcdefghijklmnopqrstuvwxyz を検索してください。"
        preview_response = await client.post(
            f"/research-runs/{parent_run_id}/checkpoints/{checkpoint['checkpoint_id']}/fork-preview",
            json={"additional_prompt": additional_prompt},
        )
        assert preview_response.status_code == 200
        fork_response = await client.post(
            f"/research-runs/{parent_run_id}/checkpoints/{checkpoint['checkpoint_id']}/forks",
            json={
                "additional_prompt": additional_prompt,
                "idempotency_key": "lineage-after-parent-delete",
                "confirmed_preview_hash": preview_response.json()["preview_hash"],
            },
        )
        assert fork_response.status_code == 202
        child_run_id = fork_response.json()["run_id"]
        delete_response = await client.delete(f"/research-runs/{parent_run_id}")
        child_status_response = await client.get(f"/research-runs/{child_run_id}")
        child_lineage_response = await client.get(
            f"/research-runs/{child_run_id}/lineage"
        )
        parent_status_response = await client.get(f"/research-runs/{parent_run_id}")

    assert delete_response.status_code == 204
    assert parent_status_response.status_code == 404
    assert child_status_response.status_code == 200
    lineage = child_lineage_response.json()
    assert lineage["run_id"] == child_run_id
    assert lineage["parent_run_id"] == parent_run_id
    assert lineage["forked_from_checkpoint_id"] == checkpoint["checkpoint_id"]
    assert lineage["source_snapshot_json"]["run_id"] == parent_run_id
    assert lineage["source_snapshot_json"]["checkpoint_id"] == checkpoint["checkpoint_id"]


@pytest.mark.anyio
async def test_unknown_research_run_returns_404(tmp_path: Path) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
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
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
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
            },
        )
        run_id = create_response.json()["run_id"]
        orchestrator.collect_deep_research(run_id)

        delete_response = await client.delete(f"/research-runs/{run_id}")
        status_response = await client.get(f"/research-runs/{run_id}")
        report_response = await client.get(f"/research-runs/{run_id}/report")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")
        reviews_response = await client.get(f"/research-runs/{run_id}/reviews")

    assert delete_response.status_code == 204
    assert status_response.status_code == 404
    assert report_response.status_code == 404
    assert audit_response.status_code == 404
    assert reviews_response.status_code == 404


@pytest.mark.anyio
async def test_delete_unknown_research_run_returns_404(tmp_path: Path) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
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
async def test_delete_forecast_linked_research_run_returns_typed_409(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
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
            },
        )
        run_id = create_response.json()["run_id"]
        orchestrator.collect_deep_research(run_id)
        with orchestrator.repository.connect() as connection:
            connection.execute(
                """
                CREATE TABLE forecast_research_packs (
                    research_run_id TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO forecast_research_packs (research_run_id) VALUES (?)",
                (run_id,),
            )

        response = await client.delete(f"/research-runs/{run_id}")
        status_response = await client.get(f"/research-runs/{run_id}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail == {
        "code": "forecast_linked_research_run",
        "message": "Research run is linked to a forecast and cannot be deleted.",
        "details": {"run_id": run_id},
    }
    assert status_response.status_code == 200


@pytest.mark.anyio
async def test_delete_waiting_run_returns_409_when_remote_cancel_fails(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(
        tmp_path,
        V2FakeAzure(cancel_raises=RuntimeError("remote cancel unavailable")),
    )
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
            },
        )
        run_id = create_response.json()["run_id"]

        response = await client.delete(f"/research-runs/{run_id}")
        status_response = await client.get(f"/research-runs/{run_id}")

    assert response.status_code == 409
    assert "Remote Deep Research cancel failed" in response.json()["detail"]
    assert status_response.status_code == 200


@pytest.mark.anyio
async def test_cancel_waiting_run_returns_409_when_remote_cancel_fails(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(
        tmp_path,
        V2FakeAzure(cancel_raises=RuntimeError("remote cancel unavailable")),
    )
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
            },
        )
        run_id = create_response.json()["run_id"]

        response = await client.post(f"/research-runs/{run_id}/cancel")
        status_response = await client.get(f"/research-runs/{run_id}")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    assert response.status_code == 409
    assert "Remote Deep Research cancel failed" in response.json()["detail"]
    status_payload = status_response.json()
    assert status_payload["status"] == "waiting_deep_research"
    assert status_payload["done_reason"] is None
    assert status_payload["needs_human_review"] is False
    assert "cancel_remote_failed" in [
        event["step"] for event in audit_response.json()["history"]
    ]


def test_mark_timeout_preserves_retryable_state_when_remote_cancel_fails(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(
        tmp_path,
        V2FakeAzure(cancel_raises=RuntimeError("remote cancel unavailable")),
    )
    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="市場調査をしてください",
        )
    )

    with pytest.raises(RuntimeError, match="Remote Deep Research cancel failed"):
        orchestrator.mark_timeout(run.id)

    latest = orchestrator.repository.get_run(run.id)
    history_steps = [
        event["step"] for event in orchestrator.repository.get_history(run.id)
    ]
    assert latest.status == RunStatus.COLLECTING
    assert latest.done_reason is None
    assert latest.needs_human_review is False
    assert latest.deep_research_status == "queued"
    assert "timeout_remote_cancel_failed" in history_steps
    assert all(
        attempt.status != "timeout"
        for attempt in orchestrator.repository.get_attempts(run.id)
    )


def test_review_operation_claim_blocks_duplicate_review_workers(tmp_path: Path) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
    run = orchestrator.repository.create_run(
        user_prompt="市場調査をしてください",
        options=CreateResearchRunRequest(
            user_prompt="市場調査をしてください",
        ).options,
        settings=orchestrator.settings,
    )
    orchestrator.repository.update_run(
        run.id,
        status=RunStatus.REVIEWING,
        report="draft report",
    )

    first_claim = orchestrator.repository.claim_review_operation(
        run.id,
        operation="review_run",
        lease_seconds=180,
    )
    second_claim = orchestrator.repository.claim_review_operation(
        run.id,
        operation="review_run",
        lease_seconds=180,
    )

    assert first_claim is not None
    assert second_claim is None

    _claimed_run, token = first_claim
    assert orchestrator.repository.release_review_operation(run.id, claim_token=token)
    assert (
        orchestrator.repository.claim_review_operation(
            run.id,
            operation="review_run",
            lease_seconds=180,
        )
        is not None
    )


@pytest.mark.anyio
async def test_resume_api_approves_human_review_run(tmp_path: Path) -> None:
    orchestrator = make_v2_orchestrator(
        tmp_path,
        V2FakeAzure(verdict=Verdict.HUMAN_REVIEW),
    )
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
            },
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
    class TimeoutThenPassAzure(V2FakeAzure):
        def review_report(self, **kwargs: Any) -> tuple[ReviewResult, str, dict[str, object]]:
            if self.review_calls == 0:
                self.review_calls += 1
                raise ReviewRequestTimeout("review timed out")
            return super().review_report(**kwargs)

    fake = TimeoutThenPassAzure()
    orchestrator = make_v2_orchestrator(tmp_path, fake)
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
            },
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
async def test_create_run_accepts_required_context_and_v2_options(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
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
                    "max_targeted_rerun_runs": 2,
                    "max_full_rerun_runs": 1,
                    "max_llm_patch_runs": 3,
                    "max_verification_runs": 2,
                    "max_total_iterations": 8,
                    "max_total_tool_calls": 120,
                },
            },
        )

    assert create_response.status_code == 202


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allow_web_search", False),
        ("max_deep_research_runs", 2),
        ("max_no_progress_rounds", 2),
    ],
)
async def test_create_run_rejects_legacy_option_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
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
                "options": {field: value},
            },
        )

    assert create_response.status_code == 422


@pytest.mark.anyio
async def test_openapi_create_request_omits_context_and_exposes_v2_options() -> None:
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    schemas = response.json()["components"]["schemas"]
    assert schemas["CreateResearchRunRequest"]["additionalProperties"] is False
    assert "context_classification" not in schemas["CreateResearchRunRequest"].get("required", [])
    create_properties = schemas["CreateResearchRunRequest"]["properties"]
    assert "context_classification" not in create_properties
    option_properties = schemas["ResearchRunOptions"].get("properties", {})
    assert set(option_properties) == {
        "max_targeted_rerun_runs",
        "max_full_rerun_runs",
        "max_llm_patch_runs",
        "max_verification_runs",
        "max_total_iterations",
        "max_total_tool_calls",
    }
    assert "max_deep_research_runs" not in option_properties
    assert "max_no_progress_rounds" not in option_properties


@pytest.mark.anyio
async def test_human_review_api_does_not_require_reviewer_identity(tmp_path: Path) -> None:
    orchestrator = make_v2_orchestrator(
        tmp_path,
        V2FakeAzure(verdict=Verdict.HUMAN_REVIEW),
    )
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
            },
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
async def test_human_review_payload_includes_suggested_rerun_prompt(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(
        tmp_path,
        V2FakeAzure(verdict=Verdict.HUMAN_REVIEW),
    )
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
            },
        )
        run_id = create_response.json()["run_id"]
        run = orchestrator.collect_deep_research(run_id)
        orchestrator.repository.update_run(
            run.id,
            done_reason="review_route_needs_full_rerun",
        )

        payload_response = await client.get(f"/research-runs/{run_id}/human-review")

    assert payload_response.status_code == 200
    payload = payload_response.json()
    suggested = payload["suggested_rerun"]
    assert suggested["scope"] == "full_rerun"
    assert suggested["expected_output_kind"] == "complete_replacement_report"
    assert suggested["expected_run_no"] == 2
    assert suggested["query_policy"]["status"] == "allowed"
    assert "Original User Prompt" in suggested["prompt"]
    assert "Expected output: complete_replacement_report" in suggested["prompt"]
    assert "final report body only" in suggested["prompt"]
    assert "Do not return a full merged report." not in suggested["prompt"]
    assert "Rerun Policy" in suggested["prompt"]
    assert suggested["target_item_ids"]


@pytest.mark.anyio
async def test_resume_api_can_request_manual_chatgpt_full_rerun(
    tmp_path: Path,
) -> None:
    fake = V2FakeAzure(verdicts=[Verdict.HUMAN_REVIEW, Verdict.PASS])
    orchestrator = make_v2_orchestrator(tmp_path, fake)
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
            },
        )
        run_id = create_response.json()["run_id"]
        run = orchestrator.collect_deep_research(run_id)
        orchestrator.repository.update_run(
            run.id,
            done_reason="review_route_needs_full_rerun",
        )
        fake.submitted_prompts.clear()

        payload_response = await client.get(f"/research-runs/{run_id}/human-review")
        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={
                "action": HumanReviewAction.REQUEST_MANUAL_FULL_RERUN.value,
                "comment": "ChatGPTで手動実行します。",
            },
        )
        pending_payload_response = await client.get(f"/research-runs/{run_id}/human-review")
        pending = pending_payload_response.json()["pending_manual_rerun"]
        upload_response = await client.post(
            f"/research-runs/{run_id}/manual-rerun-result",
            data={
                "rerun_id": pending["rerun_id"],
                "report_text": "Complete replacement report from ChatGPT.",
            },
        )
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    assert payload_response.status_code == 200
    allowed_actions = payload_response.json()["allowed_actions"]
    assert HumanReviewAction.REQUEST_MANUAL_FULL_RERUN.value in allowed_actions
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "needs_human_review"
    assert resume_response.json()["done_reason"] == "manual_chatgpt_rerun_pending"
    assert fake.submitted_prompts == []
    assert pending["scope"] == "full_rerun"
    assert pending["expected_output_kind"] == "complete_replacement_report"
    assert pending["expected_run_no"] == 2
    assert "ChatGPTで手動実行します。" in pending["prompt"]
    assert upload_response.status_code == 200
    assert upload_response.json()["status"] == "completed"
    assert fake.review_calls == 2
    assert [attempt["source"] for attempt in audit_response.json()["attempts"]] == [
        "api",
        "manual_chatgpt_rerun",
    ]


@pytest.mark.anyio
async def test_manual_full_rerun_bypasses_api_full_rerun_limit(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(
        tmp_path,
        V2FakeAzure(verdict=Verdict.HUMAN_REVIEW),
    )
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
                "options": {"max_full_rerun_runs": 1},
            },
        )
        run_id = create_response.json()["run_id"]
        run = orchestrator.collect_deep_research(run_id)
        orchestrator.repository.update_run(
            run.id,
            full_rerun_runs=1,
            done_reason="max_full_rerun_runs_reached",
        )

        payload_response = await client.get(f"/research-runs/{run_id}/human-review")
        api_full_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={
                "action": HumanReviewAction.REQUEST_FULL_RERUN.value,
                "comment": None,
            },
        )
        manual_full_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={
                "action": HumanReviewAction.REQUEST_MANUAL_FULL_RERUN.value,
                "comment": "ChatGPTで全面作り直し",
            },
        )

    payload = payload_response.json()
    assert HumanReviewAction.REQUEST_FULL_RERUN.value not in payload["allowed_actions"]
    assert HumanReviewAction.REQUEST_MANUAL_FULL_RERUN.value in payload["allowed_actions"]
    states = {state["action"]: state for state in payload["action_states"]}
    assert states[HumanReviewAction.REQUEST_FULL_RERUN.value]["blocked_reason"] == (
        "max_full_rerun_runs_reached"
    )
    assert states[HumanReviewAction.REQUEST_MANUAL_FULL_RERUN.value]["allowed"] is True
    assert payload["suggested_rerun"]["scope"] == "full_rerun"
    assert payload["suggested_rerun"]["expected_output_kind"] == (
        "complete_replacement_report"
    )
    assert api_full_response.status_code == 409
    assert manual_full_response.status_code == 200
    assert manual_full_response.json()["done_reason"] == "manual_chatgpt_rerun_pending"


@pytest.mark.anyio
async def test_manual_targeted_rerun_bypasses_api_targeted_rerun_limit(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(
        tmp_path,
        V2FakeAzure(verdict=Verdict.HUMAN_REVIEW),
    )
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
                "options": {"max_targeted_rerun_runs": 1},
            },
        )
        run_id = create_response.json()["run_id"]
        run = orchestrator.collect_deep_research(run_id)
        orchestrator.repository.update_run(
            run.id,
            targeted_rerun_runs=1,
            done_reason="max_targeted_rerun_runs_reached",
        )

        payload_response = await client.get(f"/research-runs/{run_id}/human-review")
        api_targeted_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={
                "action": HumanReviewAction.REQUEST_TARGETED_RERUN.value,
                "comment": None,
            },
        )
        manual_targeted_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={
                "action": HumanReviewAction.REQUEST_MANUAL_TARGETED_RERUN.value,
                "comment": "ChatGPTで不足itemだけ補強",
            },
        )

    payload = payload_response.json()
    assert HumanReviewAction.REQUEST_TARGETED_RERUN.value not in payload["allowed_actions"]
    assert HumanReviewAction.REQUEST_MANUAL_TARGETED_RERUN.value in payload["allowed_actions"]
    states = {state["action"]: state for state in payload["action_states"]}
    assert states[HumanReviewAction.REQUEST_TARGETED_RERUN.value]["blocked_reason"] == (
        "max_targeted_rerun_runs_reached"
    )
    assert states[HumanReviewAction.REQUEST_MANUAL_TARGETED_RERUN.value]["allowed"] is True
    assert api_targeted_response.status_code == 409
    assert manual_targeted_response.status_code == 200
    assert manual_targeted_response.json()["done_reason"] == "manual_chatgpt_rerun_pending"


@pytest.mark.anyio
async def test_resume_api_allows_rerun_actions_after_no_progress_count(
    tmp_path: Path,
) -> None:
    orchestrator = make_v2_orchestrator(
        tmp_path,
        V2FakeAzure(verdict=Verdict.HUMAN_REVIEW),
    )
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
            },
        )
        run_id = create_response.json()["run_id"]
        needs_human = orchestrator.collect_deep_research(run_id)
        orchestrator.repository.update_run(needs_human.id, no_progress_count=2)

        payload_response = await client.get(f"/research-runs/{run_id}/human-review")
        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={
                "action": HumanReviewAction.REQUEST_TARGETED_RERUN.value,
                "comment": "不足分だけ再調査してください。",
            },
        )

    allowed_actions = payload_response.json()["allowed_actions"]
    assert HumanReviewAction.REQUEST_TARGETED_RERUN.value in allowed_actions
    assert HumanReviewAction.REQUEST_FULL_RERUN.value in allowed_actions
    assert HumanReviewAction.REQUEST_ITEM_REVISION.value in allowed_actions
    assert HumanReviewAction.REQUEST_LLM_PATCH.value not in allowed_actions
    assert HumanReviewAction.REQUEST_VERIFICATION.value not in allowed_actions
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == RunStatus.WAITING_DEEP_RESEARCH.value


@pytest.mark.anyio
async def test_resume_api_rejects_body_reviewer_id(tmp_path: Path) -> None:
    orchestrator = make_v2_orchestrator(
        tmp_path,
        V2FakeAzure(verdict=Verdict.HUMAN_REVIEW),
    )
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
            },
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
    orchestrator = make_v2_orchestrator(tmp_path, V2FakeAzure())
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
            },
        )
        run_id = create_response.json()["run_id"]
        orchestrator.collect_deep_research(run_id)

        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={"action": "approve"},
        )

    assert resume_response.status_code == 409


def test_create_request_defaults_options_without_context_classification() -> None:
    request = CreateResearchRunRequest(
        user_prompt="調査してください",
    )

    assert request.options.model_dump() == {
        "max_targeted_rerun_runs": None,
        "max_full_rerun_runs": None,
        "max_llm_patch_runs": None,
        "max_verification_runs": None,
        "max_total_iterations": None,
        "max_total_tool_calls": None,
    }
