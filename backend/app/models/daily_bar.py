from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    desc,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin


class DailyBar(Base, CreatedAtMixin):
    """One completed trading session for one symbol.

    Not in the original brief; added because section 6 of it requires a previous
    close and a 20-session average volume, and neither can be derived from
    intraday snapshots on day one -- there is no history yet. See
    docs/product-spec.md section 9.

    Only close and volume are stored. Open/high/low would be speculative: no v1
    signal reads them, and an unused column is a schema commitment with no
    payoff.
    """

    __tablename__ = "daily_bars"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)

    # DATE, not a timestamp: a trading session is a calendar day in the
    # exchange's timezone, not an instant. Storing it as a timestamp would
    # invite timezone conversion to silently move a bar to the wrong session.
    session_date: Mapped[date] = mapped_column(Date, nullable=False)

    close: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        UniqueConstraint("source", "symbol", "session_date", name="uq_daily_bars_session"),
        CheckConstraint("close > 0", name="close_positive"),
        CheckConstraint("volume IS NULL OR volume >= 0", name="volume_non_negative"),
        # Serves both baseline reads: "previous close" (first row) and
        # "trailing 20 sessions of volume" (first 20 rows).
        Index("ix_daily_bars_symbol_session_date", "symbol", desc("session_date")),
        {"comment": "Completed trading sessions, used as change-detection baselines"},
    )

    def __repr__(self) -> str:
        return f"<DailyBar {self.symbol} {self.session_date} close={self.close}>"
