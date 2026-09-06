from pathlib import Path

import pytest

from datetime import date

from pdf_1099_parser import (
    extract_1099b_lots,
    extract_text_from_pdf,
    parse_robinhood_1099_pdf,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PDF_PATH = REPO_ROOT / "docs" / "c15f7458-e9d5-4dfb-a985-351df5a36cde.pdf"
SAMPLE_2026_PDF_PATH = (
    REPO_ROOT / "client" / "public" / "sample-robinhood-1099-2026.pdf"
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

    matchable = [lot for lot in summary.lots if not lot.is_aggregate]
    assert matchable, "expected 1099-B lots from the 2024 fixture"
    first = matchable[0]
    assert "BITFARMS" in (first.description or "").upper()
    assert first.cusip == "09173B107"
    assert first.date_sold == date(2024, 1, 2)
    assert first.quantity == pytest.approx(88.757)
    assert first.proceeds == pytest.approx(294.66)
    assert first.wash_sale_disallowed == pytest.approx(1.30)
    assert all(lot.proceeds != pytest.approx(8903.04) for lot in matchable)

    option_lots = [
        lot
        for lot in matchable
        if not lot.cusip and ("CALL" in (lot.description or "").upper() or "PUT" in (lot.description or "").upper())
    ]
    assert option_lots, "option 1099-B headers with blank CUSIP should still parse"
    assert any(lot.symbol == "BTDR" for lot in option_lots)
    assert any(lot.symbol == "CLSK" for lot in option_lots)


def test_option_security_header_with_blank_cusip_parses_option_sale():
    text = (
        "Proceeds from Broker and Barter Exchange Transactions\n"
        "SHORT TERM TRANSACTIONS FOR COVERED TAX LOTS\n"
        "Report on Form 8949, Part I with Box A checked.\n"
        "BTDR 12/20/2024 CALL $10.00 / CUSIP:   / Symbol: BTDR 12/20/24 C 10.000\n"
        "07/15/24 1.000 399.94 06/24/24 300.03 ... 99.91 Option sale\n"
        "Security total: 399.94 300.03 0.00 99.91\n"
    )
    lots = extract_1099b_lots(text)
    assert len(lots) == 1
    lot = lots[0]
    assert lot.symbol == "BTDR"
    assert lot.cusip == ""
    assert "CALL" in lot.description.upper()
    assert lot.quantity == pytest.approx(1.0)
    assert lot.proceeds == pytest.approx(399.94)
    assert lot.cost_basis == pytest.approx(300.03)
    assert lot.additional_info.lower() == "option sale"
    assert lot.is_aggregate is False


def test_live_2024_blank_cusip_option_lots_reach_the_worksheet(
    robinhood_1099_bytes: bytes,
):
    from lot_matcher import match_1099b_lots
    from year_close_packet import lot_match_plain_text

    summary = parse_robinhood_1099_pdf(
        robinhood_1099_bytes,
        current_symbols={"CLSK", "BTDR"},
        filename="2024-robinhood-1099.pdf",
    )
    report = match_1099b_lots(
        summary.lots,
        [],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=summary.short_term_proceeds,
        long_term_proceeds=summary.long_term_proceeds,
        short_term_cost_basis=summary.short_term_cost_basis,
        long_term_cost_basis=summary.long_term_cost_basis,
        short_term_wash=summary.short_term_wash_sale_disallowed,
        long_term_wash=summary.long_term_wash_sale_disallowed,
    )
    assert report is not None
    option_rows = [
        row
        for row in report.unmatched
        if row.status == "1099_only" and row.symbol in {"CLSK", "BTDR"}
    ]
    assert option_rows, "blank-CUSIP option lots should appear on the worksheet"
    text = lot_match_plain_text(
        {"lot_match_report": report.model_dump(mode="json")}
    )
    assert "1099_only CLSK" in text or "1099_only BTDR" in text


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
    assert summary.lots == []


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


def test_parse_2026_sample_robinhood_1099_pdf_extracts_locked_short_term_totals():
    pdf_bytes = SAMPLE_2026_PDF_PATH.read_bytes()
    text = extract_text_from_pdf(pdf_bytes)

    assert "Enclosed is your 2026 Consolidated Tax Statement" in text
    assert "Robinhood" in text
    assert "$8,315.00" in text
    assert "$6,540.00" in text
    assert "$0.00" in text
    assert "$924.00" in text
    assert "$2,699.00" in text
    assert "/ Symbol: SPX" in text
    assert "/ Symbol: NVDA" in text
    assert "/ Symbol: AMD" in text
    assert "/ Symbol: TSLA" in text

    summary = parse_robinhood_1099_pdf(
        pdf_bytes,
        current_symbols={"NVDA", "AMD", "TSLA", "AAPL", "META", "MSFT", "SPY"},
        filename="sample-robinhood-1099-2026.pdf",
        expected_previous_year=2025,
    )

    assert summary.source_filename == "sample-robinhood-1099-2026.pdf"
    assert summary.broker_name == "Robinhood"
    assert summary.tax_year == 2026
    assert summary.short_term_proceeds == pytest.approx(8315.00)
    assert summary.short_term_cost_basis == pytest.approx(6540.00)
    assert summary.short_term_wash_sale_disallowed == pytest.approx(924.00)
    assert summary.short_term_net_gain == pytest.approx(2699.00)
    assert summary.long_term_proceeds == pytest.approx(0.00)
    assert summary.long_term_cost_basis == pytest.approx(0.00)
    assert summary.long_term_wash_sale_disallowed == pytest.approx(0.00)
    assert summary.long_term_net_gain == pytest.approx(0.00)
    assert summary.referenced_symbols == ["AMD", "NVDA", "SPX", "TSLA"]
    assert summary.matched_symbols == ["AMD", "NVDA", "TSLA"]

    by_symbol = {lot.symbol: lot for lot in summary.lots if not lot.is_aggregate}
    assert set(by_symbol) == {"AMD", "NVDA", "SPX", "TSLA"}
    assert by_symbol["NVDA"].quantity == pytest.approx(12)
    assert by_symbol["NVDA"].date_sold == date(2026, 2, 20)
    assert by_symbol["NVDA"].proceeds == pytest.approx(2976.00)
    assert by_symbol["NVDA"].cost_basis == pytest.approx(3360.00)
    assert by_symbol["NVDA"].wash_sale_disallowed == pytest.approx(384.00)
    assert by_symbol["TSLA"].quantity == pytest.approx(4)
    assert by_symbol["AMD"].quantity == pytest.approx(10)
    assert by_symbol["AMD"].wash_sale_disallowed == pytest.approx(300.00)
    assert by_symbol["SPX"].proceeds == pytest.approx(2699.00)
    assert by_symbol["SPX"].cost_basis == pytest.approx(0.00)
    assert by_symbol["SPX"].date_sold == date(2027, 1, 2)
