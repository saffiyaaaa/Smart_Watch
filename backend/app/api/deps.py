"""Shared route dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.errors import TooManyRequestsError, UnauthorizedError
from app.config import get_settings
from app.infrastructure.database.repositories import users as user_repo
from app.infrastructure.database.session import get_db
from app.infrastructure.rate_limit import RateLimitExceededError
from app.infrastructure.security import decode_access_token
from app.models.user import User

# auto_error=False so a missing header reaches our handler and produces the
# standard error envelope, rather than FastAPI's default 403 body -- and 401 is
# the correct status for "no credentials supplied", not 403.
_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    """Resolve the authenticated user, or raise 401.

    The user is loaded from the database on every request rather than trusted
    from the token body. A token stays valid until it expires, so a deleted
    account would otherwise keep working with a still-valid token.
    """
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Authentication required")

    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise UnauthorizedError("Invalid or expired token")

    user = user_repo.get_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("Invalid or expired token")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def rate_limit_auth(request: Request) -> None:
    """Applied to /auth/register and /auth/login (see app/api/routes/auth.py).

    Keyed by client IP rather than email/user id: these endpoints are the
    ones an unauthenticated caller can hit before any identity exists, so IP
    is the only key available, and it is exactly the key that matters for
    throttling a credential-stuffing or registration-spam script running
    against one address.

    The limiter instance lives on app.state (see app/main.py's create_app),
    not as a module-level singleton, so each app instance -- including a
    fresh one per test -- starts with a clean budget.
    """
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return

    client_host = request.client.host if request.client else "unknown"
    try:
        request.app.state.auth_rate_limiter.check(client_host)
    except RateLimitExceededError as exc:
        raise TooManyRequestsError(exc.retry_after_seconds) from exc
