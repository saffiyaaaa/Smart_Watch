"""Freshness classification -- pure, no database, no provider."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.enums import Freshness
from app.domain.market.freshness import (
    classify_freshness,
    freshness_reference,
    is_market_open,
    most_recent_session_close,
)

NY = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)

# A Wednesday, well inside market hours in New York.
WEDNESDAY_OPEN = datetime(2026, 3, 11, 15, 0, tzinfo=UTC)  # 10:00 EST... adjusted below


def ny_instant(year, month, day, hour, minute=0) -> datetime:
    """Build a UTC datetime from wall-clock New York time, so tests read in
    the timezone the market actually operates in."""
    return datetime(year, month, day, hour, minute, tzinfo=NY).astimezone(UTC)


class TestIsMarketOpen:
    def test_weekday_during_hours_is_open(self):
        assert is_market_open(
            ny_instant(2026, 3, 11, 12, 0), timezone=NY, open_time=OPEN, close_time=CLOSE
        )

    def test_weekday_before_open_is_closed(self):
        assert not is_market_open(
            ny_instant(2026, 3, 11, 8, 0), timezone=NY, open_time=OPEN, close_time=CLOSE
        )

    def test_weekday_after_close_is_closed(self):
        assert not is_market_open(
            ny_instant(2026, 3, 11, 17, 0), timezone=NY, open_time=OPEN, close_time=CLOSE
        )

    def test_exactly_at_open_is_open(self):
        assert is_market_open(
            ny_instant(2026, 3, 11, 9, 30), timezone=NY, open_time=OPEN, close_time=CLOSE
        )

    def test_exactly_at_close_is_closed(self):
        """Half-open interval [open, close)."""
        assert not is_market_open(
            ny_instant(2026, 3, 11, 16, 0), timezone=NY, open_time=OPEN, close_time=CLOSE
        )

    def test_saturday_is_closed(self):
        assert not is_market_open(
            ny_instant(2026, 3, 14, 12, 0), timezone=NY, open_time=OPEN, close_time=CLOSE
        )

    def test_sunday_is_closed(self):
        assert not is_market_open(
            ny_instant(2026, 3, 15, 12, 0), timezone=NY, open_time=OPEN, close_time=CLOSE
        )

    def test_converts_from_a_different_timezone(self):
        """The check must convert to market-local time, not compare wall-clock
        hours in whatever zone the caller happens to pass."""
        # 12:00 UTC is 07:00 EST (winter) -- before the New York open.
        utc_morning = datetime(2026, 1, 14, 12, 0, tzinfo=UTC)
        assert not is_market_open(utc_morning, timezone=NY, open_time=OPEN, close_time=CLOSE)


class TestMostRecentSessionClose:
    def test_after_close_same_day_returns_todays_close(self):
        result = most_recent_session_close(
            ny_instant(2026, 3, 11, 18, 0), timezone=NY, close_time=CLOSE
        )
        assert result == ny_instant(2026, 3, 11, 16, 0)

    def test_before_open_returns_previous_days_close(self):
        result = most_recent_session_close(
            ny_instant(2026, 3, 11, 7, 0), timezone=NY, close_time=CLOSE
        )
        assert result == ny_instant(2026, 3, 10, 16, 0)

    def test_saturday_returns_fridays_close(self):
        result = most_recent_session_close(
            ny_instant(2026, 3, 14, 12, 0), timezone=NY, close_time=CLOSE
        )
        assert result == ny_instant(2026, 3, 13, 16, 0)

    def test_sunday_returns_fridays_close(self):
        result = most_recent_session_close(
            ny_instant(2026, 3, 15, 12, 0), timezone=NY, close_time=CLOSE
        )
        assert result == ny_instant(2026, 3, 13, 16, 0)

    def test_monday_before_open_returns_fridays_close_not_mondays(self):
        """The trickiest boundary: Monday morning must look back across the
        whole weekend, not just one day."""
        result = most_recent_session_close(
            ny_instant(2026, 3, 16, 7, 0), timezone=NY, close_time=CLOSE
        )
        assert result == ny_instant(2026, 3, 13, 16, 0)

    def test_result_is_timezone_aware_utc(self):
        result = most_recent_session_close(
            ny_instant(2026, 3, 14, 12, 0), timezone=NY, close_time=CLOSE
        )
        assert result.tzinfo is UTC


class TestFreshnessReference:
    def test_open_market_reference_is_now(self):
        now = ny_instant(2026, 3, 11, 12, 0)
        assert freshness_reference(now, timezone=NY, open_time=OPEN, close_time=CLOSE) == now

    def test_closed_market_reference_is_the_last_close(self):
        now = ny_instant(2026, 3, 14, 12, 0)  # Saturday
        ref = freshness_reference(now, timezone=NY, open_time=OPEN, close_time=CLOSE)
        assert ref == ny_instant(2026, 3, 13, 16, 0)


class TestClassifyFreshness:
    FRESH_S = 300
    STALE_S = 900

    def _classify(self, market_timestamp: datetime, *, now: datetime) -> Freshness:
        return classify_freshness(
            market_timestamp,
            now=now,
            fresh_seconds=self.FRESH_S,
            stale_seconds=self.STALE_S,
            timezone=NY,
            open_time=OPEN,
            close_time=CLOSE,
        )

    def test_just_quoted_is_fresh(self):
        now = ny_instant(2026, 3, 11, 12, 0)
        assert self._classify(now, now=now) == Freshness.FRESH

    def test_at_the_fresh_boundary_is_still_fresh(self):
        now = ny_instant(2026, 3, 11, 12, 0)
        mt = now - timedelta(seconds=self.FRESH_S)
        assert self._classify(mt, now=now) == Freshness.FRESH

    def test_just_past_the_fresh_boundary_is_delayed(self):
        now = ny_instant(2026, 3, 11, 12, 0)
        mt = now - timedelta(seconds=self.FRESH_S + 1)
        assert self._classify(mt, now=now) == Freshness.DELAYED

    def test_at_the_stale_boundary_is_still_delayed(self):
        now = ny_instant(2026, 3, 11, 12, 0)
        mt = now - timedelta(seconds=self.STALE_S)
        assert self._classify(mt, now=now) == Freshness.DELAYED

    def test_just_past_the_stale_boundary_is_stale(self):
        now = ny_instant(2026, 3, 11, 12, 0)
        mt = now - timedelta(seconds=self.STALE_S + 1)
        assert self._classify(mt, now=now) == Freshness.STALE

    def test_very_old_quote_is_stale(self):
        now = ny_instant(2026, 3, 11, 12, 0)
        mt = now - timedelta(days=3)
        assert self._classify(mt, now=now) == Freshness.STALE

    def test_fridays_close_is_still_fresh_on_saturday(self):
        """The property that justifies the whole closed-market reference
        design: a weekend must not by itself degrade Friday's valid closing
        price."""
        friday_close = ny_instant(2026, 3, 13, 16, 0)
        saturday_now = ny_instant(2026, 3, 14, 10, 0)
        assert self._classify(friday_close, now=saturday_now) == Freshness.FRESH

    def test_fridays_close_is_still_fresh_on_sunday(self):
        friday_close = ny_instant(2026, 3, 13, 16, 0)
        sunday_now = ny_instant(2026, 3, 15, 20, 0)
        assert self._classify(friday_close, now=sunday_now) == Freshness.FRESH

    def test_thursdays_close_is_stale_by_the_time_saturday_arrives(self):
        """A quote from *before* the most recent close should still age
        normally -- only the most recent completed session gets the "still
        fresh over the weekend" treatment."""
        thursday_close = ny_instant(2026, 3, 12, 16, 0)
        saturday_now = ny_instant(2026, 3, 14, 10, 0)
        assert self._classify(thursday_close, now=saturday_now) == Freshness.STALE

    def test_monday_before_open_evaluates_against_fridays_close(self):
        friday_close = ny_instant(2026, 3, 13, 16, 0)
        monday_early = ny_instant(2026, 3, 16, 8, 0)
        assert self._classify(friday_close, now=monday_early) == Freshness.FRESH

    @pytest.mark.parametrize("dst_date", [(2026, 3, 11), (2026, 11, 4)])
    def test_holds_across_a_dst_transition_month(self, dst_date):
        """ZoneInfo handles the offset change automatically; this just
        confirms nothing here hardcodes a fixed UTC offset."""
        year, month, day = dst_date
        now = ny_instant(year, month, day, 12, 0)
        assert self._classify(now, now=now) == Freshness.FRESH
