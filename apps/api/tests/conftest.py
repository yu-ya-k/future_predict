from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI

from api.config import Settings
from api.forecast.artifacts import ForecastArtifactStore
from api.forecast.dependencies import get_forecast_orchestrator
from api.forecast.repository import ForecastRepository
from api.forecast.service import ForecastOrchestrator
from api.main import create_app
from api.research.artifacts import ArtifactStore
from api.research.azure_responses import AzureResponsesClient
from api.research.dependencies import get_research_orchestrator
from api.research.repository import ResearchRepository
from api.research.service import ResearchOrchestrator
from research_fakes import IntegrationFakeAzure, make_integration_orchestrator


@dataclass(frozen=True)
class ForecastResearchIntegrationStack:
    settings: Settings
    fake_azure: IntegrationFakeAzure
    research: ResearchOrchestrator
    forecast: ForecastOrchestrator
    app: FastAPI


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def integration_fake_azure() -> IntegrationFakeAzure:
    return IntegrationFakeAzure()


@pytest.fixture
def integration_orchestrator_factory(
    tmp_path: Path,
) -> Callable[[IntegrationFakeAzure], ResearchOrchestrator]:
    def make(fake: IntegrationFakeAzure) -> ResearchOrchestrator:
        return make_integration_orchestrator(tmp_path, fake)

    return make


@pytest.fixture
def integration_orchestrator(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
    integration_fake_azure: IntegrationFakeAzure,
) -> ResearchOrchestrator:
    return integration_orchestrator_factory(integration_fake_azure)


@pytest.fixture
def integration_app(integration_orchestrator: ResearchOrchestrator) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: integration_orchestrator
    return app


@pytest.fixture
def forecast_research_integration_stack_factory(
    tmp_path: Path,
) -> Callable[[IntegrationFakeAzure | None], ForecastResearchIntegrationStack]:
    stack_index = 0

    def make(fake: IntegrationFakeAzure | None = None) -> ForecastResearchIntegrationStack:
        nonlocal stack_index
        stack_index += 1
        stack_path = tmp_path / f"forecast-research-{stack_index}"
        fake_azure = fake or IntegrationFakeAzure()
        settings = Settings(
            research_db_path=stack_path / "forecast-research.sqlite3",
            research_artifact_dir=stack_path / "research-artifacts",
            forecast_enabled=True,
            forecast_artifact_dir=stack_path / "forecast-artifacts",
            research_poller_enabled=False,
            research_review_web_search_enabled=False,
        )
        research = ResearchOrchestrator(
            settings=settings,
            repository=ResearchRepository(settings.research_db_path),
            artifacts=ArtifactStore(settings.research_artifact_dir),
            azure=cast(AzureResponsesClient, fake_azure),
        )
        forecast = ForecastOrchestrator(
            settings=settings,
            repository=ForecastRepository(settings.research_db_path),
            artifacts=ForecastArtifactStore(settings.forecast_artifact_dir),
            research_orchestrator=research,
        )
        app = create_app()
        app.dependency_overrides[get_research_orchestrator] = lambda: research
        app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
        return ForecastResearchIntegrationStack(
            settings=settings,
            fake_azure=fake_azure,
            research=research,
            forecast=forecast,
            app=app,
        )

    return make


@pytest.fixture
def forecast_research_integration_stack(
    forecast_research_integration_stack_factory: Callable[
        [IntegrationFakeAzure | None],
        ForecastResearchIntegrationStack,
    ],
) -> ForecastResearchIntegrationStack:
    return forecast_research_integration_stack_factory(None)


@pytest.fixture
def live_settings(tmp_path: Path) -> Settings:
    if os.getenv("RESEARCH_LIVE_API_TESTS") != "1":
        pytest.skip("Set RESEARCH_LIVE_API_TESTS=1 to run real API tests.")

    return Settings(
        research_db_path=tmp_path / "live-research.sqlite3",
        research_artifact_dir=tmp_path / "live-artifacts",
        research_poller_enabled=False,
    )


@pytest.fixture
def live_azure_client(live_settings: Settings) -> AzureResponsesClient:
    return AzureResponsesClient(live_settings)
