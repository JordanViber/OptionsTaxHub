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
