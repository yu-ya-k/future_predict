from __future__ import annotations

import json
from typing import Any

from openai import AzureOpenAI, OpenAI, OpenAIError
from pydantic import ValidationError

from api.config import Settings
from api.research.extractors import (
    get_response_id,
    get_response_output_text,
    response_to_jsonable,
)
from api.research.schemas import REVIEW_RESULT_SCHEMA, ContextClassification, ReviewResult
from api.research.security import (
    contains_confidential_text,
    should_enable_deep_research_web_search,
)


class ReviewResponseParseError(ValueError):
    def __init__(self, message: str, raw_response: dict[str, Any]) -> None:
        super().__init__(message)
        self.raw_response = raw_response


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
        web_search_enabled: bool | None = None,
        context_classification: ContextClassification = "public",
        contains_confidential_context: bool | None = None,
        web_search_allowed: bool = True,
    ) -> Any:
        confidential_detected = (
            contains_confidential_text(prompt)
            if contains_confidential_context is None
            else contains_confidential_context
        )
        policy_allows_web_search = should_enable_deep_research_web_search(
            context_classification=context_classification,
            contains_confidential_context=confidential_detected,
            web_search_allowed=web_search_allowed,
        )
        web_search_enabled = (
            policy_allows_web_search
            if web_search_enabled is None
            else web_search_enabled and policy_allows_web_search
        )
        if not web_search_enabled:
            raise ValueError("Deep Research requires an enabled public web search tool.")

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
        web_search_enabled: bool,
    ) -> tuple[ReviewResult, str | None, dict[str, Any]]:
        prompt = build_review_prompt(
            user_prompt=user_prompt,
            optimized_prompt=optimized_prompt,
            acceptance_criteria=acceptance_criteria,
            report=report,
            citations=citations,
        )
        tools = [{"type": "web_search"}] if web_search_enabled else []

        parse_method = getattr(self.reviewer_client.responses, "parse", None)
        if callable(parse_method):
            try:
                response = parse_method(
                    model=self.reviewer_deployment,
                    input=prompt,
                    tools=tools,
                    text_format=ReviewResult,
                )
                parsed = getattr(response, "output_parsed", None)
                if isinstance(parsed, ReviewResult):
                    return parsed, get_response_id(response), response_to_jsonable(response)
            except (
                TypeError,
                AttributeError,
                ValidationError,
                OpenAIError,
            ):
                pass

        try:
            response = self.reviewer_client.responses.create(
                model=self.reviewer_deployment,
                input=prompt,
                tools=tools,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "review_result",
                        "schema": REVIEW_RESULT_SCHEMA,
                        "strict": True,
                    }
                },
            )
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
        web_search_enabled: bool,
    ) -> tuple[str, str | None, dict[str, Any]]:
        tools = [{"type": "web_search"}] if web_search_enabled else []
        response = self.reviewer_client.responses.create(
            model=self.reviewer_deployment,
            input=build_finalize_prompt(
                user_prompt=user_prompt,
                report=report,
                review=review,
            ),
            tools=tools,
        )
        output_text = get_response_output_text(response)
        return output_text, get_response_id(response), response_to_jsonable(response)


def build_review_prompt(
    *,
    user_prompt: str,
    optimized_prompt: str,
    acceptance_criteria: list[str],
    report: str,
    citations: list[dict[str, Any]],
) -> str:
    return f"""あなたは厳格なリサーチ品質レビューアです。

目的:
ユーザーの元プロンプト、optimized prompt、acceptance criteria に照らし、
候補レポートが目的を達成しているか評価してください。

確認観点:
- ユーザーの要求項目に漏れがないか
- 主要な主張に信頼できる出典があるか
- 最新性が必要な事実が古くないか
- 数値、固有名詞、日付、モデル名、制度名が正確か
- 公式情報、一次情報、信頼できるソースが優先されているか
- 出典から結論が過剰に飛躍していないか
- 不確実性、限界、前提が明示されているか
- 機密情報や高リスク領域が含まれていないか

verdict policy:
- pass: 目的を達成し、重大な不足、誤り、出典問題がない。
- needs_llm_fix: 概ね十分。軽微な不足、構成、表現、限定的事実確認のみで直せる。
- needs_deep_research: 重大な欠落、調査範囲不足、ソース不足、矛盾、多段調査が必要。
- human_review: 高リスク、機密懸念、判断不能、または自動継続が不適切。

必ず ReviewResult schema に厳密準拠して返してください。

# User Prompt
{user_prompt}

# Optimized Prompt
{optimized_prompt}

# Acceptance Criteria
{json.dumps(acceptance_criteria, ensure_ascii=False)}

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
    return f"""あなたは熟練のリサーチ・エディタです。

既存レポートをベースに、レビューで指摘された軽微な不足のみを補ってください。
根拠のない新情報、出典捏造、大きな論点追加は禁止です。

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
