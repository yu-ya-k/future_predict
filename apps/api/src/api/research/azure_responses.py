from __future__ import annotations

import json
from typing import Any

from openai import APITimeoutError, AzureOpenAI, OpenAI, OpenAIError
from pydantic import ValidationError

from api.config import Settings
from api.research.extractors import (
    get_response_id,
    get_response_output_text,
    response_to_jsonable,
)
from api.research.schemas import REVIEW_RESULT_SCHEMA, ReviewResult

_CITATION_COMPACT_KEYS = (
    "source_type",
    "title",
    "url",
    "file_id",
    "filename",
    "start_index",
    "end_index",
)


class ReviewResponseParseError(ValueError):
    def __init__(self, message: str, raw_response: dict[str, Any]) -> None:
        super().__init__(message)
        self.raw_response = raw_response


class ReviewRequestTimeout(RuntimeError):
    pass


class AzureResponsesClient:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self._deep_research_client = client
        self._reviewer_client = client

    @property
    def client(self) -> Any:
        return self.deep_research_client

    @property
    def deep_research_client(self) -> Any:
        if self._deep_research_client is None:
            self._deep_research_client = _build_azure_client(
                endpoint=self.settings.o3_deep_research_azure_openai_endpoint,
                api_key=self.settings.o3_deep_research_azure_openai_key,
                api_version=self.settings.o3_deep_research_azure_openai_api_version,
            )
        return self._deep_research_client

    @property
    def reviewer_client(self) -> Any:
        if self._reviewer_client is None:
            reviewer_values = (
                self.settings.gpt5_5_azure_openai_endpoint,
                self.settings.gpt5_5_azure_openai_key,
                self.settings.gpt5_5_azure_openai_api_version,
            )

            if not any(reviewer_values):
                self._reviewer_client = self.deep_research_client
                return self._reviewer_client

            try:
                self._reviewer_client = _build_azure_client(
                    endpoint=self.settings.gpt5_5_azure_openai_endpoint,
                    api_key=self.settings.gpt5_5_azure_openai_key,
                    api_version=self.settings.gpt5_5_azure_openai_api_version,
                )
            except RuntimeError as error:
                raise RuntimeError("Reviewer Azure OpenAI settings are incomplete.") from error
        return self._reviewer_client

    @property
    def deep_research_deployment(self) -> str:
        return self.settings.o3_deep_research_azure_openai_deployment_name

    @property
    def reviewer_deployment(self) -> str:
        return self.settings.gpt5_5_azure_openai_deployment_name

    def submit_deep_research(
        self,
        *,
        prompt: str,
        max_tool_calls: int,
    ) -> Any:
        return self.deep_research_client.responses.create(
            model=self.deep_research_deployment,
            background=True,
            input=prompt,
            tools=[{"type": "web_search_preview"}],
            max_tool_calls=max_tool_calls,
        )

    def retrieve_response(self, response_id: str) -> Any:
        return self.deep_research_client.responses.retrieve(response_id)

    def cancel_response(self, response_id: str) -> Any:
        return self.deep_research_client.responses.cancel(response_id)

    def review_report(
        self,
        *,
        user_prompt: str,
        optimized_prompt: str,
        acceptance_criteria: list[str],
        report: str,
        citations: list[dict[str, Any]],
        research_items: list[dict[str, Any]] | None = None,
    ) -> tuple[ReviewResult, str | None, dict[str, Any]]:
        prompt = build_review_prompt(
            user_prompt=user_prompt,
            optimized_prompt=optimized_prompt,
            acceptance_criteria=acceptance_criteria,
            research_items=research_items or [],
            report=report,
            citations=citations,
            max_report_chars=self.settings.research_review_max_report_chars,
            max_citations=self.settings.research_review_max_citations,
        )
        tools = [{"type": "web_search"}] if self.settings.research_review_web_search_enabled else []
        request_client = _client_with_timeout(
            self.reviewer_client,
            timeout=self.settings.research_review_timeout_seconds,
        )

        parse_method = getattr(request_client.responses, "parse", None)
        if callable(parse_method):
            try:
                request: dict[str, Any] = {
                    "model": self.reviewer_deployment,
                    "input": prompt,
                    "text_format": ReviewResult,
                }
                if tools:
                    request["tools"] = tools
                response = parse_method(
                    **request,
                )
                parsed = getattr(response, "output_parsed", None)
                if isinstance(parsed, ReviewResult):
                    return parsed, get_response_id(response), response_to_jsonable(response)
            except APITimeoutError as error:
                raise ReviewRequestTimeout(
                    "Reviewer structured output request timed out."
                ) from error
            except (
                TypeError,
                AttributeError,
                ValidationError,
                OpenAIError,
            ):
                pass

        try:
            request = {
                "model": self.reviewer_deployment,
                "input": prompt,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "review_result",
                        "schema": REVIEW_RESULT_SCHEMA,
                        "strict": True,
                    }
                },
            }
            if tools:
                request["tools"] = tools
            response = request_client.responses.create(**request)
        except APITimeoutError as error:
            raise ReviewRequestTimeout("Reviewer structured output request timed out.") from error
        except OpenAIError as error:
            raise RuntimeError("Reviewer structured output request failed.") from error

        raw_response = response_to_jsonable(response)
        output_text = get_response_output_text(response)
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError as error:
            raise ReviewResponseParseError(
                "Reviewer returned invalid JSON.", raw_response
            ) from error

        try:
            review_result = ReviewResult.model_validate(payload)
        except ValidationError as error:
            raise ReviewResponseParseError(
                "Reviewer response did not match ReviewResult schema.",
                raw_response,
            ) from error

        return review_result, get_response_id(response), raw_response

    def finalize_report(
        self,
        *,
        user_prompt: str,
        report: str,
        review: dict[str, Any],
        enable_web_search: bool = True,
    ) -> tuple[str, str | None, dict[str, Any]]:
        tools = [{"type": "web_search"}] if enable_web_search else []
        request: dict[str, Any] = {
            "model": self.reviewer_deployment,
            "input": build_finalize_prompt(
                user_prompt=user_prompt,
                report=report,
                review=review,
            ),
        }
        if tools:
            request["tools"] = tools
        response = self.reviewer_client.responses.create(
            **request,
        )
        output_text = get_response_output_text(response)
        return output_text, get_response_id(response), response_to_jsonable(response)


def build_review_prompt(
    *,
    user_prompt: str,
    optimized_prompt: str,
    acceptance_criteria: list[str],
    research_items: list[dict[str, Any]] | None = None,
    report: str,
    citations: list[dict[str, Any]],
    max_report_chars: int = 50000,
    max_citations: int = 40,
) -> str:
    report, omitted_report_chars = _truncate_text(report, max_chars=max_report_chars)
    citations, omitted_citation_count = _compact_citations(
        citations,
        max_citations=max_citations,
    )
    context_note = ""
    if omitted_report_chars or omitted_citation_count:
        context_note = f"""
# Review Context Limits
- omitted_report_chars: {omitted_report_chars}
- omitted_citation_count: {omitted_citation_count}
"""
    return f"""You are a strict research quality reviewer.

Objective:
Evaluate whether the candidate report satisfies the original user prompt,
the optimized prompt, and the acceptance criteria.

Review criteria:
- How well the report achieves the objective in the original user prompt.
- How completely the report satisfies the expected output items in the optimized prompt
  and acceptance criteria.
- Whether each ResearchItem is answered, partially answered, unanswered, unverifiable,
  or out of scope.
- Whether key claims are backed by trustworthy sources.
- Whether facts that require recency are current enough.
- Whether numbers, proper nouns, dates, model names, and policy names are accurate.
- Whether official, primary, and otherwise reliable sources are prioritized.
- Whether conclusions overreach beyond the cited evidence.
- Whether uncertainty, limitations, and assumptions are explicit.
- Whether the topic is too high-risk for automated continuation.

Output policy:
- Write all ReviewResult string fields in English, even if the user prompt or
  candidate report is in another language.
- In rationale, include objective coverage, expected-output coverage, and the overall judgment.
- For every ResearchItem, return one item_assessment with status, severity,
  failure_mode, failure_mode_confidence, recommended_action, missing_evidence,
  evidence_summary, and rationale.
- Use needs_llm_patch only for format, organization, wording, or in-report content that
  was lost in synthesis.
- Use needs_verification for narrow factuality, freshness, or contradiction checks.
- Use needs_targeted_rerun for missing item evidence requiring additional research.
- Use needs_full_rerun only when the contract or initial report is systemically unusable.
- Use finalize_with_limitation when unresolved non-blocking items should be disclosed
  rather than rerun again.
- Use null for next_instructions only when no automated follow-up is needed.

verdict policy:
- pass: The report satisfies the objective and has no major gaps, errors, or source issues.
- needs_llm_patch: The report is mostly sufficient and can be fixed without new research.
- needs_verification: A narrow public-safe verification step is needed.
- needs_targeted_rerun: One or more unresolved ResearchItems need additional Deep Research.
- needs_full_rerun: The whole research attempt is unusable or the contract is defective.
- needs_item_revision: The ResearchItems are ambiguous or need human-approved revision.
- finalize_with_limitation: The report can finish with explicit limitations.
- human_review: The topic is high-risk, unclear, or automated continuation is inappropriate.

Return a response that strictly conforms to the ReviewResult schema.
{context_note}

# User Prompt
{user_prompt}

# Optimized Prompt
{optimized_prompt}

# Acceptance Criteria
{json.dumps(acceptance_criteria, ensure_ascii=False)}

# Research Items
{json.dumps(research_items or [], ensure_ascii=False)}

# Candidate Report
{report}

# Citations
{json.dumps(citations, ensure_ascii=False)}
"""


def build_finalize_prompt(
    *,
    user_prompt: str,
    report: str,
    review: dict[str, Any],
) -> str:
    return f"""You are an expert research editor.

Revise the existing report only enough to address the minor gaps identified in the review.
Do not add unsupported new information, fabricate citations, or expand the scope substantially.
Write the final report in English, even if the user prompt or existing report is in
another language.

# User Prompt
{user_prompt}

# Review Result
{json.dumps(review, ensure_ascii=False)}

# Existing Report
{report}
"""


def _build_azure_client(*, endpoint: str, api_key: str, api_version: str) -> Any:
    if not endpoint or not api_key:
        raise RuntimeError("Azure OpenAI settings are incomplete.")

    if endpoint.rstrip("/").endswith("/openai/v1"):
        return OpenAI(base_url=endpoint.rstrip("/") + "/", api_key=api_key)

    if not api_version:
        raise RuntimeError("Azure OpenAI api_version is required for AzureOpenAI client.")

    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )


def _client_with_timeout(client: Any, *, timeout: int) -> Any:
    with_options = getattr(client, "with_options", None)
    if callable(with_options):
        return with_options(timeout=timeout)
    return client


def _truncate_text(value: str, *, max_chars: int) -> tuple[str, int]:
    if max_chars <= 0 or len(value) <= max_chars:
        return value, 0

    marker = "\n\n[... review input truncated ...]\n\n"
    available = max(max_chars - len(marker), 0)
    head_chars = max(available * 2 // 3, 0)
    tail_chars = max(available - head_chars, 0)
    tail = value[-tail_chars:] if tail_chars else ""
    truncated = value[:head_chars] + marker + tail
    return truncated, len(value) - len(truncated)


def _compact_citations(
    citations: list[dict[str, Any]],
    *,
    max_citations: int,
) -> tuple[list[dict[str, Any]], int]:
    if max_citations <= 0:
        return [], len(citations)

    compacted: list[dict[str, Any]] = []
    for citation in citations[:max_citations]:
        compacted.append(
            {
                key: _truncate_scalar(citation[key])
                for key in _CITATION_COMPACT_KEYS
                if key in citation and citation[key] not in (None, "")
            }
        )
    return compacted, max(len(citations) - len(compacted), 0)


def _truncate_scalar(value: Any, *, max_chars: int = 1000) -> Any:
    if not isinstance(value, str) or len(value) <= max_chars:
        return value
    return value[: max_chars - 1] + "…"
