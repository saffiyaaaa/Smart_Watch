"""Wires Phase 6's signals and Phase 7's scoring into the persisted
change_events table.

This is the one place app.config.Settings' plain floats become the Decimal
values app.domain.changes.scoring.ScoringConfig requires -- the conversion is
deliberately kept here, at the boundary, rather than in either the config
layer (which has no reason to know about Decimal) or the domain layer (which
must not depend on Settings at all; see scoring.py's module docstring).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.changes.detection import compute_signals, detect_conflict
from app.domain.changes.scoring import ScoringConfig, score_change
from app.domain.enums import Freshness, Severity
from app.domain.market.quote import Quote
from app.infrastructure.database.repositories import daily_bars as bar_repo
from app.infrastructure.database.repositories import events as event_repo
from app.infrastructure.database.repositories import snapshots as snapshot_repo
from app.models.change_event import ChangeEvent

logger = logging.getLogger("smw.change_detection")


def build_scoring_config(settings: Settings) -> ScoringConfig:
    """The Settings -> ScoringConfig adapter deferred from Phase 7.

    str() before Decimal() throughout: Decimal(0.1) reproduces float's binary
    imprecision (Decimal('0.1000000000000000055511151231257827021181583404541015625')),
    while Decimal(str(0.1)) gives the exact decimal a human typed in .env.
    """
    return ScoringConfig(
        price_min_pct=Decimal(str(settings.price_min_pct)),
        price_max_pct=Decimal(str(settings.price_max_pct)),
        price_max_points=Decimal(str(settings.price_max_points)),
        volume_min_ratio=Decimal(str(settings.volume_min_ratio)),
        volume_max_ratio=Decimal(str(settings.volume_max_ratio)),
        volume_max_points=Decimal(str(settings.volume_max_points)),
        confidence_delayed=Decimal(str(settings.confidence_delayed)),
        confidence_stale=Decimal(str(settings.confidence_stale)),
        confidence_conflicting=Decimal(str(settings.confidence_conflicting)),
        confidence_no_volume_baseline=Decimal(str(settings.confidence_no_volume_baseline)),
        severity_watch_min=settings.severity_watch_min,
        severity_important_min=settings.severity_important_min,
        severity_high_min=settings.severity_high_min,
        volume_baseline_sessions=settings.volume_baseline_sessions,
    )


def _trading_day(timestamp: datetime, *, settings: Settings) -> date:
    """The exchange-local calendar date this observation belongs to -- what
    change_events.trading_day (and its one-event-per-symbol-per-day
    constraint, see Phase 2) actually buckets by."""
    return timestamp.astimezone(settings.market_tz).date()


def run_change_detection(
    db: Session,
    quote: Quote,
    *,
    freshness: Freshness,
    now: datetime,
    settings: Settings,
) -> ChangeEvent | None:
    """Score a symbol's new latest observation and persist an event if it
    clears the surfacing floor.

    Must only be called when the snapshot just inserted for `quote` is
    genuinely the new latest for its symbol (see worker/ingestion.py, which
    checks this before calling in). An out-of-order arrival must never reach
    here -- docs/product-spec.md section 3 and Phase 6's is_new_observation
    exist precisely to keep history from masquerading as news.

    `freshness` is passed in rather than recomputed: it is the exact
    classification already stored on the snapshot moments earlier, and
    recomputing it here against a second call to "now" could, in principle,
    disagree with what was actually persisted.
    """
    trading_day = _trading_day(quote.market_timestamp, settings=settings)

    baseline_bar = bar_repo.get_previous_close(db, quote.symbol, before=trading_day)
    baseline_price = baseline_bar.close if baseline_bar is not None else None

    recent_bars = bar_repo.get_recent_bars(
        db, quote.symbol, before=trading_day, limit=settings.volume_baseline_sessions
    )
    recent_volumes = [bar.volume for bar in recent_bars]

    others = snapshot_repo.get_other_sources_at(
        db,
        symbol=quote.symbol,
        market_timestamp=quote.market_timestamp,
        exclude_source=quote.source,
    )
    tolerance = Decimal(str(settings.conflict_price_tolerance_pct))
    is_conflicting = any(
        detect_conflict(quote.price, other.price, tolerance_pct=tolerance) for other in others
    )

    signals = compute_signals(
        current_price=quote.price,
        baseline_price=baseline_price,
        current_volume=quote.volume,
        recent_volumes=recent_volumes,
        volume_baseline_min_sessions=settings.volume_baseline_min_sessions,
        freshness=freshness,
        is_conflicting=is_conflicting,
    )

    age_minutes = round((now - quote.market_timestamp).total_seconds() / 60)
    result = score_change(
        signals,
        config=build_scoring_config(settings),
        current_price=quote.price,
        baseline_price=baseline_price,
        freshness_age_minutes=age_minutes,
    )

    if result.severity is Severity.NORMAL:
        # Not a meaningful change -- change_events' CHECK constraint would
        # reject this anyway, but returning early also skips the write.
        return None

    event = event_repo.upsert_event(
        db,
        symbol=quote.symbol,
        trading_day=trading_day,
        event_type=result.event_type,
        score=result.score,
        severity=result.severity,
        evidence=list(result.evidence),
        price_pct=signals.price_change_pct,
        volume_ratio=signals.volume_ratio,
        confidence=result.confidence,
    )

    if event is not None:
        logger.info(
            "change event: symbol=%s severity=%s score=%d",
            quote.symbol,
            result.severity.value,
            result.score,
        )
    return event


def is_latest_snapshot(db: Session, symbol: str, snapshot_id: int) -> bool:
    """Whether the row identified by `snapshot_id` is currently the latest
    known observation for `symbol`.

    Called from within the same transaction the snapshot was inserted in, so
    this sees the row that was just written even before commit -- one query
    after the insert is enough; no "latest before" snapshot needs to be
    captured up front.
    """
    latest = snapshot_repo.get_latest(db, symbol)
    return latest is not None and latest.id == snapshot_id
