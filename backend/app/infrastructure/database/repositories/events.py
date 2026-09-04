"""Change event persistence."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.enums import EventType, Severity
from app.models.change_event import ChangeEvent
from app.models.watchlist_item import WatchlistItem


def upsert_event(
    db: Session,
    *,
    symbol: str,
    trading_day: date,
    event_type: EventType,
    score: int,
    severity: Severity,
    evidence: list[str],
    price_pct: Decimal | None,
    volume_ratio: Decimal | None,
    confidence: Decimal,
) -> ChangeEvent | None:
    """Record a meaningful change, escalating an existing one for the session.

    One row per (symbol, trading_day). A re-run that produces an equal or lower
    score changes nothing and returns None -- so running the worker twice, or
    running two workers, cannot duplicate or downgrade a user's feed.

    When the score rises, detected_at is refreshed. That deliberately re-surfaces
    the event to users who already saw it: a 3% move that became a 9% move is new
    information, and silently updating the row in place would hide it.

    The escalation test lives in the WHERE clause of ON CONFLICT rather than in
    Python. Read-then-compare would let two concurrent workers both read the old
    score and both decide they are the higher one.
    """
    stmt = (
        insert(ChangeEvent)
        .values(
            symbol=symbol,
            trading_day=trading_day,
            event_type=event_type.value,
            score=score,
            severity=severity.value,
            evidence=evidence,
            price_pct=price_pct,
            volume_ratio=volume_ratio,
            confidence=confidence,
        )
        .on_conflict_do_update(
            constraint="uq_change_events_symbol_trading_day",
            set_={
                "event_type": event_type.value,
                "score": score,
                "severity": severity.value,
                "evidence": evidence,
                "price_pct": price_pct,
                "volume_ratio": volume_ratio,
                "confidence": confidence,
                "detected_at": func.now(),
            },
            where=ChangeEvent.score < score,
        )
        .returning(ChangeEvent)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_events_for_watchlist(
    db: Session,
    *,
    watchlist_id: uuid.UUID,
    since: datetime | None,
    min_score: int,
    limit: int,
) -> list[ChangeEvent]:
    """The change feed.

    Symbols are resolved with a subquery against watchlist_items rather than
    being loaded into Python and passed back as a list, which would cost an
    extra round trip and cap out on IN-list size for a large watchlist.

    Ordered by score first, then recency: this is an attention feed, so the most
    important item leads even if something less important happened afterwards.
    """
    symbols = select(WatchlistItem.symbol).where(WatchlistItem.watchlist_id == watchlist_id)

    stmt = select(ChangeEvent).where(
        ChangeEvent.symbol.in_(symbols),
        ChangeEvent.score >= min_score,
    )
    if since is not None:
        stmt = stmt.where(ChangeEvent.detected_at > since)

    stmt = stmt.order_by(ChangeEvent.score.desc(), ChangeEvent.detected_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars())


def get_events_for_symbol(db: Session, symbol: str, *, limit: int = 50) -> list[ChangeEvent]:
    stmt = (
        select(ChangeEvent)
        .where(ChangeEvent.symbol == symbol)
        .order_by(ChangeEvent.detected_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())
