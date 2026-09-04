"""FastAPI application entrypoint.

Deliberately thin: an app factory, CORS, and the two operational endpoints.
Domain logic never lives here.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.routes import auth as auth_routes
from app.api.routes import watchlists as watchlist_routes
from app.config import Settings, get_settings

logger = logging.getLogger("smw.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    logger.info(
        "starting environment=%s provider=%s",
        settings.environment,
        settings.market_provider,
    )
    yield
    logger.info("shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Smart Market Watchlist",
        version="0.1.0",
        description=(
            "Surfaces what meaningfully changed in a watchlist since a user last "
            "checked, with evidence. See docs/product-spec.md."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(auth_routes.router)
    app.include_router(watchlist_routes.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, Any]:
        """Liveness. Answers only "is this process running?" and must never
        touch a dependency -- otherwise a database blip would cause an
        orchestrator to kill an otherwise healthy process."""
        return {"status": "ok", "service": "smart-market-watchlist"}

    @app.get("/ready", tags=["ops"])
    def ready() -> dict[str, Any]:
        """Readiness. Reports dependency health.

        Phase 1 has no dependencies wired yet, so this reports the shape of the
        eventual response with an empty check set. Phase 9 fills in the database
        and provider checks and returns 503 when degraded.
        """
        return {"status": "ready", "checks": {}}

    return app


app = create_app()
