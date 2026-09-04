"""Concurrent mark-seen through the actual service layer -- not just the raw
repository (already proven in Phase 2's TestSeenStateIsMonotonic), but with
the ownership check and future-clamp change_feed_service.mark_seen adds on
top of it.

Real threads on separate connections, per the pattern established in
test_concurrency.py: work on one shared connection is serialised by
definition, so the race this guards against could never occur there.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.models.user import User
from app.models.watchlist import Watchlist
from app.services import change_feed_service
from tests.conftest import postgres_required

pytestmark = [pytest.mark.integration, postgres_required]

THREADS = 12


@pytest.fixture
def committed_watchlist(db_engine: Engine):
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    setup = factory()
    user = User(email=f"feed-race-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    setup.add(user)
    setup.flush()
    watchlist = Watchlist(user_id=user.id, name=f"Race {uuid.uuid4().hex[:6]}")
    setup.add(watchlist)
    setup.commit()
    ids = (user.id, watchlist.id)
    setup.close()

    yield ids

    cleanup = factory()
    cleanup.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": ids[0]})
    cleanup.commit()
    cleanup.close()


class TestConcurrentMarkSeen:
    def test_simultaneous_mark_seen_converges_to_the_latest_timestamp(
        self, db_engine: Engine, committed_watchlist
    ):
        user_id, watchlist_id = committed_watchlist
        factory = sessionmaker(bind=db_engine, expire_on_commit=False)
        base = datetime.now(UTC) - timedelta(hours=1)
        timestamps = [base + timedelta(seconds=i) for i in range(THREADS)]
        latest_timestamp = max(timestamps)

        barrier = threading.Barrier(THREADS)

        def call(i: int):
            session = factory()
            try:
                session.execute(text("SELECT 1"))
                barrier.wait(timeout=10)
                state = change_feed_service.mark_seen(
                    session,
                    watchlist_id=watchlist_id,
                    user_id=user_id,
                    seen_at=timestamps[i],
                    last_seen_event_id=None,
                )
                session.commit()
                return state.last_seen_at
            except Exception as exc:
                session.rollback()
                return exc
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            futures = [pool.submit(call, i) for i in range(THREADS)]
            results = [f.result() for f in as_completed(futures)]

        errors = [r for r in results if isinstance(r, Exception)]
        assert errors == [], f"unexpected errors: {errors}"

        check = factory()
        try:
            from app.infrastructure.database.repositories import seen_state as seen_state_repo

            final_state = seen_state_repo.get_seen_state(
                check, user_id=user_id, watchlist_id=watchlist_id
            )
            assert final_state.last_seen_at == latest_timestamp
        finally:
            check.close()

    def test_no_caller_receives_an_error_regardless_of_arrival_order(
        self, db_engine: Engine, committed_watchlist
    ):
        """Two browser tabs firing mark-seen in either order must both
        succeed -- neither should ever see a failure just because it lost a
        race."""
        user_id, watchlist_id = committed_watchlist
        factory = sessionmaker(bind=db_engine, expire_on_commit=False)

        barrier = threading.Barrier(THREADS)

        def call(_: int):
            session = factory()
            try:
                session.execute(text("SELECT 1"))
                barrier.wait(timeout=10)
                change_feed_service.mark_seen(
                    session,
                    watchlist_id=watchlist_id,
                    user_id=user_id,
                    seen_at=datetime.now(UTC),
                    last_seen_event_id=None,
                )
                session.commit()
                return None
            except Exception as exc:
                session.rollback()
                return exc
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            futures = [pool.submit(call, i) for i in range(THREADS)]
            results = [f.result() for f in as_completed(futures)]

        assert all(r is None for r in results), results
