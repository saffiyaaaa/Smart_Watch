"""Watchlist endpoints.

These routes contain no SQL and no business rules. They translate HTTP into a
service call and back, which is what keeps authorization and idempotency in one
testable place instead of spread across handlers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.config import get_settings
from app.schemas.changes import (
    ChangeEventResponse,
    ChangeFeedResponse,
    MarkSeenRequest,
    SeenStateResponse,
)
from app.schemas.watchlist import (
    SymbolRequest,
    WatchlistCreateRequest,
    WatchlistItemResponse,
    WatchlistResponse,
)
from app.services import change_feed_service, watchlist_service

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


@router.get("", response_model=list[WatchlistResponse])
def list_watchlists(current_user: CurrentUser, db: DbSession) -> list[WatchlistResponse]:
    watchlists = watchlist_service.list_watchlists(db, user_id=current_user.id)
    return [WatchlistResponse.model_validate(w) for w in watchlists]


@router.post("", response_model=WatchlistResponse, status_code=status.HTTP_201_CREATED)
def create_watchlist(
    payload: WatchlistCreateRequest, current_user: CurrentUser, db: DbSession
) -> WatchlistResponse:
    watchlist = watchlist_service.create_watchlist(db, user_id=current_user.id, name=payload.name)
    return WatchlistResponse.model_validate(watchlist)


@router.get("/{watchlist_id}", response_model=WatchlistResponse)
def get_watchlist(
    watchlist_id: uuid.UUID, current_user: CurrentUser, db: DbSession
) -> WatchlistResponse:
    watchlist = watchlist_service.get_watchlist(
        db, watchlist_id=watchlist_id, user_id=current_user.id
    )
    return WatchlistResponse.model_validate(watchlist)


@router.delete("/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist(watchlist_id: uuid.UUID, current_user: CurrentUser, db: DbSession) -> None:
    watchlist_service.delete_watchlist(db, watchlist_id=watchlist_id, user_id=current_user.id)


@router.post(
    "/{watchlist_id}/symbols",
    response_model=WatchlistItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_symbol(
    watchlist_id: uuid.UUID,
    payload: SymbolRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> WatchlistItemResponse:
    """Add a symbol. Idempotent: re-adding returns the existing item.

    Returns 201 in both cases. The alternative -- 200 for "already there" --
    would make the client's success handling depend on a race it cannot
    observe, and both outcomes leave the system in the state the caller asked
    for.
    """
    item = watchlist_service.add_symbol(
        db, watchlist_id=watchlist_id, user_id=current_user.id, symbol=payload.symbol
    )
    return WatchlistItemResponse.model_validate(item)


@router.delete("/{watchlist_id}/symbols/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
def remove_symbol(
    watchlist_id: uuid.UUID, symbol: str, current_user: CurrentUser, db: DbSession
) -> None:
    normalized = SymbolRequest(symbol=symbol).symbol
    watchlist_service.remove_symbol(
        db, watchlist_id=watchlist_id, user_id=current_user.id, symbol=normalized
    )


@router.get("/{watchlist_id}/changes", response_model=ChangeFeedResponse)
def get_changes(
    watchlist_id: uuid.UUID, current_user: CurrentUser, db: DbSession
) -> ChangeFeedResponse:
    """What meaningfully changed since this user last checked this
    watchlist. See docs/product-spec.md section 3 for exactly what that
    means, including the first-visit case."""
    result = change_feed_service.get_change_feed(
        db, watchlist_id=watchlist_id, user_id=current_user.id, settings=get_settings()
    )
    return ChangeFeedResponse(
        events=[ChangeEventResponse.model_validate(e) for e in result.events],
        first_visit=result.first_visit,
        last_seen_at=result.last_seen_at,
    )


@router.post("/{watchlist_id}/seen", response_model=SeenStateResponse)
def mark_seen(
    watchlist_id: uuid.UUID,
    payload: MarkSeenRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> SeenStateResponse:
    """Advance the user's cursor. Idempotent: calling this repeatedly, or
    concurrently from two tabs, converges to one correct state -- see
    change_feed_service.mark_seen and the GREATEST() upsert underneath it."""
    seen_at = payload.seen_at or datetime.now(UTC)
    state = change_feed_service.mark_seen(
        db,
        watchlist_id=watchlist_id,
        user_id=current_user.id,
        seen_at=seen_at,
        last_seen_event_id=payload.last_seen_event_id,
    )
    return SeenStateResponse.model_validate(state)
