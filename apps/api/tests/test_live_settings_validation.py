from __future__ import annotations

import pytest

from api.config import Settings
from live_helpers import require_live_reviewer_settings


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
