"""Operational endpoint contract."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health_returns_ok(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_does_not_touch_dependencies(client: TestClient):
    """Liveness must stay up even with every dependency down; otherwise a
    database blip gets a healthy process killed by the orchestrator."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.config.get_settings", lambda: (_ for _ in ()).throw(AssertionError))
        assert client.get("/health").status_code == 200


def test_ready_reports_a_checks_map(client: TestClient):
    r = client.get("/ready")
    assert r.status_code == 200
    assert "checks" in r.json()


def test_openapi_schema_is_generated(client: TestClient):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/health" in r.json()["paths"]
