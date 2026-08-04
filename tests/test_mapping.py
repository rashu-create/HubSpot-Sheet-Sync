"""Tests for mapping.py formatters and column utilities."""

import pytest

from src.mapping import (
    COLUMN_MAP,
    col_letter_to_index,
    fmt_capitalize,
    fmt_date_dmy,
    fmt_month_year,
    fmt_number,
    fmt_passthrough,
    normalize_domain,
)


# ── col_letter_to_index ───────────────────────────────────────────────────────

class TestColLetterToIndex:
    def test_single_a(self):
        assert col_letter_to_index("A") == 1

    def test_single_z(self):
        assert col_letter_to_index("Z") == 26

    def test_aa(self):
        assert col_letter_to_index("AA") == 27

    def test_ab(self):
        assert col_letter_to_index("AB") == 28

    def test_af(self):
        assert col_letter_to_index("AF") == 32

    def test_ak(self):
        assert col_letter_to_index("AK") == 37

    def test_lowercase_handled(self):
        """Lowercase input should work the same as uppercase."""
        assert col_letter_to_index("a") == col_letter_to_index("A")
        assert col_letter_to_index("af") == col_letter_to_index("AF")

    def test_b(self):
        assert col_letter_to_index("B") == 2

    def test_c(self):
        assert col_letter_to_index("C") == 3


# ── fmt_date_dmy ──────────────────────────────────────────────────────────────

class TestDateDmyFormatter:
    def test_iso_with_ms_and_z(self):
        assert fmt_date_dmy("2026-07-17T00:00:00.000Z") == "17 Jul 2026"

    def test_iso_without_ms(self):
        assert fmt_date_dmy("2026-07-17T12:30:00Z") == "17 Jul 2026"

    def test_date_only(self):
        assert fmt_date_dmy("2026-01-01") == "1 Jan 2026"

    def test_none_returns_empty(self):
        assert fmt_date_dmy(None) == ""

    def test_empty_string_returns_empty(self):
        assert fmt_date_dmy("") == ""

    def test_december(self):
        assert fmt_date_dmy("2026-12-25T00:00:00Z") == "25 Dec 2026"

    def test_day_without_leading_zero(self):
        """Single-digit day should not have leading zero."""
        result = fmt_date_dmy("2026-08-03T00:00:00Z")
        assert result == "3 Aug 2026"

    def test_plus_00_offset(self):
        assert fmt_date_dmy("2026-07-17T00:00:00+00:00") == "17 Jul 2026"


# ── fmt_month_year ────────────────────────────────────────────────────────────

class TestMonthYearFormatter:
    def test_august(self):
        assert fmt_month_year("2026-08-01T00:00:00Z") == "Aug 2026"

    def test_january(self):
        assert fmt_month_year("2026-01-15T00:00:00Z") == "Jan 2026"

    def test_none_returns_empty(self):
        assert fmt_month_year(None) == ""

    def test_empty_returns_empty(self):
        assert fmt_month_year("") == ""

    def test_december_2025(self):
        assert fmt_month_year("2025-12-31T23:59:59Z") == "Dec 2025"


# ── fmt_capitalize ────────────────────────────────────────────────────────────

class TestCapitalizeFormatter:
    def test_low(self):
        assert fmt_capitalize("low") == "Low"

    def test_medium(self):
        assert fmt_capitalize("medium") == "Medium"

    def test_high(self):
        assert fmt_capitalize("high") == "High"

    def test_already_capitalised(self):
        assert fmt_capitalize("High") == "High"

    def test_none_returns_empty(self):
        assert fmt_capitalize(None) == ""

    def test_empty_returns_empty(self):
        assert fmt_capitalize("") == ""

    def test_strips_whitespace(self):
        # passthrough strips all surrounding whitespace before capitalising
        assert fmt_capitalize("  high  ") == "High"

    def test_all_caps_unchanged_beyond_first(self):
        """Only first char is capitalised; rest is untouched."""
        assert fmt_capitalize("HIGH") == "HIGH"


# ── fmt_passthrough ───────────────────────────────────────────────────────────

class TestPassthroughFormatter:
    def test_none_returns_empty(self):
        assert fmt_passthrough(None) == ""

    def test_strips_whitespace(self):
        assert fmt_passthrough("  hello  ") == "hello"

    def test_value_unchanged(self):
        assert fmt_passthrough("Yes") == "Yes"


# ── fmt_number ────────────────────────────────────────────────────────────────

class TestNumberFormatter:
    def test_valid_integer_string(self):
        assert fmt_number("100") == "100"

    def test_valid_float_string(self):
        assert fmt_number("1500000.50") == "1500000.50"

    def test_none_returns_empty(self):
        assert fmt_number(None) == ""

    def test_empty_returns_empty(self):
        assert fmt_number("") == ""


# ── normalize_domain ──────────────────────────────────────────────────────────

class TestNormalizeDomain:
    def test_strips_https(self):
        assert normalize_domain("https://example.com") == "example.com"

    def test_strips_http(self):
        assert normalize_domain("http://example.com") == "example.com"

    def test_strips_www(self):
        assert normalize_domain("www.example.com") == "example.com"

    def test_strips_https_www(self):
        assert normalize_domain("https://www.example.com") == "example.com"

    def test_lowercases(self):
        assert normalize_domain("EXAMPLE.COM") == "example.com"

    def test_strips_trailing_slash(self):
        assert normalize_domain("example.com/") == "example.com"


# ── write range must never target column A ────────────────────────────────────

class TestWriteRangeNeverColA:
    def test_column_map_never_writes_a(self):
        """No entry in COLUMN_MAP should target column A."""
        col_letters = [entry[0] for entry in COLUMN_MAP]
        assert "A" not in col_letters, (
            "COLUMN_MAP must never write to column A — it is the INPUT domain column"
        )

    def test_col_letter_to_index_a_is_1(self):
        """Column A maps to index 1 — confirm the guard logic can detect it."""
        assert col_letter_to_index("A") == 1

    def test_all_mapped_cols_are_not_a(self):
        """Every mapped column index should be > 1 (i.e., not column A)."""
        for col_letter, *_ in COLUMN_MAP:
            idx = col_letter_to_index(col_letter)
            assert idx > 1, f"Column {col_letter!r} maps to index {idx} — that is column A!"
