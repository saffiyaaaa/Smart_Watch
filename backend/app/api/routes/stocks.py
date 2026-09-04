"""Symbol-level market data endpoints.

These routes serve current price and history for a single symbol inside a
watchlist the caller owns. Ownership is verified the same way as every other
watchlist operation: get_watchlist raises NotFoundError when the watchlist
does not exist or belongs to someone else, collapsing both cases to 404.

Design notes
------------
The quote endpoint reads from the database, not from a live provider call.
The worker already fetches prices on a schedule; the API serving stale
database rows is by design -- the freshness label tells the client exactly
how current the data is, and a live provider call per request would couple
API latency to an external service that may be slow or unavailable.

Invalid provider data (Phase 4's InvalidProviderData exception) can never
reach here as a valid 200 because the worker never writes a snapshot unless
the Quote model validates successfully. The completeness gate for that
guarantee is tested in test_error_handling.py, not here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.api.errors import NotFoundError
from app.config import get_settings
from app.domain.market.freshness import classify_freshness
from app.infrastructure.database.repositories import daily_bars as bar_repo
from app.infrastructure.database.repositories import snapshots as snapshot_repo
from app.schemas.stocks import DailyBarResponse, QuoteResponse
from app.services import watchlist_service

router = APIRouter(prefix="/watchlists", tags=["symbols"])


@router.get(
    "/{watchlist_id}/symbols/{symbol}/quote",
    response_model=QuoteResponse,
    summary="Current quote for a watchlist symbol",
)
def get_quote(
    watchlist_id: uuid.UUID,
    symbol: str,
    current_user: CurrentUser,
    db: DbSession,
) -> QuoteResponse:
    """Latest stored observation for one symbol, with its live freshness label.

    Returns the most recent snapshot persisted by the worker. If no snapshot
    has been ingested for this symbol yet, returns 404 -- the symbol may be
    valid but the worker simply has not run yet.

    Freshness is recomputed on every request so the label reflects reality
    now, not when the worker wrote the row. A quote from Friday at 15:55
    will read FRESH all weekend, because the reference point while the
    market is closed is the last session close, not the current wall time.
    """
    watchlist_service.get_watchlist(db, watchlist_id=watchlist_id, user_id=current_user.id)

    snapshot = snapshot_repo.get_latest(db, symbol.upper())
    if snapshot is None:
        raise NotFoundError(f"No quote data available for {symbol.upper()!r}")

    settings = get_settings()
    freshness = classify_freshness(
        snapshot.market_timestamp,
        now=datetime.now(UTC),
        fresh_seconds=settings.freshness_fresh_seconds,
        stale_seconds=settings.freshness_stale_seconds,
        timezone=settings.market_tz,
        open_time=settings.market_open,
        close_time=settings.market_close,
    )

    return QuoteResponse(
        symbol=snapshot.symbol,
        price=snapshot.price,
        volume=snapshot.volume,
        market_timestamp=snapshot.market_timestamp,
        freshness=freshness.value,
        ingest_freshness=snapshot.ingest_freshness,
    )


@router.get(
    "/{watchlist_id}/symbols/{symbol}/history",
    response_model=list[DailyBarResponse],
    summary="Daily bar history for a watchlist symbol",
)
def get_history(
    watchlist_id: uuid.UUID,
    symbol: str,
    current_user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=30, ge=1, le=90, description="Number of sessions to return"),
) -> list[DailyBarResponse]:
    """Completed trading sessions for one symbol, most recent first.

    Returns at most `limit` sessions (default 30, max 90). An empty list
    means no daily bars have been ingested yet -- not an error. The session
    data is the same source used for change-detection baselines (previous
    close and 20-day average volume), so what the user sees here is exactly
    what drove any events in their feed.
    """
    watchlist_service.get_watchlist(db, watchlist_id=watchlist_id, user_id=current_user.id)

    # get_recent_bars requires a `before` date; passing tomorrow ensures
    # the current session's bar (if ingested) is included.
    from datetime import date, timedelta

    bars = bar_repo.get_recent_bars(
        db,
        symbol.upper(),
        before=date.today() + timedelta(days=1),
        limit=limit,
    )
    return [DailyBarResponse.model_validate(b) for b in bars]
