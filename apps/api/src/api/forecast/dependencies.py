from __future__ import annotations

from functools import lru_cache

from api.config import get_settings
from api.forecast.artifacts import ForecastArtifactStore
from api.forecast.repository import ForecastRepository
from api.forecast.service import ForecastOrchestrator
from api.research.dependencies import get_research_orchestrator


@lru_cache
def get_forecast_repository() -> ForecastRepository:
    return ForecastRepository(get_settings().research_db_path)


@lru_cache
def get_forecast_artifact_store() -> ForecastArtifactStore:
    return ForecastArtifactStore(get_settings().forecast_artifact_dir)


@lru_cache
def get_forecast_orchestrator() -> ForecastOrchestrator:
    return ForecastOrchestrator(
        settings=get_settings(),
        repository=get_forecast_repository(),
        artifacts=get_forecast_artifact_store(),
        research_orchestrator=get_research_orchestrator(),
    )


def clear_forecast_dependency_caches() -> None:
    get_forecast_repository.cache_clear()
    get_forecast_artifact_store.cache_clear()
    get_forecast_orchestrator.cache_clear()

