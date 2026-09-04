from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.watchlist_item import WatchlistItem


class Watchlist(Base, CreatedAtMixin):
    __tablename__ = "watchlists"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # ondelete CASCADE lives in the database, not only in the ORM relationship.
    # The worker and any future admin tooling talk to the same database without
    # going through this ORM session, and integrity must not depend on which
    # client issued the delete.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    user: Mapped[User] = relationship(back_populates="watchlists")
    items: Mapped[list[WatchlistItem]] = relationship(
        back_populates="watchlist",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # Two watchlists called "Tech" under one account is a data-entry
        # mistake, not a feature.
        UniqueConstraint("user_id", "name", name="uq_watchlists_user_id_name"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        # PostgreSQL does not index a foreign key automatically. Without this,
        # "list my watchlists" is a sequential scan, and every cascading delete
        # of a user has to scan the whole table.
        Index("ix_watchlists_user_id", "user_id"),
        {"comment": "User-created lists of symbols"},
    )

    def __repr__(self) -> str:
        return f"<Watchlist {self.name!r} user={self.user_id}>"
