from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from api.research.poller import ResearchPoller
from conftest import ForecastResearchIntegrationStack
from research_fakes import IntegrationFakeAzure


def _typed_code(response_json: dict[str, Any]) -> str:
    detail = response_json["detail"]
    assert isinstance(detail, dict)
    code = cast(dict[str, Any], detail)["code"]
    assert isinstance(code, str)
    return code


async def _create_approved_forecast(
    client: AsyncClient,
    *,
    question: str = "Will AI agents handle 30% of support tickets by 2029?",
    resolution_criteria: str = "Resolve from public vendor and benchmark reports.",
) -> UUID:
    create = await client.post(
        "/forecasts",
        json={
            "question": question,
            "resolution_criteria": resolution_criteria,
            "outcomes": ["Yes", "No"],
        },
    )
    assert create.status_code == 202
    forecast_id = UUID(str(cast(dict[str, Any], create.json())["forecast_id"]))

    approve = await client.post(
        f"/forecasts/{forecast_id}/review",
        json={"action": "approve_framing", "comment": "framing ok"},
    )
    assert approve.status_code == 200
    assert cast(dict[str, Any], approve.json())["approved_framing_version"] == 1
    return forecast_id


def _forecast_origin_run_count(stack: ForecastResearchIntegrationStack) -> int:
    with stack.research.repository.connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM research_runs
            WHERE run_origin = 'forecast'
            """,
        ).fetchone()
    assert row is not None
    return int(row["count"])


def _policy_decision_rows(
    stack: ForecastResearchIntegrationStack,
    forecast_id: UUID,
) -> list[dict[str, Any]]:
    with stack.forecast.repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM forecast_policy_decisions
            WHERE forecast_id = ?
            ORDER BY created_at
            """,
            (str(forecast_id),),
        ).fetchall()
    return [dict(row) for row in rows]


@pytest.mark.integration
@pytest.mark.anyio
async def test_phase_a_forecast_research_pack_completes_through_poller(
    forecast_research_integration_stack: ForecastResearchIntegrationStack,
) -> None:
    stack = forecast_research_integration_stack

    async with AsyncClient(
        transport=ASGITransport(app=stack.app),
        base_url="http://testserver",
    ) as client:
        forecast_id = await _create_approved_forecast(client)

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            headers={"Idempotency-Key": "pack-1"},
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200
        pack_json = cast(dict[str, Any], pack.json())
        run_id = UUID(str(pack_json["research_run_id"]))
        policy_decision_id = str(pack_json["policy_decision_id"])
        assert pack_json["status"] == "running"
        assert pack_json["pack_role"] == "current_state"
        assert pack_json["tool_profile"] == "public"
        assert policy_decision_id
        assert stack.fake_azure.submit_calls[-1]["tool_profile"] == "public"
        assert stack.fake_azure.submit_calls[-1]["background"] is False
        assert stack.fake_azure.submit_calls[-1]["policy_decision_id"] == policy_decision_id

        policy_rows = _policy_decision_rows(stack, forecast_id)
        assert len(policy_rows) == 1
        assert policy_rows[0]["policy_decision_id"] == policy_decision_id
        assert policy_rows[0]["profile"] == "public"
        assert policy_rows[0]["status"] == "allowed"
        assert len(str(policy_rows[0]["prompt_hash"])) == 64
        pack_rows = stack.forecast.repository.list_packs(forecast_id)
        assert len(pack_rows) == 1
        assert pack_rows[0]["research_run_id"] == str(run_id)
        assert pack_rows[0]["status"] == "running"
        research_run = stack.research.repository.get_run(run_id)
        assert research_run.run_origin == "forecast"
        assert _forecast_origin_run_count(stack) == 1

        delete = await client.delete(f"/research-runs/{run_id}")
        assert delete.status_code == 409
        assert _typed_code(cast(dict[str, Any], delete.json())) == (
            "forecast_linked_research_run"
        )

        poller = ResearchPoller(orchestrator=stack.research, interval_seconds=0.01)
        await poller.tick()
        assert stack.fake_azure.review_calls == []
        assert stack.research.repository.get_reviews(run_id) == []

        detail_after_poll = await client.get(f"/forecasts/{forecast_id}")
        assert detail_after_poll.status_code == 200
        detail_after_poll_json = cast(dict[str, Any], detail_after_poll.json())
        pack_detail = cast(dict[str, Any], detail_after_poll_json["current_research_pack"])
        assert detail_after_poll_json["current_research_pack_status"] == "completed"
        assert pack_detail["pack_status"] == "completed"
        assert pack_detail["effective_status"] == "completed"
        assert pack_detail["research_run_status"] == "completed"
        assert pack_detail["done_reason"] == "forecast_raw_report_collected"
        assert pack_detail["needs_human_review"] is False

        completed_run = stack.research.repository.get_run(run_id)
        assert completed_run.status.value == "completed"
        assert completed_run.deep_research_status == "completed"
        assert completed_run.done_reason == "forecast_raw_report_collected"
        assert completed_run.report
        assert completed_run.final_report
        research_artifacts = [
            attempt.raw_response_artifact_path
            for attempt in stack.research.repository.get_attempts(run_id)
            if attempt.raw_response_artifact_path is not None
        ]
        assert research_artifacts
        assert all(Path(path).is_file() for path in research_artifacts)

        evidence = await client.post(f"/forecasts/{forecast_id}/evidence/extract")
        assert evidence.status_code == 200
        evidence_json = cast(dict[str, Any], evidence.json())
        assert evidence_json["sources"]
        assert evidence_json["claims"]
        packs_after_evidence = stack.forecast.repository.list_packs(forecast_id)
        assert len(packs_after_evidence) == 1
        assert packs_after_evidence[0]["status"] == "completed"

        scenarios = await client.post(f"/forecasts/{forecast_id}/scenarios/generate")
        assert scenarios.status_code == 200
        scenario_json = cast(dict[str, Any], scenarios.json())
        assert cast(list[dict[str, Any]], scenario_json["scenarios"])

        link_approval = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_claim_target_links"},
        )
        assert link_approval.status_code == 200

        estimate = await client.post(f"/forecasts/{forecast_id}/probabilities/compute")
        assert estimate.status_code == 200
        estimate_json = cast(dict[str, Any], estimate.json())
        estimates = cast(list[dict[str, Any]], estimate_json["estimates"])
        assert abs(
            sum(float(item["final_probability"]) for item in estimates) - 1.0,
        ) < 1e-12

        version_approval = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={
                "action": "approve_phase_a_version",
                "estimate_set_id": estimate_json["estimate_set_id"],
            },
        )
        assert version_approval.status_code == 200

        commit = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "estimate_set_id": estimate_json["estimate_set_id"],
                "expected_input_snapshot_hash": estimate_json["input_snapshot_hash"],
            },
        )
        assert commit.status_code == 200
        commit_json = cast(dict[str, Any], commit.json())
        snapshot_artifact_path = str(commit_json["snapshot_artifact_path"])
        assert Path(snapshot_artifact_path).is_file()

        resolve = await client.post(
            f"/forecasts/{forecast_id}/resolve",
            json={
                "outcome_id": estimates[0]["target_id"],
                "resolution_notes": "resolved from integration evidence",
            },
        )
        assert resolve.status_code == 200
        resolve_json = cast(dict[str, Any], resolve.json())
        assert resolve_json["scorer_version"] == "phase_a_scorer_v1"

        audit = await client.get(f"/forecasts/{forecast_id}/audit")
        assert audit.status_code == 200
        audit_json = cast(dict[str, Any], audit.json())
        event_types = {
            str(event["event_type"])
            for event in cast(list[dict[str, Any]], audit_json["events"])
        }
        assert {
            "policy_decision_recorded",
            "research_pack_dispatched",
            "evidence_extracted",
            "probabilities_computed",
            "version_committed",
            "forecast_resolved",
        }.issubset(event_types)


@pytest.mark.integration
@pytest.mark.anyio
async def test_phase_a_policy_block_stops_before_research_creation(
    forecast_research_integration_stack: ForecastResearchIntegrationStack,
) -> None:
    stack = forecast_research_integration_stack

    async with AsyncClient(
        transport=ASGITransport(app=stack.app),
        base_url="http://testserver",
    ) as client:
        forecast_id = await _create_approved_forecast(
            client,
            question="Will an api_key be published in a public incident report?",
            resolution_criteria="Resolve from public incident reports only.",
        )

        blocked = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert blocked.status_code == 409
        blocked_json = cast(dict[str, Any], blocked.json())
        assert _typed_code(blocked_json) == "policy_blocked"
        blocked_detail = cast(dict[str, Any], blocked_json["detail"])
        blocked_details = cast(dict[str, Any], blocked_detail["details"])
        policy_decision_id = str(blocked_details["policy_decision_id"])

        assert stack.fake_azure.submit_calls == []
        assert stack.forecast.repository.list_packs(forecast_id) == []
        assert _forecast_origin_run_count(stack) == 0
        policy_rows = _policy_decision_rows(stack, forecast_id)
        assert len(policy_rows) == 1
        assert policy_rows[0]["policy_decision_id"] == policy_decision_id
        assert policy_rows[0]["profile"] == "public"
        assert policy_rows[0]["status"] == "blocked"
        assert policy_rows[0]["reason"]
        assert len(str(policy_rows[0]["prompt_hash"])) == 64

        detail = await client.get(f"/forecasts/{forecast_id}")
        assert detail.status_code == 200
        detail_json = cast(dict[str, Any], detail.json())
        assert detail_json["status"] == "framing_approved"
        assert detail_json["current_research_pack"] is None

        audit = await client.get(f"/forecasts/{forecast_id}/audit")
        assert audit.status_code == 200
        audit_json = cast(dict[str, Any], audit.json())
        event_types = [
            str(event["event_type"])
            for event in cast(list[dict[str, Any]], audit_json["events"])
        ]
        assert "policy_decision_recorded" in event_types
        policy_decisions = cast(list[dict[str, Any]], audit_json["policy_decisions"])
        assert len(policy_decisions) == 1
        assert policy_decisions[0]["status"] == "blocked"


@pytest.mark.integration
@pytest.mark.anyio
async def test_phase_a_remote_failed_research_surfaces_as_pack_not_completed(
    forecast_research_integration_stack_factory: Callable[
        [IntegrationFakeAzure | None],
        ForecastResearchIntegrationStack,
    ],
) -> None:
    fake = IntegrationFakeAzure(retrieve_statuses=["failed"])
    stack = forecast_research_integration_stack_factory(fake)

    async with AsyncClient(
        transport=ASGITransport(app=stack.app),
        base_url="http://testserver",
    ) as client:
        forecast_id = await _create_approved_forecast(client)

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200
        run_id = UUID(str(cast(dict[str, Any], pack.json())["research_run_id"]))

        poller = ResearchPoller(orchestrator=stack.research, interval_seconds=0.01)
        await poller.tick()

        assert stack.research.repository.get_run(run_id).run_origin == "forecast"

        detail = await client.get(f"/forecasts/{forecast_id}")
        assert detail.status_code == 200
        detail_json = cast(dict[str, Any], detail.json())
        pack_detail = cast(dict[str, Any], detail_json["current_research_pack"])
        assert pack_detail["research_run_id"] == str(run_id)
        assert detail_json["current_research_pack_status"] == "needs_human_review"
        assert pack_detail["pack_status"] == "needs_human_review"
        assert pack_detail["effective_status"] == "needs_human_review"
        assert pack_detail["research_run_status"] == "needs_human_review"
        assert pack_detail["done_reason"] == "deep_research_failed"
        assert pack_detail["needs_human_review"] is True

        pack_rows = stack.forecast.repository.list_packs(forecast_id)
        assert len(pack_rows) == 1
        assert pack_rows[0]["status"] == "needs_human_review"
        assert pack_rows[0]["report_artifact_hash"] is None

        evidence = await client.post(f"/forecasts/{forecast_id}/evidence/extract")
        assert evidence.status_code == 409
        assert _typed_code(cast(dict[str, Any], evidence.json())) == "pack_not_completed"
