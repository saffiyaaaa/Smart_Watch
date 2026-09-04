from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.watchlist.symbols import SYMBOL_REGEX
from app.models.base import Base, CreatedAtMixin

if TYPE_CHECKING:
    from app.models.watchlist import Watchlist


class WatchlistItem(Base, CreatedAtMixin):
    __tablename__ = "watchlist_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    watchlist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False
    )

    symbol: Mapped[str] = mapped_column(String(10), nullable=False)

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")

    __table_args__ = (
        # THE constraint that makes add-symbol idempotent under concurrency.
        #
        # The tempting alternative is "SELECT, and INSERT if absent" in the
        # service layer. That loses the race: two requests can both SELECT
        # nothing, then both INSERT. No amount of application care fixes it,
        # because the gap between the two statements is where the race lives.
        # A UNIQUE index makes the duplicate impossible at the storage layer,
        # and lets the service use ON CONFLICT DO NOTHING to make the second
        # caller's request a successful no-op rather than an error.
        UniqueConstraint("watchlist_id", "symbol", name="uq_watchlist_items_watchlist_id_symbol"),
        # Also serves the (watchlist_id) prefix for "list items in a watchlist",
        # so no separate index on watchlist_id is needed.
        CheckConstraint(f"symbol ~ '{SYMBOL_REGEX}'", name="symbol_format"),
        {"comment": "Symbols belonging to a watchlist"},
    )

    def __repr__(self) -> str:
        return f"<WatchlistItem {self.symbol} watchlist={self.watchlist_id}>"
