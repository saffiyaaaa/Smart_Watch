"""Shared test fixtures.

Integration tests run against a real PostgreSQL database, never SQLite. Almost
everything worth testing here is PostgreSQL-specific: ON CONFLICT, DISTINCT ON,
GREATEST's NULL handling, JSONB, regex CHECK constraints, real index plans.
A SQLite suite would pass while proving nothing about production.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base

TEST_DB_NAME = "smw_test"


def _admin_url() -> str:
    """Connect to the maintenance database; CREATE DATABASE cannot run from
    inside the database being created."""
    base = get_settings().database_url
    return base.rsplit("/", 1)[0] + "/postgres"


def _test_url() -> str:
    return get_settings().database_url.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"


def _postgres_available() -> bool:
    engine = None
    try:
        engine = create_engine(_admin_url(), connect_args={"connect_timeout": 3})
        with engine.connect():
            return True
    except Exception:
        return False
    finally:
        if engine is not None:
            engine.dispose()


postgres_required = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL not reachable -- run `docker compose up -d`",
)


@pytest.fixture(scope="session")
def db_engine() -> Generator[Engine]:
    """Create a dedicated test database, build the schema, drop it afterwards.

    The schema is created with metadata.create_all rather than by running
    Alembic. The two are proven equivalent by the drift test in
    tests/integration/test_migrations.py, and create_all keeps the suite fast.
    """
    admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {TEST_DB_NAME}"))
    admin.dispose()

    engine = create_engine(_test_url())
    Base.metadata.create_all(engine)

    yield engine

    engine.dispose()
    admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
    admin.dispose()


@pytest.fixture
def db(db_engine: Engine) -> Generator[Session]:
    """A session wrapped in a transaction that is always rolled back.

    Each test sees a pristine database without paying to recreate the schema.

    join_transaction_mode="create_savepoint" is what makes this actually hold.
    By default a session bound to a connection with an open transaction will
    commit *that* transaction, so a single test calling db.commit() would end
    the outer transaction and leak its rows into every later test. Creating a
    savepoint instead means an inner commit releases only the savepoint, and the
    rollback below still undoes everything.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def db_factory(db_engine: Engine):
    """Independent sessions on separate connections, for concurrency tests.

    Concurrency cannot be tested through the `db` fixture: work sharing one
    connection is serialised by definition, so a race that the schema is
    supposed to prevent could never occur. These sessions commit for real and
    the caller is responsible for cleanup.
    """
    sessions: list[Session] = []

    def _make() -> Session:
        s = sessionmaker(bind=db_engine, expire_on_commit=False)()
        sessions.append(s)
        return s

    yield _make

    for s in sessions:
        s.close()


@pytest.fixture(autouse=True)
def _quiet_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    yield


os.environ.setdefault("ENVIRONMENT", "test")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip tests marked `network` unless explicitly opted in.

    These hit the real yfinance API: slow, dependent on an unofficial service
    staying up, and unnecessary for every other run. `pytest -m network` or
    `RUN_NETWORK_TESTS=1 pytest` runs them on demand.
    """
    if os.environ.get("RUN_NETWORK_TESTS") == "1":
        return
    skip_network = pytest.mark.skip(
        reason="set RUN_NETWORK_TESTS=1 (or pytest -m network) to hit the real provider"
    )
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)
