from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from api.config import Settings
from api.forecast.artifacts import ForecastArtifactStore
from api.forecast.dependencies import get_forecast_orchestrator
from api.forecast.repository import ForecastRepository
from api.forecast.service import ForecastOrchestrator
from api.main import create_app
from api.research.artifacts import ArtifactStore
from api.research.dependencies import get_research_orchestrator
from api.research.repository import ResearchRepository
from api.research.schemas import utc_now
from api.research.service import ResearchOrchestrator
from research_fakes import IntegrationFakeAzure


def _make_orchestrators(tmp_path: Path) -> tuple[ForecastOrchestrator, ResearchOrchestrator]:
    settings = Settings(
        research_db_path=tmp_path / "phase-c.sqlite3",
        research_artifact_dir=tmp_path / "research-artifacts",
        forecast_artifact_dir=tmp_path / "forecast-artifacts",
        research_poller_enabled=False,
    )
    research = ResearchOrchestrator(
        settings=settings,
        repository=ResearchRepository(settings.research_db_path),
        artifacts=ArtifactStore(settings.research_artifact_dir),
        azure=cast(Any, IntegrationFakeAzure()),
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


def _detail_contains(response_json: dict[str, object], text: str) -> bool:
    return text in json.dumps(response_json["detail"], ensure_ascii=False)


def _projection_create_payload() -> dict[str, object]:
    return {
        "forecast_mode": "scenario_projection",
        "question": "What will agent adoption look like by 2035?",
        "resolution_criteria": "Resolve from public market reports.",
        "projection_dimensions": [
            {
                "metric_id": "agent_adoption",
                "label": "Agent adoption",
                "unit": "%",
                "value_type": "percentage",
                "baseline_year": 2026,
                "baseline_value": 10,
                "horizons": [2030, 2035],
            }
        ],
    }


def _seed_projection_evidence(
    repository: ForecastRepository,
    forecast_id: str,
    *,
    data_classification: str = "public",
) -> None:
    now = utc_now().isoformat()
    source_id = str(uuid4())
    claim_id = str(uuid4())
    with repository.connect() as connection:
        connection.execute(
            """
            INSERT INTO forecast_sources (
                source_id, forecast_id, title, publisher, url, source_type,
                source_classification, data_classification, origin_tool_profile,
                reliability_score, metadata_json, created_at
            )
            VALUES (?, ?, 'Projection source', 'Test', NULL, 'manual',
                    ?, ?, 'public', 0.8, '{}', ?)
            """,
            (source_id, forecast_id, data_classification, data_classification, now),
        )
        connection.execute(
            """
            INSERT INTO forecast_claims (
                claim_id, forecast_id, text, claim_type, polarity,
                evidence_strength, reliability_score, cluster_id,
                independence_group, source_classification, data_classification,
                origin_tool_profile, extraction_model, extraction_prompt_version,
                review_status, created_at
            )
            VALUES (?, ?, 'Adoption signal is strengthening.', 'current_state', 1,
                    0.7, 0.8, 'cluster-1', 'group-1', ?, ?, 'public',
                    'test', 'phase_c_test', 'approved', ?)
            """,
            (claim_id, forecast_id, data_classification, data_classification, now),
        )
        connection.execute(
            """
            INSERT INTO forecast_claim_source_links (claim_id, source_id)
            VALUES (?, ?)
            """,
            (claim_id, source_id),
        )
        connection.execute(
            """
            UPDATE forecast_forecasts
            SET status = 'evidence_ready', updated_at = ?
            WHERE id = ?
            """,
            (now, forecast_id),
        )


@pytest.mark.anyio
async def test_create_rejects_discrete_projection_dimensions(
    tmp_path: Path,
) -> None:
    forecast, research = _make_orchestrators(tmp_path)
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
                "forecast_mode": "discrete_outcome",
                "question": "Will agents handle support tickets by 2030?",
                "resolution_criteria": "Resolve from public benchmarks.",
                "outcomes": ["Yes", "No"],
                "projection_dimensions": [
                    {
                        "metric_id": "ticket_share",
                        "label": "Ticket share",
                        "unit": "%",
                        "value_type": "percentage",
                        "baseline_year": 2026,
                        "baseline_value": 10,
                        "horizons": [2030],
                    }
                ],
            },
        )

    assert response.status_code == 422
    assert _detail_contains(response.json(), "forecast_mode_payload_mismatch")


@pytest.mark.anyio
async def test_mode_specific_endpoint_guards(tmp_path: Path) -> None:
    forecast, research = _make_orchestrators(tmp_path)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        discrete = await client.post(
            "/forecasts",
            json={
                "forecast_mode": "discrete_outcome",
                "question": "Will agents handle support tickets by 2030?",
                "resolution_criteria": "Resolve from public benchmarks.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert discrete.status_code == 202
        projection = await client.post("/forecasts", json=_projection_create_payload())
        assert projection.status_code == 202

        projection_on_discrete = await client.post(
            f"/forecasts/{discrete.json()['forecast_id']}/projections/compute"
        )
        estimate_on_projection = await client.get(
            f"/forecasts/{projection.json()['forecast_id']}/estimate-set"
        )

    assert projection_on_discrete.status_code == 409
    assert _typed_code(projection_on_discrete.json()) == "forecast_mode_mismatch"
    assert estimate_on_projection.status_code == 409
    assert _typed_code(estimate_on_projection.json()) == "forecast_mode_mismatch"


@pytest.mark.anyio
async def test_projection_manual_research_pack_uses_dimensions_without_outcomes(
    tmp_path: Path,
) -> None:
    forecast, research = _make_orchestrators(tmp_path)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post("/forecasts", json=_projection_create_payload())
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]
        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        prompt = await client.get(
            f"/forecasts/{forecast_id}/research-packs/manual-prompt"
        )
        assert prompt.status_code == 200
        prompt_json = prompt.json()
        imported = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            data={
                "prompt_sha256": prompt_json["prompt_sha256"],
                "report_text": (
                    "Public report: adoption baselines, recent market signals, "
                    "and counterpoints for agent adoption."
                ),
            },
        )

    assert "Projection dimension metadata:" in prompt_json["prompt"]
    assert "agent_adoption" in prompt_json["prompt"]
    assert imported.status_code == 200
    assert imported.json()["status"] == "completed"


@pytest.mark.anyio
async def test_commit_requires_exactly_one_set_id(tmp_path: Path) -> None:
    forecast, research = _make_orchestrators(tmp_path)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post("/forecasts", json=_projection_create_payload())
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        neither = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={"expected_input_snapshot_hash": "hash"},
        )
        both = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "estimate_set_id": str(uuid4()),
                "projection_set_id": str(uuid4()),
                "expected_input_snapshot_hash": "hash",
            },
        )

    assert neither.status_code == 422
    assert both.status_code == 422


@pytest.mark.anyio
async def test_projection_commit_missing_set_returns_404_before_approval_check(
    tmp_path: Path,
) -> None:
    forecast, research = _make_orchestrators(tmp_path)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post("/forecasts", json=_projection_create_payload())
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]
        _seed_projection_evidence(forecast.repository, forecast_id)

        computed = await client.post(f"/forecasts/{forecast_id}/projections/compute")
        assert computed.status_code == 200
        missing = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "projection_set_id": str(uuid4()),
                "expected_input_snapshot_hash": computed.json()["input_snapshot_hash"],
            },
        )

    assert missing.status_code == 404


@pytest.mark.anyio
async def test_projection_forecast_has_no_outcome_fallback_and_rejects_phase_b(
    tmp_path: Path,
) -> None:
    forecast, research = _make_orchestrators(tmp_path)
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
                "forecast_mode": "scenario_projection",
                "question": "What will agent adoption look like by 2035?",
                "resolution_criteria": "Resolve from public market reports.",
                "projection_dimensions": [
                    {
                        "metric_id": "agent_adoption",
                        "label": "Agent adoption",
                        "unit": "%",
                        "value_type": "percentage",
                        "baseline_year": 2026,
                        "baseline_value": 10,
                        "horizons": [2030, 2035],
                    }
                ],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        detail = await client.get(f"/forecasts/{forecast_id}")
        phase_b = await client.post(f"/forecasts/{forecast_id}/probabilities/compute")

    assert detail.status_code == 200
    assert detail.json()["forecast_mode"] == "scenario_projection"
    assert detail.json()["outcomes"] == []
    assert len(detail.json()["projection_dimensions"]) == 1
    assert phase_b.status_code == 409
    assert _typed_code(phase_b.json()) == "forecast_mode_mismatch"


@pytest.mark.anyio
async def test_projection_compute_approve_and_commit(tmp_path: Path) -> None:
    forecast, research = _make_orchestrators(tmp_path)
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
                "forecast_mode": "scenario_projection",
                "question": "What will agent adoption look like by 2035?",
                "resolution_criteria": "Resolve from public market reports.",
                "projection_dimensions": [
                    {
                        "metric_id": "agent_adoption",
                        "label": "Agent adoption",
                        "unit": "%",
                        "value_type": "percentage",
                        "baseline_year": 2026,
                        "baseline_value": 10,
                        "horizons": [2035],
                    }
                ],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]
        _seed_projection_evidence(forecast.repository, forecast_id)

        computed = await client.post(f"/forecasts/{forecast_id}/projections/compute")
        assert computed.status_code == 200
        projection = computed.json()

        probabilities = [scenario["probability"] for scenario in projection["scenarios"]]
        assert abs(sum(probabilities) - 1.0) <= 1e-9
        assert any(scenario["residual_flag"] for scenario in projection["scenarios"])
        assert projection["composites"][0]["mixture_components"]

        commit_without_approval = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "projection_set_id": projection["projection_set_id"],
                "expected_input_snapshot_hash": projection["input_snapshot_hash"],
            },
        )
        assert commit_without_approval.status_code == 409
        assert _typed_code(commit_without_approval.json()) == "approval_required"

        approve = await client.post(
            f"/forecasts/{forecast_id}/projections/{projection['projection_set_id']}/approve"
        )
        assert approve.status_code == 200

        committed = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "projection_set_id": projection["projection_set_id"],
                "expected_input_snapshot_hash": projection["input_snapshot_hash"],
            },
        )
        assert committed.status_code == 200
        assert committed.json()["version_kind"] == "projection"
        assert committed.json()["estimate_set_id"] is None
        assert committed.json()["projection_set_id"] == projection["projection_set_id"]
        recommitted = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "projection_set_id": projection["projection_set_id"],
                "expected_input_snapshot_hash": projection["input_snapshot_hash"],
            },
        )
        assert recommitted.status_code == 409
        assert _typed_code(recommitted.json()) == "projection_set_already_committed"

    with forecast.repository.connect() as connection:
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    assert violations == []


@pytest.mark.anyio
async def test_public_projection_commit_rejects_non_public_evidence(
    tmp_path: Path,
) -> None:
    forecast, research = _make_orchestrators(tmp_path)
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
                "forecast_mode": "scenario_projection",
                "question": "What will agent adoption look like by 2035?",
                "resolution_criteria": "Resolve from public market reports.",
                "projection_dimensions": [
                    {
                        "metric_id": "agent_adoption",
                        "label": "Agent adoption",
                        "unit": "%",
                        "value_type": "percentage",
                        "baseline_year": 2026,
                        "baseline_value": 10,
                        "horizons": [2035],
                    }
                ],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]
        _seed_projection_evidence(
            forecast.repository,
            forecast_id,
            data_classification="internal",
        )

        computed = await client.post(f"/forecasts/{forecast_id}/projections/compute")
        assert computed.status_code == 200
        projection = computed.json()
        approve = await client.post(
            f"/forecasts/{forecast_id}/projections/{projection['projection_set_id']}/approve"
        )
        assert approve.status_code == 200
        committed = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "projection_set_id": projection["projection_set_id"],
                "expected_input_snapshot_hash": projection["input_snapshot_hash"],
            },
        )

    assert committed.status_code == 409
    assert _typed_code(committed.json()) == "classification_mismatch"
