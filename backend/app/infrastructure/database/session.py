"""Engine, session factory, and the FastAPI session dependency."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def build_engine(database_url: str | None = None):
    settings = get_settings()
    return create_engine(
        database_url or settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Recycle before typical proxy/firewall idle timeouts, and verify a
        # connection is alive before handing it out. Without pre_ping, the first
        # request after an idle period fails on a silently dropped connection.
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            # A server-side statement timeout is the backstop that keeps the
            # promise "the API never waits indefinitely". Application-level
            # timeouts cannot cancel a query already running in PostgreSQL.
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
        },
        future=True,
    )


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
