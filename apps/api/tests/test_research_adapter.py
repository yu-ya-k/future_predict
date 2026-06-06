from __future__ import annotations

import json
from typing import Any

import pytest
from openai import OpenAIError

from api.config import Settings
from api.research import azure_responses
from api.research.azure_responses import AzureResponsesClient
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


def _settings() -> Settings:
    return Settings(research_poller_enabled=False)


def _valid_review_payload() -> str:
    return json.dumps(
        {
            "verdict": "pass",
            "goal_achieved": True,
            "score": 91,
            "rationale": "sufficient",
            "gaps": [],
            "factuality_concerns": [],
            "source_quality_concerns": [],
            "next_instructions": None,
            "can_be_fixed_by_llm": False,
            "requires_new_external_research": False,
            "reviewer_confidence": 88,
            "high_risk_flags": [],
            "public_web_search_used": False,
        }
    )


def test_deep_research_submit_applies_public_tool_policy() -> None:
    fake_client = _FakeClient()
    client = AzureResponsesClient(settings=_settings(), client=fake_client)

    client.submit_deep_research(prompt="public research", max_tool_calls=10)
    blocked_calls: list[dict[str, Any]] = [
        {"prompt": "public research", "max_tool_calls": 10, "web_search_allowed": False},
        {
            "prompt": "public research",
            "max_tool_calls": 10,
            "context_classification": "internal",
            "web_search_enabled": True,
        },
        {"prompt": "public research", "max_tool_calls": 10, "context_classification": "mixed"},
        {"prompt": "社外秘の戦略を調査", "max_tool_calls": 10},
    ]
    for kwargs in blocked_calls:
        with pytest.raises(ValueError, match="requires an enabled public web search"):
            client.submit_deep_research(**kwargs)

    tools_by_call = [call["tools"] for call in fake_client.responses.create_calls]
    assert tools_by_call == [[{"type": "web_search_preview"}]]


def test_review_report_falls_back_to_strict_schema_after_parse_api_error() -> None:
    fake_client = _FakeClient()
    fake_client.responses.parse_error = OpenAIError("parse helper unavailable")
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
        web_search_enabled=False,
    )

    assert review.verdict == "pass"
    assert response_id == "resp_strict"
    assert raw_response["id"] == "resp_strict"
    assert fake_client.responses.parse_calls
    assert fake_client.responses.create_calls


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
