"""Index usage on the real read paths.

An index that exists is not an index that gets used. These tests load enough
rows that a sequential scan is genuinely the more expensive option, run ANALYZE
so the planner has statistics, and then assert on the actual EXPLAIN output.

Without the volume, every plan would be a sequential scan -- PostgreSQL is right
to scan a 20-row table -- and the tests would prove nothing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import postgres_required

pytestmark = [pytest.mark.integration, postgres_required]

SYMBOLS = [f"SYM{i:03d}" for i in range(60)]
SNAPSHOTS_PER_SYMBOL = 120


@pytest.fixture(scope="module")
def loaded_db(db_engine: Engine):
    """A populated dataset, committed once and cleaned up afterwards.

    Module-scoped: loading ~7000 rows per test would dominate the runtime, and
    every test here is read-only.
    """
    session: Session = sessionmaker(bind=db_engine, expire_on_commit=False)()
    base = datetime(2026, 3, 11, 20, 0, tzinfo=UTC)

    rows = []
    for symbol in SYMBOLS:
        for i in range(SNAPSHOTS_PER_SYMBOL):
            rows.append(
                {
                    "source": "plan_test",
                    "symbol": symbol,
                    "price": 100 + (i % 50),
                    "volume": 1_000_000,
                    "market_timestamp": base - timedelta(minutes=5 * i),
                    "ingest_freshness": "FRESH",
                }
            )
    session.execute(
        text(
            "INSERT INTO market_snapshots "
            "(source, symbol, price, volume, market_timestamp, ingest_freshness) "
            "VALUES (:source, :symbol, :price, :volume, :market_timestamp, :ingest_freshness)"
        ),
        rows,
    )

    event_rows = []
    for day_offset, symbol in enumerate(SYMBOLS * 8):
        event_rows.append(
            {
                "symbol": symbol,
                "trading_day": date(2026, 3, 11) - timedelta(days=day_offset % 400),
                "event_type": "PRICE_MOVE",
                "score": 20 + (day_offset % 80),
                "severity": "WATCH",
                "evidence": '["seeded"]',
                "confidence": 1.0,
                "detected_at": base - timedelta(hours=day_offset),
            }
        )
    session.execute(
        text(
            "INSERT INTO change_events "
            "(symbol, trading_day, event_type, score, severity, evidence, confidence, detected_at) "
            "VALUES (:symbol, :trading_day, :event_type, :score, :severity, "
            "CAST(:evidence AS jsonb), :confidence, :detected_at) "
            "ON CONFLICT DO NOTHING"
        ),
        event_rows,
    )
    session.commit()

    # Without ANALYZE the planner works from default estimates and may choose a
    # sequential scan on a table it believes is tiny.
    session.execute(text("ANALYZE market_snapshots"))
    session.execute(text("ANALYZE change_events"))
    session.commit()

    yield session

    session.execute(text("DELETE FROM market_snapshots WHERE source = 'plan_test'"))
    session.execute(
        text("DELETE FROM change_events WHERE evidence = CAST('[\"seeded\"]' AS jsonb)")
    )
    session.commit()
    session.close()


def _plan(session: Session, sql: str, params: dict | None = None) -> str:
    rows = session.execute(text(f"EXPLAIN {sql}"), params or {}).scalars().all()
    return "\n".join(rows)


class TestSnapshotReadPaths:
    def test_latest_snapshot_uses_the_symbol_timestamp_index(self, loaded_db: Session):
        plan = _plan(
            loaded_db,
            """
            SELECT * FROM market_snapshots
            WHERE symbol = :symbol
            ORDER BY market_timestamp DESC
            LIMIT 1
            """,
            {"symbol": SYMBOLS[0]},
        )
        assert "ix_market_snapshots_symbol_market_timestamp" in plan
        assert "Seq Scan" not in plan

    def test_latest_snapshot_needs_no_sort(self, loaded_db: Session):
        """The index is declared DESC to match the ORDER BY, so PostgreSQL walks
        it and stops at the first row. A mismatched direction would still use the
        index but add a Sort node over every row for the symbol."""
        plan = _plan(
            loaded_db,
            """
            SELECT * FROM market_snapshots
            WHERE symbol = :symbol
            ORDER BY market_timestamp DESC
            LIMIT 1
            """,
            {"symbol": SYMBOLS[0]},
        )
        assert "Sort" not in plan

    def test_history_query_uses_the_index(self, loaded_db: Session):
        plan = _plan(
            loaded_db,
            """
            SELECT * FROM market_snapshots
            WHERE symbol = :symbol AND market_timestamp >= :since
            ORDER BY market_timestamp DESC
            LIMIT 100
            """,
            {"symbol": SYMBOLS[0], "since": datetime(2026, 3, 1, tzinfo=UTC)},
        )
        assert "ix_market_snapshots_symbol_market_timestamp" in plan
        assert "Seq Scan" not in plan

    def test_distinct_on_multi_symbol_lookup_uses_the_index(self, loaded_db: Session):
        """The watchlist render path: latest price for every symbol at once."""
        plan = _plan(
            loaded_db,
            """
            SELECT DISTINCT ON (symbol) * FROM market_snapshots
            WHERE symbol = ANY(:symbols)
            ORDER BY symbol, market_timestamp DESC
            """,
            {"symbols": SYMBOLS[:10]},
        )
        assert "ix_market_snapshots_symbol_market_timestamp" in plan
        assert "Seq Scan" not in plan

    def test_ingest_conflict_check_uses_the_uniqueness_index(self, loaded_db: Session):
        """The ingest hot path.

        This explains the real ON CONFLICT statement rather than a SELECT that
        merely resembles it. PostgreSQL reports the arbiter index it will use to
        detect the conflict, which is the thing that must not degrade into a
        scan -- every ingested quote pays this cost.

        EXPLAIN without ANALYZE plans the statement without executing it, so no
        row is written.
        """
        plan = _plan(
            loaded_db,
            """
            INSERT INTO market_snapshots
                (source, symbol, price, volume, market_timestamp, ingest_freshness)
            VALUES (:source, :symbol, 100, 1, :ts, 'FRESH')
            ON CONFLICT ON CONSTRAINT uq_market_snapshots_observation DO NOTHING
            """,
            {
                "source": "plan_test",
                "symbol": SYMBOLS[0],
                "ts": datetime(2026, 3, 11, 20, 0, tzinfo=UTC),
            },
        )
        assert "uq_market_snapshots_observation" in plan
        assert "Seq Scan" not in plan


class TestEventReadPaths:
    def test_symbol_event_history_uses_the_index(self, loaded_db: Session):
        plan = _plan(
            loaded_db,
            """
            SELECT * FROM change_events
            WHERE symbol = :symbol
            ORDER BY detected_at DESC
            LIMIT 50
            """,
            {"symbol": SYMBOLS[0]},
        )
        assert "ix_change_events_symbol_detected_at" in plan
        assert "Seq Scan" not in plan

    def test_unseen_events_query_uses_an_index(self, loaded_db: Session):
        """The change feed: events after a user's cursor. The planner may pick
        either the detected_at index or the composite one depending on
        selectivity; what must not happen is a full table scan."""
        plan = _plan(
            loaded_db,
            """
            SELECT * FROM change_events
            WHERE detected_at > :since AND score >= 20
            ORDER BY score DESC, detected_at DESC
            LIMIT 50
            """,
            {"since": datetime(2026, 3, 10, tzinfo=UTC)},
        )
        assert "Index" in plan
        assert "Seq Scan on change_events" not in plan


class TestOwnershipReadPaths:
    def test_listing_watchlists_uses_the_user_index(self, loaded_db: Session):
        """PostgreSQL does not index a foreign key automatically; without
        ix_watchlists_user_id this is a sequential scan on every page load."""
        plan = _plan(
            loaded_db,
            "SELECT * FROM watchlists WHERE user_id = :uid",
            {"uid": str(uuid.uuid4())},
        )
        assert "ix_watchlists_user_id" in plan or "Index" in plan
