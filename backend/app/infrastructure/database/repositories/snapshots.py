"""Market snapshot persistence.

APPEND-ONLY BY CONSTRUCTION.

This module offers no update and no delete function. That is the enforcement
mechanism, not an oversight: "we agreed not to mutate snapshots" is a convention
that erodes the first time someone is in a hurry, whereas a function that does
not exist cannot be called. A reviewer can confirm the guarantee by reading the
list of public names here.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.enums import Freshness
from app.models.market_snapshot import MarketSnapshot


def insert_snapshot(
    db: Session,
    *,
    source: str,
    symbol: str,
    price: Decimal,
    volume: int | None,
    market_timestamp: datetime,
    ingest_freshness: Freshness,
) -> MarketSnapshot | None:
    """Insert one observation, ignoring it if already recorded.

    Returns the created row, or None when this exact observation was already
    stored. That return value is meaningful: the caller uses it to decide
    whether to run change detection, so re-ingesting an old quote does not
    regenerate events.

    Idempotency comes from uq_market_snapshots_observation. ON CONFLICT DO
    NOTHING pushes the race into PostgreSQL, which resolves it correctly even
    across separate worker processes.
    """
    stmt = (
        insert(MarketSnapshot)
        .values(
            source=source,
            symbol=symbol,
            price=price,
            volume=volume,
            market_timestamp=market_timestamp,
            ingest_freshness=ingest_freshness.value,
        )
        .on_conflict_do_nothing(constraint="uq_market_snapshots_observation")
        .returning(MarketSnapshot)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_latest(db: Session, symbol: str) -> MarketSnapshot | None:
    """Most recent observation for a symbol.

    Ordered by market_timestamp, never by id. Arrival order is not truth: a
    provider can deliver an older quote after a newer one, and ordering by the
    autoincrement key would let that stale value become "latest".

    fetched_at is the tie-breaker, per docs/product-spec.md section 8 row 6:
    two sources may legitimately report the identical market_timestamp with
    different prices (see get_other_sources_at below), and market_timestamp
    alone does not order those two rows -- without a second key, which one
    "wins" as latest is whatever order PostgreSQL happens to return equal
    values in, not the documented "most recent fetched_at wins for display"
    rule.
    """
    stmt = (
        select(MarketSnapshot)
        .where(MarketSnapshot.symbol == symbol)
        .order_by(MarketSnapshot.market_timestamp.desc(), MarketSnapshot.fetched_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def get_latest_for_symbols(db: Session, symbols: list[str]) -> dict[str, MarketSnapshot]:
    """Latest observation for many symbols in one query.

    DISTINCT ON is PostgreSQL-specific and deliberate. The portable alternative
    (a GROUP BY subquery joined back to the table) reads the table twice; this
    walks ix_market_snapshots_symbol_market_timestamp once and takes the first
    row per symbol. It also keeps a watchlist render at one round trip instead
    of one per symbol.

    fetched_at breaks a market_timestamp tie here too, for the same reason as
    get_latest above -- DISTINCT ON keeps the first row per symbol under this
    ORDER BY, so the tie-breaker has to be part of it, not bolted on after.
    """
    if not symbols:
        return {}

    stmt = (
        select(MarketSnapshot)
        .where(MarketSnapshot.symbol.in_(symbols))
        .distinct(MarketSnapshot.symbol)
        .order_by(
            MarketSnapshot.symbol,
            MarketSnapshot.market_timestamp.desc(),
            MarketSnapshot.fetched_at.desc(),
        )
    )
    return {row.symbol: row for row in db.execute(stmt).scalars()}


def get_history(
    db: Session,
    symbol: str,
    *,
    limit: int = 100,
    since: datetime | None = None,
) -> list[MarketSnapshot]:
    stmt = select(MarketSnapshot).where(MarketSnapshot.symbol == symbol)
    if since is not None:
        stmt = stmt.where(MarketSnapshot.market_timestamp >= since)
    stmt = stmt.order_by(MarketSnapshot.market_timestamp.desc()).limit(limit)
    return list(db.execute(stmt).scalars())


def get_at_or_before(db: Session, symbol: str, moment: datetime) -> MarketSnapshot | None:
    """The observation in force at a given instant -- the price a user would
    have seen had they looked then."""
    stmt = (
        select(MarketSnapshot)
        .where(MarketSnapshot.symbol == symbol, MarketSnapshot.market_timestamp <= moment)
        .order_by(MarketSnapshot.market_timestamp.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def get_other_sources_at(
    db: Session, *, symbol: str, market_timestamp: datetime, exclude_source: str
) -> list[MarketSnapshot]:
    """Every recorded observation for this symbol at this exact instant, from
    a source other than `exclude_source` -- the raw material for conflict
    detection (docs/product-spec.md section 2).

    With a single active provider this returns nothing in production, which
    is expected and documented: it exists so that adding a second provider
    later is a configuration change, not a redesign.
    """
    stmt = select(MarketSnapshot).where(
        MarketSnapshot.symbol == symbol,
        MarketSnapshot.market_timestamp == market_timestamp,
        MarketSnapshot.source != exclude_source,
    )
    return list(db.execute(stmt).scalars())
