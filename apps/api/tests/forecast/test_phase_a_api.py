from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

from api.config import Settings
from api.forecast.artifacts import ForecastArtifactStore
from api.forecast.dependencies import get_forecast_orchestrator
from api.forecast.probability.phase_a_v1 import compute_phase_a_estimates
from api.forecast.repository import ForecastRepository
from api.forecast.schemas import (
    FRAMING_ROUGH_QUESTION_MAX_LENGTH,
    ForecastFramingDraftRequest,
)
from api.forecast.service import ForecastOrchestrator
from api.main import create_app
from api.research.artifacts import ArtifactStore
from api.research.dependencies import get_research_orchestrator
from api.research.repository import ResearchRepository
from api.research.service import ResearchOrchestrator
from research_fakes import IntegrationFakeAzure


def _make_orchestrators(
    tmp_path: Path,
    fake: IntegrationFakeAzure,
) -> tuple[ForecastOrchestrator, ResearchOrchestrator]:
    settings = Settings(
        research_db_path=tmp_path / "phase-a.sqlite3",
        research_artifact_dir=tmp_path / "research-artifacts",
        forecast_artifact_dir=tmp_path / "forecast-artifacts",
        research_poller_enabled=False,
    )
    research = ResearchOrchestrator(
        settings=settings,
        repository=ResearchRepository(settings.research_db_path),
        artifacts=ArtifactStore(settings.research_artifact_dir),
        azure=cast(Any, fake),
    )
    forecast = ForecastOrchestrator(
        settings=settings,
        repository=ForecastRepository(settings.research_db_path),
        artifacts=ForecastArtifactStore(settings.forecast_artifact_dir),
        research_orchestrator=research,
    )
    return forecast, research


def _typed_code(response_json: dict[str, object]) -> str:
    detail = response_json["detail"]
    assert isinstance(detail, dict)
    code = cast(dict[str, object], detail)["code"]
    assert isinstance(code, str)
    return code


def _request_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _framing_draft_payload(
    *,
    clarifying_questions: list[dict[str, Any]] | None = None,
    confidence: float = 0.82,
) -> dict[str, Any]:
    return {
        "forecast_prompt": "Forecast whether AI agents will handle support tickets.",
        "question": "Will AI agents handle at least 30% of support tickets by 2029?",
        "resolution_criteria": (
            "Resolve Yes if public benchmark or vendor reports show AI agents "
            "handling at least 30% of support tickets by 2029; otherwise No."
        ),
        "resolution_sources": ["Public vendor reports", "Independent benchmark reports"],
        "target_population": "Customer support teams using AI agents",
        "unit_of_analysis": "Share of support tickets handled end-to-end",
        "decision_context": "Plan support automation roadmap.",
        "outcomes": ["Yes", "No"],
        "clarifying_questions": clarifying_questions or [],
        "confidence": confidence,
    }


@pytest.mark.anyio
async def test_framing_draft_route_order_and_happy_draft(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure(structured_parse_results=[_framing_draft_payload()])
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={
                "rough_question": (
                    "AI agents might handle 30% of support tickets by 2029."
                ),
                "locale": "en",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["question"].startswith("Will AI agents")
    assert body["ready_to_create"] is True
    assert body["create_payload"]["question"] == body["draft"]["question"]
    assert body["model"] == fake.reviewer_deployment
    assert body["response_id"] == "resp_structured_1"
    assert fake.structured_parse_calls[0]["tool_profile"] == "synthesis"
    assert fake.structured_parse_calls[0]["policy_decision_id"] is None
    assert fake.structured_parse_calls[0]["vector_store_ids"] is None


@pytest.mark.anyio
async def test_framing_draft_accepts_long_rough_question(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure(structured_parse_results=[_framing_draft_payload()])
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research
    long_question = "Forecast planning premise. " * 240
    assert 5000 < len(long_question) < FRAMING_ROUGH_QUESTION_MAX_LENGTH

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={"rough_question": long_question},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ready_to_create"] is True
    assert fake.structured_parse_calls[0]["model"] == fake.reviewer_deployment


@pytest.mark.anyio
async def test_framing_draft_answers_can_make_required_questions_ready(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure(
        structured_parse_results=[
            _framing_draft_payload(
                clarifying_questions=[
                    {
                        "question_id": "deadline",
                        "label": "Resolution deadline",
                        "prompt": "What date should the forecast resolve against?",
                        "why_needed": "The forecast needs a concrete horizon.",
                        "answer_type": "date",
                        "required": True,
                        "options": [],
                    }
                ],
                confidence=0.64,
            )
        ]
    )
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={
                "rough_question": "Will AI agents handle many support tickets?",
                "answers": [
                    {
                        "question_id": "deadline",
                        "answer": "Resolve against public data available by 2029-12-31.",
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ready_to_create"] is True
    assert body["warnings"] == []
    assert body["create_payload"]["outcomes"] == ["Yes", "No"]


@pytest.mark.anyio
async def test_framing_draft_blocks_sensitive_inputs_before_llm(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={
                "rough_question": "Will internal project codename Phoenix launch?",
                "answers": [{"question_id": "scope", "answer": "Public customers"}],
            },
        )

    assert response.status_code == 409
    body = response.json()
    assert _typed_code(body) == "policy_requires_revision"
    assert "Phoenix" not in json.dumps(body)
    assert fake.structured_parse_calls == []


@pytest.mark.anyio
async def test_framing_draft_idempotency_replays_conflicts_and_in_progress(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure(structured_parse_results=[_framing_draft_payload()])
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        payload = {
            "rough_question": "Will idempotent framing draft requests replay?",
            "locale": "en",
        }
        first = await client.post(
            "/forecasts/framing-drafts",
            headers={"Idempotency-Key": "framing-replay"},
            json=payload,
        )
        replay = await client.post(
            "/forecasts/framing-drafts",
            headers={"Idempotency-Key": "framing-replay"},
            json=payload,
        )
        conflict = await client.post(
            "/forecasts/framing-drafts",
            headers={"Idempotency-Key": "framing-replay"},
            json={**payload, "rough_question": "Different framing question"},
        )

        in_progress_request = ForecastFramingDraftRequest(
            rough_question="Will in-progress framing idempotency block duplicates?"
        )
        existing = forecast.repository.reserve_idempotency_record(
            command_scope="forecast:framing_draft",
            resource_id="",
            idempotency_key="framing-in-progress",
            request_hash=_request_hash(in_progress_request.model_dump(mode="json")),
        )
        assert existing is None
        duplicate = await client.post(
            "/forecasts/framing-drafts",
            headers={"Idempotency-Key": "framing-in-progress"},
            json={"rough_question": in_progress_request.rough_question},
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["response_id"] == first.json()["response_id"]
    assert len(fake.structured_parse_calls) == 1
    assert conflict.status_code == 409
    assert _typed_code(conflict.json()) == "idempotency_conflict"
    assert duplicate.status_code == 409
    assert _typed_code(duplicate.json()) == "idempotency_in_progress"


@pytest.mark.anyio
async def test_framing_draft_parse_text_fallback_and_invalid_response(
    tmp_path: Path,
) -> None:
    fallback_fake = IntegrationFakeAzure(
        structured_parse_results=[
            "Here is the draft JSON:\n"
            + json.dumps(_framing_draft_payload(), ensure_ascii=False)
        ]
    )
    fallback_forecast, fallback_research = _make_orchestrators(tmp_path / "ok", fallback_fake)
    fallback_app = create_app()
    fallback_app.dependency_overrides[get_forecast_orchestrator] = (
        lambda: fallback_forecast
    )
    fallback_app.dependency_overrides[get_research_orchestrator] = (
        lambda: fallback_research
    )

    async with AsyncClient(
        transport=ASGITransport(app=fallback_app),
        base_url="http://testserver",
    ) as client:
        fallback = await client.post(
            "/forecasts/framing-drafts",
            json={"rough_question": "Will fallback JSON parse correctly?"},
        )

    invalid_fake = IntegrationFakeAzure(
        structured_parse_results=['{"forecast_prompt": "missing required fields"}']
    )
    invalid_forecast, invalid_research = _make_orchestrators(
        tmp_path / "invalid",
        invalid_fake,
    )
    invalid_app = create_app()
    invalid_app.dependency_overrides[get_forecast_orchestrator] = (
        lambda: invalid_forecast
    )
    invalid_app.dependency_overrides[get_research_orchestrator] = (
        lambda: invalid_research
    )

    async with AsyncClient(
        transport=ASGITransport(app=invalid_app),
        base_url="http://testserver",
    ) as client:
        invalid = await client.post(
            "/forecasts/framing-drafts",
            json={"rough_question": "Will invalid JSON schema return a typed error?"},
        )

    assert fallback.status_code == 200
    assert fallback.json()["draft"]["confidence"] == 0.82
    assert invalid.status_code == 502
    assert _typed_code(invalid.json()) == "framing_draft_invalid_response"


@pytest.mark.anyio
async def test_framing_draft_runtime_failure_returns_unavailable(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure(
        structured_parse_raises=RuntimeError("reviewer unavailable")
    )
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={"rough_question": "Will unavailable reviewer return 503?"},
        )

    assert response.status_code == 503
    assert _typed_code(response.json()) == "framing_draft_unavailable"


@pytest.mark.anyio
async def test_phase_a_forecast_lifecycle_and_forecast_research_mode(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            headers={"Idempotency-Key": "forecast-create-1"},
            json={
                "question": "Will AI agents handle 30% of support tickets by 2029?",
                "resolution_criteria": "Resolve from public vendor and benchmark reports.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        blocked = await client.post(f"/forecasts/{forecast_id}/research-packs", json={})
        assert blocked.status_code == 409
        assert _typed_code(blocked.json()) == "framing_not_approved"

        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing", "comment": "framing ok"},
        )
        assert approve.status_code == 200
        assert approve.json()["approved_framing_version"] == 1

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            headers={"Idempotency-Key": "pack-1"},
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200
        pack_json = pack.json()
        run_id = pack_json["research_run_id"]
        assert pack_json["policy_decision_id"]
        assert fake.submit_calls[-1]["tool_profile"] == "public"
        assert fake.submit_calls[-1]["background"] is False
        assert fake.submit_calls[-1]["policy_decision_id"] == pack_json["policy_decision_id"]

        completed_run = research.collect_deep_research(run_id)
        assert completed_run.status == "completed"
        assert completed_run.done_reason == "forecast_raw_report_collected"
        assert fake.review_calls == []

        delete_response = await client.delete(f"/research-runs/{run_id}")
        assert delete_response.status_code == 409
        assert "forecast_linked_research_run" in str(delete_response.json()["detail"])

        evidence = await client.post(f"/forecasts/{forecast_id}/evidence/extract")
        assert evidence.status_code == 200
        assert evidence.json()["sources"]
        assert evidence.json()["claims"]

        scenarios = await client.post(f"/forecasts/{forecast_id}/scenarios/generate")
        assert scenarios.status_code == 200
        assert all(item["outcome_id"] for item in scenarios.json()["scenarios"])

        compute_without_link_approval = await client.post(
            f"/forecasts/{forecast_id}/probabilities/compute"
        )
        assert compute_without_link_approval.status_code == 409
        assert _typed_code(compute_without_link_approval.json()) == (
            "claim_targets_not_approved"
        )

        link_approval = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_claim_target_links"},
        )
        assert link_approval.status_code == 200

        estimate = await client.post(f"/forecasts/{forecast_id}/probabilities/compute")
        assert estimate.status_code == 200
        estimate_json = cast(dict[str, Any], estimate.json())
        assert estimate_json["engine_version"] == "phase_a_v1"
        assert estimate_json["random_seed"] == 0
        assert len(estimate_json["input_snapshot_hash"]) == 64
        estimates = cast(list[dict[str, Any]], estimate_json["estimates"])
        total_probability = sum(float(item["final_probability"]) for item in estimates)
        assert abs(total_probability - 1.0) < 1e-12
        assert any(
            item["components"]["cross_impact_engine"] == "none" for item in estimates
        )

        replay = await client.post(f"/forecasts/{forecast_id}/probabilities/compute")
        assert replay.status_code == 200
        assert replay.json()["estimate_set_id"] == estimate_json["estimate_set_id"]

        commit_without_approval = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "estimate_set_id": estimate_json["estimate_set_id"],
                "expected_input_snapshot_hash": estimate_json["input_snapshot_hash"],
            },
        )
        assert commit_without_approval.status_code == 409
        assert _typed_code(commit_without_approval.json()) == "approval_required"

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
        assert commit.json()["snapshot_artifact_path"]

        current_estimate = await client.get(f"/forecasts/{forecast_id}/estimate-set")
        assert current_estimate.status_code == 200
        assert current_estimate.json()["estimate_set_id"] == estimate_json["estimate_set_id"]

        compute_after_commit = await client.post(
            f"/forecasts/{forecast_id}/probabilities/compute"
        )
        assert compute_after_commit.status_code == 409
        assert _typed_code(compute_after_commit.json()) == "estimate_set_already_committed"

        outcome_id = estimate_json["estimates"][0]["target_id"]
        resolve = await client.post(
            f"/forecasts/{forecast_id}/resolve",
            json={"outcome_id": outcome_id, "resolution_notes": "resolved"},
        )
        assert resolve.status_code == 200
        assert resolve.json()["scorer_version"] == "phase_a_scorer_v1"
        assert resolve.json()["multiclass_brier"] >= 0
        assert resolve.json()["log_score"] >= 0

        second_resolve = await client.post(
            f"/forecasts/{forecast_id}/resolve",
            json={"outcome_id": outcome_id},
        )
        assert second_resolve.status_code == 409
        assert _typed_code(second_resolve.json()) == "forecast_already_resolved"

        audit = await client.get(f"/forecasts/{forecast_id}/audit")
        assert audit.status_code == 200
        event_types = [event["event_type"] for event in audit.json()["events"]]
        assert "version_committed" in event_types
        assert "forecast_resolved" in event_types


@pytest.mark.anyio
async def test_forecast_idempotency_replays_and_conflicts(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        payload = {
            "question": "Will idempotency replay this forecast?",
            "resolution_criteria": "Resolve from public sources.",
            "outcomes": ["Yes", "No"],
        }
        first = await client.post(
            "/forecasts",
            headers={"Idempotency-Key": "create-replay"},
            json=payload,
        )
        replay = await client.post(
            "/forecasts",
            headers={"Idempotency-Key": "create-replay"},
            json=payload,
        )
        conflict = await client.post(
            "/forecasts",
            headers={"Idempotency-Key": "create-replay"},
            json={**payload, "question": "Different body"},
        )

        assert first.status_code == 202
        assert replay.status_code == 202
        assert replay.json()["forecast_id"] == first.json()["forecast_id"]
        assert conflict.status_code == 409
        assert _typed_code(conflict.json()) == "idempotency_conflict"

        listed = await client.get("/forecasts")
        assert listed.status_code == 200
        assert [
            item["forecast_id"] for item in listed.json()
        ] == [first.json()["forecast_id"]]

        forecast_id = first.json()["forecast_id"]
        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            headers={"Idempotency-Key": "approve-framing-replay"},
            json={"action": "approve_framing"},
        )
        approve_replay = await client.post(
            f"/forecasts/{forecast_id}/review",
            headers={"Idempotency-Key": "approve-framing-replay"},
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200
        assert approve_replay.status_code == 200

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            headers={"Idempotency-Key": "pack-replay"},
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        pack_replay = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            headers={"Idempotency-Key": "pack-replay"},
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200
        assert pack_replay.status_code == 200
        assert pack_replay.json()["pack_id"] == pack.json()["pack_id"]
        assert len(fake.submit_calls) == 1


@pytest.mark.anyio
async def test_forecast_idempotency_in_progress_blocks_duplicate_side_effects(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will in-progress idempotency block duplicates?",
                "resolution_criteria": "Resolve from public sources.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]
        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        payload = {"pack_role": "current_state", "tool_profile": "public"}
        canonical_payload = {**payload, "max_tool_calls": 40}
        existing = forecast.repository.reserve_idempotency_record(
            command_scope="forecast:research_pack",
            resource_id=forecast_id,
            idempotency_key="pack-in-progress",
            request_hash=_request_hash(canonical_payload),
        )
        assert existing is None

        duplicate = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            headers={"Idempotency-Key": "pack-in-progress"},
            json=payload,
        )

        assert duplicate.status_code == 409
        assert _typed_code(duplicate.json()) == "idempotency_in_progress"
        assert fake.submit_calls == []


@pytest.mark.anyio
async def test_forecast_disabled_returns_typed_conflict(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    forecast.settings = forecast.settings.model_copy(update={"forecast_enabled": False})
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts",
            json={
                "question": "Will disabled forecasts reject mutations?",
                "resolution_criteria": "Resolve from public sources.",
                "outcomes": ["Yes", "No"],
            },
        )

    assert response.status_code == 409
    assert _typed_code(response.json()) == "forecast_disabled"


def test_forecast_audit_events_are_append_only(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure()
    forecast, _research = _make_orchestrators(tmp_path, fake)
    created = forecast.create_forecast(
        request=forecast_create_request(),
        idempotency_key=None,
    )
    forecast_id = created.forecast_id

    with forecast.repository.connect() as connection:
        row = connection.execute(
            "SELECT event_id FROM forecast_audit_events WHERE forecast_id = ? LIMIT 1",
            (str(forecast_id),),
        ).fetchone()
        assert row is not None
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "UPDATE forecast_audit_events SET event_type = 'mutated' WHERE event_id = ?",
                (row["event_id"],),
            )
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "DELETE FROM forecast_audit_events WHERE event_id = ?",
                (row["event_id"],),
            )


def test_phase_a_softmax_exact_reference_case() -> None:
    snapshot = {
        "outcomes": [
            {"outcome_id": "yes"},
            {"outcome_id": "no"},
        ],
        "claims": [
            {
                "claim_id": "c1",
                "evidence_strength": 1.0,
                "reliability_score": 1.0,
                "cluster_id": "cluster-a",
                "independence_group": "group-a",
            },
            {
                "claim_id": "c2",
                "evidence_strength": 1.0,
                "reliability_score": 1.0,
                "cluster_id": "cluster-b",
                "independence_group": "group-b",
            },
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": "yes",
                "direction": 1,
                "relevance_weight": 1.0,
            },
            {
                "claim_id": "c2",
                "target_kind": "outcome",
                "target_id": "no",
                "direction": -1,
                "relevance_weight": 1.0,
            },
        ],
    }

    estimates = compute_phase_a_estimates(snapshot=snapshot, epsilon_floor=0.0)

    actual_probability = float(estimates[0]["final_probability"])
    assert abs(actual_probability - 0.8807970779778823) < 1e-15


def forecast_create_request():
    from api.forecast.schemas import ForecastCreateRequest

    return ForecastCreateRequest(
        question="Will the market adopt AI agents?",
        resolution_criteria="Resolve from public sources.",
        outcomes=["Yes", "No"],
    )
