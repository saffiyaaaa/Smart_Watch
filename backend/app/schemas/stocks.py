"""Response schemas for symbol-level market data endpoints.

Freshness is computed at read time (not stored here) by the route handler
using the same classify_freshness function the worker uses. The two values
both appear in QuoteResponse because they answer different questions:

  ingest_freshness  -- was this quote ever fresh? (immutable fact about
                       the observation; set when the worker wrote it)
  freshness         -- is it fresh *right now*? (recomputed every request)

Serving both lets the frontend decide how to present each case -- e.g. a
quote that was FRESH when ingested but is now STALE because the market was
closed for the weekend is different from one that was STALE on arrival.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class QuoteResponse(BaseModel):
    """The latest known market observation for one symbol, with live freshness."""

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    price: Decimal
    volume: int | None
    market_timestamp: datetime
    # Freshness computed right now, against the correct reference point
    # (last session close while market is closed, current time while open).
    freshness: str
    # Freshness when the worker ingested this observation. Immutable.
    ingest_freshness: str


class DailyBarResponse(BaseModel):
    """One completed trading session."""

    model_config = ConfigDict(from_attributes=True)

    session_date: date
    close: Decimal
    volume: int | None
