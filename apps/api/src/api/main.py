import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.config import get_settings
from api.forecast.router import router as forecast_router
from api.research.dependencies import get_research_orchestrator
from api.research.poller import ResearchPoller
from api.research.router import router as research_router

log = logging.getLogger(__name__)


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
    # Use explicitly configured origins when available; fall back to the
    # development-oriented cors_origins list otherwise.
    allowed_origins = app_settings.cors_allowed_origins or app_settings.cors_origins
    # allow_credentials=True is only safe when origins are a non-empty explicit
    # list, never with a wildcard.  A wildcard ("*") in the list disables
    # credentials to avoid the forbidden allow_origins=["*"] + credentials combo.
    allow_credentials = bool(allowed_origins) and "*" not in allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        # Re-raise HTTPException so FastAPI's own handler processes it normally.
        if isinstance(exc, (HTTPException, StarletteHTTPException)):
            raise exc
        log.exception(
            "Unhandled exception for %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal server error occurred."},
        )

    app.add_exception_handler(Exception, _unhandled_exception_handler)  # type: ignore[arg-type]

    app.include_router(forecast_router)
    app.include_router(research_router)
    app.add_api_route("/health", health, methods=["GET"])
    return app


def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "env": settings.app_env}


app = create_app()
