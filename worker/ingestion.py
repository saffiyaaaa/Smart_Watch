"""Market data ingestion: the only place data crosses from a provider into
PostgreSQL.

Each symbol is ingested independently -- its own timing, its own outcome, its
own exception handling -- so one provider failure can never stop the rest of a
batch. This module never raises: every failure a provider or the database can
produce is caught and reported through IngestResult, because the caller's job
is to log it and move on to the next symbol, not to interpret what it means.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.domain.enums import Freshness
from app.domain.market.freshness import classify_freshness
from app.domain.market.quote import Quote
from app.infrastructure.database.repositories import daily_bars as bar_repo
from app.infrastructure.database.repositories import snapshots as snapshot_repo
from app.infrastructure.database.repositories import watchlists as wl_repo
from app.infrastructure.providers.base import MarketDataProvider
from app.infrastructure.providers.exceptions import ProviderError
from app.services import change_detection_service

logger = logging.getLogger("smw.ingestion")

IngestOutcome = Literal["created", "duplicate", "provider_error", "unexpected_error"]


@dataclass(frozen=True)
class IngestResult:
    symbol: str
    outcome: IngestOutcome
    latency_ms: float
    detail: str | None = None
    freshness: str | None = None
    event_created: bool = False


def _daily_history_fetch_days(settings: Settings) -> int:
    """Trading sessions needed for the volume baseline (Phase 6/7), doubled
    to comfortably cover weekends without a holiday calendar (see
    docs/product-spec.md section 3)."""
    return settings.volume_baseline_sessions * 2


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


async def ingest_symbol(
    db: Session,
    provider: MarketDataProvider,
    symbol: str,
    *,
    settings: Settings | None = None,
) -> IngestResult:
    """Fetch and persist one symbol's latest quote, then best-effort refresh
    its daily bars.

    The quote and the bars are committed in separate transactions on purpose:
    a bars-fetch failure must not roll back a snapshot that was already
    successfully persisted moments earlier. IngestResult reports on the
    quote, since that is what "one valid observation creates exactly one
    snapshot" (the Phase 5 gate) is actually about; bars are a supporting
    input for Phase 6/7 and their failure is logged but does not change the
    reported outcome.
    """
    settings = settings or get_settings()
    started = time.monotonic()

    try:
        quote = await provider.get_quote(symbol)
    except ProviderError as exc:
        return IngestResult(symbol, "provider_error", _elapsed_ms(started), detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - a provider bug must not crash the batch
        logger.exception("unexpected error fetching quote for %s", symbol)
        return IngestResult(symbol, "unexpected_error", _elapsed_ms(started), detail=str(exc))

    now = datetime.now(UTC)
    try:
        freshness = classify_freshness(
            quote.market_timestamp,
            now=now,
            fresh_seconds=settings.freshness_fresh_seconds,
            stale_seconds=settings.freshness_stale_seconds,
            timezone=settings.market_tz,
            open_time=settings.market_open,
            close_time=settings.market_close,
        )
        created = snapshot_repo.insert_snapshot(
            db,
            source=quote.source,
            symbol=quote.symbol,
            price=quote.price,
            volume=quote.volume,
            market_timestamp=quote.market_timestamp,
            ingest_freshness=freshness,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("failed to persist snapshot for %s", symbol)
        return IngestResult(
            symbol,
            "unexpected_error",
            _elapsed_ms(started),
            detail=f"snapshot persistence failed: {exc}",
        )

    event_created = False
    if created is not None:
        event_created = await _run_change_detection_best_effort(
            db, quote, snapshot_id=created.id, freshness=freshness, now=now, settings=settings
        )

    await _refresh_daily_bars_best_effort(db, provider, symbol, settings=settings)

    outcome: IngestOutcome = "created" if created is not None else "duplicate"
    return IngestResult(
        symbol,
        outcome,
        _elapsed_ms(started),
        freshness=freshness.value,
        event_created=event_created,
    )


async def _run_change_detection_best_effort(
    db: Session,
    quote: Quote,
    *,
    snapshot_id: int,
    freshness: Freshness,
    now: datetime,
    settings: Settings,
) -> bool:
    """Score the new snapshot and persist a change_event, if warranted.

    Skipped entirely for an out-of-order arrival: is_latest_snapshot checks
    whether the row `snapshot_id` names is genuinely the newest known
    observation for the symbol, since an older observation arriving late must
    never masquerade as new information (docs/product-spec.md section 3).

    Its own transaction, like the daily-bars refresh: a scoring bug or a
    transient database error here must not roll back the snapshot already
    committed moments earlier.
    """
    try:
        if not change_detection_service.is_latest_snapshot(db, quote.symbol, snapshot_id):
            return False
        event = change_detection_service.run_change_detection(
            db, quote, freshness=freshness, now=now, settings=settings
        )
        db.commit()
        return event is not None
    except Exception:
        db.rollback()
        logger.exception("change detection failed for %s", quote.symbol)
        return False


async def _refresh_daily_bars_best_effort(
    db: Session, provider: MarketDataProvider, symbol: str, *, settings: Settings
) -> None:
    """Refresh the change-detection baseline for a symbol.

    Best-effort: any failure here -- a provider error, a bad row -- is logged
    and swallowed rather than propagated, because a stale or missing baseline
    degrades Phase 6/7's confidence in a signal, it does not invalidate the
    quote this cycle already captured successfully.
    """
    try:
        bars = await provider.get_daily_history(symbol, days=_daily_history_fetch_days(settings))
        for bar in bars:
            bar_repo.upsert_bar(
                db,
                source=bar.source,
                symbol=bar.symbol,
                session_date=bar.session_date,
                close=bar.close,
                volume=bar.volume,
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("daily bar refresh failed for %s", symbol, exc_info=True)


async def ingest_all(
    db: Session, provider: MarketDataProvider, *, settings: Settings | None = None
) -> list[IngestResult]:
    """Ingest every symbol tracked by any watchlist.

    Symbols are processed sequentially, each independently committed or
    rolled back, so a slow or failing provider for one symbol cannot stop or
    corrupt the rest of the batch. Running symbols concurrently is a Phase 12
    performance question, not a Phase 5 correctness one.
    """
    settings = settings or get_settings()
    symbols = wl_repo.get_all_tracked_symbols(db)

    results: list[IngestResult] = []
    for symbol in symbols:
        result = await ingest_symbol(db, provider, symbol, settings=settings)
        _log_result(result)
        results.append(result)
    return results


def _log_result(result: IngestResult) -> None:
    payload = {
        "symbol": result.symbol,
        "outcome": result.outcome,
        "latency_ms": result.latency_ms,
        "freshness": result.freshness,
        "event_created": result.event_created,
        "detail": result.detail,
    }
    if result.outcome in ("provider_error", "unexpected_error"):
        logger.warning("ingestion failed: %s", payload)
    else:
        logger.info("ingestion ok: %s", payload)
