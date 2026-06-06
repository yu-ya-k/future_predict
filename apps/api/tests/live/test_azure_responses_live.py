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
        user_prompt="Review a short public-information research result.",
        optimized_prompt=(
            "Objective: verify whether the report clearly includes claims and evidence."
        ),
        acceptance_criteria=[
            "Directly answers the user request.",
            "States evidence-backed claims and limitations.",
        ],
        research_items=[
            {
                "item_id": "RI-001",
                "criterion_id": "AC-001",
                "question": "Directly answers the user request.",
                "expected_answer_type": "fact",
                "status": "not_started",
                "severity": "major",
                "confidence": 0,
            }
        ],
        report=(
            "OpenAI is a company focused on AI research and product development. "
            "This short report is for a smoke test and is not a detailed investigation."
        ),
        citations=[],
    )

    assert review.verdict in set(Verdict)
    assert 0 <= review.score <= 100
    assert review.item_assessments
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
                "Smoke test: produce a very short English note confirming this request was "
                "received. Do not perform broad research."
            ),
            max_tool_calls=1,
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
        user_prompt="Polish a short public-information research result.",
        report=(
            "OpenAI is a company focused on AI research and product development. "
            "Limitation: smoke test only."
        ),
        review={
            "verdict": Verdict.NEEDS_LLM_PATCH.value,
            "rationale": "The wording should be made more concise.",
            "gaps": ["Slightly improve the structure."],
        },
    )

    assert report.strip()
    assert response_id
    assert raw_response
