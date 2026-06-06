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
    ResearchAttempt,
    ResearchRunOptions,
    ResearchRunRecord,
    ReviewRecord,
    RunStatus,
    ToolCallSummary,
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
                    context_classification TEXT NOT NULL DEFAULT 'public',
                    web_search_allowed INTEGER NOT NULL DEFAULT 1,
                    contains_confidential_context INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    report TEXT,
                    final_report TEXT,
                    done_reason TEXT,
                    needs_human_review INTEGER NOT NULL DEFAULT 0,
                    pending_deep_research_response_id TEXT,
                    deep_research_status TEXT,
                    deep_research_runs INTEGER NOT NULL DEFAULT 0,
                    llm_fix_runs INTEGER NOT NULL DEFAULT 0,
                    total_reviews INTEGER NOT NULL DEFAULT 0,
                    no_progress_count INTEGER NOT NULL DEFAULT 0,
                    max_deep_research_runs INTEGER NOT NULL DEFAULT 2,
                    max_llm_fix_runs INTEGER NOT NULL DEFAULT 3,
                    max_total_iterations INTEGER NOT NULL DEFAULT 5,
                    max_no_progress_rounds INTEGER NOT NULL DEFAULT 2,
                    max_total_tool_calls INTEGER NOT NULL DEFAULT 120,
                    max_cost_usd REAL NOT NULL DEFAULT 20,
                    total_tool_calls INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd REAL NOT NULL DEFAULT 0,
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
                    can_be_fixed_by_llm INTEGER NOT NULL,
                    requires_new_external_research INTEGER NOT NULL,
                    reviewer_confidence INTEGER NOT NULL,
                    review_json TEXT NOT NULL,
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

                CREATE UNIQUE INDEX IF NOT EXISTS research_cost_events_response_dedupe
                ON research_cost_events(run_id, step, response_id)
                WHERE response_id IS NOT NULL AND response_id != '';

                CREATE UNIQUE INDEX IF NOT EXISTS human_review_decisions_run_decision_no_unique
                ON human_review_decisions(run_id, decision_no);

                CREATE INDEX IF NOT EXISTS human_review_decisions_run_order
                ON human_review_decisions(run_id, decision_no, created_at);
                """
            )

    def create_run(
        self,
        *,
        user_prompt: str,
        options: ResearchRunOptions,
        settings: Settings,
        contains_confidential_context: bool,
    ) -> ResearchRunRecord:
        now = utc_now()
        run_id = uuid4()
        thread_id = str(uuid4())
        max_cost = options.max_cost_usd or settings.default_max_cost_usd
        max_tool_calls = options.max_total_tool_calls or settings.default_max_total_tool_calls

        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO research_runs (
                    id, thread_id, user_prompt, context_classification, web_search_allowed,
                    contains_confidential_context, status, needs_human_review,
                    max_deep_research_runs, max_llm_fix_runs, max_total_iterations,
                    max_no_progress_rounds, max_total_tool_calls, max_cost_usd,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    thread_id,
                    user_prompt,
                    options.context_classification,
                    int(options.allow_web_search),
                    int(contains_confidential_context),
                    RunStatus.QUEUED.value,
                    options.max_deep_research_runs or settings.default_max_deep_research_runs,
                    options.max_llm_fix_runs or settings.default_max_llm_fix_runs,
                    options.max_total_iterations or settings.default_max_total_iterations,
                    options.max_no_progress_rounds or settings.default_max_no_progress_rounds,
                    max_tool_calls,
                    max_cost,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            self.append_history(connection, run_id, {"step": "research_run_created"})

        return self.get_run(run_id)

    def get_run(self, run_id: UUID) -> ResearchRunRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()

        if row is None:
            raise KeyError(str(run_id))

        return self._row_to_run(row)

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

    def claim_deep_research_run(self, run_id: UUID) -> ResearchRunRecord | None:
        now = utc_now().isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE research_runs
                SET status = ?, updated_at = ?
                WHERE id = ?
                  AND status = ?
                  AND pending_deep_research_response_id IS NOT NULL
                """,
                (
                    RunStatus.COLLECTING.value,
                    now,
                    str(run_id),
                    RunStatus.WAITING_DEEP_RESEARCH.value,
                ),
            )
            if cursor.rowcount != 1:
                return None

        return self.get_run(run_id)

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

    def add_attempt(self, run_id: UUID, attempt: ResearchAttempt) -> None:
        now = utc_now().isoformat()
        attempt_id = str(uuid4())
        with self.connect() as connection:
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
                    "step": "attempt_recorded",
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
            connection.execute(
                """
                INSERT INTO research_reviews (
                    id, run_id, review_no, response_id, model, verdict, score, goal_achieved,
                    can_be_fixed_by_llm, requires_new_external_research, reviewer_confidence,
                    review_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(run_id),
                    review.review_no,
                    review.reviewer_response_id,
                    model,
                    review.verdict.value,
                    review.score,
                    int(review.goal_achieved),
                    int(review.can_be_fixed_by_llm),
                    int(review.requires_new_external_research),
                    review.reviewer_confidence,
                    review.model_dump_json(),
                    now,
                ),
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

        return [self._row_to_attempt(row) for row in rows]

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
            context_classification=row["context_classification"],
            web_search_allowed=bool(row["web_search_allowed"]),
            contains_confidential_context=bool(row["contains_confidential_context"]),
            status=RunStatus(row["status"]),
            report=row["report"],
            final_report=row["final_report"],
            done_reason=row["done_reason"],
            needs_human_review=bool(row["needs_human_review"]),
            pending_deep_research_response_id=row["pending_deep_research_response_id"],
            deep_research_status=row["deep_research_status"],
            deep_research_runs=row["deep_research_runs"],
            llm_fix_runs=row["llm_fix_runs"],
            total_reviews=row["total_reviews"],
            no_progress_count=row["no_progress_count"],
            max_deep_research_runs=row["max_deep_research_runs"],
            max_llm_fix_runs=row["max_llm_fix_runs"],
            max_total_iterations=row["max_total_iterations"],
            max_no_progress_rounds=row["max_no_progress_rounds"],
            max_total_tool_calls=row["max_total_tool_calls"],
            max_cost_usd=row["max_cost_usd"],
            total_tool_calls=row["total_tool_calls"],
            estimated_cost_usd=row["estimated_cost_usd"],
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
        )

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
