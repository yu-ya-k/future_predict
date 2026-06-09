from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from api.config import Settings
from api.forecast import probability
from api.forecast.artifacts import ForecastArtifactStore
from api.forecast.dependencies import get_forecast_orchestrator
from api.forecast.errors import ForecastConflict
from api.forecast.repository import ForecastRepository
from api.forecast.service import ForecastOrchestrator
from api.main import create_app
from api.research.artifacts import ArtifactStore
from api.research.dependencies import get_research_orchestrator
from api.research.repository import ResearchRepository
from api.research.schemas import RunStatus
from api.research.service import ResearchOrchestrator
from research_fakes import IntegrationFakeAzure


def _make_orchestrators(
    tmp_path: Path,
    fake: IntegrationFakeAzure,
) -> tuple[ForecastOrchestrator, ResearchOrchestrator]:
    settings = Settings(
        research_db_path=tmp_path / "phase-b.sqlite3",
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


async def _create_approved_forecast(client: AsyncClient) -> str:
    create = await client.post(
        "/forecasts",
        json={
            "question": "Will Phase B ship?",
            "resolution_criteria": "Resolve from public release notes.",
            "outcomes": ["Ship", "Slip"],
        },
    )
    assert create.status_code == 202
    forecast_id = create.json()["forecast_id"]
    approve = await client.post(
        f"/forecasts/{forecast_id}/review",
        json={"action": "approve_framing"},
    )
    assert approve.status_code == 200
    return str(forecast_id)


@pytest.mark.anyio
async def test_default_phase_b_pack_dispatch_creates_five_active_packs(
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
        forecast_id = await _create_approved_forecast(client)
        response = await client.post(f"/forecasts/{forecast_id}/research-packs/defaults")
        listed = await client.get(f"/forecasts/{forecast_id}/research-packs")

    assert response.status_code == 200
    body = response.json()
    assert [pack["pack_role"] for pack in body["packs"]] == [
        "current_state",
        "base_rate",
        "drivers",
        "counter_evidence",
        "signals",
    ]
    assert all(pack["is_active"] for pack in body["packs"])
    assert listed.status_code == 200
    assert len(listed.json()) == 5
    assert len(fake.submit_calls) == 5


@pytest.mark.anyio
async def test_default_phase_b_pack_dispatch_replays_idempotently(
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
        forecast_id = await _create_approved_forecast(client)
        first = await client.post(
            f"/forecasts/{forecast_id}/research-packs/defaults",
            headers={"Idempotency-Key": "phase-b-defaults-1"},
        )
        replay = await client.post(
            f"/forecasts/{forecast_id}/research-packs/defaults",
            headers={"Idempotency-Key": "phase-b-defaults-1"},
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    first_ids = [pack["pack_id"] for pack in first.json()["packs"]]
    replay_ids = [pack["pack_id"] for pack in replay.json()["packs"]]
    assert replay_ids == first_ids
    assert len(fake.submit_calls) == 5
    with forecast.repository.connect() as connection:
        assert (
            connection.execute(
                """
                SELECT COUNT(*) FROM forecast_research_packs
                WHERE forecast_id = ? AND is_active = 1
                """,
                (forecast_id,),
            ).fetchone()[0]
            == 5
        )
        assert (
            connection.execute(
                """
                SELECT COUNT(*) FROM research_runs
                WHERE run_origin = 'forecast'
                """,
            ).fetchone()[0]
            == 5
        )


@pytest.mark.anyio
async def test_private_pack_success_records_tools_without_pack_request(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    forecast.repository.upsert_trusted_source(
        identifier="trusted-private",
        status="approved",
        allowed_profiles=["private"],
        allowed_pack_roles=["current_state"],
        allowed_tool_names=["file_search"],
    )
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        forecast_id = await _create_approved_forecast(client)
        response = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={
                "pack_role": "current_state",
                "tool_profile": "private",
                "data_classification": "internal",
                "background": False,
                "vector_store_ids": ["vs_1"],
                "trusted_source_identifiers": ["trusted-private"],
            },
        )

    assert response.status_code == 200
    pack = response.json()
    assert pack["tool_profile"] == "private"
    assert pack["data_classification"] == "internal"
    assert research.repository.get_run(UUID(pack["research_run_id"])).run_origin == (
        "forecast"
    )
    assert fake.submit_calls == [
        {
            "prompt": fake.submit_calls[0]["prompt"],
            "max_tool_calls": 40,
            "tool_profile": "private",
            "background": False,
            "policy_decision_id": pack["policy_decision_id"],
            "vector_store_ids": ["vs_1"],
        }
    ]
    with forecast.repository.connect() as connection:
        policy = connection.execute(
            """
            SELECT * FROM forecast_policy_decisions
            WHERE policy_decision_id = ?
            """,
            (pack["policy_decision_id"],),
        ).fetchone()
        assert policy is not None
        assert policy["data_classification"] == "internal"
        assert json.loads(policy["resolved_tools_json"]) == [
            {"type": "file_search", "vector_store_ids": ["vs_1"]}
        ]
        assert json.loads(policy["vector_store_ids_json"]) == ["vs_1"]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forecast_pack_requests WHERE forecast_id = ?",
                (forecast_id,),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.anyio
async def test_phase_b_lifecycle_e2e_with_default_packs(
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
        forecast_id = await _create_approved_forecast(client)
        defaults = await client.post(f"/forecasts/{forecast_id}/research-packs/defaults")
        assert defaults.status_code == 200
        default_packs = defaults.json()["packs"]

        for pack in default_packs:
            completed = research.collect_deep_research(UUID(pack["research_run_id"]))
            assert completed.status == RunStatus.COMPLETED
            assert completed.run_origin == "forecast"

        evidence = await client.post(f"/forecasts/{forecast_id}/evidence/extract")
        assert evidence.status_code == 200
        evidence_json = evidence.json()
        assert len(evidence_json["sources"]) == 5
        assert len(evidence_json["claims"]) >= 5

        scenarios = await client.post(f"/forecasts/{forecast_id}/scenarios/generate")
        assert scenarios.status_code == 200
        assert scenarios.json()["scenarios"]

        link_approval = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_claim_target_links"},
        )
        assert link_approval.status_code == 200

        estimate = await client.post(
            f"/forecasts/{forecast_id}/probabilities/compute",
            json={"engine_version": "phase_b_v1"},
        )
        assert estimate.status_code == 200
        estimate_json = cast(dict[str, Any], estimate.json())
        assert estimate_json["engine_version"] == "phase_b_v1"
        assert estimate_json["estimates"]

        publication_approval = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={
                "action": "approve_probability_publication",
                "estimate_set_id": estimate_json["estimate_set_id"],
                "reviewer": "analyst-1",
                "review_reason": "Phase B draft is ready.",
            },
        )
        assert publication_approval.status_code == 200
        assert publication_approval.json()["estimate_set_id"] == (
            estimate_json["estimate_set_id"]
        )

        commit = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "estimate_set_id": estimate_json["estimate_set_id"],
                "expected_input_snapshot_hash": estimate_json["input_snapshot_hash"],
            },
        )
        assert commit.status_code == 200
        commit_json = commit.json()
        snapshot_path = Path(commit_json["snapshot_artifact_path"])
        assert snapshot_path.exists()
        snapshot = json.loads(snapshot_path.read_text())
        assert snapshot["engine_version"] == "phase_b_v1"
        assert len(snapshot["packs"]) == 5

        detail = await client.get(f"/forecasts/{forecast_id}")
        assert detail.status_code == 200
        outcome_id = detail.json()["outcomes"][0]["outcome_id"]
        resolve = await client.post(
            f"/forecasts/{forecast_id}/resolve",
            json={"outcome_id": outcome_id, "resolution_notes": "resolved"},
        )
        assert resolve.status_code == 200
        assert resolve.json()["scorer_version"] == "phase_b_scorer_v1"

        audit = await client.get(f"/forecasts/{forecast_id}/audit")
        assert audit.status_code == 200

    forecast_uuid = UUID(forecast_id)
    with forecast.repository.connect() as connection:
        assert (
            connection.execute(
                """
                SELECT COUNT(*) FROM forecast_research_packs
                WHERE forecast_id = ? AND is_active = 1
                """,
                (forecast_id,),
            ).fetchone()[0]
            == 5
        )
        assert (
            connection.execute(
                """
                SELECT COUNT(*) FROM research_runs
                WHERE run_origin = 'forecast'
                """,
            ).fetchone()[0]
            == 5
        )
        assert (
            connection.execute(
                """
                SELECT COUNT(*) FROM forecast_policy_decisions
                WHERE forecast_id = ?
                """,
                (forecast_id,),
            ).fetchone()[0]
            == 5
        )
    assert len(forecast.repository.get_sources(forecast_uuid)) == 5
    assert len(forecast.repository.get_claims(forecast_uuid)) >= 5
    assert len(forecast.repository.get_analog_events(forecast_uuid)) >= 2
    assert forecast.repository.get_drivers(forecast_uuid)
    assert forecast.repository.get_scenarios(forecast_uuid)
    audit_json = audit.json()
    assert len(audit_json["policy_decisions"]) == 5
    event_types = {event["event_type"] for event in audit_json["events"]}
    assert {
        "research_pack_dispatched",
        "evidence_extracted",
        "scenarios_upserted",
        "probabilities_computed",
        "review_recorded",
        "version_committed",
        "forecast_resolved",
    }.issubset(event_types)


@pytest.mark.anyio
async def test_restricted_background_pack_is_blocked_before_run_creation(
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
        forecast_id = await _create_approved_forecast(client)
        response = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={
                "pack_role": "current_state",
                "tool_profile": "public",
                "data_classification": "restricted",
                "background": True,
            },
        )

    assert response.status_code == 409
    assert _typed_code(response.json()) == "background_mode_violates_zdr"
    assert fake.submit_calls == []
    with forecast.repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM forecast_pack_requests").fetchone()[0]
            == 1
        )


@pytest.mark.anyio
async def test_private_pack_rejects_trusted_source_tool_mismatch_before_run(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    forecast.repository.upsert_trusted_source(
        identifier="trusted-private",
        status="approved",
        allowed_profiles=["private"],
        allowed_pack_roles=["current_state"],
        allowed_tool_names=["web_search_preview"],
    )
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        forecast_id = await _create_approved_forecast(client)
        response = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={
                "pack_role": "current_state",
                "tool_profile": "private",
                "data_classification": "internal",
                "background": False,
                "vector_store_ids": ["vs_1"],
                "trusted_source_identifiers": ["trusted-private"],
            },
        )

    assert response.status_code == 409
    assert _typed_code(response.json()) == "trusted_source_tool_not_allowed"
    assert fake.submit_calls == []
    with forecast.repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forecast_pack_requests WHERE reason = ?",
                ("trusted_source_tool_not_allowed",),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.anyio
async def test_phase_b_review_actions_require_reviewer(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        forecast_id = await _create_approved_forecast(client)
        missing = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_private_data_use"},
        )
        approved = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_private_data_use", "reviewer": "analyst-1"},
        )

    assert missing.status_code == 422
    assert _typed_code(missing.json()) == "reviewer_required"
    assert approved.status_code == 200


@pytest.mark.anyio
async def test_pack_rerun_requires_expected_active_pack(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        forecast_id = await _create_approved_forecast(client)
        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200
        pack_id = pack.json()["pack_id"]
        response = await client.post(
            f"/forecasts/{forecast_id}/research-packs/{pack_id}/rerun",
            json={"expected_active_pack_id": str(uuid4())},
        )

    assert response.status_code == 409
    assert _typed_code(response.json()) == "active_pack_changed"


def test_repository_migrates_old_research_packs_before_active_unique_index(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "phase-b.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE forecast_forecasts (
                id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                resolution_date TEXT,
                target_population TEXT,
                unit_of_analysis TEXT,
                resolution_criteria TEXT NOT NULL DEFAULT '',
                resolution_sources_json TEXT NOT NULL DEFAULT '[]',
                decision_context TEXT,
                confidentiality_class TEXT NOT NULL DEFAULT 'public',
                status TEXT NOT NULL,
                current_framing_version INTEGER NOT NULL DEFAULT 1,
                approved_framing_version INTEGER,
                committed_version_id TEXT,
                resolved_outcome_id TEXT,
                resolved_at TEXT,
                resolution_notes TEXT,
                idempotency_key TEXT UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE forecast_policy_decisions (
                policy_decision_id TEXT PRIMARY KEY,
                forecast_id TEXT NOT NULL,
                profile TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                prompt_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE forecast_research_packs (
                pack_id TEXT PRIMARY KEY,
                forecast_id TEXT NOT NULL,
                research_run_id TEXT NOT NULL,
                pack_role TEXT NOT NULL,
                tool_profile TEXT NOT NULL,
                status TEXT NOT NULL,
                model_deployment TEXT,
                prompt_version TEXT NOT NULL,
                max_tool_calls INTEGER NOT NULL,
                policy_decision_id TEXT NOT NULL,
                report_artifact_hash TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    repository = ForecastRepository(db_path)

    with repository.connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(forecast_research_packs)")
        }
        indexes = {
            row["name"]
            for row in connection.execute("PRAGMA index_list(forecast_research_packs)")
        }
    assert "is_active" in columns
    assert "forecast_research_packs_active_unique" in indexes


@pytest.mark.anyio
async def test_pack_rerun_gate_failure_keeps_old_pack_active(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        forecast_id = await _create_approved_forecast(client)
        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200
        pack_id = pack.json()["pack_id"]
        failed = await client.post(
            f"/forecasts/{forecast_id}/research-packs/{pack_id}/rerun",
            json={
                "expected_active_pack_id": pack_id,
                "timeout_sec": forecast.settings.research_deep_research_timeout_seconds
                + 1,
            },
        )

    assert failed.status_code == 409
    assert _typed_code(failed.json()) == "timeout_budget_exceeded"
    packs = forecast.repository.list_active_packs(UUID(forecast_id))
    assert [row["pack_id"] for row in packs] == [pack_id]
    with forecast.repository.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forecast_pack_requests WHERE reason = ?",
                ("timeout_budget_exceeded",),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.anyio
async def test_partial_phase_b_default_packs_do_not_unlock_evidence(
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
        forecast_id = await _create_approved_forecast(client)
        current = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        base_rate = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "base_rate", "tool_profile": "public"},
        )
    assert current.status_code == 200
    assert base_rate.status_code == 200
    for pack in forecast.repository.list_active_packs(UUID(forecast_id)):
        research.repository.update_run(
            UUID(pack["research_run_id"]),
            status=RunStatus.COMPLETED,
            final_report=f"Completed report for {pack['pack_role']}.",
        )

    with pytest.raises(ForecastConflict) as error:
        forecast.extract_evidence(UUID(forecast_id))
    assert error.value.code == "pack_not_completed"
    assert "missing_packs" in error.value.details


@pytest.mark.anyio
async def test_phase_b_evidence_preserves_pack_provenance(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        forecast_id = await _create_approved_forecast(client)
        defaults = await client.post(f"/forecasts/{forecast_id}/research-packs/defaults")
    assert defaults.status_code == 200
    active_packs = forecast.repository.list_active_packs(UUID(forecast_id))
    for pack in active_packs:
        research.repository.update_run(
            UUID(pack["research_run_id"]),
            status=RunStatus.COMPLETED,
            final_report=(
                f"{pack['pack_role']} report line one.\n"
                f"{pack['pack_role']} report line two."
            ),
        )

    forecast.extract_evidence(UUID(forecast_id))

    source_pack_ids = {row["pack_id"] for row in forecast.repository.get_sources(UUID(forecast_id))}
    claim_pack_ids = {row["pack_id"] for row in forecast.repository.get_claims(UUID(forecast_id))}
    expected_pack_ids = {row["pack_id"] for row in active_packs}
    assert source_pack_ids == expected_pack_ids
    assert claim_pack_ids == expected_pack_ids


def test_phase_b_commit_requires_probability_publication_review(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, _research = _make_orchestrators(tmp_path, fake)
    created = forecast.create_forecast(
        request=forecast_service_request(),
        idempotency_key=None,
    )
    forecast_id = created.forecast_id
    forecast.approve_framing(forecast_id, comment=None)
    detail = forecast.get_forecast(forecast_id)
    snapshot = {
        "outcomes": [
            {
                "outcome_id": str(outcome.outcome_id),
                "normalization_group_id": outcome.normalization_group_id,
                "sort_order": outcome.sort_order,
            }
            for outcome in detail.outcomes
        ],
        "claims": [],
        "approved_target_links": [],
        "analog_events": [],
        "cross_impact": [],
        "scenarios": [],
    }
    engine = probability.get_engine("phase_b_v1")
    estimate_set = forecast.repository.create_draft_estimate_set(
        forecast_id=forecast_id,
        engine_version=engine.engine_version,
        input_snapshot_hash=engine.snapshot_hash(snapshot),
        engine_code_hash=engine.engine_code_hash(),
        random_seed=engine.random_seed,
        normalization_group_id=detail.outcomes[0].normalization_group_id,
        snapshot=snapshot,
        estimates=engine.compute(snapshot=snapshot),
    )
    estimate_set_id = UUID(estimate_set["estimate_set_id"])

    with pytest.raises(ForecastConflict) as phase_a_error:
        forecast.approve_estimate_set(
            forecast_id,
            estimate_set_id=estimate_set_id,
            comment=None,
        )
    assert phase_a_error.value.code == "reviewer_required"

    with pytest.raises(ForecastConflict) as commit_error:
        forecast.commit_version(
            forecast_id,
            estimate_set_id=estimate_set_id,
            expected_input_snapshot_hash=estimate_set["input_snapshot_hash"],
        )
    assert commit_error.value.code == "approval_required"

    forecast.record_phase_b_review(
        forecast_id,
        action="approve_probability_publication",
        comment=None,
        reviewer="reviewer-1",
        reviewer_auth_subject=None,
        policy_decision_id=None,
        review_reason="Ready for publication.",
        estimate_set_id=estimate_set_id,
        version_id=None,
    )
    committed = forecast.commit_version(
        forecast_id,
        estimate_set_id=estimate_set_id,
        expected_input_snapshot_hash=estimate_set["input_snapshot_hash"],
    )
    assert committed.estimate_set_id == estimate_set_id


def test_phase_b_probability_dispatch_uses_analogs_polarity_and_scenarios() -> None:
    outcome_yes = str(uuid4())
    outcome_no = str(uuid4())
    scenario_yes = str(uuid4())
    snapshot = {
        "random_seed": 11,
        "perturbation_runs": 20,
        "outcomes": [
            {
                "outcome_id": outcome_yes,
                "normalization_group_id": "ng",
                "sort_order": 0,
            },
            {
                "outcome_id": outcome_no,
                "normalization_group_id": "ng",
                "sort_order": 1,
            },
        ],
        "claims": [
            {
                "claim_id": "c1",
                "polarity": -1,
                "evidence_strength": 0.8,
                "reliability_score": 0.9,
                "cluster_id": "cluster",
                "independence_group": "group",
            }
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": outcome_yes,
                "direction": -1,
                "relevance_weight": 1.0,
            }
        ],
        "analog_events": [
            {"matched_outcome_id": outcome_yes, "weight": 2.0, "active": True}
        ],
        "cross_impact": [{"target_outcome_id": outcome_no, "delta": -0.2}],
        "scenarios": [
            {
                "scenario_id": scenario_yes,
                "outcome_id": outcome_yes,
                "normalized_weight": 1.0,
                "validity_status": "valid",
            }
        ],
    }

    estimates = probability.compute(snapshot=snapshot, engine_version="phase_b_v1")

    yes = next(
        item
        for item in estimates
        if item["target_kind"] == "outcome" and item["target_id"] == outcome_yes
    )
    scenario = next(item for item in estimates if item["target_kind"] == "scenario")
    assert yes["prior"] > 0.5
    assert yes["evidence_update"] > 0
    assert scenario["target_id"] == scenario_yes
    assert float(scenario["final_probability"]) == float(yes["final_probability"])


def test_phase_b_prior_logit_is_not_clamped() -> None:
    outcome_yes = str(uuid4())
    outcome_no = str(uuid4())
    snapshot = {
        "perturbation_runs": 0,
        "outcomes": [
            {
                "outcome_id": outcome_yes,
                "normalization_group_id": "ng",
                "sort_order": 0,
            },
            {
                "outcome_id": outcome_no,
                "normalization_group_id": "ng",
                "sort_order": 1,
            },
        ],
        "claims": [],
        "approved_target_links": [],
        "analog_events": [
            {"matched_outcome_id": outcome_yes, "weight": 999.0, "active": True}
        ],
        "cross_impact": [],
        "scenarios": [],
    }

    estimates = probability.compute(snapshot=snapshot, engine_version="phase_b_v1")

    yes = next(
        item
        for item in estimates
        if item["target_kind"] == "outcome" and item["target_id"] == outcome_yes
    )
    assert math.isclose(float(yes["prior"]), 1000 / 1001)
    assert math.isclose(float(yes["final_probability"]), float(yes["prior"]))
    assert float(yes["final_probability"]) > 0.995
    assert yes["components"]["clamped"] is False


def test_phase_b_cross_impact_uses_source_outcome_prior() -> None:
    source_high = str(uuid4())
    source_low = str(uuid4())
    target = str(uuid4())

    def _snapshot(source_outcome_id: str) -> dict[str, Any]:
        return {
            "perturbation_runs": 0,
            "outcomes": [
                {
                    "outcome_id": source_high,
                    "normalization_group_id": "ng",
                    "sort_order": 0,
                },
                {
                    "outcome_id": source_low,
                    "normalization_group_id": "ng",
                    "sort_order": 1,
                },
                {
                    "outcome_id": target,
                    "normalization_group_id": "ng",
                    "sort_order": 2,
                },
            ],
            "claims": [],
            "approved_target_links": [],
            "analog_events": [
                {"matched_outcome_id": source_high, "weight": 8.0, "active": True}
            ],
            "cross_impact": [
                {
                    "source_outcome_id": source_outcome_id,
                    "target_outcome_id": target,
                    "delta": 1.0,
                }
            ],
            "scenarios": [],
        }

    high_source_estimates = probability.compute(
        snapshot=_snapshot(source_high),
        engine_version="phase_b_v1",
    )
    low_source_estimates = probability.compute(
        snapshot=_snapshot(source_low),
        engine_version="phase_b_v1",
    )
    high_target = next(
        item
        for item in high_source_estimates
        if item["target_kind"] == "outcome" and item["target_id"] == target
    )
    low_target = next(
        item
        for item in low_source_estimates
        if item["target_kind"] == "outcome" and item["target_id"] == target
    )

    assert high_target["cross_impact_adjustment"] > low_target[
        "cross_impact_adjustment"
    ]
    assert high_target["final_probability"] > low_target["final_probability"]


def test_phase_b_scenario_range_comes_from_outcome_perturbation() -> None:
    outcome_yes = str(uuid4())
    outcome_no = str(uuid4())
    scenario_small = str(uuid4())
    scenario_large = str(uuid4())
    snapshot = {
        "random_seed": 7,
        "perturbation_runs": 80,
        "outcomes": [
            {
                "outcome_id": outcome_yes,
                "normalization_group_id": "ng",
                "sort_order": 0,
            },
            {
                "outcome_id": outcome_no,
                "normalization_group_id": "ng",
                "sort_order": 1,
            },
        ],
        "claims": [
            {
                "claim_id": "c1",
                "polarity": 1,
                "evidence_strength": 0.6,
                "reliability_score": 0.8,
                "cluster_id": "cluster",
                "independence_group": "group",
            }
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": outcome_yes,
                "direction": 1,
                "relevance_weight": 1.0,
            }
        ],
        "analog_events": [],
        "cross_impact": [],
        "scenarios": [
            {
                "scenario_id": scenario_small,
                "outcome_id": outcome_yes,
                "normalized_weight": 1.0,
                "validity_status": "valid",
            },
            {
                "scenario_id": scenario_large,
                "outcome_id": outcome_yes,
                "normalized_weight": 3.0,
                "validity_status": "valid",
            },
        ],
    }

    estimates = probability.compute(snapshot=snapshot, engine_version="phase_b_v1")

    outcome = next(
        item
        for item in estimates
        if item["target_kind"] == "outcome" and item["target_id"] == outcome_yes
    )
    small = next(item for item in estimates if item["target_id"] == scenario_small)
    large = next(item for item in estimates if item["target_id"] == scenario_large)
    outcome_range = outcome["uncertainty_range"]
    small_range = small["uncertainty_range"]
    large_range = large["uncertainty_range"]

    assert math.isclose(
        float(small["final_probability"]),
        float(outcome["final_probability"]) * 0.25,
    )
    assert math.isclose(
        float(large["final_probability"]),
        float(outcome["final_probability"]) * 0.75,
    )
    assert math.isclose(float(small_range["lo80"]), float(outcome_range["lo80"]) * 0.25)
    assert math.isclose(float(small_range["hi80"]), float(outcome_range["hi80"]) * 0.25)
    assert math.isclose(float(large_range["lo80"]), float(outcome_range["lo80"]) * 0.75)
    assert math.isclose(float(large_range["hi80"]), float(outcome_range["hi80"]) * 0.75)
    assert not math.isclose(
        float(small_range["hi80"]),
        min(1.0, float(small["final_probability"]) + 0.10),
    )
    assert small["components"]["range_source"] == "outcome_perturbation"


def test_public_commit_rejects_internal_evidence_snapshot(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure()
    forecast, _research = _make_orchestrators(tmp_path, fake)
    created = forecast.create_forecast(
        request=forecast_service_request(),
        idempotency_key=None,
    )
    forecast_id = created.forecast_id
    forecast.approve_framing(forecast_id, comment=None)
    detail = forecast.get_forecast(forecast_id)
    snapshot = {
        "outcomes": [
            {
                "outcome_id": str(outcome.outcome_id),
                "normalization_group_id": outcome.normalization_group_id,
                "sort_order": outcome.sort_order,
            }
            for outcome in detail.outcomes
        ],
        "claims": [],
        "approved_target_links": [],
        "sources": [{"data_classification": "internal"}],
    }
    engine = probability.get_engine("phase_a_v1")
    estimate_set = forecast.repository.create_draft_estimate_set(
        forecast_id=forecast_id,
        engine_version=engine.engine_version,
        input_snapshot_hash=engine.snapshot_hash(snapshot),
        engine_code_hash=engine.engine_code_hash(),
        random_seed=engine.random_seed,
        normalization_group_id=detail.outcomes[0].normalization_group_id,
        snapshot=snapshot,
        estimates=engine.compute(snapshot=snapshot),
    )
    forecast.repository.approve_estimate_set(
        forecast_id,
        estimate_set_id=UUID(estimate_set["estimate_set_id"]),
        comment=None,
    )

    with pytest.raises(ForecastConflict) as error:
        forecast.commit_version(
            forecast_id,
            estimate_set_id=UUID(estimate_set["estimate_set_id"]),
            expected_input_snapshot_hash=estimate_set["input_snapshot_hash"],
        )
    assert error.value.code == "classification_mismatch"


def forecast_service_request():
    from api.forecast.schemas import ForecastCreateRequest

    return ForecastCreateRequest(
        question="Will public commit reject internal evidence?",
        resolution_criteria="Resolve from public records.",
        outcomes=["Yes", "No"],
    )
