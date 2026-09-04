"""The Phase 8 gate: last-seen state and the change feed, at the service
layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.config import get_settings
from app.services import change_feed_service
from tests.conftest import postgres_required
from tests.fixtures import seed

pytestmark = [pytest.mark.integration, postgres_required]


class TestFirstVisit:
    def test_user_with_no_seen_state_gets_first_visit_true(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        seed.add_item(db, watchlist=wl, symbol="AAPL")
        seed.make_event(db, symbol="AAPL", score=60, detected_at=datetime.now(UTC))
        db.commit()

        result = change_feed_service.get_change_feed(
            db, watchlist_id=wl.id, user_id=user.id, settings=get_settings()
        )

        assert result.first_visit is True
        assert result.last_seen_at is None
        assert len(result.events) == 1

    def test_events_outside_the_lookback_window_are_excluded(self, db: Session):
        settings = get_settings()
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        seed.add_item(db, watchlist=wl, symbol="AAPL")
        too_old = datetime.now(UTC) - timedelta(hours=settings.first_visit_lookback_hours + 1)
        seed.make_event(db, symbol="AAPL", score=60, detected_at=too_old)
        db.commit()

        result = change_feed_service.get_change_feed(
            db, watchlist_id=wl.id, user_id=user.id, settings=settings
        )

        assert result.events == []

    def test_first_visit_is_capped_at_the_configured_max(self, db: Session):
        settings = get_settings()
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        now = datetime.now(UTC)
        for i in range(settings.first_visit_max_events + 5):
            symbol = f"SYM{i}"
            seed.add_item(db, watchlist=wl, symbol=symbol)
            seed.make_event(
                db, symbol=symbol, trading_day=seed.FIXED_SESSION, score=60, detected_at=now
            )
        db.commit()

        result = change_feed_service.get_change_feed(
            db, watchlist_id=wl.id, user_id=user.id, settings=settings
        )

        assert len(result.events) == settings.first_visit_max_events


class TestReturningUser:
    def test_only_events_after_the_cursor_are_returned(self, db: Session):
        settings = get_settings()
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        seed.add_item(db, watchlist=wl, symbol="AAPL")
        seed.add_item(db, watchlist=wl, symbol="MSFT")

        cursor = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)
        change_feed_service.mark_seen(
            db, watchlist_id=wl.id, user_id=user.id, seen_at=cursor, last_seen_event_id=None
        )
        seed.make_event(
            db,
            symbol="AAPL",
            trading_day=seed.PREVIOUS_SESSION,
            detected_at=cursor - timedelta(hours=1),
        )
        seed.make_event(
            db,
            symbol="MSFT",
            trading_day=seed.FIXED_SESSION,
            detected_at=cursor + timedelta(hours=1),
        )
        db.commit()

        result = change_feed_service.get_change_feed(
            db, watchlist_id=wl.id, user_id=user.id, settings=settings
        )

        assert result.first_visit is False
        assert [e.symbol for e in result.events] == ["MSFT"]

    def test_marking_seen_removes_those_events_from_the_next_fetch(self, db: Session):
        """ "Events already seen do not repeatedly appear as new" -- the
        Phase 8 gate, at the service layer."""
        settings = get_settings()
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        seed.add_item(db, watchlist=wl, symbol="AAPL")
        seed.make_event(db, symbol="AAPL", detected_at=datetime.now(UTC))
        db.commit()

        first = change_feed_service.get_change_feed(
            db, watchlist_id=wl.id, user_id=user.id, settings=settings
        )
        assert len(first.events) == 1

        change_feed_service.mark_seen(
            db,
            watchlist_id=wl.id,
            user_id=user.id,
            seen_at=datetime.now(UTC),
            last_seen_event_id=first.events[0].id,
        )
        db.commit()

        second = change_feed_service.get_change_feed(
            db, watchlist_id=wl.id, user_id=user.id, settings=settings
        )
        assert second.events == []
        assert second.first_visit is False

    def test_mark_seen_is_idempotent(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        moment = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)

        for _ in range(5):
            state = change_feed_service.mark_seen(
                db, watchlist_id=wl.id, user_id=user.id, seen_at=moment, last_seen_event_id=None
            )
        assert state.last_seen_at == moment

    def test_seen_at_cannot_be_pushed_into_the_future(self, db: Session):
        """A malicious or buggy client setting seen_at far in the future
        must not permanently suppress events that have not happened yet."""
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        far_future = datetime.now(UTC) + timedelta(days=3650)

        state = change_feed_service.mark_seen(
            db, watchlist_id=wl.id, user_id=user.id, seen_at=far_future, last_seen_event_id=None
        )

        assert state.last_seen_at < far_future
        assert state.last_seen_at <= datetime.now(UTC)


class TestDifferentUsersHaveIndependentCursors:
    def test_two_users_watching_the_same_symbol_see_independent_feeds(self, db: Session):
        """Why change_events has no user_id (Phase 2): one market event,
        independent per-user cursors."""
        alice = seed.make_user(db, email=seed.unique_email())
        bob = seed.make_user(db, email=seed.unique_email())
        wl_a = seed.make_watchlist(db, user=alice)
        wl_b = seed.make_watchlist(db, user=bob)
        seed.add_item(db, watchlist=wl_a, symbol="AAPL")
        seed.add_item(db, watchlist=wl_b, symbol="AAPL")

        event_time = datetime.now(UTC)
        seed.make_event(db, symbol="AAPL", detected_at=event_time)
        db.commit()

        # Alice has already seen it; Bob has not.
        change_feed_service.mark_seen(
            db,
            watchlist_id=wl_a.id,
            user_id=alice.id,
            seen_at=event_time + timedelta(seconds=1),
            last_seen_event_id=None,
        )
        db.commit()

        settings = get_settings()
        alice_feed = change_feed_service.get_change_feed(
            db, watchlist_id=wl_a.id, user_id=alice.id, settings=settings
        )
        bob_feed = change_feed_service.get_change_feed(
            db, watchlist_id=wl_b.id, user_id=bob.id, settings=settings
        )

        assert alice_feed.events == []
        assert alice_feed.first_visit is False
        assert len(bob_feed.events) == 1
        assert bob_feed.first_visit is True


class TestOwnershipIsEnforced:
    def test_cannot_read_another_users_change_feed(self, db: Session):
        alice = seed.make_user(db, email=seed.unique_email())
        bob = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=alice)

        with pytest.raises(NotFoundError):
            change_feed_service.get_change_feed(
                db, watchlist_id=wl.id, user_id=bob.id, settings=get_settings()
            )

    def test_cannot_mark_another_users_watchlist_seen(self, db: Session):
        alice = seed.make_user(db, email=seed.unique_email())
        bob = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=alice)

        with pytest.raises(NotFoundError):
            change_feed_service.mark_seen(
                db,
                watchlist_id=wl.id,
                user_id=bob.id,
                seen_at=datetime.now(UTC),
                last_seen_event_id=None,
            )


class TestOrderingByScoreThenRecency:
    def test_highest_score_leads_even_if_less_recent(self, db: Session):
        """Uses a real "now"-relative cursor, not a fixed historical date:
        with no seen state the service applies the *real* first-visit
        lookback window, which a fixed-past-date event would fall outside of
        regardless of when the test happens to run."""
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        base = datetime.now(UTC)

        change_feed_service.mark_seen(
            db,
            watchlist_id=wl.id,
            user_id=user.id,
            seen_at=base - timedelta(hours=2),
            last_seen_event_id=None,
        )
        db.commit()

        for sym, score, minutes_ago in (("AAPL", 30, 0), ("MSFT", 90, 60), ("NVDA", 60, 30)):
            seed.add_item(db, watchlist=wl, symbol=sym)
            seed.make_event(
                db, symbol=sym, score=score, detected_at=base - timedelta(minutes=minutes_ago)
            )
        db.commit()

        result = change_feed_service.get_change_feed(
            db, watchlist_id=wl.id, user_id=user.id, settings=get_settings()
        )
        assert [e.symbol for e in result.events] == ["MSFT", "NVDA", "AAPL"]
