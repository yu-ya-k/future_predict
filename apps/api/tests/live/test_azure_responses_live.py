from __future__ import annotations

import pytest

from api.config import Settings
from api.research.azure_responses import AzureResponsesClient
from api.research.extractors import get_response_id, get_response_status
from api.research.schemas import Verdict
from live_helpers import require_live_deep_research_settings, require_live_reviewer_settings

TERMINAL_DEEP_RESEARCH_STATUSES = {"completed", "failed", "cancelled", "incomplete"}


def _cleanup_live_deep_research_response(
    client: AzureResponsesClient,
    *,
    response_id: str,
    last_known_status: str | None,
) -> None:
    if last_known_status in TERMINAL_DEEP_RESEARCH_STATUSES:
        return

    try:
        cancel_result = client.cancel_response(response_id)
    except Exception as cancel_error:
        try:
            latest = client.retrieve_response(response_id)
        except Exception as retrieve_error:
            raise AssertionError(
                f"Failed to cancel live Deep Research response {response_id}; "
                f"follow-up retrieve also failed with {retrieve_error!r}."
            ) from cancel_error

        latest_status = get_response_status(latest)
        if latest_status in TERMINAL_DEEP_RESEARCH_STATUSES:
            return

        raise AssertionError(
            f"Failed to cancel live Deep Research response {response_id}; "
            f"latest status after cancel failure is {latest_status!r}."
        ) from cancel_error

    assert cancel_result is not None


@pytest.mark.live_api
def test_live_reviewer_structured_output_smoke(
    live_settings: Settings,
    live_azure_client: AzureResponsesClient,
) -> None:
    require_live_reviewer_settings(live_settings)

    review, response_id, raw_response = live_azure_client.review_report(
        user_prompt="公開情報に基づく短い調査結果をレビューしてください。",
        optimized_prompt="目的: レポートが主張と根拠を明確に含むか確認する。",
        acceptance_criteria=[
            "ユーザー要求に直接答えている",
            "根拠がある主張と限界が明示されている",
        ],
        report=(
            "OpenAI は AI 研究と製品開発を行う企業である。"
            "この短いレポートは smoke test 用であり、詳細調査ではない。"
        ),
        citations=[],
        web_search_enabled=False,
    )

    assert review.verdict in set(Verdict)
    assert 0 <= review.score <= 100
    assert isinstance(review.can_be_fixed_by_llm, bool)
    assert isinstance(review.requires_new_external_research, bool)
    assert response_id
    assert raw_response


@pytest.mark.live_api
def test_live_deep_research_submit_retrieve_cancel_smoke(
    live_settings: Settings,
    live_azure_client: AzureResponsesClient,
) -> None:
    require_live_deep_research_settings(live_settings)
    response_id: str | None = None
    latest_status: str | None = None

    try:
        response = live_azure_client.submit_deep_research(
            prompt=(
                "Smoke test: produce a very short Japanese note confirming this request was "
                "received. Do not perform broad research."
            ),
            max_tool_calls=1,
            web_search_enabled=False,
            context_classification="public",
            contains_confidential_context=False,
            web_search_allowed=False,
        )
        response_id = get_response_id(response)
        assert response_id
        latest_status = get_response_status(response)
        assert latest_status in {"queued", "in_progress", "completed"}

        retrieved = live_azure_client.retrieve_response(response_id)
        assert get_response_id(retrieved) == response_id
        latest_status = get_response_status(retrieved)
        assert latest_status in {
            "queued",
            "in_progress",
            "completed",
            "failed",
            "cancelled",
            "incomplete",
        }
    finally:
        if response_id:
            _cleanup_live_deep_research_response(
                live_azure_client,
                response_id=response_id,
                last_known_status=latest_status,
            )


@pytest.mark.live_api
def test_live_reviewer_finalize_smoke(
    live_settings: Settings,
    live_azure_client: AzureResponsesClient,
) -> None:
    require_live_reviewer_settings(live_settings)

    report, response_id, raw_response = live_azure_client.finalize_report(
        user_prompt="公開情報に基づく短い調査結果を整えてください。",
        report="OpenAI は AI 研究と製品開発を行う企業である。限界: smoke test 用。",
        review={
            "verdict": Verdict.NEEDS_LLM_FIX.value,
            "rationale": "表現を簡潔に整える必要がある。",
            "gaps": ["構成を少し整える"],
        },
        web_search_enabled=False,
    )

    assert report.strip()
    assert response_id
    assert raw_response
