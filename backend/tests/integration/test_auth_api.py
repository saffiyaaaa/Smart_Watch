"""Authentication endpoint behaviour."""

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.rate_limit import InMemoryRateLimiter
from tests.conftest import postgres_required

pytestmark = [pytest.mark.integration, postgres_required]


class TestRegistration:
    def test_creates_an_account(self, client: TestClient):
        r = client.post(
            "/auth/register", json={"email": "new@example.com", "password": "a-good-password"}
        )
        assert r.status_code == 201
        assert r.json()["email"] == "new@example.com"

    def test_never_returns_the_password_hash(self, client: TestClient):
        """The response schema is an allowlist, so a field cannot leak by
        being added to the model later."""
        r = client.post(
            "/auth/register", json={"email": "leak@example.com", "password": "a-good-password"}
        )
        body = r.text.lower()
        assert "password" not in body
        assert "hash" not in body

    def test_duplicate_email_is_a_conflict(self, client: TestClient):
        payload = {"email": "dup@example.com", "password": "a-good-password"}
        assert client.post("/auth/register", json=payload).status_code == 201

        r = client.post("/auth/register", json=payload)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "conflict"

    def test_email_is_case_insensitive_for_identity(self, client: TestClient):
        """ "Bob@x.com" and "bob@x.com" are one person. Without normalisation a
        user could register twice and then fail to log in."""
        assert (
            client.post(
                "/auth/register", json={"email": "Case@Example.com", "password": "a-good-password"}
            ).status_code
            == 201
        )
        r = client.post(
            "/auth/register", json={"email": "case@example.com", "password": "a-good-password"}
        )
        assert r.status_code == 409

    @pytest.mark.parametrize(
        "payload",
        [
            {"email": "not-an-email", "password": "a-good-password"},
            {"email": "ok@example.com", "password": "short"},
            {"email": "ok@example.com"},
            {"password": "a-good-password"},
            {},
        ],
    )
    def test_invalid_input_rejected_with_422(self, client: TestClient, payload: dict):
        r = client.post("/auth/register", json=payload)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_error"

    def test_over_long_password_rejected_cleanly(self, client: TestClient):
        r = client.post("/auth/register", json={"email": "long@example.com", "password": "x" * 100})
        assert r.status_code == 422


class TestLogin:
    def test_returns_a_usable_token(self, client: TestClient, alice: dict):
        r = client.post(
            "/auth/login", json={"email": alice["email"], "password": alice["password"]}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0

        me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.status_code == 200
        assert me.json()["email"] == alice["email"]

    def test_wrong_password_rejected(self, client: TestClient, alice: dict):
        r = client.post("/auth/login", json={"email": alice["email"], "password": "wrong"})
        assert r.status_code == 401

    def test_unknown_email_and_wrong_password_are_indistinguishable(
        self, client: TestClient, alice: dict
    ):
        """Otherwise the login form becomes a tool for discovering which
        addresses are registered."""
        unknown = client.post(
            "/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
        )
        wrong = client.post("/auth/login", json={"email": alice["email"], "password": "wrong"})

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json() == wrong.json()

    def test_login_is_case_insensitive_on_email(self, client: TestClient, alice: dict):
        r = client.post(
            "/auth/login",
            json={"email": alice["email"].upper(), "password": alice["password"]},
        )
        assert r.status_code == 200


class TestProtectedEndpoints:
    """Failure-matrix: unauthenticated access to protected resources."""

    PROTECTED: ClassVar[list[tuple[str, str]]] = [
        ("get", "/auth/me"),
        ("get", "/watchlists"),
        ("post", "/watchlists"),
        ("get", "/watchlists/11111111-1111-1111-1111-111111111111"),
        ("delete", "/watchlists/11111111-1111-1111-1111-111111111111"),
        ("post", "/watchlists/11111111-1111-1111-1111-111111111111/symbols"),
        ("delete", "/watchlists/11111111-1111-1111-1111-111111111111/symbols/AAPL"),
    ]

    @pytest.mark.parametrize(("method", "path"), PROTECTED)
    def test_missing_token_is_401(self, client: TestClient, method: str, path: str):
        # client.request(), not client.get()/delete(): those do not accept a
        # json body, and the parametrised list mixes verbs.
        r = client.request(method.upper(), path, json={})
        assert r.status_code == 401, f"{method.upper()} {path} returned {r.status_code}"
        assert r.json()["error"]["code"] == "unauthorized"

    @pytest.mark.parametrize(("method", "path"), PROTECTED)
    def test_garbage_token_is_401(self, client: TestClient, method: str, path: str):
        r = client.request(
            method.upper(), path, json={}, headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert r.status_code == 401

    def test_401_carries_www_authenticate(self, client: TestClient):
        assert "www-authenticate" in {k.lower() for k in client.get("/auth/me").headers}

    def test_deleted_user_token_stops_working(self, client: TestClient, alice: dict, db):
        """The user is loaded from the database on every request rather than
        trusted from the token body, so a removed account cannot keep operating
        on a still-valid token."""
        from app.models.user import User

        db.query(User).filter(User.email == alice["email"]).delete()
        db.flush()

        assert client.get("/auth/me", headers=alice["headers"]).status_code == 401


class TestRateLimiting:
    """The Phase 13 gate: /auth/register and /auth/login carry a sensible
    rate limit. A tiny budget is swapped in directly on app.state (see
    app/api/deps.py's rate_limit_auth) rather than firing the default 10
    requests or waiting out the real 60s window -- this is the same limiter
    instance the dependency reads, just configured for a fast, deterministic
    test.
    """

    def test_exceeding_the_login_budget_is_429(self, client: TestClient):
        client.app.state.auth_rate_limiter = InMemoryRateLimiter(limit=2, window_seconds=60)
        payload = {"email": "nobody@example.com", "password": "wrong-password"}

        for _ in range(2):
            r = client.post("/auth/login", json=payload)
            assert r.status_code != 429

        r = client.post("/auth/login", json=payload)
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "rate_limited"

    def test_429_carries_retry_after(self, client: TestClient):
        client.app.state.auth_rate_limiter = InMemoryRateLimiter(limit=1, window_seconds=30)
        payload = {"email": "nobody@example.com", "password": "wrong-password"}

        client.post("/auth/login", json=payload)
        r = client.post("/auth/login", json=payload)

        assert r.status_code == 429
        retry_after = int(r.headers["retry-after"])
        assert 0 < retry_after <= 30

    def test_register_and_login_share_one_budget_per_client(self, client: TestClient):
        """Both endpoints are reachable before any identity exists, so both
        draw from the same IP-keyed budget -- a script alternating between
        the two to dodge a per-endpoint limit gains nothing."""
        client.app.state.auth_rate_limiter = InMemoryRateLimiter(limit=2, window_seconds=60)

        assert client.post(
            "/auth/register", json={"email": "budget@example.com", "password": "a-good-password"}
        ).status_code in (201, 409)
        assert (
            client.post(
                "/auth/login", json={"email": "budget@example.com", "password": "a-good-password"}
            ).status_code
            != 429
        )

        r = client.post(
            "/auth/register", json={"email": "budget2@example.com", "password": "a-good-password"}
        )
        assert r.status_code == 429

    def test_a_different_client_ip_has_its_own_budget(self, client: TestClient):
        """The limiter is keyed by client IP; TestClient's requests always
        come from the same synthetic address, so this exercises the key
        function directly rather than through two real connections."""
        limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
        client.app.state.auth_rate_limiter = limiter

        payload = {"email": "nobody@example.com", "password": "wrong-password"}
        client.post("/auth/login", json=payload)  # consumes the one slot for testclient's IP

        limiter.check("some-other-ip")  # a distinct key is unaffected


class TestRequestSizeLimit:
    def test_an_oversized_body_is_rejected(self, client: TestClient):
        oversized_password = "a" * 2_000_000  # default limit is 1,000,000 bytes
        r = client.post(
            "/auth/register",
            json={"email": "big@example.com", "password": oversized_password},
        )
        assert r.status_code == 413
        assert r.json()["error"]["code"] == "request_too_large"

    def test_a_normal_sized_body_is_unaffected(self, client: TestClient):
        r = client.post(
            "/auth/register",
            json={"email": "normal@example.com", "password": "a-good-password"},
        )
        assert r.status_code == 201
