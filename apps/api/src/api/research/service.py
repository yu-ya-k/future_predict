from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError

from api.config import Settings
from api.research.artifacts import ArtifactStore
from api.research.azure_responses import AzureResponsesClient, ReviewResponseParseError
from api.research.costing import build_cost_event, count_billable_web_search_calls
from api.research.extractors import (
    extract_citations,
    extract_tool_calls,
    get_response_id,
    get_response_output_text,
    get_response_status,
    response_to_jsonable,
)
from api.research.nodes import build_optimized_prompt
from api.research.progress import compute_no_progress_count, report_hash
from api.research.repository import ResearchRepository
from api.research.routing import route_after_review
from api.research.schemas import (
    CreateResearchRunRequest,
    HumanReviewAction,
    HumanReviewAuditSummary,
    HumanReviewPayload,
    HumanReviewQueueItem,
    HumanReviewResumeRequest,
    ResearchAttempt,
    ResearchRunRecord,
    ReviewRecord,
    RunStatus,
)
from api.research.security import (
    contains_confidential_text,
    should_enable_deep_research_web_search,
    should_enable_reviewer_web_search,
)

TERMINAL_FAILURE_STATUSES = {"failed", "cancelled", "incomplete"}


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

    def create_run(self, request: CreateResearchRunRequest) -> ResearchRunRecord:
        contains_confidential = contains_confidential_text(request.user_prompt)
        run = self.repository.create_run(
            user_prompt=request.user_prompt,
            options=request.options,
            settings=self.settings,
            contains_confidential_context=contains_confidential,
        )
        return self.submit_deep_research(run.id)

    def submit_deep_research(
        self,
        run_id: UUID,
        *,
        human_comment: str | None = None,
    ) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        run_no = run.deep_research_runs + 1
        if run.deep_research_runs == 0:
            optimized_prompt, acceptance_criteria = build_optimized_prompt(
                user_prompt=run.user_prompt,
                context_classification=run.context_classification,
            )
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
                    "acceptance_criteria": acceptance_criteria,
                },
            )
        else:
            optimized_prompt = run.optimized_prompt or run.user_prompt
            prompt = self._build_rerun_brief(run, human_comment=human_comment)
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
                    "artifact_path": prompt_path,
                },
            )

        remaining_tool_calls = run.max_total_tool_calls - run.total_tool_calls
        if remaining_tool_calls <= 0:
            return self._enter_human_review(
                run.id,
                done_reason="max_total_tool_calls_reached_before_deep_research_submit",
                optimized_prompt=optimized_prompt,
            )

        try:
            web_search_enabled = should_enable_deep_research_web_search(
                context_classification=run.context_classification,
                contains_confidential_context=run.contains_confidential_context,
                web_search_allowed=run.web_search_allowed,
            )
            response = self.azure.submit_deep_research(
                prompt=prompt,
                max_tool_calls=min(
                    remaining_tool_calls,
                    self.settings.default_max_total_tool_calls,
                ),
                web_search_enabled=web_search_enabled,
                context_classification=run.context_classification,
                contains_confidential_context=run.contains_confidential_context,
                web_search_allowed=run.web_search_allowed,
            )
            raw_path, _ = self.artifacts.save_json(
                run.id,
                f"raw-responses/deep_research_submit_{run_no:03d}.json",
                response_to_jsonable(response),
            )
            response_id = get_response_id(response)
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
            return self.repository.update_run(
                run.id,
                optimized_prompt=optimized_prompt,
                status=RunStatus.WAITING_DEEP_RESEARCH,
                needs_human_review=False,
                pending_deep_research_response_id=response_id,
                deep_research_status=response_status,
                deep_research_runs=run_no,
                done_reason=None,
            )
        except Exception as error:
            self.repository.add_attempt(
                run.id,
                ResearchAttempt(
                    run_no=run_no,
                    response_id=None,
                    status="failed_to_submit",
                    model=self.azure.deep_research_deployment,
                    prompt=prompt,
                    error=repr(error),
                ),
            )
            return self._enter_human_review(
                run.id,
                done_reason="deep_research_submit_failed",
                optimized_prompt=optimized_prompt,
                deep_research_runs=run_no,
            )

    def collect_deep_research(self, run_id: UUID) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if run.status not in {RunStatus.WAITING_DEEP_RESEARCH, RunStatus.COLLECTING}:
            return run

        if run.status == RunStatus.WAITING_DEEP_RESEARCH:
            claimed = self.repository.claim_deep_research_run(run.id)
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
            return self.repository.update_run(
                run.id,
                status=RunStatus.WAITING_DEEP_RESEARCH,
                deep_research_status="retrieve_retryable_error",
            )

        response_status = get_response_status(response)
        if response_status in {"queued", "in_progress"}:
            return self.repository.update_run(
                run.id,
                status=RunStatus.WAITING_DEEP_RESEARCH,
                deep_research_status=response_status,
            )

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
                deep_research_status=response_status,
                total_tool_calls=run.total_tool_calls + tool_call_delta,
                estimated_cost_usd=run.estimated_cost_usd + cost_delta,
            )

        if response_status != "completed":
            return self._enter_human_review(
                run.id,
                done_reason="deep_research_unknown_status",
                deep_research_status=response_status,
            )

        report = get_response_output_text(response)
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
        report_path, _ = self.artifacts.save_text(
            run.id,
            f"reports/report_attempt_{run.deep_research_runs:03d}.md",
            report,
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
        updated = self.repository.update_run(
            run.id,
            status=RunStatus.REVIEWING,
            report=report,
            deep_research_status=response_status,
            total_tool_calls=run.total_tool_calls + tool_call_delta,
            estimated_cost_usd=run.estimated_cost_usd + cost_delta,
        )
        return self.review_run(updated.id)

    def review_run(self, run_id: UUID) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if not run.report:
            return self._enter_human_review(run.id, done_reason="missing_report_for_review")

        previous_reviews = self.repository.get_reviews(run.id)
        acceptance_criteria = _acceptance_criteria_from_history(self.repository.get_history(run.id))
        web_search_enabled = should_enable_reviewer_web_search(
            context_classification=run.context_classification,
            contains_confidential_context=run.contains_confidential_context,
            web_search_allowed=run.web_search_allowed,
        )

        try:
            review_result, response_id, raw_response = self._review_with_retry(
                run=run,
                acceptance_criteria=acceptance_criteria,
                web_search_enabled=web_search_enabled,
            )
            run = self.repository.get_run(run.id)
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
            )
        except (ValueError, ValidationError, RuntimeError) as error:
            self.repository.append_history_event(
                run.id,
                {"step": "review_failed", "error": repr(error)},
            )
            return self._enter_human_review(
                run.id,
                done_reason="review_schema_or_request_failed",
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

        route = route_after_review(
            {
                "review": review.model_dump(),
                "total_reviews": run.total_reviews + 1,
                "deep_research_runs": run.deep_research_runs,
                "llm_fix_runs": run.llm_fix_runs,
                "no_progress_count": no_progress_count,
                "max_total_iterations": run.max_total_iterations,
                "max_deep_research_runs": run.max_deep_research_runs,
                "max_llm_fix_runs": run.max_llm_fix_runs,
                "max_no_progress_rounds": run.max_no_progress_rounds,
                "estimated_cost_usd": next_estimated_cost,
                "max_cost_usd": run.max_cost_usd,
                "total_tool_calls": next_total_tool_calls,
                "max_total_tool_calls": run.max_total_tool_calls,
                "contains_confidential_context": run.contains_confidential_context,
                "web_search_allowed": run.web_search_allowed,
            }
        )

        self.repository.append_history_event(
            run.id,
            {
                "step": "route_after_review",
                "route": route,
                "verdict": review.verdict.value,
                "total_reviews": run.total_reviews + 1,
                "no_progress_count": no_progress_count,
                "total_tool_calls": next_total_tool_calls,
                "estimated_cost_usd": next_estimated_cost,
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
            return self.repository.update_run(
                run.id,
                status=RunStatus.COMPLETED,
                final_report=final_report,
                done_reason="passed_review",
                total_reviews=run.total_reviews + 1,
                no_progress_count=no_progress_count,
                total_tool_calls=next_total_tool_calls,
                estimated_cost_usd=next_estimated_cost,
                needs_human_review=False,
            )

        if route == "human_review":
            return self._enter_human_review(
                run.id,
                done_reason=f"review_route_{review.verdict.value}",
                total_reviews=run.total_reviews + 1,
                no_progress_count=no_progress_count,
                total_tool_calls=next_total_tool_calls,
                estimated_cost_usd=next_estimated_cost,
            )

        updated = self.repository.update_run(
            run.id,
            status=RunStatus.REVIEWING,
            needs_human_review=False,
            done_reason=None,
            total_reviews=run.total_reviews + 1,
            no_progress_count=no_progress_count,
            total_tool_calls=next_total_tool_calls,
            estimated_cost_usd=next_estimated_cost,
        )
        if route == "llm_finalize":
            return self.llm_finalize(updated.id)
        if route == "deep_research_submit":
            return self.submit_deep_research(updated.id)

        return self._enter_human_review(
            updated.id,
            done_reason=f"unknown_review_route_{route}",
        )

    def llm_finalize(
        self,
        run_id: UUID,
        *,
        human_comment: str | None = None,
    ) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
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

        run_no = run.llm_fix_runs + 1
        prompt = self._build_llm_finalize_prompt(
            run=run,
            review=latest_review,
            human_comment=human_comment,
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
                    "step": "llm_finalize_failed",
                    "run_no": run_no,
                    "error": repr(error),
                },
            )
            return self._enter_human_review(run.id, done_reason="llm_finalize_failed")

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
            f"reports/llm_fix_{run_no:03d}.md",
            revised_report,
        )
        self.repository.append_history_event(
            run.id,
            {
                "step": "llm_finalize",
                "run_no": run_no,
                "response_id": response_id,
                "tool_calls_count": len(llm_tool_calls),
                "estimated_cost_usd": cost_event.estimated_cost_usd,
                "report_path": report_path,
            },
        )
        updated = self.repository.update_run(
            run.id,
            status=RunStatus.REVIEWING,
            needs_human_review=False,
            report=revised_report,
            llm_fix_runs=run_no,
            total_tool_calls=run.total_tool_calls + tool_call_delta,
            estimated_cost_usd=run.estimated_cost_usd + cost_delta,
            done_reason=None,
        )
        return self.review_run(updated.id)

    def list_human_reviews(self) -> list[HumanReviewQueueItem]:
        return [
            self._build_human_review_queue_item(run)
            for run in self.repository.list_human_review_runs()
        ]

    def get_human_review_payload(self, run_id: UUID) -> HumanReviewPayload:
        run = self.repository.get_run(run_id)
        if not _is_waiting_for_human_review(run):
            raise ValueError("Run is not waiting for human review.")
        latest_review = self._latest_review(run.id)
        return HumanReviewPayload(
            run_id=run.id,
            reason=run.done_reason or (latest_review.rationale if latest_review else ""),
            latest_report=run.report or "",
            latest_review=latest_review,
            allowed_actions=list(HumanReviewAction),
            audit_summary=self._human_review_audit_summary(run),
            warnings=run.warnings,
        )

    def resume_run(
        self,
        run_id: UUID,
        request: HumanReviewResumeRequest,
        *,
        reviewer_id: str | None = None,
    ) -> ResearchRunRecord:
        decision_reviewer_id = reviewer_id or request.reviewer_id
        pending_run = self.repository.get_run(run_id)
        if not _is_waiting_for_human_review(pending_run):
            raise ValueError("Run is not waiting for human review.")

        blocked_reason = _blocked_human_resume_reason(pending_run, request.action)
        if blocked_reason is not None:
            self.repository.append_history_event(
                pending_run.id,
                {
                    "step": "human_review_resume_blocked",
                    "action": request.action.value,
                    "reason": blocked_reason,
                    "comment": request.comment,
                    "reviewer_id": decision_reviewer_id,
                },
            )
            raise ValueError(
                f"Human review action {request.action.value!r} is blocked by {blocked_reason}."
            )

        claimed = self.repository.claim_human_review_decision(
            run_id,
            action=request.action,
            comment=request.comment,
            reviewer_id=decision_reviewer_id,
        )
        if claimed is None:
            raise ValueError("Run is not waiting for human review.")
        run, _decision = claimed

        if request.action == HumanReviewAction.APPROVE:
            if not run.report:
                return self.repository.update_run(
                    run.id,
                    status=RunStatus.FAILED,
                    needs_human_review=False,
                    done_reason="human_approved_without_report",
                )
            final_path, _ = self.artifacts.save_text(
                run.id,
                "reports/final_report.md",
                run.report,
            )
            self.repository.append_history_event(
                run.id,
                {"step": "finalized_by_human", "final_report_path": final_path},
            )
            return self.repository.update_run(
                run.id,
                status=RunStatus.COMPLETED,
                final_report=run.report,
                done_reason="human_approved",
                needs_human_review=False,
            )

        if request.action == HumanReviewAction.REQUEST_LLM_FIX:
            return self.llm_finalize(run.id, human_comment=request.comment)

        if request.action == HumanReviewAction.REQUEST_DEEP_RESEARCH:
            return self.submit_deep_research(run.id, human_comment=request.comment)

        warnings = list(run.warnings)
        if request.comment:
            warnings.append(f"Human reviewer rejected the run: {request.comment}")
        else:
            warnings.append("Human reviewer rejected the run.")
        return self.repository.update_run(
            run.id,
            status=RunStatus.FAILED,
            needs_human_review=False,
            done_reason="human_rejected",
            warnings=warnings,
        )

    def mark_timeout(self, run_id: UUID) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if run.status not in {RunStatus.WAITING_DEEP_RESEARCH, RunStatus.COLLECTING}:
            return run

        if run.status == RunStatus.WAITING_DEEP_RESEARCH:
            claimed = self.repository.claim_deep_research_run(run.id)
            if claimed is None:
                return self.repository.get_run(run.id)
            run = claimed

        if run.status != RunStatus.COLLECTING:
            return run

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
            deep_research_status="timeout",
        )

    def cancel_run(self, run_id: UUID) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if run.pending_deep_research_response_id and run.status == RunStatus.WAITING_DEEP_RESEARCH:
            try:
                self.azure.cancel_response(run.pending_deep_research_response_id)
            except Exception as error:
                self.repository.append_history_event(
                    run.id,
                    {"step": "cancel_remote_failed", "error": repr(error)},
                )
        return self.repository.update_run(
            run.id,
            status=RunStatus.CANCELLED,
            done_reason="cancelled_by_user",
            needs_human_review=False,
        )

    def _review_with_retry(
        self,
        *,
        run: ResearchRunRecord,
        acceptance_criteria: list[str],
        web_search_enabled: bool,
    ) -> tuple[Any, str | None, dict[str, Any]]:
        last_error: Exception | None = None
        citations = [citation.model_dump() for citation in self.repository.get_citations(run.id)]
        for attempt_no in range(2):
            try:
                return self.azure.review_report(
                    user_prompt=run.user_prompt,
                    optimized_prompt=run.optimized_prompt or run.user_prompt,
                    acceptance_criteria=acceptance_criteria,
                    report=run.report or "",
                    citations=citations,
                    web_search_enabled=web_search_enabled,
                )
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
        **fields: Any,
    ) -> ResearchRunRecord:
        updated = self.repository.update_run(
            run_id,
            status=RunStatus.NEEDS_HUMAN_REVIEW,
            needs_human_review=True,
            done_reason=done_reason,
            **fields,
        )
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
        return updated

    def _human_review_audit_summary(
        self,
        run: ResearchRunRecord,
    ) -> HumanReviewAuditSummary:
        return HumanReviewAuditSummary(
            deep_research_runs=run.deep_research_runs,
            llm_fix_runs=run.llm_fix_runs,
            total_reviews=run.total_reviews,
            no_progress_count=run.no_progress_count,
            total_tool_calls=run.total_tool_calls,
            estimated_cost_usd=run.estimated_cost_usd,
        )

    def _build_human_review_queue_item(
        self,
        run: ResearchRunRecord,
    ) -> HumanReviewQueueItem:
        latest_review = self._latest_review(run.id)
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

    def _build_rerun_brief(
        self,
        run: ResearchRunRecord,
        *,
        human_comment: str | None,
    ) -> str:
        latest_review = self._latest_review(run.id)
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
        next_instructions = None if latest_review is None else latest_review.next_instructions
        human_block = human_comment or "なし"
        return f"""# Original User Prompt
{run.user_prompt}

# Original Research Brief
{run.optimized_prompt or run.user_prompt}

# Previous Report
{run.report or ""}

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
Do not merely rewrite the previous report.
Re-investigate weak areas.
Focus on missing evidence, outdated facts, contradictions, and insufficient sources.
Return a complete revised report with citations.
"""

    def _build_llm_finalize_prompt(
        self,
        *,
        run: ResearchRunRecord,
        review: ReviewRecord,
        human_comment: str | None,
    ) -> str:
        human_block = human_comment or "なし"
        return f"""あなたは熟練のリサーチ・エディタです。

既存レポートをベースに、レビューで指摘された軽微な不足のみを補ってください。
必要に応じて限定的な事実確認をしてください。

禁止:
- 根拠のない新情報を追加しない
- 出典を捏造しない
- 大きな論点を新規に広げない
- Deep Research が必要な不足を LLM だけで埋めたふりをしない
- 不確実な点を断定しない

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
完成版レポート本文のみを返してください。
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

        web_search_enabled = should_enable_reviewer_web_search(
            context_classification=run.context_classification,
            contains_confidential_context=run.contains_confidential_context,
            web_search_allowed=run.web_search_allowed,
        )
        revised_report, response_id, raw_response = self.azure.finalize_report(
            user_prompt=run.user_prompt,
            report=run.report or "",
            review={"prompt": prompt},
            web_search_enabled=web_search_enabled,
        )
        if not revised_report:
            raise RuntimeError("LLM finalize returned an empty report.")
        return revised_report, response_id, raw_response


def _extract_response_error(response: Any) -> str:
    raw = response_to_jsonable(response)
    if raw.get("error"):
        return str(raw["error"])
    if raw.get("incomplete_details"):
        return str(raw["incomplete_details"])
    return "Deep Research ended without a completed response."


def _acceptance_criteria_from_history(history: list[dict[str, Any]]) -> list[str]:
    for event in reversed(history):
        criteria = event.get("acceptance_criteria")
        if isinstance(criteria, list):
            return [str(item) for item in cast(list[object], criteria)]
    return []


def _is_waiting_for_human_review(run: ResearchRunRecord) -> bool:
    return run.status == RunStatus.NEEDS_HUMAN_REVIEW and run.needs_human_review


def _blocked_human_resume_reason(
    run: ResearchRunRecord,
    action: HumanReviewAction,
) -> str | None:
    if action not in {
        HumanReviewAction.REQUEST_LLM_FIX,
        HumanReviewAction.REQUEST_DEEP_RESEARCH,
    }:
        return None

    if run.estimated_cost_usd >= run.max_cost_usd:
        return "max_cost_usd_reached"

    if run.total_tool_calls >= run.max_total_tool_calls:
        return "max_total_tool_calls_reached"

    if run.total_reviews >= run.max_total_iterations:
        return "max_total_iterations_reached"

    if run.no_progress_count >= run.max_no_progress_rounds:
        return "max_no_progress_rounds_reached"

    if (
        action == HumanReviewAction.REQUEST_DEEP_RESEARCH
        and run.deep_research_runs >= run.max_deep_research_runs
    ):
        return "max_deep_research_runs_reached"

    if action == HumanReviewAction.REQUEST_LLM_FIX and run.llm_fix_runs >= run.max_llm_fix_runs:
        return "max_llm_fix_runs_reached"

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
