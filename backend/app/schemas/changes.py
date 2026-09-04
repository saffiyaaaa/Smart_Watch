"""Change feed and seen-state request/response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ChangeEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    event_type: str
    score: int
    severity: str
    evidence: list[str]
    detected_at: datetime


class ChangeFeedResponse(BaseModel):
    events: list[ChangeEventResponse]
    # True only when the user has no prior seen state for this watchlist --
    # the frontend uses this to frame the feed as "here's what's been
    # happening" rather than "here's what changed while you were away".
    first_visit: bool
    last_seen_at: datetime | None


class MarkSeenRequest(BaseModel):
    # Both optional. Omitted seen_at defaults to server time -- "mark
    # everything as seen right now". A client that wants to say "I have seen
    # exactly through this specific event" can supply both, which prevents
    # marking events seen that were never actually rendered.
    seen_at: datetime | None = None
    last_seen_event_id: int | None = None


class SeenStateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    watchlist_id: uuid.UUID
    last_seen_at: datetime
    last_seen_event_id: int | None
