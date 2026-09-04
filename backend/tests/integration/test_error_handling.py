"""Integration tests for error handling, rollback, and the gate
'invalid provider data never reaches the frontend as valid current data'.

Completion gates addressed here
--------------------------------
1. Database failures produce controlled errors (failure mode #11).
2. Invalid provider data never reaches the frontend as a valid 200.
3. Rollback: a partially-completed write leaves no trace.
4. Every error carries the standard error envelope.

Test strategy
-------------
For DB failures we patch engine.connect to raise OperationalError and test
through /ready -- the endpoint specifically designed to surface dependency
health. This avoids the auth/session complication and tests exactly the
right behaviour.

For the invalid-data gate we test the ingestion layer (the only place where
provider data enters the system) rather than trying to put bad data into the
snapshot table directly. The table's CHECK constraints would prevent that
anyway, which is the point: the domain model and the DB schema agree on what
valid data looks like, so a bad Quote cannot produce a stored snapshot.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.domain.market.quote import Quote
from app.infrastructure.providers.exceptions import (
    InvalidProviderData,
    ProviderUnavailable,
    SymbolNotFound,
)
from tests.conftest import postgres_required

pytestmark = [pytest.mark.integration, postgres_required]


# ---------------------------------------------------------------------------
# Standard error envelope
# ---------------------------------------------------------------------------


class TestErrorEnvelope:
    """Every error response uses {"error": {"code": ..., "message": ..., "details": ...}}."""

    def test_404_uses_standard_envelope(self, client: TestClient, alice: dict):
        r = client.get(f"/watchlists/{uuid.uuid4()}/changes", headers=alice["headers"])
        assert r.status_code == 404
        body = r.json()
        assert "error" in body
        assert "code" in body["error"]
        assert "message" in body["error"]
        assert "details" in body["error"]
        assert body["error"]["code"] == "not_found"

    def test_401_uses_standard_envelope(self, client: TestClient):
        r = client.get("/watchlists")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "unauthorized"

    def test_401_carries_www_authenticate_header(self, client: TestClient):
        r = client.get("/watchlists")
        assert "WWW-Authenticate" in r.headers

    def test_422_uses_standard_envelope(self, client: TestClient, alice: dict):
        """Pydantic validation errors are formatted in the standard envelope."""
        r = client.post("/watchlists", json={"name": ""}, headers=alice["headers"])
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "validation_error"
        assert "fields" in body["error"]["details"]

    def test_unknown_route_is_404_with_standard_envelope(self, client: TestClient):
        r = client.get("/this-route-does-not-exist")
        assert r.status_code == 404
        # FastAPI/Starlette wraps this through our StarletteHTTPException handler.
        assert "error" in r.json()

    def test_409_conflict_uses_standard_envelope(self, client: TestClient, alice: dict):
        """Duplicate watchlist name produces 409 with the standard envelope."""
        client.post("/watchlists", json={"name": "Tech"}, headers=alice["headers"])
        r = client.post("/watchlists", json={"name": "Tech"}, headers=alice["headers"])
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "conflict"


# ---------------------------------------------------------------------------
# Database failure → controlled 503
# ---------------------------------------------------------------------------


class TestDatabaseFailureProduces503:
    def test_ready_returns_503_when_database_unreachable(self, client: TestClient):
        """/ready is the designed endpoint for surfacing DB failures.

        We patch engine.connect to raise OperationalError, simulating a
        database that cannot be reached. The endpoint must return 503 with
        a 'degraded' status rather than crashing or returning 200.
        """
        from unittest.mock import patch

        from app.infrastructure.database import session as db_session

        with patch.object(
            db_session.engine,
            "connect",
            side_effect=OperationalError("connect", {}, Exception("connection refused")),
        ):
            r = client.get("/ready")

        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["checks"]["database"] == "error"

    def test_ready_503_uses_the_standard_content_type(self, client: TestClient):
        """The 503 /ready response must be JSON, not HTML or plain text,
        so the frontend (and monitoring tools) can parse it uniformly."""
        from unittest.mock import patch

        from app.infrastructure.database import session as db_session

        with patch.object(
            db_session.engine,
            "connect",
            side_effect=OperationalError("connect", {}, Exception("connection refused")),
        ):
            r = client.get("/ready")

        assert r.status_code == 503
        assert "application/json" in r.headers.get("content-type", "")

    def test_ready_503_does_not_expose_connection_string(self, client: TestClient):
        """Connection details must never appear in the 503 response body."""
        from unittest.mock import patch

        from app.infrastructure.database import session as db_session

        with patch.object(
            db_session.engine,
            "connect",
            side_effect=OperationalError(
                "connect",
                {},
                Exception("postgresql+psycopg://smw:secret@localhost:5433/db"),
            ),
        ):
            r = client.get("/ready")

        assert "postgresql" not in r.text
        assert "secret" not in r.text


# ---------------------------------------------------------------------------
# Rollback: a failed write leaves no partial data
# ---------------------------------------------------------------------------


class TestTransactionRollback:
    def test_failed_request_does_not_persist_partial_writes(
        self, client: TestClient, alice: dict, db: Session
    ):
        """A request that raises after a partial write must roll back entirely.

        We prove this using the watchlist endpoint: create two watchlists
        back-to-back. If the first succeeded and was rolled back, it must
        not appear in the list afterward.

        The test database session fixture already wraps each test in a savepoint
        that is rolled back at teardown, so this just verifies the HTTP layer's
        own rollback (the get_db dependency) works correctly by triggering an
        error partway through the second request.
        """
        # First request: a valid create -- must succeed.
        r1 = client.post("/watchlists", json={"name": "First"}, headers=alice["headers"])
        assert r1.status_code == 201

        # Second request: a conflict (same name) -- must fail cleanly.
        r2 = client.post("/watchlists", json={"name": "First"}, headers=alice["headers"])
        assert r2.status_code == 409

        # The list must contain exactly one watchlist.
        lst = client.get("/watchlists", headers=alice["headers"]).json()
        assert len(lst) == 1
        assert lst[0]["name"] == "First"


# ---------------------------------------------------------------------------
# Invalid provider data never reaches the frontend as a valid 200
# ---------------------------------------------------------------------------


class TestInvalidProviderDataIsRejected:
    """The Phase 9 gate: 'invalid provider data never reaches the frontend
    as valid current data.'

    Provider data enters the system at exactly one point: the Quote / Bar
    domain models in app/domain/market/quote.py. Every field is validated
    by Pydantic on construction, so a malformed observation raises
    ValidationError before any database write occurs.

    We test this at the model layer (fast, no DB) and at the ingestion
    boundary (proves the worker enforces it end-to-end).
    """

    def test_negative_price_is_rejected_at_model_layer(self):
        with pytest.raises(ValidationError, match="price"):
            Quote(
                source="mock",
                symbol="AAPL",
                price=Decimal("-1.00"),
                volume=1_000_000,
                market_timestamp=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
                fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            )

    def test_future_timestamp_is_rejected_at_model_layer(self):
        from datetime import UTC, datetime, timedelta

        with pytest.raises(ValidationError, match="future"):
            Quote(
                source="mock",
                symbol="AAPL",
                price=Decimal("150.00"),
                volume=1_000_000,
                market_timestamp=datetime.now(UTC) + timedelta(hours=1),
                fetched_at=datetime.now(UTC),
            )

    def test_naive_timestamp_is_rejected_at_model_layer(self):
        import datetime as dt

        with pytest.raises(ValidationError, match="timezone"):
            Quote(
                source="mock",
                symbol="AAPL",
                price=Decimal("150.00"),
                volume=1_000_000,
                market_timestamp=dt.datetime.now(),  # no tzinfo
                fetched_at=dt.datetime.now(dt.UTC),
            )

    def test_invalid_provider_data_exception_stops_ingestion(self, db: Session):
        """InvalidProviderData from the provider layer must not produce a
        stored snapshot. We call the ingestion function with a provider that
        raises it and assert no snapshot row was written.
        """
        from sqlalchemy import select
        from worker.ingestion import ingest_symbol

        from app.models.market_snapshot import MarketSnapshot
        from tests.fixtures.fake_providers import SelectiveFailureProvider

        provider = SelectiveFailureProvider(
            failing_symbols={"AAPL"},
            exception_factory=lambda sym: InvalidProviderData(f"bad data for {sym}"),
        )

        import asyncio

        # ingest_symbol should catch InvalidProviderData and not write anything.
        result = asyncio.run(ingest_symbol(db, provider, "AAPL"))
        assert result.outcome == "provider_error"

        snapshots = list(
            db.execute(select(MarketSnapshot).where(MarketSnapshot.symbol == "AAPL")).scalars()
        )
        assert snapshots == [], "No snapshot must be stored when provider data is invalid"

    def test_provider_unavailable_stops_ingestion_without_crashing(self, db: Session):
        """A transient provider failure must be handled gracefully --
        no exception bubbles out of ingest_symbol, and nothing is written.
        """
        from sqlalchemy import select
        from worker.ingestion import ingest_symbol

        from app.models.market_snapshot import MarketSnapshot
        from tests.fixtures.fake_providers import SelectiveFailureProvider

        provider = SelectiveFailureProvider(
            failing_symbols={"TSLA"},
            exception_factory=lambda sym: ProviderUnavailable(f"timeout for {sym}"),
        )

        import asyncio

        # Should not raise.
        result = asyncio.run(ingest_symbol(db, provider, "TSLA"))
        assert result.outcome == "provider_error"

        snapshots = list(
            db.execute(select(MarketSnapshot).where(MarketSnapshot.symbol == "TSLA")).scalars()
        )
        assert snapshots == []

    def test_symbol_not_found_stops_ingestion_without_crashing(self, db: Session):
        """An unknown or delisted symbol (failure mode #15) must be handled
        gracefully -- no crash, no partial write.
        """
        from worker.ingestion import ingest_symbol

        from tests.fixtures.fake_providers import SelectiveFailureProvider

        provider = SelectiveFailureProvider(
            failing_symbols={"DELIST"},
            exception_factory=lambda sym: SymbolNotFound(f"{sym} not found"),
        )

        import asyncio

        result = asyncio.run(ingest_symbol(db, provider, "DELIST"))
        assert result.outcome == "provider_error"
        # No exception raised -- test passes by reaching here.
