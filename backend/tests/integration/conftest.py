"""API test client wired to the transactional test database."""

from __future__ import annotations

from collections.abc import Callable, Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.infrastructure.database.session import get_db
from app.main import create_app


@pytest.fixture
def client(db: Session) -> Generator[TestClient]:
    """A client whose requests run inside the test's transaction.

    get_db is overridden so route handlers share the test's session. Without
    this the app would open its own connection, the test's uncommitted fixture
    data would be invisible to it, and anything the app wrote would survive the
    test's rollback.

    The override yields the session without committing or closing: the `db`
    fixture owns that lifecycle, and letting the app close it would break any
    assertion made after the request.
    """
    app = create_app()

    def _override() -> Generator[Session]:
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def register_user(client: TestClient) -> Callable[..., dict]:
    """Register a user and return their credentials plus an auth header."""
    counter = {"n": 0}

    def _register(password: str = "test-password-123") -> dict:
        counter["n"] += 1
        email = f"user{counter['n']}-{id(counter)}@example.com"

        created = client.post("/auth/register", json={"email": email, "password": password})
        assert created.status_code == 201, created.text

        token = client.post("/auth/login", json={"email": email, "password": password}).json()[
            "access_token"
        ]
        return {
            "email": email,
            "password": password,
            "id": created.json()["id"],
            "token": token,
            "headers": {"Authorization": f"Bearer {token}"},
        }

    return _register


@pytest.fixture
def alice(register_user) -> dict:
    return register_user()


@pytest.fixture
def bob(register_user) -> dict:
    return register_user()
