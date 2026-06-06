from __future__ import annotations

import pytest

from api.config import Settings


def _is_openai_v1_endpoint(endpoint: str) -> bool:
    return endpoint.rstrip("/").endswith("/openai/v1")


def _has_complete_client_settings(*, endpoint: str, api_key: str, api_version: str) -> bool:
    if not endpoint or not api_key:
        return False
    return bool(api_version) or _is_openai_v1_endpoint(endpoint)


def require_live_deep_research_settings(settings: Settings) -> None:
    if not settings.o3_deep_research_azure_openai_endpoint:
        pytest.skip("O3_DEEP_RESEARCH_AZURE_OPENAI_ENDPOINT is required.")
    if not settings.o3_deep_research_azure_openai_key:
        pytest.skip("O3_DEEP_RESEARCH_AZURE_OPENAI_KEY is required.")
    if not settings.o3_deep_research_azure_openai_deployment_name:
        pytest.skip("O3_DEEP_RESEARCH_AZURE_OPENAI_DEPLOYMENT_NAME is required.")
    if (
        not _is_openai_v1_endpoint(settings.o3_deep_research_azure_openai_endpoint)
        and not settings.o3_deep_research_azure_openai_api_version
    ):
        pytest.skip("O3_DEEP_RESEARCH_AZURE_OPENAI_API_VERSION is required.")


def require_live_reviewer_settings(settings: Settings) -> None:
    reviewer_endpoint = settings.gpt5_5_azure_openai_endpoint
    reviewer_key = settings.gpt5_5_azure_openai_key
    reviewer_api_version = settings.gpt5_5_azure_openai_api_version
    has_any_reviewer_client_setting = any(
        (reviewer_endpoint, reviewer_key, reviewer_api_version)
    )
    has_complete_reviewer_client_settings = _has_complete_client_settings(
        endpoint=reviewer_endpoint,
        api_key=reviewer_key,
        api_version=reviewer_api_version,
    )

    if has_any_reviewer_client_setting and not has_complete_reviewer_client_settings:
        pytest.skip(
            "GPT5_5_AZURE_OPENAI_ENDPOINT and GPT5_5_AZURE_OPENAI_KEY are required; "
            "GPT5_5_AZURE_OPENAI_API_VERSION is also required unless the endpoint "
            "ends with /openai/v1."
        )

    if not settings.gpt5_5_azure_openai_deployment_name:
        pytest.skip("GPT5_5_AZURE_OPENAI_DEPLOYMENT_NAME is required.")

    if has_complete_reviewer_client_settings:
        return

    if not settings.o3_deep_research_azure_openai_endpoint:
        pytest.skip("O3_DEEP_RESEARCH_AZURE_OPENAI_ENDPOINT is required for reviewer fallback.")
    if not settings.o3_deep_research_azure_openai_key:
        pytest.skip("O3_DEEP_RESEARCH_AZURE_OPENAI_KEY is required for reviewer fallback.")
    if (
        not _is_openai_v1_endpoint(settings.o3_deep_research_azure_openai_endpoint)
        and not settings.o3_deep_research_azure_openai_api_version
    ):
        pytest.skip("O3_DEEP_RESEARCH_AZURE_OPENAI_API_VERSION is required for reviewer fallback.")
