from __future__ import annotations

import logging
from functools import lru_cache
from secrets import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException, status

from api.config import get_settings
from api.research.artifacts import ArtifactStore
from api.research.azure_responses import AzureResponsesClient
from api.research.repository import ResearchRepository
from api.research.service import ResearchOrchestrator

log = logging.getLogger(__name__)

ApiKeyHeader = Annotated[str | None, Header(alias="X-API-Key")]
AuthorizationHeader = Annotated[str | None, Header()]


@lru_cache
def get_research_repository() -> ResearchRepository:
    settings = get_settings()
    return ResearchRepository(settings.research_db_path)


@lru_cache
def get_artifact_store() -> ArtifactStore:
    settings = get_settings()
    return ArtifactStore(settings.research_artifact_dir)


@lru_cache
def get_azure_responses_client() -> AzureResponsesClient:
    return AzureResponsesClient(get_settings())


@lru_cache
def get_research_orchestrator() -> ResearchOrchestrator:
    return ResearchOrchestrator(
        settings=get_settings(),
        repository=get_research_repository(),
        artifacts=get_artifact_store(),
        azure=get_azure_responses_client(),
    )


def require_research_api_key(
    x_api_key: ApiKeyHeader = None,
    authorization: AuthorizationHeader = None,
) -> None:
    settings = get_settings()
    expected_key = settings.research_api_key.strip()
    if not expected_key:
        if settings.app_env.strip().lower() != "development":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "API authentication is not configured. "
                    "Set RESEARCH_API_KEY to a non-empty value."
                ),
            )
        log.warning(
            "RESEARCH_API_KEY is not set — authentication is disabled. "
            "This is only acceptable in development."
        )
        return
    expected_key_bytes = expected_key.encode("utf-8")

    candidates: list[str] = []
    if x_api_key:
        candidates.append(x_api_key)

    bearer_token = _bearer_token(authorization)
    if bearer_token:
        candidates.append(bearer_token)

    if any(
        compare_digest(candidate.encode("utf-8"), expected_key_bytes)
        for candidate in candidates
    ):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing research API key.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None

    scheme, separator, token = authorization.partition(" ")
    if separator == "" or scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def clear_research_dependency_caches() -> None:
    get_research_repository.cache_clear()
    get_artifact_store.cache_clear()
    get_azure_responses_client.cache_clear()
    get_research_orchestrator.cache_clear()
