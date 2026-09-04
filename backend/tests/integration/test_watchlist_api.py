"""Watchlist endpoint behaviour: ownership, idempotency, validation."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import postgres_required

pytestmark = [pytest.mark.integration, postgres_required]


def _create_watchlist(client: TestClient, user: dict, name: str = "Tech") -> str:
    r = client.post("/watchlists", json={"name": name}, headers=user["headers"])
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestWatchlistCrud:
    def test_create_and_read_back(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist(client, alice, "My List")

        r = client.get(f"/watchlists/{wl_id}", headers=alice["headers"])
        assert r.status_code == 200
        assert r.json()["name"] == "My List"
        assert r.json()["items"] == []

    def test_list_is_empty_for_a_new_user(self, client: TestClient, alice: dict):
        r = client.get("/watchlists", headers=alice["headers"])
        assert r.status_code == 200
        assert r.json() == []

    def test_duplicate_name_is_a_conflict(self, client: TestClient, alice: dict):
        _create_watchlist(client, alice, "Tech")
        r = client.post("/watchlists", json={"name": "Tech"}, headers=alice["headers"])
        assert r.status_code == 409

    def test_name_is_trimmed(self, client: TestClient, alice: dict):
        r = client.post("/watchlists", json={"name": "  Spaced  "}, headers=alice["headers"])
        assert r.json()["name"] == "Spaced"

    @pytest.mark.parametrize("name", ["", "   ", "x" * 101])
    def test_invalid_name_rejected(self, client: TestClient, alice: dict, name: str):
        r = client.post("/watchlists", json={"name": name}, headers=alice["headers"])
        assert r.status_code == 422

    def test_delete_removes_it(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist(client, alice)
        assert client.delete(f"/watchlists/{wl_id}", headers=alice["headers"]).status_code == 204
        assert client.get(f"/watchlists/{wl_id}", headers=alice["headers"]).status_code == 404

    def test_unknown_id_is_404(self, client: TestClient, alice: dict):
        r = client.get(f"/watchlists/{uuid.uuid4()}", headers=alice["headers"])
        assert r.status_code == 404

    def test_malformed_id_is_422(self, client: TestClient, alice: dict):
        r = client.get("/watchlists/not-a-uuid", headers=alice["headers"])
        assert r.status_code == 422


class TestCrossUserIsolation:
    """The Phase 3 gate: user A cannot read or mutate user B's watchlist."""

    def test_cannot_read_another_users_watchlist(self, client: TestClient, alice: dict, bob: dict):
        wl_id = _create_watchlist(client, alice)
        assert client.get(f"/watchlists/{wl_id}", headers=bob["headers"]).status_code == 404

    def test_not_yours_is_indistinguishable_from_not_found(
        self, client: TestClient, alice: dict, bob: dict
    ):
        """Returning 403 for "exists but not yours" would confirm that another
        user's id is real -- an information leak when ids appear in URLs."""
        wl_id = _create_watchlist(client, alice)

        others = client.get(f"/watchlists/{wl_id}", headers=bob["headers"])
        missing = client.get(f"/watchlists/{uuid.uuid4()}", headers=bob["headers"])

        assert others.status_code == missing.status_code == 404
        assert others.json() == missing.json()

    def test_cannot_delete_another_users_watchlist(
        self, client: TestClient, alice: dict, bob: dict
    ):
        wl_id = _create_watchlist(client, alice)
        assert client.delete(f"/watchlists/{wl_id}", headers=bob["headers"]).status_code == 404
        # And it is still there for its owner.
        assert client.get(f"/watchlists/{wl_id}", headers=alice["headers"]).status_code == 200

    def test_cannot_add_symbols_to_another_users_watchlist(
        self, client: TestClient, alice: dict, bob: dict
    ):
        wl_id = _create_watchlist(client, alice)

        r = client.post(
            f"/watchlists/{wl_id}/symbols", json={"symbol": "AAPL"}, headers=bob["headers"]
        )
        assert r.status_code == 404

        owner_view = client.get(f"/watchlists/{wl_id}", headers=alice["headers"])
        assert owner_view.json()["items"] == []

    def test_cannot_remove_symbols_from_another_users_watchlist(
        self, client: TestClient, alice: dict, bob: dict
    ):
        wl_id = _create_watchlist(client, alice)
        client.post(
            f"/watchlists/{wl_id}/symbols", json={"symbol": "AAPL"}, headers=alice["headers"]
        )

        r = client.delete(f"/watchlists/{wl_id}/symbols/AAPL", headers=bob["headers"])
        assert r.status_code == 404

        owner_view = client.get(f"/watchlists/{wl_id}", headers=alice["headers"])
        assert [i["symbol"] for i in owner_view.json()["items"]] == ["AAPL"]

    def test_list_shows_only_own_watchlists(self, client: TestClient, alice: dict, bob: dict):
        _create_watchlist(client, alice, "Alice list")
        _create_watchlist(client, bob, "Bob list")

        names = [w["name"] for w in client.get("/watchlists", headers=alice["headers"]).json()]
        assert names == ["Alice list"]

    def test_two_users_may_use_the_same_watchlist_name(
        self, client: TestClient, alice: dict, bob: dict
    ):
        _create_watchlist(client, alice, "Tech")
        _create_watchlist(client, bob, "Tech")


class TestSymbolManagement:
    def test_add_symbol(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist(client, alice)
        r = client.post(
            f"/watchlists/{wl_id}/symbols", json={"symbol": "AAPL"}, headers=alice["headers"]
        )
        assert r.status_code == 201
        assert r.json()["symbol"] == "AAPL"

    def test_symbol_is_normalized(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist(client, alice)
        r = client.post(
            f"/watchlists/{wl_id}/symbols", json={"symbol": "  aapl "}, headers=alice["headers"]
        )
        assert r.json()["symbol"] == "AAPL"

    def test_adding_twice_yields_one_item(self, client: TestClient, alice: dict):
        """The Phase 3 gate. Adding something already present is not an error:
        the requested state already holds."""
        wl_id = _create_watchlist(client, alice)

        first = client.post(
            f"/watchlists/{wl_id}/symbols", json={"symbol": "AAPL"}, headers=alice["headers"]
        )
        second = client.post(
            f"/watchlists/{wl_id}/symbols", json={"symbol": "AAPL"}, headers=alice["headers"]
        )

        assert first.status_code == second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

        items = client.get(f"/watchlists/{wl_id}", headers=alice["headers"]).json()["items"]
        assert len(items) == 1

    def test_different_casings_are_the_same_symbol(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist(client, alice)
        for variant in ("aapl", "AAPL", "Aapl", " aApL "):
            client.post(
                f"/watchlists/{wl_id}/symbols",
                json={"symbol": variant},
                headers=alice["headers"],
            )

        items = client.get(f"/watchlists/{wl_id}", headers=alice["headers"]).json()["items"]
        assert [i["symbol"] for i in items] == ["AAPL"]

    @pytest.mark.parametrize(
        "symbol", ["", "   ", "1AAPL", "AA PL", "AAPL!", "TOOLONGSYMBOL", "AAPL'; DROP TABLE x;--"]
    )
    def test_invalid_symbol_rejected_with_422(self, client: TestClient, alice: dict, symbol: str):
        wl_id = _create_watchlist(client, alice)
        r = client.post(
            f"/watchlists/{wl_id}/symbols", json={"symbol": symbol}, headers=alice["headers"]
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_error"

    def test_remove_symbol(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist(client, alice)
        client.post(
            f"/watchlists/{wl_id}/symbols", json={"symbol": "AAPL"}, headers=alice["headers"]
        )

        assert (
            client.delete(f"/watchlists/{wl_id}/symbols/AAPL", headers=alice["headers"]).status_code
            == 204
        )
        assert client.get(f"/watchlists/{wl_id}", headers=alice["headers"]).json()["items"] == []

    def test_remove_accepts_lowercase_in_the_path(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist(client, alice)
        client.post(
            f"/watchlists/{wl_id}/symbols", json={"symbol": "AAPL"}, headers=alice["headers"]
        )
        r = client.delete(f"/watchlists/{wl_id}/symbols/aapl", headers=alice["headers"])
        assert r.status_code == 204

    def test_removing_an_absent_symbol_is_404(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist(client, alice)
        r = client.delete(f"/watchlists/{wl_id}/symbols/AAPL", headers=alice["headers"])
        assert r.status_code == 404

    def test_deleting_a_watchlist_removes_its_items(self, client: TestClient, alice: dict):
        """ON DELETE CASCADE in the schema, not cleanup code in the service."""
        wl_id = _create_watchlist(client, alice)
        client.post(
            f"/watchlists/{wl_id}/symbols", json={"symbol": "AAPL"}, headers=alice["headers"]
        )
        client.delete(f"/watchlists/{wl_id}", headers=alice["headers"])

        from app.models.watchlist_item import WatchlistItem

        # Fresh watchlist, same symbol: proves the old item is gone rather than
        # merely hidden behind the deleted parent.
        new_id = _create_watchlist(client, alice, "Second")
        r = client.post(
            f"/watchlists/{new_id}/symbols", json={"symbol": "AAPL"}, headers=alice["headers"]
        )
        assert r.status_code == 201
        assert WatchlistItem is not None


class TestResponseSchemas:
    def test_watchlist_response_shape(self, client: TestClient, alice: dict):
        wl_id = _create_watchlist(client, alice)
        client.post(
            f"/watchlists/{wl_id}/symbols", json={"symbol": "AAPL"}, headers=alice["headers"]
        )

        body = client.get(f"/watchlists/{wl_id}", headers=alice["headers"]).json()
        assert set(body) == {"id", "name", "created_at", "items"}
        assert set(body["items"][0]) == {"id", "symbol", "created_at"}

    def test_user_id_is_never_exposed_on_a_watchlist(self, client: TestClient, alice: dict):
        """The owner is implied by authentication; echoing user ids back would
        hand out identifiers with no purpose in the client."""
        wl_id = _create_watchlist(client, alice)
        assert "user_id" not in client.get(f"/watchlists/{wl_id}", headers=alice["headers"]).json()

    def test_errors_use_the_standard_envelope(self, client: TestClient, alice: dict):
        r = client.get(f"/watchlists/{uuid.uuid4()}", headers=alice["headers"])
        body = r.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message", "details"}
