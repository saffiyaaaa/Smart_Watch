"""The internal, provider-agnostic representation of market data.

Quote and Bar are the boundary between "whatever a provider sent" and the rest
of the system. Once one of these exists, every field on it is known-good: a
positive price, a real volume or an explicit absence of one, a timezone-aware
timestamp that is not from the future. Constructing one from bad input raises
pydantic.ValidationError rather than silently coercing -- coercion here is
exactly how stale or fabricated data would end up looking legitimate two
layers downstream, in a change event a user sees.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.domain.market.validators import (
    require_finite_positive,
    require_non_blank_source,
    require_non_negative_volume,
    require_valid_symbol,
)

# Allowance for clock skew between this process and a provider's reported
# timestamp. Further in the future than this cannot be a real observation and
# means the response is malformed or the provider's clock is broken -- either
# way, not something to silently accept.
MAX_FUTURE_SKEW = timedelta(seconds=60)


class Quote(BaseModel):
    """A single validated observation: this symbol traded at this price at
    this instant, according to this source."""

    model_config = ConfigDict(frozen=True)

    source: str
    symbol: str
    price: Decimal
    volume: int | None
    # When the market says the price held.
    market_timestamp: datetime
    # When we asked. Never substituted for market_timestamp -- see
    # docs/product-spec.md section 2.
    fetched_at: datetime

    @field_validator("source")
    @classmethod
    def _source_valid(cls, v: str) -> str:
        return require_non_blank_source(v)

    @field_validator("symbol")
    @classmethod
    def _symbol_valid(cls, v: str) -> str:
        return require_valid_symbol(v)

    @field_validator("price")
    @classmethod
    def _price_valid(cls, v: Decimal) -> Decimal:
        return require_finite_positive(v, field="price")

    @field_validator("volume")
    @classmethod
    def _volume_valid(cls, v: int | None) -> int | None:
        return require_non_negative_volume(v)

    @field_validator("market_timestamp", "fetched_at")
    @classmethod
    def _timestamps_are_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return v

    @model_validator(mode="after")
    def _market_timestamp_not_from_the_future(self) -> Quote:
        if self.market_timestamp > datetime.now(UTC) + MAX_FUTURE_SKEW:
            raise ValueError(f"market_timestamp {self.market_timestamp} is in the future")
        return self


class Bar(BaseModel):
    """One completed trading session, used as a change-detection baseline."""

    model_config = ConfigDict(frozen=True)

    source: str
    symbol: str
    session_date: date
    close: Decimal
    volume: int | None

    @field_validator("source")
    @classmethod
    def _source_valid(cls, v: str) -> str:
        return require_non_blank_source(v)

    @field_validator("symbol")
    @classmethod
    def _symbol_valid(cls, v: str) -> str:
        return require_valid_symbol(v)

    @field_validator("close")
    @classmethod
    def _close_valid(cls, v: Decimal) -> Decimal:
        return require_finite_positive(v, field="close")

    @field_validator("volume")
    @classmethod
    def _volume_valid(cls, v: int | None) -> int | None:
        return require_non_negative_volume(v)
