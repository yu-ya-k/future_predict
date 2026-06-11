from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import api.forecast.projection.phase_c_v1 as phase_c_v1
from api.forecast.repository import ForecastRepository
from api.research.schemas import utc_now


def _create_projection_forecast(
    repository: ForecastRepository,
    *,
    metric_id: str,
) -> UUID:
    row = repository.create_forecast(
        question=f"What will {metric_id} look like?",
        original_execution_prompt=None,
        resolution_date=None,
        target_population=None,
        unit_of_analysis=None,
        resolution_criteria="Resolve from public reports.",
        resolution_sources=[],
        decision_context=None,
        confidentiality_class="public",
        forecast_mode="scenario_projection",
        outcome_labels=[],
        projection_dimensions=[
            {
                "metric_id": metric_id,
                "label": metric_id.replace("_", " ").title(),
                "unit": "%",
                "value_type": "percentage",
                "baseline_year": 2026,
                "baseline_value": 10.0,
                "horizons": [2030],
            }
        ],
        idempotency_key=None,
    )
    return UUID(row["id"])


def _insert_projection_set(
    connection: sqlite3.Connection,
    *,
    forecast_id: UUID,
) -> str:
    projection_set_id = str(uuid4())
    connection.execute(
        """
        INSERT INTO forecast_projection_sets (
            projection_set_id, forecast_id, status, engine_version,
            input_snapshot_hash, engine_code_hash, random_seed,
            snapshot_json, created_at
        )
        VALUES (?, ?, 'draft', 'phase_c_v1', ?, ?, 0, '{}', ?)
        """,
        (
            projection_set_id,
            str(forecast_id),
            f"input-{projection_set_id}",
            f"code-{projection_set_id}",
            utc_now().isoformat(),
        ),
    )
    return projection_set_id


def _insert_projection_scenario(
    connection: sqlite3.Connection,
    *,
    projection_set_id: str,
    forecast_id: UUID,
) -> str:
    scenario_id = str(uuid4())
    connection.execute(
        """
        INSERT INTO forecast_projection_scenarios (
            projection_scenario_id, projection_set_id, forecast_id,
            label, description, coverage_role, residual_flag, probability,
            probability_logit, driver_vector_json, narrative, validity_status,
            created_at
        )
        VALUES (?, ?, ?, 'Baseline', 'Baseline projection.', 'core', 0, 1.0,
                0.0, '{}', '', 'valid', ?)
        """,
        (scenario_id, projection_set_id, str(forecast_id), utc_now().isoformat()),
    )
    return scenario_id


def _insert_claim(
    connection: sqlite3.Connection,
    *,
    forecast_id: UUID,
) -> str:
    claim_id = str(uuid4())
    connection.execute(
        """
        INSERT INTO forecast_claims (
            claim_id, forecast_id, text, claim_type, polarity,
            evidence_strength, reliability_score, cluster_id, independence_group,
            source_classification, data_classification, origin_tool_profile,
            extraction_model, extraction_prompt_version, created_at
        )
        VALUES (?, ?, 'Projection evidence.', 'current_state', 1, 0.8, 0.9,
                'cluster-1', 'group-1', 'public', 'public', 'public',
                'test', 'phase_c_repository_test', ?)
        """,
        (claim_id, str(forecast_id), utc_now().isoformat()),
    )
    return claim_id


@pytest.mark.parametrize(
    "covered_function",
    [
        "canonical_json_bytes",
        "snapshot_hash",
        "compute_phase_c_projection",
        "_scenario_probabilities",
        "_metric_points",
        "_composites",
        "_sensitivities",
        "input_signature",
        "_softmax",
        "_stable",
    ],
)
def test_engine_code_hash_covers_phase_c_helper_sources(
    monkeypatch: pytest.MonkeyPatch,
    covered_function: str,
) -> None:
    original_hash = phase_c_v1.engine_code_hash()
    original_getsource = phase_c_v1.inspect.getsource
    target = getattr(phase_c_v1, covered_function)

    def fake_getsource(function: Callable[..., object]) -> str:
        source = original_getsource(function)
        if function is target:
            return f"{source}\n# test mutation"
        return source

    monkeypatch.setattr(phase_c_v1.inspect, "getsource", fake_getsource)

    assert phase_c_v1.engine_code_hash() != original_hash


def test_engine_code_hash_covers_phase_c_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_hash = phase_c_v1.engine_code_hash()

    monkeypatch.setattr(phase_c_v1, "K_SIGNAL", 9.0)

    assert phase_c_v1.engine_code_hash() != original_hash


def test_frozen_projection_dimensions_reject_update_and_delete(
    tmp_path: Path,
) -> None:
    repository = ForecastRepository(tmp_path / "phase-c.sqlite3")
    forecast_id = _create_projection_forecast(repository, metric_id="agent_adoption")
    dimension = repository.get_projection_dimensions(forecast_id)[0]

    repository.approve_framing(forecast_id, comment=None)

    with repository.connect() as connection:
        frozen = connection.execute(
            """
            SELECT frozen FROM forecast_projection_dimensions
            WHERE dimension_id = ?
            """,
            (dimension["dimension_id"],),
        ).fetchone()
        assert frozen is not None
        assert frozen["frozen"] == 1

        with pytest.raises(sqlite3.DatabaseError, match="frozen forecast_projection_dimensions"):
            connection.execute(
                """
                UPDATE forecast_projection_dimensions
                SET label = 'Changed'
                WHERE dimension_id = ?
                """,
                (dimension["dimension_id"],),
            )
        with pytest.raises(sqlite3.DatabaseError, match="frozen forecast_projection_dimensions"):
            connection.execute(
                """
                DELETE FROM forecast_projection_dimensions
                WHERE dimension_id = ?
                """,
                (dimension["dimension_id"],),
            )


def test_projection_child_rows_reject_cross_forecast_insert_and_update(
    tmp_path: Path,
) -> None:
    repository = ForecastRepository(tmp_path / "phase-c.sqlite3")
    forecast_a = _create_projection_forecast(repository, metric_id="agent_adoption")
    forecast_b = _create_projection_forecast(repository, metric_id="agent_revenue")
    dimension_a = repository.get_projection_dimensions(forecast_a)[0]["dimension_id"]
    dimension_b = repository.get_projection_dimensions(forecast_b)[0]["dimension_id"]

    with repository.connect() as connection:
        set_a = _insert_projection_set(connection, forecast_id=forecast_a)
        scenario_a = _insert_projection_scenario(
            connection,
            projection_set_id=set_a,
            forecast_id=forecast_a,
        )
        claim_b = _insert_claim(connection, forecast_id=forecast_b)
        now = utc_now().isoformat()

        with pytest.raises(
            sqlite3.DatabaseError,
            match="forecast_projection_scenarios forecast ownership mismatch",
        ):
            connection.execute(
                """
                INSERT INTO forecast_projection_scenarios (
                    projection_scenario_id, projection_set_id, forecast_id,
                    label, description, coverage_role, residual_flag, probability,
                    probability_logit, driver_vector_json, narrative,
                    validity_status, created_at
                )
                VALUES (?, ?, ?, 'Cross', 'Cross projection.', 'core', 0, 1.0,
                        0.0, '{}', '', 'valid', ?)
                """,
                (str(uuid4()), set_a, str(forecast_b), now),
            )

        with pytest.raises(
            sqlite3.DatabaseError,
            match="forecast_projection_metric_points forecast ownership mismatch",
        ):
            connection.execute(
                """
                INSERT INTO forecast_projection_metric_points (
                    metric_point_id, projection_set_id, projection_scenario_id,
                    dimension_id, forecast_id, metric_id, horizon_year,
                    p10, p50, p90, mean, distribution_family,
                    distribution_params_json, baseline_transform, created_at
                )
                VALUES (?, ?, ?, ?, ?, 'agent_revenue', 2030, 10, 20, 30, 20,
                        'triangular_quantile_v1', '{}', 'level', ?)
                """,
                (str(uuid4()), set_a, scenario_a, dimension_b, str(forecast_a), now),
            )

        metric_point_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO forecast_projection_metric_points (
                metric_point_id, projection_set_id, projection_scenario_id,
                dimension_id, forecast_id, metric_id, horizon_year,
                p10, p50, p90, mean, distribution_family,
                distribution_params_json, baseline_transform, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'agent_adoption', 2030, 10, 20, 30, 20,
                    'triangular_quantile_v1', '{}', 'level', ?)
            """,
            (metric_point_id, set_a, scenario_a, dimension_a, str(forecast_a), now),
        )
        with pytest.raises(
            sqlite3.DatabaseError,
            match="forecast_projection_metric_points forecast ownership mismatch",
        ):
            connection.execute(
                """
                UPDATE forecast_projection_metric_points
                SET dimension_id = ?
                WHERE metric_point_id = ?
                """,
                (dimension_b, metric_point_id),
            )

        with pytest.raises(
            sqlite3.DatabaseError,
            match="forecast_projection_composites forecast ownership mismatch",
        ):
            connection.execute(
                """
                INSERT INTO forecast_projection_composites (
                    composite_id, projection_set_id, dimension_id, forecast_id,
                    metric_id, horizon_year, p10, p50, p90, mean,
                    mixture_components_json, created_at
                )
                VALUES (?, ?, ?, ?, 'agent_revenue', 2030, 10, 20, 30, 20,
                        '[]', ?)
                """,
                (str(uuid4()), set_a, dimension_b, str(forecast_a), now),
            )

        with pytest.raises(
            sqlite3.DatabaseError,
            match="forecast_projection_sensitivities forecast ownership mismatch",
        ):
            connection.execute(
                """
                INSERT INTO forecast_projection_sensitivities (
                    sensitivity_id, projection_set_id, forecast_id,
                    sensitivity_kind, target_ref, baseline_snapshot_hash,
                    perturbed_input_json, delta_p50, delta_p90,
                    delta_probability, rank, created_at
                )
                VALUES (?, ?, ?, 'driver_one_way', 'target', 'snapshot',
                        '{}', 0, 0, 0, 1, ?)
                """,
                (str(uuid4()), set_a, str(forecast_b), now),
            )

        with pytest.raises(
            sqlite3.DatabaseError,
            match="forecast_projection_evidence_links forecast ownership mismatch",
        ):
            connection.execute(
                """
                INSERT INTO forecast_projection_evidence_links (
                    link_id, forecast_id, projection_set_id, dimension_id,
                    projection_scenario_id, claim_id, relevance_weight, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 1.0, ?)
                """,
                (str(uuid4()), str(forecast_a), set_a, dimension_a, scenario_a, claim_b, now),
            )
