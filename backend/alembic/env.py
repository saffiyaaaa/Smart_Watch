"""Alembic environment.

The database URL comes from app.config rather than alembic.ini so that
migrations, the API and the worker can never disagree about which database they
are pointed at.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.config import get_settings

# Importing app.models registers every table on Base.metadata. Without this,
# autogenerate silently produces an empty migration.
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Only fall back to app config when a URL has not been supplied explicitly.
# Overriding unconditionally would make migrations impossible to point at
# another database, which tests and one-off CI runs legitimately need to do.
if not config.get_main_option("sqlalchemy.url", default=""):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Detect column type and server-default drift, not just added and
            # dropped tables. Off by default, and its absence is why schemas
            # quietly diverge from models over time.
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
