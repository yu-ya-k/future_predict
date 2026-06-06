from __future__ import annotations

from functools import lru_cache

from api.config import get_settings
from api.research.artifacts import ArtifactStore
from api.research.azure_responses import AzureResponsesClient
from api.research.repository import ResearchRepository
from api.research.service import ResearchOrchestrator


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


def clear_research_dependency_caches() -> None:
    get_research_repository.cache_clear()
    get_artifact_store.cache_clear()
    get_azure_responses_client.cache_clear()
    get_research_orchestrator.cache_clear()
