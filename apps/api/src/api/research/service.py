from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import ValidationError

from api.config import Settings
from api.research.artifacts import ArtifactStore
from api.research.azure_responses import (
    AzureResponsesClient,
    ReviewRequestTimeout,
    ReviewResponseParseError,
)
from api.research.costing import (
    build_cost_event,
    count_billable_web_search_calls,
    estimate_usage_cost_usd,
)
from api.research.extractors import (
    extract_citations,
    extract_tool_calls,
    get_response_id,
    get_response_output_text,
    get_response_status,
    response_to_jsonable,
)
from api.research.merge import (
    PatchDelta as MergePatchDelta,
)
from api.research.merge import (
    RegressionError,
    ReportDocument,
    deterministic_merge,
)
from api.research.nodes import build_objective_contract
from api.research.progress import compute_no_progress_count, report_hash
from api.research.query_policy import contains_sensitive_terms, query_policy_gate
from api.research.repository import ResearchRepository
from api.research.routing import (
    MIN_REVIEWER_CONFIDENCE_FOR_AUTO_FINALIZE,
    RouteState,
    route_after_review_decision,
)
from api.research.schemas import (
    Citation,
    CostEvent,
    CreateResearchRunRequest,
    FailureMode,
    ForkPreviewResponse,
    ForkSubmitResponse,
    HumanReviewAction,
    HumanReviewActionState,
    HumanReviewAuditSummary,
    HumanReviewPayload,
    HumanReviewQueueItem,
    HumanReviewResumeRequest,
    HumanReviewRouteSummary,
    ItemStatus,
    QueryPolicyDecision,
    RecommendedAction,
    RerunExecutionMode,
    RerunPlan,
    ResearchAttempt,
    ResearchCheckpoint,
    ResearchItem,
    ResearchRunLineage,
    ResearchRunOptions,
    ResearchRunRecord,
    ReviewRecord,
    RunStatus,
    Severity,
    SuggestedRerunPrompt,
    Verdict,
    VerificationQuery,
    utc_now,
)

TERMINAL_FAILURE_STATUSES = {"failed", "cancelled", "incomplete"}
TERMINAL_RUN_STATUSES = {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED}
NON_TERMINAL_RUN_STATUSES = set(RunStatus) - TERMINAL_RUN_STATUSES
MANUAL_RERUN_PROMPT_BLOCKED_REASON = "manual_rerun_prompt_blocked_by_query_policy"
MANUAL_RERUN_ACTION_SCOPES = {
    HumanReviewAction.REQUEST_MANUAL_TARGETED_RERUN: "targeted_gap_closure",
    HumanReviewAction.REQUEST_MANUAL_FULL_RERUN: "full_rerun",
}


class ResearchOrchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: ResearchRepository,
        artifacts: ArtifactStore,
        azure: AzureResponsesClient,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.artifacts = artifacts
        self.azure = azure

    def create_run(
        self,
        request: CreateResearchRunRequest,
        *,
        forecast_mode: bool = False,
        tool_profile: Literal["public", "private", "synthesis"] = "public",
        background: bool = True,
        policy_decision_id: str | None = None,
    ) -> ResearchRunRecord:
        if forecast_mode and not policy_decision_id:
            raise ValueError("policy_decision_id is required for forecast research runs.")
        run = self.repository.create_run(
            user_prompt=request.user_prompt,
            options=request.options,
            settings=self.settings,
            run_origin="forecast" if forecast_mode else "research",
        )
        return self.submit_deep_research(
            run.id,
            tool_profile=tool_profile,
            background=background,
            policy_decision_id=policy_decision_id,
        )

    def create_run_record(
        self,
        request: CreateResearchRunRequest,
        *,
        forecast_mode: bool = False,
    ) -> ResearchRunRecord:
        """Create a ResearchRun row without submitting it to Deep Research."""
        return self.repository.create_run(
            user_prompt=request.user_prompt,
            options=request.options,
            settings=self.settings,
            run_origin="forecast" if forecast_mode else "research",
        )

    def create_manual_import_run(
        self,
        *,
        input_prompt: str,
        report: str,
        options: ResearchRunOptions,
        allow_remote_review: bool,
        allow_api_reruns: bool,
        rerun_execution_mode: RerunExecutionMode | None = None,
        idempotency_key: str | None,
        metadata: dict[str, Any],
    ) -> tuple[ResearchRunRecord, bool]:
        mode = rerun_execution_mode or (
            RerunExecutionMode.API if allow_api_reruns else RerunExecutionMode.DISABLED
        )
        if not allow_api_reruns and mode == RerunExecutionMode.API:
            mode = RerunExecutionMode.DISABLED
        if mode == RerunExecutionMode.DISABLED:
            options = options.model_copy(
                update={
                    "max_targeted_rerun_runs": 0,
                    "max_full_rerun_runs": 0,
                }
            )

        imported_at = utc_now()
        options_payload = options.model_dump(mode="json")
        request_metadata = {
            **metadata,
            "prompt_sha256": hashlib.sha256(input_prompt.encode("utf-8")).hexdigest(),
            "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
            "prompt_chars": len(input_prompt),
            "report_chars": len(report),
            "allow_remote_review": allow_remote_review,
            "allow_api_reruns": allow_api_reruns,
            "rerun_execution_mode": mode.value,
            "options": options_payload,
            "imported_at": imported_at.isoformat(),
        }
        request_hash = _manual_import_request_hash(
            input_prompt=input_prompt,
            report=report,
            options=options_payload,
            allow_remote_review=allow_remote_review,
            allow_api_reruns=allow_api_reruns,
            rerun_execution_mode=mode,
        )
        if idempotency_key:
            existing = self.repository.get_manual_import_idempotency(idempotency_key)
            if existing is not None:
                existing_run, existing_hash = existing
                if existing_hash != request_hash:
                    raise ValueError(
                        "Manual import idempotency key already exists with a "
                        "different request."
                    )
                return existing_run, False

        contract, research_items, _optimized_prompt = build_objective_contract(
            user_prompt=input_prompt,
        )
        run_id = uuid4()
        thread_id = str(uuid4())
        try:
            prompt_path, _ = self.artifacts.save_text(
                run_id,
                "prompts/manual_import_prompt.txt",
                input_prompt,
            )
            report_path, _ = self.artifacts.save_text(
                run_id,
                "reports/report_attempt_001.md",
                report,
            )
            raw_path, _ = self.artifacts.save_json(
                run_id,
                "raw-responses/manual_import_001.json",
                {
                    **request_metadata,
                    "source": "manual_upload",
                    "prompt_path": prompt_path,
                    "report_path": report_path,
                    "request_hash": request_hash,
                },
            )
            citations = _manual_upload_citations(report, retrieved_at=imported_at.isoformat())
            attempt = ResearchAttempt(
                run_no=1,
                response_id=None,
                status="completed",
                model="chatgpt-deep-research-manual",
                source="manual_upload",
                prompt=input_prompt,
                report=report,
                citations=citations,
                raw_response_artifact_path=raw_path,
            )
            report_digest = report_hash(report)
            manual_checkpoint_snapshot = _manual_import_checkpoint_snapshot(
                kind="deep_research_collected",
                status=RunStatus.REVIEWING,
                input_prompt=input_prompt,
                report=report,
                source_attempt_no=1,
                snapshot_extra={
                    "source": "manual_upload",
                    "prompt_path": prompt_path,
                    "report_path": report_path,
                    "citations_count": len(citations),
                },
            )
            done_reason: str | None = None
            if not allow_remote_review:
                done_reason = "manual_import_remote_review_not_allowed"
            dispatch_review = done_reason is None
            initial_status = (
                RunStatus.REVIEWING if dispatch_review else RunStatus.NEEDS_HUMAN_REVIEW
            )
            human_review_checkpoint_snapshot = None
            if done_reason is not None:
                human_review_checkpoint_snapshot = _manual_import_checkpoint_snapshot(
                    kind="human_review_required",
                    status=RunStatus.NEEDS_HUMAN_REVIEW,
                    input_prompt=input_prompt,
                    report=report,
                    source_attempt_no=1,
                    snapshot_extra={
                        "reason": done_reason,
                        "audit_summary": {
                            "deep_research_runs": 1,
                            "targeted_rerun_runs": 0,
                            "full_rerun_runs": 0,
                            "llm_patch_runs": 0,
                            "verification_runs": 0,
                            "total_reviews": 0,
                            "no_progress_count": 0,
                            "total_tool_calls": 0,
                            "estimated_cost_usd": 0,
                        },
                    },
                )

            run, created = self.repository.create_manual_import_run(
                run_id=run_id,
                thread_id=thread_id,
                input_prompt=input_prompt,
                report=report,
                options=options,
                settings=self.settings,
                request_hash=request_hash,
                request_metadata=request_metadata,
                idempotency_key=idempotency_key,
                contract=contract,
                research_items=research_items,
                attempt=attempt,
                prompt_path=prompt_path,
                report_path=report_path,
                initial_status=initial_status,
                needs_human_review=not dispatch_review,
                done_reason=done_reason,
                manual_checkpoint_snapshot=manual_checkpoint_snapshot,
                manual_checkpoint_report_hash=report_digest,
                human_review_checkpoint_snapshot=human_review_checkpoint_snapshot,
                human_review_checkpoint_report_hash=report_digest if done_reason else None,
            )
        except Exception:
            self.artifacts.delete_run(run_id)
            raise
        if not created:
            self.artifacts.delete_run(run_id)
            return run, False
        return run, dispatch_review

    def create_forecast_manual_import_run(
        self,
        *,
        input_prompt: str,
        report: str,
        metadata: dict[str, Any],
    ) -> ResearchRunRecord:
        imported_at = utc_now()
        options = ResearchRunOptions(
            max_targeted_rerun_runs=0,
            max_full_rerun_runs=0,
        )
        options_payload = options.model_dump(mode="json")
        request_metadata = {
            **metadata,
            "prompt_sha256": hashlib.sha256(input_prompt.encode("utf-8")).hexdigest(),
            "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
            "prompt_chars": len(input_prompt),
            "report_chars": len(report),
            "allow_remote_review": False,
            "allow_api_reruns": False,
            "rerun_execution_mode": RerunExecutionMode.DISABLED.value,
            "options": options_payload,
            "imported_at": imported_at.isoformat(),
            "run_origin": "forecast",
        }
        request_hash = _manual_import_request_hash(
            input_prompt=input_prompt,
            report=report,
            options=options_payload,
            allow_remote_review=False,
            allow_api_reruns=False,
            rerun_execution_mode=RerunExecutionMode.DISABLED,
        )
        contract, research_items, _optimized_prompt = build_objective_contract(
            user_prompt=input_prompt,
        )
        run_id = uuid4()
        thread_id = str(uuid4())
        try:
            prompt_path, _ = self.artifacts.save_text(
                run_id,
                "prompts/forecast_manual_prompt.txt",
                input_prompt,
            )
            report_path, _ = self.artifacts.save_text(
                run_id,
                "reports/report_attempt_001.md",
                report,
            )
            raw_path, _ = self.artifacts.save_json(
                run_id,
                "raw-responses/forecast_manual_import_001.json",
                {
                    **request_metadata,
                    "source": "manual_upload",
                    "prompt_path": prompt_path,
                    "report_path": report_path,
                    "request_hash": request_hash,
                },
            )
            citations = _manual_upload_citations(report, retrieved_at=imported_at.isoformat())
            attempt = ResearchAttempt(
                run_no=1,
                response_id=None,
                status="completed",
                model="chatgpt-deep-research-manual",
                source="manual_upload",
                prompt=input_prompt,
                report=report,
                citations=citations,
                raw_response_artifact_path=raw_path,
            )
            report_digest = report_hash(report)
            checkpoint_snapshot = _manual_import_checkpoint_snapshot(
                kind="deep_research_collected",
                status=RunStatus.COMPLETED,
                input_prompt=input_prompt,
                report=report,
                source_attempt_no=1,
                snapshot_extra={
                    "source": "manual_upload",
                    "prompt_path": prompt_path,
                    "report_path": report_path,
                    "citations_count": len(citations),
                    "forecast_manual_import": True,
                },
            )
            run, created = self.repository.create_manual_import_run(
                run_id=run_id,
                thread_id=thread_id,
                input_prompt=input_prompt,
                report=report,
                options=options,
                settings=self.settings,
                request_hash=request_hash,
                request_metadata=request_metadata,
                idempotency_key=None,
                contract=contract,
                research_items=research_items,
                attempt=attempt,
                prompt_path=prompt_path,
                report_path=report_path,
                initial_status=RunStatus.COMPLETED,
                needs_human_review=False,
                done_reason=None,
                manual_checkpoint_snapshot=checkpoint_snapshot,
                manual_checkpoint_report_hash=report_digest,
                final_report=report,
                run_origin="forecast",
                terminal_status="completed_manual_import",
            )
        except Exception:
            self.artifacts.delete_run(run_id)
            raise
        if not created:
            self.artifacts.delete_run(run_id)
        return run

    def submit_deep_research(
        self,
        run_id: UUID,
        *,
        human_comment: str | None = None,
        scope: str = "targeted_gap_closure",
        tool_profile: Literal["public", "private", "synthesis"] = "public",
        background: bool = True,
        policy_decision_id: str | None = None,
    ) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if run.run_origin == "forecast" and not policy_decision_id:
            raise ValueError("policy_decision_id is required for forecast research runs.")
        run_no = run.deep_research_runs + 1
        requested_max_tool_calls = self.settings.default_max_total_tool_calls

        if run.deep_research_runs == 0:
            contract, research_items, optimized_prompt = build_objective_contract(
                user_prompt=run.user_prompt,
            )
            self.repository.save_objective_contract(run.id, contract)
            self.repository.upsert_research_items(run.id, research_items)
            acceptance_criteria = [
                criterion.description for criterion in contract.acceptance_criteria
            ]
            prompt = optimized_prompt
            prompt_path, _ = self.artifacts.save_text(
                run.id,
                "prompts/optimized_prompt.txt",
                prompt,
            )
            self.repository.append_history_event(
                run.id,
                {
                    "step": "optimized_prompt_created",
                    "artifact_path": prompt_path,
                    "contract_id": contract.contract_id,
                    "research_item_ids": [item.item_id for item in research_items],
                    "acceptance_criteria": acceptance_criteria,
                },
            )
            run = self.repository.update_run(run.id, optimized_prompt=optimized_prompt)
            self._save_checkpoint(
                run,
                kind="research_brief_created",
                node_anchor="brief",
                report_override="",
                snapshot_extra={
                    "artifact_path": prompt_path,
                    "contract_id": contract.contract_id,
                    "research_item_ids": [item.item_id for item in research_items],
                },
                forkable=False,
            )
        else:
            optimized_prompt = run.optimized_prompt or run.user_prompt
            plan = self._build_rerun_plan(
                run,
                human_comment=human_comment,
                scope=scope,
            )
            requested_max_tool_calls = plan.max_tool_calls
            self.repository.add_rerun_plan(run.id, plan)
            prompt = self._build_rerun_brief(run, human_comment=human_comment, plan=plan)
            prompt_path, _ = self.artifacts.save_text(
                run.id,
                f"prompts/rerun_prompt_{run_no:03d}.txt",
                prompt,
            )
            self.repository.append_history_event(
                run.id,
                {
                    "step": "deep_research_rerun_brief_created",
                    "run_no": run_no,
                    "rerun_id": plan.rerun_id,
                    "target_item_ids": plan.target_item_ids,
                    "artifact_path": prompt_path,
                },
            )

        policy_decision = _deep_research_query_policy_decision(prompt)
        if policy_decision.status != "allowed":
            self.repository.append_history_event(
                run.id,
                {
                    "step": "deep_research_submit_blocked",
                    "reason": policy_decision.blocked_reason,
                    "policy_status": policy_decision.status,
                },
            )
            return self._enter_human_review(
                run.id,
                done_reason="deep_research_blocked_by_query_policy",
                optimized_prompt=optimized_prompt,
            )

        remaining_tool_calls = run.max_total_tool_calls - run.total_tool_calls
        if remaining_tool_calls <= 0:
            return self._enter_human_review(
                run.id,
                done_reason="max_total_tool_calls_reached_before_deep_research_submit",
                optimized_prompt=optimized_prompt,
            )

        response_id: str | None = None
        try:
            response = self.azure.submit_deep_research(
                prompt=prompt,
                max_tool_calls=min(
                    remaining_tool_calls,
                    requested_max_tool_calls,
                ),
                tool_profile=tool_profile,
                background=background,
                policy_decision_id=policy_decision_id,
            )
            response_id = get_response_id(response)
            raw_path, _ = self.artifacts.save_json(
                run.id,
                f"raw-responses/deep_research_submit_{run_no:03d}.json",
                response_to_jsonable(response),
            )
            response_status = get_response_status(response) or "queued"
            self.repository.add_attempt(
                run.id,
                ResearchAttempt(
                    run_no=run_no,
                    response_id=response_id,
                    status=response_status,
                    model=self.azure.deep_research_deployment,
                    prompt=prompt,
                    raw_response_artifact_path=raw_path,
                ),
            )
            updated = self.repository.update_run_if_status(
                run.id,
                {RunStatus.QUEUED, RunStatus.REVIEWING, RunStatus.SUBMITTED},
                optimized_prompt=optimized_prompt,
                status=RunStatus.WAITING_DEEP_RESEARCH,
                needs_human_review=False,
                pending_deep_research_response_id=response_id,
                deep_research_status=response_status,
                deep_research_runs=run_no,
                targeted_rerun_runs=(
                    run.targeted_rerun_runs + 1
                    if run.deep_research_runs > 0 and scope != "full_rerun"
                    else run.targeted_rerun_runs
                ),
                full_rerun_runs=(
                    run.full_rerun_runs + 1
                    if run.deep_research_runs > 0 and scope == "full_rerun"
                    else run.full_rerun_runs
                ),
                done_reason=None,
            )
            if updated is None:
                self._cancel_submitted_response_after_status_change(run.id, response_id)
                return self.repository.get_run(run.id)
            return updated
        except Exception as error:
            self._record_deep_research_submit_failure(
                run_id=run.id,
                run_no=run_no,
                prompt=prompt,
                error=error,
                response_id=response_id,
            )
            done_reason = (
                "deep_research_submitted_but_persistence_failed"
                if response_id
                else "deep_research_submit_failed"
            )
            return self._enter_human_review(
                run.id,
                done_reason=done_reason,
                optimized_prompt=optimized_prompt,
                deep_research_runs=run_no,
            )

    def _request_manual_chatgpt_rerun(
        self,
        run_id: UUID,
        *,
        human_comment: str | None = None,
        scope: str = "targeted_gap_closure",
    ) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        active = self.repository.get_active_manual_rerun_request(run.id)
        if active is not None:
            return self._enter_human_review(
                run.id,
                done_reason="manual_chatgpt_rerun_pending",
            )

        run_no = run.deep_research_runs + 1
        plan = self._build_rerun_plan(
            run,
            human_comment=human_comment,
            scope=scope,
        )
        self.repository.add_rerun_plan(run.id, plan)
        prompt = self._build_rerun_brief(run, human_comment=human_comment, plan=plan)
        prompt_path, _ = self.artifacts.save_text(
            run.id,
            f"prompts/rerun_prompt_{run_no:03d}.txt",
            prompt,
        )
        self.repository.append_history_event(
            run.id,
            {
                "step": "manual_rerun_brief_created",
                "run_no": run_no,
                "rerun_id": plan.rerun_id,
                "scope": plan.scope,
                "target_item_ids": plan.target_item_ids,
                "artifact_path": prompt_path,
            },
        )
        policy_decision = _deep_research_query_policy_decision(prompt)
        if policy_decision.status != "allowed":
            self.repository.append_history_event(
                run.id,
                {
                    "step": "manual_rerun_prompt_blocked_by_query_policy",
                    "rerun_id": plan.rerun_id,
                    "reason": policy_decision.blocked_reason,
                    "policy_status": policy_decision.status,
                },
            )
            return self._enter_human_review(
                run.id,
                done_reason="manual_rerun_prompt_blocked_by_query_policy",
                allowed_statuses={RunStatus.REVIEWING, RunStatus.NEEDS_HUMAN_REVIEW},
            )

        self.repository.add_manual_rerun_request(
            run.id,
            plan=plan,
            expected_run_no=run_no,
            prompt=prompt,
            prompt_artifact_path=prompt_path,
            query_policy=policy_decision,
            base_report_hash=report_hash(run.report or ""),
        )
        return self._enter_human_review(
            run.id,
            done_reason="manual_chatgpt_rerun_pending",
            allowed_statuses={RunStatus.REVIEWING, RunStatus.NEEDS_HUMAN_REVIEW},
        )

    def accept_manual_rerun_result(
        self,
        run_id: UUID,
        *,
        rerun_id: str,
        report_text: str,
    ) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)

        result = report_text.strip()
        if not result:
            raise ValueError("manual rerun result must not be blank.")
        result_sha256 = hashlib.sha256(result.encode("utf-8")).hexdigest()

        row = self.repository.get_manual_rerun_request_row(run.id, rerun_id)
        if row is not None and row["status"] == "accepted":
            if row["result_sha256"] == result_sha256:
                return self.repository.get_run(run.id)
            raise ValueError(
                "Manual rerun result was already accepted with different content."
            )

        active = self.repository.get_active_manual_rerun_request(run.id)
        if active is None:
            raise ValueError("Run has no pending manual ChatGPT rerun.")
        if active.rerun_id != rerun_id:
            raise ValueError("Manual rerun id does not match the active pending rerun.")
        if active.base_report_hash and active.base_report_hash != report_hash(run.report or ""):
            raise ValueError("Run report changed since the manual rerun prompt was created.")

        if active.scope == "full_rerun":
            merged_report = result
        else:
            try:
                merged_report = _merge_targeted_research_delta(
                    existing_report=run.report or "",
                    delta=result,
                    run_no=active.expected_run_no,
                )
            except RegressionError as error:
                self.repository.append_history_event(
                    run.id,
                    {
                        "step": "manual_rerun_targeted_merge_rejected",
                        "rerun_id": active.rerun_id,
                        "reason": str(error),
                    },
                )
                raise ValueError(str(error)) from error

        imported_at = utc_now()
        updated = self.repository.accept_manual_rerun_request_and_update_run(
            run.id,
            rerun_id=active.rerun_id,
            result_sha256=result_sha256,
            allowed_statuses={RunStatus.NEEDS_HUMAN_REVIEW, RunStatus.REVIEWING},
            status=RunStatus.REVIEWING,
            needs_human_review=False,
            done_reason=None,
            report=merged_report,
            deep_research_runs=active.expected_run_no,
            targeted_rerun_runs=(
                run.targeted_rerun_runs + 1
                if active.scope != "full_rerun"
                else run.targeted_rerun_runs
            ),
            full_rerun_runs=(
                run.full_rerun_runs + 1
                if active.scope == "full_rerun"
                else run.full_rerun_runs
            ),
            pending_deep_research_response_id=None,
            deep_research_status="completed",
            review_claim_token=None,
            review_claim_operation=None,
            review_claim_expires_at=None,
        )
        if updated is None:
            row = self.repository.get_manual_rerun_request_row(run.id, rerun_id)
            if row is not None and row["status"] == "accepted":
                if row["result_sha256"] == result_sha256:
                    return self.repository.get_run(run.id)
                raise ValueError(
                    "Manual rerun result was already accepted with different content."
                )
            raise ValueError("Manual rerun result could not be accepted because run state changed.")

        artifact_fragment = _manual_rerun_artifact_fragment(
            active.rerun_id,
            result_sha256,
        )
        raw_path, _ = self.artifacts.save_json(
            run.id,
            (
                f"raw-responses/manual_chatgpt_rerun_"
                f"{active.expected_run_no:03d}_{artifact_fragment}.json"
            ),
            {
                "source": "manual_chatgpt_rerun",
                "rerun_id": active.rerun_id,
                "scope": active.scope,
                "expected_run_no": active.expected_run_no,
                "result_sha256": result_sha256,
                "result_chars": len(result),
                "imported_at": imported_at.isoformat(),
                "prompt_artifact_path": active.prompt_artifact_path,
            },
        )
        result_path, _ = self.artifacts.save_text(
            run.id,
            (
                f"reports/manual_chatgpt_rerun_result_"
                f"{active.expected_run_no:03d}_{artifact_fragment}.md"
            ),
            result,
        )
        report_path, _ = self.artifacts.save_text(
            run.id,
            (
                f"reports/report_attempt_{active.expected_run_no:03d}_{artifact_fragment}.md"
            ),
            merged_report,
        )
        citations = _manual_upload_citations(
            result,
            retrieved_at=imported_at.isoformat(),
            source_type="manual_chatgpt_rerun_url_unverified",
        )
        self.repository.add_attempt(
            run.id,
            ResearchAttempt(
                run_no=active.expected_run_no,
                response_id=None,
                status="completed",
                model="chatgpt-deep-research-manual",
                source="manual_chatgpt_rerun",
                prompt=active.prompt,
                report=result,
                citations=citations,
                raw_response_artifact_path=raw_path,
            ),
        )

        self.repository.append_history_event(
            run.id,
            {
                "step": "manual_rerun_result_collected",
                "rerun_id": active.rerun_id,
                "scope": active.scope,
                "result_path": result_path,
                "report_path": report_path,
                "citations_count": len(citations),
            },
        )
        self._save_checkpoint(
            updated,
            kind="deep_research_collected",
            node_anchor=f"deep_research:{active.expected_run_no}",
            source_attempt_no=active.expected_run_no,
            source_response_id=None,
            report_override=merged_report,
            snapshot_extra={
                "source": "manual_chatgpt_rerun",
                "rerun_id": active.rerun_id,
                "scope": active.scope,
                "result_path": result_path,
                "report_path": report_path,
                "citations_count": len(citations),
            },
        )
        return self.review_run(updated.id)

    def _record_deep_research_submit_failure(
        self,
        *,
        run_id: UUID,
        run_no: int,
        prompt: str,
        error: Exception,
        response_id: str | None,
    ) -> None:
        if response_id:
            self._cancel_submitted_response_after_persistence_failure(run_id, response_id)
            self.repository.append_history_event(
                run_id,
                {
                    "step": "deep_research_submit_persistence_failed",
                    "response_id": response_id,
                    "error": repr(error),
                },
            )
        status = "submitted_but_persistence_failed" if response_id else "failed_to_submit"
        try:
            self.repository.add_attempt(
                run_id,
                ResearchAttempt(
                    run_no=run_no,
                    response_id=response_id,
                    status=status,
                    model=self.azure.deep_research_deployment,
                    prompt=prompt,
                    error=repr(error),
                ),
            )
        except Exception as attempt_error:
            self.repository.append_history_event(
                run_id,
                {
                    "step": "deep_research_submit_failure_attempt_record_failed",
                    "response_id": response_id,
                    "error": repr(attempt_error),
                    "original_error": repr(error),
                },
            )

    def collect_deep_research(self, run_id: UUID) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if run.status not in {RunStatus.WAITING_DEEP_RESEARCH, RunStatus.COLLECTING}:
            return run

        if run.status == RunStatus.WAITING_DEEP_RESEARCH:
            claimed = self.repository.claim_deep_research_run(
                run.id,
                lease_seconds=self.settings.research_deep_research_collecting_stale_seconds,
            )
            if claimed is None:
                return self.repository.get_run(run.id)
            run = claimed

        if run.status != RunStatus.COLLECTING:
            return run

        response_id = run.pending_deep_research_response_id
        if not response_id:
            return self._enter_human_review(
                run.id,
                done_reason="missing_deep_research_response_id",
            )

        try:
            response = self.azure.retrieve_response(response_id)
        except Exception as error:
            self.repository.append_history_event(
                run.id,
                {
                    "step": "deep_research_retrieve_retryable_error",
                    "response_id": response_id,
                    "error": repr(error),
                },
            )
            latest_run = self.repository.get_run(run.id)
            if _is_terminal_run_status(latest_run.status):
                self.repository.append_history_event(
                    latest_run.id,
                    {
                        "step": "deep_research_retrieve_retry_ignored_terminal_run",
                        "status": latest_run.status.value,
                        "response_id": response_id,
                    },
                )
                return latest_run
            retried = self.repository.update_run_if_status(
                run.id,
                {RunStatus.COLLECTING},
                status=RunStatus.WAITING_DEEP_RESEARCH,
                deep_research_status="retrieve_retryable_error",
                review_claim_token=None,
                review_claim_operation=None,
                review_claim_expires_at=None,
            )
            return retried or self.repository.get_run(run.id)

        latest_run = self.repository.get_run(run.id)
        if _is_terminal_run_status(latest_run.status):
            self.repository.append_history_event(
                latest_run.id,
                {
                    "step": "deep_research_collect_ignored_terminal_run",
                    "status": latest_run.status.value,
                    "response_id": response_id,
                },
            )
            return latest_run
        if latest_run.status != RunStatus.COLLECTING:
            return latest_run
        run = latest_run

        response_status = get_response_status(response)
        if response_status in {"queued", "in_progress"}:
            waiting = self.repository.update_run_if_status(
                run.id,
                {RunStatus.COLLECTING},
                status=RunStatus.WAITING_DEEP_RESEARCH,
                deep_research_status=response_status,
                review_claim_token=None,
                review_claim_operation=None,
                review_claim_expires_at=None,
            )
            return waiting or self.repository.get_run(run.id)

        raw_response = response_to_jsonable(response)
        raw_path, _ = self.artifacts.save_json(
            run.id,
            f"raw-responses/deep_research_collect_{run.deep_research_runs:03d}.json",
            raw_response,
        )

        if response_status in TERMINAL_FAILURE_STATUSES:
            error_text = _extract_response_error(response)
            citations = extract_citations(response)
            tool_calls = extract_tool_calls(response)
            cost_event = build_cost_event(
                step="deep_research",
                model=self.azure.deep_research_deployment,
                response_id=response_id,
                response=raw_response,
                tool_calls=len(tool_calls),
                billable_tool_calls=count_billable_web_search_calls(tool_calls),
                input_cost_per_1m=self.settings.research_deep_research_input_cost_per_1m,
                output_cost_per_1m=self.settings.research_deep_research_output_cost_per_1m,
                tool_call_cost=self.settings.research_web_search_cost_per_call,
            )
            self.repository.add_attempt(
                run.id,
                ResearchAttempt(
                    run_no=run.deep_research_runs,
                    response_id=response_id,
                    status=response_status,
                    model=self.azure.deep_research_deployment,
                    prompt=run.optimized_prompt or run.user_prompt,
                    citations=citations,
                    tool_calls_summary=tool_calls,
                    error=error_text,
                    raw_response_artifact_path=raw_path,
                ),
            )
            cost_recorded = self.repository.add_cost_event(run.id, cost_event)
            tool_call_delta = len(tool_calls) if cost_recorded else 0
            cost_delta = cost_event.estimated_cost_usd if cost_recorded else 0.0
            return self._enter_human_review(
                run.id,
                done_reason=f"deep_research_{response_status}",
                allowed_statuses={RunStatus.COLLECTING},
                deep_research_status=response_status,
                total_tool_calls=run.total_tool_calls + tool_call_delta,
                estimated_cost_usd=run.estimated_cost_usd + cost_delta,
            )

        if response_status != "completed":
            return self._enter_human_review(
                run.id,
                done_reason="deep_research_unknown_status",
                allowed_statuses={RunStatus.COLLECTING},
                deep_research_status=response_status,
            )

        report = get_response_output_text(response)
        latest_plan = _latest_rerun_plan(self.repository.get_rerun_plans(run.id))
        lineage = self.repository.get_lineage(run.id)
        should_merge_seed_report = (
            lineage is not None
            and run.deep_research_runs == 1
            and bool((run.report or "").strip())
        )
        merge_error: str | None = None
        merged_report = report
        if should_merge_seed_report or run.deep_research_runs > 1 and (
            latest_plan is None or latest_plan.scope != "full_rerun"
        ):
            try:
                merged_report = _merge_targeted_research_delta(
                    existing_report=run.report or "",
                    delta=report,
                    run_no=run.deep_research_runs,
                )
            except RegressionError as error:
                merge_error = str(error)
                merged_report = run.report or ""
        citations = extract_citations(response)
        tool_calls = extract_tool_calls(response)
        cost_event = build_cost_event(
            step="deep_research",
            model=self.azure.deep_research_deployment,
            response_id=response_id,
            response=raw_response,
            tool_calls=len(tool_calls),
            billable_tool_calls=count_billable_web_search_calls(tool_calls),
            input_cost_per_1m=self.settings.research_deep_research_input_cost_per_1m,
            output_cost_per_1m=self.settings.research_deep_research_output_cost_per_1m,
            tool_call_cost=self.settings.research_web_search_cost_per_call,
        )
        self.repository.add_attempt(
            run.id,
            ResearchAttempt(
                run_no=run.deep_research_runs,
                response_id=response_id,
                status=response_status,
                model=self.azure.deep_research_deployment,
                prompt=run.optimized_prompt or run.user_prompt,
                report=report,
                citations=citations,
                tool_calls_summary=tool_calls,
                raw_response_artifact_path=raw_path,
            ),
        )
        cost_recorded = self.repository.add_cost_event(run.id, cost_event)
        tool_call_delta = len(tool_calls) if cost_recorded else 0
        cost_delta = cost_event.estimated_cost_usd if cost_recorded else 0.0
        if merge_error is not None:
            self.repository.append_history_event(
                run.id,
                {
                    "step": "targeted_rerun_merge_rejected",
                    "reason": merge_error,
                    "response_id": response_id,
                },
            )
            return self._enter_human_review(
                run.id,
                done_reason="targeted_rerun_merge_rejected",
                allowed_statuses={RunStatus.COLLECTING},
                deep_research_status=response_status,
                total_tool_calls=run.total_tool_calls + tool_call_delta,
                estimated_cost_usd=run.estimated_cost_usd + cost_delta,
            )
        report_path, _ = self.artifacts.save_text(
            run.id,
            f"reports/report_attempt_{run.deep_research_runs:03d}.md",
            merged_report,
        )
        self.repository.append_history_event(
            run.id,
            {
                "step": "deep_research_collected",
                "response_id": response_id,
                "citations_count": len(citations),
                "tool_calls_count": len(tool_calls),
                "estimated_cost_usd": cost_event.estimated_cost_usd,
                "report_path": report_path,
            },
        )
        self._save_checkpoint(
            run,
            kind="deep_research_collected",
            node_anchor=f"deep_research:{run.deep_research_runs}",
            source_attempt_no=run.deep_research_runs,
            source_response_id=response_id,
            report_override=merged_report,
            snapshot_extra={
                "report_path": report_path,
                "citations_count": len(citations),
                "tool_calls_count": len(tool_calls),
            },
        )
        updated = self.repository.update_run_if_status(
            run.id,
            {RunStatus.COLLECTING},
            status=(
                RunStatus.COMPLETED
                if getattr(run, "run_origin", "research") == "forecast"
                else RunStatus.REVIEWING
            ),
            report=merged_report,
            final_report=(
                merged_report
                if getattr(run, "run_origin", "research") == "forecast"
                else run.final_report
            ),
            done_reason=(
                "forecast_raw_report_collected"
                if getattr(run, "run_origin", "research") == "forecast"
                else run.done_reason
            ),
            terminal_status=(
                "forecast_raw_report_collected"
                if getattr(run, "run_origin", "research") == "forecast"
                else run.terminal_status
            ),
            deep_research_status=response_status,
            total_tool_calls=run.total_tool_calls + tool_call_delta,
            estimated_cost_usd=run.estimated_cost_usd + cost_delta,
            review_claim_token=None,
            review_claim_operation=None,
            review_claim_expires_at=None,
        )
        if updated is None:
            self.repository.append_history_event(
                run.id,
                {
                    "step": "deep_research_collect_update_skipped_status_changed",
                    "response_id": response_id,
                },
            )
            return self.repository.get_run(run.id)
        if getattr(run, "run_origin", "research") == "forecast":
            return updated
        return self.review_run(updated.id)

    def review_run(
        self,
        run_id: UUID,
        *,
        _review_claim_token: str | None = None,
    ) -> ResearchRunRecord:
        current = self.repository.get_run(run_id)
        if getattr(current, "run_origin", "research") == "forecast":
            return current
        if _review_claim_token is not None:
            return self._review_run_claimed(
                run_id,
                _review_claim_token=_review_claim_token,
            )

        claimed = self.repository.claim_review_operation(
            run_id,
            operation="review_run",
            lease_seconds=self.settings.research_review_timeout_seconds,
        )
        if claimed is None:
            return self.repository.get_run(run_id)

        run, claim_token = claimed
        try:
            return self._review_run_claimed(
                run.id,
                _review_claim_token=claim_token,
            )
        finally:
            self.repository.release_review_operation(run.id, claim_token=claim_token)

    def _review_run_claimed(
        self,
        run_id: UUID,
        *,
        _review_claim_token: str,
    ) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if run.status != RunStatus.REVIEWING:
            return run
        if not run.report:
            return self._enter_human_review(run.id, done_reason="missing_report_for_review")

        previous_reviews = self.repository.get_reviews(run.id)
        contract = self.repository.get_objective_contract(run.id)
        research_items = self.repository.get_research_items(run.id)
        acceptance_criteria = (
            [criterion.description for criterion in contract.acceptance_criteria]
            if contract is not None
            else _acceptance_criteria_from_history(self.repository.get_history(run.id))
        )
        try:
            review_result, response_id, raw_response = self._review_with_retry(
                run=run,
                acceptance_criteria=acceptance_criteria,
                research_items=research_items,
            )
            run = self.repository.get_run(run.id)
            if run.status != RunStatus.REVIEWING or run.needs_human_review:
                if raw_response:
                    self.artifacts.save_json(
                        run.id,
                        f"raw-responses/review_resp_ignored_{run.total_reviews + 1:03d}.json",
                        raw_response,
                    )
                    self._record_review_response_cost(
                        run_id=run.id,
                        response_id=response_id,
                        raw_response=raw_response,
                        step="review_ignored",
                    )
                self.repository.append_history_event(
                    run.id,
                    {
                        "step": "review_ignored_status_changed",
                        "status": run.status.value,
                        "needs_human_review": run.needs_human_review,
                        "response_id": response_id,
                    },
                )
                return run
        except ReviewResponseParseError as error:
            self.artifacts.save_json(
                run.id,
                f"raw-responses/review_resp_parse_failed_{run.total_reviews + 1:03d}.json",
                error.raw_response,
            )
            self.repository.append_history_event(
                run.id,
                {"step": "review_failed", "error": repr(error)},
            )
            return self._enter_human_review(
                run.id,
                done_reason="review_schema_or_request_failed",
                allowed_statuses={RunStatus.REVIEWING},
            )
        except ReviewRequestTimeout as error:
            self.repository.append_history_event(
                run.id,
                {"step": "review_failed", "error": repr(error)},
            )
            return self._enter_human_review(
                run.id,
                done_reason="review_timeout",
                allowed_statuses={RunStatus.REVIEWING},
            )
        except (ValueError, ValidationError, RuntimeError) as error:
            self.repository.append_history_event(
                run.id,
                {"step": "review_failed", "error": repr(error)},
            )
            return self._enter_human_review(
                run.id,
                done_reason="review_schema_or_request_failed",
                allowed_statuses={RunStatus.REVIEWING},
            )

        review_tool_calls = extract_tool_calls(raw_response)
        review_citations = extract_citations(raw_response)
        cost_event = build_cost_event(
            step="review",
            model=self.azure.reviewer_deployment,
            response_id=response_id,
            response=raw_response,
            tool_calls=len(review_tool_calls),
            billable_tool_calls=count_billable_web_search_calls(review_tool_calls),
            input_cost_per_1m=self.settings.research_reviewer_input_cost_per_1m,
            output_cost_per_1m=self.settings.research_reviewer_output_cost_per_1m,
            tool_call_cost=self.settings.research_web_search_cost_per_call,
        )

        if raw_response:
            self.artifacts.save_json(
                run.id,
                f"raw-responses/review_resp_{run.total_reviews + 1:03d}.json",
                raw_response,
            )
        self.repository.add_tool_calls(
            run.id,
            response_id=response_id,
            step="review",
            tool_calls=review_tool_calls,
        )
        self.repository.add_citations(run.id, review_citations)
        cost_recorded = self.repository.add_cost_event(run.id, cost_event)
        tool_call_delta = len(review_tool_calls) if cost_recorded else 0
        cost_delta = cost_event.estimated_cost_usd if cost_recorded else 0.0
        next_total_tool_calls = run.total_tool_calls + tool_call_delta
        next_estimated_cost = run.estimated_cost_usd + cost_delta

        review = ReviewRecord(
            **review_result.model_dump(),
            review_no=run.total_reviews + 1,
            recommended_route=review_result.verdict,
            reviewer_response_id=response_id,
            report_hash=report_hash(run.report),
        )
        unknown_item_ids = _unknown_item_assessment_ids(research_items, review)
        missing_item_ids = _missing_item_assessment_ids(research_items, review)
        if unknown_item_ids or missing_item_ids:
            self.repository.add_review(
                run_id=run.id,
                review=review,
                model=self.azure.reviewer_deployment,
            )
            self.repository.append_history_event(
                run.id,
                {
                    "step": (
                        "review_unknown_research_items"
                        if unknown_item_ids
                        else "review_missing_research_items"
                    ),
                    "unknown_item_ids": unknown_item_ids,
                    "missing_item_ids": missing_item_ids,
                    "known_item_ids": [item.item_id for item in research_items],
                    "assessed_item_ids": [
                        assessment.item_id for assessment in review.item_assessments
                    ],
                },
            )
            return self._enter_human_review(
                run.id,
                done_reason=(
                    "review_referenced_unknown_research_items"
                    if unknown_item_ids
                    else "review_missing_research_items"
                ),
                allowed_statuses={RunStatus.REVIEWING},
                total_reviews=run.total_reviews + 1,
                total_tool_calls=next_total_tool_calls,
                estimated_cost_usd=next_estimated_cost,
            )
        if review.item_assessments:
            self.repository.upsert_research_items(
                run.id,
                _items_from_review_assessments(
                    existing_items=research_items,
                    review=review,
                    attempt_no=run.deep_research_runs,
                ),
            )
        no_progress_count = compute_no_progress_count(
            previous_reviews=previous_reviews,
            current_review=review,
            current_no_progress_count=run.no_progress_count,
        )
        self.repository.add_review(
            run_id=run.id,
            review=review,
            model=self.azure.reviewer_deployment,
        )
        self._save_checkpoint(
            run,
            kind="review_recorded",
            node_anchor=f"review:{review.review_no}",
            source_attempt_no=run.deep_research_runs,
            source_review_no=review.review_no,
            source_response_id=response_id,
            snapshot_extra={
                "review": review.model_dump(mode="json"),
                "review_response_id": response_id,
            },
        )

        route_state: RouteState = {
            "review": review.model_dump(),
            "total_reviews": run.total_reviews + 1,
            "targeted_rerun_runs": run.targeted_rerun_runs,
            "full_rerun_runs": run.full_rerun_runs,
            "llm_patch_runs": run.llm_patch_runs,
            "verification_runs": run.verification_runs,
            "no_progress_count": no_progress_count,
            "max_total_iterations": run.max_total_iterations,
            "max_targeted_rerun_runs": run.max_targeted_rerun_runs,
            "max_full_rerun_runs": run.max_full_rerun_runs,
            "max_llm_patch_runs": run.max_llm_patch_runs,
            "max_verification_runs": run.max_verification_runs,
            "total_tool_calls": next_total_tool_calls,
            "max_total_tool_calls": run.max_total_tool_calls,
        }
        route_decision = route_after_review_decision(route_state)
        route = route_decision["selected_route"]

        self.repository.append_history_event(
            run.id,
            {
                "step": "route_after_review",
                "route": route,
                "candidate_route": route_decision["candidate_route"],
                "selected_route": route,
                "blocked_reason": route_decision["blocked_reason"],
                "dominant_actions": route_decision["dominant_actions"],
                "budget_snapshot": _route_budget_snapshot(route_state),
                "verdict": review.verdict.value,
                "total_reviews": run.total_reviews + 1,
                "no_progress_count": no_progress_count,
                "total_tool_calls": next_total_tool_calls,
                "estimated_cost_usd": next_estimated_cost,
                "reviewer_confidence": review.reviewer_confidence,
                "high_risk_flags": review.high_risk_flags,
            },
        )

        if route == "finalize":
            final_report = run.report or ""
            final_path, _ = self.artifacts.save_text(
                run.id,
                "reports/final_report.md",
                final_report,
            )
            self.repository.append_history_event(
                run.id,
                {"step": "finalized", "final_report_path": final_path},
            )
            self._save_checkpoint(
                run,
                kind="finalized",
                node_anchor="finalized",
                source_attempt_no=run.deep_research_runs,
                source_review_no=review.review_no,
                source_response_id=response_id,
                report_override=final_report,
                snapshot_extra={"final_report_path": final_path},
            )
            completed = self.repository.update_run_if_status(
                run.id,
                {RunStatus.REVIEWING},
                status=RunStatus.COMPLETED,
                final_report=final_report,
                done_reason="passed_review",
                terminal_status="completed_passed_review",
                total_reviews=run.total_reviews + 1,
                no_progress_count=no_progress_count,
                total_tool_calls=next_total_tool_calls,
                estimated_cost_usd=next_estimated_cost,
                needs_human_review=False,
            )
            if completed is None:
                self.repository.append_history_event(
                    run.id,
                    {"step": "finalize_update_skipped_status_changed"},
                )
                return self.repository.get_run(run.id)
            return completed

        if route == "human_review":
            done_reason = (
                route_decision["blocked_reason"]
                if route_decision["candidate_route"] != "human_review"
                and route_decision["blocked_reason"] is not None
                else _human_review_route_reason(review)
            )
            return self._enter_human_review(
                run.id,
                done_reason=done_reason,
                allowed_statuses={RunStatus.REVIEWING},
                total_reviews=run.total_reviews + 1,
                no_progress_count=no_progress_count,
                total_tool_calls=next_total_tool_calls,
                estimated_cost_usd=next_estimated_cost,
            )

        updated = self.repository.update_run_if_status(
            run.id,
            {RunStatus.REVIEWING},
            status=RunStatus.REVIEWING,
            needs_human_review=False,
            done_reason=None,
            total_reviews=run.total_reviews + 1,
            no_progress_count=no_progress_count,
            total_tool_calls=next_total_tool_calls,
            estimated_cost_usd=next_estimated_cost,
        )
        if updated is None:
            self.repository.append_history_event(
                run.id,
                {"step": "review_update_skipped_status_changed", "route": route},
            )
            return self.repository.get_run(run.id)
        if route == "llm_patch":
            return self.llm_finalize(
                updated.id,
                _review_claim_token=_review_claim_token,
            )
        if route == "build_targeted_rerun_plan":
            if self._should_use_manual_chatgpt_rerun(updated):
                return self._request_manual_chatgpt_rerun(updated.id)
            return self.submit_deep_research(updated.id)
        if route == "verify_items":
            return self.verify_items(
                updated.id,
                _review_claim_token=_review_claim_token,
            )
        if route == "full_rerun_submit":
            if self._should_use_manual_chatgpt_rerun(updated):
                return self._request_manual_chatgpt_rerun(
                    updated.id,
                    scope="full_rerun",
                )
            return self.submit_deep_research(updated.id, scope="full_rerun")
        if route == "finalize_with_limitation":
            return self._finalize_with_limitation(updated.id)
        if route == "revise_research_items":
            return self._enter_human_review(updated.id, done_reason="needs_item_revision")

        return self._enter_human_review(
            updated.id,
            done_reason=f"unknown_review_route_{route}",
        )

    def llm_finalize(
        self,
        run_id: UUID,
        *,
        human_comment: str | None = None,
        _review_claim_token: str | None = None,
    ) -> ResearchRunRecord:
        if _review_claim_token is not None:
            return self._llm_finalize_claimed(
                run_id,
                human_comment=human_comment,
                _review_claim_token=_review_claim_token,
            )

        claimed = self.repository.claim_review_operation(
            run_id,
            operation="llm_finalize",
            lease_seconds=self.settings.research_review_timeout_seconds,
        )
        if claimed is None:
            return self.repository.get_run(run_id)

        run, claim_token = claimed
        try:
            return self._llm_finalize_claimed(
                run.id,
                human_comment=human_comment,
                _review_claim_token=claim_token,
            )
        finally:
            self.repository.release_review_operation(run.id, claim_token=claim_token)

    def _llm_finalize_claimed(
        self,
        run_id: UUID,
        *,
        human_comment: str | None = None,
        _review_claim_token: str,
    ) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if run.status != RunStatus.REVIEWING:
            return run
        latest_review = self._latest_review(run.id)
        if not run.report:
            return self._enter_human_review(
                run.id,
                done_reason="missing_report_for_llm_finalize",
            )
        if latest_review is None:
            return self._enter_human_review(
                run.id,
                done_reason="missing_review_for_llm_finalize",
            )

        run_no = run.llm_patch_runs + 1
        prompt = self._build_llm_finalize_prompt(
            run=run,
            review=latest_review,
            human_comment=human_comment,
        )
        try:
            self.repository.append_history_event(
                run.id,
                {
                    "step": "llm_finalize_attempt_started",
                    "run_no": run_no,
                    "review_timeout_seconds": self.settings.research_review_timeout_seconds,
                },
            )
            try:
                revised_report, response_id, raw_response = self._call_llm_finalize(
                    prompt=prompt,
                    run=run,
                )
            except Exception as error:
                self.repository.append_history_event(
                    run.id,
                    {
                        "step": "llm_finalize_attempt_failed",
                        "run_no": run_no,
                        "error": repr(error),
                    },
                )
                raise
            else:
                self.repository.append_history_event(
                    run.id,
                    {
                        "step": "llm_finalize_attempt_completed",
                        "run_no": run_no,
                        "response_id": response_id,
                    },
                )
            latest_run = self.repository.get_run(run.id)
            if latest_run.status != RunStatus.REVIEWING or latest_run.needs_human_review:
                if raw_response:
                    self.artifacts.save_json(
                        run.id,
                        f"raw-responses/llm_finalize_resp_ignored_{run_no:03d}.json",
                        raw_response,
                    )
                    self._record_review_response_cost(
                        run_id=latest_run.id,
                        response_id=response_id,
                        raw_response=raw_response,
                        step="llm_finalize_ignored",
                    )
                self.repository.append_history_event(
                    latest_run.id,
                    {
                        "step": "llm_finalize_ignored_status_changed",
                        "status": latest_run.status.value,
                        "needs_human_review": latest_run.needs_human_review,
                        "response_id": response_id,
                    },
                )
                return latest_run
            run = latest_run
        except Exception as error:
            self.repository.append_history_event(
                run.id,
                {
                    "step": "llm_finalize_failed",
                    "run_no": run_no,
                    "error": repr(error),
                },
            )
            return self._enter_human_review(
                run.id,
                done_reason="llm_finalize_failed",
                allowed_statuses={RunStatus.REVIEWING},
            )

        llm_tool_calls = extract_tool_calls(raw_response)
        llm_citations = extract_citations(raw_response)
        cost_event = build_cost_event(
            step="llm_finalize",
            model=self.azure.reviewer_deployment,
            response_id=response_id,
            response=raw_response,
            tool_calls=len(llm_tool_calls),
            billable_tool_calls=count_billable_web_search_calls(llm_tool_calls),
            input_cost_per_1m=self.settings.research_reviewer_input_cost_per_1m,
            output_cost_per_1m=self.settings.research_reviewer_output_cost_per_1m,
            tool_call_cost=self.settings.research_web_search_cost_per_call,
        )

        if raw_response:
            self.artifacts.save_json(
                run.id,
                f"raw-responses/llm_finalize_resp_{run_no:03d}.json",
                raw_response,
            )
        self.repository.add_tool_calls(
            run.id,
            response_id=response_id,
            step="llm_finalize",
            tool_calls=llm_tool_calls,
        )
        self.repository.add_citations(run.id, llm_citations)
        cost_recorded = self.repository.add_cost_event(run.id, cost_event)
        tool_call_delta = len(llm_tool_calls) if cost_recorded else 0
        cost_delta = cost_event.estimated_cost_usd if cost_recorded else 0.0
        report_path, _ = self.artifacts.save_text(
            run.id,
            f"reports/llm_patch_{run_no:03d}.md",
            revised_report,
        )
        self.repository.append_history_event(
            run.id,
            {
                "step": "llm_patch",
                "run_no": run_no,
                "response_id": response_id,
                "tool_calls_count": len(llm_tool_calls),
                "estimated_cost_usd": cost_event.estimated_cost_usd,
                "report_path": report_path,
            },
        )
        self._save_checkpoint(
            run,
            kind="llm_patch_applied",
            node_anchor=f"llm_patch:{run_no}",
            source_attempt_no=run.deep_research_runs,
            source_review_no=latest_review.review_no,
            source_response_id=response_id,
            report_override=revised_report,
            snapshot_extra={
                "llm_patch_run_no": run_no,
                "report_path": report_path,
            },
        )
        updated = self.repository.update_run_if_status(
            run.id,
            {RunStatus.REVIEWING},
            status=RunStatus.REVIEWING,
            needs_human_review=False,
            report=revised_report,
            llm_patch_runs=run_no,
            total_tool_calls=run.total_tool_calls + tool_call_delta,
            estimated_cost_usd=run.estimated_cost_usd + cost_delta,
            done_reason=None,
        )
        if updated is None:
            self.repository.append_history_event(
                run.id,
                {
                    "step": "llm_finalize_update_skipped_status_changed",
                    "run_no": run_no,
                    "response_id": response_id,
                },
            )
            return self.repository.get_run(run.id)
        return self.review_run(updated.id, _review_claim_token=_review_claim_token)

    def list_human_reviews(self) -> list[HumanReviewQueueItem]:
        return [
            self._build_human_review_queue_item(run)
            for run in self.repository.list_human_review_runs()
        ]

    def get_human_review_payload(self, run_id: UUID) -> HumanReviewPayload:
        run = self.repository.get_run(run_id)
        if not _is_waiting_for_human_review(run):
            raise ValueError("Run is not waiting for human review.")
        latest_review = _visible_latest_review(
            run,
            latest_review=self._latest_review(run.id),
        )
        pending_manual_rerun = self.repository.get_active_manual_rerun_request(run.id)
        query_policy_blocked_actions = (
            {}
            if pending_manual_rerun
            else self._manual_rerun_query_policy_blocked_actions(run)
        )
        return HumanReviewPayload(
            run_id=run.id,
            reason=run.done_reason or (latest_review.rationale if latest_review else ""),
            latest_report=run.report or "",
            latest_review=latest_review,
            unresolved_items=[
                item
                for item in self.repository.get_research_items(run.id)
                if item.status
                in {
                    ItemStatus.NOT_STARTED,
                    ItemStatus.PARTIAL,
                    ItemStatus.UNANSWERED,
                    ItemStatus.UNVERIFIABLE,
                }
            ],
            allowed_actions=[]
            if pending_manual_rerun
            else _allowed_human_review_actions(
                run,
                query_policy_blocked_actions=query_policy_blocked_actions,
            ),
            action_states=_human_review_action_states(
                run,
                pending_manual_rerun=pending_manual_rerun is not None,
                query_policy_blocked_actions=query_policy_blocked_actions,
            ),
            route_summary=_human_review_route_summary(
                self.repository.get_history(run.id),
                latest_review=latest_review,
            ),
            audit_summary=self._human_review_audit_summary(run),
            warnings=run.warnings,
            pending_manual_rerun=pending_manual_rerun,
            suggested_rerun=None
            if pending_manual_rerun
            else self._build_suggested_rerun_prompt(run, latest_review=latest_review),
        )

    def get_cost_events(self, run_id: UUID) -> list[CostEvent]:
        return [
            event.model_copy(
                update={"estimated_cost_usd": self._estimate_cost_event(run_id, event)}
            )
            for event in self.repository.get_cost_events(run_id)
        ]

    def estimate_run_cost_usd(self, run_id: UUID, fallback: float = 0.0) -> float:
        cost_events = self.get_cost_events(run_id)
        if not cost_events:
            return fallback
        return sum(event.estimated_cost_usd for event in cost_events)

    def build_fork_preview(
        self,
        run_id: UUID,
        checkpoint_id: UUID,
        *,
        additional_prompt: str,
    ) -> ForkPreviewResponse:
        self.repository.get_run(run_id)
        checkpoint = self.repository.get_checkpoint(
            run_id,
            checkpoint_id,
            include_forks=True,
        )
        if not checkpoint.forkable:
            raise ValueError("Checkpoint is not forkable.")

        source_prompt = _checkpoint_source_prompt(checkpoint)
        source_report = _checkpoint_source_report(checkpoint)
        composed_prompt = _build_fork_deep_research_prompt(
            checkpoint=checkpoint,
            source_prompt=source_prompt,
            source_report=source_report,
            additional_prompt=additional_prompt,
        )
        query_policy = _deep_research_query_policy_decision(composed_prompt)
        warnings: list[str] = []
        if query_policy.status != "allowed":
            warnings.append(
                query_policy.blocked_reason or "Fork prompt was blocked by query policy."
            )
        if not source_report.strip():
            warnings.append("Source checkpoint has no report snapshot.")
        preview_hash = _fork_preview_hash(
            checkpoint_id=checkpoint.checkpoint_id,
            additional_prompt=additional_prompt,
            composed_prompt=composed_prompt,
            query_policy=query_policy,
        )
        return ForkPreviewResponse(
            run_id=run_id,
            checkpoint_id=checkpoint.checkpoint_id,
            composed_prompt=composed_prompt,
            query_policy=query_policy,
            policy_decision=query_policy,
            source_prompt_excerpt=_excerpt(source_prompt),
            source_report_excerpt=_excerpt(source_report),
            warnings=warnings,
            preview_hash=preview_hash,
        )

    def fork_from_checkpoint(
        self,
        run_id: UUID,
        checkpoint_id: UUID,
        *,
        additional_prompt: str,
        idempotency_key: str,
        confirmed_preview_hash: str,
    ) -> ForkSubmitResponse:
        parent = self.repository.get_run(run_id)
        checkpoint = self.repository.get_checkpoint(run_id, checkpoint_id, include_forks=True)
        if not checkpoint.forkable:
            raise ValueError("Checkpoint is not forkable.")

        existing = self.repository.get_lineage_by_fork_request(
            parent_run_id=parent.id,
            checkpoint_id=checkpoint.checkpoint_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            _validate_existing_fork_lineage(
                existing,
                additional_prompt=additional_prompt,
                confirmed_preview_hash=confirmed_preview_hash,
            )
            child = self.repository.get_run(existing.run_id)
            return _fork_submit_response(child, existing)

        preview = self.build_fork_preview(
            parent.id,
            checkpoint.checkpoint_id,
            additional_prompt=additional_prompt,
        )
        if preview.preview_hash != confirmed_preview_hash:
            raise RuntimeError("Fork preview hash does not match the current preview.")

        source_report = _checkpoint_source_report(checkpoint)
        root_run_id = self._root_run_id(parent.id)
        child, lineage, created = self.repository.create_fork_run(
            parent=parent,
            root_run_id=root_run_id,
            checkpoint=checkpoint,
            additional_prompt=additional_prompt,
            confirmed_preview_hash=confirmed_preview_hash,
            idempotency_key=idempotency_key,
            source_snapshot_json=checkpoint.snapshot_json,
            source_report_artifact_path=None,
            seed_report=source_report,
        )
        if not created:
            _validate_existing_fork_lineage(
                lineage,
                additional_prompt=additional_prompt,
                confirmed_preview_hash=confirmed_preview_hash,
            )
            return _fork_submit_response(child, lineage)

        try:
            source_report_artifact_path, _ = self.artifacts.save_text(
                child.id,
                "reports/fork_source_report.md",
                source_report,
            )
            self.artifacts.save_text(
                child.id,
                "prompts/fork_source_prompt.txt",
                _checkpoint_source_prompt(checkpoint),
            )
            lineage = self.repository.update_lineage_source_report_artifact_path(
                child.id,
                source_report_artifact_path,
            )

            contract = self.repository.get_objective_contract(parent.id)
            if contract is not None:
                self.repository.save_objective_contract(child.id, contract)
            research_items = self.repository.get_research_items(parent.id)
            if research_items:
                self.repository.upsert_research_items(child.id, research_items)
        except Exception as error:
            failed = self._mark_fork_initialization_failed(
                child,
                prompt=preview.composed_prompt,
                error=error,
            )
            lineage = self.repository.get_lineage(child.id) or lineage
            return _fork_submit_response(failed, lineage)

        if preview.query_policy.status != "allowed":
            blocked = self._enter_human_review(
                child.id,
                done_reason="fork_deep_research_blocked_by_query_policy",
            )
            lineage = self.repository.get_lineage(child.id) or lineage
            return _fork_submit_response(blocked, lineage)

        submitted = self._submit_fork_deep_research(
            child.id,
            prompt=preview.composed_prompt,
        )
        lineage = self.repository.get_lineage(child.id) or lineage
        return _fork_submit_response(submitted, lineage)

    def _root_run_id(self, run_id: UUID) -> UUID:
        lineage = self.repository.get_lineage(run_id)
        return lineage.root_run_id if lineage is not None else run_id

    def _submit_fork_deep_research(
        self,
        run_id: UUID,
        *,
        prompt: str,
    ) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        run_no = run.deep_research_runs + 1
        remaining_tool_calls = run.max_total_tool_calls - run.total_tool_calls
        if remaining_tool_calls <= 0:
            return self._enter_human_review(
                run.id,
                done_reason="max_total_tool_calls_reached_before_deep_research_submit",
            )
        response_id: str | None = None
        try:
            response = self.azure.submit_deep_research(
                prompt=prompt,
                max_tool_calls=min(
                    remaining_tool_calls,
                    self.settings.default_max_total_tool_calls,
                ),
                tool_profile="public",
                background=True,
                policy_decision_id=None,
            )
            response_id = get_response_id(response)
            raw_path, _ = self.artifacts.save_json(
                run.id,
                f"raw-responses/fork_deep_research_submit_{run_no:03d}.json",
                response_to_jsonable(response),
            )
            response_status = get_response_status(response) or "queued"
            self.repository.add_attempt(
                run.id,
                ResearchAttempt(
                    run_no=run_no,
                    response_id=response_id,
                    status=response_status,
                    model=self.azure.deep_research_deployment,
                    prompt=prompt,
                    raw_response_artifact_path=raw_path,
                ),
            )
            updated = self.repository.update_run_if_status(
                run.id,
                {RunStatus.QUEUED, RunStatus.REVIEWING, RunStatus.SUBMITTED},
                optimized_prompt=prompt,
                status=RunStatus.WAITING_DEEP_RESEARCH,
                needs_human_review=False,
                pending_deep_research_response_id=response_id,
                deep_research_status=response_status,
                deep_research_runs=run_no,
                done_reason=None,
            )
            if updated is None:
                self._cancel_submitted_response_after_status_change(run.id, response_id)
                return self.repository.get_run(run.id)
            return updated
        except Exception as error:
            self._record_deep_research_submit_failure(
                run_id=run.id,
                run_no=run_no,
                prompt=prompt,
                error=error,
                response_id=response_id,
            )
            done_reason = (
                "fork_deep_research_submitted_but_persistence_failed"
                if response_id
                else "fork_deep_research_submit_failed"
            )
            return self._enter_human_review(
                run.id,
                done_reason=done_reason,
                deep_research_runs=run_no,
            )

    def _mark_fork_initialization_failed(
        self,
        child: ResearchRunRecord,
        *,
        prompt: str,
        error: Exception,
    ) -> ResearchRunRecord:
        run_no = child.deep_research_runs + 1
        self.repository.add_attempt(
            child.id,
            ResearchAttempt(
                run_no=run_no,
                response_id=None,
                status="failed_to_initialize",
                model=self.azure.deep_research_deployment,
                prompt=prompt,
                error=repr(error),
            ),
        )
        self.repository.append_history_event(
            child.id,
            {
                "step": "fork_initialization_failed",
                "run_no": run_no,
                "error": repr(error),
            },
        )
        return self._enter_human_review(
            child.id,
            done_reason="fork_deep_research_initialization_failed",
            deep_research_runs=run_no,
        )

    def _save_checkpoint(
        self,
        run: ResearchRunRecord,
        *,
        kind: str,
        node_anchor: str,
        source_attempt_no: int | None = None,
        source_review_no: int | None = None,
        source_response_id: str | None = None,
        report_override: str | None = None,
        snapshot_extra: dict[str, Any] | None = None,
        forkable: bool = True,
    ) -> ResearchCheckpoint:
        source_prompt = run.optimized_prompt or run.user_prompt
        source_report = report_override if report_override is not None else (
            run.final_report or run.report or ""
        )
        checkpoint_report_hash = report_hash(source_report) if source_report else None
        dedupe_parts = [
            kind,
            str(source_attempt_no or ""),
            str(source_review_no or ""),
            source_response_id or "",
            checkpoint_report_hash or "",
        ]
        snapshot = {
            "kind": kind,
            "status": run.status.value,
            "source_prompt": source_prompt,
            "source_report": source_report,
            "source_report_excerpt": _excerpt(source_report),
            "source_prompt_excerpt": _excerpt(source_prompt),
            "source_attempt_no": source_attempt_no,
            "source_review_no": source_review_no,
            "source_response_id": source_response_id,
        }
        if snapshot_extra:
            snapshot.update(snapshot_extra)
        return self.repository.add_checkpoint(
            run.id,
            kind=kind,
            node_anchor=node_anchor,
            forkable=forkable and bool(source_report.strip()),
            dedupe_key=":".join(dedupe_parts),
            source_attempt_no=source_attempt_no,
            source_review_no=source_review_no,
            source_response_id=source_response_id,
            checkpoint_report_hash=checkpoint_report_hash,
            snapshot_json=snapshot,
        )

    def verify_items(
        self,
        run_id: UUID,
        *,
        _review_claim_token: str | None = None,
    ) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        latest_review = self._latest_review(run.id)
        target_items = _target_item_ids_for_action(
            latest_review,
            {RecommendedAction.VERIFY},
        )
        research_items = self.repository.get_research_items(run.id)
        item_by_id = {item.item_id: item for item in research_items}
        target_research_items = [
            item_by_id[item_id] for item_id in target_items if item_id in item_by_id
        ]
        if not target_research_items:
            return self._enter_human_review(
                run.id,
                done_reason="verification_missing_target_items",
                allowed_statuses={RunStatus.REVIEWING},
            )

        raw_queries = [
            _verification_query_for_item(item, latest_review)
            for item in target_research_items
        ]
        policy_decision = query_policy_gate(
            {
                "candidate_queries": raw_queries,
                "contains_sensitive_terms": any(
                    contains_sensitive_terms(query) for query in raw_queries
                ),
            },
            {},
        )
        for index, (item, raw_query) in enumerate(
            zip(target_research_items, raw_queries, strict=True)
        ):
            safe_query = (
                policy_decision.safe_queries[index]
                if index < len(policy_decision.safe_queries)
                and policy_decision.status == "allowed"
                else None
            )
            self.repository.add_verification_query(
                run.id,
                VerificationQuery(
                    item_id=item.item_id,
                    raw_query=raw_query,
                    safe_query=safe_query,
                    policy_status=policy_decision.status,
                    blocked_reason=policy_decision.blocked_reason,
                ),
            )

        if policy_decision.status != "allowed":
            self.repository.append_history_event(
                run.id,
                {
                    "step": "verification_blocked",
                    "reason": policy_decision.blocked_reason,
                },
            )
            return self._enter_human_review(
                run.id,
                done_reason="verification_blocked_by_query_policy",
                allowed_statuses={RunStatus.REVIEWING},
            )

        run_no = run.verification_runs + 1
        prompt = _build_verification_prompt(
            run=run,
            items=target_research_items,
            safe_queries=policy_decision.safe_queries,
            latest_review=latest_review,
        )
        try:
            verification_delta, response_id, raw_response = self._call_verification(
                run=run,
                prompt=prompt,
            )
        except Exception as error:
            self.repository.append_history_event(
                run.id,
                {
                    "step": "verification_failed",
                    "run_no": run_no,
                    "error": repr(error),
                },
            )
            return self._enter_human_review(
                run.id,
                done_reason="verification_failed",
                allowed_statuses={RunStatus.REVIEWING},
            )

        verification_tool_calls = extract_tool_calls(raw_response)
        verification_citations = extract_citations(raw_response)
        cost_event = build_cost_event(
            step="verification",
            model=self.azure.reviewer_deployment,
            response_id=response_id,
            response=raw_response,
            tool_calls=len(verification_tool_calls),
            billable_tool_calls=count_billable_web_search_calls(verification_tool_calls),
            input_cost_per_1m=self.settings.research_reviewer_input_cost_per_1m,
            output_cost_per_1m=self.settings.research_reviewer_output_cost_per_1m,
            tool_call_cost=self.settings.research_web_search_cost_per_call,
        )
        if raw_response:
            self.artifacts.save_json(
                run.id,
                f"raw-responses/verification_resp_{run_no:03d}.json",
                raw_response,
            )
        self.repository.add_tool_calls(
            run.id,
            response_id=response_id,
            step="verification",
            tool_calls=verification_tool_calls,
        )
        self.repository.add_citations(run.id, verification_citations)
        cost_recorded = self.repository.add_cost_event(run.id, cost_event)
        tool_call_delta = len(verification_tool_calls) if cost_recorded else 0
        cost_delta = cost_event.estimated_cost_usd if cost_recorded else 0.0
        patched_report = _deterministic_merge_delta(
            run.report or "",
            verification_delta,
            run_no=run_no,
            heading="Targeted Verification Notes",
        )
        updated = self.repository.update_run_if_status(
            run.id,
            {RunStatus.REVIEWING},
            report=patched_report,
            verification_runs=run_no,
            total_tool_calls=run.total_tool_calls + tool_call_delta,
            estimated_cost_usd=run.estimated_cost_usd + cost_delta,
            done_reason=None,
        )
        if updated is None:
            return self.repository.get_run(run.id)
        self.repository.append_history_event(
            run.id,
            {
                "step": "verification_completed",
                "target_item_ids": target_items,
                "response_id": response_id,
                "tool_calls_count": len(verification_tool_calls),
            },
        )
        self._save_checkpoint(
            updated,
            kind="verification_completed",
            node_anchor=f"verification:{run_no}",
            source_attempt_no=updated.deep_research_runs,
            source_review_no=latest_review.review_no if latest_review else None,
            source_response_id=response_id,
            report_override=patched_report,
            snapshot_extra={
                "verification_run_no": run_no,
                "target_item_ids": target_items,
            },
        )
        return self.review_run(updated.id, _review_claim_token=_review_claim_token)

    def _finalize_with_limitation(self, run_id: UUID) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        items = self.repository.get_research_items(run.id)
        blocked_reason = _limitation_finalize_blocked_reason(items)
        if blocked_reason is not None:
            self.repository.append_history_event(
                run.id,
                {
                    "step": "finalize_with_limitation_blocked",
                    "reason": blocked_reason,
                },
            )
            return self._enter_human_review(
                run.id,
                done_reason=blocked_reason,
                allowed_statuses={RunStatus.REVIEWING},
            )
        unresolved = [
            item
            for item in items
            if item.status
            in {
                ItemStatus.NOT_STARTED,
                ItemStatus.PARTIAL,
                ItemStatus.UNANSWERED,
                ItemStatus.UNVERIFIABLE,
            }
        ]
        limitation_lines = [
            f"- {item.item_id}: {item.unresolved_reason or item.question}"
            for item in unresolved
            if item.severity != "blocker"
        ]
        final_report = _deterministic_merge_delta(
            run.report or "",
            "\n".join(limitation_lines) or "No material unresolved limitations.",
            run_no=run.total_reviews + 1,
            heading="Limitations",
        )
        final_path, _ = self.artifacts.save_text(
            run.id,
            "reports/final_report.md",
            final_report,
        )
        self.repository.append_history_event(
            run.id,
            {
                "step": "finalized_with_limitation",
                "final_report_path": final_path,
                "unresolved_item_ids": [item.item_id for item in unresolved],
            },
        )
        self._save_checkpoint(
            run,
            kind="finalized",
            node_anchor="finalized_with_limitation",
            source_attempt_no=run.deep_research_runs,
            source_review_no=run.total_reviews or None,
            report_override=final_report,
            snapshot_extra={
                "final_report_path": final_path,
                "unresolved_item_ids": [item.item_id for item in unresolved],
            },
        )
        completed = self.repository.update_run_if_status(
            run.id,
            {RunStatus.REVIEWING},
            status=RunStatus.COMPLETED,
            final_report=final_report,
            report=final_report,
            done_reason="completed_with_limitations",
            terminal_status="completed_with_limitations",
            needs_human_review=False,
        )
        return completed or self.repository.get_run(run.id)

    def resume_run(
        self,
        run_id: UUID,
        request: HumanReviewResumeRequest,
    ) -> ResearchRunRecord:
        pending_run = self.repository.get_run(run_id)
        if not _is_waiting_for_human_review(pending_run):
            raise ValueError("Run is not waiting for human review.")
        if self.repository.get_active_manual_rerun_request(pending_run.id) is not None:
            raise ValueError(
                "Run has a pending manual ChatGPT rerun result; "
                "upload the result before other actions."
            )

        blocked_reason = _blocked_human_resume_reason(pending_run, request.action)
        if blocked_reason is not None:
            self.repository.append_history_event(
                pending_run.id,
                {
                    "step": "human_review_resume_blocked",
                    "action": request.action.value,
                    "reason": blocked_reason,
                    "comment": request.comment,
                },
            )
            raise ValueError(
                f"Human review action {request.action.value!r} is blocked by {blocked_reason}."
            )

        query_policy_decision = self._manual_rerun_query_policy_decision(
            pending_run,
            action=request.action,
            human_comment=request.comment,
        )
        if (
            query_policy_decision is not None
            and query_policy_decision.status != "allowed"
        ):
            self.repository.append_history_event(
                pending_run.id,
                {
                    "step": "human_review_resume_blocked",
                    "action": request.action.value,
                    "reason": MANUAL_RERUN_PROMPT_BLOCKED_REASON,
                    "comment": request.comment,
                    "policy_status": query_policy_decision.status,
                    "policy_reason": query_policy_decision.blocked_reason,
                },
            )
            raise ValueError(
                f"Human review action {request.action.value!r} is blocked by "
                f"{MANUAL_RERUN_PROMPT_BLOCKED_REASON}."
            )

        claimed = self.repository.claim_human_review_decision(
            run_id,
            action=request.action,
            comment=request.comment,
            reviewer_id=None,
        )
        if claimed is None:
            raise ValueError("Run is not waiting for human review.")
        run, _decision = claimed

        if request.action in {
            HumanReviewAction.APPROVE,
            HumanReviewAction.APPROVE_WITH_LIMITATION,
        }:
            if not run.report:
                failed = self.repository.update_run_if_status(
                    run.id,
                    {RunStatus.REVIEWING},
                    status=RunStatus.FAILED,
                    needs_human_review=False,
                    done_reason="human_approved_without_report",
                )
                return failed or self.repository.get_run(run.id)
            final_path, _ = self.artifacts.save_text(
                run.id,
                "reports/final_report.md",
                run.report,
            )
            self.repository.append_history_event(
                run.id,
                {"step": "finalized_by_human", "final_report_path": final_path},
            )
            self._save_checkpoint(
                run,
                kind="finalized",
                node_anchor="finalized_by_human",
                source_attempt_no=run.deep_research_runs,
                source_review_no=run.total_reviews or None,
                report_override=run.report,
                snapshot_extra={"final_report_path": final_path},
            )
            completed = self.repository.update_run_if_status(
                run.id,
                {RunStatus.REVIEWING},
                status=RunStatus.COMPLETED,
                final_report=run.report,
                done_reason=(
                    "human_approved_with_limitation"
                    if request.action == HumanReviewAction.APPROVE_WITH_LIMITATION
                    else "human_approved"
                ),
                terminal_status=(
                    "completed_with_limitations"
                    if request.action == HumanReviewAction.APPROVE_WITH_LIMITATION
                    else "completed_by_human_approval"
                ),
                needs_human_review=False,
            )
            return completed or self.repository.get_run(run.id)

        if request.action == HumanReviewAction.REQUEST_REVIEW:
            self.repository.append_history_event(
                run.id,
                {
                    "step": "review_retry_requested_by_human",
                    "comment": request.comment,
                },
            )
            return self.review_run(run.id)

        if request.action == HumanReviewAction.REQUEST_LLM_PATCH:
            return self.llm_finalize(run.id, human_comment=request.comment)

        if request.action == HumanReviewAction.REQUEST_TARGETED_RERUN:
            if self._should_use_manual_chatgpt_rerun(run):
                return self._request_manual_chatgpt_rerun(
                    run.id,
                    human_comment=request.comment,
                )
            return self.submit_deep_research(run.id, human_comment=request.comment)

        if request.action == HumanReviewAction.REQUEST_FULL_RERUN:
            if self._should_use_manual_chatgpt_rerun(run):
                return self._request_manual_chatgpt_rerun(
                    run.id,
                    human_comment=request.comment,
                    scope="full_rerun",
                )
            return self.submit_deep_research(
                run.id,
                human_comment=request.comment,
                scope="full_rerun",
            )

        if request.action == HumanReviewAction.REQUEST_MANUAL_TARGETED_RERUN:
            return self._request_manual_chatgpt_rerun(
                run.id,
                human_comment=request.comment,
            )

        if request.action == HumanReviewAction.REQUEST_MANUAL_FULL_RERUN:
            return self._request_manual_chatgpt_rerun(
                run.id,
                human_comment=request.comment,
                scope="full_rerun",
            )

        if request.action == HumanReviewAction.REQUEST_VERIFICATION:
            return self.verify_items(run.id)

        if request.action == HumanReviewAction.REQUEST_ITEM_REVISION:
            return self._enter_human_review(
                run.id,
                done_reason="item_revision_requires_manual_edit",
            )

        warnings = list(run.warnings)
        if request.comment:
            warnings.append(f"Human reviewer rejected the run: {request.comment}")
        else:
            warnings.append("Human reviewer rejected the run.")
        rejected = self.repository.update_run_if_status(
            run.id,
            {RunStatus.REVIEWING},
            status=RunStatus.FAILED,
            needs_human_review=False,
            done_reason="human_rejected",
            warnings=warnings,
        )
        return rejected or self.repository.get_run(run.id)

    def mark_timeout(self, run_id: UUID) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if run.status not in {RunStatus.WAITING_DEEP_RESEARCH, RunStatus.COLLECTING}:
            return run

        if run.status == RunStatus.WAITING_DEEP_RESEARCH:
            claimed = self.repository.claim_deep_research_run(
                run.id,
                lease_seconds=self.settings.research_deep_research_timeout_seconds,
            )
            if claimed is None:
                return self.repository.get_run(run.id)
            run = claimed

        if run.status != RunStatus.COLLECTING:
            return run

        if not self._cancel_remote_response(
            run,
            history_step_prefix="timeout_remote_cancel",
        ):
            raise RuntimeError(
                "Remote Deep Research cancel failed; run was not marked timed out."
            )
        self.repository.add_attempt(
            run.id,
            ResearchAttempt(
                run_no=run.deep_research_runs,
                response_id=run.pending_deep_research_response_id,
                status="timeout",
                model=self.azure.deep_research_deployment,
                prompt=run.optimized_prompt or run.user_prompt,
                error="Deep Research polling timed out.",
            ),
        )
        return self._enter_human_review(
            run.id,
            done_reason="deep_research_timeout",
            allowed_statuses={RunStatus.COLLECTING},
            deep_research_status="timeout",
        )

    def mark_review_timeout(self, run_id: UUID) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if run.status != RunStatus.REVIEWING:
            return run
        if run.review_claim_expires_at is not None and run.review_claim_expires_at > utc_now():
            self.repository.append_history_event(
                run.id,
                {
                    "step": "review_timeout_skipped_active_operation",
                    "timeout_seconds": self.settings.research_review_timeout_seconds,
                    "total_reviews": run.total_reviews,
                    "claim_operation": run.review_claim_operation,
                    "claim_expires_at": run.review_claim_expires_at.isoformat(),
                },
            )
            return run

        self.repository.append_history_event(
            run.id,
            {
                "step": "review_timeout",
                "timeout_seconds": self.settings.research_review_timeout_seconds,
                "total_reviews": run.total_reviews,
            },
        )
        return self._enter_human_review(
            run.id,
            done_reason="review_timeout",
            allowed_statuses={RunStatus.REVIEWING},
        )

    def cancel_run(self, run_id: UUID) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if _is_terminal_run_status(run.status):
            self.repository.append_history_event(
                run.id,
                {
                    "step": "cancel_ignored_terminal_run",
                    "status": run.status.value,
                },
            )
            return run

        should_cancel_remote = run.status in {
            RunStatus.WAITING_DEEP_RESEARCH,
            RunStatus.COLLECTING,
        }
        if should_cancel_remote and not self._cancel_remote_response(
            run,
            history_step_prefix="cancel_remote",
        ):
            raise RuntimeError("Remote Deep Research cancel failed; run was not cancelled.")

        updated = self.repository.update_run_if_status(
            run.id,
            NON_TERMINAL_RUN_STATUSES,
            status=RunStatus.CANCELLED,
            done_reason="cancelled_by_user",
            needs_human_review=False,
        )
        if updated is None:
            latest = self.repository.get_run(run.id)
            self.repository.append_history_event(
                run.id,
                {
                    "step": "cancel_ignored_status_changed",
                    "status": latest.status.value,
                },
            )
            return latest

        return updated

    def delete_run(self, run_id: UUID) -> None:
        run = self.repository.get_run(run_id)
        if self.repository.is_linked_to_forecast(run.id):
            raise RuntimeError("forecast_linked_research_run")
        if not _is_terminal_run_status(run.status) and _has_pending_remote_deep_research(run):
            if not self._cancel_remote_response(
                run,
                history_step_prefix="delete_remote_cancel",
            ):
                raise RuntimeError("Remote Deep Research cancel failed; run was not deleted.")
        try:
            self.artifacts.delete_run(run.id)
        except Exception as error:
            raise RuntimeError("Artifact cleanup failed; run was not deleted.") from error
        deleted = self.repository.delete_run(run.id)
        if not deleted:
            raise KeyError(str(run.id))

    def _cancel_remote_response(
        self,
        run: ResearchRunRecord,
        *,
        history_step_prefix: str,
    ) -> bool:
        response_id = run.pending_deep_research_response_id
        if not response_id:
            self.repository.append_history_event(
                run.id,
                {
                    "step": f"{history_step_prefix}_skipped",
                    "reason": "missing_response_id",
                },
            )
            return True

        try:
            self.azure.cancel_response(response_id)
        except Exception as error:
            self.repository.append_history_event(
                run.id,
                {
                    "step": f"{history_step_prefix}_failed",
                    "response_id": response_id,
                    "error": repr(error),
                },
            )
            return False

        self.repository.append_history_event(
            run.id,
            {
                "step": f"{history_step_prefix}_succeeded",
                "response_id": response_id,
            },
        )
        if history_step_prefix == "delete_remote_cancel":
            self.repository.append_history_event(
                run.id,
                {
                    "step": "delete_remote_cancel_confirmed",
                    "response_id": response_id,
                },
            )
        return True

    def _cancel_submitted_response_after_status_change(
        self,
        run_id: UUID,
        response_id: str | None,
    ) -> None:
        if not response_id:
            return
        try:
            self.azure.cancel_response(response_id)
        except Exception as error:
            self.repository.append_history_event(
                run_id,
                {
                    "step": "deep_research_submit_status_changed_remote_cancel_failed",
                    "response_id": response_id,
                    "error": repr(error),
                },
            )
            return
        self.repository.append_history_event(
            run_id,
            {
                "step": "deep_research_submit_status_changed_remote_cancel_succeeded",
                "response_id": response_id,
            },
        )

    def _cancel_submitted_response_after_persistence_failure(
        self,
        run_id: UUID,
        response_id: str,
    ) -> None:
        try:
            self.azure.cancel_response(response_id)
        except Exception as error:
            self.repository.append_history_event(
                run_id,
                {
                    "step": "deep_research_submit_persistence_remote_cancel_failed",
                    "response_id": response_id,
                    "error": repr(error),
                },
            )
            return
        self.repository.append_history_event(
            run_id,
            {
                "step": "deep_research_submit_persistence_remote_cancel_succeeded",
                "response_id": response_id,
            },
        )

    def _review_with_retry(
        self,
        *,
        run: ResearchRunRecord,
        acceptance_criteria: list[str],
        research_items: list[ResearchItem],
    ) -> tuple[Any, str | None, dict[str, Any]]:
        last_error: Exception | None = None
        all_citations = [
            citation.model_dump() for citation in self.repository.get_citations(run.id)
        ]
        citations = all_citations[: self.settings.research_review_max_citations]
        omitted_report_chars = max(
            len(run.report or "") - self.settings.research_review_max_report_chars,
            0,
        )
        omitted_citations = max(
            len(all_citations) - self.settings.research_review_max_citations,
            0,
        )
        if omitted_report_chars or omitted_citations:
            self.repository.append_history_event(
                run.id,
                {
                    "step": "review_context_bounded",
                    "omitted_report_chars": omitted_report_chars,
                    "omitted_citation_count": omitted_citations,
                    "max_report_chars": self.settings.research_review_max_report_chars,
                    "max_citations": self.settings.research_review_max_citations,
                },
            )
        for attempt_no in range(2):
            self.repository.append_history_event(
                run.id,
                {
                    "step": "review_attempt_started",
                    "attempt_no": attempt_no + 1,
                    "review_timeout_seconds": self.settings.research_review_timeout_seconds,
                    "report_chars": len(run.report or ""),
                    "user_prompt_chars": len(run.user_prompt),
                    "optimized_prompt_chars": len(run.optimized_prompt or run.user_prompt),
                    "citations_count": len(citations),
                    "max_report_chars": self.settings.research_review_max_report_chars,
                    "max_citations": self.settings.research_review_max_citations,
                    "web_search_enabled": self.settings.research_review_web_search_enabled,
                },
            )
            try:
                result = self.azure.review_report(
                    user_prompt=run.user_prompt,
                    optimized_prompt=run.optimized_prompt or run.user_prompt,
                    acceptance_criteria=acceptance_criteria,
                    research_items=[item.model_dump(mode="json") for item in research_items],
                    report=run.report or "",
                    citations=citations,
                )
                self.repository.append_history_event(
                    run.id,
                    {
                        "step": "review_attempt_completed",
                        "attempt_no": attempt_no + 1,
                        "response_id": result[1],
                    },
                )
                return result
            except ReviewRequestTimeout as error:
                self.repository.append_history_event(
                    run.id,
                    {
                        "step": "review_attempt_failed",
                        "attempt_no": attempt_no + 1,
                        "error": repr(error),
                    },
                )
                raise
            except ReviewResponseParseError as error:
                last_error = error
                raw_response = error.raw_response
                response_id = str(raw_response.get("id") or "")
                artifact_name = (
                    f"raw-responses/review_resp_parse_failed_"
                    f"{run.total_reviews + 1:03d}_{attempt_no + 1}.json"
                )
                self.artifacts.save_json(
                    run.id,
                    artifact_name,
                    raw_response,
                )
                self._record_review_response_cost(
                    run_id=run.id,
                    response_id=response_id,
                    raw_response=raw_response,
                    step="review_failed",
                )
                self.repository.append_history_event(
                    run.id,
                    {
                        "step": "review_attempt_failed",
                        "attempt_no": attempt_no + 1,
                        "error": repr(error),
                    },
                )
            except (ValueError, ValidationError, RuntimeError) as error:
                last_error = error
                self.repository.append_history_event(
                    run.id,
                    {
                        "step": "review_attempt_failed",
                        "attempt_no": attempt_no + 1,
                        "error": repr(error),
                    },
                )

        if last_error is None:
            raise RuntimeError("Review failed without an exception.")
        raise last_error

    def _record_review_response_cost(
        self,
        *,
        run_id: UUID,
        response_id: str | None,
        raw_response: dict[str, Any],
        step: str,
    ) -> None:
        tool_calls = extract_tool_calls(raw_response)
        citations = extract_citations(raw_response)
        cost_event = build_cost_event(
            step=step,
            model=self.azure.reviewer_deployment,
            response_id=response_id,
            response=raw_response,
            tool_calls=len(tool_calls),
            billable_tool_calls=count_billable_web_search_calls(tool_calls),
            input_cost_per_1m=self.settings.research_reviewer_input_cost_per_1m,
            output_cost_per_1m=self.settings.research_reviewer_output_cost_per_1m,
            tool_call_cost=self.settings.research_web_search_cost_per_call,
        )
        self.repository.add_tool_calls(
            run_id,
            response_id=response_id,
            step=step,
            tool_calls=tool_calls,
        )
        self.repository.add_citations(run_id, citations)
        if not self.repository.add_cost_event(run_id, cost_event):
            return

        run = self.repository.get_run(run_id)
        self.repository.update_run(
            run_id,
            total_tool_calls=run.total_tool_calls + len(tool_calls),
            estimated_cost_usd=run.estimated_cost_usd + cost_event.estimated_cost_usd,
        )

    def _enter_human_review(
        self,
        run_id: UUID,
        *,
        done_reason: str,
        allowed_statuses: set[RunStatus] | None = None,
        **fields: Any,
    ) -> ResearchRunRecord:
        transition_statuses = allowed_statuses or NON_TERMINAL_RUN_STATUSES
        updated = self.repository.update_run_if_status(
            run_id,
            transition_statuses,
            status=RunStatus.NEEDS_HUMAN_REVIEW,
            needs_human_review=True,
            done_reason=done_reason,
            review_claim_token=None,
            review_claim_operation=None,
            review_claim_expires_at=None,
            **fields,
        )
        if updated is None:
            latest = self.repository.get_run(run_id)
            self.repository.append_history_event(
                run_id,
                {
                    "step": "human_review_update_skipped_status_changed",
                    "reason": done_reason,
                    "status": latest.status.value,
                },
            )
            return latest
        latest_review = self._latest_review(updated.id)
        self.repository.append_history_event(
            updated.id,
            {
                "step": "human_review_required",
                "reason": done_reason,
                "latest_review_no": latest_review.review_no if latest_review else None,
                "latest_verdict": latest_review.verdict.value if latest_review else None,
                "audit_summary": self._human_review_audit_summary(updated).model_dump(),
            },
        )
        self._save_checkpoint(
            updated,
            kind="human_review_required",
            node_anchor=(
                f"human_review:{latest_review.review_no}"
                if latest_review
                else "human_review"
            ),
            source_attempt_no=updated.deep_research_runs or None,
            source_review_no=latest_review.review_no if latest_review else None,
            source_response_id=updated.pending_deep_research_response_id,
            snapshot_extra={
                "reason": done_reason,
                "audit_summary": self._human_review_audit_summary(updated).model_dump(),
            },
        )
        return updated

    def _human_review_audit_summary(
        self,
        run: ResearchRunRecord,
    ) -> HumanReviewAuditSummary:
        return HumanReviewAuditSummary(
            deep_research_runs=run.deep_research_runs,
            targeted_rerun_runs=run.targeted_rerun_runs,
            full_rerun_runs=run.full_rerun_runs,
            llm_patch_runs=run.llm_patch_runs,
            verification_runs=run.verification_runs,
            total_reviews=run.total_reviews,
            no_progress_count=run.no_progress_count,
            total_tool_calls=run.total_tool_calls,
            estimated_cost_usd=self.estimate_run_cost_usd(
                run.id,
                fallback=run.estimated_cost_usd,
            ),
        )

    def _estimate_cost_event(self, run_id: UUID, event: CostEvent) -> float:
        if event.step == "deep_research":
            input_cost_per_1m = self.settings.research_deep_research_input_cost_per_1m
            output_cost_per_1m = self.settings.research_deep_research_output_cost_per_1m
        else:
            input_cost_per_1m = self.settings.research_reviewer_input_cost_per_1m
            output_cost_per_1m = self.settings.research_reviewer_output_cost_per_1m
        billable_tool_calls = self.repository.count_billable_web_search_tool_calls(
            run_id,
            step=event.step,
            response_id=event.response_id,
        )
        return estimate_usage_cost_usd(
            model=event.model,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            billable_tool_calls=(
                event.tool_calls if billable_tool_calls is None else billable_tool_calls
            ),
            input_cost_per_1m=input_cost_per_1m,
            output_cost_per_1m=output_cost_per_1m,
            tool_call_cost=self.settings.research_web_search_cost_per_call,
        )

    def _build_human_review_queue_item(
        self,
        run: ResearchRunRecord,
    ) -> HumanReviewQueueItem:
        latest_review = _visible_latest_review(
            run,
            latest_review=self._latest_review(run.id),
        )
        return HumanReviewQueueItem(
            run_id=run.id,
            status=run.status,
            done_reason=run.done_reason,
            latest_verdict=latest_review.verdict if latest_review else None,
            latest_score=latest_review.score if latest_review else None,
            latest_rationale=latest_review.rationale if latest_review else None,
            audit_summary=self._human_review_audit_summary(run),
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    def _latest_review(self, run_id: UUID) -> ReviewRecord | None:
        reviews = self.repository.get_reviews(run_id)
        if not reviews:
            return None
        return reviews[-1]

    def _is_manual_upload_run(self, run_id: UUID) -> bool:
        return any(
            attempt.run_no == 1 and attempt.source == "manual_upload"
            for attempt in self.repository.get_attempts(run_id)
        )

    def _should_use_manual_chatgpt_rerun(self, run: ResearchRunRecord) -> bool:
        return run.rerun_execution_mode == RerunExecutionMode.MANUAL_CHATGPT

    def _manual_rerun_query_policy_decision(
        self,
        run: ResearchRunRecord,
        *,
        action: HumanReviewAction,
        human_comment: str | None,
    ) -> QueryPolicyDecision | None:
        scope = MANUAL_RERUN_ACTION_SCOPES.get(action)
        if scope is None:
            return None
        if _blocked_human_resume_reason(run, action) is not None:
            return None
        plan = self._build_rerun_plan(run, human_comment=human_comment, scope=scope)
        prompt = self._build_rerun_brief(
            run,
            human_comment=human_comment,
            plan=plan,
        )
        return _deep_research_query_policy_decision(prompt)

    def _manual_rerun_query_policy_blocked_actions(
        self,
        run: ResearchRunRecord,
    ) -> dict[HumanReviewAction, QueryPolicyDecision]:
        blocked: dict[HumanReviewAction, QueryPolicyDecision] = {}
        for action in MANUAL_RERUN_ACTION_SCOPES:
            decision = self._manual_rerun_query_policy_decision(
                run,
                action=action,
                human_comment=None,
            )
            if decision is not None and decision.status != "allowed":
                blocked[action] = decision
        return blocked

    def _build_suggested_rerun_prompt(
        self,
        run: ResearchRunRecord,
        *,
        latest_review: ReviewRecord | None,
    ) -> SuggestedRerunPrompt | None:
        manual_full_allowed = (
            _blocked_human_resume_reason(
                run,
                HumanReviewAction.REQUEST_MANUAL_FULL_RERUN,
            )
            is None
        )
        manual_targeted_allowed = (
            _blocked_human_resume_reason(
                run,
                HumanReviewAction.REQUEST_MANUAL_TARGETED_RERUN,
            )
            is None
        )
        done_reason = run.done_reason or ""
        scope: str | None = None
        if (
            manual_full_allowed
            and (
                (
                    latest_review is not None
                    and latest_review.verdict == Verdict.NEEDS_FULL_RERUN
                )
                or "full_rerun" in done_reason
            )
        ):
            scope = "full_rerun"
        elif (
            manual_targeted_allowed
            and (
                (
                    latest_review is not None
                    and latest_review.verdict == Verdict.NEEDS_TARGETED_RERUN
                )
                or "targeted_rerun" in done_reason
            )
        ):
            scope = "targeted_rerun"
        if scope is None:
            return None

        plan = self._build_rerun_plan(run, human_comment=None, scope=scope)
        prompt = self._build_rerun_brief(run, human_comment=None, plan=plan)
        query_policy = _deep_research_query_policy_decision(prompt)
        if query_policy.status != "allowed":
            return None
        return SuggestedRerunPrompt(
            scope=scope,
            expected_output_kind=_expected_rerun_output_kind(scope),
            expected_run_no=run.deep_research_runs + 1,
            prompt=prompt,
            target_item_ids=plan.target_item_ids,
            query_policy=query_policy,
            base_report_hash=report_hash(run.report) if run.report else None,
        )

    def _build_rerun_brief(
        self,
        run: ResearchRunRecord,
        *,
        human_comment: str | None,
        plan: RerunPlan,
    ) -> str:
        latest_review = self._latest_review(run.id)
        items = self.repository.get_research_items(run.id)
        target_item_id_set = set(plan.target_item_ids)
        target_items = [item for item in items if item.item_id in target_item_id_set]
        preserve_item_ids = [
            item.item_id for item in items if item.item_id not in target_item_id_set
        ]
        review_block = (
            "No previous review is available."
            if latest_review is None
            else f"""verdict: {latest_review.verdict.value}
score: {latest_review.score}
rationale: {latest_review.rationale}"""
        )
        gaps = [] if latest_review is None else latest_review.gaps
        factuality_concerns = [] if latest_review is None else latest_review.factuality_concerns
        source_quality_concerns = (
            [] if latest_review is None else latest_review.source_quality_concerns
        )
        next_instructions = (
            "None."
            if latest_review is None or latest_review.next_instructions is None
            else latest_review.next_instructions
        )
        human_block = human_comment or "None."
        output_contract = (
            """Expected output: complete_replacement_report.
Return the final report body only. Do not return rerun analysis, a plan, a checklist,
or commentary about what you would do."""
            if plan.scope == "full_rerun"
            else """Expected output: targeted_delta_sections.
Return only the item-scoped additions or replacement sections needed to patch the
previous report. Do not return a full merged report."""
        )
        rerun_policy = (
            """You are rebuilding the report because the current report or contract execution
was judged unusable. Address the full Objective Contract and all ResearchItems.
Return a complete replacement report."""
            if plan.scope == "full_rerun"
            else """You are not rewriting the full report.
Close only the specified unresolved ResearchItems.
Treat # Rerun Plan and # Next Instructions as the primary guidance.
Focus on missing evidence, outdated facts, contradictions, and insufficient sources for
the target items only.
Return only item-scoped delta sections and evidence summaries.
Do not return a complete revised report.
Do not rewrite preserved sections or preserved ResearchItems."""
        )
        return f"""# Original User Prompt
{run.user_prompt}

# Output Contract
{output_contract}

# Original Research Brief
{run.optimized_prompt or run.user_prompt}

# Previous Report
{run.report or ""}

# Rerun Plan
{plan.model_dump_json()}

# Target ResearchItems
{[item.model_dump(mode="json") for item in target_items]}

# Preserve ResearchItems
{preserve_item_ids}

# Review Result
{review_block}

# Gaps To Fix
{gaps}

# Factuality Concerns
{factuality_concerns}

# Source Quality Concerns
{source_quality_concerns}

# Next Instructions
{next_instructions}

# Human Reviewer Comment
{human_block}

# Rerun Policy
{rerun_policy}
Write the entire Deep Research output in English, even if the original prompt,
previous report, review, or human comment is in another language.
"""

    def _build_rerun_plan(
        self,
        run: ResearchRunRecord,
        *,
        human_comment: str | None,
        scope: str,
    ) -> RerunPlan:
        latest_review = self._latest_review(run.id)
        items = self.repository.get_research_items(run.id)
        if scope == "full_rerun":
            target_item_ids = [item.item_id for item in items]
        else:
            target_item_ids = _target_item_ids_for_action(
                latest_review,
                {
                    RecommendedAction.TARGETED_RERUN,
                    RecommendedAction.FULL_RERUN,
                    RecommendedAction.VERIFY,
                },
            )
        if not target_item_ids:
            target_item_ids = [
                item.item_id
                for item in items
                if item.status in {ItemStatus.PARTIAL, ItemStatus.UNANSWERED}
            ]
        if not target_item_ids:
            target_item_ids = [item.item_id for item in items[:1]]
        target_items = [item for item in items if item.item_id in set(target_item_ids)]
        forbidden_changes = (
            [
                "Do not omit required Objective Contract sections.",
                "Do not return rerun analysis or a plan instead of the final report.",
            ]
            if scope == "full_rerun"
            else [
                "Do not rewrite accepted sections.",
                "Do not return a full merged report.",
            ]
        )
        return RerunPlan(
            rerun_id=f"RR-{uuid4()}",
            scope=scope,
            target_item_ids=target_item_ids,
            preserve_item_ids=[
                item.item_id for item in items if item.item_id not in target_item_ids
            ],
            target_questions=[item.question for item in target_items],
            missing_evidence=_missing_evidence_for_items(latest_review, target_item_ids),
            preferred_source_types=["official", "primary", "regulator", "filing"],
            already_tried_queries=[
                tool.query
                for tool in self.repository.get_tool_calls(run.id)
                if tool.query
            ],
            forbidden_changes=forbidden_changes,
            output_mode=_expected_rerun_output_kind(scope),
            max_tool_calls=min(
                80 if scope == "full_rerun" else 25,
                max(1, run.max_total_tool_calls - run.total_tool_calls),
            ),
            rerun_reason=human_comment
            or (latest_review.route_rationale if latest_review else None)
            or "Close unresolved ResearchItems.",
        )

    def _build_llm_finalize_prompt(
        self,
        *,
        run: ResearchRunRecord,
        review: ReviewRecord,
        human_comment: str | None,
    ) -> str:
        human_block = human_comment or "None."
        return f"""You are an expert research editor.

Use the existing report as the base and address only the minor gaps identified in the review.
Perform limited fact checks if needed.

Rules:
- Do not add unsupported new information.
- Do not fabricate citations.
- Do not substantially expand the scope.
- Do not pretend to resolve gaps that require another Deep Research run.
- Do not state uncertain points as facts.
- Write the final report in English, even if the prompt, report, review, or human
  comment is in another language.

# User Prompt
{run.user_prompt}

# Existing Report
{run.report or ""}

# Review
verdict: {review.verdict.value}
score: {review.score}
rationale: {review.rationale}
gaps: {review.gaps}
factuality_concerns: {review.factuality_concerns}
source_quality_concerns: {review.source_quality_concerns}
next_instructions: {review.next_instructions}

# Human Reviewer Comment
{human_block}

# Output
Return only the final report body.
"""

    def _call_llm_finalize(
        self,
        *,
        prompt: str,
        run: ResearchRunRecord,
    ) -> tuple[str, str | None, dict[str, Any]]:
        custom_finalize = getattr(self.azure, "llm_finalize_report", None)
        if callable(custom_finalize):
            result = custom_finalize(prompt=prompt, run=run)
            return _normalize_llm_finalize_result(result)

        revised_report, response_id, raw_response = self.azure.finalize_report(
            user_prompt=run.user_prompt,
            report=run.report or "",
            review={"prompt": prompt},
            enable_web_search=True,
        )
        if not revised_report:
            raise RuntimeError("LLM finalize returned an empty report.")
        return revised_report, response_id, raw_response

    def _call_verification(
        self,
        *,
        run: ResearchRunRecord,
        prompt: str,
    ) -> tuple[str, str | None, dict[str, Any]]:
        custom_verify = getattr(self.azure, "verify_report", None)
        if callable(custom_verify):
            result = custom_verify(prompt=prompt, run=run)
            return _normalize_llm_finalize_result(result)

        verification_note, response_id, raw_response = self.azure.finalize_report(
            user_prompt=run.user_prompt,
            report=run.report or "",
            review={"prompt": prompt},
            enable_web_search=True,
        )
        if not verification_note:
            raise RuntimeError("Verification returned an empty response.")
        return verification_note, response_id, raw_response


def _extract_response_error(response: Any) -> str:
    raw = response_to_jsonable(response)
    if raw.get("error"):
        return str(raw["error"])
    if raw.get("incomplete_details"):
        return str(raw["incomplete_details"])
    return "Deep Research ended without a completed response."


def _manual_import_request_hash(
    *,
    input_prompt: str,
    report: str,
    options: dict[str, Any],
    allow_remote_review: bool,
    allow_api_reruns: bool,
    rerun_execution_mode: RerunExecutionMode,
) -> str:
    payload = {
        "input_prompt_sha256": hashlib.sha256(input_prompt.encode("utf-8")).hexdigest(),
        "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
        "options": options,
        "allow_remote_review": allow_remote_review,
        "allow_api_reruns": allow_api_reruns,
        "rerun_execution_mode": rerun_execution_mode.value,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manual_rerun_artifact_fragment(rerun_id: str, result_sha256: str) -> str:
    safe_rerun_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", rerun_id).strip("._-")
    if not safe_rerun_id:
        safe_rerun_id = "rerun"
    return f"{safe_rerun_id[:40]}_{result_sha256[:12]}"


def _manual_import_checkpoint_snapshot(
    *,
    kind: str,
    status: RunStatus,
    input_prompt: str,
    report: str,
    source_attempt_no: int,
    snapshot_extra: dict[str, Any],
) -> dict[str, Any]:
    snapshot = {
        "kind": kind,
        "status": status.value,
        "source_prompt": input_prompt,
        "source_report": report,
        "source_report_excerpt": _excerpt(report),
        "source_prompt_excerpt": _excerpt(input_prompt),
        "source_attempt_no": source_attempt_no,
        "source_review_no": None,
        "source_response_id": None,
    }
    snapshot.update(snapshot_extra)
    return snapshot


_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]{1,300})\]\((https?://[^)\s]+)\)")
_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+")


def _manual_upload_citations(
    report: str,
    *,
    retrieved_at: str,
    source_type: str = "manual_upload_url_unverified",
) -> list[Citation]:
    citations: list[Citation] = []
    seen_urls: set[str] = set()
    for match in _MARKDOWN_LINK_RE.finditer(report):
        title = match.group(1).strip() or None
        url = _clean_manual_url(match.group(2))
        if url in seen_urls:
            continue
        seen_urls.add(url)
        citations.append(
            Citation(
                title=title,
                url=url,
                start_index=match.start(2),
                end_index=match.end(2),
                source_type=source_type,
                retrieved_at=retrieved_at,
            )
        )
    for match in _URL_RE.finditer(report):
        url = _clean_manual_url(match.group(0))
        if url in seen_urls:
            continue
        seen_urls.add(url)
        citations.append(
            Citation(
                title=None,
                url=url,
                start_index=match.start(),
                end_index=match.end(),
                source_type=source_type,
                retrieved_at=retrieved_at,
            )
        )
    return citations


def _clean_manual_url(url: str) -> str:
    return url.rstrip(".,;:")


def _acceptance_criteria_from_history(history: list[dict[str, Any]]) -> list[str]:
    for event in reversed(history):
        criteria = event.get("acceptance_criteria")
        if isinstance(criteria, list):
            return [str(item) for item in cast(list[object], criteria)]
    return []


def _deep_research_query_policy_decision(prompt: str) -> QueryPolicyDecision:
    return query_policy_gate(
        {
            "candidate_queries": [prompt],
            "contains_sensitive_terms": contains_sensitive_terms(prompt),
        },
        {},
    )


def _checkpoint_source_prompt(checkpoint: ResearchCheckpoint) -> str:
    value = checkpoint.snapshot_json.get("source_prompt")
    return value if isinstance(value, str) else ""


def _checkpoint_source_report(checkpoint: ResearchCheckpoint) -> str:
    value = checkpoint.snapshot_json.get("source_report")
    return value if isinstance(value, str) else ""


def _excerpt(text: str, *, limit: int = 1200) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}\n..."


def _build_fork_deep_research_prompt(
    *,
    checkpoint: ResearchCheckpoint,
    source_prompt: str,
    source_report: str,
    additional_prompt: str,
) -> str:
    return f"""# Fork Source
Parent run: {checkpoint.run_id}
Checkpoint: {checkpoint.checkpoint_id}
Checkpoint kind: {checkpoint.kind}
Source attempt: {checkpoint.source_attempt_no or "n/a"}
Source review: {checkpoint.source_review_no or "n/a"}

# Original Research Brief
{source_prompt}

# Source Report Snapshot
{source_report}

# Additional Fork Instructions
{additional_prompt}

# Fork Policy
Run a new Deep Research pass as a child run. Treat the source report as preserved
context, then return only the new or changed findings needed to satisfy the
additional instructions. Do not mutate the parent run. Write the output in English
unless the additional instructions explicitly require another language.
"""


def _fork_preview_hash(
    *,
    checkpoint_id: UUID,
    additional_prompt: str,
    composed_prompt: str,
    query_policy: QueryPolicyDecision,
) -> str:
    payload = {
        "checkpoint_id": str(checkpoint_id),
        "additional_prompt": additional_prompt,
        "composed_prompt": composed_prompt,
        "query_policy": query_policy.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fork_submit_response(
    child: ResearchRunRecord,
    lineage: ResearchRunLineage,
) -> ForkSubmitResponse:
    return ForkSubmitResponse(
        run_id=child.id,
        parent_run_id=lineage.parent_run_id,
        forked_from_checkpoint_id=lineage.forked_from_checkpoint_id,
        child_run_id=child.id,
        status=child.status,
        done_reason=child.done_reason,
        needs_human_review=child.needs_human_review,
        source_snapshot_json=lineage.source_snapshot_json,
        lineage=lineage,
    )


def _validate_existing_fork_lineage(
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


def _latest_rerun_plan(plans: list[RerunPlan]) -> RerunPlan | None:
    if not plans:
        return None
    return plans[-1]


def _unknown_item_assessment_ids(
    existing_items: list[ResearchItem],
    review: ReviewRecord,
) -> list[str]:
    if not existing_items:
        return []
    known_ids = {item.item_id for item in existing_items}
    return [
        assessment.item_id
        for assessment in review.item_assessments
        if assessment.item_id not in known_ids
    ]


def _missing_item_assessment_ids(
    existing_items: list[ResearchItem],
    review: ReviewRecord,
) -> list[str]:
    if not existing_items:
        return []
    assessed_ids = {assessment.item_id for assessment in review.item_assessments}
    return [item.item_id for item in existing_items if item.item_id not in assessed_ids]


def _items_from_review_assessments(
    *,
    existing_items: list[ResearchItem],
    review: ReviewRecord,
    attempt_no: int,
) -> list[ResearchItem]:
    by_id = {item.item_id: item for item in existing_items}
    updated: list[ResearchItem] = []
    for assessment in review.item_assessments:
        item = by_id.get(assessment.item_id)
        if item is None:
            continue
        updated.append(
            item.model_copy(
                update={
                    "status": assessment.status,
                    "severity": assessment.severity,
                    "confidence": assessment.failure_mode_confidence,
                    "evidence_summary": assessment.evidence_summary,
                    "failure_mode": assessment.failure_mode,
                    "failure_mode_confidence": assessment.failure_mode_confidence,
                    "unresolved_reason": (
                        assessment.rationale
                        if assessment.status
                        in {ItemStatus.PARTIAL, ItemStatus.UNANSWERED, ItemStatus.UNVERIFIABLE}
                        else None
                    ),
                    "last_attempt_no": attempt_no,
                    "last_review_no": review.review_no,
                }
            )
        )
    updated_ids = {item.item_id for item in updated}
    unchanged = [item for item in existing_items if item.item_id not in updated_ids]
    return [*updated, *unchanged]


def _verification_query_for_item(
    item: ResearchItem,
    review: ReviewRecord | None,
) -> str:
    missing_evidence: list[str] = []
    rationale = ""
    if review is not None:
        for assessment in review.item_assessments:
            if assessment.item_id == item.item_id:
                missing_evidence = assessment.missing_evidence
                rationale = assessment.rationale
                break
    evidence_need = "; ".join(missing_evidence) if missing_evidence else item.question
    return f"{item.question} {evidence_need} {rationale}".strip()


def _build_verification_prompt(
    *,
    run: ResearchRunRecord,
    items: list[ResearchItem],
    safe_queries: list[str],
    latest_review: ReviewRecord | None,
) -> str:
    review_context = (
        "No prior review context is available."
        if latest_review is None
        else f"""verdict: {latest_review.verdict.value}
rationale: {latest_review.rationale}
factuality_concerns: {latest_review.factuality_concerns}
freshness_concerns: {latest_review.freshness_concerns}
source_quality_concerns: {latest_review.source_quality_concerns}"""
    )
    return f"""# Verification Task
Verify only the listed ResearchItems. Use public web search only for the safe
queries supplied below. Return concise item-scoped verification notes with source
metadata and remaining uncertainty. Do not rewrite the full report.

# Original Prompt
{run.user_prompt}

# Current Report
{run.report or ""}

# Target ResearchItems
{[item.model_dump(mode="json") for item in items]}

# Safe Queries
{safe_queries}

# Latest Review
{review_context}
"""


def _target_item_ids_for_action(
    review: ReviewRecord | None,
    actions: set[RecommendedAction],
) -> list[str]:
    if review is None:
        return []
    return [
        item.item_id
        for item in review.item_assessments
        if item.recommended_action in actions
        or (
            RecommendedAction.TARGETED_RERUN in actions
            and item.failure_mode
            in {
                FailureMode.NEEDS_DIFFERENT_SOURCES,
                FailureMode.NEEDS_DEEPER_SEARCH,
                FailureMode.NEEDS_QUERY_REFORMULATION,
            }
        )
    ]


def _missing_evidence_for_items(
    review: ReviewRecord | None,
    target_item_ids: list[str],
) -> list[str]:
    if review is None:
        return []
    target_set = set(target_item_ids)
    missing: list[str] = []
    for item in review.item_assessments:
        if item.item_id in target_set:
            missing.extend(item.missing_evidence)
    return missing


def _deterministic_merge_delta(
    existing_report: str,
    delta: str,
    *,
    run_no: int,
    heading: str = "Targeted Research Updates",
) -> str:
    existing = existing_report.rstrip()
    delta_text = delta.strip()
    if not existing:
        return delta_text
    if not delta_text:
        return existing
    return (
        f"{existing}\n\n"
        f"## {heading} {run_no}\n\n"
        f"{delta_text}\n"
    )


def _merge_targeted_research_delta(
    *,
    existing_report: str,
    delta: str,
    run_no: int,
) -> str:
    if _looks_like_full_merged_report(existing_report, delta):
        raise RegressionError("Targeted rerun returned what looks like a full merged report.")

    section_id = f"targeted-research-updates-{run_no:03d}"
    report = ReportDocument(
        sections={"base": existing_report.rstrip()},
        mutable_sections={section_id},
        preserve_section_ids={"base"},
    )
    merged = deterministic_merge(
        report,
        [
            MergePatchDelta(
                target_item_id="targeted-rerun",
                section_id=section_id,
                operation="add_new_section",
                new_text=delta.strip(),
                citation_ids=[],
                patch_reason="targeted Deep Research delta",
            )
        ],
    )
    base = merged.sections["base"].rstrip()
    update = merged.sections[section_id].strip()
    if not base:
        return update
    if not update:
        return base
    return f"{base}\n\n## Targeted Research Updates {run_no}\n\n{update}\n"


def _looks_like_full_merged_report(existing_report: str, delta: str) -> bool:
    existing = existing_report.strip()
    candidate = delta.strip()
    if not existing or not candidate:
        return False
    leading_sample = existing[: min(500, len(existing))]
    if leading_sample and leading_sample in candidate:
        return True
    if len(candidate) > len(existing) * 0.8:
        existing_lines = {
            line.strip()
            for line in existing.splitlines()
            if len(line.strip()) >= 40
        }
        candidate_lines = {
            line.strip()
            for line in candidate.splitlines()
            if len(line.strip()) >= 40
        }
        if existing_lines and len(existing_lines & candidate_lines) / len(existing_lines) >= 0.5:
            return True
    return False


def _limitation_finalize_blocked_reason(items: list[ResearchItem]) -> str | None:
    for item in items:
        if item.severity == Severity.BLOCKER and item.status not in {
            ItemStatus.ANSWERED,
            ItemStatus.OUT_OF_SCOPE,
        }:
            return "limitation_blocker_unresolved"
        if item.severity == Severity.MAJOR and item.status == ItemStatus.NOT_STARTED:
            return "limitation_required_item_unreviewed"
    return None


def _is_waiting_for_human_review(run: ResearchRunRecord) -> bool:
    return run.status == RunStatus.NEEDS_HUMAN_REVIEW and run.needs_human_review


def _is_terminal_run_status(status: RunStatus) -> bool:
    return status in TERMINAL_RUN_STATUSES


def _has_pending_remote_deep_research(run: ResearchRunRecord) -> bool:
    return (
        run.status in {RunStatus.WAITING_DEEP_RESEARCH, RunStatus.COLLECTING}
        and bool(run.pending_deep_research_response_id)
    )


def _visible_latest_review(
    run: ResearchRunRecord,
    *,
    latest_review: ReviewRecord | None,
) -> ReviewRecord | None:
    if latest_review is None:
        return None
    if _done_reason_is_deep_research_execution_stop(run.done_reason):
        return None
    return latest_review


def _done_reason_is_deep_research_execution_stop(done_reason: str | None) -> bool:
    if done_reason is None:
        return False
    if done_reason.startswith("deep_research_"):
        return True
    return done_reason in {
        "missing_deep_research_response_id",
        "max_total_tool_calls_reached_before_deep_research_submit",
    }


def _human_review_route_reason(review: ReviewRecord) -> str:
    if review.high_risk_flags:
        return "review_route_high_risk"
    if review.reviewer_confidence < MIN_REVIEWER_CONFIDENCE_FOR_AUTO_FINALIZE:
        return "review_route_low_confidence"
    return f"review_route_{review.verdict.value}"


def _allowed_human_review_actions(
    run: ResearchRunRecord,
    *,
    query_policy_blocked_actions: (
        dict[HumanReviewAction, QueryPolicyDecision] | None
    ) = None,
) -> list[HumanReviewAction]:
    query_policy_blocked_actions = query_policy_blocked_actions or {}
    return [
        action
        for action in HumanReviewAction
        if _blocked_human_resume_reason(run, action) is None
        and action not in query_policy_blocked_actions
    ]


def _human_review_action_states(
    run: ResearchRunRecord,
    *,
    pending_manual_rerun: bool = False,
    query_policy_blocked_actions: (
        dict[HumanReviewAction, QueryPolicyDecision] | None
    ) = None,
) -> list[HumanReviewActionState]:
    query_policy_blocked_actions = query_policy_blocked_actions or {}
    states: list[HumanReviewActionState] = []
    for action in HumanReviewAction:
        blocked_reason = (
            "manual_chatgpt_rerun_pending"
            if pending_manual_rerun
            else _blocked_human_resume_reason(run, action)
        )
        if blocked_reason is None and action in query_policy_blocked_actions:
            blocked_reason = MANUAL_RERUN_PROMPT_BLOCKED_REASON
        states.append(
            HumanReviewActionState(
                action=action,
                allowed=blocked_reason is None,
                blocked_reason=blocked_reason,
            )
        )
    return states


def _expected_rerun_output_kind(scope: str) -> str:
    return (
        "complete_replacement_report"
        if scope == "full_rerun"
        else "targeted_delta_sections"
    )


def _human_review_route_summary(
    history: list[dict[str, Any]],
    *,
    latest_review: ReviewRecord | None,
) -> HumanReviewRouteSummary | None:
    route_events = [
        event
        for event in history
        if event.get("step") == "route_after_review"
    ]
    if not route_events and latest_review is None:
        return None
    latest_event: dict[str, Any] = route_events[-1] if route_events else {}
    dominant_actions_raw = latest_event.get("dominant_actions")
    dominant_actions: list[str] = []
    if isinstance(dominant_actions_raw, list):
        dominant_actions = [
            str(action) for action in cast(list[object], dominant_actions_raw)
        ]
    return HumanReviewRouteSummary(
        candidate_route=cast(str | None, latest_event.get("candidate_route")),
        selected_route=cast(str | None, latest_event.get("selected_route")),
        blocked_reason=cast(str | None, latest_event.get("blocked_reason")),
        dominant_actions=dominant_actions,
        latest_review_no=latest_review.review_no if latest_review else None,
        latest_verdict=latest_review.verdict if latest_review else None,
    )


def _route_budget_snapshot(route_state: RouteState) -> dict[str, object]:
    route_state_dict = cast(dict[str, object], route_state)
    keys = [
        "total_reviews",
        "max_total_iterations",
        "targeted_rerun_runs",
        "max_targeted_rerun_runs",
        "full_rerun_runs",
        "max_full_rerun_runs",
        "llm_patch_runs",
        "max_llm_patch_runs",
        "verification_runs",
        "max_verification_runs",
        "no_progress_count",
        "total_tool_calls",
        "max_total_tool_calls",
    ]
    return {key: route_state_dict.get(key) for key in keys if key in route_state_dict}


def _blocked_human_resume_reason(
    run: ResearchRunRecord,
    action: HumanReviewAction,
) -> str | None:
    if action in {
        HumanReviewAction.APPROVE,
        HumanReviewAction.APPROVE_WITH_LIMITATION,
    }:
        if not run.report:
            return "missing_report_for_approval"
        return None

    if action == HumanReviewAction.REQUEST_REVIEW:
        if run.done_reason not in {
            "review_timeout",
            "review_schema_or_request_failed",
        }:
            return "review_retry_available_only_after_review_error"
        if not run.report:
            return "missing_report_for_review_retry"
        return None

    if action == HumanReviewAction.REQUEST_LLM_PATCH and not run.report:
        return "missing_report_for_llm_patch"

    if action == HumanReviewAction.REQUEST_VERIFICATION and run.total_reviews == 0:
        return "missing_review_for_verification"

    if action in {
        HumanReviewAction.REQUEST_TARGETED_RERUN,
        HumanReviewAction.REQUEST_MANUAL_TARGETED_RERUN,
    } and not run.report:
        return "missing_report_for_targeted_rerun_use_full_rerun"

    if action not in {
        HumanReviewAction.REQUEST_LLM_PATCH,
        HumanReviewAction.REQUEST_TARGETED_RERUN,
        HumanReviewAction.REQUEST_FULL_RERUN,
        HumanReviewAction.REQUEST_MANUAL_TARGETED_RERUN,
        HumanReviewAction.REQUEST_MANUAL_FULL_RERUN,
        HumanReviewAction.REQUEST_VERIFICATION,
        HumanReviewAction.REQUEST_ITEM_REVISION,
    }:
        return None

    if run.total_tool_calls >= run.max_total_tool_calls:
        return "max_total_tool_calls_reached"

    if run.total_reviews >= run.max_total_iterations:
        return "max_total_iterations_reached"

    if run.no_progress_count >= 2 and action not in {
        HumanReviewAction.REQUEST_TARGETED_RERUN,
        HumanReviewAction.REQUEST_FULL_RERUN,
        HumanReviewAction.REQUEST_MANUAL_TARGETED_RERUN,
        HumanReviewAction.REQUEST_MANUAL_FULL_RERUN,
        HumanReviewAction.REQUEST_ITEM_REVISION,
    }:
        return "max_no_progress_count_reached"

    if (
        action == HumanReviewAction.REQUEST_TARGETED_RERUN
        and run.targeted_rerun_runs >= run.max_targeted_rerun_runs
    ):
        return "max_targeted_rerun_runs_reached"

    if (
        action == HumanReviewAction.REQUEST_FULL_RERUN
        and run.full_rerun_runs >= run.max_full_rerun_runs
    ):
        return "max_full_rerun_runs_reached"

    if (
        action == HumanReviewAction.REQUEST_LLM_PATCH
        and run.llm_patch_runs >= run.max_llm_patch_runs
    ):
        return "max_llm_patch_runs_reached"

    if (
        action == HumanReviewAction.REQUEST_VERIFICATION
        and run.verification_runs >= run.max_verification_runs
    ):
        return "max_verification_runs_reached"

    return None


def _normalize_llm_finalize_result(result: object) -> tuple[str, str | None, dict[str, Any]]:
    if isinstance(result, tuple):
        tuple_result = cast(tuple[object, ...], result)
        if not tuple_result:
            raise RuntimeError("LLM finalize returned an empty report.")
        report = str(tuple_result[0])
        response_id = (
            str(tuple_result[1]) if len(tuple_result) > 1 and tuple_result[1] is not None else None
        )
        raw_response = (
            cast(dict[str, Any], tuple_result[2])
            if len(tuple_result) > 2 and isinstance(tuple_result[2], dict)
            else {}
        )
        if not report:
            raise RuntimeError("LLM finalize returned an empty report.")
        return report, response_id, raw_response

    if isinstance(result, dict):
        result_dict = cast(dict[str, Any], result)
        report_value = result_dict.get("report") or result_dict.get("output_text")
        report = str(report_value or "")
        if not report:
            raise RuntimeError("LLM finalize returned an empty report.")
        response_id_value = result_dict.get("response_id") or result_dict.get("id")
        response_id = str(response_id_value) if response_id_value is not None else None
        return report, response_id, result_dict

    report = str(result or "")
    if not report:
        raise RuntimeError("LLM finalize returned an empty report.")
    return report, None, {}
