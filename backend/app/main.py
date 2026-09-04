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
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.errors import register_exception_handlers
from app.api.middleware import MaxBodySizeMiddleware
from app.api.routes import auth as auth_routes
from app.api.routes import stocks as stocks_routes
from app.api.routes import watchlists as watchlist_routes
from app.config import Settings, get_settings
from app.infrastructure.database.session import engine
from app.infrastructure.rate_limit import InMemoryRateLimiter

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
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_request_body_bytes)

    # Per-app-instance, not a module-level singleton, so each instance --
    # including a fresh one per test -- starts with a clean budget. See
    # app/api/deps.py's rate_limit_auth, which reads this off app.state.
    app.state.auth_rate_limiter = InMemoryRateLimiter(
        limit=settings.auth_rate_limit_max_requests,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )

    register_exception_handlers(app)
    app.include_router(auth_routes.router)
    app.include_router(watchlist_routes.router)
    app.include_router(stocks_routes.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, Any]:
        """Liveness. Answers only "is this process running?" and must never
        touch a dependency -- otherwise a database blip would cause an
        orchestrator to kill an otherwise healthy process."""
        return {"status": "ok", "service": "smart-market-watchlist"}

    @app.get("/ready", tags=["ops"])
    def ready() -> dict[str, Any]:
        """Readiness. Reports dependency health.

        Probes the database with a lightweight SELECT 1. Returns 200 when all
        dependencies are reachable and 503 when any are not, per failure mode
        #11 in docs/product-spec.md. The provider is not checked here because
        it runs asynchronously in the worker, not in the API request path --
        a momentarily unreachable provider does not make the API unready.
        """
        checks: dict[str, str] = {}
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            logger.warning("readiness check: database unreachable: %s", exc)
            checks["database"] = "error"

        all_ok = all(v == "ok" for v in checks.values())
        status_code = 200 if all_ok else 503
        status_text = "ready" if all_ok else "degraded"
        return JSONResponse(
            content={"status": status_text, "checks": checks},
            status_code=status_code,
        )

    return app


app = create_app()
