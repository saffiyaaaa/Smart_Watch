"""Watchlist orchestration.

Every function takes a user_id and passes it through to the repository, so
authorization is structural rather than a check a route might forget to perform.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import ConflictError, NotFoundError
from app.infrastructure.database.repositories import watchlists as wl_repo
from app.models.watchlist import Watchlist
from app.models.watchlist_item import WatchlistItem

logger = logging.getLogger("smw.watchlist")


def create_watchlist(db: Session, *, user_id: uuid.UUID, name: str) -> Watchlist:
    try:
        with db.begin_nested():
            return wl_repo.create_watchlist(db, user_id=user_id, name=name)
    except IntegrityError as exc:
        raise ConflictError(f"A watchlist named {name!r} already exists") from exc


def list_watchlists(db: Session, *, user_id: uuid.UUID) -> list[Watchlist]:
    return wl_repo.list_watchlists(db, user_id=user_id)


def get_watchlist(db: Session, *, watchlist_id: uuid.UUID, user_id: uuid.UUID) -> Watchlist:
    """Fetch an owned watchlist, or raise NotFound.

    A watchlist belonging to someone else raises the same NotFoundError as one
    that does not exist. The caller cannot distinguish the two, so probing ids
    reveals nothing.
    """
    watchlist = wl_repo.get_watchlist(db, watchlist_id=watchlist_id, user_id=user_id)
    if watchlist is None:
        raise NotFoundError("Watchlist not found")
    return watchlist


def delete_watchlist(db: Session, *, watchlist_id: uuid.UUID, user_id: uuid.UUID) -> None:
    if not wl_repo.delete_watchlist(db, watchlist_id=watchlist_id, user_id=user_id):
        raise NotFoundError("Watchlist not found")
    logger.info("watchlist deleted id=%s", watchlist_id)


def add_symbol(
    db: Session, *, watchlist_id: uuid.UUID, user_id: uuid.UUID, symbol: str
) -> WatchlistItem:
    """Add a symbol to an owned watchlist. Idempotent.

    Ownership is verified first so that adding to someone else's watchlist is a
    404, not a silent success against a list the caller cannot see.

    The symbol arrives already normalised by the Pydantic schema. Adding one
    that is already present returns the existing item with 200-style success:
    the requested state already holds, which is not an error.
    """
    get_watchlist(db, watchlist_id=watchlist_id, user_id=user_id)
    item = wl_repo.add_symbol(db, watchlist_id=watchlist_id, symbol=symbol)
    logger.info("symbol added watchlist=%s symbol=%s", watchlist_id, symbol)
    return item


def remove_symbol(db: Session, *, watchlist_id: uuid.UUID, user_id: uuid.UUID, symbol: str) -> None:
    get_watchlist(db, watchlist_id=watchlist_id, user_id=user_id)
    if not wl_repo.remove_symbol(db, watchlist_id=watchlist_id, symbol=symbol):
        raise NotFoundError(f"{symbol} is not in this watchlist")
