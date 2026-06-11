"""
Tests for parser_engine.py — regex extraction, date parsing, fallback logic.
These run without containers or PDFs.
"""
import re
import pytest

# Import after conftest.py adds bill-parser to sys.path
from parser_engine import DATE_RE, AMOUNT_RE, INVOICE_RE, _parse_date, _fallback_extract


class TestDateRegex:
    """Test the generic date regex pattern."""

    @pytest.mark.parametrize("text,expected", [
        ("12.03.2026", "12.03.2026"),
        ("01-12-2025", "01-12-2025"),
        ("2025-01-15", "2025-01-15"),
        ("12/03/2026", "12/03/2026"),
    ])
    def test_finds_valid_dates(self, text, expected):
        matches = DATE_RE.findall(text)
        assert expected in matches

    def test_no_match_on_garbage(self):
        matches = DATE_RE.findall("no dates here at all")
        assert matches == []

    def test_finds_multiple_dates(self):
        text = "Period 01.01.2026 to 31.01.2026"
        matches = DATE_RE.findall(text)
        assert len(matches) == 2


class TestParseDateHelper:
    """Test the _parse_date function."""

    @pytest.mark.parametrize("input_str,expected", [
        ("12.03.2026", "2026-03-12"),
        ("01.01.2025", "2025-01-01"),
        ("2025-06-15", "2025-06-15"),
        ("31.12.2024", "2024-12-31"),
    ])
    def test_parses_valid_dates(self, input_str, expected):
        assert _parse_date(input_str) == expected

    def test_returns_none_on_garbage(self):
        assert _parse_date("not a date") is None

    def test_returns_none_on_empty(self):
        assert _parse_date("") is None


class TestAmountRegex:
    """Test the generic amount extraction regex."""

    @pytest.mark.parametrize("text,expected", [
        ("Kokku: 123,45", "123,45"),
        ("TOTAL 99.50", "99.50"),
        ("Summa maksta 234,00", "234,00"),
        ("Kokku:  1234,56", "1234,56"),
    ])
    def test_extracts_amounts(self, text, expected):
        m = AMOUNT_RE.search(text)
        assert m is not None, f"No match in: {text}"
        assert m.group(1) == expected

    def test_no_match_on_text_without_amount(self):
        assert AMOUNT_RE.search("no amounts here") is None


class TestInvoiceRegex:
    """Test the generic invoice number regex."""

    @pytest.mark.parametrize("text,expected", [
        ("Arve nr 12345", "12345"),
        ("Invoice No. INV-2026-001", "INV-2026-001"),
        ("Arve nr. A-1234/56", "A-1234/56"),
    ])
    def test_extracts_invoice_numbers(self, text, expected):
        m = INVOICE_RE.search(text)
        assert m is not None, f"No match in: {text}"
        assert m.group(1).strip() == expected


class TestFallbackExtract:
    """Test the _fallback_extract function that uses generic regex."""

    def test_extracts_date_and_amount(self):
        text = "Arve kuupäev: 15.03.2026\nKokku: 99,50 EUR"
        result = _fallback_extract(text)
        assert result.get("invoice_date") == "2026-03-15"
        assert result.get("total_incl_vat") == 99.50

    def test_handles_empty_text(self):
        result = _fallback_extract("")
        assert result == {}

    def test_partial_extraction(self):
        text = "Date: 01.02.2026\nNo amount here"
        result = _fallback_extract(text)
        assert result.get("invoice_date") == "2026-02-01"
        assert "total_incl_vat" not in result
