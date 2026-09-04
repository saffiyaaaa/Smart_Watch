"""Freshness classification: how current a market observation is, right now.

Lives in domain/market rather than domain/changes because it characterises a
single observation's quality -- it is not a signal derived from comparing two
observations, which is domain/changes' job (Phase 6). It has to exist this
early regardless: market_snapshots.ingest_freshness is NOT NULL, so every
insert needs a classification before Phase 6 exists to want one. Phase 6's
confidence multiplier reuses this same function rather than defining a second
one.

See docs/product-spec.md section 2 for the exact definitions and thresholds.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.enums import Freshness

# Safety bound for walking backward to find the last completed session. A
# handful of days comfortably covers any run of weekends; if a real gap ever
# exceeded this, something else is badly wrong and this should fail loudly
# rather than loop forever.
_MAX_LOOKBACK_DAYS = 10


def is_market_open(now: datetime, *, timezone: ZoneInfo, open_time: time, close_time: time) -> bool:
    """Weekday-and-hours check only.

    v1 has no holiday calendar (docs/product-spec.md section 2). On a market
    holiday this still reports "open" during trading hours -- the system
    reads a holiday's stale quotes as unexpectedly old rather than as an
    explicitly known closure, which is a documented limitation, not an
    oversight: it errs toward under-confidence, the safe direction.
    """
    local = now.astimezone(timezone)
    if local.weekday() >= 5:  # Saturday, Sunday
        return False
    return open_time <= local.time() < close_time


def most_recent_session_close(now: datetime, *, timezone: ZoneInfo, close_time: time) -> datetime:
    """The UTC instant of the most recently completed session's close, at or
    before `now`. This is the staleness reference while the market is closed,
    so a weekend does not by itself make Friday's closing price look STALE."""
    local = now.astimezone(timezone)
    candidate_date = local.date()

    for _ in range(_MAX_LOOKBACK_DAYS):
        candidate_close = datetime.combine(candidate_date, close_time, tzinfo=timezone)
        if candidate_date.weekday() < 5 and candidate_close <= local:
            return candidate_close.astimezone(UTC)
        candidate_date -= timedelta(days=1)

    raise RuntimeError(f"no trading session close found within {_MAX_LOOKBACK_DAYS} days of {now}")


def freshness_reference(
    now: datetime, *, timezone: ZoneInfo, open_time: time, close_time: time
) -> datetime:
    """The instant staleness is measured against.

    While the market is open, that is simply "now". While it is closed, using
    "now" would make a perfectly good Friday-afternoon close look STALE for
    the entire weekend; measuring from the most recent close instead means a
    closed market does not by itself degrade confidence in the last real
    price.
    """
    if is_market_open(now, timezone=timezone, open_time=open_time, close_time=close_time):
        return now
    return most_recent_session_close(now, timezone=timezone, close_time=close_time)


def classify_freshness(
    market_timestamp: datetime,
    *,
    now: datetime,
    fresh_seconds: int,
    stale_seconds: int,
    timezone: ZoneInfo,
    open_time: time,
    close_time: time,
) -> Freshness:
    """FRESH / DELAYED / STALE, per docs/product-spec.md section 2.

    Takes explicit primitive parameters rather than a Settings object so this
    stays a pure, directly testable function with no dependency on the
    infrastructure layer's configuration type.
    """
    reference = freshness_reference(
        now, timezone=timezone, open_time=open_time, close_time=close_time
    )
    age_seconds = (reference - market_timestamp).total_seconds()

    if age_seconds <= fresh_seconds:
        return Freshness.FRESH
    if age_seconds <= stale_seconds:
        return Freshness.DELAYED
    return Freshness.STALE
