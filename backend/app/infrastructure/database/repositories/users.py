"""User persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


def normalize_email(email: str) -> str:
    """Single definition of email identity.

    Lives here so that registration, login and lookup cannot disagree about
    whether "Bob@Example.com " and "bob@example.com" are the same account. A
    mismatch would let a user register twice and then fail to log in.
    """
    return email.strip().lower()


def create_user(db: Session, *, email: str, password_hash: str) -> User:
    """Insert a user. Raises IntegrityError if the email is taken.

    No pre-check for an existing email: SELECT-then-INSERT leaves a window in
    which a concurrent request can register the same address. The unique index
    is the real guard, and the service layer converts the resulting
    IntegrityError into a 409.
    """
    user = User(email=normalize_email(email), password_hash=password_hash)
    db.add(user)
    db.flush()
    return user


def get_by_email(db: Session, email: str) -> User | None:
    stmt = select(User).where(User.email == normalize_email(email))
    return db.execute(stmt).scalars().first()


def get_by_id(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)
