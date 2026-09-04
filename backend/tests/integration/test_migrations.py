"""Migration reproducibility.

The gate is "migrations apply from an empty database". These tests run Alembic
against a genuinely empty database rather than trusting that it worked once on a
developer machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.models import Base
from tests.conftest import _admin_url, postgres_required

pytestmark = [pytest.mark.integration, postgres_required]

MIGRATION_DB = "smw_migration_test"

EXPECTED_TABLES = {
    "users",
    "watchlists",
    "watchlist_items",
    "market_snapshots",
    "daily_bars",
    "change_events",
    "user_seen_state",
}


@pytest.fixture
def migration_engine():
    """A throwaway empty database, created and dropped per test."""
    admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {MIGRATION_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {MIGRATION_DB}"))

    url = _admin_url().rsplit("/", 1)[0] + f"/{MIGRATION_DB}"
    engine = create_engine(url)
    yield engine, url

    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {MIGRATION_DB} WITH (FORCE)"))
    admin.dispose()


ALEMBIC_INI_PATH = str(Path(__file__).parents[2] / "alembic.ini")


def _alembic_config(url: str) -> Config:
    cfg = Config(ALEMBIC_INI_PATH)
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_upgrade_from_empty_database(migration_engine):
    engine, url = migration_engine
    command.upgrade(_alembic_config(url), "head")

    tables = set(inspect(engine).get_table_names())
    assert tables >= EXPECTED_TABLES
    assert "alembic_version" in tables


def test_downgrade_then_upgrade_is_reproducible(migration_engine):
    engine, url = migration_engine
    cfg = _alembic_config(url)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    assert not (EXPECTED_TABLES & set(inspect(engine).get_table_names()))

    command.upgrade(cfg, "head")
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES


def test_migrations_match_the_models(migration_engine):
    """No drift between models and migrations.

    This is the test that catches the most common real failure in a project like
    this: someone edits a model, forgets to generate a migration, and the code
    works locally against a database built with create_all while production --
    built from migrations -- is missing the column.
    """
    engine, url = migration_engine
    command.upgrade(_alembic_config(url), "head")

    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"compare_type": True, "compare_server_default": True}
        )
        diff = compare_metadata(ctx, Base.metadata)

    assert diff == [], f"models and migrations have diverged: {diff}"


def test_single_migration_head():
    """Two heads mean someone branched the migration history, and `upgrade head`
    becomes ambiguous. Cheap to check, painful to discover during a deploy."""
    heads = ScriptDirectory.from_config(Config(ALEMBIC_INI_PATH)).get_heads()
    assert len(heads) == 1, f"expected exactly one migration head, found {heads}"
