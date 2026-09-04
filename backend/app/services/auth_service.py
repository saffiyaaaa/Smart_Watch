"""Registration and login orchestration."""

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import ConflictError, UnauthorizedError
from app.infrastructure.database.repositories import users as user_repo
from app.infrastructure.security import (
    burn_password_time,
    create_access_token,
    hash_password,
    verify_password,
)
from app.models.user import User

logger = logging.getLogger("smw.auth")


def register(db: Session, *, email: str, password: str) -> User:
    """Create an account.

    No "does this email exist" pre-check. SELECT-then-INSERT leaves a window in
    which two concurrent registrations both see nothing and both proceed; the
    unique index is the real guard, so the IntegrityError is caught and
    translated instead.
    """
    try:
        with db.begin_nested():
            user = user_repo.create_user(db, email=email, password_hash=hash_password(password))
    except IntegrityError as exc:
        raise ConflictError("An account with that email already exists") from exc

    logger.info("user registered id=%s", user.id)
    return user


def login(db: Session, *, email: str, password: str) -> tuple[User, str]:
    """Authenticate and issue an access token.

    Both failure paths -- unknown email and wrong password -- return the same
    message. Saying "no such user" would turn the login form into a tool for
    discovering which addresses are registered.
    """
    user = user_repo.get_by_email(db, email)

    if user is None:
        # Spend the same CPU time a real verification would, so the response
        # latency does not reveal whether the account exists.
        burn_password_time()
        raise UnauthorizedError("Incorrect email or password")

    if not verify_password(password, user.password_hash):
        raise UnauthorizedError("Incorrect email or password")

    return user, create_access_token(user.id)
