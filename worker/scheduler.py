"""Ingestion scheduling: a single asyncio loop, deliberately not Celery/Redis.

The workload is one provider call per tracked symbol every few minutes, run
from a single process. That does not need a distributed task queue, and per
this project's own principle -- every technology needs a concrete reason to
exist -- adding one here would not have one. See docs/design-decisions.md.

Run from the repository root (not from backend/), since `worker` and `app`
are sibling packages:

    backend/.venv/bin/python -m worker.scheduler --once
    backend/.venv/bin/python -m worker.scheduler            # loops forever

`-m` is required, not `python worker/scheduler.py`: only `-m` puts the
repository root (rather than worker/'s own directory) on sys.path, which is
what makes `from worker.ingestion import ingest_all` resolve.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config import get_settings
from app.infrastructure.database.session import SessionLocal
from app.infrastructure.providers.factory import get_provider
from worker.ingestion import ingest_all

logger = logging.getLogger("smw.scheduler")


async def run_once(*, session_factory: Callable[[], Session] = SessionLocal) -> list:
    """One ingestion cycle: every tracked symbol, once each.

    `session_factory` defaults to the real SessionLocal and is overridden only
    by tests, so a scheduler test can point at the isolated test database
    instead of silently opening a connection to whatever DATABASE_URL the
    environment happens to have configured.
    """
    settings = get_settings()
    provider = get_provider()

    db = session_factory()
    try:
        results = await ingest_all(db, provider, settings=settings)
    finally:
        db.close()

    ok = sum(1 for r in results if r.outcome in ("created", "duplicate"))
    failed = len(results) - ok
    logger.info(
        "ingestion cycle complete: %d ok, %d failed, %d total symbols", ok, failed, len(results)
    )
    return results


async def run_forever() -> None:
    settings = get_settings()
    logger.info("scheduler starting, interval=%ds", settings.worker_interval_seconds)
    while True:
        try:
            await run_once()
        except Exception:
            # A whole-cycle failure -- the database being down, say -- must
            # not kill the scheduler. The next tick gets another chance once
            # the dependency recovers.
            logger.exception("ingestion cycle raised unexpectedly")
        await asyncio.sleep(settings.worker_interval_seconds)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Market data ingestion worker")
    parser.add_argument(
        "--once", action="store_true", help="run a single ingestion cycle and exit"
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)

    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )

    if args.once:
        asyncio.run(run_once())
    else:
        asyncio.run(run_forever())


if __name__ == "__main__":
    main()
