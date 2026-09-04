"""The change feed and the per-user cursor over it.

Every function here takes a user_id and routes through
watchlist_service.get_watchlist first, so a user reading or advancing a
watchlist's feed goes through the same ownership check as every other
watchlist operation -- "not yours" and "not found" collapse to the same
404 here exactly as they do everywhere else in the API.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import Settings
from app.infrastructure.database.repositories import events as event_repo
from app.infrastructure.database.repositories import seen_state as seen_state_repo
from app.models.change_event import ChangeEvent
from app.models.user_seen_state import UserSeenState
from app.services import watchlist_service


@dataclass(frozen=True)
class ChangeFeedResult:
    events: list[ChangeEvent]
    first_visit: bool
    last_seen_at: datetime | None


def get_change_feed(
    db: Session, *, watchlist_id: uuid.UUID, user_id: uuid.UUID, settings: Settings
) -> ChangeFeedResult:
    """ "What changed since I last checked" -- resolved precisely per
    docs/product-spec.md section 3.

    A user with no seen state yet is not shown an empty feed; they are shown
    recent activity (the last `first_visit_lookback_hours`, capped at
    `first_visit_max_events`) framed as first_visit, since "nothing changed"
    would be a strange first impression for a watchlist that has been
    accumulating real events all along.
    """
    watchlist_service.get_watchlist(db, watchlist_id=watchlist_id, user_id=user_id)

    seen_state = seen_state_repo.get_seen_state(db, user_id=user_id, watchlist_id=watchlist_id)

    if seen_state is None:
        since = datetime.now(UTC) - timedelta(hours=settings.first_visit_lookback_hours)
        events = event_repo.get_events_for_watchlist(
            db,
            watchlist_id=watchlist_id,
            since=since,
            min_score=settings.severity_watch_min,
            limit=settings.first_visit_max_events,
        )
        return ChangeFeedResult(events=events, first_visit=True, last_seen_at=None)

    events = event_repo.get_events_for_watchlist(
        db,
        watchlist_id=watchlist_id,
        since=seen_state.last_seen_at,
        min_score=settings.severity_watch_min,
        limit=settings.change_feed_max_events,
    )
    return ChangeFeedResult(events=events, first_visit=False, last_seen_at=seen_state.last_seen_at)


def mark_seen(
    db: Session,
    *,
    watchlist_id: uuid.UUID,
    user_id: uuid.UUID,
    seen_at: datetime,
    last_seen_event_id: int | None,
) -> UserSeenState:
    """Advance the user's cursor. Idempotent and safe under concurrency --
    see infrastructure/database/repositories/seen_state.py's GREATEST()
    upsert, which is what actually provides those guarantees; this function
    only adds the ownership check and the future-clamp below.

    `seen_at` is clamped to "not after now": a client is free to report an
    earlier timestamp (what it actually rendered), but must not be able to
    push the cursor into the future and permanently suppress events that
    have not happened yet -- the monotonic upsert protects against the
    cursor moving *backwards*, this protects against it moving too far
    *forwards*.
    """
    watchlist_service.get_watchlist(db, watchlist_id=watchlist_id, user_id=user_id)

    clamped_seen_at = min(seen_at, datetime.now(UTC))
    return seen_state_repo.advance_seen_state(
        db,
        user_id=user_id,
        watchlist_id=watchlist_id,
        seen_at=clamped_seen_at,
        last_seen_event_id=last_seen_event_id,
    )
