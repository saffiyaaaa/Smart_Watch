"""Engine, session factory, and the FastAPI session dependency."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def build_engine(database_url: str | None = None):
    settings = get_settings()
    engine = create_engine(
        database_url or settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Recycle before typical proxy/firewall idle timeouts, and verify a
        # connection is alive before handing it out. Without pre_ping, the first
        # request after an idle period fails on a silently dropped connection.
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )

    # A server-side statement timeout is the backstop that keeps the promise
    # "the API never waits indefinitely" -- application-level timeouts cannot
    # cancel a query already running in PostgreSQL.
    #
    # Set via a real `SET` command on connect, not the `options=-c ...`
    # startup-packet parameter create_engine's connect_args would normally
    # use for this: a connection pooler in front of Postgres (PgBouncer,
    # which is what a managed provider's pooled endpoint -- e.g. Neon's
    # `-pooler` hostname -- actually is) may not forward arbitrary startup
    # parameters, so a connection with that option in its startup packet can
    # be rejected outright while a plain connection to the same endpoint
    # succeeds. A `SET` issued after the connection is established is a
    # normal query, not a startup parameter, and works the same way whether
    # the endpoint is a direct connection or a pooler in front of one.
    @event.listens_for(engine, "connect")
    def _set_statement_timeout(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"SET statement_timeout = {settings.db_statement_timeout_ms}")
        finally:
            cursor.close()

    return engine


engine = build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    # Attributes stay usable after commit. Without this, reading any field on a
    # returned ORM object after the request's commit triggers a fresh SELECT on
    # a closed session and raises.
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session]:
    """Request-scoped session.

    One transaction per request: commit on success, roll back on any exception.
    Routes never commit -- that decision belongs to the request boundary, so a
    handler that raises halfway through cannot leave a partial write behind.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
