"""Field-level validation rules shared by Quote and Bar.

Factored out because both models enforce the same four rules -- a real price,
a real-or-absent volume, a symbol the rest of the system recognises, a named
source -- and duplicating the logic (not the boilerplate) would let the two
definitions of "valid price" quietly drift apart.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.watchlist.symbols import is_valid_symbol


def require_finite_positive(value: Decimal, *, field: str) -> Decimal:
    if not value.is_finite():
        raise ValueError(f"{field} must be a finite number")
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def require_non_negative_volume(value: int | None) -> int | None:
    if value is not None and value < 0:
        raise ValueError("volume must not be negative")
    return value


def require_valid_symbol(value: str) -> str:
    if not is_valid_symbol(value):
        raise ValueError(f"{value!r} is not a valid symbol")
    return value


def require_non_blank_source(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("source must not be blank")
    return value
