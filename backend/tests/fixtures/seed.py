"""Deterministic test data.

Every value is fixed. Nothing here reads the wall clock or a random source: a
test that fails only on a Monday, or only when a random price lands on a
threshold boundary, costs more to diagnose than the realism was ever worth.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.enums import EventType, Freshness, Severity
from app.models.change_event import ChangeEvent
from app.models.daily_bar import DailyBar
from app.models.market_snapshot import MarketSnapshot
from app.models.user import User
from app.models.watchlist import Watchlist
from app.models.watchlist_item import WatchlistItem

# A Wednesday, so "previous session" arithmetic never crosses a weekend
# unintentionally in tests that do not care about weekends.
FIXED_NOW = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)
FIXED_SESSION = date(2026, 3, 11)
PREVIOUS_SESSION = date(2026, 3, 10)

SOURCE = "mock"


def make_user(db: Session, *, email: str = "alice@example.com") -> User:
    user = User(email=email, password_hash="$2b$12$fake.hash.for.tests")
    db.add(user)
    db.flush()
    return user


def make_watchlist(db: Session, *, user: User, name: str = "Tech") -> Watchlist:
    watchlist = Watchlist(user_id=user.id, name=name)
    db.add(watchlist)
    db.flush()
    return watchlist


def add_item(db: Session, *, watchlist: Watchlist, symbol: str) -> WatchlistItem:
    item = WatchlistItem(watchlist_id=watchlist.id, symbol=symbol)
    db.add(item)
    db.flush()
    return item


def make_snapshot(
    db: Session,
    *,
    symbol: str = "AAPL",
    price: str = "180.00",
    volume: int | None = 50_000_000,
    market_timestamp: datetime | None = None,
    fetched_at: datetime | None = None,
    source: str = SOURCE,
    freshness: Freshness = Freshness.FRESH,
) -> MarketSnapshot:
    snapshot = MarketSnapshot(
        source=source,
        symbol=symbol,
        price=Decimal(price),
        volume=volume,
        market_timestamp=market_timestamp or FIXED_NOW,
        ingest_freshness=freshness.value,
    )
    if fetched_at is not None:
        snapshot.fetched_at = fetched_at
    db.add(snapshot)
    db.flush()
    return snapshot


def make_daily_bar(
    db: Session,
    *,
    symbol: str = "AAPL",
    session_date: date | None = None,
    close: str = "175.00",
    volume: int | None = 40_000_000,
    source: str = SOURCE,
) -> DailyBar:
    bar = DailyBar(
        source=source,
        symbol=symbol,
        session_date=session_date or PREVIOUS_SESSION,
        close=Decimal(close),
        volume=volume,
    )
    db.add(bar)
    db.flush()
    return bar


def make_volume_history(
    db: Session, *, symbol: str = "AAPL", sessions: int = 20, volume: int = 40_000_000
) -> list[DailyBar]:
    """A flat run of sessions ending the day before FIXED_SESSION.

    Flat on purpose: with a constant baseline the expected 20-day average is
    exactly `volume`, so a volume-ratio assertion can be written as an exact
    number rather than an approximation.
    """
    bars = []
    for i in range(1, sessions + 1):
        bars.append(
            make_daily_bar(
                db,
                symbol=symbol,
                session_date=FIXED_SESSION - timedelta(days=i),
                close="175.00",
                volume=volume,
            )
        )
    return bars


def make_event(
    db: Session,
    *,
    symbol: str = "AAPL",
    trading_day: date | None = None,
    score: int = 60,
    severity: Severity = Severity.IMPORTANT,
    event_type: EventType = EventType.PRICE_MOVE,
    evidence: list[str] | None = None,
    detected_at: datetime | None = None,
    confidence: str = "1.000",
) -> ChangeEvent:
    event = ChangeEvent(
        symbol=symbol,
        trading_day=trading_day or FIXED_SESSION,
        event_type=event_type.value,
        score=score,
        severity=severity.value,
        evidence=evidence if evidence is not None else ["Price +5.0% vs previous close"],
        confidence=Decimal(confidence),
        price_pct=Decimal("5.0"),
        volume_ratio=None,
    )
    if detected_at is not None:
        event.detected_at = detected_at
    db.add(event)
    db.flush()
    return event


def unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.com"
