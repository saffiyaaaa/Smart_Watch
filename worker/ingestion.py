"""Market data ingestion: the only place data crosses from a provider into
PostgreSQL.

Each symbol is ingested independently -- its own timing, its own outcome, its
own exception handling -- so one provider failure can never stop the rest of a
batch. This module never raises: every failure a provider or the database can
produce is caught and reported through IngestResult, because the caller's job
is to log it and move on to the next symbol, not to interpret what it means.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.domain.enums import Freshness
from app.domain.market.freshness import classify_freshness
from app.domain.market.quote import Bar, Quote
from app.infrastructure.cache import QuoteCache
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
    cache: QuoteCache | None = None,
    db_lock: asyncio.Lock | None = None,
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

    `cache` is checked before calling the provider and populated after (see
    app/infrastructure/cache/quote_cache.py); a cache miss or a disabled cache
    behaves exactly like the plain provider call this replaced.

    `db_lock` serialises the sections that touch `db` when several symbols'
    calls are running concurrently under ingest_all's semaphore (Phase 12) --
    a single Session is not safe to have two coroutines inside at once. A
    caller ingesting one symbol on its own (every direct call in the test
    suite) gets a private lock that is never contended, so this parameter is
    invisible to that case.
    """
    settings = settings or get_settings()
    cache = cache or QuoteCache(settings)
    db_lock = db_lock or asyncio.Lock()
    started = time.monotonic()

    try:
        quote = await _fetch_quote(provider, symbol, cache=cache)
    except ProviderError as exc:
        return IngestResult(symbol, "provider_error", _elapsed_ms(started), detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - a provider bug must not crash the batch
        logger.exception("unexpected error fetching quote for %s", symbol)
        return IngestResult(symbol, "unexpected_error", _elapsed_ms(started), detail=str(exc))

    # Fetched outside db_lock, alongside the quote above: this is the other
    # network call ingest_symbol makes, and it must not hold up a different
    # symbol's db work while it is in flight.
    bars = await _fetch_daily_bars_best_effort(provider, symbol, settings=settings)

    now = datetime.now(UTC)
    async with db_lock:
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

        _persist_daily_bars_best_effort(db, symbol, bars)

    outcome: IngestOutcome = "created" if created is not None else "duplicate"
    return IngestResult(
        symbol,
        outcome,
        _elapsed_ms(started),
        freshness=freshness.value,
        event_created=event_created,
    )


async def _fetch_quote(provider: MarketDataProvider, symbol: str, *, cache: QuoteCache) -> Quote:
    """Cache-checked provider fetch. Raises exactly what `provider.get_quote`
    would; the cache is transparent to every caller."""
    cached = await cache.get(symbol)
    if cached is not None:
        return cached
    quote = await provider.get_quote(symbol)
    await cache.set(symbol, quote)
    return quote


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


async def _fetch_daily_bars_best_effort(
    provider: MarketDataProvider, symbol: str, *, settings: Settings
) -> list[Bar]:
    """The network half of the daily-bars refresh, kept outside db_lock so it
    can overlap with other symbols' db work. Best-effort: a provider failure
    here is logged and swallowed, same as before the fetch/persist split --
    an empty result just means _persist_daily_bars_best_effort has nothing to
    write, exactly as if the provider had returned no bars."""
    try:
        return await provider.get_daily_history(symbol, days=_daily_history_fetch_days(settings))
    except Exception:
        logger.warning("daily bar fetch failed for %s", symbol, exc_info=True)
        return []


def _persist_daily_bars_best_effort(db: Session, symbol: str, bars: list[Bar]) -> None:
    """The db half. Its own transaction: a bad row here must not roll back
    the snapshot already committed moments earlier in the same db_lock
    section, because a stale or missing baseline degrades Phase 6/7's
    confidence in a signal -- it does not invalidate the quote this cycle
    already captured successfully."""
    try:
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
        logger.warning("daily bar persistence failed for %s", symbol, exc_info=True)


async def ingest_all(
    db: Session, provider: MarketDataProvider, *, settings: Settings | None = None
) -> list[IngestResult]:
    """Ingest every symbol tracked by any watchlist.

    Up to `settings.worker_symbol_concurrency` symbols are in flight at once,
    bounded by a semaphore -- the dominant cost per symbol is two provider
    network calls (quote, daily history), and those genuinely overlap under
    asyncio. All db work still happens through the one `db` Session passed
    in, serialised by a shared db_lock (see ingest_symbol) rather than by
    running the batch sequentially: only one coroutine is ever inside a
    Session call at a time, but a slow provider response for one symbol no
    longer blocks every other symbol's db work behind it.

    Each symbol still independently commits or rolls back, so a slow or
    failing provider for one symbol cannot corrupt the rest of the batch --
    the concurrency bound just controls how many are in flight together.
    """
    settings = settings or get_settings()
    symbols = wl_repo.get_all_tracked_symbols(db)
    if not symbols:
        return []

    cache = QuoteCache(settings)
    semaphore = asyncio.Semaphore(settings.worker_symbol_concurrency)
    db_lock = asyncio.Lock()

    async def _bounded(symbol: str) -> IngestResult:
        async with semaphore:
            return await ingest_symbol(
                db, provider, symbol, settings=settings, cache=cache, db_lock=db_lock
            )

    try:
        results = list(await asyncio.gather(*(_bounded(symbol) for symbol in symbols)))
    finally:
        await cache.close()

    for result in results:
        _log_result(result)
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
