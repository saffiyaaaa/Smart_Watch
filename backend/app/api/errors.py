"""Domain exceptions and the single error response format.

Every failure leaves the API in the same shape:

    {"error": {"code": "...", "message": "...", "details": {...}}}

A consistent envelope means the frontend has one error path rather than one per
endpoint, and a machine-readable `code` lets it react to specific conditions
without string-matching human prose that may later be reworded.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("smw.errors")

# Literal rather than status.HTTP_422_*: Starlette renamed the constant from
# UNPROCESSABLE_ENTITY to UNPROCESSABLE_CONTENT, so either name breaks on some
# supported version. The number has not changed since RFC 4918.
HTTP_422 = 422


class AppError(Exception):
    """Base class for expected, described failures."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(message)


class NotFoundError(AppError):
    """The resource does not exist, or is not visible to this caller.

    Deliberately covers both. Returning 403 for "exists but not yours" would
    confirm that someone else's id is real, which is an information leak in a
    system where ids appear in URLs.
    """

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ValidationError(AppError):
    status_code = HTTP_422
    code = "validation_error"


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"


class TooManyRequestsError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"

    def __init__(self, retry_after_seconds: float) -> None:
        # Ceil, not round: a client that obeys Retry-After exactly must never
        # retry a moment too early and get rate-limited a second time.
        self.retry_after_seconds = max(1, int(retry_after_seconds) + 1)
        super().__init__("Too many requests", {"retry_after_seconds": self.retry_after_seconds})


class RequestTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "request_too_large"


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers: dict[str, str] | None = None
        if isinstance(exc, UnauthorizedError):
            headers = {"WWW-Authenticate": "Bearer"}
        elif isinstance(exc, TooManyRequestsError):
            headers = {"Retry-After": str(exc.retry_after_seconds)}
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=HTTP_422,
            content=error_body(
                "validation_error",
                "Request validation failed",
                # jsonable-safe: the raw errors can contain exception objects.
                {"fields": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        headers = getattr(exc, "headers", None)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(_code_for_status(exc.status_code), str(exc.detail)),
            headers=headers,
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        """A constraint stopped a write that the service layer expected to work.

        This is the safety net behind the idempotency handling, not the primary
        mechanism. It returns 409 rather than 500 because a violated uniqueness
        constraint is a conflict with existing data, not a server fault.
        """
        logger.warning("integrity error: %s", exc.orig)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=error_body("conflict", "The request conflicts with existing data"),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _database_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        """Database trouble becomes a controlled 503, never a false success.

        The exception text is logged but not returned: it can contain the
        connection string, table structure and row values.
        """
        logger.error("database error", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error_body("service_unavailable", "The service is temporarily unavailable"),
        )


def _code_for_status(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        422: "validation_error",
        429: "rate_limited",
        503: "service_unavailable",
    }.get(status_code, "error")
