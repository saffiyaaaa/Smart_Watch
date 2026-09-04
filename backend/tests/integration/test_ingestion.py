"""The Phase 5 gate, exercised end to end against real PostgreSQL.

MockProvider is the default here rather than a hand-rolled fake, wherever it
is sufficient -- it is real application code, not a test double, so using it
means these tests exercise the same provider path a demo run would.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from freezegun import freeze_time
from sqlalchemy.orm import Session
from worker.ingestion import ingest_all, ingest_symbol

from app.config import Settings
from app.domain.market.quote import Quote
from app.infrastructure.cache import QuoteCache
from app.infrastructure.database.repositories import daily_bars as bar_repo
from app.infrastructure.database.repositories import snapshots as snapshot_repo
from app.infrastructure.providers.exceptions import ProviderUnavailable
from app.infrastructure.providers.mock_provider import (
    MockProvider,
    StaleProvider,
    TimeoutProvider,
)
from app.models.watchlist import Watchlist
from tests.conftest import postgres_required
from tests.fixtures import seed
from tests.fixtures.fake_providers import (
    CallCountingProvider,
    ConcurrencyTrackingProvider,
    HistoryFailsQuoteSucceedsProvider,
    SelectiveFailureProvider,
    SequenceProvider,
)

pytestmark = [pytest.mark.integration, postgres_required]


def _quote(
    symbol: str, *, price: str, market_timestamp: datetime, source: str = "sequence"
) -> Quote:
    return Quote(
        source=source,
        symbol=symbol,
        price=Decimal(price),
        volume=1_000_000,
        market_timestamp=market_timestamp,
        fetched_at=datetime.now(UTC),
    )


class TestOneValidObservationCreatesOneSnapshot:
    async def test_created_outcome_and_one_row(self, db: Session):
        result = await ingest_symbol(db, MockProvider(), "AAPL")

        assert result.outcome == "created"
        assert len(snapshot_repo.get_history(db, "AAPL")) == 1

    async def test_daily_bars_are_persisted_alongside_the_quote(self, db: Session):
        """Bars are the change-detection baseline (Phase 6/7); ingesting a
        quote without them would leave that baseline permanently empty."""
        await ingest_symbol(db, MockProvider(), "AAPL")

        bars = bar_repo.get_recent_bars(db, "AAPL", before=datetime.now(UTC).date(), limit=100)
        assert len(bars) > 0


class TestDuplicateObservationsDoNotDuplicate:
    async def test_the_same_observation_ingested_twice_creates_no_duplicate(self, db: Session):
        """MockProvider's quote is always "now"; freezing time is what makes
        two separate calls observe the literal same instant, which is the
        actual duplicate-detection scenario -- two calls a millisecond apart
        are two different (both valid) observations, not a duplicate."""
        with freeze_time("2026-03-11 15:30:00", tz_offset=0):
            first = await ingest_symbol(db, MockProvider(), "AAPL")
            second = await ingest_symbol(db, MockProvider(), "AAPL")

        assert first.outcome == "created"
        assert second.outcome == "duplicate"
        assert len(snapshot_repo.get_history(db, "AAPL")) == 1

    async def test_duplicate_ingestion_does_not_raise(self, db: Session):
        with freeze_time("2026-03-11 15:30:00", tz_offset=0):
            await ingest_symbol(db, MockProvider(), "MSFT")
            result = await ingest_symbol(db, MockProvider(), "MSFT")
        assert result.detail is None


class TestOutOfOrderObservations:
    async def test_an_older_observation_does_not_overwrite_the_latest(self, db: Session):
        newer_ts = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)
        older_ts = newer_ts - timedelta(hours=1)

        # Newer arrives first (e.g. this cycle), older arrives second (e.g. a
        # retried request for a delayed earlier cycle finally lands).
        provider = SequenceProvider(
            [
                _quote("AAPL", price="200.00", market_timestamp=newer_ts),
                _quote("AAPL", price="100.00", market_timestamp=older_ts),
            ]
        )

        await ingest_symbol(db, provider, "AAPL")
        await ingest_symbol(db, provider, "AAPL")

        latest = snapshot_repo.get_latest(db, "AAPL")
        assert latest.price == Decimal("200.000000")
        assert latest.market_timestamp == newer_ts

    async def test_the_older_observation_is_still_stored_as_history(self, db: Session):
        newer_ts = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)
        older_ts = newer_ts - timedelta(hours=1)
        provider = SequenceProvider(
            [
                _quote("AAPL", price="200.00", market_timestamp=newer_ts),
                _quote("AAPL", price="100.00", market_timestamp=older_ts),
            ]
        )

        await ingest_symbol(db, provider, "AAPL")
        second = await ingest_symbol(db, provider, "AAPL")

        assert second.outcome == "created"  # a genuinely new fact, just an old one
        assert len(snapshot_repo.get_history(db, "AAPL")) == 2


class TestProviderTimeoutLeavesDataIntact:
    async def test_previous_valid_snapshot_survives_a_timeout(self, db: Session):
        existing = seed.make_snapshot(db, symbol="AAPL", price="180.00")
        db.commit()

        result = await ingest_symbol(db, TimeoutProvider(), "AAPL")

        assert result.outcome == "provider_error"
        latest = snapshot_repo.get_latest(db, "AAPL")
        assert latest.id == existing.id
        assert latest.price == Decimal("180.000000")

    async def test_no_snapshot_is_written_on_timeout(self, db: Session):
        await ingest_symbol(db, TimeoutProvider(), "NEWSYM")
        assert snapshot_repo.get_latest(db, "NEWSYM") is None

    async def test_provider_unavailable_behaves_the_same_way(self, db: Session):
        existing = seed.make_snapshot(db, symbol="AAPL", price="180.00")
        db.commit()

        provider = SelectiveFailureProvider(
            {"AAPL"}, exception_factory=lambda s: ProviderUnavailable(f"down: {s}")
        )
        result = await ingest_symbol(db, provider, "AAPL")

        assert result.outcome == "provider_error"
        assert snapshot_repo.get_latest(db, "AAPL").id == existing.id


class TestStaleDataIsMarkedNotHidden:
    async def test_stale_quote_is_persisted_and_labeled_stale(self, db: Session):
        result = await ingest_symbol(db, StaleProvider(), "AAPL")

        assert result.outcome == "created"
        assert result.freshness == "STALE"
        snapshot = snapshot_repo.get_latest(db, "AAPL")
        assert snapshot.ingest_freshness == "STALE"

    async def test_stale_data_is_never_silently_relabeled_fresh(self, db: Session):
        """The failure-matrix promise in its most literal form: a stale
        observation must never be stored as if it were current."""
        result = await ingest_symbol(db, StaleProvider(), "AAPL")
        assert result.freshness != "FRESH"


class TestBatchIsolation:
    """The Phase 5 gate: one symbol's failure cannot stop the rest of a
    batch, or corrupt data already committed for other symbols."""

    async def test_one_failing_symbol_does_not_block_the_others(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        watchlist = seed.make_watchlist(db, user=user)
        for sym in ("AAPL", "BROKEN", "MSFT"):
            seed.add_item(db, watchlist=watchlist, symbol=sym)
        db.commit()

        provider = SelectiveFailureProvider(
            {"BROKEN"}, exception_factory=lambda s: ProviderUnavailable(f"down: {s}")
        )
        results = await ingest_all(db, provider)

        by_symbol = {r.symbol: r for r in results}
        assert by_symbol["AAPL"].outcome == "created"
        assert by_symbol["MSFT"].outcome == "created"
        assert by_symbol["BROKEN"].outcome == "provider_error"

        assert snapshot_repo.get_latest(db, "AAPL") is not None
        assert snapshot_repo.get_latest(db, "MSFT") is not None
        assert snapshot_repo.get_latest(db, "BROKEN") is None

    async def test_batch_of_five_with_two_failing_ingests_the_other_three(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        watchlist = seed.make_watchlist(db, user=user)
        symbols = ["A1", "BAD1", "A2", "BAD2", "A3"]
        for sym in symbols:
            seed.add_item(db, watchlist=watchlist, symbol=sym)
        db.commit()

        provider = SelectiveFailureProvider(
            {"BAD1", "BAD2"}, exception_factory=lambda s: ProviderUnavailable(f"down: {s}")
        )
        results = await ingest_all(db, provider)

        outcomes = {r.symbol: r.outcome for r in results}
        assert outcomes["BAD1"] == "provider_error"
        assert outcomes["BAD2"] == "provider_error"
        assert outcomes["A1"] == outcomes["A2"] == outcomes["A3"] == "created"
        assert sum(1 for o in outcomes.values() if o == "created") == 3


class TestBarsFailureDoesNotRollBackTheSnapshot:
    async def test_snapshot_survives_a_daily_history_failure(self, db: Session):
        provider = HistoryFailsQuoteSucceedsProvider(
            exception_factory=lambda s: ProviderUnavailable(f"history down: {s}")
        )
        result = await ingest_symbol(db, provider, "AAPL")

        assert result.outcome == "created"
        assert snapshot_repo.get_latest(db, "AAPL") is not None

    async def test_no_bars_were_written_when_history_fails(self, db: Session):
        provider = HistoryFailsQuoteSucceedsProvider(
            exception_factory=lambda s: ProviderUnavailable(f"history down: {s}")
        )
        await ingest_symbol(db, provider, "AAPL")

        bars = bar_repo.get_recent_bars(db, "AAPL", before=datetime.now(UTC).date(), limit=100)
        assert bars == []


class TestIngestAllDiscoversTrackedSymbols:
    async def test_ingests_every_symbol_across_every_watchlist(self, db: Session):
        alice = seed.make_user(db, email=seed.unique_email())
        bob = seed.make_user(db, email=seed.unique_email())
        wl_a = seed.make_watchlist(db, user=alice)
        wl_b = seed.make_watchlist(db, user=bob)
        seed.add_item(db, watchlist=wl_a, symbol="AAPL")
        seed.add_item(db, watchlist=wl_b, symbol="NVDA")
        db.commit()

        results = await ingest_all(db, MockProvider())

        assert {r.symbol for r in results} == {"AAPL", "NVDA"}

    async def test_a_symbol_watched_by_two_users_is_ingested_once(self, db: Session):
        alice = seed.make_user(db, email=seed.unique_email())
        bob = seed.make_user(db, email=seed.unique_email())
        wl_a = seed.make_watchlist(db, user=alice)
        wl_b = seed.make_watchlist(db, user=bob)
        seed.add_item(db, watchlist=wl_a, symbol="AAPL")
        seed.add_item(db, watchlist=wl_b, symbol="AAPL")
        db.commit()

        results = await ingest_all(db, MockProvider())

        assert [r.symbol for r in results] == ["AAPL"]

    async def test_empty_watchlists_produce_no_work(self, db: Session):
        assert await ingest_all(db, MockProvider()) == []


class TestIngestAllConcurrency:
    """The Phase 12 gate: the worker processes symbols concurrently, bounded
    by settings.worker_symbol_concurrency, instead of one at a time."""

    async def test_concurrent_fetches_are_bounded_by_worker_symbol_concurrency(self, db: Session):
        alice = seed.make_user(db, email=seed.unique_email())
        watchlist = seed.make_watchlist(db, user=alice)
        symbols = ["AAPL", "MSFT", "GOOG", "AMZN", "NVDA", "META"]
        for symbol in symbols:
            seed.add_item(db, watchlist=watchlist, symbol=symbol)
        db.commit()

        provider = ConcurrencyTrackingProvider()
        settings = Settings(_env_file=None, worker_symbol_concurrency=2)

        results = await ingest_all(db, provider, settings=settings)

        assert {r.symbol for r in results} == set(symbols)
        assert all(r.outcome == "created" for r in results)
        # Genuine concurrency happened (more than one in flight at once)...
        assert provider.max_concurrent > 1
        # ...but never more than the configured bound.
        assert provider.max_concurrent <= 2

    async def test_higher_concurrency_bound_allows_more_overlap(self, db: Session):
        alice = seed.make_user(db, email=seed.unique_email())
        watchlist = seed.make_watchlist(db, user=alice)
        symbols = [f"SYM{i}" for i in range(6)]
        for symbol in symbols:
            seed.add_item(db, watchlist=watchlist, symbol=symbol)
        db.commit()

        provider = ConcurrencyTrackingProvider()
        settings = Settings(_env_file=None, worker_symbol_concurrency=6)

        await ingest_all(db, provider, settings=settings)

        assert provider.max_concurrent == 6

    async def test_concurrent_run_is_faster_than_sequential(self, db: Session):
        """Compares concurrency=6 against concurrency=1 for the same shape
        of work, rather than asserting an absolute wall-clock threshold --
        per-symbol persistence (each symbol upserts ~40 daily bars, one real
        round trip each) is itself non-trivial and would make a fixed
        threshold flaky across machines. What must hold regardless of
        machine speed is that overlapping the artificial provider delay
        below saves real time compared to not overlapping it; a concurrency
        regression that reintroduced sequential processing would collapse
        this ratio to ~1.0.
        """
        alice = seed.make_user(db, email=seed.unique_email())

        async def _timed_run(symbols: list[str], concurrency: int) -> float:
            # get_all_tracked_symbols (what ingest_all works from) returns
            # every symbol across every watchlist, so the previous run's
            # watchlist has to be gone before this one starts, or the second
            # call would ingest both sets and the timings would no longer be
            # comparable.
            db.query(Watchlist).filter(Watchlist.user_id == alice.id).delete()
            db.commit()

            wl = seed.make_watchlist(db, user=alice, name=f"wl-{concurrency}")
            for symbol in symbols:
                seed.add_item(db, watchlist=wl, symbol=symbol)
            db.commit()

            provider = ConcurrencyTrackingProvider(delay_seconds=0.2)
            settings = Settings(_env_file=None, worker_symbol_concurrency=concurrency)

            started = time.monotonic()
            results = await ingest_all(db, provider, settings=settings)
            assert len(results) == len(symbols)
            return time.monotonic() - started

        sequential_elapsed = await _timed_run([f"SEQ{i}" for i in range(6)], concurrency=1)
        concurrent_elapsed = await _timed_run([f"CONC{i}" for i in range(6)], concurrency=6)

        assert concurrent_elapsed < sequential_elapsed / 1.4


class _FakeRedis:
    """Minimal in-memory stand-in for redis.asyncio.Redis -- see
    tests/unit/test_quote_cache.py for why the cache's own logic does not
    need a live server to verify."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, name: str) -> str | None:
        return self._store.get(name)

    async def set(self, name: str, value: str, ex: int | None = None) -> None:
        self._store[name] = value

    async def aclose(self) -> None:
        pass


class TestQuoteCacheAvoidsRepeatedProviderCalls:
    """The Phase 12 gate: repeated shared quote requests do not
    unnecessarily hammer the provider, within the cache's TTL."""

    async def test_a_second_ingest_within_the_ttl_reuses_the_cached_quote(self, db: Session):
        settings = Settings(_env_file=None, cache_enabled=True, cache_quote_ttl_seconds=300)
        cache = QuoteCache(settings, client=_FakeRedis())
        provider = CallCountingProvider()

        await ingest_symbol(db, provider, "AAPL", settings=settings, cache=cache)
        await ingest_symbol(db, provider, "AAPL", settings=settings, cache=cache)

        assert provider.calls["AAPL"] == 1

    async def test_a_disabled_cache_calls_the_provider_every_time(self, db: Session):
        settings = Settings(_env_file=None, cache_enabled=False)
        cache = QuoteCache(settings, client=_FakeRedis())
        provider = CallCountingProvider()

        await ingest_symbol(db, provider, "MSFT", settings=settings, cache=cache)
        await ingest_symbol(db, provider, "MSFT", settings=settings, cache=cache)

        assert provider.calls["MSFT"] == 2

    async def test_different_symbols_are_cached_independently(self, db: Session):
        settings = Settings(_env_file=None, cache_enabled=True, cache_quote_ttl_seconds=300)
        cache = QuoteCache(settings, client=_FakeRedis())
        provider = CallCountingProvider()

        await ingest_symbol(db, provider, "AAPL", settings=settings, cache=cache)
        await ingest_symbol(db, provider, "MSFT", settings=settings, cache=cache)

        assert provider.calls == {"AAPL": 1, "MSFT": 1}
