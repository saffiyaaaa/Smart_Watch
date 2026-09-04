"""Change-detection signals: pure functions comparing an observation to a
baseline.

Zero imports from the database or provider layers. Every function here takes
plain values (Decimal, int, None) and plain dataclasses -- never a SQLAlchemy
model, never a Quote -- so this module can be tested exhaustively without a
database, a provider, or an event loop, and so the calculation itself can
never accidentally depend on how a value was fetched or stored.

Freshness classification lives in app.domain.market.freshness (built in
Phase 5, ahead of this module, because market_snapshots.ingest_freshness is
NOT NULL and needs a classification at insert time). It is a property of one
observation's quality; the functions here are about *comparing* two
observations, which is a different question and this module's actual job.

See docs/product-spec.md sections 2-3 for the definitions these implement.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.enums import Freshness

_HUNDRED = Decimal(100)


def price_change_pct(current_price: Decimal, baseline_price: Decimal | None) -> Decimal | None:
    """Percentage change of `current_price` relative to `baseline_price`
    (the previous session's close -- docs/product-spec.md section 2).

    Returns None, never raises or divides by zero, when no baseline exists
    (a newly listed symbol with no prior session) or the baseline is not a
    usable positive price. A pure function should degrade on bad input, not
    crash the pipeline calling it -- the caller decides what "the price
    signal is unavailable" means for scoring (Phase 7).

    Expressed in percentage *points* (6.2 means 6.2%), matching
    docs/product-spec.md section 4's scoring formula.
    """
    if baseline_price is None or baseline_price <= 0:
        return None
    return ((current_price - baseline_price) / baseline_price) * _HUNDRED


def average_volume(recent_volumes: Sequence[int | None], *, min_sessions: int) -> int | None:
    """Trailing average volume, or None if fewer than `min_sessions` real
    observations are available.

    None entries (volume not reported for that session) are dropped rather
    than treated as zero -- averaging in zeros would drag the baseline down
    and make perfectly ordinary volume look artificially spiky by
    comparison. "Not enough history yet" and "volume was flat" are different
    facts and must not collapse into the same number.
    """
    valid = [v for v in recent_volumes if v is not None]
    if len(valid) < min_sessions:
        return None
    return round(sum(valid) / len(valid))


def volume_ratio(current_volume: int | None, baseline_average: int | None) -> Decimal | None:
    """Current volume relative to its trailing baseline (see average_volume).

    None when current volume was not reported, or the baseline itself is
    unavailable -- this is a *different*, weaker signal than "no spike"
    (a ratio near 1.0), and docs/product-spec.md section 4 scores it
    differently: missing volume degrades confidence, a genuinely flat volume
    does not.
    """
    if current_volume is None or baseline_average is None or baseline_average <= 0:
        return None
    return Decimal(current_volume) / Decimal(baseline_average)


def detect_conflict(price_a: Decimal, price_b: Decimal, *, tolerance_pct: Decimal) -> bool:
    """Whether two same-instant, same-symbol observations disagree beyond
    the documented tolerance (docs/product-spec.md section 2).

    Uses the difference relative to the *average* of the two prices, not
    either one specifically -- unlike price_change_pct, neither observation
    here is privileged as "the baseline"; this is a symmetric agreement
    check between two peers, so the answer must not depend on which one
    happens to be passed as `price_a`.
    """
    if price_a <= 0 or price_b <= 0:
        return False  # not a meaningful comparison; validated upstream anyway
    midpoint = (price_a + price_b) / 2
    diff_pct = abs(price_a - price_b) / midpoint * _HUNDRED
    return diff_pct > tolerance_pct


def is_new_observation(
    candidate_timestamp: datetime, latest_known_timestamp: datetime | None
) -> bool:
    """Whether `candidate_timestamp` represents genuinely new information
    worth running detection on.

    Per docs/product-spec.md section 3, "latest" means max(market_timestamp),
    never arrival order. An observation that is not strictly newer than what
    is already known must not trigger detection, even though Phase 5 still
    stores it as history -- and an observation with the *same* timestamp as
    what is already known is not new information either, it is the same
    fact observed again.
    """
    if latest_known_timestamp is None:
        return True
    return candidate_timestamp > latest_known_timestamp


@dataclass(frozen=True)
class Signals:
    """The normalized inputs Phase 7's scorer consumes. A pure data bundle,
    not a computation -- see compute_signals for how it is assembled."""

    price_change_pct: Decimal | None
    volume_ratio: Decimal | None
    freshness: Freshness
    is_conflicting: bool


def compute_signals(
    *,
    current_price: Decimal,
    baseline_price: Decimal | None,
    current_volume: int | None,
    recent_volumes: Sequence[int | None],
    volume_baseline_min_sessions: int,
    freshness: Freshness,
    is_conflicting: bool,
) -> Signals:
    """Assemble the four signals scoring needs, from already-known values.

    `freshness` and `is_conflicting` are supplied pre-computed rather than
    derived here: freshness needs "now" and market-hours configuration
    (app.domain.market.freshness), and conflict detection needs a second
    source's price. Both are decisions an orchestration layer makes before
    calling this -- this function's job is only to bundle results, not to
    reach for its own inputs.
    """
    baseline_avg_volume = average_volume(recent_volumes, min_sessions=volume_baseline_min_sessions)
    return Signals(
        price_change_pct=price_change_pct(current_price, baseline_price),
        volume_ratio=volume_ratio(current_volume, baseline_avg_volume),
        freshness=freshness,
        is_conflicting=is_conflicting,
    )
