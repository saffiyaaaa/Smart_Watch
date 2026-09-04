"""Application configuration.

Every tunable number in the system lives here and nowhere else. If a threshold
appears as a literal inside domain logic or a route, that is a bug: it means the
behaviour described in docs/product-spec.md can no longer be changed or tested
from one place.

The scoring fields mirror docs/product-spec.md section 4 exactly.
"""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal[
    "yfinance",
    "mock",
    "failing",
    "timeout",
    "stale",
    "malformed",
    "conflicting",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------ application
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --------------------------------------------------------------- database
    database_url: str = (
        "postgresql+psycopg://smw:smw_dev_password@localhost:5433/smart_market_watchlist"
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_statement_timeout_ms: int = 5000

    # ------------------------------------------------------------------- auth
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # --------------------------------------------------------------- provider
    market_provider: ProviderName = "mock"
    provider_timeout_seconds: float = 10.0
    provider_max_retries: int = Field(default=3, ge=1, le=10)
    provider_backoff_base_seconds: float = 0.5

    # -------------------------------------------------------------- freshness
    freshness_fresh_seconds: int = 300
    freshness_stale_seconds: int = 900
    market_timezone: str = "America/New_York"
    market_open: time = time(9, 30)
    market_close: time = time(16, 0)

    # ---------------------------------------------------------------- scoring
    price_min_pct: float = 1.0
    price_max_pct: float = 8.0
    price_max_points: float = 55.0

    volume_min_ratio: float = 1.5
    volume_max_ratio: float = 5.0
    volume_max_points: float = 45.0

    confidence_delayed: float = Field(default=0.85, gt=0.0, le=1.0)
    confidence_stale: float = Field(default=0.60, gt=0.0, le=1.0)
    confidence_conflicting: float = Field(default=0.50, gt=0.0, le=1.0)
    confidence_no_volume_baseline: float = Field(default=0.85, gt=0.0, le=1.0)

    severity_watch_min: int = 20
    severity_important_min: int = 50
    severity_high_min: int = 75

    volume_baseline_sessions: int = 20
    volume_baseline_min_sessions: int = 5
    conflict_price_tolerance_pct: float = 0.5

    # ----------------------------------------------------------------- worker
    worker_interval_seconds: int = 300
    worker_symbol_concurrency: int = Field(default=4, ge=1, le=32)
    first_visit_lookback_hours: int = 24
    first_visit_max_events: int = 20
    # Cap for a *returning* user's feed. Distinct from first_visit_max_events:
    # a first visit is bounded by a time window (the last 24h) that naturally
    # limits volume, but a returning user who has not checked in a long time
    # could otherwise have an unbounded number of events since their cursor.
    change_feed_max_events: int = 100

    # ---------------------------------------------------------------- caching
    redis_url: str = "redis://localhost:6380/0"
    cache_enabled: bool = False
    cache_quote_ttl_seconds: int = 30

    # ------------------------------------------------------------ rate limiting
    rate_limit_enabled: bool = True
    # Registration and login are the endpoints an attacker actually benefits
    # from hammering (credential stuffing, spam accounts); everything else
    # sits behind auth already, so only these two carry a dedicated budget.
    auth_rate_limit_max_requests: int = Field(default=10, ge=1)
    auth_rate_limit_window_seconds: float = Field(default=60.0, gt=0)

    # ------------------------------------------------------------- request size
    # Every payload this API accepts is a small JSON object; 1 MB is generous
    # headroom, not a real limit on legitimate use.
    max_request_body_bytes: int = Field(default=1_000_000, ge=1)

    # --------------------------------------------------------------- frontend
    cors_origins: str = "http://localhost:5173"

    # ------------------------------------------------------------- validation
    @field_validator("database_url")
    @classmethod
    def _normalize_database_url_driver(cls, v: str) -> str:
        """Every managed Postgres provider (Neon, Render, Heroku, Supabase...)
        hands out a bare `postgresql://` or `postgres://` connection string,
        which is a real value SQLAlchemy accepts -- just not with this
        project's driver. This project installs `psycopg` (v3, see
        pyproject.toml), not `psycopg2`, and SQLAlchemy's default dialect for
        an unqualified `postgresql://` scheme is psycopg2. Without this
        normalization, pasting a provider's connection string verbatim into
        DATABASE_URL fails at startup with `ModuleNotFoundError: No module
        named 'psycopg2'` -- a confusing error for a correct connection
        string. A URL that already names a driver (`+psycopg`, `+psycopg2`,
        an explicit test driver) is left alone.
        """
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://") :]
        return v

    @field_validator("market_timezone")
    @classmethod
    def _timezone_must_resolve(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as exc:  # ZoneInfoNotFoundError subclasses KeyError,
            # which pydantic does not convert into a ValidationError -- rewrap it
            # so a bad zone surfaces as a normal config error, not a raw crash.
            raise ValueError(f"unknown market_timezone {v!r}") from exc
        return v

    @model_validator(mode="after")
    def _check_invariants(self) -> Settings:
        if self.freshness_fresh_seconds >= self.freshness_stale_seconds:
            raise ValueError("freshness_fresh_seconds must be < freshness_stale_seconds")
        if self.price_min_pct >= self.price_max_pct:
            raise ValueError("price_min_pct must be < price_max_pct")
        if self.volume_min_ratio >= self.volume_max_ratio:
            raise ValueError("volume_min_ratio must be < volume_max_ratio")
        if not (
            0
            < self.severity_watch_min
            < self.severity_important_min
            < self.severity_high_min
            <= 100
        ):
            raise ValueError("severity bands must be strictly increasing within 0..100")
        if self.volume_baseline_min_sessions > self.volume_baseline_sessions:
            raise ValueError("volume_baseline_min_sessions cannot exceed volume_baseline_sessions")

        # The load-bearing invariant from docs/product-spec.md section 4:
        # untrustworthy data must not be able to produce a HIGH alert. Checked
        # here so a careless .env edit fails at startup rather than silently
        # weakening the guarantee in production.
        max_raw = self.price_max_points + self.volume_max_points
        for label, multiplier in (
            ("stale", self.confidence_stale),
            ("conflicting", self.confidence_conflicting),
        ):
            if round(max_raw * multiplier) >= self.severity_high_min:
                raise ValueError(
                    f"confidence_{label}={multiplier} allows a {label} observation to "
                    f"reach HIGH severity (>= {self.severity_high_min}); this breaks the "
                    "invariant documented in docs/product-spec.md section 4"
                )
        return self

    # ----------------------------------------------------------------- derived
    @property
    def market_tz(self) -> ZoneInfo:
        return ZoneInfo(self.market_timezone)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Tests override via dependency injection or by clearing
    the cache, never by mutating a module-level singleton."""
    return Settings()
