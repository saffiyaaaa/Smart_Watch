"""Repository behaviour against a real database."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.domain.enums import EventType, Freshness, Severity
from app.infrastructure.database.repositories import (
    daily_bars as bar_repo,
)
from app.infrastructure.database.repositories import (
    events as event_repo,
)
from app.infrastructure.database.repositories import (
    seen_state as seen_repo,
)
from app.infrastructure.database.repositories import (
    snapshots as snap_repo,
)
from app.infrastructure.database.repositories import (
    watchlists as wl_repo,
)
from tests.conftest import postgres_required
from tests.fixtures import seed

pytestmark = [pytest.mark.integration, postgres_required]


class TestSnapshotsAreAppendOnly:
    def test_repository_exposes_no_mutation_functions(self):
        """The enforcement mechanism for "snapshots are immutable".

        A convention erodes the first time someone is in a hurry. A function
        that does not exist cannot be called, and this test fails the moment
        someone adds one.
        """
        public = {
            name
            for name, obj in inspect.getmembers(snap_repo, inspect.isfunction)
            if not name.startswith("_") and obj.__module__ == snap_repo.__name__
        }
        forbidden = {n for n in public if any(v in n for v in ("update", "delete", "set", "edit"))}
        assert forbidden == set(), f"snapshot repository must stay append-only, found {forbidden}"

    def test_insert_is_idempotent(self, db: Session):
        ts = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)
        kwargs = {
            "source": "mock",
            "symbol": "AAPL",
            "price": Decimal("180.00"),
            "volume": 1_000,
            "market_timestamp": ts,
            "ingest_freshness": Freshness.FRESH,
        }

        first = snap_repo.insert_snapshot(db, **kwargs)
        second = snap_repo.insert_snapshot(db, **kwargs)

        assert first is not None
        # None is the signal "already known" -- the worker uses it to skip
        # re-running change detection on a repeat observation.
        assert second is None

    def test_duplicate_with_different_price_does_not_overwrite(self, db: Session):
        """Re-observing an instant must not let a later, different value
        silently replace a recorded fact."""
        ts = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)
        snap_repo.insert_snapshot(
            db,
            source="mock",
            symbol="AAPL",
            price=Decimal("180.00"),
            volume=1_000,
            market_timestamp=ts,
            ingest_freshness=Freshness.FRESH,
        )
        snap_repo.insert_snapshot(
            db,
            source="mock",
            symbol="AAPL",
            price=Decimal("999.00"),
            volume=1_000,
            market_timestamp=ts,
            ingest_freshness=Freshness.FRESH,
        )

        assert snap_repo.get_latest(db, "AAPL").price == Decimal("180.000000")


class TestLatestIsByMarketTimeNotArrivalOrder:
    def test_out_of_order_arrival_does_not_become_latest(self, db: Session):
        """Failure-matrix row 8. A provider can deliver an older quote after a
        newer one; ordering by the autoincrement id would let that stale value
        win."""
        newer = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)
        older = newer - timedelta(hours=1)

        seed.make_snapshot(db, symbol="AAPL", price="200.00", market_timestamp=newer)
        # Inserted second, so it has the higher id -- but the earlier timestamp.
        seed.make_snapshot(db, symbol="AAPL", price="100.00", market_timestamp=older)

        latest = snap_repo.get_latest(db, "AAPL")
        assert latest.price == Decimal("200.000000")
        assert latest.market_timestamp == newer

    def test_older_observation_is_still_stored_as_history(self, db: Session):
        newer = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)
        seed.make_snapshot(db, symbol="AAPL", market_timestamp=newer)
        seed.make_snapshot(db, symbol="AAPL", market_timestamp=newer - timedelta(hours=1))

        assert len(snap_repo.get_history(db, "AAPL")) == 2

    def test_get_latest_for_symbols_returns_one_row_each(self, db: Session):
        ts = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)
        for sym, price in (("AAPL", "180.00"), ("MSFT", "400.00")):
            seed.make_snapshot(
                db, symbol=sym, price=price, market_timestamp=ts - timedelta(hours=1)
            )
            seed.make_snapshot(db, symbol=sym, price=price, market_timestamp=ts)

        latest = snap_repo.get_latest_for_symbols(db, ["AAPL", "MSFT"])
        assert set(latest) == {"AAPL", "MSFT"}
        assert all(s.market_timestamp == ts for s in latest.values())

    def test_get_latest_for_symbols_handles_empty_input(self, db: Session):
        assert snap_repo.get_latest_for_symbols(db, []) == {}

    def test_get_at_or_before_returns_the_price_in_force(self, db: Session):
        base = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)
        seed.make_snapshot(db, symbol="AAPL", price="100.00", market_timestamp=base)
        seed.make_snapshot(
            db, symbol="AAPL", price="200.00", market_timestamp=base + timedelta(hours=2)
        )

        at = snap_repo.get_at_or_before(db, "AAPL", base + timedelta(hours=1))
        assert at.price == Decimal("100.000000")


class TestDailyBarBaselines:
    def test_upsert_corrects_a_revised_bar(self, db: Session):
        """Unlike snapshots, bars are revised: a bar fetched before the close is
        provisional and exchanges restate volume after settlement."""
        args = {"source": "mock", "symbol": "AAPL", "session_date": seed.PREVIOUS_SESSION}
        bar_repo.upsert_bar(db, **args, close=Decimal("175.00"), volume=1_000)
        bar_repo.upsert_bar(db, **args, close=Decimal("176.50"), volume=2_000)

        bar = bar_repo.get_previous_close(db, "AAPL", seed.FIXED_SESSION)
        assert bar.close == Decimal("176.500000")
        assert bar.volume == 2_000

    def test_previous_close_excludes_the_current_session(self, db: Session):
        """Strictly before: an in-progress session must not be its own baseline,
        which would make every percentage change zero."""
        seed.make_daily_bar(db, session_date=seed.FIXED_SESSION, close="999.00")
        seed.make_daily_bar(db, session_date=seed.PREVIOUS_SESSION, close="175.00")

        assert bar_repo.get_previous_close(db, "AAPL", seed.FIXED_SESSION).close == Decimal(
            "175.000000"
        )

    def test_previous_close_is_none_without_history(self, db: Session):
        assert bar_repo.get_previous_close(db, "NOHIST", seed.FIXED_SESSION) is None

    def test_recent_bars_excludes_current_session_and_respects_limit(self, db: Session):
        seed.make_volume_history(db, symbol="AAPL", sessions=25)
        seed.make_daily_bar(db, session_date=seed.FIXED_SESSION, close="999.00", volume=999)

        bars = bar_repo.get_recent_bars(db, "AAPL", before=seed.FIXED_SESSION, limit=20)
        assert len(bars) == 20
        assert all(b.session_date < seed.FIXED_SESSION for b in bars)


class TestEventEscalation:
    def _upsert(self, db: Session, score: int, severity: Severity, evidence: list[str]):
        return event_repo.upsert_event(
            db,
            symbol="AAPL",
            trading_day=seed.FIXED_SESSION,
            event_type=EventType.PRICE_MOVE,
            score=score,
            severity=severity,
            evidence=evidence,
            price_pct=Decimal("5.0"),
            volume_ratio=None,
            confidence=Decimal("1.0"),
        )

    def test_first_event_is_created(self, db: Session):
        assert self._upsert(db, 40, Severity.WATCH, ["first"]) is not None

    def test_equal_score_does_not_duplicate_or_update(self, db: Session):
        """Why a 5-minute worker tick does not produce 78 identical events."""
        self._upsert(db, 40, Severity.WATCH, ["first"])
        assert self._upsert(db, 40, Severity.WATCH, ["again"]) is None

        events = event_repo.get_events_for_symbol(db, "AAPL")
        assert len(events) == 1
        assert events[0].evidence == ["first"]

    def test_lower_score_cannot_downgrade_an_event(self, db: Session):
        self._upsert(db, 80, Severity.HIGH, ["big move"])
        assert self._upsert(db, 30, Severity.WATCH, ["small move"]) is None
        assert event_repo.get_events_for_symbol(db, "AAPL")[0].score == 80

    def test_higher_score_escalates_in_place(self, db: Session):
        self._upsert(db, 40, Severity.WATCH, ["3% move"])
        escalated = self._upsert(db, 85, Severity.HIGH, ["9% move"])

        assert escalated is not None
        events = event_repo.get_events_for_symbol(db, "AAPL")
        assert len(events) == 1
        assert (events[0].score, events[0].severity) == (85, "HIGH")
        assert events[0].evidence == ["9% move"]

    def test_different_trading_days_are_separate_events(self, db: Session):
        self._upsert(db, 40, Severity.WATCH, ["today"])
        event_repo.upsert_event(
            db,
            symbol="AAPL",
            trading_day=seed.PREVIOUS_SESSION,
            event_type=EventType.PRICE_MOVE,
            score=40,
            severity=Severity.WATCH,
            evidence=["yesterday"],
            price_pct=Decimal("5.0"),
            volume_ratio=None,
            confidence=Decimal("1.0"),
        )
        assert len(event_repo.get_events_for_symbol(db, "AAPL")) == 2


class TestChangeFeedQuery:
    def test_only_returns_symbols_in_the_watchlist(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        seed.add_item(db, watchlist=wl, symbol="AAPL")

        seed.make_event(db, symbol="AAPL", score=60)
        seed.make_event(db, symbol="TSLA", score=90)  # not watched

        events = event_repo.get_events_for_watchlist(
            db, watchlist_id=wl.id, since=None, min_score=20, limit=50
        )
        assert [e.symbol for e in events] == ["AAPL"]

    def test_filters_below_the_minimum_score(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        seed.add_item(db, watchlist=wl, symbol="AAPL")
        seed.add_item(db, watchlist=wl, symbol="MSFT")

        seed.make_event(db, symbol="AAPL", score=60)
        seed.make_event(db, symbol="MSFT", score=25, severity=Severity.WATCH)

        events = event_repo.get_events_for_watchlist(
            db, watchlist_id=wl.id, since=None, min_score=50, limit=50
        )
        assert [e.symbol for e in events] == ["AAPL"]

    def test_since_excludes_already_seen_events(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        seed.add_item(db, watchlist=wl, symbol="AAPL")
        seed.add_item(db, watchlist=wl, symbol="MSFT")

        cursor = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)
        seed.make_event(db, symbol="AAPL", detected_at=cursor - timedelta(hours=1))
        seed.make_event(db, symbol="MSFT", detected_at=cursor + timedelta(hours=1))

        events = event_repo.get_events_for_watchlist(
            db, watchlist_id=wl.id, since=cursor, min_score=20, limit=50
        )
        assert [e.symbol for e in events] == ["MSFT"]

    def test_ordered_by_score_then_recency(self, db: Session):
        """An attention feed leads with the most important item, even when
        something less important happened more recently."""
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        base = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)

        for sym, score, minutes in (("AAPL", 30, 60), ("MSFT", 90, 0), ("NVDA", 60, 30)):
            seed.add_item(db, watchlist=wl, symbol=sym)
            seed.make_event(
                db, symbol=sym, score=score, detected_at=base + timedelta(minutes=minutes)
            )

        events = event_repo.get_events_for_watchlist(
            db, watchlist_id=wl.id, since=None, min_score=20, limit=50
        )
        assert [e.symbol for e in events] == ["MSFT", "NVDA", "AAPL"]


class TestSeenStateIsMonotonic:
    def _advance(self, db: Session, user, wl, moment, event_id=None):
        return seen_repo.advance_seen_state(
            db,
            user_id=user.id,
            watchlist_id=wl.id,
            seen_at=moment,
            last_seen_event_id=event_id,
        )

    def test_first_call_creates_state(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        moment = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)

        state = self._advance(db, user, wl, moment)
        assert state.last_seen_at == moment

    def test_repeated_call_is_a_no_op(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        moment = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)

        for _ in range(5):
            self._advance(db, user, wl, moment)

        assert seen_repo.get_seen_state(db, user_id=user.id, watchlist_id=wl.id).last_seen_at == (
            moment
        )

    def test_cursor_moves_forward(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        early = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)

        self._advance(db, user, wl, early)
        state = self._advance(db, user, wl, early + timedelta(hours=1))
        assert state.last_seen_at == early + timedelta(hours=1)

    def test_cursor_never_moves_backwards(self, db: Session):
        """The two-tabs case. A late-arriving request carrying an earlier
        timestamp must not rewind the cursor and resurface seen events."""
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        late = datetime(2026, 3, 11, 14, 0, tzinfo=UTC)

        self._advance(db, user, wl, late)
        state = self._advance(db, user, wl, late - timedelta(hours=2))
        assert state.last_seen_at == late

    def test_null_event_id_stays_null(self, db: Session):
        """Regression guard: COALESCE(..., 0) here would store 0, which is not
        a real event id and would violate the foreign key."""
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        moment = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)

        self._advance(db, user, wl, moment)
        state = self._advance(db, user, wl, moment + timedelta(minutes=1))
        assert state.last_seen_event_id is None

    def test_event_id_advances_and_never_regresses(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        moment = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)
        older = seed.make_event(db, symbol="AAPL", trading_day=seed.PREVIOUS_SESSION)
        newer = seed.make_event(db, symbol="AAPL", trading_day=seed.FIXED_SESSION)

        self._advance(db, user, wl, moment, event_id=newer.id)
        state = self._advance(db, user, wl, moment, event_id=older.id)
        assert state.last_seen_event_id == newer.id

    def test_users_have_independent_cursors(self, db: Session):
        """Why change_events has no user_id: one market event, many cursors."""
        alice = seed.make_user(db, email=seed.unique_email())
        bob = seed.make_user(db, email=seed.unique_email())
        wl_a = seed.make_watchlist(db, user=alice)
        wl_b = seed.make_watchlist(db, user=bob)
        moment = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)

        self._advance(db, alice, wl_a, moment)
        self._advance(db, bob, wl_b, moment + timedelta(hours=3))

        a = seen_repo.get_seen_state(db, user_id=alice.id, watchlist_id=wl_a.id)
        b = seen_repo.get_seen_state(db, user_id=bob.id, watchlist_id=wl_b.id)
        assert a.last_seen_at != b.last_seen_at


class TestWatchlistOwnership:
    def test_get_watchlist_scoped_to_owner(self, db: Session):
        alice = seed.make_user(db, email=seed.unique_email())
        bob = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=alice)

        assert wl_repo.get_watchlist(db, watchlist_id=wl.id, user_id=alice.id) is not None
        # "not yours" and "not found" are the same answer, so a probe cannot
        # confirm that another user's id exists.
        assert wl_repo.get_watchlist(db, watchlist_id=wl.id, user_id=bob.id) is None

    def test_delete_scoped_to_owner(self, db: Session):
        alice = seed.make_user(db, email=seed.unique_email())
        bob = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=alice)

        assert wl_repo.delete_watchlist(db, watchlist_id=wl.id, user_id=bob.id) is False
        assert wl_repo.get_watchlist(db, watchlist_id=wl.id, user_id=alice.id) is not None
        assert wl_repo.delete_watchlist(db, watchlist_id=wl.id, user_id=alice.id) is True

    def test_list_returns_only_own_watchlists(self, db: Session):
        alice = seed.make_user(db, email=seed.unique_email())
        bob = seed.make_user(db, email=seed.unique_email())
        seed.make_watchlist(db, user=alice, name="Alice list")
        seed.make_watchlist(db, user=bob, name="Bob list")

        assert [w.name for w in wl_repo.list_watchlists(db, user_id=alice.id)] == ["Alice list"]


class TestAddSymbolIdempotency:
    def test_adding_twice_yields_one_item(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)

        first = wl_repo.add_symbol(db, watchlist_id=wl.id, symbol="AAPL")
        second = wl_repo.add_symbol(db, watchlist_id=wl.id, symbol="AAPL")

        # Same row returned, no error: adding something already present is a
        # request whose desired state already holds.
        assert first.id == second.id
        assert wl_repo.get_symbols(db, watchlist_id=wl.id) == ["AAPL"]

    def test_remove_is_idempotent(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        wl_repo.add_symbol(db, watchlist_id=wl.id, symbol="AAPL")

        assert wl_repo.remove_symbol(db, watchlist_id=wl.id, symbol="AAPL") is True
        assert wl_repo.remove_symbol(db, watchlist_id=wl.id, symbol="AAPL") is False

    def test_tracked_symbols_are_deduplicated_across_users(self, db: Session):
        """The worker's queue: a hundred users watching AAPL must produce one
        provider call, not a hundred."""
        for _ in range(3):
            user = seed.make_user(db, email=seed.unique_email())
            wl = seed.make_watchlist(db, user=user)
            wl_repo.add_symbol(db, watchlist_id=wl.id, symbol="AAPL")

        assert wl_repo.get_all_tracked_symbols(db) == ["AAPL"]
