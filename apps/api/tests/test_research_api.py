from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import create_app
from api.research.azure_responses import ReviewRequestTimeout
from api.research.dependencies import get_research_orchestrator
from api.research.schemas import (
    CreateResearchRunRequest,
    HumanReviewAction,
    ReviewResult,
    RunStatus,
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
        assert initial_status_json["deep_research_submitted_at"] is not None

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
        ) -> dict[str, object]:
            if self.submitted_prompts:
                self.submitted_prompts.append(prompt)
                self.submitted_max_tool_calls.append(max_tool_calls)
                raise RuntimeError("remote submit failed")
            return super().submit_deep_research(
                prompt=prompt,
                max_tool_calls=max_tool_calls,
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
