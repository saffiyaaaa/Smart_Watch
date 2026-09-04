from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserSeenState(Base):
    """Where one user's attention has reached in one watchlist.

    This table is the entire reason the product can answer "since *you* last
    checked". The alternative -- a `seen` flag on the event itself -- collapses
    two concepts that must stay apart: an event is something the *system*
    observed, seen state is something a *user* did. With a flag on the event,
    two users could not have different views of the same market, which is the
    single most important property of the product.

    The composite primary key means one row per user per watchlist, enforced by
    the storage engine rather than by application care.
    """

    __tablename__ = "user_seen_state"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    watchlist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("watchlists.id", ondelete="CASCADE"), primary_key=True
    )

    # The cursor. Advances forward only -- enforced with GREATEST() in the
    # upsert (see repositories/seen_state.py), not with a read-then-compare in
    # Python, which would lose the race between two browser tabs.
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Which event the client actually rendered. SET NULL rather than CASCADE:
    # if an event is ever removed, the user's cursor must survive. Deleting
    # their seen state would make every past event look new again.
    last_seen_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("change_events.id", ondelete="SET NULL"), nullable=True
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<UserSeenState user={self.user_id} watchlist={self.watchlist_id}>"
