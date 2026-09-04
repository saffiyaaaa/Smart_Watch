"""Shared route dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.errors import UnauthorizedError
from app.infrastructure.database.repositories import users as user_repo
from app.infrastructure.database.session import get_db
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
