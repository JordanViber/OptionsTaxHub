from pathlib import Path

import pytest

from pdf_1099_parser import parse_robinhood_1099_pdf


PDF_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "c15f7458-e9d5-4dfb-a985-351df5a36cde.pdf"
)


@pytest.fixture(scope="module")
def robinhood_1099_bytes() -> bytes:
    return PDF_PATH.read_bytes()


def test_parse_robinhood_1099_pdf_extracts_summary_and_symbols(
    robinhood_1099_bytes: bytes,
):
    summary = parse_robinhood_1099_pdf(
        robinhood_1099_bytes,
        current_symbols={"CLSK", "TSLL", "ASST"},
        filename="2024-robinhood-1099.pdf",
        expected_previous_year=2024,
    )

    assert summary.source_filename == "2024-robinhood-1099.pdf"
    assert summary.broker_name == "Robinhood"
    assert summary.tax_year == 2024
    assert summary.short_term_proceeds == pytest.approx(281823.83)
    assert summary.short_term_cost_basis == pytest.approx(264439.89)
    assert summary.short_term_wash_sale_disallowed == pytest.approx(17409.64)
    assert summary.short_term_net_gain == pytest.approx(34793.58)
    assert summary.long_term_proceeds == pytest.approx(108.56)
    assert summary.long_term_cost_basis == pytest.approx(141.72)
    assert summary.long_term_wash_sale_disallowed == pytest.approx(33.16)
    assert "CLSK" in summary.referenced_symbols
    assert "TSLL" in summary.referenced_symbols
    assert summary.matched_symbols == ["CLSK", "TSLL"]
    assert any("expected prior tax year (2024)" in insight for insight in summary.insights)
    assert any("$17,442.80" in insight for insight in summary.insights)


def test_parse_robinhood_1099_pdf_handles_missing_totals_and_unknown_year(monkeypatch):
    monkeypatch.setattr(
        "pdf_1099_parser.extract_text_from_pdf",
        lambda _pdf_bytes: "Account summary without tax tables or symbol references.",
    )

    summary = parse_robinhood_1099_pdf(
        b"fake-pdf",
        current_symbols={"AAPL"},
        filename="empty.pdf",
        expected_previous_year=2024,
    )

    assert summary.source_filename == "empty.pdf"
    assert summary.broker_name == ""
    assert summary.tax_year is None
    assert summary.short_term_proceeds == pytest.approx(0.0)
    assert summary.long_term_wash_sale_disallowed == pytest.approx(0.0)
    assert summary.referenced_symbols == []
    assert summary.matched_symbols == []
    assert summary.insights == []


def test_parse_robinhood_1099_pdf_reports_year_mismatch_and_unmatched_symbols(monkeypatch):
    monkeypatch.setattr(
        "pdf_1099_parser.extract_text_from_pdf",
        lambda _pdf_bytes: (
            "Enclosed is your 2023 Consolidated Tax Statement\n"
            "/ Symbol: XYZ\n"
            "Robinhood"
        ),
    )

    summary = parse_robinhood_1099_pdf(
        b"fake-pdf",
        current_symbols={"AAPL"},
        filename="mismatch.pdf",
        expected_previous_year=2024,
    )

    assert summary.tax_year == 2023
    assert summary.referenced_symbols == ["XYZ"]
    assert summary.matched_symbols == []
    assert any("not the expected prior year (2024)" in insight for insight in summary.insights)
    assert any("No symbols from the prior-year 1099 directly matched the current CSV" in insight for insight in summary.insights)


def test_parse_robinhood_1099_pdf_handles_negative_net_gain(monkeypatch):
    """Parser should correctly extract negative totals (loss years)."""
    # Simulate a page where net_gain is negative (parentheses notation from PDF)
    monkeypatch.setattr(
        "pdf_1099_parser.extract_text_from_pdf",
        lambda _pdf_bytes: (
            "Enclosed is your 2023 Consolidated Tax Statement\n"
            "Robinhood\n"
            "Total Short-term 5,000.00 6,200.00 0.00 0.00 (1,200.00)\n"
            "Total Long-term 2,000.00 2,050.00 0.00 0.00 -50.00\n"
        ),
    )

    summary = parse_robinhood_1099_pdf(
        b"fake-pdf",
        current_symbols=set(),
        filename="loss-year.pdf",
        expected_previous_year=2023,
    )

    assert summary.short_term_proceeds == pytest.approx(5000.00)
    assert summary.short_term_cost_basis == pytest.approx(6200.00)
    assert summary.short_term_net_gain == pytest.approx(-1200.00)
    assert summary.long_term_proceeds == pytest.approx(2000.00)
    assert summary.long_term_cost_basis == pytest.approx(2050.00)
    assert summary.long_term_net_gain == pytest.approx(-50.00)
