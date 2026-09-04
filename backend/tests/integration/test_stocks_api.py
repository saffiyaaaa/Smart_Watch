"""Integration tests for GET /watchlists/{id}/symbols/{symbol}/quote
and GET /watchlists/{id}/symbols/{symbol}/history.

These prove the routes wire ownership, schema, freshness computation,
and the empty-state cases correctly. The underlying snapshot and bar
repositories are already tested independently in test_repositories.py;
these tests exist to confirm the HTTP layer wires them correctly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import postgres_required
from tests.fixtures import seed

pytestmark = [pytest.mark.integration, postgres_required]


def _wl_with_symbol(client: TestClient, user: dict, symbol: str = "AAPL") -> str:
    """Create a watchlist, add a symbol, return the watchlist id."""
    wl = client.post("/watchlists", json={"name": "Tech"}, headers=user["headers"]).json()
    client.post(
        f"/watchlists/{wl['id']}/symbols",
        json={"symbol": symbol},
        headers=user["headers"],
    )
    return wl["id"]


# ---------------------------------------------------------------------------
# Quote endpoint
# ---------------------------------------------------------------------------


class TestGetQuote:
    def test_returns_latest_snapshot(self, client: TestClient, alice: dict, db: Session):
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        seed.make_snapshot(db, symbol="AAPL", price="191.58")
        db.commit()

        r = client.get(f"/watchlists/{wl_id}/symbols/AAPL/quote", headers=alice["headers"])

        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "AAPL"
        assert body["price"] == "191.580000"

    def test_response_schema_contains_all_required_fields(
        self, client: TestClient, alice: dict, db: Session
    ):
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        seed.make_snapshot(db, symbol="AAPL")
        db.commit()

        body = client.get(
            f"/watchlists/{wl_id}/symbols/AAPL/quote", headers=alice["headers"]
        ).json()
        assert set(body) == {
            "symbol",
            "price",
            "volume",
            "market_timestamp",
            "freshness",
            "ingest_freshness",
        }

    def test_freshness_field_is_a_valid_freshness_value(
        self, client: TestClient, alice: dict, db: Session
    ):
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        seed.make_snapshot(db, symbol="AAPL")
        db.commit()

        body = client.get(
            f"/watchlists/{wl_id}/symbols/AAPL/quote", headers=alice["headers"]
        ).json()
        assert body["freshness"] in {"FRESH", "DELAYED", "STALE"}
        assert body["ingest_freshness"] in {"FRESH", "DELAYED", "STALE"}

    def test_symbol_lookup_is_case_insensitive(self, client: TestClient, alice: dict, db: Session):
        """The route normalizes to uppercase before querying, so aapl == AAPL."""
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        seed.make_snapshot(db, symbol="AAPL")
        db.commit()

        r = client.get(f"/watchlists/{wl_id}/symbols/aapl/quote", headers=alice["headers"])
        assert r.status_code == 200
        assert r.json()["symbol"] == "AAPL"

    def test_returns_404_when_no_snapshot_exists(self, client: TestClient, alice: dict):
        wl_id = _wl_with_symbol(client, alice, "NVDA")

        r = client.get(f"/watchlists/{wl_id}/symbols/NVDA/quote", headers=alice["headers"])

        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"

    def test_unknown_watchlist_is_404(self, client: TestClient, alice: dict):
        r = client.get(f"/watchlists/{uuid.uuid4()}/symbols/AAPL/quote", headers=alice["headers"])
        assert r.status_code == 404

    def test_another_users_watchlist_is_404(
        self, client: TestClient, alice: dict, bob: dict, db: Session
    ):
        """Ownership collapse: not-yours is indistinguishable from not-found."""
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        seed.make_snapshot(db, symbol="AAPL")
        db.commit()

        r = client.get(f"/watchlists/{wl_id}/symbols/AAPL/quote", headers=bob["headers"])
        assert r.status_code == 404

    def test_requires_authentication(self, client: TestClient, alice: dict, db: Session):
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        seed.make_snapshot(db, symbol="AAPL")
        db.commit()

        r = client.get(f"/watchlists/{wl_id}/symbols/AAPL/quote")
        assert r.status_code == 401

    def test_most_recent_snapshot_wins_when_multiple_exist(
        self, client: TestClient, alice: dict, db: Session
    ):
        """get_latest orders by market_timestamp, not by insert order."""
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        now = datetime.now(UTC)
        # Older snapshot at a higher price — must NOT win.
        seed.make_snapshot(
            db,
            symbol="AAPL",
            price="200.00",
            market_timestamp=now - timedelta(hours=2),
        )
        # Newer snapshot at a lower price — must win.
        seed.make_snapshot(
            db,
            symbol="AAPL",
            price="191.58",
            market_timestamp=now - timedelta(minutes=5),
        )
        db.commit()

        body = client.get(
            f"/watchlists/{wl_id}/symbols/AAPL/quote", headers=alice["headers"]
        ).json()
        assert body["price"] == "191.580000"

    def test_conflicting_sources_at_the_same_instant_break_ties_by_fetched_at(
        self, client: TestClient, alice: dict, db: Session
    ):
        """docs/product-spec.md section 8 row 6: two sources may report the
        identical market_timestamp with different prices (a genuine conflict,
        not a race) -- market_timestamp alone cannot order these two rows, so
        the documented precedence is "most recent fetched_at wins for
        display"."""
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        instant = datetime.now(UTC) - timedelta(minutes=5)

        seed.make_snapshot(
            db,
            symbol="AAPL",
            price="180.00",
            market_timestamp=instant,
            fetched_at=instant,
            source="mock",
        )
        seed.make_snapshot(
            db,
            symbol="AAPL",
            price="182.50",
            market_timestamp=instant,  # same instant, disagreeing sources
            fetched_at=instant + timedelta(seconds=30),  # confirmed most recently
            source="yfinance",
        )
        db.commit()

        body = client.get(
            f"/watchlists/{wl_id}/symbols/AAPL/quote", headers=alice["headers"]
        ).json()
        assert body["price"] == "182.500000"

    def test_null_volume_is_serialized_correctly(
        self, client: TestClient, alice: dict, db: Session
    ):
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        seed.make_snapshot(db, symbol="AAPL", volume=None)
        db.commit()

        body = client.get(
            f"/watchlists/{wl_id}/symbols/AAPL/quote", headers=alice["headers"]
        ).json()
        assert body["volume"] is None


# ---------------------------------------------------------------------------
# History endpoint
# ---------------------------------------------------------------------------


class TestGetHistory:
    def test_returns_bars_most_recent_first(self, client: TestClient, alice: dict, db: Session):
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        seed.make_volume_history(db, symbol="AAPL", sessions=5)
        db.commit()

        body = client.get(
            f"/watchlists/{wl_id}/symbols/AAPL/history", headers=alice["headers"]
        ).json()

        assert len(body) == 5
        # Dates are descending (most recent first).
        dates = [b["session_date"] for b in body]
        assert dates == sorted(dates, reverse=True)

    def test_response_schema(self, client: TestClient, alice: dict, db: Session):
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        seed.make_daily_bar(db, symbol="AAPL")
        db.commit()

        body = client.get(
            f"/watchlists/{wl_id}/symbols/AAPL/history", headers=alice["headers"]
        ).json()
        assert set(body[0]) == {"session_date", "close", "volume"}

    def test_empty_history_returns_empty_list(self, client: TestClient, alice: dict):
        wl_id = _wl_with_symbol(client, alice, "MSFT")

        r = client.get(f"/watchlists/{wl_id}/symbols/MSFT/history", headers=alice["headers"])

        assert r.status_code == 200
        assert r.json() == []

    def test_limit_parameter_is_honoured(self, client: TestClient, alice: dict, db: Session):
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        seed.make_volume_history(db, symbol="AAPL", sessions=20)
        db.commit()

        body = client.get(
            f"/watchlists/{wl_id}/symbols/AAPL/history?limit=5", headers=alice["headers"]
        ).json()
        assert len(body) == 5

    def test_limit_defaults_to_30(self, client: TestClient, alice: dict, db: Session):
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        # Seed more than 30 so we can verify truncation.
        seed.make_volume_history(db, symbol="AAPL", sessions=35)
        db.commit()

        body = client.get(
            f"/watchlists/{wl_id}/symbols/AAPL/history", headers=alice["headers"]
        ).json()
        assert len(body) == 30

    def test_limit_above_90_is_rejected(self, client: TestClient, alice: dict):
        wl_id = _wl_with_symbol(client, alice, "AAPL")

        r = client.get(
            f"/watchlists/{wl_id}/symbols/AAPL/history?limit=91", headers=alice["headers"]
        )
        assert r.status_code == 422

    def test_unknown_watchlist_is_404(self, client: TestClient, alice: dict):
        r = client.get(f"/watchlists/{uuid.uuid4()}/symbols/AAPL/history", headers=alice["headers"])
        assert r.status_code == 404

    def test_another_users_watchlist_is_404(
        self, client: TestClient, alice: dict, bob: dict, db: Session
    ):
        wl_id = _wl_with_symbol(client, alice, "AAPL")
        seed.make_daily_bar(db, symbol="AAPL")
        db.commit()

        r = client.get(f"/watchlists/{wl_id}/symbols/AAPL/history", headers=bob["headers"])
        assert r.status_code == 404

    def test_requires_authentication(self, client: TestClient, alice: dict):
        wl_id = _wl_with_symbol(client, alice, "AAPL")

        r = client.get(f"/watchlists/{wl_id}/symbols/AAPL/history")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Ready endpoint
# ---------------------------------------------------------------------------


class TestReadyEndpoint:
    def test_returns_200_with_database_ok_when_db_reachable(self, client: TestClient):
        r = client.get("/ready")

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ready"
        assert body["checks"]["database"] == "ok"

    def test_ready_schema(self, client: TestClient):
        body = client.get("/ready").json()
        assert "status" in body
        assert "checks" in body

    def test_health_is_independent_of_database(self, client: TestClient):
        """/health must not touch dependencies -- it only proves the process is alive."""
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
