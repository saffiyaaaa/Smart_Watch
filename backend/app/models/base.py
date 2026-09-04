"""Declarative base and shared column conventions.

The naming convention is not cosmetic. Without it PostgreSQL invents constraint
names, Alembic autogenerate cannot match an existing constraint to its model, and
migrations start emitting spurious drop/create pairs. Deterministic names also let
a test assert on a specific constraint by name.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CreatedAtMixin:
    """Timezone-aware creation timestamp, clocked by the database.

    The database is the single clock. Application servers drift, and once the API
    runs as more than one instance there is no such thing as "the" app clock.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
