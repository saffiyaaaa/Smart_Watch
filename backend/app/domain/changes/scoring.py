"""Attention scoring: converts Phase 6's signals into a score, a severity,
and the evidence that justifies both.

Pure, like detection.py -- zero imports from the database, provider, or
config layers. ScoringConfig mirrors the relevant fields of app.config.Settings
but is not that class: it takes Decimal values (never float), because this is
exactly the computation docs/product-spec.md section 2 singled out precision
for -- a threshold comparison deciding whether a user gets alerted must not
carry binary-float rounding error. Converting Settings' floats into Decimal is
the job of whatever wires this into the ingestion pipeline, not this module's.

See docs/product-spec.md section 4 for the formula and the worked examples
this module is verified against.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.domain.changes.detection import Signals
from app.domain.enums import EventType, Freshness, Severity

_ZERO = Decimal(0)
_ONE = Decimal(1)
_HUNDRED = Decimal(100)


def _clamp_unit(value: Decimal) -> Decimal:
    """Clamp to [0, 1]. Used to turn "how far past the floor, as a fraction
    of the floor-to-ceiling span" into a value safe to multiply by a points
    budget, even when the input signal sits beyond the ceiling."""
    return max(_ZERO, min(_ONE, value))


@dataclass(frozen=True)
class ScoringConfig:
    """Every number the scorer needs, with no defaults.

    Deliberately has none: a threshold silently inherited from a default
    could mean a test believes it is checking a boundary at 8.0% when a
    future edit to this file's defaults quietly moved it to 7.5%. Requiring
    every field forces each test (and the eventual production adapter from
    Settings) to state the value it is actually relying on.
    """

    price_min_pct: Decimal
    price_max_pct: Decimal
    price_max_points: Decimal
    volume_min_ratio: Decimal
    volume_max_ratio: Decimal
    volume_max_points: Decimal
    confidence_delayed: Decimal
    confidence_stale: Decimal
    confidence_conflicting: Decimal
    confidence_no_volume_baseline: Decimal
    severity_watch_min: int
    severity_important_min: int
    severity_high_min: int
    volume_baseline_sessions: int


def price_points(
    pct: Decimal | None, *, min_pct: Decimal, max_pct: Decimal, max_points: Decimal
) -> Decimal:
    """0 below `min_pct` (not yet meaningful), scaling linearly to
    `max_points` at `max_pct`, clamped there beyond it. Direction does not
    matter -- a -8% move is exactly as attention-worthy as +8%."""
    if pct is None:
        return _ZERO
    magnitude = abs(pct)
    if magnitude < min_pct:
        return _ZERO
    return max_points * _clamp_unit((magnitude - min_pct) / (max_pct - min_pct))


def volume_points(
    ratio: Decimal | None, *, min_ratio: Decimal, max_ratio: Decimal, max_points: Decimal
) -> Decimal:
    """Same shape as price_points, for volume relative to its baseline."""
    if ratio is None:
        return _ZERO
    if ratio < min_ratio:
        return _ZERO
    return max_points * _clamp_unit((ratio - min_ratio) / (max_ratio - min_ratio))


def confidence_multiplier(signals: Signals, *, config: ScoringConfig) -> Decimal:
    """How much to discount the raw score for data-quality problems.

    Multipliers compose (multiply, not replace), so data that is both stale
    and conflicting is discounted more than either alone -- see
    docs/product-spec.md section 4's "STALE+CONFLICT" case. A missing volume
    baseline uses the same discount regardless of *why* volume_ratio is None
    (no current volume reported, or an insufficient session history):
    Signals collapses both into one None because from a scoring perspective
    the operative fact is identical -- "this signal cannot be trusted right
    now" -- not which upstream cause produced it.
    """
    multiplier = _ONE
    if signals.freshness is Freshness.DELAYED:
        multiplier *= config.confidence_delayed
    elif signals.freshness is Freshness.STALE:
        multiplier *= config.confidence_stale
    if signals.is_conflicting:
        multiplier *= config.confidence_conflicting
    if signals.volume_ratio is None:
        multiplier *= config.confidence_no_volume_baseline
    return multiplier


def classify_severity(score: int, *, config: ScoringConfig) -> Severity:
    """Band lookup. NORMAL is a real, reachable outcome of this function --
    it is the caller's job (the ingestion pipeline, not this module) to
    decide not to persist it, matching change_events' CHECK constraint that
    excludes NORMAL entirely."""
    if score >= config.severity_high_min:
        return Severity.HIGH
    if score >= config.severity_important_min:
        return Severity.IMPORTANT
    if score >= config.severity_watch_min:
        return Severity.WATCH
    return Severity.NORMAL


def classify_event_type(price_pts: Decimal, volume_pts: Decimal) -> EventType:
    """Which signal(s) actually drove the score, for the persisted event's
    event_type column."""
    if price_pts > 0 and volume_pts > 0:
        return EventType.PRICE_AND_VOLUME
    if volume_pts > 0:
        return EventType.VOLUME_SPIKE
    return EventType.PRICE_MOVE


def _round_and_clamp(value: Decimal) -> int:
    """0.5 rounds up, explicitly -- Decimal's ambient rounding context is not
    relied on, so this function's behaviour does not depend on where it is
    called from. Clamped to [0, 100] as a final safety net; the formula
    should never overshoot given clamped points and a multiplier <= 1, but a
    misconfigured multiplier > 1 must not be allowed to escape the band a
    reader of change_events.score expects."""
    rounded = int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return max(0, min(100, rounded))


def _age_phrase(freshness_age_minutes: int | None, *, fallback: str) -> str:
    if freshness_age_minutes is None:
        return fallback
    return f"{freshness_age_minutes} minutes old"


def build_evidence(
    signals: Signals,
    *,
    config: ScoringConfig,
    current_price: Decimal,
    baseline_price: Decimal | None,
    price_pts: Decimal,
    volume_pts: Decimal,
    freshness_age_minutes: int | None,
) -> list[str]:
    """Human-readable reasons, generated from the same values that produced
    the score -- so the explanation a user sees can never drift from the
    computation that justified it.

    Only lists a signal when it actually contributed points: a price move
    below min_pct is real but not why this event was flagged, and mentioning
    it would answer a question the user did not ask.
    """
    evidence: list[str] = []

    if price_pts > 0 and signals.price_change_pct is not None and baseline_price is not None:
        pct = signals.price_change_pct
        sign = "+" if pct >= 0 else ""
        evidence.append(
            f"Price {sign}{pct:.1f}% vs previous close "
            f"(${baseline_price:.2f} → ${current_price:.2f})"
        )

    if volume_pts > 0 and signals.volume_ratio is not None:
        evidence.append(
            f"Volume {signals.volume_ratio:.1f}× the {config.volume_baseline_sessions}-day average"
        )

    if signals.freshness is Freshness.DELAYED:
        pct = int(config.confidence_delayed * _HUNDRED)
        age = _age_phrase(freshness_age_minutes, fallback="delayed")
        evidence.append(f"Quote is {age} — confidence reduced to {pct}%")
    elif signals.freshness is Freshness.STALE:
        pct = int(config.confidence_stale * _HUNDRED)
        age = _age_phrase(freshness_age_minutes, fallback="stale")
        evidence.append(f"Quote is {age} — confidence reduced to {pct}%")

    if signals.is_conflicting:
        pct = int(config.confidence_conflicting * _HUNDRED)
        evidence.append(f"Sources disagree on this price — confidence reduced to {pct}%")

    if signals.volume_ratio is None:
        pct = int(config.confidence_no_volume_baseline * _HUNDRED)
        evidence.append(f"Volume baseline unavailable — confidence reduced to {pct}%")

    return evidence


@dataclass(frozen=True)
class AttentionResult:
    score: int
    severity: Severity
    event_type: EventType
    confidence: Decimal
    evidence: tuple[str, ...]


def score_change(
    signals: Signals,
    *,
    config: ScoringConfig,
    current_price: Decimal,
    baseline_price: Decimal | None,
    freshness_age_minutes: int | None = None,
) -> AttentionResult:
    """The single entry point. Every call site -- the worker, and only the
    worker -- should use this function and nothing lower-level; an API route
    computing its own score or severity would be exactly the "hidden scoring
    logic" the Phase 7 gate rules out.
    """
    p_pts = price_points(
        signals.price_change_pct,
        min_pct=config.price_min_pct,
        max_pct=config.price_max_pct,
        max_points=config.price_max_points,
    )
    v_pts = volume_points(
        signals.volume_ratio,
        min_ratio=config.volume_min_ratio,
        max_ratio=config.volume_max_ratio,
        max_points=config.volume_max_points,
    )
    confidence = confidence_multiplier(signals, config=config)
    score = _round_and_clamp((p_pts + v_pts) * confidence)
    severity = classify_severity(score, config=config)
    evidence = build_evidence(
        signals,
        config=config,
        current_price=current_price,
        baseline_price=baseline_price,
        price_pts=p_pts,
        volume_pts=v_pts,
        freshness_age_minutes=freshness_age_minutes,
    )

    return AttentionResult(
        score=score,
        severity=severity,
        event_type=classify_event_type(p_pts, v_pts),
        confidence=confidence,
        evidence=tuple(evidence),
    )
