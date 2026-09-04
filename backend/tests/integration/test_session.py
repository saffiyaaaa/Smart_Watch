"""build_engine's statement timeout.

Set via a `SET statement_timeout = ...` issued on connect, not the
`options=-c ...` startup-packet parameter create_engine's connect_args would
normally use for this -- a connection pooler in front of Postgres (PgBouncer,
which is what a managed provider's pooled endpoint, e.g. Neon's `-pooler`
hostname, actually is) may not forward arbitrary startup parameters, so a
connection carrying that option can be rejected outright while a plain
connection to the same endpoint succeeds. This is exactly the failure that
motivated the change: alembic's migration connection (no connect_args) worked
against a pooled endpoint that the app's persistent engine (with the old
connect_args) could not connect to at all.

A `SET` issued after the connection is established is a normal query, not a
startup parameter, so it works the same way whether the endpoint is a direct
connection or a pooler in front of one.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import Settings
from app.infrastructure.database.session import build_engine
from tests.conftest import _test_url, postgres_required

pytestmark = [pytest.mark.integration, postgres_required]


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "environment": "test",
        "database_url": _test_url(),
        "jwt_secret": "test-secret",
    }
    return Settings(**{**base, **overrides})


class TestStatementTimeout:
    def test_a_query_past_the_timeout_is_cancelled(
        self, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
    ):
        settings = _settings(db_statement_timeout_ms=500)
        monkeypatch.setattr("app.infrastructure.database.session.get_settings", lambda: settings)

        engine = build_engine(settings.database_url)
        try:
            with engine.connect() as conn, pytest.raises(Exception, match="statement timeout"):
                conn.execute(text("SELECT pg_sleep(2)"))
        finally:
            engine.dispose()

    def test_a_query_within_the_timeout_succeeds(
        self, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
    ):
        settings = _settings(db_statement_timeout_ms=2000)
        monkeypatch.setattr("app.infrastructure.database.session.get_settings", lambda: settings)

        engine = build_engine(settings.database_url)
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1")).scalar()
                assert result == 1
        finally:
            engine.dispose()

    def test_the_timeout_is_set_via_a_command_not_a_startup_parameter(self, db_engine: Engine):
        """The regression this file exists to prevent: connect_args must not
        reintroduce `options=-c statement_timeout=...`, since that is exactly
        what a connection pooler (PgBouncer, e.g. behind Neon's `-pooler`
        endpoint) can reject outright."""
        engine = build_engine(_test_url())
        try:
            assert "options" not in (engine.dialect.create_connect_args(engine.url)[1] or {})
        finally:
            engine.dispose()
