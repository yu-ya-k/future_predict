from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from api.config import Settings
from api.research.schemas import (
    Citation,
    CostEvent,
    HumanReviewAction,
    HumanReviewDecision,
    ObjectiveContract,
    RecommendedAction,
    RerunPlan,
    ResearchAttempt,
    ResearchCheckpoint,
    ResearchCheckpointChildFork,
    ResearchItem,
    ResearchRunLineage,
    ResearchRunOptions,
    ResearchRunRecord,
    ReviewRecord,
    RunStatus,
    ToolCallSummary,
    Verdict,
    VerificationQuery,
    utc_now,
)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _legacy_review_flags(review: ReviewRecord) -> dict[str, int]:
    actions = {assessment.recommended_action for assessment in review.item_assessments}
    can_be_fixed_by_llm = (
        review.verdict == Verdict.NEEDS_LLM_PATCH
        or RecommendedAction.LLM_PATCH in actions
    )
    requires_new_external_research = (
        review.verdict
        in {
            Verdict.NEEDS_VERIFICATION,
            Verdict.NEEDS_TARGETED_RERUN,
            Verdict.NEEDS_FULL_RERUN,
        }
        or bool(
            actions
            & {
                RecommendedAction.VERIFY,
                RecommendedAction.TARGETED_RERUN,
                RecommendedAction.FULL_RERUN,
            }
        )
    )
    return {
        "can_be_fixed_by_llm": int(can_be_fixed_by_llm),
        "requires_new_external_research": int(requires_new_external_research),
    }


def _validate_existing_lineage_request(
    lineage: ResearchRunLineage,
    *,
    additional_prompt: str,
    confirmed_preview_hash: str,
) -> None:
    if (
        lineage.additional_prompt != additional_prompt
        or lineage.confirmed_preview_hash != confirmed_preview_hash
    ):
        raise ValueError(
            "Fork idempotency key already exists with a different prompt or preview hash."
        )


class ResearchRepository:
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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_runs (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    user_prompt TEXT NOT NULL,
                    optimized_prompt TEXT,
                    status TEXT NOT NULL,
                    report TEXT,
                    final_report TEXT,
                    done_reason TEXT,
                    needs_human_review INTEGER NOT NULL DEFAULT 0,
                    pending_deep_research_response_id TEXT,
                    deep_research_status TEXT,
                    context_classification TEXT NOT NULL DEFAULT 'public',
                    deep_research_runs INTEGER NOT NULL DEFAULT 0,
                    targeted_rerun_runs INTEGER NOT NULL DEFAULT 0,
                    full_rerun_runs INTEGER NOT NULL DEFAULT 0,
                    llm_patch_runs INTEGER NOT NULL DEFAULT 0,
                    verification_runs INTEGER NOT NULL DEFAULT 0,
                    total_reviews INTEGER NOT NULL DEFAULT 0,
                    no_progress_count INTEGER NOT NULL DEFAULT 0,
                    max_targeted_rerun_runs INTEGER NOT NULL DEFAULT 2,
                    max_full_rerun_runs INTEGER NOT NULL DEFAULT 1,
                    max_llm_patch_runs INTEGER NOT NULL DEFAULT 3,
                    max_verification_runs INTEGER NOT NULL DEFAULT 3,
                    max_total_iterations INTEGER NOT NULL DEFAULT 5,
                    max_total_tool_calls INTEGER NOT NULL DEFAULT 120,
                    total_tool_calls INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd REAL NOT NULL DEFAULT 0,
                    terminal_status TEXT,
                    review_claim_token TEXT,
                    review_claim_operation TEXT,
                    review_claim_expires_at TEXT,
                    warnings TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_attempts (
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

                CREATE TABLE IF NOT EXISTS research_reviews (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    review_no INTEGER NOT NULL,
                    response_id TEXT,
                    model TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    goal_achieved INTEGER NOT NULL,
                    reviewer_confidence INTEGER NOT NULL,
                    review_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS objective_contracts (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    contract_json TEXT NOT NULL,
                    contract_frozen INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_items (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL,
                    criterion_id TEXT NOT NULL,
                    item_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rerun_plans (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    rerun_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    target_item_ids TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS verification_queries (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL,
                    raw_query TEXT,
                    safe_query TEXT,
                    policy_status TEXT NOT NULL,
                    blocked_reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_citations (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    attempt_id TEXT REFERENCES research_attempts(id) ON DELETE SET NULL,
                    title TEXT,
                    url TEXT,
                    source_type TEXT,
                    start_index INTEGER,
                    end_index INTEGER,
                    retrieved_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_tool_calls (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    response_id TEXT,
                    step TEXT NOT NULL,
                    tool_type TEXT NOT NULL,
                    query TEXT,
                    url TEXT,
                    status TEXT,
                    raw_tool_call TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_cost_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    step TEXT NOT NULL,
                    model TEXT NOT NULL,
                    response_id TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    tool_calls INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS human_review_decisions (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    decision_no INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    comment TEXT,
                    reviewer_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_history (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    checkpoint_no INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    node_anchor TEXT NOT NULL,
                    forkable INTEGER NOT NULL DEFAULT 0,
                    dedupe_key TEXT NOT NULL,
                    source_attempt_no INTEGER,
                    source_review_no INTEGER,
                    source_response_id TEXT,
                    report_hash TEXT,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_run_lineage (
                    run_id TEXT PRIMARY KEY REFERENCES research_runs(id) ON DELETE CASCADE,
                    root_run_id TEXT NOT NULL,
                    parent_run_id TEXT NOT NULL,
                    forked_from_checkpoint_id TEXT NOT NULL,
                    fork_mode TEXT NOT NULL,
                    additional_prompt TEXT NOT NULL,
                    confirmed_preview_hash TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    source_snapshot_json TEXT NOT NULL,
                    source_report_artifact_path TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS research_checkpoints_run_no_unique
                ON research_checkpoints(run_id, checkpoint_no);

                CREATE UNIQUE INDEX IF NOT EXISTS research_checkpoints_run_dedupe_unique
                ON research_checkpoints(run_id, dedupe_key);

                CREATE UNIQUE INDEX IF NOT EXISTS research_run_lineage_parent_request_unique
                ON research_run_lineage(
                    parent_run_id,
                    forked_from_checkpoint_id,
                    idempotency_key
                );

                CREATE UNIQUE INDEX IF NOT EXISTS research_cost_events_response_dedupe
                ON research_cost_events(run_id, step, response_id)
                WHERE response_id IS NOT NULL AND response_id != '';

                CREATE UNIQUE INDEX IF NOT EXISTS human_review_decisions_run_decision_no_unique
                ON human_review_decisions(run_id, decision_no);

                CREATE INDEX IF NOT EXISTS human_review_decisions_run_order
                ON human_review_decisions(run_id, decision_no, created_at);
                """
            )
            self._ensure_run_columns(connection)

    def _ensure_run_columns(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("PRAGMA table_info(research_runs)").fetchall()
        existing = {row["name"] for row in rows}
        columns = {
            "context_classification": "TEXT NOT NULL DEFAULT 'public'",
            "targeted_rerun_runs": "INTEGER NOT NULL DEFAULT 0",
            "full_rerun_runs": "INTEGER NOT NULL DEFAULT 0",
            "llm_patch_runs": "INTEGER NOT NULL DEFAULT 0",
            "verification_runs": "INTEGER NOT NULL DEFAULT 0",
            "max_targeted_rerun_runs": "INTEGER NOT NULL DEFAULT 2",
            "max_full_rerun_runs": "INTEGER NOT NULL DEFAULT 1",
            "max_llm_patch_runs": "INTEGER NOT NULL DEFAULT 3",
            "max_verification_runs": "INTEGER NOT NULL DEFAULT 3",
            "terminal_status": "TEXT",
            "review_claim_token": "TEXT",
            "review_claim_operation": "TEXT",
            "review_claim_expires_at": "TEXT",
        }
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE research_runs ADD COLUMN {name} {definition}")

    def create_run(
        self,
        *,
        user_prompt: str,
        options: ResearchRunOptions,
        settings: Settings,
    ) -> ResearchRunRecord:
        now = utc_now()
        run_id = uuid4()
        thread_id = str(uuid4())
        max_tool_calls = options.max_total_tool_calls or settings.default_max_total_tool_calls

        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO research_runs (
                    id, thread_id, user_prompt, status, needs_human_review,
                    max_targeted_rerun_runs, max_full_rerun_runs,
                    max_llm_patch_runs, max_verification_runs, max_total_iterations,
                    max_total_tool_calls,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    thread_id,
                    user_prompt,
                    RunStatus.QUEUED.value,
                    options.max_targeted_rerun_runs
                    if options.max_targeted_rerun_runs is not None
                    else settings.default_max_targeted_rerun_runs,
                    options.max_full_rerun_runs
                    if options.max_full_rerun_runs is not None
                    else settings.default_max_full_rerun_runs,
                    options.max_llm_patch_runs
                    if options.max_llm_patch_runs is not None
                    else settings.default_max_llm_patch_runs,
                    options.max_verification_runs
                    if options.max_verification_runs is not None
                    else settings.default_max_verification_runs,
                    options.max_total_iterations or settings.default_max_total_iterations,
                    max_tool_calls,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            self.append_history(connection, run_id, {"step": "research_run_created"})

        return self.get_run(run_id)

    def create_fork_run(
        self,
        *,
        parent: ResearchRunRecord,
        root_run_id: UUID,
        checkpoint: ResearchCheckpoint,
        additional_prompt: str,
        confirmed_preview_hash: str,
        idempotency_key: str,
        source_snapshot_json: dict[str, Any],
        source_report_artifact_path: str | None,
        seed_report: str,
    ) -> tuple[ResearchRunRecord, ResearchRunLineage, bool]:
        existing = self.get_lineage_by_fork_request(
            parent_run_id=parent.id,
            checkpoint_id=checkpoint.checkpoint_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            _validate_existing_lineage_request(
                existing,
                additional_prompt=additional_prompt,
                confirmed_preview_hash=confirmed_preview_hash,
            )
            return self.get_run(existing.run_id), existing, False

        now = utc_now()
        child_run_id = uuid4()
        thread_id = str(uuid4())
        lineage = ResearchRunLineage(
            run_id=child_run_id,
            root_run_id=root_run_id,
            parent_run_id=parent.id,
            forked_from_checkpoint_id=checkpoint.checkpoint_id,
            fork_mode="deep_research_delta",
            additional_prompt=additional_prompt,
            confirmed_preview_hash=confirmed_preview_hash,
            idempotency_key=idempotency_key,
            source_snapshot_json=source_snapshot_json,
            source_report_artifact_path=source_report_artifact_path,
            created_at=now,
        )

        try:
            with self.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO research_runs (
                        id, thread_id, user_prompt, optimized_prompt, status, report,
                        needs_human_review, context_classification,
                        max_targeted_rerun_runs, max_full_rerun_runs,
                        max_llm_patch_runs, max_verification_runs,
                        max_total_iterations, max_total_tool_calls,
                        total_tool_calls, estimated_cost_usd,
                        warnings, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
                    """,
                    (
                        str(child_run_id),
                        thread_id,
                        parent.user_prompt,
                        parent.optimized_prompt,
                        RunStatus.QUEUED.value,
                        seed_report,
                        getattr(parent, "context_classification", "public"),
                        parent.max_targeted_rerun_runs,
                        parent.max_full_rerun_runs,
                        parent.max_llm_patch_runs,
                        parent.max_verification_runs,
                        parent.max_total_iterations,
                        parent.max_total_tool_calls,
                        _json_dump([]),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO research_run_lineage (
                        run_id, root_run_id, parent_run_id, forked_from_checkpoint_id,
                        fork_mode, additional_prompt, confirmed_preview_hash,
                        idempotency_key, source_snapshot_json,
                        source_report_artifact_path, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(lineage.run_id),
                        str(lineage.root_run_id),
                        str(lineage.parent_run_id),
                        str(lineage.forked_from_checkpoint_id),
                        lineage.fork_mode,
                        lineage.additional_prompt,
                        lineage.confirmed_preview_hash,
                        lineage.idempotency_key,
                        _json_dump(lineage.source_snapshot_json),
                        lineage.source_report_artifact_path,
                        lineage.created_at.isoformat(),
                    ),
                )
                self.append_history(
                    connection,
                    child_run_id,
                    {
                        "step": "research_run_forked",
                        "parent_run_id": str(parent.id),
                        "root_run_id": str(root_run_id),
                        "forked_from_checkpoint_id": str(checkpoint.checkpoint_id),
                        "checkpoint_kind": checkpoint.kind,
                    },
                )
        except sqlite3.IntegrityError:
            existing = self.get_lineage_by_fork_request(
                parent_run_id=parent.id,
                checkpoint_id=checkpoint.checkpoint_id,
                idempotency_key=idempotency_key,
            )
            if existing is None:
                raise
            _validate_existing_lineage_request(
                existing,
                additional_prompt=additional_prompt,
                confirmed_preview_hash=confirmed_preview_hash,
            )
            return self.get_run(existing.run_id), existing, False

        return self.get_run(child_run_id), lineage, True

    def get_run(self, run_id: UUID) -> ResearchRunRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()

        if row is None:
            raise KeyError(str(run_id))

        return self._row_to_run(row)

    def delete_run(self, run_id: UUID) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM research_runs WHERE id = ?",
                (str(run_id),),
            )
            return cursor.rowcount == 1

    def list_waiting_runs(self, *, timeout_seconds: int) -> list[ResearchRunRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM research_runs
                WHERE status = ?
                  AND pending_deep_research_response_id IS NOT NULL
                  AND updated_at <= ?
                ORDER BY updated_at ASC
                LIMIT 25
                """,
                (RunStatus.WAITING_DEEP_RESEARCH.value, utc_now().isoformat()),
            ).fetchall()

        runs = [self._row_to_run(row) for row in rows]
        return runs

    def list_stale_collecting_runs(
        self,
        *,
        stale_seconds: int,
        timeout_seconds: int,
    ) -> list[ResearchRunRecord]:
        cutoff = utc_now() - timedelta(seconds=stale_seconds)
        timeout_cutoff = utc_now() - timedelta(seconds=timeout_seconds)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*
                FROM research_runs r
                JOIN research_attempts a
                  ON a.run_id = r.id
                 AND a.response_id = r.pending_deep_research_response_id
                WHERE r.status = ?
                  AND r.pending_deep_research_response_id IS NOT NULL
                  AND r.updated_at <= ?
                  AND (
                      r.review_claim_token IS NULL
                      OR r.review_claim_expires_at IS NULL
                      OR r.review_claim_expires_at <= ?
                  )
                  AND a.created_at > ?
                  AND a.created_at = (
                      SELECT MIN(created_at)
                      FROM research_attempts
                      WHERE run_id = r.id
                        AND response_id = r.pending_deep_research_response_id
                  )
                ORDER BY r.updated_at ASC
                LIMIT 25
                """,
                (
                    RunStatus.COLLECTING.value,
                    cutoff.isoformat(),
                    utc_now().isoformat(),
                    timeout_cutoff.isoformat(),
                ),
            ).fetchall()

        return [self._row_to_run(row) for row in rows]

    def claim_deep_research_run(
        self,
        run_id: UUID,
        *,
        lease_seconds: int = 60,
    ) -> ResearchRunRecord | None:
        now = utc_now()
        expires_at = now + timedelta(seconds=max(lease_seconds, 1))
        token = str(uuid4())
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE research_runs
                SET status = ?,
                    review_claim_token = ?,
                    review_claim_operation = ?,
                    review_claim_expires_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status = ?
                  AND pending_deep_research_response_id IS NOT NULL
                """,
                (
                    RunStatus.COLLECTING.value,
                    token,
                    "deep_research_collect",
                    expires_at.isoformat(),
                    now.isoformat(),
                    str(run_id),
                    RunStatus.WAITING_DEEP_RESEARCH.value,
                ),
            )
            if cursor.rowcount != 1:
                return None

        return self.get_run(run_id)

    def claim_stale_collecting_run(
        self,
        run_id: UUID,
        *,
        stale_seconds: int,
        timeout_seconds: int,
        lease_seconds: int,
    ) -> ResearchRunRecord | None:
        now = utc_now()
        cutoff = now - timedelta(seconds=stale_seconds)
        timeout_cutoff = now - timedelta(seconds=timeout_seconds)
        expires_at = now + timedelta(seconds=max(lease_seconds, 1))
        token = str(uuid4())
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE research_runs
                SET review_claim_token = ?,
                    review_claim_operation = ?,
                    review_claim_expires_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status = ?
                  AND pending_deep_research_response_id IS NOT NULL
                  AND updated_at <= ?
                  AND (
                      review_claim_token IS NULL
                      OR review_claim_expires_at IS NULL
                      OR review_claim_expires_at <= ?
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM research_attempts a
                      WHERE a.run_id = research_runs.id
                        AND a.response_id = research_runs.pending_deep_research_response_id
                        AND a.created_at > ?
                        AND a.created_at = (
                            SELECT MIN(created_at)
                            FROM research_attempts
                            WHERE run_id = research_runs.id
                              AND response_id = research_runs.pending_deep_research_response_id
                        )
                  )
                """,
                (
                    token,
                    "deep_research_collect",
                    expires_at.isoformat(),
                    now.isoformat(),
                    str(run_id),
                    RunStatus.COLLECTING.value,
                    cutoff.isoformat(),
                    now.isoformat(),
                    timeout_cutoff.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM research_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()

        if row is None:
            raise KeyError(str(run_id))
        return self._row_to_run(row)

    def claim_review_operation(
        self,
        run_id: UUID,
        *,
        operation: str,
        lease_seconds: int,
    ) -> tuple[ResearchRunRecord, str] | None:
        now = utc_now()
        expires_at = now + timedelta(seconds=lease_seconds)
        token = str(uuid4())
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE research_runs
                SET review_claim_token = ?,
                    review_claim_operation = ?,
                    review_claim_expires_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status = ?
                  AND needs_human_review = 0
                  AND (
                      review_claim_token IS NULL
                      OR review_claim_expires_at IS NULL
                      OR review_claim_expires_at <= ?
                  )
                """,
                (
                    token,
                    operation,
                    expires_at.isoformat(),
                    now.isoformat(),
                    str(run_id),
                    RunStatus.REVIEWING.value,
                    now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM research_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()

        if row is None:
            raise KeyError(str(run_id))
        return self._row_to_run(row), token

    def release_review_operation(self, run_id: UUID, *, claim_token: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE research_runs
                SET review_claim_token = NULL,
                    review_claim_operation = NULL,
                    review_claim_expires_at = NULL,
                    updated_at = ?
                WHERE id = ?
                  AND review_claim_token = ?
                """,
                (utc_now().isoformat(), str(run_id), claim_token),
            )
        return cursor.rowcount == 1

    def list_timed_out_runs(self, *, timeout_seconds: int) -> list[ResearchRunRecord]:
        cutoff = utc_now() - timedelta(seconds=timeout_seconds)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*
                FROM research_runs r
                JOIN research_attempts a
                  ON a.run_id = r.id
                 AND a.response_id = r.pending_deep_research_response_id
                WHERE r.status IN (?, ?)
                  AND r.pending_deep_research_response_id IS NOT NULL
                  AND a.created_at = (
                      SELECT MIN(created_at)
                      FROM research_attempts
                      WHERE run_id = r.id
                        AND response_id = r.pending_deep_research_response_id
                  )
                  AND a.created_at <= ?
                ORDER BY a.created_at ASC
                LIMIT 25
                """,
                (
                    RunStatus.WAITING_DEEP_RESEARCH.value,
                    RunStatus.COLLECTING.value,
                    cutoff.isoformat(),
                ),
            ).fetchall()

        return [self._row_to_run(row) for row in rows]

    def list_stale_reviewing_runs(self, *, timeout_seconds: int) -> list[ResearchRunRecord]:
        cutoff = utc_now() - timedelta(seconds=timeout_seconds)
        now = utc_now()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*
                FROM research_runs r
                JOIN research_history h
                  ON h.run_id = r.id
                WHERE r.status = ?
                  AND r.needs_human_review = 0
                  AND (
                      r.review_claim_token IS NULL
                      OR r.review_claim_expires_at IS NULL
                      OR r.review_claim_expires_at <= ?
                  )
                  AND h.created_at <= ?
                  AND json_extract(h.event_json, '$.step') IN (
                      'review_attempt_started',
                      'llm_finalize_attempt_started'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM research_history done
                      WHERE done.run_id = r.id
                        AND done.created_at > h.created_at
                        AND json_extract(done.event_json, '$.step') IN (
                            'review_attempt_completed',
                            'review_attempt_failed',
                            'llm_finalize_attempt_completed',
                            'llm_finalize_attempt_failed'
                        )
                  )
                GROUP BY r.id
                ORDER BY MIN(h.created_at) ASC
                LIMIT 25
                """,
                (RunStatus.REVIEWING.value, now.isoformat(), cutoff.isoformat()),
            ).fetchall()

        return [self._row_to_run(row) for row in rows]

    def count_billable_web_search_tool_calls(
        self,
        run_id: UUID,
        *,
        step: str,
        response_id: str | None,
    ) -> int | None:
        response_filter = (
            "response_id IS NULL" if response_id is None else "response_id = ?"
        )
        values: list[Any] = [str(run_id), step]
        if response_id is not None:
            values.append(response_id)

        with self.connect() as connection:
            total_row = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM research_tool_calls
                WHERE run_id = ?
                  AND step = ?
                  AND {response_filter}
                """,
                values,
            ).fetchone()
            total = int(total_row["count"] if total_row is not None else 0)
            if total == 0:
                return None

            billable_row = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM research_tool_calls
                WHERE run_id = ?
                  AND step = ?
                  AND {response_filter}
                  AND tool_type LIKE '%web_search%'
                """,
                values,
            ).fetchone()

        return int(billable_row["count"] if billable_row is not None else 0)

    def list_human_review_runs(self) -> list[ResearchRunRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM research_runs
                WHERE status = ?
                  AND needs_human_review = 1
                ORDER BY updated_at ASC
                """,
                (RunStatus.NEEDS_HUMAN_REVIEW.value,),
            ).fetchall()

        return [self._row_to_run(row) for row in rows]

    def update_run(self, run_id: UUID, **fields: Any) -> ResearchRunRecord:
        if not fields:
            return self.get_run(run_id)

        fields["updated_at"] = utc_now().isoformat()
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = [self._db_value(value) for value in fields.values()]
        values.append(str(run_id))

        with self.connect() as connection:
            connection.execute(
                f"UPDATE research_runs SET {columns} WHERE id = ?",
                values,
            )

        return self.get_run(run_id)

    def update_run_if_status(
        self,
        run_id: UUID,
        allowed_statuses: set[RunStatus],
        **fields: Any,
    ) -> ResearchRunRecord | None:
        if not allowed_statuses:
            return None
        if not fields:
            run = self.get_run(run_id)
            return run if run.status in allowed_statuses else None

        fields["updated_at"] = utc_now().isoformat()
        columns = ", ".join(f"{key} = ?" for key in fields)
        status_placeholders = ", ".join("?" for _ in allowed_statuses)
        values = [self._db_value(value) for value in fields.values()]
        values.append(str(run_id))
        values.extend(status.value for status in allowed_statuses)

        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE research_runs
                SET {columns}
                WHERE id = ?
                  AND status IN ({status_placeholders})
                """,
                values,
            )
            if cursor.rowcount != 1:
                return None

        return self.get_run(run_id)

    def add_attempt(self, run_id: UUID, attempt: ResearchAttempt) -> None:
        now = utc_now().isoformat()
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT id, prompt, response_id
                FROM research_attempts
                WHERE run_id = ?
                  AND attempt_no = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (str(run_id), attempt.run_no),
            ).fetchone()
            if existing is None:
                attempt_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO research_attempts (
                        id, run_id, attempt_no, response_id, model, prompt, output_text, status,
                        error, tool_calls, citations, raw_response_artifact_path, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        str(run_id),
                        attempt.run_no,
                        attempt.response_id,
                        attempt.model,
                        attempt.prompt,
                        attempt.report,
                        attempt.status,
                        attempt.error,
                        attempt.model_dump_json(include={"tool_calls_summary"}),
                        attempt.model_dump_json(include={"citations"}),
                        attempt.raw_response_artifact_path,
                        now,
                    ),
                )
                history_step = "attempt_recorded"
            else:
                attempt_id = existing["id"]
                submitted_prompt = existing["prompt"] or attempt.prompt
                response_id = attempt.response_id or existing["response_id"]
                connection.execute(
                    """
                    UPDATE research_attempts
                    SET response_id = ?,
                        model = ?,
                        prompt = ?,
                        output_text = ?,
                        status = ?,
                        error = ?,
                        tool_calls = ?,
                        citations = ?,
                        raw_response_artifact_path = ?
                    WHERE id = ?
                    """,
                    (
                        response_id,
                        attempt.model,
                        submitted_prompt,
                        attempt.report,
                        attempt.status,
                        attempt.error,
                        attempt.model_dump_json(include={"tool_calls_summary"}),
                        attempt.model_dump_json(include={"citations"}),
                        attempt.raw_response_artifact_path,
                        attempt_id,
                    ),
                )
                history_step = "attempt_updated"
            for citation in attempt.citations:
                self._insert_citation(connection, run_id, citation, attempt_id=attempt_id)
            for tool_call in attempt.tool_calls_summary:
                self._insert_tool_call(
                    connection,
                    run_id,
                    response_id=attempt.response_id,
                    step="deep_research",
                    tool_call=tool_call,
                )
            self.append_history(
                connection,
                run_id,
                {
                    "step": history_step,
                    "run_no": attempt.run_no,
                    "status": attempt.status,
                    "response_id": attempt.response_id,
                },
            )

    def add_review(
        self,
        *,
        run_id: UUID,
        review: ReviewRecord,
        model: str,
    ) -> None:
        now = utc_now().isoformat()
        with self.connect() as connection:
            table_columns = self._table_columns(connection, "research_reviews")
            columns = [
                "id",
                "run_id",
                "review_no",
                "response_id",
                "model",
                "verdict",
                "score",
                "goal_achieved",
            ]
            values: list[Any] = [
                str(uuid4()),
                str(run_id),
                review.review_no,
                review.reviewer_response_id,
                model,
                review.verdict.value,
                review.score,
                int(review.goal_achieved),
            ]
            legacy_values = _legacy_review_flags(review)
            for legacy_column in (
                "can_be_fixed_by_llm",
                "requires_new_external_research",
            ):
                if legacy_column in table_columns:
                    columns.append(legacy_column)
                    values.append(legacy_values[legacy_column])
            columns.extend(
                [
                    "reviewer_confidence",
                    "review_json",
                    "created_at",
                ]
            )
            values.extend(
                [
                    review.reviewer_confidence,
                    review.model_dump_json(),
                    now,
                ]
            )
            connection.execute(
                f"""
                INSERT INTO research_reviews ({", ".join(columns)})
                VALUES ({", ".join("?" for _ in columns)})
                """,
                values,
            )
            self.append_history(
                connection,
                run_id,
                {
                    "step": "review_recorded",
                    "review_no": review.review_no,
                    "verdict": review.verdict.value,
                    "score": review.score,
                },
            )

    def add_tool_calls(
        self,
        run_id: UUID,
        *,
        response_id: str | None,
        step: str,
        tool_calls: list[ToolCallSummary],
    ) -> None:
        if not tool_calls:
            return

        with self.connect() as connection:
            for tool_call in tool_calls:
                self._insert_tool_call(
                    connection,
                    run_id,
                    response_id=response_id,
                    step=step,
                    tool_call=tool_call,
                )

    def add_citations(self, run_id: UUID, citations: list[Citation]) -> None:
        if not citations:
            return

        with self.connect() as connection:
            for citation in citations:
                self._insert_citation(connection, run_id, citation)

    def add_cost_event(self, run_id: UUID, cost_event: CostEvent) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO research_cost_events (
                    id, run_id, step, model, response_id, input_tokens, output_tokens,
                    tool_calls, estimated_cost_usd, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(run_id),
                    cost_event.step,
                    cost_event.model,
                    cost_event.response_id,
                    cost_event.input_tokens,
                    cost_event.output_tokens,
                    cost_event.tool_calls,
                    cost_event.estimated_cost_usd,
                    utc_now().isoformat(),
                ),
            )
            return cursor.rowcount == 1

    def get_attempts(self, run_id: UUID) -> list[ResearchAttempt]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM research_attempts
                WHERE run_id = ?
                ORDER BY attempt_no ASC, created_at ASC
                """,
                (str(run_id),),
            ).fetchall()

        return self._coalesce_attempt_rows(rows)

    def get_deep_research_submitted_at(self, run_id: UUID) -> datetime | None:
        run = self.get_run(run_id)
        response_id = run.pending_deep_research_response_id
        if not response_id:
            return None

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT created_at
                FROM research_attempts
                WHERE run_id = ?
                  AND response_id = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (str(run_id), response_id),
            ).fetchone()

        if row is None:
            return None
        return _parse_dt(row["created_at"])

    def get_reviews(self, run_id: UUID) -> list[ReviewRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT review_json FROM research_reviews
                WHERE run_id = ?
                ORDER BY review_no ASC, created_at ASC
                """,
                (str(run_id),),
            ).fetchall()

        return [ReviewRecord.model_validate_json(row["review_json"]) for row in rows]

    def _table_columns(self, connection: sqlite3.Connection, table_name: str) -> set[str]:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row["name"] for row in rows}

    def get_citations(self, run_id: UUID) -> list[Citation]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT title, url, source_type, start_index, end_index, retrieved_at
                FROM research_citations
                WHERE run_id = ?
                ORDER BY created_at ASC
                """,
                (str(run_id),),
            ).fetchall()

        return [Citation.model_validate(dict(row)) for row in rows]

    def get_tool_calls(self, run_id: UUID) -> list[ToolCallSummary]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT response_id, step, tool_type, status, query, url
                FROM research_tool_calls
                WHERE run_id = ?
                ORDER BY created_at ASC
                """,
                (str(run_id),),
            ).fetchall()

        return [
                ToolCallSummary(
                    type=row["tool_type"],
                    status=row["status"],
                    query=row["query"],
                    url=row["url"],
                    step=row["step"],
                    response_id=row["response_id"],
                )
            for row in rows
        ]

    def get_cost_events(self, run_id: UUID) -> list[CostEvent]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT step, model, response_id, input_tokens, output_tokens, tool_calls,
                       estimated_cost_usd, created_at
                FROM research_cost_events
                WHERE run_id = ?
                ORDER BY created_at ASC
                """,
                (str(run_id),),
            ).fetchall()

        return [
            CostEvent.model_validate({**dict(row), "created_at": _parse_dt(row["created_at"])})
            for row in rows
        ]

    def save_objective_contract(
        self,
        run_id: UUID,
        contract: ObjectiveContract,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM objective_contracts WHERE run_id = ?",
                (str(run_id),),
            )
            connection.execute(
                """
                INSERT INTO objective_contracts (
                    id, run_id, contract_json, contract_frozen, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(run_id),
                    contract.model_dump_json(),
                    int(contract.contract_frozen),
                    utc_now().isoformat(),
                ),
            )
            self.append_history(
                connection,
                run_id,
                {"step": "objective_contract_saved", "contract_id": contract.contract_id},
            )

    def get_objective_contract(self, run_id: UUID) -> ObjectiveContract | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT contract_json
                FROM objective_contracts
                WHERE run_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (str(run_id),),
            ).fetchone()
        if row is None:
            return None
        return ObjectiveContract.model_validate_json(row["contract_json"])

    def upsert_research_items(self, run_id: UUID, items: list[ResearchItem]) -> None:
        now = utc_now().isoformat()
        with self.connect() as connection:
            for item in items:
                existing = connection.execute(
                    """
                    SELECT id
                    FROM research_items
                    WHERE run_id = ? AND item_id = ?
                    """,
                    (str(run_id), item.item_id),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO research_items (
                            id, run_id, item_id, criterion_id, item_json, created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            str(run_id),
                            item.item_id,
                            item.criterion_id,
                            item.model_dump_json(),
                            now,
                            now,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE research_items
                        SET criterion_id = ?, item_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            item.criterion_id,
                            item.model_dump_json(),
                            now,
                            existing["id"],
                        ),
                    )
            self.append_history(
                connection,
                run_id,
                {"step": "research_items_upserted", "count": len(items)},
            )

    def get_research_items(self, run_id: UUID) -> list[ResearchItem]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT item_json
                FROM research_items
                WHERE run_id = ?
                ORDER BY item_id ASC, created_at ASC
                """,
                (str(run_id),),
            ).fetchall()
        return [ResearchItem.model_validate_json(row["item_json"]) for row in rows]

    def add_rerun_plan(self, run_id: UUID, plan: RerunPlan) -> None:
        created_at = utc_now()
        plan = plan.model_copy(update={"created_at": created_at})
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO rerun_plans (
                    id, run_id, rerun_id, scope, target_item_ids, plan_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(run_id),
                    plan.rerun_id,
                    plan.scope,
                    _json_dump(plan.target_item_ids),
                    plan.model_dump_json(),
                    created_at.isoformat(),
                ),
            )
            self.append_history(
                connection,
                run_id,
                {
                    "step": "rerun_plan_created",
                    "rerun_id": plan.rerun_id,
                    "scope": plan.scope,
                    "target_item_ids": plan.target_item_ids,
                },
            )

    def get_rerun_plans(self, run_id: UUID) -> list[RerunPlan]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT plan_json
                FROM rerun_plans
                WHERE run_id = ?
                ORDER BY created_at ASC
                """,
                (str(run_id),),
            ).fetchall()
        return [RerunPlan.model_validate_json(row["plan_json"]) for row in rows]

    def add_verification_query(
        self,
        run_id: UUID,
        query: VerificationQuery,
    ) -> None:
        created_at = query.created_at or utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO verification_queries (
                    id, run_id, item_id, raw_query, safe_query, policy_status,
                    blocked_reason, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(run_id),
                    query.item_id,
                    query.raw_query,
                    query.safe_query,
                    query.policy_status,
                    query.blocked_reason,
                    created_at.isoformat(),
                ),
            )
            self.append_history(
                connection,
                run_id,
                {
                    "step": "verification_query_policy_decision",
                    "item_id": query.item_id,
                    "policy_status": query.policy_status,
                    "blocked_reason": query.blocked_reason,
                },
            )

    def get_verification_queries(self, run_id: UUID) -> list[VerificationQuery]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT item_id, raw_query, safe_query, policy_status, blocked_reason,
                       created_at
                FROM verification_queries
                WHERE run_id = ?
                ORDER BY created_at ASC
                """,
                (str(run_id),),
            ).fetchall()
        return [
            VerificationQuery(
                item_id=row["item_id"],
                raw_query=row["raw_query"],
                safe_query=row["safe_query"],
                policy_status=row["policy_status"],
                blocked_reason=row["blocked_reason"],
                created_at=_parse_dt(row["created_at"]),
            )
            for row in rows
        ]

    def claim_human_review_decision(
        self,
        run_id: UUID,
        *,
        action: HumanReviewAction,
        comment: str | None,
        reviewer_id: str | None,
    ) -> tuple[ResearchRunRecord, HumanReviewDecision] | None:
        updated_at = utc_now().isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE research_runs
                SET status = ?,
                    needs_human_review = 0,
                    done_reason = NULL,
                    updated_at = ?
                WHERE id = ?
                  AND status = ?
                  AND needs_human_review = 1
                """,
                (
                    RunStatus.REVIEWING.value,
                    updated_at,
                    str(run_id),
                    RunStatus.NEEDS_HUMAN_REVIEW.value,
                ),
            )
            if cursor.rowcount != 1:
                return None

            decision = self._insert_human_decision(
                connection,
                run_id,
                action=action,
                comment=comment,
                reviewer_id=reviewer_id,
            )
            row = connection.execute(
                "SELECT * FROM research_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()

        if row is None:
            raise KeyError(str(run_id))
        return self._row_to_run(row), decision

    def _insert_human_decision(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
        *,
        action: HumanReviewAction,
        comment: str | None,
        reviewer_id: str | None,
    ) -> HumanReviewDecision:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(decision_no), 0) + 1 AS next_decision_no
            FROM human_review_decisions
            WHERE run_id = ?
            """,
            (str(run_id),),
        ).fetchone()
        decision_no = int(row["next_decision_no"])
        created_at = utc_now()
        connection.execute(
            """
            INSERT INTO human_review_decisions (
                id, run_id, decision_no, action, comment, reviewer_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                str(run_id),
                decision_no,
                action.value,
                comment,
                reviewer_id,
                created_at.isoformat(),
            ),
        )
        self.append_history(
            connection,
            run_id,
            {
                "step": "human_review_decision",
                "decision_no": decision_no,
                "action": action.value,
                "comment": comment,
                "reviewer_id": reviewer_id,
            },
        )
        return HumanReviewDecision(
            decision_no=decision_no,
            action=action,
            comment=comment,
            reviewer_id=reviewer_id,
            created_at=created_at,
        )

    def get_human_decisions(self, run_id: UUID) -> list[HumanReviewDecision]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT decision_no, action, comment, reviewer_id, created_at
                FROM human_review_decisions
                WHERE run_id = ?
                ORDER BY decision_no ASC, created_at ASC
                """,
                (str(run_id),),
            ).fetchall()

        return [
            HumanReviewDecision(
                decision_no=row["decision_no"],
                action=HumanReviewAction(row["action"]),
                comment=row["comment"],
                reviewer_id=row["reviewer_id"],
                created_at=_parse_dt(row["created_at"]),
            )
            for row in rows
        ]

    def add_checkpoint(
        self,
        run_id: UUID,
        *,
        kind: str,
        node_anchor: str,
        forkable: bool,
        dedupe_key: str,
        snapshot_json: dict[str, Any],
        source_attempt_no: int | None = None,
        source_review_no: int | None = None,
        source_response_id: str | None = None,
        checkpoint_report_hash: str | None = None,
    ) -> ResearchCheckpoint:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for _ in range(5):
                existing = connection.execute(
                    """
                    SELECT *
                    FROM research_checkpoints
                    WHERE run_id = ? AND dedupe_key = ?
                    """,
                    (str(run_id), dedupe_key),
                ).fetchone()
                if existing is not None:
                    return self._row_to_checkpoint(existing, child_forks=[])

                row = connection.execute(
                    """
                    SELECT COALESCE(MAX(checkpoint_no), 0) + 1 AS next_checkpoint_no
                    FROM research_checkpoints
                    WHERE run_id = ?
                    """,
                    (str(run_id),),
                ).fetchone()
                checkpoint_no = int(row["next_checkpoint_no"])
                checkpoint_id = uuid4()
                checkpoint_snapshot = {
                    **snapshot_json,
                    "run_id": str(run_id),
                    "checkpoint_id": str(checkpoint_id),
                }
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO research_checkpoints (
                            checkpoint_id, run_id, checkpoint_no, kind, node_anchor,
                            forkable, dedupe_key, source_attempt_no, source_review_no,
                            source_response_id, report_hash, snapshot_json, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(checkpoint_id),
                            str(run_id),
                            checkpoint_no,
                            kind,
                            node_anchor,
                            int(forkable),
                            dedupe_key,
                            source_attempt_no,
                            source_review_no,
                            source_response_id,
                            checkpoint_report_hash,
                            _json_dump(checkpoint_snapshot),
                            now.isoformat(),
                        ),
                    )
                except sqlite3.IntegrityError:
                    existing = connection.execute(
                        """
                        SELECT *
                        FROM research_checkpoints
                        WHERE run_id = ? AND dedupe_key = ?
                        """,
                        (str(run_id), dedupe_key),
                    ).fetchone()
                    if existing is not None:
                        return self._row_to_checkpoint(existing, child_forks=[])
                    continue
                if cursor.rowcount != 1:
                    existing = connection.execute(
                        """
                        SELECT *
                        FROM research_checkpoints
                        WHERE run_id = ? AND dedupe_key = ?
                        """,
                        (str(run_id), dedupe_key),
                    ).fetchone()
                    if existing is not None:
                        return self._row_to_checkpoint(existing, child_forks=[])
                    continue

                inserted = connection.execute(
                    "SELECT * FROM research_checkpoints WHERE checkpoint_id = ?",
                    (str(checkpoint_id),),
                ).fetchone()
                if inserted is None:
                    raise RuntimeError("Checkpoint insert could not be read back.")
                self.append_history(
                    connection,
                    run_id,
                    {
                        "step": "research_checkpoint_saved",
                        "checkpoint_id": str(checkpoint_id),
                        "checkpoint_no": checkpoint_no,
                        "kind": kind,
                        "node_anchor": node_anchor,
                        "forkable": forkable,
                    },
                )
                return self._row_to_checkpoint(inserted, child_forks=[])
            raise RuntimeError("Checkpoint insert collided repeatedly.")

    def list_checkpoints(
        self,
        run_id: UUID,
        *,
        include_forks: bool = False,
    ) -> list[ResearchCheckpoint]:
        with self.connect() as connection:
            checkpoint_rows = connection.execute(
                """
                SELECT *
                FROM research_checkpoints
                WHERE run_id = ?
                ORDER BY checkpoint_no ASC
                """,
                (str(run_id),),
            ).fetchall()
            forks_by_checkpoint = (
                self._child_forks_by_checkpoint(connection, parent_run_id=run_id)
                if include_forks
                else {}
            )

        return [
            self._row_to_checkpoint(
                row,
                child_forks=forks_by_checkpoint.get(row["checkpoint_id"], []),
            )
            for row in checkpoint_rows
        ]

    def get_checkpoint(
        self,
        run_id: UUID,
        checkpoint_id: UUID,
        *,
        include_forks: bool = False,
    ) -> ResearchCheckpoint:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM research_checkpoints
                WHERE run_id = ? AND checkpoint_id = ?
                """,
                (str(run_id), str(checkpoint_id)),
            ).fetchone()
            if row is None:
                raise KeyError(str(checkpoint_id))
            child_forks = (
                self._child_forks_by_checkpoint(
                    connection,
                    parent_run_id=run_id,
                    checkpoint_id=checkpoint_id,
                ).get(str(checkpoint_id), [])
                if include_forks
                else []
            )
        return self._row_to_checkpoint(row, child_forks=child_forks)

    def get_lineage(self, run_id: UUID) -> ResearchRunLineage | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM research_run_lineage
                WHERE run_id = ?
                """,
                (str(run_id),),
            ).fetchone()
        return None if row is None else self._row_to_lineage(row)

    def get_lineage_by_fork_request(
        self,
        *,
        parent_run_id: UUID,
        checkpoint_id: UUID,
        idempotency_key: str,
    ) -> ResearchRunLineage | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM research_run_lineage
                WHERE parent_run_id = ?
                  AND forked_from_checkpoint_id = ?
                  AND idempotency_key = ?
                """,
                (str(parent_run_id), str(checkpoint_id), idempotency_key),
            ).fetchone()
        return None if row is None else self._row_to_lineage(row)

    def update_lineage_source_report_artifact_path(
        self,
        run_id: UUID,
        source_report_artifact_path: str,
    ) -> ResearchRunLineage:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE research_run_lineage
                SET source_report_artifact_path = ?
                WHERE run_id = ?
                """,
                (source_report_artifact_path, str(run_id)),
            )
        lineage = self.get_lineage(run_id)
        if lineage is None:
            raise KeyError(str(run_id))
        return lineage

    def list_child_forks(self, parent_run_id: UUID) -> list[ResearchCheckpointChildFork]:
        with self.connect() as connection:
            rows_by_checkpoint = self._child_forks_by_checkpoint(
                connection,
                parent_run_id=parent_run_id,
            )
        forks: list[ResearchCheckpointChildFork] = []
        for checkpoint_forks in rows_by_checkpoint.values():
            forks.extend(checkpoint_forks)
        return forks

    def get_history(self, run_id: UUID) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_json FROM research_history
                WHERE run_id = ?
                ORDER BY created_at ASC
                """,
                (str(run_id),),
            ).fetchall()

        return [json.loads(row["event_json"]) for row in rows]

    def append_history(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
        event: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO research_history (id, run_id, event_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (str(uuid4()), str(run_id), _json_dump(event), utc_now().isoformat()),
        )

    def append_history_event(self, run_id: UUID, event: dict[str, Any]) -> None:
        with self.connect() as connection:
            self.append_history(connection, run_id, event)

    def _row_to_run(self, row: sqlite3.Row) -> ResearchRunRecord:
        return ResearchRunRecord(
            id=UUID(row["id"]),
            thread_id=row["thread_id"],
            user_prompt=row["user_prompt"],
            optimized_prompt=row["optimized_prompt"],
            status=RunStatus(row["status"]),
            report=row["report"],
            final_report=row["final_report"],
            done_reason=row["done_reason"],
            needs_human_review=bool(row["needs_human_review"]),
            pending_deep_research_response_id=row["pending_deep_research_response_id"],
            deep_research_status=row["deep_research_status"],
            deep_research_runs=row["deep_research_runs"],
            targeted_rerun_runs=row["targeted_rerun_runs"],
            full_rerun_runs=row["full_rerun_runs"],
            llm_patch_runs=row["llm_patch_runs"],
            verification_runs=row["verification_runs"],
            total_reviews=row["total_reviews"],
            no_progress_count=row["no_progress_count"],
            max_targeted_rerun_runs=row["max_targeted_rerun_runs"],
            max_full_rerun_runs=row["max_full_rerun_runs"],
            max_llm_patch_runs=row["max_llm_patch_runs"],
            max_verification_runs=row["max_verification_runs"],
            max_total_iterations=row["max_total_iterations"],
            max_total_tool_calls=row["max_total_tool_calls"],
            total_tool_calls=row["total_tool_calls"],
            estimated_cost_usd=row["estimated_cost_usd"],
            terminal_status=row["terminal_status"],
            review_claim_token=row["review_claim_token"],
            review_claim_operation=row["review_claim_operation"],
            review_claim_expires_at=(
                _parse_dt(row["review_claim_expires_at"])
                if row["review_claim_expires_at"]
                else None
            ),
            warnings=_json_load(row["warnings"], []),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def _row_to_attempt(self, row: sqlite3.Row) -> ResearchAttempt:
        tool_payload = _json_load(row["tool_calls"], {"tool_calls_summary": []})
        citation_payload = _json_load(row["citations"], {"citations": []})
        return ResearchAttempt(
            run_no=row["attempt_no"],
            response_id=row["response_id"],
            status=row["status"],
            model=row["model"],
            prompt=row["prompt"],
            report=row["output_text"] or "",
            citations=[
                Citation.model_validate(item) for item in citation_payload.get("citations", [])
            ],
            tool_calls_summary=[
                ToolCallSummary.model_validate(item)
                for item in tool_payload.get("tool_calls_summary", [])
            ],
            error=row["error"],
            raw_response_artifact_path=row["raw_response_artifact_path"],
            created_at=_parse_dt(row["created_at"]),
        )

    def _coalesce_attempt_rows(self, rows: list[sqlite3.Row]) -> list[ResearchAttempt]:
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["attempt_no"], []).append(row)

        attempts: list[ResearchAttempt] = []
        for attempt_no in sorted(grouped):
            attempt_rows = grouped[attempt_no]
            latest = self._row_to_attempt(attempt_rows[-1])
            submitted_prompt = next(
                (row["prompt"] for row in attempt_rows if row["prompt"]),
                latest.prompt,
            )
            attempts.append(latest.model_copy(update={"prompt": submitted_prompt}))
        return attempts

    def _insert_citation(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
        citation: Citation,
        *,
        attempt_id: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO research_citations (
                id, run_id, attempt_id, title, url, source_type, start_index, end_index,
                retrieved_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                str(run_id),
                attempt_id,
                citation.title,
                citation.url,
                citation.source_type,
                citation.start_index,
                citation.end_index,
                citation.retrieved_at,
                utc_now().isoformat(),
            ),
        )

    def _insert_tool_call(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
        *,
        response_id: str | None,
        step: str,
        tool_call: ToolCallSummary,
    ) -> None:
        connection.execute(
            """
            INSERT INTO research_tool_calls (
                id, run_id, response_id, step, tool_type, query, url, status,
                raw_tool_call, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                str(run_id),
                response_id,
                step,
                tool_call.type,
                tool_call.query,
                tool_call.url,
                tool_call.status,
                tool_call.model_dump_json(),
                utc_now().isoformat(),
            ),
        )

    def _row_to_checkpoint(
        self,
        row: sqlite3.Row,
        *,
        child_forks: list[ResearchCheckpointChildFork],
    ) -> ResearchCheckpoint:
        return ResearchCheckpoint(
            checkpoint_id=UUID(row["checkpoint_id"]),
            run_id=UUID(row["run_id"]),
            checkpoint_no=int(row["checkpoint_no"]),
            kind=row["kind"],
            node_anchor=row["node_anchor"],
            forkable=bool(row["forkable"]),
            dedupe_key=row["dedupe_key"],
            source_attempt_no=row["source_attempt_no"],
            source_review_no=row["source_review_no"],
            source_response_id=row["source_response_id"],
            report_hash=row["report_hash"],
            snapshot_json=_json_load(row["snapshot_json"], {}),
            created_at=_parse_dt(row["created_at"]),
            forks=child_forks,
            child_forks=child_forks,
        )

    def _row_to_lineage(self, row: sqlite3.Row) -> ResearchRunLineage:
        return ResearchRunLineage(
            run_id=UUID(row["run_id"]),
            root_run_id=UUID(row["root_run_id"]),
            parent_run_id=UUID(row["parent_run_id"]),
            forked_from_checkpoint_id=UUID(row["forked_from_checkpoint_id"]),
            fork_mode=row["fork_mode"],
            additional_prompt=row["additional_prompt"],
            confirmed_preview_hash=row["confirmed_preview_hash"],
            idempotency_key=row["idempotency_key"],
            source_snapshot_json=_json_load(row["source_snapshot_json"], {}),
            source_report_artifact_path=row["source_report_artifact_path"],
            created_at=_parse_dt(row["created_at"]),
        )

    def _child_forks_by_checkpoint(
        self,
        connection: sqlite3.Connection,
        *,
        parent_run_id: UUID,
        checkpoint_id: UUID | None = None,
    ) -> dict[str, list[ResearchCheckpointChildFork]]:
        values: list[Any] = [str(parent_run_id)]
        checkpoint_filter = ""
        if checkpoint_id is not None:
            checkpoint_filter = "AND l.forked_from_checkpoint_id = ?"
            values.append(str(checkpoint_id))
        rows = connection.execute(
            f"""
            SELECT l.forked_from_checkpoint_id, r.id, r.status, r.done_reason, r.created_at
            FROM research_run_lineage l
            JOIN research_runs r
              ON r.id = l.run_id
            WHERE l.parent_run_id = ?
              {checkpoint_filter}
            ORDER BY l.created_at ASC
            """,
            values,
        ).fetchall()
        forks_by_checkpoint: dict[str, list[ResearchCheckpointChildFork]] = {}
        for row in rows:
            forks_by_checkpoint.setdefault(row["forked_from_checkpoint_id"], []).append(
                ResearchCheckpointChildFork(
                    run_id=UUID(row["id"]),
                    status=RunStatus(row["status"]),
                    done_reason=row["done_reason"],
                    created_at=_parse_dt(row["created_at"]),
                )
            )
        return forks_by_checkpoint

    def _db_value(self, value: Any) -> Any:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, RunStatus):
            return value.value
        if isinstance(value, list | dict):
            return _json_dump(value)
        return value


def make_repository(settings: Settings) -> ResearchRepository:
    return ResearchRepository(settings.research_db_path)
