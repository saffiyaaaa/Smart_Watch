"""Configuration invariants.

These matter more than they look. config.py refuses to start when a threshold
combination would break a guarantee documented in docs/product-spec.md. That
turns a silent behavioural regression -- the dangerous kind -- into a loud
startup failure.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**overrides) -> Settings:
    """Build Settings from explicit values, ignoring any developer .env so the
    test asserts on the code's defaults rather than the local machine's."""
    base = {
        "_env_file": None,
        "environment": "test",
        "database_url": "postgresql+psycopg://u:p@localhost:5433/db",
        "jwt_secret": "test-secret",
    }
    return Settings(**{**base, **overrides})


class TestDefaults:
    def test_defaults_match_the_product_spec(self):
        s = _settings()
        assert (s.price_min_pct, s.price_max_pct, s.price_max_points) == (1.0, 8.0, 55.0)
        assert (s.volume_min_ratio, s.volume_max_ratio, s.volume_max_points) == (1.5, 5.0, 45.0)
        assert (s.severity_watch_min, s.severity_important_min, s.severity_high_min) == (20, 50, 75)
        assert s.confidence_stale == 0.60
        assert s.confidence_conflicting == 0.50

    def test_max_raw_score_is_100(self):
        s = _settings()
        assert s.price_max_points + s.volume_max_points == 100.0

    def test_cors_origins_parse_to_a_list(self):
        s = _settings(cors_origins="http://a.test, http://b.test")
        assert s.cors_origin_list == ["http://a.test", "http://b.test"]

    def test_market_timezone_resolves(self):
        assert _settings().market_tz.key == "America/New_York"


class TestInvariantsRejectBadConfig:
    def test_unknown_timezone_rejected(self):
        with pytest.raises(ValidationError):
            _settings(market_timezone="Mars/Olympus_Mons")

    def test_freshness_windows_must_be_ordered(self):
        with pytest.raises(ValidationError, match="freshness_fresh_seconds"):
            _settings(freshness_fresh_seconds=900, freshness_stale_seconds=300)

    def test_severity_bands_must_increase(self):
        with pytest.raises(ValidationError, match="severity bands"):
            _settings(severity_watch_min=60, severity_important_min=50)

    def test_price_bounds_must_be_ordered(self):
        with pytest.raises(ValidationError, match="price_min_pct"):
            _settings(price_min_pct=9.0, price_max_pct=8.0)

    def test_volume_bounds_must_be_ordered(self):
        with pytest.raises(ValidationError, match="volume_min_ratio"):
            _settings(volume_min_ratio=6.0, volume_max_ratio=5.0)

    def test_unknown_provider_rejected(self):
        with pytest.raises(ValidationError):
            _settings(market_provider="bloomberg_terminal")


class TestStaleDataCannotReachHigh:
    """The load-bearing guarantee from docs/product-spec.md section 4."""

    def test_stale_confidence_that_would_allow_high_is_rejected(self):
        # 0.80 x 100 = 80 >= 75, so a stale quote could raise a HIGH alert.
        with pytest.raises(ValidationError, match="reach HIGH severity"):
            _settings(confidence_stale=0.80)

    def test_conflicting_confidence_that_would_allow_high_is_rejected(self):
        with pytest.raises(ValidationError, match="reach HIGH severity"):
            _settings(confidence_conflicting=0.95)

    def test_boundary_is_exactly_at_the_high_floor(self):
        # 0.75 x 100 = 75, which *is* HIGH -> must be rejected.
        with pytest.raises(ValidationError, match="reach HIGH severity"):
            _settings(confidence_stale=0.75)
        # 0.74 x 100 = 74, one point below HIGH -> allowed.
        assert _settings(confidence_stale=0.74).confidence_stale == 0.74

    def test_defaults_satisfy_the_invariant(self):
        s = _settings()
        max_raw = s.price_max_points + s.volume_max_points
        assert round(max_raw * s.confidence_stale) < s.severity_high_min
        assert round(max_raw * s.confidence_conflicting) < s.severity_high_min

    def test_delayed_may_still_reach_high_by_design(self):
        # Not a violation: see docs/product-spec.md section 4. DELAYED shades
        # confidence, it does not veto -- suppressing a real 8% move because a
        # quote is 10 minutes old would be the more expensive error.
        s = _settings()
        max_raw = s.price_max_points + s.volume_max_points
        assert round(max_raw * s.confidence_delayed) >= s.severity_high_min
