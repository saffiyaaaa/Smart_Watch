"""Change-detection signals -- pure, no database, no provider, no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.changes.detection import (
    Signals,
    average_volume,
    compute_signals,
    detect_conflict,
    is_new_observation,
    price_change_pct,
    volume_ratio,
)
from app.domain.enums import Freshness


class TestPriceChangePct:
    def test_matches_the_product_spec_worked_example(self):
        """docs/product-spec.md section 4: "Price +6.2% vs previous close
        ($180.40 -> $191.58)"."""
        pct = price_change_pct(Decimal("191.58"), Decimal("180.40"))
        assert round(pct, 1) == Decimal("6.2")

    def test_price_increase_is_positive(self):
        assert price_change_pct(Decimal("110"), Decimal("100")) == Decimal("10")

    def test_price_decrease_is_negative(self):
        assert price_change_pct(Decimal("90"), Decimal("100")) == Decimal("-10")

    def test_identical_price_is_zero_not_none(self):
        """Identical observations produce no change -- a real zero, which is
        meaningfully different from "no signal available"."""
        assert price_change_pct(Decimal("100"), Decimal("100")) == Decimal("0")

    def test_none_baseline_returns_none(self):
        """No prior session recorded (e.g. a newly listed symbol) -- the
        signal is unavailable, not zero."""
        assert price_change_pct(Decimal("100"), None) is None

    def test_zero_baseline_returns_none_defensively(self):
        """A validated system should never produce a zero baseline (Bar/
        DailyBar both reject it), but this function does not trust its
        caller blindly -- degrading beats dividing by zero."""
        assert price_change_pct(Decimal("100"), Decimal("0")) is None

    def test_negative_baseline_returns_none_defensively(self):
        assert price_change_pct(Decimal("100"), Decimal("-5")) is None

    def test_is_deterministic(self):
        results = {price_change_pct(Decimal("191.58"), Decimal("180.40")) for _ in range(100)}
        assert len(results) == 1


class TestAverageVolume:
    MIN_SESSIONS = 5

    def test_below_the_minimum_session_count_is_unavailable(self):
        volumes = [1_000_000] * (self.MIN_SESSIONS - 1)
        assert average_volume(volumes, min_sessions=self.MIN_SESSIONS) is None

    def test_exactly_the_minimum_session_count_is_available(self):
        volumes = [1_000_000] * self.MIN_SESSIONS
        assert average_volume(volumes, min_sessions=self.MIN_SESSIONS) == 1_000_000

    def test_above_the_minimum_is_available(self):
        volumes = [1_000_000] * (self.MIN_SESSIONS + 1)
        assert average_volume(volumes, min_sessions=self.MIN_SESSIONS) == 1_000_000

    def test_computes_a_real_average(self):
        volumes = [100, 200, 300, 400, 500]
        assert average_volume(volumes, min_sessions=5) == 300

    def test_none_entries_are_dropped_not_treated_as_zero(self):
        """Averaging in a None as 0 would drag the baseline down and make
        ordinary volume look artificially spiky by comparison."""
        with_none = average_volume([100, 100, 100, 100, None], min_sessions=4)
        without_none = average_volume([100, 100, 100, 100], min_sessions=4)
        assert with_none == without_none == 100

    def test_none_entries_still_count_toward_insufficient_history(self):
        """Four real values plus one None is only four real sessions, not
        five -- the None must not be silently treated as "one more session
        of data"."""
        volumes = [100, 100, 100, 100, None]
        assert average_volume(volumes, min_sessions=5) is None

    def test_empty_history_is_unavailable(self):
        assert average_volume([], min_sessions=1) is None

    def test_all_none_is_unavailable(self):
        assert average_volume([None, None, None], min_sessions=1) is None


class TestVolumeRatio:
    def test_matches_the_product_spec_worked_example(self):
        """docs/product-spec.md section 4: "Volume 3.1x the 20-day
        average"."""
        ratio = volume_ratio(3_100_000, 1_000_000)
        assert round(ratio, 1) == Decimal("3.1")

    def test_identical_volume_is_one_not_none(self):
        assert volume_ratio(1_000_000, 1_000_000) == Decimal("1")

    def test_missing_current_volume_is_unavailable(self):
        assert volume_ratio(None, 1_000_000) is None

    def test_missing_baseline_is_unavailable(self):
        """Distinct from average_volume's own None -- this is the
        composition point: whatever made the baseline unavailable, the ratio
        built on it must also be unavailable, not silently zero."""
        assert volume_ratio(1_000_000, None) is None

    def test_zero_baseline_is_unavailable_defensively(self):
        assert volume_ratio(1_000_000, 0) is None

    def test_zero_current_volume_is_a_real_ratio_of_zero(self):
        """Zero volume reported is a real fact (no trades), not a missing
        one -- distinct from current_volume=None."""
        assert volume_ratio(0, 1_000_000) == Decimal("0")

    def test_is_deterministic(self):
        results = {volume_ratio(3_100_000, 1_000_000) for _ in range(100)}
        assert len(results) == 1


class TestDetectConflict:
    TOLERANCE = Decimal("0.5")

    def test_identical_prices_do_not_conflict(self):
        assert not detect_conflict(Decimal("100"), Decimal("100"), tolerance_pct=self.TOLERANCE)

    def test_within_tolerance_does_not_conflict(self):
        assert not detect_conflict(
            Decimal("100.00"), Decimal("100.40"), tolerance_pct=self.TOLERANCE
        )

    def test_beyond_tolerance_conflicts(self):
        assert detect_conflict(Decimal("100.00"), Decimal("102.00"), tolerance_pct=self.TOLERANCE)

    def test_exactly_at_the_tolerance_boundary_does_not_conflict(self):
        """Strict inequality: the tolerance is inclusive of its own edge.
        99.75 and 100.25 average to exactly 100, and their gap of 0.50 is
        exactly 0.5% of that midpoint -- a precise boundary, not an
        approximation."""
        assert not detect_conflict(
            Decimal("99.75"), Decimal("100.25"), tolerance_pct=self.TOLERANCE
        )

    def test_just_past_the_boundary_conflicts(self):
        """Same construction, nudged by one cent past the boundary on each
        side: still averages to 100, gap is now 0.52% of the midpoint."""
        assert detect_conflict(Decimal("99.74"), Decimal("100.26"), tolerance_pct=self.TOLERANCE)

    def test_symmetric_regardless_of_argument_order(self):
        a, b = Decimal("100.00"), Decimal("102.00")
        assert detect_conflict(a, b, tolerance_pct=self.TOLERANCE) == detect_conflict(
            b, a, tolerance_pct=self.TOLERANCE
        )

    def test_zero_price_is_not_a_meaningful_comparison(self):
        assert not detect_conflict(Decimal("0"), Decimal("100"), tolerance_pct=self.TOLERANCE)

    def test_negative_price_is_not_a_meaningful_comparison(self):
        assert not detect_conflict(Decimal("-5"), Decimal("100"), tolerance_pct=self.TOLERANCE)

    def test_matches_the_conflicting_provider_scenario(self):
        """docs/product-spec.md section 2's conflict definition, exercised
        with the exact 2% divergence app/infrastructure/providers/
        mock_provider.py's ConflictingProvider produces against MockProvider,
        so the two modules' understanding of "conflicting" cannot drift
        apart unnoticed."""
        base = Decimal("180.00")
        disagreeing = base * Decimal("1.02")
        assert detect_conflict(base, disagreeing, tolerance_pct=self.TOLERANCE)


class TestIsNewObservation:
    T0 = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)

    def test_strictly_newer_is_new(self):
        assert is_new_observation(self.T0 + timedelta(seconds=1), self.T0)

    def test_equal_timestamp_is_not_new(self):
        """The same fact observed again is not new information -- this is
        what keeps re-ingesting an already-known quote from re-triggering
        detection."""
        assert not is_new_observation(self.T0, self.T0)

    def test_older_timestamp_is_not_new(self):
        """Failure-matrix row 8: an out-of-order arrival must not be treated
        as new information, even though Phase 5 still stores it as
        history."""
        assert not is_new_observation(self.T0 - timedelta(hours=1), self.T0)

    def test_no_prior_observation_means_everything_is_new(self):
        assert is_new_observation(self.T0, None)

    def test_one_microsecond_newer_is_new(self):
        """Exercises the boundary precisely: this is a strict `>`, not a
        rounded or truncated comparison."""
        assert is_new_observation(self.T0 + timedelta(microseconds=1), self.T0)


class TestComputeSignals:
    def test_assembles_all_four_signals(self):
        signals = compute_signals(
            current_price=Decimal("191.58"),
            baseline_price=Decimal("180.40"),
            current_volume=3_100_000,
            recent_volumes=[1_000_000] * 20,
            volume_baseline_min_sessions=5,
            freshness=Freshness.FRESH,
            is_conflicting=False,
        )
        assert isinstance(signals, Signals)
        assert round(signals.price_change_pct, 1) == Decimal("6.2")
        assert round(signals.volume_ratio, 1) == Decimal("3.1")
        assert signals.freshness is Freshness.FRESH
        assert signals.is_conflicting is False

    def test_insufficient_volume_history_degrades_not_crashes(self):
        """ "Missing volume does not crash scoring" (Phase 6 gate), exercised
        one layer up through the actual assembly function scoring will
        call."""
        signals = compute_signals(
            current_price=Decimal("100"),
            baseline_price=Decimal("100"),
            current_volume=1_000_000,
            recent_volumes=[1_000_000, 1_000_000],  # fewer than min_sessions
            volume_baseline_min_sessions=5,
            freshness=Freshness.FRESH,
            is_conflicting=False,
        )
        assert signals.volume_ratio is None
        assert signals.price_change_pct == Decimal("0")

    def test_missing_price_baseline_degrades_not_crashes(self):
        signals = compute_signals(
            current_price=Decimal("100"),
            baseline_price=None,
            current_volume=1_000_000,
            recent_volumes=[1_000_000] * 20,
            volume_baseline_min_sessions=5,
            freshness=Freshness.DELAYED,
            is_conflicting=False,
        )
        assert signals.price_change_pct is None
        assert signals.volume_ratio == Decimal("1")

    def test_is_frozen(self):
        signals = compute_signals(
            current_price=Decimal("100"),
            baseline_price=Decimal("100"),
            current_volume=None,
            recent_volumes=[],
            volume_baseline_min_sessions=1,
            freshness=Freshness.FRESH,
            is_conflicting=False,
        )
        with pytest.raises(AttributeError):
            signals.is_conflicting = True  # type: ignore[misc]
