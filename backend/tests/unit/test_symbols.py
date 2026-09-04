"""Symbol normalisation -- pure, no database required."""

from __future__ import annotations

import pytest

from app.domain.watchlist.symbols import (
    InvalidSymbolError,
    is_valid_symbol,
    normalize_symbol,
)


class TestNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("AAPL", "AAPL"),
            ("aapl", "AAPL"),
            ("AaPl", "AAPL"),
            ("  AAPL  ", "AAPL"),
            ("\tAAPL\n", "AAPL"),
            ("brk.b", "BRK.B"),
            ("rds-a", "RDS-A"),
            ("a", "A"),
        ],
    )
    def test_normalizes_to_canonical_form(self, raw: str, expected: str):
        assert normalize_symbol(raw) == expected

    def test_case_folding_is_what_makes_uniqueness_work(self):
        """Without this, "aapl" and "AAPL" would be two rows under the unique
        constraint and the user would see the same company twice."""
        assert normalize_symbol("aapl") == normalize_symbol("AAPL")

    def test_is_deterministic(self):
        assert len({normalize_symbol("  aapl ") for _ in range(50)}) == 1


class TestRejection:
    @pytest.mark.parametrize(
        ("raw", "reason_fragment"),
        [
            ("", "empty"),
            ("   ", "empty"),
            ("ABCDEFGHIJK", "at most 10"),
            ("1AAPL", "start with a letter"),
            (".AAPL", "start with a letter"),
            ("-AAPL", "start with a letter"),
            ("AA PL", "letters, digits"),
            ("AAPL!", "letters, digits"),
            ("AA/PL", "letters, digits"),
            ("AA_PL", "letters, digits"),
            ("<script>", "start with a letter"),
        ],
    )
    def test_invalid_input_rejected_with_a_reason(self, raw: str, reason_fragment: str):
        with pytest.raises(InvalidSymbolError) as exc:
            normalize_symbol(raw)
        assert reason_fragment in exc.value.reason

    def test_non_string_rejected(self):
        with pytest.raises(InvalidSymbolError):
            normalize_symbol(None)  # type: ignore[arg-type]

    def test_boundary_lengths(self):
        assert normalize_symbol("A" * 10) == "A" * 10
        with pytest.raises(InvalidSymbolError):
            normalize_symbol("A" * 11)

    def test_sql_injection_attempt_is_just_an_invalid_symbol(self):
        """Parameterised queries make this harmless anyway; rejecting it early
        means such input never reaches the database at all."""
        assert not is_valid_symbol("AAPL'; DROP TABLE users;--")


class TestIsValidSymbol:
    def test_returns_bool_without_raising(self):
        assert is_valid_symbol("aapl") is True
        assert is_valid_symbol("!!") is False
