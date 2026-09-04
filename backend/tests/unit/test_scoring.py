"""Attention scoring -- pure, no database, no config, no network."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.changes.detection import Signals
from app.domain.changes.scoring import (
    ScoringConfig,
    build_evidence,
    classify_event_type,
    classify_severity,
    confidence_multiplier,
    price_points,
    score_change,
    volume_points,
)
from app.domain.enums import EventType, Freshness, Severity


def default_config(**overrides) -> ScoringConfig:
    """Explicit defaults for tests, matching docs/product-spec.md section 4
    -- never imported from app.config, so a real settings change cannot
    silently move what these tests believe they are checking."""
    base = {
        "price_min_pct": Decimal("1.0"),
        "price_max_pct": Decimal("8.0"),
        "price_max_points": Decimal("55"),
        "volume_min_ratio": Decimal("1.5"),
        "volume_max_ratio": Decimal("5.0"),
        "volume_max_points": Decimal("45"),
        "confidence_delayed": Decimal("0.85"),
        "confidence_stale": Decimal("0.60"),
        "confidence_conflicting": Decimal("0.50"),
        "confidence_no_volume_baseline": Decimal("0.85"),
        "severity_watch_min": 20,
        "severity_important_min": 50,
        "severity_high_min": 75,
        "volume_baseline_sessions": 20,
    }
    return ScoringConfig(**{**base, **overrides})


def make_signals(**overrides) -> Signals:
    base = {
        "price_change_pct": None,
        "volume_ratio": None,
        "freshness": Freshness.FRESH,
        "is_conflicting": False,
    }
    return Signals(**{**base, **overrides})


class TestPricePoints:
    CFG = default_config()

    def test_below_the_floor_is_zero(self):
        assert (
            price_points(
                Decimal("0.9"),
                min_pct=self.CFG.price_min_pct,
                max_pct=self.CFG.price_max_pct,
                max_points=self.CFG.price_max_points,
            )
            == 0
        )

    def test_at_the_floor_is_zero(self):
        """The floor itself contributes nothing -- points begin strictly
        past it."""
        assert (
            price_points(
                Decimal("1.0"),
                min_pct=self.CFG.price_min_pct,
                max_pct=self.CFG.price_max_pct,
                max_points=self.CFG.price_max_points,
            )
            == 0
        )

    def test_just_past_the_floor_is_a_small_positive_value(self):
        pts = price_points(
            Decimal("1.1"),
            min_pct=self.CFG.price_min_pct,
            max_pct=self.CFG.price_max_pct,
            max_points=self.CFG.price_max_points,
        )
        assert Decimal("0") < pts < Decimal("2")

    def test_at_the_ceiling_is_max_points(self):
        pts = price_points(
            Decimal("8.0"),
            min_pct=self.CFG.price_min_pct,
            max_pct=self.CFG.price_max_pct,
            max_points=self.CFG.price_max_points,
        )
        assert pts == Decimal("55")

    def test_beyond_the_ceiling_is_clamped_not_extrapolated(self):
        pts = price_points(
            Decimal("50.0"),
            min_pct=self.CFG.price_min_pct,
            max_pct=self.CFG.price_max_pct,
            max_points=self.CFG.price_max_points,
        )
        assert pts == Decimal("55")

    def test_direction_does_not_matter(self):
        """A -8% move is exactly as attention-worthy as +8%."""
        up = price_points(
            Decimal("8.0"), min_pct=Decimal("1"), max_pct=Decimal("8"), max_points=Decimal("55")
        )
        down = price_points(
            Decimal("-8.0"), min_pct=Decimal("1"), max_pct=Decimal("8"), max_points=Decimal("55")
        )
        assert up == down

    def test_none_is_zero(self):
        assert (
            price_points(
                None,
                min_pct=self.CFG.price_min_pct,
                max_pct=self.CFG.price_max_pct,
                max_points=self.CFG.price_max_points,
            )
            == 0
        )


class TestVolumePoints:
    CFG = default_config()

    def test_below_the_floor_is_zero(self):
        assert (
            volume_points(
                Decimal("1.4"),
                min_ratio=self.CFG.volume_min_ratio,
                max_ratio=self.CFG.volume_max_ratio,
                max_points=self.CFG.volume_max_points,
            )
            == 0
        )

    def test_at_the_floor_is_zero(self):
        assert (
            volume_points(
                Decimal("1.5"),
                min_ratio=self.CFG.volume_min_ratio,
                max_ratio=self.CFG.volume_max_ratio,
                max_points=self.CFG.volume_max_points,
            )
            == 0
        )

    def test_at_the_ceiling_is_max_points(self):
        pts = volume_points(
            Decimal("5.0"),
            min_ratio=self.CFG.volume_min_ratio,
            max_ratio=self.CFG.volume_max_ratio,
            max_points=self.CFG.volume_max_points,
        )
        assert pts == Decimal("45")

    def test_beyond_the_ceiling_is_clamped(self):
        pts = volume_points(
            Decimal("100"),
            min_ratio=self.CFG.volume_min_ratio,
            max_ratio=self.CFG.volume_max_ratio,
            max_points=self.CFG.volume_max_points,
        )
        assert pts == Decimal("45")

    def test_none_is_zero(self):
        assert (
            volume_points(
                None,
                min_ratio=self.CFG.volume_min_ratio,
                max_ratio=self.CFG.volume_max_ratio,
                max_points=self.CFG.volume_max_points,
            )
            == 0
        )


class TestConfidenceMultiplier:
    CFG = default_config()

    def test_fresh_and_clean_is_full_confidence(self):
        signals = make_signals(freshness=Freshness.FRESH, volume_ratio=Decimal("2"))
        assert confidence_multiplier(signals, config=self.CFG) == Decimal("1")

    def test_delayed_applies_its_multiplier(self):
        signals = make_signals(freshness=Freshness.DELAYED, volume_ratio=Decimal("2"))
        assert confidence_multiplier(signals, config=self.CFG) == Decimal("0.85")

    def test_stale_applies_its_multiplier(self):
        signals = make_signals(freshness=Freshness.STALE, volume_ratio=Decimal("2"))
        assert confidence_multiplier(signals, config=self.CFG) == Decimal("0.60")

    def test_conflicting_applies_its_multiplier(self):
        signals = make_signals(is_conflicting=True, volume_ratio=Decimal("2"))
        assert confidence_multiplier(signals, config=self.CFG) == Decimal("0.50")

    def test_missing_volume_baseline_applies_its_multiplier(self):
        signals = make_signals(volume_ratio=None)
        assert confidence_multiplier(signals, config=self.CFG) == Decimal("0.85")

    def test_multipliers_compose_by_multiplication(self):
        """docs/product-spec.md section 4's STALE+CONFLICT case: 0.60 x 0.50,
        not the minimum or the sum of the two."""
        signals = make_signals(
            freshness=Freshness.STALE, is_conflicting=True, volume_ratio=Decimal("2")
        )
        assert confidence_multiplier(signals, config=self.CFG) == Decimal("0.30")

    def test_all_four_degradations_compose(self):
        signals = make_signals(freshness=Freshness.STALE, is_conflicting=True, volume_ratio=None)
        expected = Decimal("0.60") * Decimal("0.50") * Decimal("0.85")
        assert confidence_multiplier(signals, config=self.CFG) == expected

    def test_delayed_and_stale_are_mutually_exclusive(self):
        """A quote has one freshness classification; the elif in the
        implementation must not accidentally apply both multipliers."""
        stale_signals = make_signals(freshness=Freshness.STALE, volume_ratio=Decimal("2"))
        assert confidence_multiplier(stale_signals, config=self.CFG) == Decimal("0.60")


class TestClassifySeverity:
    CFG = default_config()

    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0, Severity.NORMAL),
            (19, Severity.NORMAL),
            (20, Severity.WATCH),
            (21, Severity.WATCH),
            (49, Severity.WATCH),
            (50, Severity.IMPORTANT),
            (51, Severity.IMPORTANT),
            (74, Severity.IMPORTANT),
            (75, Severity.HIGH),
            (76, Severity.HIGH),
            (100, Severity.HIGH),
        ],
    )
    def test_every_band_boundary(self, score: int, expected: Severity):
        assert classify_severity(score, config=self.CFG) is expected


class TestClassifyEventType:
    def test_price_only(self):
        assert classify_event_type(Decimal("10"), Decimal("0")) is EventType.PRICE_MOVE

    def test_volume_only(self):
        assert classify_event_type(Decimal("0"), Decimal("10")) is EventType.VOLUME_SPIKE

    def test_both(self):
        assert classify_event_type(Decimal("10"), Decimal("10")) is EventType.PRICE_AND_VOLUME

    def test_neither_defaults_to_price_move(self):
        """Not user-facing -- a zero-zero result is NORMAL severity and never
        persisted -- but the function must still return something sane
        rather than raising."""
        assert classify_event_type(Decimal("0"), Decimal("0")) is EventType.PRICE_MOVE


class TestBuildEvidence:
    CFG = default_config()

    def test_price_signal_produces_a_numbered_explanation(self):
        signals = make_signals(price_change_pct=Decimal("6.2"))
        evidence = build_evidence(
            signals,
            config=self.CFG,
            current_price=Decimal("191.58"),
            baseline_price=Decimal("180.40"),
            price_pts=Decimal("36"),
            volume_pts=Decimal("0"),
            freshness_age_minutes=None,
        )
        assert any("6.2%" in e and "180.40" in e and "191.58" in e for e in evidence)

    def test_price_below_threshold_is_not_mentioned(self):
        """A real but non-contributing signal should not clutter the
        explanation -- price_pts=0 signals it did not drive the score."""
        signals = make_signals(price_change_pct=Decimal("0.5"))
        evidence = build_evidence(
            signals,
            config=self.CFG,
            current_price=Decimal("100.50"),
            baseline_price=Decimal("100.00"),
            price_pts=Decimal("0"),
            volume_pts=Decimal("0"),
            freshness_age_minutes=None,
        )
        assert not any("Price" in e for e in evidence)

    def test_volume_signal_produces_a_numbered_explanation(self):
        signals = make_signals(volume_ratio=Decimal("3.1"))
        evidence = build_evidence(
            signals,
            config=self.CFG,
            current_price=Decimal("100"),
            baseline_price=Decimal("100"),
            price_pts=Decimal("0"),
            volume_pts=Decimal("20"),
            freshness_age_minutes=None,
        )
        assert any("3.1×" in e and "20-day" in e for e in evidence)

    def test_stale_mentions_exact_age_when_available(self):
        signals = make_signals(freshness=Freshness.STALE, volume_ratio=Decimal("2"))
        evidence = build_evidence(
            signals,
            config=self.CFG,
            current_price=Decimal("100"),
            baseline_price=Decimal("100"),
            price_pts=Decimal("0"),
            volume_pts=Decimal("0"),
            freshness_age_minutes=42,
        )
        assert any("42 minutes old" in e and "60%" in e for e in evidence)

    def test_stale_falls_back_to_a_generic_phrase_without_an_age(self):
        signals = make_signals(freshness=Freshness.STALE, volume_ratio=Decimal("2"))
        evidence = build_evidence(
            signals,
            config=self.CFG,
            current_price=Decimal("100"),
            baseline_price=Decimal("100"),
            price_pts=Decimal("0"),
            volume_pts=Decimal("0"),
            freshness_age_minutes=None,
        )
        assert any("stale" in e.lower() and "60%" in e for e in evidence)

    def test_conflict_is_mentioned(self):
        signals = make_signals(is_conflicting=True, volume_ratio=Decimal("2"))
        evidence = build_evidence(
            signals,
            config=self.CFG,
            current_price=Decimal("100"),
            baseline_price=Decimal("100"),
            price_pts=Decimal("0"),
            volume_pts=Decimal("0"),
            freshness_age_minutes=None,
        )
        assert any("disagree" in e.lower() and "50%" in e for e in evidence)

    def test_missing_volume_baseline_is_mentioned(self):
        signals = make_signals(volume_ratio=None)
        evidence = build_evidence(
            signals,
            config=self.CFG,
            current_price=Decimal("100"),
            baseline_price=Decimal("100"),
            price_pts=Decimal("0"),
            volume_pts=Decimal("0"),
            freshness_age_minutes=None,
        )
        assert any("volume baseline" in e.lower() for e in evidence)

    def test_no_signals_no_evidence(self):
        signals = make_signals(volume_ratio=Decimal("2"))  # fresh, clean, no move
        evidence = build_evidence(
            signals,
            config=self.CFG,
            current_price=Decimal("100"),
            baseline_price=Decimal("100"),
            price_pts=Decimal("0"),
            volume_pts=Decimal("0"),
            freshness_age_minutes=None,
        )
        assert evidence == []


class TestScoreChangeCalibrationTable:
    """The six worked examples from docs/product-spec.md section 4,
    reproduced exactly through the real scoring function -- not just
    hand-verified arithmetic, but this module's actual code path."""

    CFG = default_config()

    def _score(self, pct, ratio, freshness=Freshness.FRESH) -> int:
        signals = make_signals(price_change_pct=pct, volume_ratio=ratio, freshness=freshness)
        return score_change(
            signals, config=self.CFG, current_price=Decimal("100"), baseline_price=Decimal("100")
        ).score

    def test_row_1_below_floor_is_not_surfaced(self):
        assert self._score(Decimal("0.8"), Decimal("1.0")) == 0

    def test_row_2_watch(self):
        assert self._score(Decimal("3.5"), Decimal("1.0")) == 20

    def test_row_3_watch(self):
        assert self._score(Decimal("2.0"), Decimal("3.0")) == 27

    def test_row_4_watch(self):
        assert self._score(Decimal("6.0"), Decimal("2.0")) == 46

    def test_row_5_high(self):
        assert self._score(Decimal("8.0"), Decimal("3.6")) == 82

    def test_row_6_stale_caps_the_same_move_at_watch(self):
        """Identical market movement to row 5; only data quality differs.
        The whole point of the design lives in this one test."""
        assert self._score(Decimal("8.0"), Decimal("3.6"), freshness=Freshness.STALE) == 49


class TestLoadBearingInvariant:
    """docs/product-spec.md section 4: untrustworthy data must not be able to
    produce a HIGH-severity alert. app/config.py's test_config.py proves the
    configured *numbers* satisfy this algebraically; this proves score_change
    *itself* honours it end-to-end, against the worst-case input the formula
    can produce.
    """

    CFG = default_config()
    WORST_PCT = Decimal("50")  # far past the ceiling, to force max price points
    WORST_RATIO = Decimal("50")  # far past the ceiling, to force max volume points

    def _worst_case_score(self, **signal_overrides) -> int:
        signals = make_signals(
            price_change_pct=self.WORST_PCT, volume_ratio=self.WORST_RATIO, **signal_overrides
        )
        return score_change(
            signals, config=self.CFG, current_price=Decimal("100"), baseline_price=Decimal("100")
        ).score

    def test_worst_case_raw_score_is_100(self):
        """Confirms the premise before testing what degrades it."""
        assert self._worst_case_score() == 100

    def test_stale_worst_case_stays_below_high(self):
        assert self._worst_case_score(freshness=Freshness.STALE) < self.CFG.severity_high_min

    def test_conflicting_worst_case_stays_below_high(self):
        assert self._worst_case_score(is_conflicting=True) < self.CFG.severity_high_min

    def test_stale_and_conflicting_together_stays_well_below_high(self):
        assert (
            self._worst_case_score(freshness=Freshness.STALE, is_conflicting=True)
            < self.CFG.severity_high_min
        )

    def test_delayed_worst_case_may_still_reach_high_by_design(self):
        """Not a violation -- see docs/product-spec.md section 4. DELAYED
        shades confidence, it does not veto: suppressing a genuine 8% move
        over a 10-minute-old quote would be the more expensive error."""
        assert self._worst_case_score(freshness=Freshness.DELAYED) >= self.CFG.severity_high_min

    def test_every_important_or_high_result_has_evidence(self):
        """The other half of the Phase 7 gate: a severity worth surfacing
        must always come with a reason."""
        for freshness in (Freshness.FRESH, Freshness.DELAYED, Freshness.STALE):
            for conflicting in (False, True):
                result = score_change(
                    make_signals(
                        price_change_pct=self.WORST_PCT,
                        volume_ratio=self.WORST_RATIO,
                        freshness=freshness,
                        is_conflicting=conflicting,
                    ),
                    config=self.CFG,
                    current_price=Decimal("191.58"),
                    baseline_price=Decimal("180.40"),
                )
                if result.severity in (Severity.IMPORTANT, Severity.HIGH):
                    assert len(result.evidence) > 0, (
                        f"{result.severity} with no evidence "
                        f"(freshness={freshness}, conflicting={conflicting})"
                    )


class TestDeterminism:
    def test_identical_inputs_produce_identical_results_100_times(self):
        cfg = default_config()
        signals = make_signals(
            price_change_pct=Decimal("6.2"), volume_ratio=Decimal("3.1"), is_conflicting=False
        )
        results = {
            score_change(
                signals,
                config=cfg,
                current_price=Decimal("191.58"),
                baseline_price=Decimal("180.40"),
            )
            for _ in range(100)
        }
        assert len(results) == 1


class TestThresholdChangesAreRespected:
    """Threshold changes are covered by tests -- the Phase 7 gate item, shown
    directly: moving a threshold changes the outcome in the expected
    direction, proving the values in ScoringConfig are actually load-bearing
    and not hardcoded past them."""

    def test_raising_price_min_pct_suppresses_a_previously_surfaced_move(self):
        cfg_default = default_config()
        cfg_stricter = default_config(price_min_pct=Decimal("5.0"))
        signals = make_signals(price_change_pct=Decimal("3.5"))

        was_surfaced = (
            score_change(
                signals,
                config=cfg_default,
                current_price=Decimal("100"),
                baseline_price=Decimal("100"),
            ).score
            > 0
        )
        now_suppressed = (
            score_change(
                signals,
                config=cfg_stricter,
                current_price=Decimal("100"),
                baseline_price=Decimal("100"),
            ).score
            == 0
        )
        assert was_surfaced
        assert now_suppressed

    def test_lowering_severity_high_min_reclassifies_the_same_score(self):
        cfg = default_config(severity_high_min=40)
        assert classify_severity(45, config=cfg) is Severity.HIGH

    def test_config_rejects_nothing_itself_but_score_change_honours_whatever_it_is_given(self):
        """ScoringConfig is a plain data holder with no validation of its
        own -- app.config.Settings owns the invariant checks (Phase 1).
        This just confirms score_change applies whatever it receives rather
        than silently falling back to different numbers."""
        lenient = default_config(confidence_stale=Decimal("1.0"))
        signals = make_signals(
            price_change_pct=Decimal("50"), volume_ratio=Decimal("50"), freshness=Freshness.STALE
        )
        result = score_change(
            signals, config=lenient, current_price=Decimal("100"), baseline_price=Decimal("100")
        )
        assert result.score == 100  # no discount applied, because none was configured
