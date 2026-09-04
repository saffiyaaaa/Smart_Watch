"""Scheduler orchestration: wiring, not ingestion logic (covered by
test_ingestion.py).

run_once opens its own session rather than sharing the `db` fixture's
transaction, so these tests inject a sessionmaker bound to the same isolated
`smw_test` database (via the session-scoped `db_engine` fixture) and clean up
only the specific rows they create -- the same pattern test_concurrency.py
uses, and for the same reason: real commits need real, narrow cleanup rather
than a rollback.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from worker.scheduler import build_arg_parser, run_once

from app.models.user import User
from app.models.watchlist import Watchlist
from app.models.watchlist_item import WatchlistItem
from tests.conftest import postgres_required

pytestmark = [pytest.mark.integration, postgres_required]


class TestRunOnce:
    async def test_ingests_a_seeded_watchlist(self, db_engine: Engine):
        factory = sessionmaker(bind=db_engine, expire_on_commit=False)
        setup = factory()
        user = User(email=f"scheduler-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
        setup.add(user)
        setup.flush()
        watchlist = Watchlist(user_id=user.id, name="Scheduler Test")
        setup.add(watchlist)
        setup.flush()
        setup.add(WatchlistItem(watchlist_id=watchlist.id, symbol="SCHEDTEST"))
        setup.commit()
        user_id = user.id
        setup.close()

        try:
            results = await run_once(session_factory=factory)
            assert any(r.symbol == "SCHEDTEST" for r in results)
            matching = next(r for r in results if r.symbol == "SCHEDTEST")
            assert matching.outcome in ("created", "duplicate")
        finally:
            cleanup = factory()
            cleanup.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            cleanup.commit()
            cleanup.close()

    async def test_uses_the_injected_session_factory_not_the_real_one(self, db_engine: Engine):
        """A regression guard for the exact bug this dependency-injection
        parameter exists to prevent: run_once must not silently fall back to
        the production SessionLocal when a factory is supplied."""
        factory = sessionmaker(bind=db_engine, expire_on_commit=False)
        # No watchlists exist in the fresh test database for this run, so the
        # only way this returns [] is if it actually queried smw_test and not
        # whatever the real DATABASE_URL points at.
        results = await run_once(session_factory=factory)
        assert results == []


class TestArgParsing:
    def test_default_is_not_once(self):
        args = build_arg_parser().parse_args([])
        assert args.once is False

    def test_once_flag(self):
        args = build_arg_parser().parse_args(["--once"])
        assert args.once is True

    def test_unknown_flag_is_rejected(self):
        with pytest.raises(SystemExit):
            build_arg_parser().parse_args(["--nonsense"])
