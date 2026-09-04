"""Watchlist and watchlist-item persistence.

Every read here takes a user_id and filters on it. Ownership is not an optional
extra argument that a caller might forget: a function that cannot express
"fetch this watchlist regardless of owner" cannot be misused into leaking one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.models.watchlist import Watchlist
from app.models.watchlist_item import WatchlistItem


def create_watchlist(db: Session, *, user_id: uuid.UUID, name: str) -> Watchlist:
    watchlist = Watchlist(user_id=user_id, name=name.strip())
    db.add(watchlist)
    db.flush()
    return watchlist


def list_watchlists(db: Session, *, user_id: uuid.UUID) -> list[Watchlist]:
    stmt = (
        select(Watchlist)
        .where(Watchlist.user_id == user_id)
        # selectinload issues one extra query for all items rather than one per
        # watchlist. Without it, rendering N watchlists costs N+1 queries.
        .options(selectinload(Watchlist.items))
        .order_by(Watchlist.created_at.desc())
    )
    return list(db.execute(stmt).scalars())


def get_watchlist(db: Session, *, watchlist_id: uuid.UUID, user_id: uuid.UUID) -> Watchlist | None:
    """Fetch a watchlist owned by this user, or None.

    The ownership filter is in the WHERE clause, not a separate check after the
    fetch. A caller therefore cannot accidentally read another user's row and
    then forget to compare owners -- and "not found" and "not yours" become the
    same answer, which avoids confirming that someone else's id exists.
    """
    stmt = (
        select(Watchlist)
        .where(Watchlist.id == watchlist_id, Watchlist.user_id == user_id)
        .options(selectinload(Watchlist.items))
    )
    return db.execute(stmt).scalars().first()


def delete_watchlist(db: Session, *, watchlist_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Delete an owned watchlist. Returns whether a row was removed.

    Items and seen state disappear with it via ON DELETE CASCADE in the schema,
    so no orphan cleanup is needed here.
    """
    stmt = delete(Watchlist).where(
        Watchlist.id == watchlist_id,
        Watchlist.user_id == user_id,
    )
    return db.execute(stmt).rowcount > 0


def add_symbol(db: Session, *, watchlist_id: uuid.UUID, symbol: str) -> WatchlistItem:
    """Add a symbol. Idempotent and safe under concurrent duplicate requests.

    ON CONFLICT DO NOTHING returns no row when the symbol is already present, so
    the existing row is then read back. Both callers in a concurrent double-add
    receive the same item and a success response -- adding something already
    present is not an error, it is a request whose desired state already holds.

    The alternative, SELECT-then-INSERT, has a window between the two statements
    where both callers see nothing and both insert. The unique index would still
    prevent the duplicate row, but one caller would get an unexplained 500.
    """
    stmt = (
        insert(WatchlistItem)
        .values(watchlist_id=watchlist_id, symbol=symbol)
        .on_conflict_do_nothing(constraint="uq_watchlist_items_watchlist_id_symbol")
        .returning(WatchlistItem)
    )
    created = db.execute(stmt).scalar_one_or_none()
    if created is not None:
        return created

    existing = (
        db.execute(
            select(WatchlistItem).where(
                WatchlistItem.watchlist_id == watchlist_id,
                WatchlistItem.symbol == symbol,
            )
        )
        .scalars()
        .first()
    )
    if existing is None:  # pragma: no cover - only reachable if the row was
        # deleted between the insert and this read.
        raise RuntimeError(f"symbol {symbol} vanished during add")
    return existing


def remove_symbol(db: Session, *, watchlist_id: uuid.UUID, symbol: str) -> bool:
    stmt = delete(WatchlistItem).where(
        WatchlistItem.watchlist_id == watchlist_id,
        WatchlistItem.symbol == symbol,
    )
    return db.execute(stmt).rowcount > 0


def get_symbols(db: Session, *, watchlist_id: uuid.UUID) -> list[str]:
    stmt = (
        select(WatchlistItem.symbol)
        .where(WatchlistItem.watchlist_id == watchlist_id)
        .order_by(WatchlistItem.symbol)
    )
    return list(db.execute(stmt).scalars())


def get_all_tracked_symbols(db: Session) -> list[str]:
    """Every distinct symbol across all users -- the worker's work queue.

    Distinct at the database level: one hundred users watching AAPL must produce
    one provider call, not one hundred.
    """
    stmt = select(WatchlistItem.symbol).distinct().order_by(WatchlistItem.symbol)
    return list(db.execute(stmt).scalars())
