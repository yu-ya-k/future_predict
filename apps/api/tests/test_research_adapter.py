from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from openai import APITimeoutError, OpenAIError

from api.config import Settings
from api.research import azure_responses
from api.research.azure_responses import (
    AzureResponsesClient,
    ReviewRequestTimeout,
    build_review_prompt,
)
from api.research.extractors import extract_citations, extract_tool_calls


class _Response:
    def __init__(
        self,
        *,
        response_id: str = "resp_1",
        output_text: str = "",
        output_parsed: object | None = None,
    ) -> None:
        self.id = response_id
        self.status = "completed"
        self.output_text = output_text
        self.output_parsed = output_parsed


class _FakeResponses:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.parse_calls: list[dict[str, Any]] = []
        self.parse_error: Exception | None = None
        self.create_response = _Response(response_id="resp_create")

    def create(self, **kwargs: Any) -> _Response:
        self.create_calls.append(kwargs)
        return self.create_response

    def parse(self, **kwargs: Any) -> _Response:
        self.parse_calls.append(kwargs)
        if self.parse_error is not None:
            raise self.parse_error
        return _Response(response_id="resp_parse")


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()
        self.with_options_calls: list[dict[str, Any]] = []

    def with_options(self, **kwargs: Any) -> _FakeClient:
        self.with_options_calls.append(kwargs)
        return self


def _settings() -> Settings:
    return Settings(research_poller_enabled=False)


def _valid_review_payload() -> str:
    return json.dumps(
        {
            "verdict": "pass",
            "goal_achieved": True,
            "score": 91,
            "rationale": "sufficient",
            "item_assessments": [
                {
                    "item_id": "RI-001",
                    "status": "answered",
                    "severity": "major",
                    "failure_mode": "none",
                    "failure_mode_confidence": 90,
                    "recommended_action": "none",
                    "evidence_summary": "covered",
                    "missing_evidence": [],
                    "rationale": "covered",
                }
            ],
            "gaps": [],
            "factuality_concerns": [],
            "source_quality_concerns": [],
            "freshness_concerns": [],
            "security_concerns": [],
            "next_instructions": None,
            "reviewer_confidence": 88,
            "high_risk_flags": [],
            "public_web_search_used": False,
            "route_rationale": "pass",
        }
    )


def test_deep_research_submit_always_includes_public_web_search_tool() -> None:
    fake_client = _FakeClient()
    client = AzureResponsesClient(settings=_settings(), client=fake_client)

    client.submit_deep_research(prompt="public research", max_tool_calls=10)
    client.submit_deep_research(prompt="社外秘の戦略を調査", max_tool_calls=10)

    tools_by_call = [call["tools"] for call in fake_client.responses.create_calls]
    assert tools_by_call == [[{"type": "web_search_preview"}]] * 2


def test_review_report_falls_back_to_strict_schema_after_parse_helper_error() -> None:
    fake_client = _FakeClient()
    fake_client.responses.parse_error = TypeError("parse helper unavailable")
    fake_client.responses.create_response = _Response(
        response_id="resp_strict",
        output_text=_valid_review_payload(),
    )
    client = AzureResponsesClient(settings=_settings(), client=fake_client)

    review, response_id, raw_response = client.review_report(
        user_prompt="prompt",
        optimized_prompt="optimized",
        acceptance_criteria=[],
        report="report",
        citations=[],
    )

    assert review.verdict == "pass"
    assert response_id == "resp_strict"
    assert raw_response["id"] == "resp_strict"
    assert fake_client.with_options_calls == [{"timeout": 180}]
    assert fake_client.responses.parse_calls
    assert fake_client.responses.create_calls
    assert "tools" not in fake_client.responses.parse_calls[0]
    assert "tools" not in fake_client.responses.create_calls[0]


def test_review_report_falls_back_to_strict_schema_after_parse_api_error() -> None:
    fake_client = _FakeClient()
    fake_client.responses.parse_error = OpenAIError("service unavailable")
    fake_client.responses.create_response = _Response(
        response_id="resp_strict",
        output_text=_valid_review_payload(),
    )
    client = AzureResponsesClient(settings=_settings(), client=fake_client)

    review, response_id, raw_response = client.review_report(
        user_prompt="prompt",
        optimized_prompt="optimized",
        acceptance_criteria=[],
        report="report",
        citations=[],
    )

    assert review.verdict == "pass"
    assert response_id == "resp_strict"
    assert raw_response["id"] == "resp_strict"
    assert fake_client.responses.parse_calls
    assert fake_client.responses.create_calls


def test_review_report_does_not_fallback_after_parse_timeout() -> None:
    fake_client = _FakeClient()
    fake_client.responses.parse_error = APITimeoutError(
        request=httpx.Request("POST", "https://example.test/responses")
    )
    client = AzureResponsesClient(settings=_settings(), client=fake_client)

    with pytest.raises(ReviewRequestTimeout):
        client.review_report(
            user_prompt="prompt",
            optimized_prompt="optimized",
            acceptance_criteria=[],
            report="report",
            citations=[],
        )

    assert fake_client.responses.parse_calls
    assert fake_client.responses.create_calls == []


def test_review_report_can_enable_web_search_by_setting() -> None:
    fake_client = _FakeClient()
    fake_client.responses.parse_error = TypeError("parse helper unavailable")
    fake_client.responses.create_response = _Response(
        response_id="resp_strict",
        output_text=_valid_review_payload(),
    )
    client = AzureResponsesClient(
        settings=Settings(
            research_poller_enabled=False,
            research_review_web_search_enabled=True,
        ),
        client=fake_client,
    )

    client.review_report(
        user_prompt="prompt",
        optimized_prompt="optimized",
        acceptance_criteria=[],
        report="report",
        citations=[],
    )

    assert fake_client.responses.parse_calls[0]["tools"] == [{"type": "web_search"}]
    assert fake_client.responses.create_calls[0]["tools"] == [{"type": "web_search"}]


def test_review_prompt_compacts_large_report_and_citations() -> None:
    prompt = build_review_prompt(
        user_prompt="prompt",
        optimized_prompt="optimized",
        acceptance_criteria=[],
        report="A" * 200,
        citations=[
            {
                "title": f"title {index}",
                "url": f"https://example.com/{index}",
                "irrelevant": "x" * 200,
            }
            for index in range(5)
        ],
        max_report_chars=80,
        max_citations=2,
    )

    assert "[... review input truncated ...]" in prompt
    assert "omitted_report_chars" in prompt
    assert "omitted_citation_count: 3" in prompt
    assert "title 0" in prompt
    assert "title 2" not in prompt
    assert "irrelevant" not in prompt


def test_finalize_report_always_includes_web_search_tool() -> None:
    fake_client = _FakeClient()
    fake_client.responses.create_response = _Response(
        response_id="resp_finalize",
        output_text="final report",
    )
    client = AzureResponsesClient(settings=_settings(), client=fake_client)

    report, response_id, raw_response = client.finalize_report(
        user_prompt="prompt",
        report="draft",
        review={"rationale": "tighten wording"},
    )

    assert report == "final report"
    assert response_id == "resp_finalize"
    assert raw_response["id"] == "resp_finalize"
    assert fake_client.responses.create_calls[0]["tools"] == [{"type": "web_search"}]


def test_reviewer_client_uses_gpt_settings_when_present(monkeypatch: Any) -> None:
    created: list[tuple[str, str, str]] = []
    deep_marker = object()
    reviewer_marker = object()

    def fake_build_client(*, endpoint: str, api_key: str, api_version: str) -> object:
        created.append((endpoint, api_key, api_version))
        return reviewer_marker if endpoint == "https://reviewer.example" else deep_marker

    monkeypatch.setattr(azure_responses, "_build_azure_client", fake_build_client)
    client = AzureResponsesClient(
        settings=Settings(
            research_poller_enabled=False,
            o3_deep_research_azure_openai_endpoint="https://deep.example",
            o3_deep_research_azure_openai_key="deep-key",
            o3_deep_research_azure_openai_api_version="2025-01-01",
            gpt5_5_azure_openai_endpoint="https://reviewer.example",
            gpt5_5_azure_openai_key="reviewer-key",
            gpt5_5_azure_openai_api_version="2025-02-01",
        )
    )

    assert client.deep_research_client is deep_marker
    assert client.reviewer_client is reviewer_marker
    assert created == [
        ("https://deep.example", "deep-key", "2025-01-01"),
        ("https://reviewer.example", "reviewer-key", "2025-02-01"),
    ]


def test_reviewer_client_accepts_openai_v1_gpt_settings_without_api_version(
    monkeypatch: Any,
) -> None:
    created: list[tuple[str, str, str]] = []
    reviewer_marker = object()

    def fake_build_client(*, endpoint: str, api_key: str, api_version: str) -> object:
        created.append((endpoint, api_key, api_version))
        return reviewer_marker

    monkeypatch.setattr(azure_responses, "_build_azure_client", fake_build_client)
    client = AzureResponsesClient(
        settings=Settings(
            research_poller_enabled=False,
            gpt5_5_azure_openai_endpoint="https://reviewer.example/openai/v1",
            gpt5_5_azure_openai_key="reviewer-key",
            gpt5_5_azure_openai_api_version="",
        )
    )

    assert client.reviewer_client is reviewer_marker
    assert created == [
        ("https://reviewer.example/openai/v1", "reviewer-key", ""),
    ]


def test_reviewer_client_rejects_partial_non_v1_gpt_settings() -> None:
    client = AzureResponsesClient(
        settings=Settings(
            research_poller_enabled=False,
            gpt5_5_azure_openai_endpoint="https://reviewer.example",
            gpt5_5_azure_openai_key="reviewer-key",
            gpt5_5_azure_openai_api_version="",
        )
    )

    with pytest.raises(RuntimeError, match="Reviewer Azure OpenAI settings are incomplete"):
        _ = client.reviewer_client


def test_extractors_handle_action_query_and_citation_variants() -> None:
    response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "report",
                        "annotations": [
                            {
                                "type": "file_citation",
                                "file_id": "file_123",
                                "filename": "source.csv",
                                "start_index": 0,
                                "end_index": 4,
                            },
                            {
                                "uri": "https://example.com/source",
                                "title": "Source without type",
                            },
                        ],
                    }
                ],
            },
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {
                    "query": "actual search query",
                    "url": "https://search.example",
                },
            },
        ]
    }

    citations = extract_citations(response)
    tool_calls = extract_tool_calls(response)

    assert [citation.source_type for citation in citations] == ["file_citation", "citation"]
    assert citations[0].title == "source.csv"
    assert citations[1].url == "https://example.com/source"
    assert tool_calls[0].query == "actual search query"
    assert tool_calls[0].url == "https://search.example"
