"""Daily bar persistence -- the source of change-detection baselines."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.daily_bar import DailyBar


def upsert_bar(
    db: Session,
    *,
    source: str,
    symbol: str,
    session_date: date,
    close: Decimal,
    volume: int | None,
) -> DailyBar:
    """Record a completed session, overwriting an existing row for that date.

    Unlike snapshots, bars are upserted rather than insert-only. A snapshot is a
    claim about an instant and can never become untrue; a daily bar is a summary
    of a session that providers legitimately revise -- a bar fetched before the
    close is provisional, and exchanges restate volume after settlement. Refusing
    the correction would pin the baseline to a known-wrong value.
    """
    stmt = (
        insert(DailyBar)
        .values(
            source=source,
            symbol=symbol,
            session_date=session_date,
            close=close,
            volume=volume,
        )
        .on_conflict_do_update(
            constraint="uq_daily_bars_session",
            set_={"close": close, "volume": volume},
        )
        .returning(DailyBar)
    )
    return db.execute(stmt).scalar_one()


def get_previous_close(db: Session, symbol: str, before: date) -> DailyBar | None:
    """The price baseline: the last completed session before `before`.

    Strictly before, so an in-progress session cannot be its own baseline (which
    would make every percentage change zero).
    """
    stmt = (
        select(DailyBar)
        .where(DailyBar.symbol == symbol, DailyBar.session_date < before)
        .order_by(DailyBar.session_date.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def get_recent_bars(db: Session, symbol: str, *, before: date, limit: int) -> list[DailyBar]:
    """Trailing sessions used for the average-volume baseline.

    Excludes `before` itself: comparing today's partial volume against an
    average that already contains today would dampen exactly the spike we are
    trying to detect.
    """
    stmt = (
        select(DailyBar)
        .where(DailyBar.symbol == symbol, DailyBar.session_date < before)
        .order_by(DailyBar.session_date.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())
