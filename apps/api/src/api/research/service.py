from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError

from api.config import Settings
from api.research.artifacts import ArtifactStore
from api.research.azure_responses import AzureResponsesClient, ReviewResponseParseError
from api.research.extractors import (
    extract_citations,
    extract_tool_calls,
    get_response_id,
    get_response_output_text,
    get_response_status,
    response_to_jsonable,
)
from api.research.nodes import build_optimized_prompt
from api.research.progress import compute_no_progress_count
from api.research.repository import ResearchRepository
from api.research.routing import route_after_review
from api.research.schemas import (
    CreateResearchRunRequest,
    HumanReviewAction,
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

        try:
            web_search_enabled = should_enable_deep_research_web_search(
                context_classification=run.context_classification,
                contains_confidential_context=run.contains_confidential_context,
                web_search_allowed=run.web_search_allowed,
            )
            response = self.azure.submit_deep_research(
                prompt=prompt,
                max_tool_calls=min(
                    run.max_total_tool_calls,
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
            return self.repository.update_run(
                run.id,
                optimized_prompt=optimized_prompt,
                status=RunStatus.NEEDS_HUMAN_REVIEW,
                needs_human_review=True,
                done_reason="deep_research_submit_failed",
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
            return self.repository.update_run(
                run.id,
                status=RunStatus.NEEDS_HUMAN_REVIEW,
                needs_human_review=True,
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

        raw_path, _ = self.artifacts.save_json(
            run.id,
            f"raw-responses/deep_research_collect_{run.deep_research_runs:03d}.json",
            response_to_jsonable(response),
        )

        if response_status in TERMINAL_FAILURE_STATUSES:
            error_text = _extract_response_error(response)
            self.repository.add_attempt(
                run.id,
                ResearchAttempt(
                    run_no=run.deep_research_runs,
                    response_id=response_id,
                    status=response_status,
                    model=self.azure.deep_research_deployment,
                    prompt=run.optimized_prompt or run.user_prompt,
                    error=error_text,
                    raw_response_artifact_path=raw_path,
                ),
            )
            return self.repository.update_run(
                run.id,
                status=RunStatus.NEEDS_HUMAN_REVIEW,
                needs_human_review=True,
                done_reason=f"deep_research_{response_status}",
                deep_research_status=response_status,
            )

        if response_status != "completed":
            return self.repository.update_run(
                run.id,
                status=RunStatus.NEEDS_HUMAN_REVIEW,
                needs_human_review=True,
                done_reason="deep_research_unknown_status",
                deep_research_status=response_status,
            )

        report = get_response_output_text(response)
        citations = extract_citations(response)
        tool_calls = extract_tool_calls(response)
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
                "report_path": report_path,
            },
        )
        updated = self.repository.update_run(
            run.id,
            status=RunStatus.REVIEWING,
            report=report,
            deep_research_status=response_status,
            total_tool_calls=run.total_tool_calls + len(tool_calls),
        )
        return self.review_run(updated.id)

    def review_run(self, run_id: UUID) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if not run.report:
            return self.repository.update_run(
                run.id,
                status=RunStatus.NEEDS_HUMAN_REVIEW,
                needs_human_review=True,
                done_reason="missing_report_for_review",
            )

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
            return self.repository.update_run(
                run.id,
                status=RunStatus.NEEDS_HUMAN_REVIEW,
                needs_human_review=True,
                done_reason="review_schema_or_request_failed",
            )
        except (ValueError, ValidationError, RuntimeError) as error:
            self.repository.append_history_event(
                run.id,
                {"step": "review_failed", "error": repr(error)},
            )
            return self.repository.update_run(
                run.id,
                status=RunStatus.NEEDS_HUMAN_REVIEW,
                needs_human_review=True,
                done_reason="review_schema_or_request_failed",
            )

        if raw_response:
            self.artifacts.save_json(
                run.id,
                f"raw-responses/review_resp_{run.total_reviews + 1:03d}.json",
                raw_response,
            )

        review = ReviewRecord(
            **review_result.model_dump(),
            review_no=run.total_reviews + 1,
            recommended_route=review_result.verdict,
            reviewer_response_id=response_id,
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
                "estimated_cost_usd": run.estimated_cost_usd,
                "max_cost_usd": run.max_cost_usd,
                "total_tool_calls": run.total_tool_calls,
                "max_total_tool_calls": run.max_total_tool_calls,
                "contains_confidential_context": run.contains_confidential_context,
                "web_search_allowed": run.web_search_allowed,
            }
        )

        self.repository.append_history_event(
            run.id,
            {"step": "route_after_review", "route": route, "verdict": review.verdict.value},
        )

        if route == "finalize":
            final_path, _ = self.artifacts.save_text(run.id, "reports/final_report.md", run.report)
            self.repository.append_history_event(
                run.id,
                {"step": "finalized", "final_report_path": final_path},
            )
            return self.repository.update_run(
                run.id,
                status=RunStatus.COMPLETED,
                final_report=run.report,
                done_reason="passed_review",
                total_reviews=run.total_reviews + 1,
                no_progress_count=no_progress_count,
                needs_human_review=False,
            )

        if route == "human_review":
            return self.repository.update_run(
                run.id,
                status=RunStatus.NEEDS_HUMAN_REVIEW,
                needs_human_review=True,
                done_reason=f"review_route_{review.verdict.value}",
                total_reviews=run.total_reviews + 1,
                no_progress_count=no_progress_count,
            )

        updated = self.repository.update_run(
            run.id,
            status=RunStatus.REVIEWING,
            needs_human_review=False,
            done_reason=None,
            total_reviews=run.total_reviews + 1,
            no_progress_count=no_progress_count,
        )
        if route == "llm_finalize":
            return self.llm_finalize(updated.id)
        if route == "deep_research_submit":
            return self.submit_deep_research(updated.id)

        return self.repository.update_run(
            updated.id,
            status=RunStatus.NEEDS_HUMAN_REVIEW,
            needs_human_review=True,
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
            return self.repository.update_run(
                run.id,
                status=RunStatus.NEEDS_HUMAN_REVIEW,
                needs_human_review=True,
                done_reason="missing_report_for_llm_finalize",
            )
        if latest_review is None:
            return self.repository.update_run(
                run.id,
                status=RunStatus.NEEDS_HUMAN_REVIEW,
                needs_human_review=True,
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
            return self.repository.update_run(
                run.id,
                status=RunStatus.NEEDS_HUMAN_REVIEW,
                needs_human_review=True,
                done_reason="llm_finalize_failed",
            )

        if raw_response:
            self.artifacts.save_json(
                run.id,
                f"raw-responses/llm_finalize_resp_{run_no:03d}.json",
                raw_response,
            )
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
                "report_path": report_path,
            },
        )
        updated = self.repository.update_run(
            run.id,
            status=RunStatus.REVIEWING,
            needs_human_review=False,
            report=revised_report,
            llm_fix_runs=run_no,
            done_reason=None,
        )
        return self.review_run(updated.id)

    def resume_run(
        self,
        run_id: UUID,
        request: HumanReviewResumeRequest,
    ) -> ResearchRunRecord:
        run = self.repository.get_run(run_id)
        if run.status != RunStatus.NEEDS_HUMAN_REVIEW and not run.needs_human_review:
            raise ValueError("Run is not waiting for human review.")

        self.repository.append_history_event(
            run.id,
            {
                "step": "human_review_decision",
                "action": request.action.value,
                "comment": request.comment,
            },
        )

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
            self.repository.update_run(
                run.id,
                status=RunStatus.REVIEWING,
                needs_human_review=False,
                done_reason=None,
            )
            return self.llm_finalize(run.id, human_comment=request.comment)

        if request.action == HumanReviewAction.REQUEST_DEEP_RESEARCH:
            self.repository.update_run(
                run.id,
                status=RunStatus.REVIEWING,
                needs_human_review=False,
                done_reason=None,
            )
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
        return self.repository.update_run(
            run.id,
            status=RunStatus.NEEDS_HUMAN_REVIEW,
            needs_human_review=True,
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
rationale: {latest_review.rationale}
gaps: {latest_review.gaps}
factuality_concerns: {latest_review.factuality_concerns}
source_quality_concerns: {latest_review.source_quality_concerns}
next_instructions: {latest_review.next_instructions}"""
        )
        human_block = human_comment or "なし"
        return f"""# Original User Prompt
{run.user_prompt}

# Original Research Brief
{run.optimized_prompt or run.user_prompt}

# Previous Report
{run.report or ""}

# Review Result
{review_block}

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
