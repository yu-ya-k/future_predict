from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import get_settings
from api.research.dependencies import get_research_orchestrator
from api.research.poller import ResearchPoller
from api.research.router import router as research_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    poller: ResearchPoller | None = None
    if settings.research_poller_enabled:
        poller = ResearchPoller(
            orchestrator=get_research_orchestrator(),
            interval_seconds=settings.research_poller_interval_seconds,
        )
        poller.start()
        app.state.research_poller = poller

    try:
        yield
    finally:
        if poller is not None:
            await poller.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Future Predict API", lifespan=lifespan)

    app_settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(research_router)
    app.add_api_route("/health", health, methods=["GET"])
    return app


def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "env": settings.app_env}


app = create_app()
