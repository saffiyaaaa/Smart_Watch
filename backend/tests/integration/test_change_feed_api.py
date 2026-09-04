"""GET /watchlists/{id}/changes and POST /watchlists/{id}/seen at the HTTP
layer: request/response shape, ownership, and validation. Behavioural
correctness of the underlying logic is covered by
test_change_feed_service.py; these tests exist to prove the routes wire that
logic up correctly."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import postgres_required
from tests.fixtures import seed

pytestmark = [pytest.mark.integration, postgres_required]


def _create_watchlist_with_symbol(client: TestClient, user: dict, symbol: str = "AAPL") -> str:
    wl = client.post("/watchlists", json={"name": "Tech"}, headers=user["headers"]).json()
    client.post(f"/watchlists/{wl['id']}/symbols", json={"symbol": symbol}, headers=user["headers"])
    return wl["id"]


class TestGetChangesEndpoint:
    def test_new_watchlist_is_first_visit_with_no_events(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist_with_symbol(client, alice)

        r = client.get(f"/watchlists/{wl_id}/changes", headers=alice["headers"])

        assert r.status_code == 200
        body = r.json()
        assert body["first_visit"] is True
        assert body["last_seen_at"] is None
        assert body["events"] == []

    def test_returns_a_real_event_created_by_the_pipeline(
        self, client: TestClient, alice: dict, db: Session
    ):
        """Seeds an event directly rather than running the worker -- proving
        the route surfaces persisted events correctly is this test's job;
        proving the worker produces them is test_change_detection_pipeline.py's."""
        wl_id = _create_watchlist_with_symbol(client, alice)
        seed.make_event(
            db,
            symbol="AAPL",
            score=65,
            evidence=["Price +6.2% vs previous close ($180.40 → $191.58)"],
            detected_at=datetime.now(UTC),
        )
        db.commit()

        r = client.get(f"/watchlists/{wl_id}/changes", headers=alice["headers"])

        body = r.json()
        assert len(body["events"]) == 1
        event = body["events"][0]
        assert event["symbol"] == "AAPL"
        assert event["score"] == 65
        assert "6.2%" in event["evidence"][0]

    def test_unknown_watchlist_is_404(self, client: TestClient, alice: dict):
        r = client.get(f"/watchlists/{uuid.uuid4()}/changes", headers=alice["headers"])
        assert r.status_code == 404

    def test_cannot_read_another_users_change_feed(
        self, client: TestClient, alice: dict, bob: dict
    ):
        wl_id = _create_watchlist_with_symbol(client, alice)
        r = client.get(f"/watchlists/{wl_id}/changes", headers=bob["headers"])
        assert r.status_code == 404

    def test_requires_authentication(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist_with_symbol(client, alice)
        r = client.get(f"/watchlists/{wl_id}/changes")
        assert r.status_code == 401

    def test_response_schema(self, client: TestClient, alice: dict, db: Session):
        wl_id = _create_watchlist_with_symbol(client, alice)
        seed.make_event(db, symbol="AAPL", detected_at=datetime.now(UTC))
        db.commit()

        body = client.get(f"/watchlists/{wl_id}/changes", headers=alice["headers"]).json()
        assert set(body) == {"events", "first_visit", "last_seen_at"}
        assert set(body["events"][0]) == {
            "id",
            "symbol",
            "event_type",
            "score",
            "severity",
            "evidence",
            "detected_at",
        }


class TestMarkSeenEndpoint:
    def test_mark_seen_with_no_body_uses_server_time(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist_with_symbol(client, alice)

        r = client.post(f"/watchlists/{wl_id}/seen", json={}, headers=alice["headers"])

        assert r.status_code == 200
        body = r.json()
        seen_at = datetime.fromisoformat(body["last_seen_at"])
        assert abs((datetime.now(UTC) - seen_at).total_seconds()) < 10

    def test_mark_seen_with_explicit_timestamp(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist_with_symbol(client, alice)
        explicit = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)

        r = client.post(
            f"/watchlists/{wl_id}/seen",
            json={"seen_at": explicit.isoformat()},
            headers=alice["headers"],
        )

        assert r.status_code == 200
        assert datetime.fromisoformat(r.json()["last_seen_at"]) == explicit

    def test_repeated_calls_are_idempotent(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist_with_symbol(client, alice)
        explicit = datetime(2026, 3, 11, 12, 0, tzinfo=UTC).isoformat()

        for _ in range(3):
            r = client.post(
                f"/watchlists/{wl_id}/seen", json={"seen_at": explicit}, headers=alice["headers"]
            )
            assert r.status_code == 200

    def test_marks_events_seen_end_to_end(self, client: TestClient, alice: dict, db: Session):
        wl_id = _create_watchlist_with_symbol(client, alice)
        seed.make_event(db, symbol="AAPL", detected_at=datetime.now(UTC))
        db.commit()

        before = client.get(f"/watchlists/{wl_id}/changes", headers=alice["headers"]).json()
        assert len(before["events"]) == 1

        client.post(f"/watchlists/{wl_id}/seen", json={}, headers=alice["headers"])

        after = client.get(f"/watchlists/{wl_id}/changes", headers=alice["headers"]).json()
        assert after["events"] == []
        assert after["first_visit"] is False

    def test_cannot_mark_another_users_watchlist_seen(
        self, client: TestClient, alice: dict, bob: dict
    ):
        wl_id = _create_watchlist_with_symbol(client, alice)
        r = client.post(f"/watchlists/{wl_id}/seen", json={}, headers=bob["headers"])
        assert r.status_code == 404

    def test_unknown_watchlist_is_404(self, client: TestClient, alice: dict):
        r = client.post(f"/watchlists/{uuid.uuid4()}/seen", json={}, headers=alice["headers"])
        assert r.status_code == 404

    def test_requires_authentication(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist_with_symbol(client, alice)
        r = client.post(f"/watchlists/{wl_id}/seen", json={})
        assert r.status_code == 401

    def test_response_schema(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist_with_symbol(client, alice)
        body = client.post(f"/watchlists/{wl_id}/seen", json={}, headers=alice["headers"]).json()
        assert set(body) == {"watchlist_id", "last_seen_at", "last_seen_event_id"}
        assert body["watchlist_id"] == wl_id

    def test_future_timestamp_is_clamped_at_the_api_layer_too(
        self, client: TestClient, alice: dict
    ):
        wl_id = _create_watchlist_with_symbol(client, alice)
        far_future = (datetime.now(UTC) + timedelta(days=3650)).isoformat()

        r = client.post(
            f"/watchlists/{wl_id}/seen", json={"seen_at": far_future}, headers=alice["headers"]
        )

        seen_at = datetime.fromisoformat(r.json()["last_seen_at"])
        assert seen_at <= datetime.now(UTC)
