from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI

from api.config import Settings
from api.main import create_app
from api.research.azure_responses import AzureResponsesClient
from api.research.dependencies import get_research_orchestrator
from api.research.service import ResearchOrchestrator
from research_fakes import IntegrationFakeAzure, make_integration_orchestrator


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
