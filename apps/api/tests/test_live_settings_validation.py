from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from api.config import Settings
from live_helpers import require_live_reviewer_settings


@pytest.mark.parametrize(
    "settings_kwargs",
    [
        {"research_poller_interval_seconds": 0},
        {"research_deep_research_timeout_seconds": 0},
        {"research_deep_research_submit_timeout_seconds": 0},
        {"research_deep_research_submit_stale_seconds": 0},
        {
            "research_deep_research_submit_timeout_seconds": 120,
            "research_deep_research_submit_stale_seconds": 120,
        },
        {
            "research_deep_research_submit_timeout_seconds": 121,
            "research_deep_research_submit_stale_seconds": 120,
        },
        {"research_deep_research_collecting_stale_seconds": 0},
        {"research_review_timeout_seconds": 0},
        {"research_review_max_report_chars": 0},
        {"research_review_max_citations": -1},
        {"default_max_targeted_rerun_runs": -1},
        {"default_max_total_iterations": 0},
        {"default_max_total_tool_calls": 0},
    ],
)
def test_settings_reject_invalid_runtime_bounds(
    settings_kwargs: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        Settings(research_poller_enabled=False, **settings_kwargs)


def test_settings_allow_submit_stale_after_submit_timeout() -> None:
    settings = Settings(
        research_poller_enabled=False,
        research_deep_research_submit_timeout_seconds=120,
        research_deep_research_submit_stale_seconds=121,
    )

    assert settings.research_deep_research_submit_stale_seconds == 121


def test_live_reviewer_settings_skip_on_partial_gpt_client_settings() -> None:
    settings = Settings(
        research_poller_enabled=False,
        o3_deep_research_azure_openai_endpoint="https://deep.example",
        o3_deep_research_azure_openai_key="deep-key",
        o3_deep_research_azure_openai_api_version="2025-01-01",
        gpt5_5_azure_openai_endpoint="https://reviewer.example",
        gpt5_5_azure_openai_key="reviewer-key",
        gpt5_5_azure_openai_api_version="",
        gpt5_5_azure_openai_deployment_name="gpt-5.5",
    )

    with pytest.raises(pytest.skip.Exception):
        require_live_reviewer_settings(settings)


def test_live_reviewer_settings_allow_complete_gpt_client_settings() -> None:
    settings = Settings(
        research_poller_enabled=False,
        o3_deep_research_azure_openai_endpoint="",
        o3_deep_research_azure_openai_key="",
        o3_deep_research_azure_openai_api_version="",
        gpt5_5_azure_openai_endpoint="https://reviewer.example",
        gpt5_5_azure_openai_key="reviewer-key",
        gpt5_5_azure_openai_api_version="2025-02-01",
        gpt5_5_azure_openai_deployment_name="gpt-5.5",
    )

    require_live_reviewer_settings(settings)


def test_live_reviewer_settings_allow_openai_v1_gpt_client_without_api_version() -> None:
    settings = Settings(
        research_poller_enabled=False,
        o3_deep_research_azure_openai_endpoint="",
        o3_deep_research_azure_openai_key="",
        o3_deep_research_azure_openai_api_version="",
        gpt5_5_azure_openai_endpoint="https://reviewer.example/openai/v1",
        gpt5_5_azure_openai_key="reviewer-key",
        gpt5_5_azure_openai_api_version="",
        gpt5_5_azure_openai_deployment_name="gpt-5.5",
    )

    require_live_reviewer_settings(settings)


def test_live_reviewer_settings_allow_o3_fallback_when_no_gpt_client_settings() -> None:
    settings = Settings(
        research_poller_enabled=False,
        o3_deep_research_azure_openai_endpoint="https://deep.example",
        o3_deep_research_azure_openai_key="deep-key",
        o3_deep_research_azure_openai_api_version="2025-01-01",
        o3_deep_research_azure_openai_deployment_name="",
        gpt5_5_azure_openai_endpoint="",
        gpt5_5_azure_openai_key="",
        gpt5_5_azure_openai_api_version="",
        gpt5_5_azure_openai_deployment_name="gpt-5.5",
    )

    require_live_reviewer_settings(settings)
