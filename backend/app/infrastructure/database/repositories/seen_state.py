"""User seen-state persistence -- the per-user cursor over change events."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.user_seen_state import UserSeenState


def get_seen_state(
    db: Session, *, user_id: uuid.UUID, watchlist_id: uuid.UUID
) -> UserSeenState | None:
    stmt = select(UserSeenState).where(
        UserSeenState.user_id == user_id,
        UserSeenState.watchlist_id == watchlist_id,
    )
    return db.execute(stmt).scalars().first()


def advance_seen_state(
    db: Session,
    *,
    user_id: uuid.UUID,
    watchlist_id: uuid.UUID,
    seen_at: datetime,
    last_seen_event_id: int | None = None,
) -> UserSeenState:
    """Advance the user's cursor. Idempotent, monotonic, concurrency-safe.

    GREATEST() is the whole trick, and it is worth understanding.

    The obvious implementation reads the current row, compares in Python, then
    writes if the new value is later. Two browser tabs marking seen at the same
    moment can both read the old value and both write -- and the one that lands
    second wins, even if it carries the *earlier* timestamp. The cursor moves
    backwards and previously seen events reappear as new.

    Doing the comparison inside the UPDATE makes PostgreSQL evaluate it while
    holding the row lock. The second writer sees the first writer's value. The
    cursor cannot move backwards, calling this repeatedly is free, and the
    ordering of concurrent calls stops mattering -- all three properties the
    Phase 8 gate requires, from one SQL function.
    """
    stmt = (
        insert(UserSeenState)
        .values(
            user_id=user_id,
            watchlist_id=watchlist_id,
            last_seen_at=seen_at,
            last_seen_event_id=last_seen_event_id,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "watchlist_id"],
            set_={
                "last_seen_at": func.greatest(UserSeenState.last_seen_at, seen_at),
                # PostgreSQL's GREATEST ignores NULL operands and returns NULL
                # only when every operand is NULL, which is exactly the wanted
                # behaviour here. Wrapping these in COALESCE(..., 0) would look
                # defensive but would store 0 when both are NULL -- and 0 is not
                # a real event id, so it would violate the foreign key.
                "last_seen_event_id": func.greatest(
                    UserSeenState.last_seen_event_id,
                    last_seen_event_id,
                ),
                "updated_at": func.now(),
            },
        )
        .returning(UserSeenState)
    )
    return db.execute(stmt).scalar_one()
