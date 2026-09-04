"""The real market data adapter: Yahoo Finance via the unofficial yfinance
library.

yfinance is synchronous and makes real HTTP calls, so every call here goes
through call_with_retry, which moves it to a worker thread and applies the
configured timeout and bounded, backed-off retry. This class's only job is to
translate yfinance's failure modes into the three provider exceptions
everything else in the system understands, and to validate what comes back
through Quote/Bar rather than trust it.

Observed empirically (see the Phase 4 build notes) rather than assumed: an
unknown or delisted symbol does not raise from yfinance -- it returns an
empty DataFrame. That is the condition this module treats as SymbolNotFound.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
from pydantic import ValidationError as PydanticValidationError

from app.config import Settings, get_settings
from app.domain.market.quote import Bar, Quote
from app.infrastructure.providers.base import MarketDataProvider
from app.infrastructure.providers.exceptions import (
    InvalidProviderData,
    ProviderUnavailable,
    SymbolNotFound,
)
from app.infrastructure.providers.retry import call_with_retry


def _decimal_from(value: Any, *, context: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise InvalidProviderData(f"{context}: unusable numeric value {value!r}") from exc


def _volume_from(raw: Any) -> int | None:
    if raw is None or pd.isna(raw):
        return None
    return int(raw)


class YFinanceProvider(MarketDataProvider):
    source = "yfinance"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def get_quote(self, symbol: str) -> Quote:
        s = self._settings
        return await call_with_retry(
            lambda: self._fetch_quote(symbol),
            timeout_seconds=s.provider_timeout_seconds,
            max_retries=s.provider_max_retries,
            backoff_base_seconds=s.provider_backoff_base_seconds,
        )

    async def get_daily_history(self, symbol: str, days: int) -> list[Bar]:
        s = self._settings
        return await call_with_retry(
            lambda: self._fetch_daily_history(symbol, days),
            timeout_seconds=s.provider_timeout_seconds,
            max_retries=s.provider_max_retries,
            backoff_base_seconds=s.provider_backoff_base_seconds,
        )

    # -- Synchronous, network-touching methods. Only ever invoked through
    # call_with_retry's asyncio.to_thread, never called directly from async
    # code, since yfinance would block the event loop otherwise.

    def _fetch_quote(self, symbol: str) -> Quote:
        import yfinance as yf

        try:
            history = yf.Ticker(symbol).history(period="1d", interval="1m")
        except Exception as exc:
            # yfinance's own exception types are not a stable public contract
            # -- they change between releases and wrap requests/curl_cffi
            # errors differently depending on version. Treating any transport
            # failure as retryable is the safe default here; a genuinely
            # persistent problem still surfaces once retries are exhausted.
            raise ProviderUnavailable(f"yfinance request failed for {symbol}: {exc}") from exc

        if history is None or history.empty:
            raise SymbolNotFound(f"no data returned for {symbol!r}")

        last = history.iloc[-1]

        market_timestamp = history.index[-1].to_pydatetime()
        market_timestamp = (
            market_timestamp.replace(tzinfo=UTC)
            if market_timestamp.tzinfo is None
            else market_timestamp.astimezone(UTC)
        )

        price = _decimal_from(last.get("Close"), context=f"quote for {symbol}")
        volume = _volume_from(last.get("Volume"))

        try:
            return Quote(
                source=self.source,
                symbol=symbol,
                price=price,
                volume=volume,
                market_timestamp=market_timestamp,
                fetched_at=datetime.now(UTC),
            )
        except PydanticValidationError as exc:
            raise InvalidProviderData(
                f"provider returned invalid data for {symbol}: {exc}"
            ) from exc

    def _fetch_daily_history(self, symbol: str, days: int) -> list[Bar]:
        import yfinance as yf

        try:
            history = yf.Ticker(symbol).history(period=f"{days}d", interval="1d")
        except Exception as exc:
            raise ProviderUnavailable(f"yfinance request failed for {symbol}: {exc}") from exc

        if history is None or history.empty:
            raise SymbolNotFound(f"no history returned for {symbol!r}")

        bars: list[Bar] = []
        for idx, row in history.iterrows():
            close = _decimal_from(row.get("Close"), context=f"history row for {symbol} on {idx}")
            volume = _volume_from(row.get("Volume"))
            try:
                bars.append(
                    Bar(
                        source=self.source,
                        symbol=symbol,
                        session_date=idx.date(),
                        close=close,
                        volume=volume,
                    )
                )
            except PydanticValidationError as exc:
                raise InvalidProviderData(
                    f"invalid history row for {symbol} on {idx}: {exc}"
                ) from exc
        return bars
