"""Concurrency guarantees, exercised with real threads on real connections.

These cannot be tested through the shared `db` fixture: work on one connection
is serialised by definition, so the race the schema is meant to prevent could
never occur and the test would prove nothing.

Each thread gets its own session and commits for real, so the rows genuinely
race inside PostgreSQL. Cleanup is explicit because nothing is rolled back.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import Freshness
from app.infrastructure.database.repositories import snapshots as snapshot_repo
from app.infrastructure.database.repositories import watchlists as wl_repo
from app.models.user import User
from app.models.watchlist import Watchlist
from tests.conftest import postgres_required

pytestmark = [pytest.mark.integration, postgres_required]

THREADS = 12


@pytest.fixture
def committed_watchlist(db_engine: Engine):
    """A user and watchlist committed for real, visible to every connection."""
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    setup = factory()

    user = User(email=f"race-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
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


def _run_racing(session_factory, work, count: int = THREADS) -> list:
    """Run `work(session, i)` in `count` threads that genuinely overlap.

    The barrier is essential, and its absence is a trap worth documenting.
    Without it, thread startup and connection checkout stagger the threads
    enough that the first commit lands before the others reach their critical
    section -- and then a deliberately broken SELECT-then-INSERT implementation
    passes the test too. Measured: without the barrier the naive version
    produced 1 create and 11 clean "already exists" reads; with it, 11 of 12
    raise IntegrityError.

    So: check out the connection first, wait on the barrier, and only then do
    the work. Otherwise the test proves nothing about concurrency.
    """
    barrier = threading.Barrier(count)

    def runner(i: int):
        session: Session = session_factory()
        try:
            # Force a real connection checkout before the barrier, so the
            # threads are not still negotiating the pool when released.
            session.execute(text("SELECT 1"))
            barrier.wait(timeout=10)
            result = work(session, i)
            session.commit()
            return result
        except Exception as exc:
            session.rollback()
            return exc
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(runner, i) for i in range(count)]
        return [f.result() for f in as_completed(futures)]


class TestConcurrentAddSymbol:
    """The Phase 3 gate: concurrent duplicate adds cannot create duplicates."""

    def test_simultaneous_adds_create_exactly_one_row(self, db_engine: Engine, committed_watchlist):
        _, watchlist_id = committed_watchlist
        factory = sessionmaker(bind=db_engine, expire_on_commit=False)

        results = _run_racing(
            factory,
            lambda session, _: (
                wl_repo.add_symbol(session, watchlist_id=watchlist_id, symbol="AAPL").id
            ),
        )

        assert not [r for r in results if isinstance(r, Exception)]
        # Every caller received the same row, not one winner and eleven losers.
        assert len(set(results)) == 1

        check = factory()
        try:
            assert wl_repo.get_symbols(check, watchlist_id=watchlist_id) == ["AAPL"]
        finally:
            check.close()

    def test_no_caller_receives_an_error(self, db_engine: Engine, committed_watchlist):
        """Adding something already present is a request whose desired state
        already holds, so every concurrent caller must see success.

        A SELECT-then-INSERT implementation fails this test with 11 of 12
        callers raising IntegrityError.
        """
        _, watchlist_id = committed_watchlist
        factory = sessionmaker(bind=db_engine, expire_on_commit=False)

        results = _run_racing(
            factory,
            lambda session, _: wl_repo.add_symbol(
                session, watchlist_id=watchlist_id, symbol="MSFT"
            ),
        )

        errors = [r for r in results if isinstance(r, Exception)]
        assert errors == [], f"{len(errors)} of {THREADS} callers failed: {errors[:3]}"

    def test_concurrent_adds_of_different_symbols_all_succeed(
        self, db_engine: Engine, committed_watchlist
    ):
        """The uniqueness constraint must not serialise unrelated work."""
        _, watchlist_id = committed_watchlist
        factory = sessionmaker(bind=db_engine, expire_on_commit=False)
        symbols = [f"SYM{i}" for i in range(THREADS)]

        results = _run_racing(
            factory,
            lambda session, i: wl_repo.add_symbol(
                session, watchlist_id=watchlist_id, symbol=symbols[i]
            ),
        )
        assert not [r for r in results if isinstance(r, Exception)]

        check = factory()
        try:
            assert sorted(wl_repo.get_symbols(check, watchlist_id=watchlist_id)) == sorted(symbols)
        finally:
            check.close()


class TestConcurrentRegistration:
    def test_simultaneous_registration_of_one_email_creates_one_user(self, db_engine: Engine):
        """SELECT-then-INSERT would let several of these through. The unique
        index is what actually prevents it; the service turns the resulting
        IntegrityError into a clean 409 for the losing callers."""
        from app.api.errors import ConflictError
        from app.services.auth_service import register

        factory = sessionmaker(bind=db_engine, expire_on_commit=False)
        email = f"race-{uuid.uuid4().hex[:8]}@example.com"

        try:
            results = _run_racing(
                factory,
                lambda session, _: register(session, email=email, password="a-good-password"),
                count=6,
            )

            created = [r for r in results if not isinstance(r, Exception)]
            conflicts = [r for r in results if isinstance(r, ConflictError)]
            unexpected = [
                r for r in results if isinstance(r, Exception) and not isinstance(r, ConflictError)
            ]

            assert unexpected == [], f"unexpected errors: {unexpected}"
            assert len(created) == 1
            assert len(conflicts) == 5

            check = factory()
            count = check.query(User).filter(User.email == email).count()
            check.close()
            assert count == 1
        finally:
            cleanup = factory()
            cleanup.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})
            cleanup.commit()
            cleanup.close()


class TestConcurrentSnapshotIngestion:
    """The Phase 5/11 gate: two ingestion cycles racing on the exact same
    observation must not create two rows.

    insert_snapshot's docstring already claims ON CONFLICT DO NOTHING
    "resolves correctly even across separate worker processes" -- this test
    is what actually backs that claim under a real race, the way
    TestConcurrentAddSymbol backs the equivalent claim for watchlist items.
    In production this race is between the API worker and, someday, a second
    scheduler instance; a single-session db.flush() test (see
    test_constraints.py's TestSnapshotIdentity) cannot exercise it because
    the conflict on one connection can only ever be detected sequentially.
    """

    def test_simultaneous_identical_observations_create_exactly_one_snapshot(
        self, db_engine: Engine
    ):
        factory = sessionmaker(bind=db_engine, expire_on_commit=False)
        symbol = f"R{uuid.uuid4().hex[:6].upper()}"
        ts = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)

        try:
            results = _run_racing(
                factory,
                lambda session, _: snapshot_repo.insert_snapshot(
                    session,
                    source="race_test",
                    symbol=symbol,
                    price=Decimal("180.00"),
                    volume=1_000_000,
                    market_timestamp=ts,
                    ingest_freshness=Freshness.FRESH,
                ),
            )

            assert not [r for r in results if isinstance(r, Exception)]
            # Exactly one caller got the row back; every other caller's
            # ON CONFLICT DO NOTHING returned None rather than raising or
            # silently creating a second row.
            winners = [r for r in results if r is not None]
            assert len(winners) == 1

            check = factory()
            try:
                count = (
                    check.execute(
                        text("SELECT COUNT(*) FROM market_snapshots WHERE symbol = :s"),
                        {"s": symbol},
                    )
                ).scalar()
                assert count == 1
            finally:
                check.close()
        finally:
            cleanup = factory()
            cleanup.execute(text("DELETE FROM market_snapshots WHERE symbol = :s"), {"s": symbol})
            cleanup.commit()
            cleanup.close()
