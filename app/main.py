"""Application factory and ASGI entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import health
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import dispose_engine, init_engine

logger = logging.getLogger(__name__)

DESCRIPTION = """
REST API for user wallets.

* `POST /api/v1/wallets` — create a wallet
* `GET /api/v1/wallets/{uuid}` — read the current balance
* `POST /api/v1/wallets/{uuid}/operation` — deposit or withdraw

Balance changes are atomic at the database level, so concurrent operations on
the same wallet are applied correctly and can never drive a balance negative.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the engine's lifetime, so shutdown never strands connections."""
    settings: Settings = app.state.settings
    init_engine(settings)
    logger.info("application started", extra={"environment": settings.environment})
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("application stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    Takes settings as an argument so tests can construct an app against a
    throwaway database without touching the environment.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        # No custom response class: FastAPI serialises a route's return value
        # straight to JSON bytes via Pydantic whenever a response model is
        # declared, which every route here does. Handing it ORJSONResponse
        # instead would re-route each response through the slower, deprecated
        # path. Exception handlers build their responses explicitly and use
        # app.core.http.ORJSONResponse.
        # Schema endpoints can be switched off for a public deployment.
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(api_router)
    app.include_router(health.router)

    return app


app = create_app()
