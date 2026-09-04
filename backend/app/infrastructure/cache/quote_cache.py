"""A best-effort cache in front of MarketDataProvider.get_quote (Phase 12).

Nothing in the request path calls a provider at all -- app/api/routes/stocks.py
serves the last snapshot the worker persisted, by design. So this cache is not
about API latency; it is about the worker not re-fetching the same symbol from
the provider more often than `cache_quote_ttl_seconds` warrants, which matters
whenever a cycle is re-run before the previous one's data has gone stale (a
short WORKER_INTERVAL_SECONDS, an ad-hoc `--once` invocation during a demo, or
a future second worker process) -- see docker-compose.yml's redis service
comment.

Disabled by default (`cache_enabled=False`) and fails open on every error path:
a cache is an optimization, and it must never become a new way for ingestion to
fail. Any Redis error -- unreachable, timeout, a corrupted value -- is logged
and treated as a cache miss, never raised.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.config import Settings
from app.domain.market.quote import Quote

logger = logging.getLogger("smw.cache")


class _AsyncRedisLike(Protocol):
    """The slice of redis.asyncio.Redis this module actually uses.

    Named so tests can inject a small in-memory fake instead of a live Redis
    server -- the caching *logic* (key format, TTL, fail-open behaviour) does
    not need a real network round-trip to be verified.
    """

    async def get(self, name: str) -> str | bytes | None: ...
    async def set(self, name: str, value: str, ex: int | None = None) -> object: ...
    async def aclose(self) -> None: ...


class QuoteCache:
    """Symbol -> most recent cached Quote, with a short TTL."""

    def __init__(self, settings: Settings, *, client: _AsyncRedisLike | None = None) -> None:
        self._enabled = settings.cache_enabled
        self._ttl = settings.cache_quote_ttl_seconds
        self._client = client
        self._owns_client = client is None
        if self._enabled and self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(settings.redis_url, decode_responses=True)

    @staticmethod
    def _key(symbol: str) -> str:
        return f"quote:{symbol.upper()}"

    async def get(self, symbol: str) -> Quote | None:
        if not self._enabled or self._client is None:
            return None
        try:
            raw = await self._client.get(self._key(symbol))
        except Exception:
            logger.warning("quote cache read failed for %s", symbol, exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return Quote.model_validate_json(raw)
        except Exception:
            logger.warning("quote cache held an unparsable value for %s", symbol, exc_info=True)
            return None

    async def set(self, symbol: str, quote: Quote) -> None:
        if not self._enabled or self._client is None:
            return
        try:
            await self._client.set(self._key(symbol), quote.model_dump_json(), ex=self._ttl)
        except Exception:
            logger.warning("quote cache write failed for %s", symbol, exc_info=True)

    async def close(self) -> None:
        # A caller-supplied client is the caller's to close, not ours -- only
        # the connection we opened ourselves gets torn down here.
        if self._client is not None and self._owns_client:
            try:
                await self._client.aclose()
            except Exception:
                logger.warning("quote cache close failed", exc_info=True)
