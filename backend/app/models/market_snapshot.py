from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    desc,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import Freshness
from app.models.base import Base

_FRESHNESS_VALUES = ", ".join(f"'{f.value}'" for f in Freshness)


class MarketSnapshot(Base):
    """One validated observation from a provider. Immutable and append-only.

    There is deliberately no `updated_at`, no soft-delete flag and no mutable
    column on this table. A snapshot is a claim that a price held at a moment in
    time; that claim cannot later become untrue, so nothing here should ever be
    rewritten. The repository enforces this by offering no update or delete
    method at all -- see infrastructure/database/repositories/snapshots.py.
    """

    __tablename__ = "market_snapshots"

    # BIGINT identity rather than UUID: this is the highest-volume table in the
    # system, it is never addressed by URL, and monotonic keys keep inserts at
    # the right-hand edge of the index instead of scattering random pages.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)

    # NUMERIC, never float. Binary floating point cannot represent 0.1 exactly,
    # and percentage-change comparisons against thresholds are precisely where
    # that error would decide whether a user is alerted.
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)

    # Nullable: some providers omit volume. NULL means "not reported", which is
    # different from 0 ("reported as no trades"). Scoring treats them
    # differently, so the schema must be able to tell them apart.
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # When the market says the price held.
    market_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When we asked. Never substituted for market_timestamp -- that substitution
    # is exactly how stale data gets laundered into fresh data.
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Freshness at the moment of ingestion: an immutable fact about the
    # observation. Display freshness is recomputed at read time, because
    # "is this current?" has a different answer every second.
    ingest_freshness: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        # THE constraint that makes ingestion idempotent. Identity of an
        # observation is (source, symbol, market_timestamp): fetching the same
        # quote twice observes one fact twice, it does not create two facts.
        # Lets the worker use ON CONFLICT DO NOTHING and stay safe when retried,
        # run twice, or run as two instances.
        UniqueConstraint(
            "source", "symbol", "market_timestamp", name="uq_market_snapshots_observation"
        ),
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint("volume IS NULL OR volume >= 0", name="volume_non_negative"),
        CheckConstraint(
            f"ingest_freshness IN ({_FRESHNESS_VALUES})", name="ingest_freshness_valid"
        ),
        # The read path: "latest known state for this symbol" and "history for
        # this symbol". DESC matches the ORDER BY so PostgreSQL can walk the
        # index backwards and stop at the first row instead of sorting.
        Index(
            "ix_market_snapshots_symbol_market_timestamp",
            "symbol",
            desc("market_timestamp"),
        ),
        {"comment": "Immutable observations from market data providers"},
    )

    def __repr__(self) -> str:
        return f"<MarketSnapshot {self.symbol} {self.price} @ {self.market_timestamp}>"
