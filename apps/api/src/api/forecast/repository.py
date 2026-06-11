# ruff: noqa: E501
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from api.forecast.schemas import ForecastMode, ForecastStatus, ToolProfile
from api.research.schemas import utc_now


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


IDEMPOTENCY_IN_PROGRESS = "__forecast_idempotency_in_progress__"
_UNSET = object()


class ResearchPackAlreadyExists(Exception):
    def __init__(self, existing_pack: sqlite3.Row) -> None:
        super().__init__("research_pack_already_exists")
        self.existing_pack = existing_pack


def _load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})")}


def _ensure_column(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
    ddl: str,
) -> None:
    if column_name not in _table_columns(connection, table_name):
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def _is_phase_a_research_pack_unique_error(error: sqlite3.IntegrityError) -> bool:
    message = str(error)
    return (
        "UNIQUE constraint failed: forecast_research_packs.forecast_id, "
        "forecast_research_packs.pack_role, forecast_research_packs.tool_profile"
    ) in message


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_notnull(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
) -> int | None:
    for row in connection.execute(f"PRAGMA table_info({table_name})"):
        if row["name"] == column_name:
            return int(row["notnull"])
    return None


class ForecastRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS forecast_forecasts (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    original_execution_prompt TEXT,
                    resolution_date TEXT,
                    target_population TEXT,
                    unit_of_analysis TEXT,
                    resolution_criteria TEXT NOT NULL DEFAULT '',
                    resolution_sources_json TEXT NOT NULL DEFAULT '[]',
                    decision_context TEXT,
                    confidentiality_class TEXT NOT NULL DEFAULT 'public',
                    forecast_mode TEXT NOT NULL DEFAULT 'discrete_outcome'
                        CHECK (forecast_mode IN ('discrete_outcome','scenario_projection')),
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
                );

                CREATE TABLE IF NOT EXISTS forecast_outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    framing_version INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    definition TEXT NOT NULL,
                    resolution_rule TEXT NOT NULL,
                    exclusive_group_id TEXT NOT NULL,
                    normalization_group_id TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    frozen INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_policy_decisions (
                    policy_decision_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    profile TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    prompt_hash TEXT NOT NULL,
                    decision TEXT NOT NULL DEFAULT 'allowed',
                    policy_version TEXT NOT NULL DEFAULT 'phase_a_v1',
                    data_classification TEXT NOT NULL DEFAULT 'public',
                    resolved_tools_json TEXT NOT NULL DEFAULT '[]',
                    vector_store_ids_json TEXT NOT NULL DEFAULT '[]',
                    mcp_server_ids_json TEXT NOT NULL DEFAULT '[]',
                    background INTEGER NOT NULL DEFAULT 1,
                    blocked_terms_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_research_packs (
                    pack_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    research_run_id TEXT NOT NULL REFERENCES research_runs(id),
                    pack_role TEXT NOT NULL,
                    tool_profile TEXT NOT NULL,
                    status TEXT NOT NULL,
                    model_deployment TEXT,
                    prompt_version TEXT NOT NULL,
                    max_tool_calls INTEGER NOT NULL,
                    policy_decision_id TEXT NOT NULL REFERENCES forecast_policy_decisions(policy_decision_id),
                    report_artifact_hash TEXT,
                    attempt_no INTEGER NOT NULL DEFAULT 1,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    rerun_of_pack_id TEXT REFERENCES forecast_research_packs(pack_id),
                    timeout_sec INTEGER,
                    estimated_cost_budget_usd REAL,
                    vector_store_ids_json TEXT NOT NULL DEFAULT '[]',
                    mcp_server_ids_json TEXT NOT NULL DEFAULT '[]',
                    cache_key TEXT,
                    rerun_policy TEXT,
                    pack_request_id TEXT,
                    data_classification TEXT NOT NULL DEFAULT 'public',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_pack_requests (
                    pack_request_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    pack_role TEXT NOT NULL,
                    tool_profile TEXT NOT NULL,
                    data_classification TEXT NOT NULL DEFAULT 'public',
                    status TEXT NOT NULL,
                    reason TEXT,
                    policy_decision_id TEXT REFERENCES forecast_policy_decisions(policy_decision_id),
                    reviewer TEXT,
                    reviewer_auth_subject TEXT,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_sources (
                    source_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    pack_id TEXT REFERENCES forecast_research_packs(pack_id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    publisher TEXT,
                    url TEXT,
                    source_type TEXT NOT NULL,
                    source_classification TEXT NOT NULL,
                    data_classification TEXT NOT NULL DEFAULT 'public',
                    origin_tool_profile TEXT NOT NULL DEFAULT 'public',
                    reliability_score REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_claims (
                    claim_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    polarity INTEGER NOT NULL CHECK (polarity IN (-1, 1)),
                    evidence_strength REAL NOT NULL CHECK (evidence_strength >= 0 AND evidence_strength <= 1),
                    reliability_score REAL NOT NULL CHECK (reliability_score >= 0 AND reliability_score <= 1),
                    cluster_id TEXT NOT NULL,
                    independence_group TEXT NOT NULL,
                    source_classification TEXT NOT NULL,
                    data_classification TEXT NOT NULL DEFAULT 'public',
                    origin_tool_profile TEXT NOT NULL DEFAULT 'public',
                    pack_id TEXT REFERENCES forecast_research_packs(pack_id) ON DELETE SET NULL,
                    extraction_batch_id TEXT,
                    report_artifact_hash TEXT,
                    manual_locked INTEGER NOT NULL DEFAULT 0,
                    origin TEXT NOT NULL DEFAULT 'extractor',
                    extraction_model TEXT NOT NULL,
                    extraction_prompt_version TEXT NOT NULL,
                    review_status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_claim_source_links (
                    claim_id TEXT NOT NULL REFERENCES forecast_claims(claim_id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL REFERENCES forecast_sources(source_id) ON DELETE CASCADE,
                    PRIMARY KEY (claim_id, source_id)
                );

                CREATE TABLE IF NOT EXISTS forecast_claim_target_links (
                    link_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    claim_id TEXT NOT NULL REFERENCES forecast_claims(claim_id) ON DELETE CASCADE,
                    target_kind TEXT NOT NULL CHECK (target_kind IN ('outcome','scenario')),
                    target_id TEXT NOT NULL,
                    direction INTEGER NOT NULL CHECK (direction IN (-1, 1)),
                    relevance_weight REAL NOT NULL CHECK (relevance_weight >= 0 AND relevance_weight <= 1),
                    review_status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_scenarios (
                    scenario_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    outcome_id TEXT NOT NULL REFERENCES forecast_outcomes(outcome_id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL,
                    normalized_weight REAL NOT NULL DEFAULT 1,
                    validity_status TEXT NOT NULL DEFAULT 'valid',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_drivers (
                    driver_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_driver_states (
                    state_id TEXT PRIMARY KEY,
                    driver_id TEXT NOT NULL REFERENCES forecast_drivers(driver_id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_scenario_driver_states (
                    scenario_id TEXT NOT NULL REFERENCES forecast_scenarios(scenario_id) ON DELETE CASCADE,
                    state_id TEXT NOT NULL REFERENCES forecast_driver_states(state_id) ON DELETE CASCADE,
                    PRIMARY KEY (scenario_id, state_id)
                );

                CREATE TABLE IF NOT EXISTS forecast_cross_impact (
                    cross_impact_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    source_outcome_id TEXT NOT NULL REFERENCES forecast_outcomes(outcome_id) ON DELETE CASCADE,
                    target_outcome_id TEXT NOT NULL REFERENCES forecast_outcomes(outcome_id) ON DELETE CASCADE,
                    delta REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_analog_events (
                    analog_event_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    pack_id TEXT REFERENCES forecast_research_packs(pack_id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    matched_outcome_id TEXT NOT NULL REFERENCES forecast_outcomes(outcome_id) ON DELETE CASCADE,
                    weight REAL NOT NULL CHECK (weight > 0),
                    rationale TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_trusted_sources (
                    trusted_source_id TEXT PRIMARY KEY,
                    identifier TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK (status IN ('pending','approved','revoked','expired')),
                    approved_by TEXT,
                    approved_at TEXT,
                    expires_at TEXT,
                    allowed_profiles_json TEXT NOT NULL DEFAULT '[]',
                    allowed_pack_roles_json TEXT NOT NULL DEFAULT '[]',
                    allowed_tool_names_json TEXT NOT NULL DEFAULT '[]',
                    allowed_vector_store_ids_json TEXT NOT NULL DEFAULT '[]',
                    allowed_mcp_server_ids_json TEXT NOT NULL DEFAULT '[]',
                    owner_team_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_estimate_sets (
                    estimate_set_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK (status IN ('draft','frozen')),
                    engine_version TEXT NOT NULL,
                    input_snapshot_hash TEXT NOT NULL,
                    engine_code_hash TEXT NOT NULL,
                    random_seed INTEGER NOT NULL,
                    normalization_group_id TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    snapshot_artifact_path TEXT,
                    created_at TEXT NOT NULL,
                    frozen_at TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS forecast_estimate_sets_one_draft
                ON forecast_estimate_sets(forecast_id)
                WHERE status = 'draft';

                CREATE TABLE IF NOT EXISTS forecast_projection_dimensions (
                    dimension_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    framing_version INTEGER NOT NULL,
                    metric_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    value_type TEXT NOT NULL CHECK (value_type IN ('number','currency','percentage','index')),
                    currency TEXT,
                    nominal_or_real TEXT CHECK (nominal_or_real IS NULL OR nominal_or_real IN ('nominal','real')),
                    baseline_year INTEGER NOT NULL,
                    baseline_value REAL NOT NULL CHECK (baseline_value >= 0),
                    baseline_source_ids_json TEXT NOT NULL DEFAULT '[]',
                    horizons_json TEXT NOT NULL DEFAULT '[]',
                    sort_order INTEGER NOT NULL,
                    frozen INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (forecast_id, framing_version, metric_id)
                );

                CREATE TABLE IF NOT EXISTS forecast_projection_sets (
                    projection_set_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK (status IN ('draft','frozen')),
                    engine_version TEXT NOT NULL,
                    input_snapshot_hash TEXT NOT NULL,
                    engine_code_hash TEXT NOT NULL,
                    random_seed INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    snapshot_artifact_path TEXT,
                    created_at TEXT NOT NULL,
                    frozen_at TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS forecast_projection_sets_one_draft
                ON forecast_projection_sets(forecast_id)
                WHERE status = 'draft';

                CREATE TABLE IF NOT EXISTS forecast_projection_scenarios (
                    projection_scenario_id TEXT PRIMARY KEY,
                    projection_set_id TEXT NOT NULL REFERENCES forecast_projection_sets(projection_set_id) ON DELETE CASCADE,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL,
                    coverage_role TEXT NOT NULL,
                    residual_flag INTEGER NOT NULL DEFAULT 0 CHECK (residual_flag IN (0,1)),
                    probability REAL NOT NULL CHECK (probability >= 0 AND probability <= 1),
                    probability_logit REAL NOT NULL,
                    driver_vector_json TEXT NOT NULL DEFAULT '{}',
                    narrative TEXT NOT NULL DEFAULT '',
                    validity_status TEXT NOT NULL DEFAULT 'valid',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_projection_metric_points (
                    metric_point_id TEXT PRIMARY KEY,
                    projection_set_id TEXT NOT NULL REFERENCES forecast_projection_sets(projection_set_id) ON DELETE CASCADE,
                    projection_scenario_id TEXT NOT NULL REFERENCES forecast_projection_scenarios(projection_scenario_id) ON DELETE CASCADE,
                    dimension_id TEXT NOT NULL REFERENCES forecast_projection_dimensions(dimension_id),
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    metric_id TEXT NOT NULL,
                    horizon_year INTEGER NOT NULL,
                    p10 REAL NOT NULL,
                    p50 REAL NOT NULL,
                    p90 REAL NOT NULL,
                    mean REAL NOT NULL,
                    distribution_family TEXT NOT NULL,
                    distribution_params_json TEXT NOT NULL DEFAULT '{}',
                    baseline_transform TEXT NOT NULL DEFAULT 'level',
                    created_at TEXT NOT NULL,
                    CHECK (p10 <= p50 AND p50 <= p90),
                    CHECK (p10 >= 0 AND p50 >= 0 AND p90 >= 0 AND mean >= 0)
                );

                CREATE TABLE IF NOT EXISTS forecast_projection_composites (
                    composite_id TEXT PRIMARY KEY,
                    projection_set_id TEXT NOT NULL REFERENCES forecast_projection_sets(projection_set_id) ON DELETE CASCADE,
                    dimension_id TEXT NOT NULL REFERENCES forecast_projection_dimensions(dimension_id),
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    metric_id TEXT NOT NULL,
                    horizon_year INTEGER NOT NULL,
                    p10 REAL NOT NULL,
                    p50 REAL NOT NULL,
                    p90 REAL NOT NULL,
                    mean REAL NOT NULL,
                    mixture_components_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    CHECK (p10 <= p50 AND p50 <= p90),
                    CHECK (p10 >= 0 AND p50 >= 0 AND p90 >= 0 AND mean >= 0)
                );

                CREATE TABLE IF NOT EXISTS forecast_projection_sensitivities (
                    sensitivity_id TEXT PRIMARY KEY,
                    projection_set_id TEXT NOT NULL REFERENCES forecast_projection_sets(projection_set_id) ON DELETE CASCADE,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    sensitivity_kind TEXT NOT NULL CHECK (sensitivity_kind IN ('driver_one_way','scenario_probability')),
                    target_ref TEXT NOT NULL,
                    baseline_snapshot_hash TEXT NOT NULL,
                    perturbed_input_json TEXT NOT NULL DEFAULT '{}',
                    delta_p50 REAL NOT NULL DEFAULT 0,
                    delta_p90 REAL NOT NULL DEFAULT 0,
                    delta_probability REAL NOT NULL DEFAULT 0,
                    rank INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_projection_evidence_links (
                    link_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    projection_set_id TEXT REFERENCES forecast_projection_sets(projection_set_id) ON DELETE CASCADE,
                    dimension_id TEXT REFERENCES forecast_projection_dimensions(dimension_id) ON DELETE CASCADE,
                    projection_scenario_id TEXT REFERENCES forecast_projection_scenarios(projection_scenario_id) ON DELETE CASCADE,
                    claim_id TEXT NOT NULL REFERENCES forecast_claims(claim_id) ON DELETE CASCADE,
                    relevance_weight REAL NOT NULL CHECK (relevance_weight >= 0 AND relevance_weight <= 1),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_projection_resolutions (
                    projection_resolution_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    version_id TEXT NOT NULL REFERENCES forecast_versions(version_id),
                    status TEXT NOT NULL CHECK (status IN ('partially_resolved','resolved')),
                    notes TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_projection_resolution_actuals (
                    actual_id TEXT PRIMARY KEY,
                    projection_resolution_id TEXT NOT NULL REFERENCES forecast_projection_resolutions(projection_resolution_id) ON DELETE CASCADE,
                    dimension_id TEXT NOT NULL REFERENCES forecast_projection_dimensions(dimension_id),
                    metric_id TEXT NOT NULL,
                    horizon_year INTEGER NOT NULL,
                    actual_value REAL NOT NULL CHECK (actual_value >= 0),
                    source TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (projection_resolution_id, dimension_id, horizon_year)
                );

                CREATE TABLE IF NOT EXISTS forecast_probability_estimates (
                    estimate_id TEXT PRIMARY KEY,
                    estimate_set_id TEXT NOT NULL REFERENCES forecast_estimate_sets(estimate_set_id) ON DELETE CASCADE,
                    forecast_version_id TEXT,
                    target_kind TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    prior REAL NOT NULL,
                    evidence_update REAL NOT NULL,
                    cross_impact_adjustment REAL NOT NULL,
                    simulation_adjustment REAL NOT NULL,
                    calibration_adjustment REAL NOT NULL,
                    human_adjustment REAL NOT NULL,
                    final_probability REAL NOT NULL,
                    uncertainty_range_json TEXT NOT NULL,
                    components_json TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    input_snapshot_hash TEXT NOT NULL,
                    random_seed INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_versions (
                    version_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    version_kind TEXT NOT NULL DEFAULT 'estimate'
                        CHECK (version_kind IN ('estimate','projection')),
                    estimate_set_id TEXT REFERENCES forecast_estimate_sets(estimate_set_id),
                    projection_set_id TEXT REFERENCES forecast_projection_sets(projection_set_id),
                    input_snapshot_hash TEXT NOT NULL,
                    snapshot_artifact_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    CHECK (
                        (version_kind = 'estimate' AND estimate_set_id IS NOT NULL AND projection_set_id IS NULL)
                        OR
                        (version_kind = 'projection' AND projection_set_id IS NOT NULL AND estimate_set_id IS NULL)
                    )
                );

                CREATE UNIQUE INDEX IF NOT EXISTS forecast_versions_estimate_unique
                ON forecast_versions(estimate_set_id)
                WHERE estimate_set_id IS NOT NULL;

                CREATE UNIQUE INDEX IF NOT EXISTS forecast_versions_projection_unique
                ON forecast_versions(projection_set_id)
                WHERE projection_set_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS forecast_reviews (
                    review_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    framing_version INTEGER,
                    estimate_set_id TEXT,
                    projection_set_id TEXT,
                    version_id TEXT,
                    action TEXT NOT NULL,
                    comment TEXT,
                    reviewer TEXT,
                    reviewer_auth_subject TEXT,
                    policy_decision_id TEXT,
                    review_reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_resolutions (
                    resolution_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL UNIQUE REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    version_id TEXT NOT NULL REFERENCES forecast_versions(version_id),
                    outcome_id TEXT NOT NULL REFERENCES forecast_outcomes(outcome_id),
                    multiclass_brier REAL NOT NULL,
                    log_score REAL NOT NULL,
                    scorer_version TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_audit_events (
                    event_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_idempotency_keys (
                    command_scope TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (command_scope, resource_id, idempotency_key)
                );

                CREATE TRIGGER IF NOT EXISTS forecast_audit_events_no_update
                BEFORE UPDATE ON forecast_audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_audit_events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_audit_events_no_delete
                BEFORE DELETE ON forecast_audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_audit_events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_reviews_no_update
                BEFORE UPDATE ON forecast_reviews
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_reviews are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_reviews_no_delete
                BEFORE DELETE ON forecast_reviews
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_reviews are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_policy_decisions_no_update
                BEFORE UPDATE ON forecast_policy_decisions
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_policy_decisions are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_policy_decisions_no_delete
                BEFORE DELETE ON forecast_policy_decisions
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_policy_decisions are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_versions_no_update
                BEFORE UPDATE ON forecast_versions
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_versions are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_versions_no_delete
                BEFORE DELETE ON forecast_versions
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_versions are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_estimate_sets_no_frozen_update
                BEFORE UPDATE ON forecast_estimate_sets
                WHEN OLD.status = 'frozen'
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_estimate_sets are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_estimate_sets_no_frozen_delete
                BEFORE DELETE ON forecast_estimate_sets
                WHEN OLD.status = 'frozen'
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_estimate_sets are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_probability_estimates_no_frozen_update
                BEFORE UPDATE ON forecast_probability_estimates
                WHEN EXISTS (
                    SELECT 1 FROM forecast_estimate_sets
                    WHERE estimate_set_id = OLD.estimate_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_probability_estimates are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_probability_estimates_no_frozen_delete
                BEFORE DELETE ON forecast_probability_estimates
                WHEN EXISTS (
                    SELECT 1 FROM forecast_estimate_sets
                    WHERE estimate_set_id = OLD.estimate_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_probability_estimates are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_sets_no_frozen_update
                BEFORE UPDATE ON forecast_projection_sets
                WHEN OLD.status = 'frozen'
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_sets are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_sets_no_frozen_delete
                BEFORE DELETE ON forecast_projection_sets
                WHEN OLD.status = 'frozen'
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_sets are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_dimensions_no_frozen_update
                BEFORE UPDATE ON forecast_projection_dimensions
                WHEN OLD.frozen = 1
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_dimensions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_dimensions_no_frozen_delete
                BEFORE DELETE ON forecast_projection_dimensions
                WHEN OLD.frozen = 1
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_dimensions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_scenarios_forecast_owner_insert
                BEFORE INSERT ON forecast_projection_scenarios
                WHEN NOT EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = NEW.projection_set_id
                      AND forecast_id = NEW.forecast_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_projection_scenarios forecast ownership mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_scenarios_forecast_owner_update
                BEFORE UPDATE ON forecast_projection_scenarios
                WHEN NOT EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = NEW.projection_set_id
                      AND forecast_id = NEW.forecast_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_projection_scenarios forecast ownership mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_metric_points_forecast_owner_insert
                BEFORE INSERT ON forecast_projection_metric_points
                WHEN NOT EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = NEW.projection_set_id
                      AND forecast_id = NEW.forecast_id
                )
                OR NOT EXISTS (
                    SELECT 1 FROM forecast_projection_scenarios
                    WHERE projection_scenario_id = NEW.projection_scenario_id
                      AND forecast_id = NEW.forecast_id
                )
                OR NOT EXISTS (
                    SELECT 1 FROM forecast_projection_dimensions
                    WHERE dimension_id = NEW.dimension_id
                      AND forecast_id = NEW.forecast_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_projection_metric_points forecast ownership mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_metric_points_forecast_owner_update
                BEFORE UPDATE ON forecast_projection_metric_points
                WHEN NOT EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = NEW.projection_set_id
                      AND forecast_id = NEW.forecast_id
                )
                OR NOT EXISTS (
                    SELECT 1 FROM forecast_projection_scenarios
                    WHERE projection_scenario_id = NEW.projection_scenario_id
                      AND forecast_id = NEW.forecast_id
                )
                OR NOT EXISTS (
                    SELECT 1 FROM forecast_projection_dimensions
                    WHERE dimension_id = NEW.dimension_id
                      AND forecast_id = NEW.forecast_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_projection_metric_points forecast ownership mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_composites_forecast_owner_insert
                BEFORE INSERT ON forecast_projection_composites
                WHEN NOT EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = NEW.projection_set_id
                      AND forecast_id = NEW.forecast_id
                )
                OR NOT EXISTS (
                    SELECT 1 FROM forecast_projection_dimensions
                    WHERE dimension_id = NEW.dimension_id
                      AND forecast_id = NEW.forecast_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_projection_composites forecast ownership mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_composites_forecast_owner_update
                BEFORE UPDATE ON forecast_projection_composites
                WHEN NOT EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = NEW.projection_set_id
                      AND forecast_id = NEW.forecast_id
                )
                OR NOT EXISTS (
                    SELECT 1 FROM forecast_projection_dimensions
                    WHERE dimension_id = NEW.dimension_id
                      AND forecast_id = NEW.forecast_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_projection_composites forecast ownership mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_sensitivities_forecast_owner_insert
                BEFORE INSERT ON forecast_projection_sensitivities
                WHEN NOT EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = NEW.projection_set_id
                      AND forecast_id = NEW.forecast_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_projection_sensitivities forecast ownership mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_sensitivities_forecast_owner_update
                BEFORE UPDATE ON forecast_projection_sensitivities
                WHEN NOT EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = NEW.projection_set_id
                      AND forecast_id = NEW.forecast_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_projection_sensitivities forecast ownership mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_evidence_links_forecast_owner_insert
                BEFORE INSERT ON forecast_projection_evidence_links
                WHEN (
                    NEW.projection_set_id IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM forecast_projection_sets
                        WHERE projection_set_id = NEW.projection_set_id
                          AND forecast_id = NEW.forecast_id
                    )
                )
                OR (
                    NEW.dimension_id IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM forecast_projection_dimensions
                        WHERE dimension_id = NEW.dimension_id
                          AND forecast_id = NEW.forecast_id
                    )
                )
                OR (
                    NEW.projection_scenario_id IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM forecast_projection_scenarios
                        WHERE projection_scenario_id = NEW.projection_scenario_id
                          AND forecast_id = NEW.forecast_id
                    )
                )
                OR NOT EXISTS (
                    SELECT 1 FROM forecast_claims
                    WHERE claim_id = NEW.claim_id
                      AND forecast_id = NEW.forecast_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_projection_evidence_links forecast ownership mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_evidence_links_forecast_owner_update
                BEFORE UPDATE ON forecast_projection_evidence_links
                WHEN (
                    NEW.projection_set_id IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM forecast_projection_sets
                        WHERE projection_set_id = NEW.projection_set_id
                          AND forecast_id = NEW.forecast_id
                    )
                )
                OR (
                    NEW.dimension_id IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM forecast_projection_dimensions
                        WHERE dimension_id = NEW.dimension_id
                          AND forecast_id = NEW.forecast_id
                    )
                )
                OR (
                    NEW.projection_scenario_id IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM forecast_projection_scenarios
                        WHERE projection_scenario_id = NEW.projection_scenario_id
                          AND forecast_id = NEW.forecast_id
                    )
                )
                OR NOT EXISTS (
                    SELECT 1 FROM forecast_claims
                    WHERE claim_id = NEW.claim_id
                      AND forecast_id = NEW.forecast_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_projection_evidence_links forecast ownership mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_scenarios_no_frozen_insert
                BEFORE INSERT ON forecast_projection_scenarios
                WHEN EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = NEW.projection_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_scenarios are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_scenarios_no_frozen_update
                BEFORE UPDATE ON forecast_projection_scenarios
                WHEN EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = OLD.projection_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_scenarios are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_scenarios_no_frozen_delete
                BEFORE DELETE ON forecast_projection_scenarios
                WHEN EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = OLD.projection_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_scenarios are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_metric_points_no_frozen_insert
                BEFORE INSERT ON forecast_projection_metric_points
                WHEN EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = NEW.projection_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_metric_points are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_metric_points_no_frozen_update
                BEFORE UPDATE ON forecast_projection_metric_points
                WHEN EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = OLD.projection_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_metric_points are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_metric_points_no_frozen_delete
                BEFORE DELETE ON forecast_projection_metric_points
                WHEN EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = OLD.projection_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_metric_points are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_composites_no_frozen_insert
                BEFORE INSERT ON forecast_projection_composites
                WHEN EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = NEW.projection_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_composites are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_composites_no_frozen_update
                BEFORE UPDATE ON forecast_projection_composites
                WHEN EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = OLD.projection_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_composites are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_composites_no_frozen_delete
                BEFORE DELETE ON forecast_projection_composites
                WHEN EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = OLD.projection_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_composites are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_sensitivities_no_frozen_insert
                BEFORE INSERT ON forecast_projection_sensitivities
                WHEN EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = NEW.projection_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_sensitivities are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_sensitivities_no_frozen_update
                BEFORE UPDATE ON forecast_projection_sensitivities
                WHEN EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = OLD.projection_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_sensitivities are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS forecast_projection_sensitivities_no_frozen_delete
                BEFORE DELETE ON forecast_projection_sensitivities
                WHEN EXISTS (
                    SELECT 1 FROM forecast_projection_sets
                    WHERE projection_set_id = OLD.projection_set_id
                      AND status = 'frozen'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'frozen forecast_projection_sensitivities are immutable');
                END;
                """
            )
            _ensure_column(
                connection,
                table_name="forecast_forecasts",
                column_name="forecast_mode",
                ddl=(
                    "forecast_mode TEXT NOT NULL DEFAULT 'discrete_outcome' "
                    "CHECK (forecast_mode IN ('discrete_outcome','scenario_projection'))"
                ),
            )
            _ensure_column(
                connection,
                table_name="forecast_forecasts",
                column_name="original_execution_prompt",
                ddl="original_execution_prompt TEXT",
            )
            _ensure_column(
                connection,
                table_name="forecast_policy_decisions",
                column_name="decision",
                ddl="decision TEXT NOT NULL DEFAULT 'allowed'",
            )
            _ensure_column(
                connection,
                table_name="forecast_policy_decisions",
                column_name="policy_version",
                ddl="policy_version TEXT NOT NULL DEFAULT 'phase_a_v1'",
            )
            _ensure_column(
                connection,
                table_name="forecast_policy_decisions",
                column_name="data_classification",
                ddl="data_classification TEXT NOT NULL DEFAULT 'public'",
            )
            _ensure_column(
                connection,
                table_name="forecast_policy_decisions",
                column_name="resolved_tools_json",
                ddl="resolved_tools_json TEXT NOT NULL DEFAULT '[]'",
            )
            _ensure_column(
                connection,
                table_name="forecast_policy_decisions",
                column_name="vector_store_ids_json",
                ddl="vector_store_ids_json TEXT NOT NULL DEFAULT '[]'",
            )
            _ensure_column(
                connection,
                table_name="forecast_policy_decisions",
                column_name="mcp_server_ids_json",
                ddl="mcp_server_ids_json TEXT NOT NULL DEFAULT '[]'",
            )
            _ensure_column(
                connection,
                table_name="forecast_policy_decisions",
                column_name="background",
                ddl="background INTEGER NOT NULL DEFAULT 1",
            )
            _ensure_column(
                connection,
                table_name="forecast_policy_decisions",
                column_name="blocked_terms_json",
                ddl="blocked_terms_json TEXT NOT NULL DEFAULT '[]'",
            )
            for column_name, ddl in {
                "attempt_no": "attempt_no INTEGER NOT NULL DEFAULT 1",
                "is_active": "is_active INTEGER NOT NULL DEFAULT 1",
                "rerun_of_pack_id": "rerun_of_pack_id TEXT REFERENCES forecast_research_packs(pack_id)",
                "timeout_sec": "timeout_sec INTEGER",
                "estimated_cost_budget_usd": "estimated_cost_budget_usd REAL",
                "vector_store_ids_json": "vector_store_ids_json TEXT NOT NULL DEFAULT '[]'",
                "mcp_server_ids_json": "mcp_server_ids_json TEXT NOT NULL DEFAULT '[]'",
                "cache_key": "cache_key TEXT",
                "rerun_policy": "rerun_policy TEXT",
                "pack_request_id": "pack_request_id TEXT",
                "data_classification": "data_classification TEXT NOT NULL DEFAULT 'public'",
            }.items():
                _ensure_column(
                    connection,
                    table_name="forecast_research_packs",
                    column_name=column_name,
                    ddl=ddl,
                )
            for column_name, ddl in {
                "data_classification": "data_classification TEXT NOT NULL DEFAULT 'public'",
                "origin_tool_profile": "origin_tool_profile TEXT NOT NULL DEFAULT 'public'",
            }.items():
                _ensure_column(
                    connection,
                    table_name="forecast_sources",
                    column_name=column_name,
                    ddl=ddl,
                )
            for column_name, ddl in {
                "data_classification": "data_classification TEXT NOT NULL DEFAULT 'public'",
                "origin_tool_profile": "origin_tool_profile TEXT NOT NULL DEFAULT 'public'",
                "pack_id": "pack_id TEXT REFERENCES forecast_research_packs(pack_id) ON DELETE SET NULL",
                "extraction_batch_id": "extraction_batch_id TEXT",
                "report_artifact_hash": "report_artifact_hash TEXT",
                "manual_locked": "manual_locked INTEGER NOT NULL DEFAULT 0",
                "origin": "origin TEXT NOT NULL DEFAULT 'extractor'",
            }.items():
                _ensure_column(
                    connection,
                    table_name="forecast_claims",
                    column_name=column_name,
                    ddl=ddl,
                )
            for column_name, ddl in {
                "reviewer": "reviewer TEXT",
                "reviewer_auth_subject": "reviewer_auth_subject TEXT",
                "policy_decision_id": "policy_decision_id TEXT",
                "review_reason": "review_reason TEXT",
            }.items():
                _ensure_column(
                    connection,
                    table_name="forecast_reviews",
                    column_name=column_name,
                    ddl=ddl,
                )
            _ensure_column(
                connection,
                table_name="forecast_reviews",
                column_name="projection_set_id",
                ddl="projection_set_id TEXT",
            )
            self._migrate_forecast_versions_projection_columns(connection)
            for column_name, ddl in {
                "allowed_vector_store_ids_json": "allowed_vector_store_ids_json TEXT NOT NULL DEFAULT '[]'",
                "allowed_mcp_server_ids_json": "allowed_mcp_server_ids_json TEXT NOT NULL DEFAULT '[]'",
            }.items():
                _ensure_column(
                    connection,
                    table_name="forecast_trusted_sources",
                    column_name=column_name,
                    ddl=ddl,
                )
            connection.execute("DROP INDEX IF EXISTS forecast_research_packs_phase_a_unique")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS forecast_research_packs_active_unique
                ON forecast_research_packs(forecast_id, pack_role, tool_profile)
                WHERE is_active = 1
                """
            )
            connection.execute(
                """
                UPDATE forecast_sources
                SET data_classification = source_classification,
                    origin_tool_profile = source_classification
                WHERE source_classification IN ('public', 'private')
                  AND data_classification = 'public'
                """
            )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"forecast_repository_foreign_key_check_failed: {violations}")

    def _migrate_forecast_versions_projection_columns(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        if not _table_exists(connection, "forecast_versions"):
            return
        columns = _table_columns(connection, "forecast_versions")
        estimate_notnull = _column_notnull(
            connection,
            table_name="forecast_versions",
            column_name="estimate_set_id",
        )
        if (
            {"version_kind", "projection_set_id"}.issubset(columns)
            and estimate_notnull == 0
        ):
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS forecast_versions_estimate_unique
                ON forecast_versions(estimate_set_id)
                WHERE estimate_set_id IS NOT NULL
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS forecast_versions_projection_unique
                ON forecast_versions(projection_set_id)
                WHERE projection_set_id IS NOT NULL
                """
            )
            return

        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        try:
            connection.executescript(
                """
                DROP TRIGGER IF EXISTS forecast_versions_no_update;
                DROP TRIGGER IF EXISTS forecast_versions_no_delete;
                DROP INDEX IF EXISTS forecast_versions_estimate_unique;
                DROP INDEX IF EXISTS forecast_versions_projection_unique;
                ALTER TABLE forecast_versions RENAME TO forecast_versions_old;
                CREATE TABLE forecast_versions (
                    version_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    version_kind TEXT NOT NULL DEFAULT 'estimate'
                        CHECK (version_kind IN ('estimate','projection')),
                    estimate_set_id TEXT REFERENCES forecast_estimate_sets(estimate_set_id),
                    projection_set_id TEXT REFERENCES forecast_projection_sets(projection_set_id),
                    input_snapshot_hash TEXT NOT NULL,
                    snapshot_artifact_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    CHECK (
                        (version_kind = 'estimate' AND estimate_set_id IS NOT NULL AND projection_set_id IS NULL)
                        OR
                        (version_kind = 'projection' AND projection_set_id IS NOT NULL AND estimate_set_id IS NULL)
                    )
                );
                INSERT INTO forecast_versions (
                    version_id, forecast_id, version_kind, estimate_set_id,
                    projection_set_id, input_snapshot_hash, snapshot_artifact_path,
                    created_at
                )
                SELECT
                    version_id, forecast_id, 'estimate', estimate_set_id,
                    NULL, input_snapshot_hash, snapshot_artifact_path, created_at
                FROM forecast_versions_old;
                DROP TABLE forecast_versions_old;
                CREATE UNIQUE INDEX forecast_versions_estimate_unique
                ON forecast_versions(estimate_set_id)
                WHERE estimate_set_id IS NOT NULL;
                CREATE UNIQUE INDEX forecast_versions_projection_unique
                ON forecast_versions(projection_set_id)
                WHERE projection_set_id IS NOT NULL;
                CREATE TRIGGER forecast_versions_no_update
                BEFORE UPDATE ON forecast_versions
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_versions are append-only');
                END;
                CREATE TRIGGER forecast_versions_no_delete
                BEFORE DELETE ON forecast_versions
                BEGIN
                    SELECT RAISE(ABORT, 'forecast_versions are append-only');
                END;
                """
            )
        finally:
            connection.execute("PRAGMA legacy_alter_table = OFF")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                UPDATE forecast_claims
                SET data_classification = source_classification,
                    origin_tool_profile = source_classification
                WHERE source_classification IN ('public', 'private')
                  AND data_classification = 'public'
                """
            )

    def append_audit(
        self,
        connection: sqlite3.Connection,
        forecast_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO forecast_audit_events (
                event_id, forecast_id, event_type, event_json, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                str(forecast_id),
                event_type,
                _dump(payload),
                utc_now().isoformat(),
            ),
        )

    def get_idempotency_record(
        self,
        *,
        command_scope: str,
        resource_id: str,
        idempotency_key: str,
    ) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_idempotency_keys
                WHERE command_scope = ? AND resource_id = ? AND idempotency_key = ?
                """,
                (command_scope, resource_id, idempotency_key),
            ).fetchone()

    def reserve_idempotency_record(
        self,
        *,
        command_scope: str,
        resource_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> sqlite3.Row | None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM forecast_idempotency_keys
                WHERE command_scope = ? AND resource_id = ? AND idempotency_key = ?
                """,
                (command_scope, resource_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                return existing
            connection.execute(
                """
                INSERT INTO forecast_idempotency_keys (
                    command_scope, resource_id, idempotency_key, request_hash,
                    response_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    command_scope,
                    resource_id,
                    idempotency_key,
                    request_hash,
                    IDEMPOTENCY_IN_PROGRESS,
                    utc_now().isoformat(),
                ),
            )
        return None

    def complete_idempotency_record(
        self,
        *,
        command_scope: str,
        resource_id: str,
        idempotency_key: str,
        request_hash: str,
        response: dict[str, Any] | list[Any],
    ) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE forecast_idempotency_keys
                SET response_json = ?
                WHERE command_scope = ? AND resource_id = ? AND idempotency_key = ?
                  AND request_hash = ?
                """,
                (
                    _dump(response),
                    command_scope,
                    resource_id,
                    idempotency_key,
                    request_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("idempotency_record_not_reserved")

    def delete_idempotency_record(
        self,
        *,
        command_scope: str,
        resource_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                DELETE FROM forecast_idempotency_keys
                WHERE command_scope = ? AND resource_id = ? AND idempotency_key = ?
                  AND request_hash = ? AND response_json = ?
                """,
                (
                    command_scope,
                    resource_id,
                    idempotency_key,
                    request_hash,
                    IDEMPOTENCY_IN_PROGRESS,
                ),
            )

    def create_forecast(
        self,
        *,
        question: str,
        original_execution_prompt: str | None,
        resolution_date: date | None,
        target_population: str | None,
        unit_of_analysis: str | None,
        resolution_criteria: str,
        resolution_sources: list[str],
        decision_context: str | None,
        confidentiality_class: str,
        forecast_mode: str,
        outcome_labels: list[str],
        projection_dimensions: list[dict[str, Any]] | None = None,
        idempotency_key: str | None,
    ) -> sqlite3.Row:
        if idempotency_key:
            existing = self.get_forecast_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        now = utc_now().isoformat()
        forecast_id = uuid4()
        normalization_group_id = f"ng-{forecast_id}"
        if forecast_mode == ForecastMode.SCENARIO_PROJECTION.value:
            labels: list[str] = []
        else:
            labels = [label.strip() for label in outcome_labels if label.strip()] or [
                "Yes",
                "No",
            ]
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO forecast_forecasts (
                    id, question, original_execution_prompt, resolution_date,
                    target_population, unit_of_analysis,
                    resolution_criteria, resolution_sources_json, decision_context,
                    confidentiality_class, forecast_mode, status, current_framing_version,
                    idempotency_key, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    str(forecast_id),
                    question,
                    original_execution_prompt,
                    resolution_date.isoformat() if resolution_date else None,
                    target_population,
                    unit_of_analysis,
                    resolution_criteria,
                    _dump(resolution_sources),
                    decision_context,
                    confidentiality_class,
                    forecast_mode,
                    ForecastStatus.FRAMING_PENDING.value,
                    idempotency_key,
                    now,
                    now,
                ),
            )
            for index, label in enumerate(labels):
                connection.execute(
                    """
                    INSERT INTO forecast_outcomes (
                        outcome_id, forecast_id, framing_version, label, definition,
                        resolution_rule, exclusive_group_id, normalization_group_id,
                        sort_order, created_at
                    )
                    VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        str(forecast_id),
                        label,
                        f"Outcome '{label}' resolves for the forecast question.",
                        resolution_criteria or "Resolution follows the approved forecast criteria.",
                        normalization_group_id,
                        normalization_group_id,
                        index,
                        now,
                    ),
                )
            for index, dimension in enumerate(projection_dimensions or []):
                connection.execute(
                    """
                    INSERT INTO forecast_projection_dimensions (
                        dimension_id, forecast_id, framing_version, metric_id, label,
                        unit, value_type, currency, nominal_or_real, baseline_year,
                        baseline_value, baseline_source_ids_json, horizons_json,
                        sort_order, created_at, updated_at
                    )
                    VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        str(forecast_id),
                        dimension["metric_id"],
                        dimension["label"],
                        dimension["unit"],
                        dimension["value_type"],
                        dimension.get("currency"),
                        dimension.get("nominal_or_real"),
                        dimension["baseline_year"],
                        dimension["baseline_value"],
                        _dump([str(item) for item in dimension.get("baseline_source_ids", [])]),
                        _dump(dimension["horizons"]),
                        index,
                        now,
                        now,
                    ),
                )
            self.append_audit(
                connection,
                forecast_id,
                "forecast_created",
                {
                    "framing_version": 1,
                    "forecast_mode": forecast_mode,
                    "outcome_count": len(labels),
                    "projection_dimension_count": len(projection_dimensions or []),
                },
            )
            row = connection.execute(
                "SELECT * FROM forecast_forecasts WHERE id = ?",
                (str(forecast_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(forecast_id))
        return row

    def get_forecast_by_idempotency_key(self, key: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM forecast_forecasts WHERE idempotency_key = ?",
                (key,),
            ).fetchone()

    def get_forecast(self, forecast_id: UUID) -> sqlite3.Row:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM forecast_forecasts WHERE id = ?",
                (str(forecast_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(forecast_id))
        return row

    def list_forecasts(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM forecast_forecasts ORDER BY created_at DESC"
            ).fetchall()

    def get_outcomes(
        self,
        forecast_id: UUID,
        *,
        framing_version: int | None = None,
    ) -> list[sqlite3.Row]:
        with self.connect() as connection:
            if framing_version is None:
                rows = connection.execute(
                    """
                    SELECT * FROM forecast_outcomes
                    WHERE forecast_id = ?
                    ORDER BY sort_order, label
                    """,
                    (str(forecast_id),),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM forecast_outcomes
                    WHERE forecast_id = ? AND framing_version = ?
                    ORDER BY sort_order, label
                    """,
                    (str(forecast_id), framing_version),
                ).fetchall()
        return rows

    def get_projection_dimensions(
        self,
        forecast_id: UUID,
        *,
        framing_version: int | None = None,
    ) -> list[sqlite3.Row]:
        with self.connect() as connection:
            if framing_version is None:
                return connection.execute(
                    """
                    SELECT * FROM forecast_projection_dimensions
                    WHERE forecast_id = ?
                    ORDER BY sort_order, metric_id
                    """,
                    (str(forecast_id),),
                ).fetchall()
            return connection.execute(
                """
                SELECT * FROM forecast_projection_dimensions
                WHERE forecast_id = ? AND framing_version = ?
                ORDER BY sort_order, metric_id
                """,
                (str(forecast_id), framing_version),
            ).fetchall()

    def approve_framing(self, forecast_id: UUID, *, comment: str | None) -> sqlite3.Row:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM forecast_forecasts WHERE id = ?",
                (str(forecast_id),),
            ).fetchone()
            if row is None:
                raise KeyError(str(forecast_id))
            version = int(row["current_framing_version"])
            connection.execute(
                """
                UPDATE forecast_outcomes
                SET frozen = 1
                WHERE forecast_id = ? AND framing_version = ?
                """,
                (str(forecast_id), version),
            )
            connection.execute(
                """
                UPDATE forecast_projection_dimensions
                SET frozen = 1, updated_at = ?
                WHERE forecast_id = ? AND framing_version = ?
                """,
                (now, str(forecast_id), version),
            )
            connection.execute(
                """
                UPDATE forecast_forecasts
                SET approved_framing_version = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    version,
                    ForecastStatus.FRAMING_APPROVED.value,
                    now,
                    str(forecast_id),
                ),
            )
            connection.execute(
                """
                INSERT INTO forecast_reviews (
                    review_id, forecast_id, framing_version, action, comment, created_at
                )
                VALUES (?, ?, ?, 'approve_framing', ?, ?)
                """,
                (str(uuid4()), str(forecast_id), version, comment, now),
            )
            self.append_audit(
                connection,
                forecast_id,
                "framing_approved",
                {"framing_version": version},
            )
            updated = connection.execute(
                "SELECT * FROM forecast_forecasts WHERE id = ?",
                (str(forecast_id),),
            ).fetchone()
        if updated is None:
            raise KeyError(str(forecast_id))
        return updated

    def add_policy_decision(
        self,
        *,
        forecast_id: UUID,
        profile: str,
        status: str,
        reason: str | None,
        prompt_hash: str,
        decision: str | None = None,
        policy_version: str = "phase_a_v1",
        data_classification: str = "public",
        resolved_tools: list[dict[str, Any]] | None = None,
        vector_store_ids: list[str] | None = None,
        mcp_server_ids: list[str] | None = None,
        background: bool = True,
        blocked_terms: list[str] | None = None,
    ) -> UUID:
        decision_id = uuid4()
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO forecast_policy_decisions (
                    policy_decision_id, forecast_id, profile, status, reason,
                    prompt_hash, decision, policy_version, data_classification,
                    resolved_tools_json, vector_store_ids_json, mcp_server_ids_json,
                    background, blocked_terms_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(decision_id),
                    str(forecast_id),
                    profile,
                    status,
                    reason,
                    prompt_hash,
                    decision or status,
                    policy_version,
                    data_classification,
                    _dump(resolved_tools or []),
                    _dump(vector_store_ids or []),
                    _dump(mcp_server_ids or []),
                    1 if background else 0,
                    _dump(blocked_terms or []),
                    now,
                ),
            )
            self.append_audit(
                connection,
                forecast_id,
                "policy_decision_recorded",
                {
                    "policy_decision_id": str(decision_id),
                    "profile": profile,
                    "status": status,
                    "reason": reason,
                    "policy_version": policy_version,
                    "data_classification": data_classification,
                },
            )
        return decision_id

    def add_research_pack(
        self,
        *,
        forecast_id: UUID,
        research_run_id: UUID,
        pack_role: str,
        tool_profile: str,
        status: str,
        model_deployment: str | None,
        prompt_version: str,
        max_tool_calls: int,
        policy_decision_id: UUID,
        attempt_no: int = 1,
        is_active: bool = True,
        rerun_of_pack_id: UUID | None = None,
        timeout_sec: int | None = None,
        estimated_cost_budget_usd: float | None = None,
        vector_store_ids: list[str] | None = None,
        mcp_server_ids: list[str] | None = None,
        cache_key: str | None = None,
        rerun_policy: str | None = None,
        pack_request_id: UUID | None = None,
        data_classification: str = "public",
        replace_active_pack_id: UUID | None = None,
    ) -> sqlite3.Row:
        pack_id = uuid4()
        now = utc_now().isoformat()
        with self.connect() as connection:
            try:
                if replace_active_pack_id is not None:
                    cursor = connection.execute(
                        """
                        UPDATE forecast_research_packs
                        SET is_active = 0, updated_at = ?
                        WHERE pack_id = ? AND forecast_id = ? AND is_active = 1
                        """,
                        (now, str(replace_active_pack_id), str(forecast_id)),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("active_pack_changed")
                connection.execute(
                    """
                    INSERT INTO forecast_research_packs (
                        pack_id, forecast_id, research_run_id, pack_role, tool_profile,
                        status, model_deployment, prompt_version, max_tool_calls,
                        policy_decision_id, attempt_no, is_active, rerun_of_pack_id,
                        timeout_sec, estimated_cost_budget_usd, vector_store_ids_json,
                        mcp_server_ids_json, cache_key, rerun_policy, pack_request_id,
                        data_classification, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(pack_id),
                        str(forecast_id),
                        str(research_run_id),
                        pack_role,
                        tool_profile,
                        status,
                        model_deployment,
                        prompt_version,
                        max_tool_calls,
                        str(policy_decision_id),
                        attempt_no,
                        1 if is_active else 0,
                        str(rerun_of_pack_id) if rerun_of_pack_id else None,
                        timeout_sec,
                        estimated_cost_budget_usd,
                        _dump(vector_store_ids or []),
                        _dump(mcp_server_ids or []),
                        cache_key,
                        rerun_policy,
                        str(pack_request_id) if pack_request_id else None,
                        data_classification,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                if not _is_phase_a_research_pack_unique_error(error):
                    raise
                existing = connection.execute(
                    """
                    SELECT * FROM forecast_research_packs
                    WHERE forecast_id = ? AND pack_role = ? AND tool_profile = ?
                      AND is_active = 1
                    """,
                    (str(forecast_id), pack_role, tool_profile),
                ).fetchone()
                if existing is None:
                    raise
                raise ResearchPackAlreadyExists(existing) from error
            connection.execute(
                """
                UPDATE forecast_forecasts
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (ForecastStatus.PACK_RUNNING.value, now, str(forecast_id)),
            )
            self.append_audit(
                connection,
                forecast_id,
                "research_pack_dispatched",
                {
                    "pack_id": str(pack_id),
                    "research_run_id": str(research_run_id),
                    "pack_role": pack_role,
                    "tool_profile": tool_profile,
                    "attempt_no": attempt_no,
                    "data_classification": data_classification,
                },
            )
            row = connection.execute(
                "SELECT * FROM forecast_research_packs WHERE pack_id = ?",
                (str(pack_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(pack_id))
        return row

    def list_packs(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_research_packs
                WHERE forecast_id = ?
                ORDER BY created_at
                """,
                (str(forecast_id),),
            ).fetchall()

    def list_active_packs(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_research_packs
                WHERE forecast_id = ? AND is_active = 1
                ORDER BY pack_role, tool_profile, created_at
                """,
                (str(forecast_id),),
            ).fetchall()

    def get_pack(self, pack_id: UUID) -> sqlite3.Row:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM forecast_research_packs WHERE pack_id = ?",
                (str(pack_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(pack_id))
        return row

    def deactivate_pack(self, pack_id: UUID) -> None:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE forecast_research_packs
                SET is_active = 0, updated_at = ?
                WHERE pack_id = ?
                """,
                (now, str(pack_id)),
            )

    def add_pack_request(
        self,
        *,
        forecast_id: UUID,
        pack_role: str,
        tool_profile: str,
        data_classification: str,
        status: str,
        reason: str | None,
        policy_decision_id: UUID | None,
        request_payload: dict[str, Any],
        reviewer: str | None = None,
        reviewer_auth_subject: str | None = None,
    ) -> UUID:
        pack_request_id = uuid4()
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO forecast_pack_requests (
                    pack_request_id, forecast_id, pack_role, tool_profile,
                    data_classification, status, reason, policy_decision_id,
                    reviewer, reviewer_auth_subject, request_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(pack_request_id),
                    str(forecast_id),
                    pack_role,
                    tool_profile,
                    data_classification,
                    status,
                    reason,
                    str(policy_decision_id) if policy_decision_id else None,
                    reviewer,
                    reviewer_auth_subject,
                    _dump(request_payload),
                    now,
                    now,
                ),
            )
            self.append_audit(
                connection,
                forecast_id,
                "pack_request_recorded",
                {
                    "pack_request_id": str(pack_request_id),
                    "pack_role": pack_role,
                    "tool_profile": tool_profile,
                    "data_classification": data_classification,
                    "status": status,
                    "reason": reason,
                },
            )
        return pack_request_id

    def get_policy_decision(self, policy_decision_id: UUID) -> sqlite3.Row:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM forecast_policy_decisions
                WHERE policy_decision_id = ?
                """,
                (str(policy_decision_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(policy_decision_id))
        return row

    def get_trusted_source(self, identifier: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_trusted_sources
                WHERE identifier = ?
                """,
                (identifier,),
            ).fetchone()

    def upsert_trusted_source(
        self,
        *,
        identifier: str,
        status: str,
        approved_by: str | None = None,
        approved_at: datetime | None = None,
        expires_at: datetime | None = None,
        allowed_profiles: list[str] | None = None,
        allowed_pack_roles: list[str] | None = None,
        allowed_tool_names: list[str] | None = None,
        allowed_vector_store_ids: list[str] | None = None,
        allowed_mcp_server_ids: list[str] | None = None,
        owner_team_id: str | None = None,
    ) -> sqlite3.Row:
        now = utc_now().isoformat()
        trusted_source_id = uuid4()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO forecast_trusted_sources (
                    trusted_source_id, identifier, status, approved_by, approved_at,
                    expires_at, allowed_profiles_json, allowed_pack_roles_json,
                    allowed_tool_names_json, allowed_vector_store_ids_json,
                    allowed_mcp_server_ids_json, owner_team_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identifier) DO UPDATE SET
                    status = excluded.status,
                    approved_by = excluded.approved_by,
                    approved_at = excluded.approved_at,
                    expires_at = excluded.expires_at,
                    allowed_profiles_json = excluded.allowed_profiles_json,
                    allowed_pack_roles_json = excluded.allowed_pack_roles_json,
                    allowed_tool_names_json = excluded.allowed_tool_names_json,
                    allowed_vector_store_ids_json = excluded.allowed_vector_store_ids_json,
                    allowed_mcp_server_ids_json = excluded.allowed_mcp_server_ids_json,
                    owner_team_id = excluded.owner_team_id,
                    updated_at = excluded.updated_at
                """,
                (
                    str(trusted_source_id),
                    identifier,
                    status,
                    approved_by,
                    approved_at.isoformat() if approved_at else None,
                    expires_at.isoformat() if expires_at else None,
                    _dump(allowed_profiles or []),
                    _dump(allowed_pack_roles or []),
                    _dump(allowed_tool_names or []),
                    _dump(allowed_vector_store_ids or []),
                    _dump(allowed_mcp_server_ids or []),
                    owner_team_id,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM forecast_trusted_sources WHERE identifier = ?",
                (identifier,),
            ).fetchone()
        if row is None:
            raise KeyError(identifier)
        return row

    def update_research_pack_status(
        self,
        *,
        pack_id: UUID,
        status: str,
        report_artifact_hash: str | None | object = _UNSET,
    ) -> sqlite3.Row:
        now = utc_now().isoformat()
        assignments = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, now]
        if report_artifact_hash is not _UNSET:
            assignments.append("report_artifact_hash = ?")
            values.append(report_artifact_hash)
        values.append(str(pack_id))
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE forecast_research_packs
                SET {", ".join(assignments)}
                WHERE pack_id = ?
                """,
                values,
            )
            row = connection.execute(
                "SELECT * FROM forecast_research_packs WHERE pack_id = ?",
                (str(pack_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(pack_id))
        return row

    def mark_pack_completed(
        self,
        *,
        pack_id: UUID,
        report_artifact_hash: str | None,
    ) -> None:
        self.update_research_pack_status(
            pack_id=pack_id,
            status="completed",
            report_artifact_hash=report_artifact_hash,
        )

    def replace_evidence(
        self,
        *,
        forecast_id: UUID,
        pack_id: UUID,
        sources: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        links: list[dict[str, Any]],
    ) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM forecast_claim_target_links WHERE forecast_id = ?",
                (str(forecast_id),),
            )
            connection.execute(
                "DELETE FROM forecast_claims WHERE forecast_id = ?",
                (str(forecast_id),),
            )
            connection.execute(
                "DELETE FROM forecast_sources WHERE forecast_id = ?",
                (str(forecast_id),),
            )
            for source in sources:
                connection.execute(
                    """
                    INSERT INTO forecast_sources (
                        source_id, forecast_id, pack_id, title, publisher, url,
                        source_type, source_classification, reliability_score,
                        metadata_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source["source_id"],
                        str(forecast_id),
                        str(source.get("pack_id", pack_id)),
                        source["title"],
                        source.get("publisher"),
                        source.get("url"),
                        source["source_type"],
                        source["source_classification"],
                        source["reliability_score"],
                        _dump(source.get("metadata", {})),
                        now,
                    ),
                )
            for claim in claims:
                connection.execute(
                    """
                    INSERT INTO forecast_claims (
                        claim_id, forecast_id, text, claim_type, polarity,
                        evidence_strength, reliability_score, cluster_id,
                        independence_group, source_classification, extraction_model,
                        extraction_prompt_version, review_status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim["claim_id"],
                        str(forecast_id),
                        claim["text"],
                        claim["claim_type"],
                        claim["polarity"],
                        claim["evidence_strength"],
                        claim["reliability_score"],
                        claim["cluster_id"],
                        claim["independence_group"],
                        claim["source_classification"],
                        claim["extraction_model"],
                        claim["extraction_prompt_version"],
                        claim["review_status"],
                        now,
                    ),
                )
                for source_id in claim["source_ids"]:
                    connection.execute(
                        """
                        INSERT INTO forecast_claim_source_links (claim_id, source_id)
                        VALUES (?, ?)
                        """,
                        (claim["claim_id"], source_id),
                    )
            for link in links:
                connection.execute(
                    """
                    INSERT INTO forecast_claim_target_links (
                        link_id, forecast_id, claim_id, target_kind, target_id,
                        direction, relevance_weight, review_status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        str(forecast_id),
                        link["claim_id"],
                        link["target_kind"],
                        link["target_id"],
                        link["direction"],
                        link["relevance_weight"],
                        link["review_status"],
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE forecast_forecasts
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (ForecastStatus.EVIDENCE_READY.value, now, str(forecast_id)),
            )
            self.append_audit(
                connection,
                forecast_id,
                "evidence_extracted",
                {"source_count": len(sources), "claim_count": len(claims)},
            )
        return self.get_sources(forecast_id), self.get_claims(forecast_id)

    def upsert_evidence_batch(
        self,
        *,
        forecast_id: UUID,
        pack_id: UUID,
        extraction_batch_id: str,
        report_artifact_hash: str | None,
        sources: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        links: list[dict[str, Any]],
    ) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active_pack_ids = {
                row["pack_id"]
                for row in connection.execute(
                    """
                    SELECT pack_id FROM forecast_research_packs
                    WHERE forecast_id = ? AND is_active = 1
                    """,
                    (str(forecast_id),),
                ).fetchall()
            }
            connection.execute(
                """
                DELETE FROM forecast_claim_target_links
                WHERE forecast_id = ?
                  AND claim_id IN (
                    SELECT claim_id FROM forecast_claims
                    WHERE forecast_id = ?
                      AND origin != 'manual'
                      AND manual_locked = 0
                      AND (
                        pack_id IS NULL
                        OR pack_id NOT IN (
                          SELECT pack_id FROM forecast_research_packs
                          WHERE forecast_id = ? AND is_active = 1
                        )
                      )
                  )
                """,
                (str(forecast_id), str(forecast_id), str(forecast_id)),
            )
            connection.execute(
                """
                DELETE FROM forecast_claims
                WHERE forecast_id = ?
                  AND origin != 'manual'
                  AND manual_locked = 0
                  AND (
                    pack_id IS NULL
                    OR pack_id NOT IN (
                      SELECT pack_id FROM forecast_research_packs
                      WHERE forecast_id = ? AND is_active = 1
                    )
                  )
                """,
                (str(forecast_id), str(forecast_id)),
            )
            connection.execute(
                """
                DELETE FROM forecast_sources
                WHERE forecast_id = ?
                  AND (
                    pack_id IS NULL
                    OR pack_id NOT IN (
                      SELECT pack_id FROM forecast_research_packs
                      WHERE forecast_id = ? AND is_active = 1
                    )
                  )
                  AND source_id NOT IN (
                    SELECT source_id FROM forecast_claim_source_links
                  )
                """,
                (str(forecast_id), str(forecast_id)),
            )
            for source in sources:
                source_pack_id = str(source.get("pack_id", pack_id))
                if source_pack_id not in active_pack_ids:
                    continue
                data_classification = source.get(
                    "data_classification",
                    source.get("source_classification", "public"),
                )
                origin_tool_profile = source.get(
                    "origin_tool_profile",
                    source.get("source_classification", "public"),
                )
                connection.execute(
                    """
                    INSERT INTO forecast_sources (
                        source_id, forecast_id, pack_id, title, publisher, url,
                        source_type, source_classification, data_classification,
                        origin_tool_profile, reliability_score, metadata_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        pack_id = excluded.pack_id,
                        title = excluded.title,
                        publisher = excluded.publisher,
                        url = excluded.url,
                        source_type = excluded.source_type,
                        source_classification = excluded.source_classification,
                        data_classification = excluded.data_classification,
                        origin_tool_profile = excluded.origin_tool_profile,
                        reliability_score = excluded.reliability_score,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        source["source_id"],
                        str(forecast_id),
                        source_pack_id,
                        source["title"],
                        source.get("publisher"),
                        source.get("url"),
                        source["source_type"],
                        source.get("source_classification", data_classification),
                        data_classification,
                        origin_tool_profile,
                        source["reliability_score"],
                        _dump(source.get("metadata", {})),
                        now,
                    ),
                )
            for claim in claims:
                claim_pack_id = str(claim.get("pack_id", pack_id))
                if claim_pack_id not in active_pack_ids:
                    continue
                existing = connection.execute(
                    """
                    SELECT manual_locked, origin FROM forecast_claims
                    WHERE claim_id = ?
                    """,
                    (claim["claim_id"],),
                ).fetchone()
                if existing is not None and (
                    int(existing["manual_locked"]) == 1 or existing["origin"] == "manual"
                ):
                    continue
                data_classification = claim.get(
                    "data_classification",
                    claim.get("source_classification", "public"),
                )
                origin_tool_profile = claim.get(
                    "origin_tool_profile",
                    claim.get("source_classification", "public"),
                )
                connection.execute(
                    """
                    INSERT INTO forecast_claims (
                        claim_id, forecast_id, text, claim_type, polarity,
                        evidence_strength, reliability_score, cluster_id,
                        independence_group, source_classification,
                        data_classification, origin_tool_profile, pack_id,
                        extraction_batch_id, report_artifact_hash, manual_locked,
                        origin, extraction_model, extraction_prompt_version,
                        review_status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(claim_id) DO UPDATE SET
                        text = excluded.text,
                        claim_type = excluded.claim_type,
                        polarity = excluded.polarity,
                        evidence_strength = excluded.evidence_strength,
                        reliability_score = excluded.reliability_score,
                        cluster_id = excluded.cluster_id,
                        independence_group = excluded.independence_group,
                        source_classification = excluded.source_classification,
                        data_classification = excluded.data_classification,
                        origin_tool_profile = excluded.origin_tool_profile,
                        pack_id = excluded.pack_id,
                        extraction_batch_id = excluded.extraction_batch_id,
                        report_artifact_hash = excluded.report_artifact_hash,
                        extraction_model = excluded.extraction_model,
                        extraction_prompt_version = excluded.extraction_prompt_version,
                        review_status = excluded.review_status
                    """,
                    (
                        claim["claim_id"],
                        str(forecast_id),
                        claim["text"],
                        claim["claim_type"],
                        claim["polarity"],
                        claim["evidence_strength"],
                        claim["reliability_score"],
                        claim["cluster_id"],
                        claim["independence_group"],
                        claim.get("source_classification", data_classification),
                        data_classification,
                        origin_tool_profile,
                        claim_pack_id,
                        extraction_batch_id,
                        claim.get("report_artifact_hash", report_artifact_hash),
                        int(claim.get("manual_locked", 0)),
                        claim.get("origin", "extractor"),
                        claim["extraction_model"],
                        claim["extraction_prompt_version"],
                        claim["review_status"],
                        now,
                    ),
                )
                for source_id in claim["source_ids"]:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO forecast_claim_source_links (claim_id, source_id)
                        VALUES (?, ?)
                        """,
                        (claim["claim_id"], source_id),
                    )
            for link in links:
                claim_exists = connection.execute(
                    """
                    SELECT 1 FROM forecast_claims
                    WHERE forecast_id = ? AND claim_id = ?
                    """,
                    (str(forecast_id), link["claim_id"]),
                ).fetchone()
                if claim_exists is None:
                    continue
                connection.execute(
                    """
                    INSERT INTO forecast_claim_target_links (
                        link_id, forecast_id, claim_id, target_kind, target_id,
                        direction, relevance_weight, review_status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(link_id) DO UPDATE SET
                        direction = excluded.direction,
                        relevance_weight = excluded.relevance_weight,
                        review_status = excluded.review_status
                    """,
                    (
                        link.get("link_id", str(uuid4())),
                        str(forecast_id),
                        link["claim_id"],
                        link["target_kind"],
                        link["target_id"],
                        link["direction"],
                        link["relevance_weight"],
                        link["review_status"],
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE forecast_forecasts
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (ForecastStatus.EVIDENCE_READY.value, now, str(forecast_id)),
            )
            self.append_audit(
                connection,
                forecast_id,
                "evidence_upserted",
                {
                    "pack_id": str(pack_id),
                    "extraction_batch_id": extraction_batch_id,
                    "source_count": len(sources),
                    "claim_count": len(claims),
                },
            )
            self.append_audit(
                connection,
                forecast_id,
                "evidence_extracted",
                {"source_count": len(sources), "claim_count": len(claims)},
            )
        return self.get_sources(forecast_id), self.get_claims(forecast_id)

    def get_sources(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_sources
                WHERE forecast_id = ?
                ORDER BY created_at, title
                """,
                (str(forecast_id),),
            ).fetchall()

    def get_active_sources(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT sources.* FROM forecast_sources AS sources
                LEFT JOIN forecast_research_packs AS packs
                  ON packs.pack_id = sources.pack_id
                WHERE sources.forecast_id = ?
                  AND (sources.pack_id IS NULL OR packs.is_active = 1)
                ORDER BY sources.created_at, sources.title
                """,
                (str(forecast_id),),
            ).fetchall()

    def get_claims(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_claims
                WHERE forecast_id = ?
                ORDER BY created_at, text
                """,
                (str(forecast_id),),
            ).fetchall()

    def get_active_claims(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT claims.* FROM forecast_claims AS claims
                LEFT JOIN forecast_research_packs AS packs
                  ON packs.pack_id = claims.pack_id
                WHERE claims.forecast_id = ?
                  AND (claims.pack_id IS NULL OR packs.is_active = 1)
                ORDER BY claims.created_at, claims.text
                """,
                (str(forecast_id),),
            ).fetchall()

    def get_claim_source_ids(self, claim_id: UUID) -> list[UUID]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_id FROM forecast_claim_source_links
                WHERE claim_id = ?
                ORDER BY source_id
                """,
                (str(claim_id),),
            ).fetchall()
        return [UUID(row["source_id"]) for row in rows]

    def replace_scenarios(
        self,
        *,
        forecast_id: UUID,
        scenarios: list[dict[str, Any]],
    ) -> list[sqlite3.Row]:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM forecast_scenarios WHERE forecast_id = ?",
                (str(forecast_id),),
            )
            for scenario in scenarios:
                connection.execute(
                    """
                    INSERT INTO forecast_scenarios (
                        scenario_id, forecast_id, outcome_id, label, description,
                        normalized_weight, validity_status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scenario["scenario_id"],
                        str(forecast_id),
                        scenario["outcome_id"],
                        scenario["label"],
                        scenario["description"],
                        scenario["normalized_weight"],
                        scenario["validity_status"],
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE forecast_forecasts
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (ForecastStatus.SCENARIOS_READY.value, now, str(forecast_id)),
            )
            self.append_audit(
                connection,
                forecast_id,
                "scenarios_generated",
                {"scenario_count": len(scenarios)},
            )
        return self.get_scenarios(forecast_id)

    def upsert_scenarios(
        self,
        *,
        forecast_id: UUID,
        scenarios: list[dict[str, Any]],
    ) -> list[sqlite3.Row]:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for scenario in scenarios:
                connection.execute(
                    """
                    INSERT INTO forecast_scenarios (
                        scenario_id, forecast_id, outcome_id, label, description,
                        normalized_weight, validity_status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scenario_id) DO UPDATE SET
                        outcome_id = excluded.outcome_id,
                        label = excluded.label,
                        description = excluded.description,
                        normalized_weight = excluded.normalized_weight,
                        validity_status = excluded.validity_status
                    """,
                    (
                        scenario["scenario_id"],
                        str(forecast_id),
                        scenario["outcome_id"],
                        scenario["label"],
                        scenario["description"],
                        scenario["normalized_weight"],
                        scenario["validity_status"],
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE forecast_forecasts
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (ForecastStatus.SCENARIOS_READY.value, now, str(forecast_id)),
            )
            self.append_audit(
                connection,
                forecast_id,
                "scenarios_upserted",
                {"scenario_count": len(scenarios)},
            )
        return self.get_scenarios(forecast_id)

    def get_scenarios(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_scenarios
                WHERE forecast_id = ?
                ORDER BY created_at, label
                """,
                (str(forecast_id),),
            ).fetchall()

    def replace_drivers(
        self,
        *,
        forecast_id: UUID,
        drivers: list[dict[str, Any]],
    ) -> list[sqlite3.Row]:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM forecast_drivers WHERE forecast_id = ?",
                (str(forecast_id),),
            )
            for driver in drivers:
                driver_id = driver["driver_id"]
                connection.execute(
                    """
                    INSERT INTO forecast_drivers (
                        driver_id, forecast_id, name, description, sort_order,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        driver_id,
                        str(forecast_id),
                        driver["name"],
                        driver["description"],
                        driver.get("sort_order", 0),
                        now,
                        now,
                    ),
                )
                for state in driver.get("states", []):
                    connection.execute(
                        """
                        INSERT INTO forecast_driver_states (
                            state_id, driver_id, label, description, sort_order,
                            created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            state["state_id"],
                            driver_id,
                            state["label"],
                            state["description"],
                            state.get("sort_order", 0),
                            now,
                            now,
                        ),
                    )
            self.append_audit(
                connection,
                forecast_id,
                "drivers_replaced",
                {"driver_count": len(drivers)},
            )
        return self.get_drivers(forecast_id)

    def get_drivers(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_drivers
                WHERE forecast_id = ?
                ORDER BY sort_order, name
                """,
                (str(forecast_id),),
            ).fetchall()

    def get_driver_states(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT states.*
                FROM forecast_driver_states states
                JOIN forecast_drivers drivers ON drivers.driver_id = states.driver_id
                WHERE drivers.forecast_id = ?
                ORDER BY drivers.sort_order, states.sort_order, states.label
                """,
                (str(forecast_id),),
            ).fetchall()

    def replace_scenario_driver_links(
        self,
        *,
        links: list[dict[str, str]],
    ) -> None:
        scenario_ids = {link["scenario_id"] for link in links}
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for scenario_id in scenario_ids:
                connection.execute(
                    "DELETE FROM forecast_scenario_driver_states WHERE scenario_id = ?",
                    (scenario_id,),
                )
            for link in links:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO forecast_scenario_driver_states (
                        scenario_id, state_id
                    )
                    VALUES (?, ?)
                    """,
                    (link["scenario_id"], link["state_id"]),
                )

    def get_scenario_driver_state_ids(self, scenario_id: UUID) -> list[UUID]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT state_id FROM forecast_scenario_driver_states
                WHERE scenario_id = ?
                ORDER BY state_id
                """,
                (str(scenario_id),),
            ).fetchall()
        return [UUID(row["state_id"]) for row in rows]

    def replace_analog_events(
        self,
        *,
        forecast_id: UUID,
        pack_id: UUID | None,
        analog_events: list[dict[str, Any]],
    ) -> list[sqlite3.Row]:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM forecast_analog_events WHERE forecast_id = ?",
                (str(forecast_id),),
            )
            for event in analog_events:
                connection.execute(
                    """
                    INSERT INTO forecast_analog_events (
                        analog_event_id, forecast_id, pack_id, title,
                        matched_outcome_id, weight, rationale, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.get("analog_event_id", str(uuid4())),
                        str(forecast_id),
                        str(pack_id) if pack_id else None,
                        event["title"],
                        event["matched_outcome_id"],
                        event["weight"],
                        event.get("rationale", ""),
                        now,
                        now,
                    ),
                )
            self.append_audit(
                connection,
                forecast_id,
                "analog_events_replaced",
                {"analog_event_count": len(analog_events)},
            )
        return self.get_analog_events(forecast_id)

    def get_analog_events(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_analog_events
                WHERE forecast_id = ?
                ORDER BY created_at, title
                """,
                (str(forecast_id),),
            ).fetchall()

    def replace_cross_impact(
        self,
        *,
        forecast_id: UUID,
        impacts: list[dict[str, Any]],
    ) -> list[sqlite3.Row]:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM forecast_cross_impact WHERE forecast_id = ?",
                (str(forecast_id),),
            )
            for impact in impacts:
                connection.execute(
                    """
                    INSERT INTO forecast_cross_impact (
                        cross_impact_id, forecast_id, source_outcome_id,
                        target_outcome_id, delta, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        impact.get("cross_impact_id", str(uuid4())),
                        str(forecast_id),
                        impact["source_outcome_id"],
                        impact["target_outcome_id"],
                        impact["delta"],
                        now,
                        now,
                    ),
                )
        return self.get_cross_impact(forecast_id)

    def get_cross_impact(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_cross_impact
                WHERE forecast_id = ?
                ORDER BY source_outcome_id, target_outcome_id
                """,
                (str(forecast_id),),
            ).fetchall()

    def get_approved_target_links(
        self,
        forecast_id: UUID,
        *,
        active_claims_only: bool = False,
    ) -> list[sqlite3.Row]:
        with self.connect() as connection:
            if active_claims_only:
                return connection.execute(
                    """
                    SELECT links.* FROM forecast_claim_target_links AS links
                    JOIN forecast_claims AS claims
                      ON claims.claim_id = links.claim_id
                    LEFT JOIN forecast_research_packs AS packs
                      ON packs.pack_id = claims.pack_id
                    WHERE links.forecast_id = ?
                      AND links.review_status = 'approved'
                      AND (claims.pack_id IS NULL OR packs.is_active = 1)
                    ORDER BY links.target_kind, links.target_id, links.claim_id
                    """,
                    (str(forecast_id),),
                ).fetchall()
            return connection.execute(
                """
                SELECT * FROM forecast_claim_target_links
                WHERE forecast_id = ? AND review_status = 'approved'
                ORDER BY target_kind, target_id, claim_id
                """,
                (str(forecast_id),),
            ).fetchall()

    def create_draft_estimate_set(
        self,
        *,
        forecast_id: UUID,
        engine_version: str,
        input_snapshot_hash: str,
        engine_code_hash: str,
        random_seed: int,
        normalization_group_id: str,
        snapshot: dict[str, Any],
        estimates: list[dict[str, Any]],
    ) -> sqlite3.Row:
        estimate_set_id = uuid4()
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM forecast_estimate_sets
                WHERE forecast_id = ? AND status = 'draft'
                """,
                (str(forecast_id),),
            ).fetchone()
            if existing is not None:
                if (
                    existing["engine_version"] == engine_version
                    and existing["input_snapshot_hash"] == input_snapshot_hash
                    and existing["engine_code_hash"] == engine_code_hash
                ):
                    return existing
                raise ValueError("draft_estimate_set_exists")
            connection.execute(
                """
                INSERT INTO forecast_estimate_sets (
                    estimate_set_id, forecast_id, status, engine_version,
                    input_snapshot_hash, engine_code_hash, random_seed,
                    normalization_group_id, snapshot_json, created_at
                )
                VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(estimate_set_id),
                    str(forecast_id),
                    engine_version,
                    input_snapshot_hash,
                    engine_code_hash,
                    random_seed,
                    normalization_group_id,
                    _dump(snapshot),
                    now,
                ),
            )
            for estimate in estimates:
                connection.execute(
                    """
                    INSERT INTO forecast_probability_estimates (
                        estimate_id, estimate_set_id, target_kind, target_id, prior,
                        evidence_update, cross_impact_adjustment,
                        simulation_adjustment, calibration_adjustment,
                        human_adjustment, final_probability,
                        uncertainty_range_json, components_json, engine_version,
                        input_snapshot_hash, random_seed, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        str(estimate_set_id),
                        estimate["target_kind"],
                        estimate["target_id"],
                        estimate["prior"],
                        estimate["evidence_update"],
                        estimate["cross_impact_adjustment"],
                        estimate["simulation_adjustment"],
                        estimate["calibration_adjustment"],
                        estimate["human_adjustment"],
                        estimate["final_probability"],
                        _dump(estimate["uncertainty_range"]),
                        _dump(estimate["components"]),
                        engine_version,
                        input_snapshot_hash,
                        random_seed,
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE forecast_forecasts
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (ForecastStatus.DRAFT_READY.value, now, str(forecast_id)),
            )
            self.append_audit(
                connection,
                forecast_id,
                "probabilities_computed",
                {
                    "estimate_set_id": str(estimate_set_id),
                    "input_snapshot_hash": input_snapshot_hash,
                    "engine_version": engine_version,
                },
            )
            row = connection.execute(
                "SELECT * FROM forecast_estimate_sets WHERE estimate_set_id = ?",
                (str(estimate_set_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(estimate_set_id))
        return row

    def get_draft_estimate_set(self, forecast_id: UUID) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_estimate_sets
                WHERE forecast_id = ? AND status = 'draft'
                """,
                (str(forecast_id),),
            ).fetchone()

    def get_estimate_set(self, estimate_set_id: UUID) -> sqlite3.Row:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM forecast_estimate_sets WHERE estimate_set_id = ?",
                (str(estimate_set_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(estimate_set_id))
        return row

    def get_current_estimate_set(self, forecast_id: UUID) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_estimate_sets
                WHERE forecast_id = ?
                ORDER BY
                    CASE status WHEN 'draft' THEN 0 ELSE 1 END,
                    COALESCE(frozen_at, created_at) DESC,
                    created_at DESC
                LIMIT 1
                """,
                (str(forecast_id),),
            ).fetchone()

    def get_estimates(self, estimate_set_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_probability_estimates
                WHERE estimate_set_id = ?
                ORDER BY target_kind, target_id
                """,
                (str(estimate_set_id),),
            ).fetchall()

    def get_estimate_dicts(self, estimate_set_id: UUID) -> list[dict[str, Any]]:
        return [
            {
                "target_kind": row["target_kind"],
                "target_id": row["target_id"],
                "prior": row["prior"],
                "evidence_update": row["evidence_update"],
                "cross_impact_adjustment": row["cross_impact_adjustment"],
                "simulation_adjustment": row["simulation_adjustment"],
                "calibration_adjustment": row["calibration_adjustment"],
                "human_adjustment": row["human_adjustment"],
                "final_probability": row["final_probability"],
                "uncertainty_range": _load(row["uncertainty_range_json"], {}),
                "components": _load(row["components_json"], {}),
            }
            for row in self.get_estimates(estimate_set_id)
        ]

    def create_draft_projection_set(
        self,
        *,
        forecast_id: UUID,
        engine_version: str,
        input_snapshot_hash: str,
        engine_code_hash: str,
        random_seed: int,
        snapshot: dict[str, Any],
        scenarios: list[dict[str, Any]],
        metric_points: list[dict[str, Any]],
        composites: list[dict[str, Any]],
        sensitivities: list[dict[str, Any]],
    ) -> sqlite3.Row:
        if abs(sum(float(item["probability"]) for item in scenarios) - 1.0) > 1e-9:
            raise ValueError("projection_probability_sum_invalid")
        projection_set_id = uuid4()
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM forecast_projection_sets
                WHERE forecast_id = ? AND status = 'draft'
                """,
                (str(forecast_id),),
            ).fetchone()
            if existing is not None:
                if (
                    existing["engine_version"] == engine_version
                    and existing["input_snapshot_hash"] == input_snapshot_hash
                    and existing["engine_code_hash"] == engine_code_hash
                ):
                    return existing
                raise ValueError("draft_projection_set_exists")
            dimension_forecasts = {
                row["dimension_id"]: row["forecast_id"]
                for row in connection.execute(
                    """
                    SELECT dimension_id, forecast_id
                    FROM forecast_projection_dimensions
                    WHERE forecast_id = ?
                    """,
                    (str(forecast_id),),
                ).fetchall()
            }
            connection.execute(
                """
                INSERT INTO forecast_projection_sets (
                    projection_set_id, forecast_id, status, engine_version,
                    input_snapshot_hash, engine_code_hash, random_seed,
                    snapshot_json, created_at
                )
                VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(projection_set_id),
                    str(forecast_id),
                    engine_version,
                    input_snapshot_hash,
                    engine_code_hash,
                    random_seed,
                    _dump(snapshot),
                    now,
                ),
            )
            scenario_ids: set[str] = set()
            for scenario in scenarios:
                scenario_id = str(scenario.get("projection_scenario_id") or uuid4())
                scenario_ids.add(scenario_id)
                connection.execute(
                    """
                    INSERT INTO forecast_projection_scenarios (
                        projection_scenario_id, projection_set_id, forecast_id,
                        label, description, coverage_role, residual_flag,
                        probability, probability_logit, driver_vector_json,
                        narrative, validity_status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scenario_id,
                        str(projection_set_id),
                        str(forecast_id),
                        scenario["label"],
                        scenario["description"],
                        scenario["coverage_role"],
                        1 if scenario.get("residual_flag") else 0,
                        scenario["probability"],
                        scenario["probability_logit"],
                        _dump(scenario.get("driver_vector", {})),
                        scenario.get("narrative", ""),
                        scenario.get("validity_status", "valid"),
                        now,
                    ),
                )
            for point in metric_points:
                dimension_id = str(point["dimension_id"])
                scenario_id = str(point["projection_scenario_id"])
                if dimension_forecasts.get(dimension_id) != str(forecast_id):
                    raise ValueError("projection_dimension_forecast_mismatch")
                if scenario_id not in scenario_ids:
                    raise ValueError("projection_scenario_set_mismatch")
                connection.execute(
                    """
                    INSERT INTO forecast_projection_metric_points (
                        metric_point_id, projection_set_id, projection_scenario_id,
                        dimension_id, forecast_id, metric_id, horizon_year,
                        p10, p50, p90, mean, distribution_family,
                        distribution_params_json, baseline_transform, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(point.get("metric_point_id") or uuid4()),
                        str(projection_set_id),
                        scenario_id,
                        dimension_id,
                        str(forecast_id),
                        point["metric_id"],
                        point["horizon_year"],
                        point["p10"],
                        point["p50"],
                        point["p90"],
                        point["mean"],
                        point["distribution_family"],
                        _dump(point.get("distribution_params", {})),
                        point.get("baseline_transform", "level"),
                        now,
                    ),
                )
            for composite in composites:
                dimension_id = str(composite["dimension_id"])
                if dimension_forecasts.get(dimension_id) != str(forecast_id):
                    raise ValueError("projection_dimension_forecast_mismatch")
                connection.execute(
                    """
                    INSERT INTO forecast_projection_composites (
                        composite_id, projection_set_id, dimension_id, forecast_id,
                        metric_id, horizon_year, p10, p50, p90, mean,
                        mixture_components_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(composite.get("composite_id") or uuid4()),
                        str(projection_set_id),
                        dimension_id,
                        str(forecast_id),
                        composite["metric_id"],
                        composite["horizon_year"],
                        composite["p10"],
                        composite["p50"],
                        composite["p90"],
                        composite["mean"],
                        _dump(composite.get("mixture_components", [])),
                        now,
                    ),
                )
            for sensitivity in sensitivities:
                connection.execute(
                    """
                    INSERT INTO forecast_projection_sensitivities (
                        sensitivity_id, projection_set_id, forecast_id,
                        sensitivity_kind, target_ref, baseline_snapshot_hash,
                        perturbed_input_json, delta_p50, delta_p90,
                        delta_probability, rank, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(sensitivity.get("sensitivity_id") or uuid4()),
                        str(projection_set_id),
                        str(forecast_id),
                        sensitivity["sensitivity_kind"],
                        sensitivity["target_ref"],
                        sensitivity["baseline_snapshot_hash"],
                        _dump(sensitivity.get("perturbed_input", {})),
                        sensitivity.get("delta_p50", 0.0),
                        sensitivity.get("delta_p90", 0.0),
                        sensitivity.get("delta_probability", 0.0),
                        sensitivity["rank"],
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE forecast_forecasts
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (ForecastStatus.DRAFT_READY.value, now, str(forecast_id)),
            )
            self.append_audit(
                connection,
                forecast_id,
                "projection_computed",
                {
                    "projection_set_id": str(projection_set_id),
                    "input_snapshot_hash": input_snapshot_hash,
                    "engine_version": engine_version,
                },
            )
            row = connection.execute(
                "SELECT * FROM forecast_projection_sets WHERE projection_set_id = ?",
                (str(projection_set_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(projection_set_id))
        return row

    def get_draft_projection_set(self, forecast_id: UUID) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_projection_sets
                WHERE forecast_id = ? AND status = 'draft'
                """,
                (str(forecast_id),),
            ).fetchone()

    def get_projection_set(self, projection_set_id: UUID) -> sqlite3.Row:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM forecast_projection_sets
                WHERE projection_set_id = ?
                """,
                (str(projection_set_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(projection_set_id))
        return row

    def get_current_projection_set(self, forecast_id: UUID) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_projection_sets
                WHERE forecast_id = ?
                ORDER BY
                    CASE status WHEN 'draft' THEN 0 ELSE 1 END,
                    COALESCE(frozen_at, created_at) DESC,
                    created_at DESC
                LIMIT 1
                """,
                (str(forecast_id),),
            ).fetchone()

    def get_projection_scenarios(self, projection_set_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_projection_scenarios
                WHERE projection_set_id = ?
                ORDER BY residual_flag, probability DESC, label
                """,
                (str(projection_set_id),),
            ).fetchall()

    def get_projection_metric_points(self, projection_set_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_projection_metric_points
                WHERE projection_set_id = ?
                ORDER BY metric_id, horizon_year, projection_scenario_id
                """,
                (str(projection_set_id),),
            ).fetchall()

    def get_projection_composites(self, projection_set_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_projection_composites
                WHERE projection_set_id = ?
                ORDER BY metric_id, horizon_year
                """,
                (str(projection_set_id),),
            ).fetchall()

    def get_projection_sensitivities(self, projection_set_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_projection_sensitivities
                WHERE projection_set_id = ?
                ORDER BY rank, sensitivity_kind, target_ref
                """,
                (str(projection_set_id),),
            ).fetchall()

    def approve_estimate_set(
        self,
        forecast_id: UUID,
        *,
        estimate_set_id: UUID,
        comment: str | None,
    ) -> None:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO forecast_reviews (
                    review_id, forecast_id, estimate_set_id, action, comment, created_at
                )
                VALUES (?, ?, ?, 'approve_phase_a_version', ?, ?)
                """,
                (str(uuid4()), str(forecast_id), str(estimate_set_id), comment, now),
            )
            self.append_audit(
                connection,
                forecast_id,
                "phase_a_version_approved",
                {"estimate_set_id": str(estimate_set_id)},
            )

    def approve_claim_target_links(
        self,
        forecast_id: UUID,
        *,
        comment: str | None,
    ) -> int:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE forecast_claim_target_links
                SET review_status = 'approved'
                WHERE forecast_id = ? AND review_status != 'approved'
                """,
                (str(forecast_id),),
            )
            approved_count = cursor.rowcount
            connection.execute(
                """
                INSERT INTO forecast_reviews (
                    review_id, forecast_id, action, comment, created_at
                )
                VALUES (?, ?, 'approve_claim_target_links', ?, ?)
                """,
                (str(uuid4()), str(forecast_id), comment, now),
            )
            self.append_audit(
                connection,
                forecast_id,
                "claim_target_links_approved",
                {"approved_count": approved_count},
            )
        return approved_count

    def add_review_record(
        self,
        *,
        forecast_id: UUID,
        action: str,
        comment: str | None = None,
        reviewer: str | None = None,
        reviewer_auth_subject: str | None = None,
        policy_decision_id: UUID | None = None,
        review_reason: str | None = None,
        estimate_set_id: UUID | None = None,
        projection_set_id: UUID | None = None,
        version_id: UUID | None = None,
    ) -> None:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO forecast_reviews (
                    review_id, forecast_id, estimate_set_id, projection_set_id,
                    version_id, action, comment, reviewer, reviewer_auth_subject,
                    policy_decision_id, review_reason, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(forecast_id),
                    str(estimate_set_id) if estimate_set_id else None,
                    str(projection_set_id) if projection_set_id else None,
                    str(version_id) if version_id else None,
                    action,
                    comment,
                    reviewer,
                    reviewer_auth_subject,
                    str(policy_decision_id) if policy_decision_id else None,
                    review_reason,
                    now,
                ),
            )
            self.append_audit(
                connection,
                forecast_id,
                "review_recorded",
                {
                    "action": action,
                    "reviewer": reviewer,
                    "policy_decision_id": (
                        str(policy_decision_id) if policy_decision_id else None
                    ),
                    "review_reason": review_reason,
                },
            )

    def estimate_set_has_approval(self, forecast_id: UUID, estimate_set_id: UUID) -> bool:
        with self.connect() as connection:
            estimate_set = connection.execute(
                """
                SELECT engine_version FROM forecast_estimate_sets
                WHERE estimate_set_id = ? AND forecast_id = ?
                """,
                (str(estimate_set_id), str(forecast_id)),
            ).fetchone()
            if estimate_set is None:
                return False
            approval_action = (
                "approve_probability_publication"
                if estimate_set["engine_version"] == "phase_b_v1"
                else "approve_phase_a_version"
            )
            row = connection.execute(
                """
                SELECT 1 FROM forecast_reviews
                WHERE forecast_id = ? AND estimate_set_id = ?
                  AND action = ?
                LIMIT 1
                """,
                (str(forecast_id), str(estimate_set_id), approval_action),
            ).fetchone()
        return row is not None

    def projection_set_has_approval(
        self,
        forecast_id: UUID,
        projection_set_id: UUID,
    ) -> bool:
        with self.connect() as connection:
            projection_set = connection.execute(
                """
                SELECT 1 FROM forecast_projection_sets
                WHERE projection_set_id = ? AND forecast_id = ?
                """,
                (str(projection_set_id), str(forecast_id)),
            ).fetchone()
            if projection_set is None:
                return False
            row = connection.execute(
                """
                SELECT 1 FROM forecast_reviews
                WHERE forecast_id = ? AND projection_set_id = ?
                  AND action = 'approve_projection_publication'
                LIMIT 1
                """,
                (str(forecast_id), str(projection_set_id)),
            ).fetchone()
        return row is not None

    def approve_projection_set(
        self,
        forecast_id: UUID,
        *,
        projection_set_id: UUID,
        comment: str | None,
    ) -> None:
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO forecast_reviews (
                    review_id, forecast_id, projection_set_id, action, comment, created_at
                )
                VALUES (?, ?, ?, 'approve_projection_publication', ?, ?)
                """,
                (str(uuid4()), str(forecast_id), str(projection_set_id), comment, now),
            )
            self.append_audit(
                connection,
                forecast_id,
                "projection_publication_approved",
                {"projection_set_id": str(projection_set_id)},
            )

    def commit_estimate_set(
        self,
        *,
        forecast_id: UUID,
        estimate_set_id: UUID,
        expected_input_snapshot_hash: str,
        snapshot_artifact_path: str,
    ) -> sqlite3.Row:
        version_id = uuid4()
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            estimate_set = connection.execute(
                """
                SELECT * FROM forecast_estimate_sets
                WHERE estimate_set_id = ? AND forecast_id = ?
                """,
                (str(estimate_set_id), str(forecast_id)),
            ).fetchone()
            if estimate_set is None:
                raise KeyError(str(estimate_set_id))
            if estimate_set["status"] != "draft":
                raise ValueError("estimate_set_already_committed")
            if estimate_set["input_snapshot_hash"] != expected_input_snapshot_hash:
                raise ValueError("input_snapshot_hash_mismatch")
            connection.execute(
                """
                INSERT INTO forecast_versions (
                    version_id, forecast_id, version_kind, estimate_set_id,
                    projection_set_id, input_snapshot_hash, snapshot_artifact_path,
                    created_at
                )
                VALUES (?, ?, 'estimate', ?, NULL, ?, ?, ?)
                """,
                (
                    str(version_id),
                    str(forecast_id),
                    str(estimate_set_id),
                    expected_input_snapshot_hash,
                    snapshot_artifact_path,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE forecast_probability_estimates
                SET forecast_version_id = ?
                WHERE estimate_set_id = ?
                """,
                (str(version_id), str(estimate_set_id)),
            )
            cursor = connection.execute(
                """
                UPDATE forecast_estimate_sets
                SET status = 'frozen', snapshot_artifact_path = ?, frozen_at = ?
                WHERE estimate_set_id = ? AND status = 'draft'
                """,
                (snapshot_artifact_path, now, str(estimate_set_id)),
            )
            if cursor.rowcount != 1:
                raise ValueError("estimate_set_already_committed")
            connection.execute(
                """
                UPDATE forecast_forecasts
                SET status = ?, committed_version_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ForecastStatus.COMMITTED.value,
                    str(version_id),
                    now,
                    str(forecast_id),
                ),
            )
            self.append_audit(
                connection,
                forecast_id,
                "version_committed",
                {
                    "version_id": str(version_id),
                    "estimate_set_id": str(estimate_set_id),
                    "input_snapshot_hash": expected_input_snapshot_hash,
                },
            )
            row = connection.execute(
                "SELECT * FROM forecast_versions WHERE version_id = ?",
                (str(version_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(version_id))
        return row

    def commit_projection_set(
        self,
        *,
        forecast_id: UUID,
        projection_set_id: UUID,
        expected_input_snapshot_hash: str,
        snapshot_artifact_path: str,
    ) -> sqlite3.Row:
        version_id = uuid4()
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            projection_set = connection.execute(
                """
                SELECT * FROM forecast_projection_sets
                WHERE projection_set_id = ? AND forecast_id = ?
                """,
                (str(projection_set_id), str(forecast_id)),
            ).fetchone()
            if projection_set is None:
                raise KeyError(str(projection_set_id))
            if projection_set["status"] != "draft":
                raise ValueError("projection_set_already_committed")
            if projection_set["input_snapshot_hash"] != expected_input_snapshot_hash:
                raise ValueError("input_snapshot_hash_mismatch")
            connection.execute(
                """
                INSERT INTO forecast_versions (
                    version_id, forecast_id, version_kind, estimate_set_id,
                    projection_set_id, input_snapshot_hash, snapshot_artifact_path,
                    created_at
                )
                VALUES (?, ?, 'projection', NULL, ?, ?, ?, ?)
                """,
                (
                    str(version_id),
                    str(forecast_id),
                    str(projection_set_id),
                    expected_input_snapshot_hash,
                    snapshot_artifact_path,
                    now,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE forecast_projection_sets
                SET status = 'frozen', snapshot_artifact_path = ?, frozen_at = ?
                WHERE projection_set_id = ? AND status = 'draft'
                """,
                (snapshot_artifact_path, now, str(projection_set_id)),
            )
            if cursor.rowcount != 1:
                raise ValueError("projection_set_already_committed")
            connection.execute(
                """
                UPDATE forecast_forecasts
                SET status = ?, committed_version_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ForecastStatus.COMMITTED.value,
                    str(version_id),
                    now,
                    str(forecast_id),
                ),
            )
            self.append_audit(
                connection,
                forecast_id,
                "projection_version_committed",
                {
                    "version_id": str(version_id),
                    "projection_set_id": str(projection_set_id),
                    "input_snapshot_hash": expected_input_snapshot_hash,
                },
            )
            row = connection.execute(
                "SELECT * FROM forecast_versions WHERE version_id = ?",
                (str(version_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(version_id))
        return row

    def get_versions(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_versions
                WHERE forecast_id = ?
                ORDER BY created_at
                """,
                (str(forecast_id),),
            ).fetchall()

    def resolve_forecast(
        self,
        *,
        forecast_id: UUID,
        version_id: UUID,
        outcome_id: UUID,
        multiclass_brier: float,
        log_score: float,
        scorer_version: str,
        notes: str | None,
    ) -> sqlite3.Row:
        now = utc_now().isoformat()
        resolution_id = uuid4()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM forecast_resolutions WHERE forecast_id = ?",
                (str(forecast_id),),
            ).fetchone()
            if existing is not None:
                raise ValueError("forecast_already_resolved")
            connection.execute(
                """
                INSERT INTO forecast_resolutions (
                    resolution_id, forecast_id, version_id, outcome_id,
                    multiclass_brier, log_score, scorer_version, notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(resolution_id),
                    str(forecast_id),
                    str(version_id),
                    str(outcome_id),
                    multiclass_brier,
                    log_score,
                    scorer_version,
                    notes,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE forecast_forecasts
                SET status = ?, resolved_outcome_id = ?, resolved_at = ?,
                    resolution_notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ForecastStatus.RESOLVED.value,
                    str(outcome_id),
                    now,
                    notes,
                    now,
                    str(forecast_id),
                ),
            )
            self.append_audit(
                connection,
                forecast_id,
                "forecast_resolved",
                {
                    "version_id": str(version_id),
                    "outcome_id": str(outcome_id),
                    "multiclass_brier": multiclass_brier,
                    "log_score": log_score,
                },
            )
            row = connection.execute(
                "SELECT * FROM forecast_resolutions WHERE resolution_id = ?",
                (str(resolution_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(resolution_id))
        return row

    def get_audit(self, forecast_id: UUID) -> dict[str, list[sqlite3.Row]]:
        with self.connect() as connection:
            reviews = connection.execute(
                """
                SELECT * FROM forecast_reviews
                WHERE forecast_id = ?
                ORDER BY created_at
                """,
                (str(forecast_id),),
            ).fetchall()
            versions = connection.execute(
                """
                SELECT * FROM forecast_versions
                WHERE forecast_id = ?
                ORDER BY created_at
                """,
                (str(forecast_id),),
            ).fetchall()
            policy_decisions = connection.execute(
                """
                SELECT * FROM forecast_policy_decisions
                WHERE forecast_id = ?
                ORDER BY created_at
                """,
                (str(forecast_id),),
            ).fetchall()
            events = connection.execute(
                """
                SELECT * FROM forecast_audit_events
                WHERE forecast_id = ?
                ORDER BY created_at
                """,
                (str(forecast_id),),
            ).fetchall()
        return {
            "reviews": reviews,
            "versions": versions,
            "policy_decisions": policy_decisions,
            "events": events,
        }

    @staticmethod
    def forecast_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "forecast_id": UUID(row["id"]),
            "forecast_mode": ForecastMode(row["forecast_mode"]),
            "question": row["question"],
            "original_execution_prompt": row["original_execution_prompt"],
            "status": ForecastStatus(row["status"]),
            "resolution_date": _parse_date(row["resolution_date"]),
            "target_population": row["target_population"],
            "unit_of_analysis": row["unit_of_analysis"],
            "resolution_criteria": row["resolution_criteria"],
            "resolution_sources": _load(row["resolution_sources_json"], []),
            "decision_context": row["decision_context"],
            "confidentiality_class": row["confidentiality_class"],
            "current_framing_version": row["current_framing_version"],
            "approved_framing_version": row["approved_framing_version"],
            "committed_version_id": (
                UUID(row["committed_version_id"]) if row["committed_version_id"] else None
            ),
            "resolved_at": _parse_dt(row["resolved_at"]),
            "created_at": _parse_dt(row["created_at"]),
            "updated_at": _parse_dt(row["updated_at"]),
        }

    @staticmethod
    def tool_profile(row: sqlite3.Row) -> ToolProfile:
        return ToolProfile(row["tool_profile"])
