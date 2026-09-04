"""Shared vocabulary for the whole system.

These live in `domain` rather than `models` because they are business concepts
that the pure change-detection and scoring code (phases 6-7) reasons about. The
database happens to persist them; it does not own them.

They are stored as VARCHAR + CHECK rather than as native PostgreSQL ENUM types.
Native enums require a migration with an exclusive lock to add a value, which is
a needless obstacle for a vocabulary expected to grow (new event types, new
freshness classes). The CHECK constraint gives the same database-level guarantee
without that cost.
"""

from __future__ import annotations

from enum import StrEnum


class Freshness(StrEnum):
    """How current an observation is. See docs/product-spec.md section 2."""

    FRESH = "FRESH"
    DELAYED = "DELAYED"
    STALE = "STALE"


class Severity(StrEnum):
    """Attention band. See docs/product-spec.md section 4.

    NORMAL is never persisted: an event below the WATCH floor is by definition
    not a meaningful change, and storing it would mean the events table no longer
    means "things worth surfacing".
    """

    NORMAL = "NORMAL"
    WATCH = "WATCH"
    IMPORTANT = "IMPORTANT"
    HIGH = "HIGH"


class EventType(StrEnum):
    """What drove the event, derived from which signals contributed points."""

    PRICE_MOVE = "PRICE_MOVE"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    PRICE_AND_VOLUME = "PRICE_AND_VOLUME"
