# ruff: noqa: E501
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from api.forecast.schemas import ForecastStatus, ToolProfile
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


def _is_phase_a_research_pack_unique_error(error: sqlite3.IntegrityError) -> bool:
    message = str(error)
    return (
        "UNIQUE constraint failed: forecast_research_packs.forecast_id, "
        "forecast_research_packs.pack_role, forecast_research_packs.tool_profile"
    ) in message


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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS forecast_research_packs_phase_a_unique
                ON forecast_research_packs(forecast_id, pack_role, tool_profile);

                CREATE TABLE IF NOT EXISTS forecast_sources (
                    source_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    pack_id TEXT REFERENCES forecast_research_packs(pack_id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    publisher TEXT,
                    url TEXT,
                    source_type TEXT NOT NULL,
                    source_classification TEXT NOT NULL,
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
                    estimate_set_id TEXT NOT NULL UNIQUE REFERENCES forecast_estimate_sets(estimate_set_id),
                    input_snapshot_hash TEXT NOT NULL,
                    snapshot_artifact_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_reviews (
                    review_id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL REFERENCES forecast_forecasts(id) ON DELETE CASCADE,
                    framing_version INTEGER,
                    estimate_set_id TEXT,
                    version_id TEXT,
                    action TEXT NOT NULL,
                    comment TEXT,
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
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(forecast_forecasts)")
            }
            if "original_execution_prompt" not in columns:
                connection.execute(
                    "ALTER TABLE forecast_forecasts ADD COLUMN original_execution_prompt TEXT"
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
        outcome_labels: list[str],
        idempotency_key: str | None,
    ) -> sqlite3.Row:
        if idempotency_key:
            existing = self.get_forecast_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        now = utc_now().isoformat()
        forecast_id = uuid4()
        normalization_group_id = f"ng-{forecast_id}"
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
                    confidentiality_class, status, current_framing_version,
                    idempotency_key, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
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
            self.append_audit(
                connection,
                forecast_id,
                "forecast_created",
                {"framing_version": 1, "outcome_count": len(labels)},
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
    ) -> UUID:
        decision_id = uuid4()
        now = utc_now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO forecast_policy_decisions (
                    policy_decision_id, forecast_id, profile, status, reason,
                    prompt_hash, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(decision_id),
                    str(forecast_id),
                    profile,
                    status,
                    reason,
                    prompt_hash,
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
    ) -> sqlite3.Row:
        pack_id = uuid4()
        now = utc_now().isoformat()
        with self.connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO forecast_research_packs (
                        pack_id, forecast_id, research_run_id, pack_role, tool_profile,
                        status, model_deployment, prompt_version, max_tool_calls,
                        policy_decision_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        str(pack_id),
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

    def get_sources(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_sources
                WHERE forecast_id = ? AND source_classification = 'public'
                ORDER BY created_at, title
                """,
                (str(forecast_id),),
            ).fetchall()

    def get_claims(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM forecast_claims
                WHERE forecast_id = ? AND source_classification = 'public'
                ORDER BY created_at, text
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

    def get_approved_target_links(self, forecast_id: UUID) -> list[sqlite3.Row]:
        with self.connect() as connection:
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
                return existing
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

    def estimate_set_has_approval(self, forecast_id: UUID, estimate_set_id: UUID) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM forecast_reviews
                WHERE forecast_id = ? AND estimate_set_id = ?
                  AND action = 'approve_phase_a_version'
                LIMIT 1
                """,
                (str(forecast_id), str(estimate_set_id)),
            ).fetchone()
        return row is not None

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
                    version_id, forecast_id, estimate_set_id, input_snapshot_hash,
                    snapshot_artifact_path, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
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
