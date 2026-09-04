"""Symbol normalisation and validation.

Pure functions: no database, no network, no configuration. This is the single
definition of what a ticker symbol is, used by the API schema, the service layer
and the worker. The database CHECK constraint mirrors the same regex as a
backstop that no code path can bypass.
"""

from __future__ import annotations

import re

# Uppercase, starts with a letter, may contain digits, dots or hyphens
# (BRK.B, RDS-A). Defined here, in the domain, and imported by the SQLAlchemy
# model for its CHECK constraint -- the domain must not depend on the
# persistence layer, and there must be exactly one definition of "symbol".
SYMBOL_REGEX = r"^[A-Z][A-Z0-9.\-]{0,9}$"

MAX_SYMBOL_LENGTH = 10

_SYMBOL_PATTERN = re.compile(SYMBOL_REGEX)


class InvalidSymbolError(ValueError):
    """Raised when a string cannot be a ticker symbol."""

    def __init__(self, raw: str, reason: str) -> None:
        self.raw = raw
        self.reason = reason
        super().__init__(f"{raw!r} is not a valid symbol: {reason}")


def normalize_symbol(raw: str) -> str:
    """Convert user input into the canonical stored form.

    Normalisation happens before validation so that "  aapl  " becomes "AAPL"
    and is accepted, rather than being rejected for cosmetic reasons the user
    cannot see. Case folding here is what makes "aapl" and "AAPL" the same
    watchlist entry -- without it the uniqueness constraint would happily store
    both, and the user would see the same company twice.
    """
    if not isinstance(raw, str):
        raise InvalidSymbolError(str(raw), "must be a string")

    symbol = raw.strip().upper()

    if not symbol:
        raise InvalidSymbolError(raw, "must not be empty")
    if len(symbol) > MAX_SYMBOL_LENGTH:
        raise InvalidSymbolError(raw, f"must be at most {MAX_SYMBOL_LENGTH} characters")
    if not _SYMBOL_PATTERN.match(symbol):
        raise InvalidSymbolError(
            raw,
            "must start with a letter and contain only letters, digits, dots or hyphens",
        )

    return symbol


def is_valid_symbol(raw: str) -> bool:
    try:
        normalize_symbol(raw)
    except InvalidSymbolError:
        return False
    return True
