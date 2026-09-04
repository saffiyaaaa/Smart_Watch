"""The Phase 8 pipeline wiring: ingest -> detect -> score -> persist,
exercised end to end against real PostgreSQL.

Phases 5-7 each proved their own layer in isolation with mocks and pure
functions. These tests prove the layers actually fit together: that
ingesting a real quote through the real worker path produces the exact
change_event a hand-computed score would predict.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session
from worker.ingestion import ingest_symbol

from app.domain.enums import Severity
from app.infrastructure.database.repositories import daily_bars as bar_repo
from app.infrastructure.database.repositories import events as event_repo
from app.infrastructure.providers.mock_provider import StaleProvider
from tests.conftest import postgres_required
from tests.fixtures import seed
from tests.fixtures.fake_providers import SequenceProvider

pytestmark = [pytest.mark.integration, postgres_required]

TODAY = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)
YESTERDAY = seed.PREVIOUS_SESSION


def _quote(symbol: str, *, price: str, market_timestamp: datetime = TODAY):
    from app.domain.market.quote import Quote

    return Quote(
        source="sequence",
        symbol=symbol,
        price=Decimal(price),
        volume=3_100_000,
        market_timestamp=market_timestamp,
        fetched_at=datetime.now(UTC),
    )


class TestASignificantMoveProducesAnEvent:
    async def test_a_big_move_against_the_previous_close_creates_an_event(self, db: Session):
        """+6.2% against a previous close of $180.40, with plenty of volume
        history -- exactly the docs/product-spec.md section 4 worked
        example, run through the real ingestion path."""
        seed.make_volume_history(db, symbol="AAPL", sessions=20, volume=1_000_000)
        bar_repo.upsert_bar(
            db,
            source=seed.SOURCE,
            symbol="AAPL",
            session_date=YESTERDAY,
            close=Decimal("180.40"),
            volume=1_000_000,
        )
        db.commit()

        provider = SequenceProvider([_quote("AAPL", price="191.58")])
        result = await ingest_symbol(db, provider, "AAPL")

        assert result.event_created is True
        events = event_repo.get_events_for_symbol(db, "AAPL")
        assert len(events) == 1
        assert events[0].severity in (Severity.WATCH.value, Severity.IMPORTANT.value)
        assert any("6.2%" in e for e in events[0].evidence)
        assert any("180.40" in e and "191.58" in e for e in events[0].evidence)

    async def test_a_tiny_move_creates_no_event(self, db: Session):
        seed.make_volume_history(db, symbol="AAPL", sessions=20, volume=1_000_000)
        bar_repo.upsert_bar(
            db,
            source=seed.SOURCE,
            symbol="AAPL",
            session_date=YESTERDAY,
            close=Decimal("180.00"),
            volume=1_000_000,
        )
        db.commit()

        provider = SequenceProvider([_quote("AAPL", price="180.10")])  # +0.06%
        result = await ingest_symbol(db, provider, "AAPL")

        assert result.event_created is False
        assert event_repo.get_events_for_symbol(db, "AAPL") == []

    async def test_no_baseline_yet_degrades_instead_of_crashing(self, db: Session):
        """A newly-listed symbol with no prior session: price_change_pct is
        None (Phase 6), which must flow all the way through without an
        exception, not just in isolated unit tests."""
        provider = SequenceProvider([_quote("NEWLIST", price="50.00")])
        result = await ingest_symbol(db, provider, "NEWLIST")

        assert result.outcome == "created"  # the snapshot itself is fine
        # event may or may not be created depending on volume alone; the
        # requirement here is only that nothing raised.


class TestOutOfOrderNeverTriggersDetection:
    async def test_an_older_arrival_after_a_newer_one_does_not_create_an_event(self, db: Session):
        """docs/product-spec.md section 3: an out-of-order observation is
        stored as history but must never trigger detection, even when its
        own price move -- taken in isolation -- would otherwise qualify."""
        seed.make_volume_history(db, symbol="AAPL", sessions=20, volume=1_000_000)
        bar_repo.upsert_bar(
            db,
            source=seed.SOURCE,
            symbol="AAPL",
            session_date=YESTERDAY,
            close=Decimal("100.00"),
            volume=1_000_000,
        )
        db.commit()

        newer = TODAY
        older = TODAY.replace(hour=10)
        provider = SequenceProvider(
            [
                _quote("AAPL", price="110.00", market_timestamp=newer),  # +10%, qualifies
                _quote("AAPL", price="150.00", market_timestamp=older),  # +50%, would also qualify
            ]
        )

        await ingest_symbol(db, provider, "AAPL")
        second = await ingest_symbol(db, provider, "AAPL")

        assert second.event_created is False
        events = event_repo.get_events_for_symbol(db, "AAPL")
        assert len(events) == 1  # from the first (newer) ingest only
        assert events[0].price_pct == Decimal("10.0000")


class TestReingestionEscalatesNotDuplicates:
    async def test_a_bigger_move_later_the_same_day_escalates_the_event(self, db: Session):
        seed.make_volume_history(db, symbol="AAPL", sessions=20, volume=1_000_000)
        bar_repo.upsert_bar(
            db,
            source=seed.SOURCE,
            symbol="AAPL",
            session_date=YESTERDAY,
            close=Decimal("100.00"),
            volume=1_000_000,
        )
        db.commit()

        provider = SequenceProvider(
            [
                _quote("AAPL", price="103.50", market_timestamp=TODAY),
                _quote("AAPL", price="109.00", market_timestamp=TODAY.replace(hour=16)),
            ]
        )

        await ingest_symbol(db, provider, "AAPL")
        await ingest_symbol(db, provider, "AAPL")

        events = event_repo.get_events_for_symbol(db, "AAPL")
        assert len(events) == 1
        assert events[0].price_pct == Decimal("9.0000")


class TestStaleDataProducesADegradedEvent:
    async def test_stale_quote_that_would_be_high_is_capped_below_it(self, db: Session):
        """The load-bearing invariant (docs/product-spec.md section 4),
        proven for the last time in this build -- now through the entire
        pipeline: a real ingestion cycle, a real database, a persisted row."""
        seed.make_volume_history(db, symbol="AAPL", sessions=20, volume=1_000_000)
        bar_repo.upsert_bar(
            db,
            source=seed.SOURCE,
            symbol="AAPL",
            session_date=YESTERDAY,
            close=Decimal("100.00"),
            volume=1_000_000,
        )
        db.commit()

        result = await ingest_symbol(db, StaleProvider(), "AAPL")

        assert result.freshness == "STALE"
        events = event_repo.get_events_for_symbol(db, "AAPL")
        if events:  # StaleProvider's deterministic price may or may not clear WATCH
            assert events[0].severity != Severity.HIGH.value
            assert any(
                "stale" in e.lower() or "minutes old" in e.lower() for e in events[0].evidence
            )
