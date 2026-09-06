"""Lot matcher: same-year 1099-B lots vs CSV FIFO closes."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from csv_parser import RealizedEvent, parse_csv
from lot_matcher import match_1099b_lots
from models import AssetType, Form1099BLot
from pdf_1099_parser import parse_robinhood_1099_pdf

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_CSV = REPO_ROOT / "client" / "public" / "sample-robinhood-transactions.csv"
SAMPLE_1099 = REPO_ROOT / "client" / "public" / "sample-robinhood-1099-2026.pdf"


def _lot(**overrides) -> Form1099BLot:
    payload = {
        "symbol": "AMD",
        "quantity": 10,
        "date_sold": date(2024, 7, 17),
        "proceeds": 1200.0,
        "cost_basis": 1500.0,
        "wash_sale_disallowed": 300.0,
    }
    payload.update(overrides)
    return Form1099BLot(**payload)


def _event(**overrides) -> RealizedEvent:
    payload = {
        "sale_date": date(2024, 7, 15),
        "symbol": "AMD",
        "quantity": 10,
        "cost_basis": 1500.0,
        "sale_proceeds": 1200.0,
        "pnl": -300.0,
        "is_long_term": False,
        "purchase_date": date(2024, 6, 1),
        "settle_date": date(2024, 7, 17),
        "asset_type": AssetType.STOCK,
    }
    payload.update(overrides)
    return RealizedEvent(**payload)


def test_2026_sample_pairs_as_settlement_gap_and_spx_is_1099_only():
    csv_text = SAMPLE_CSV.read_text()
    _lots, _txns, _warnings, realized = parse_csv(csv_text)
    summary = parse_robinhood_1099_pdf(
        SAMPLE_1099.read_bytes(),
        current_symbols={"NVDA", "AMD", "TSLA", "AAPL", "META", "MSFT", "SPY"},
        filename="sample-robinhood-1099-2026.pdf",
    )
    report = match_1099b_lots(
        summary.lots,
        realized,
        form_1099_tax_year=2026,
        analysis_tax_year=2026,
        short_term_proceeds=summary.short_term_proceeds,
        long_term_proceeds=summary.long_term_proceeds,
        short_term_cost_basis=summary.short_term_cost_basis,
        long_term_cost_basis=summary.long_term_cost_basis,
        short_term_wash=summary.short_term_wash_sale_disallowed,
        long_term_wash=summary.long_term_wash_sale_disallowed,
    )
    assert report is not None
    gap_symbols = {row.symbol for row in report.gap}
    assert {"NVDA", "TSLA", "AMD"} <= gap_symbols
    assert all(row.status == "matched_settlement_gap" for row in report.gap)
    assert any(row.symbol == "SPX" and row.status == "1099_only" for row in report.unmatched)
    assert report.gap_count >= 3
    assert report.totals_ok is True


def test_year_mismatch_returns_none():
    report = match_1099b_lots(
        [_lot()],
        [_event()],
        form_1099_tax_year=2024,
        analysis_tax_year=2026,
    )
    assert report is None


def test_csv_only_sell_is_unmatched():
    report = match_1099b_lots(
        [_lot()],
        [_event(), _event(symbol="NVDA", sale_proceeds=2976.0, quantity=12, cost_basis=3360.0)],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=1200.0,
        short_term_cost_basis=1500.0,
        short_term_wash=300.0,
    )
    assert report is not None
    assert report.gap_count == 1
    assert report.gap[0].status == "matched_settlement_gap"
    assert report.unmatched_count == 1
    assert report.unmatched[0].symbol == "NVDA"
    assert report.unmatched[0].status == "csv_only"


def test_two_1099_lots_cannot_reuse_one_csv_close():
    report = match_1099b_lots(
        [_lot(), _lot()],
        [_event()],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=2400.0,
        short_term_cost_basis=3000.0,
        short_term_wash=600.0,
    )
    assert report is not None
    assert report.gap_count == 1
    assert report.gap[0].status == "matched_settlement_gap"
    assert report.unmatched_count == 1
    assert report.unmatched[0].status == "1099_only"


def test_trade_date_alignment_is_matched():
    report = match_1099b_lots(
        [_lot(date_sold=date(2024, 7, 15))],
        [_event()],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=1200.0,
        short_term_cost_basis=1500.0,
        short_term_wash=300.0,
    )
    assert report is not None
    assert report.matched_count == 1
    assert report.gap_count == 0
    assert report.matched[0].status == "matched"
    assert report.totals_ok is True


def test_prior_year_realized_closes_are_not_export_candidates():
    report = match_1099b_lots(
        [_lot()],
        [
            _event(),
            _event(
                sale_date=date(2023, 7, 15),
                settle_date=date(2023, 7, 17),
                symbol="NVDA",
                sale_proceeds=2976.0,
                quantity=12,
                cost_basis=3360.0,
            ),
        ],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=1200.0,
        short_term_cost_basis=1500.0,
        short_term_wash=300.0,
    )
    assert report is not None
    assert all(row.symbol != "NVDA" for row in report.unmatched)
    assert all(
        row.export_trade_date is None or row.export_trade_date.year == 2024
        for row in (*report.matched, *report.gap, *report.unmatched)
    )


def test_multi_year_activity_book_does_not_false_match_prior_year_amd():
    """Signed-in books replay FIFO on all years. A 2023 AMD close with the
    same qty/proceeds must not steal the 2024 1099 pair or show as csv_only.
    """
    report = match_1099b_lots(
        [_lot()],
        [
            _event(sale_date=date(2023, 7, 15), settle_date=date(2023, 7, 17)),
            _event(),
        ],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=1200.0,
        short_term_cost_basis=1500.0,
        short_term_wash=300.0,
    )
    assert report is not None
    assert report.unmatched_count == 0
    paired = report.matched + report.gap
    assert len(paired) == 1
    assert paired[0].export_trade_date == date(2024, 7, 15)


def test_candidate_year_follows_trade_date_not_settlement():
    """Realized summary keys the year off sale_date, not settle_date.

    A 2023-12-29 trade that settles 2024-01-02 must not pair with a 2024
    1099 lot or dump as csv_only. A 2024-12-30 trade that settles 2025-01-02
    stays in the 2024 candidate set.
    """
    report = match_1099b_lots(
        [_lot(date_sold=date(2024, 1, 2))],
        [
            _event(sale_date=date(2023, 12, 29), settle_date=date(2024, 1, 2)),
            _event(
                sale_date=date(2024, 12, 30),
                settle_date=date(2025, 1, 2),
                symbol="NVDA",
                sale_proceeds=2976.0,
                quantity=12,
                cost_basis=3360.0,
            ),
        ],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=1200.0,
        short_term_cost_basis=1500.0,
        short_term_wash=300.0,
    )
    assert report is not None
    all_rows = (*report.matched, *report.gap, *report.unmatched)
    assert all(
        row.export_trade_date is None or row.export_trade_date.year == 2024
        for row in all_rows
    )
    assert any(row.status == "1099_only" and row.symbol == "AMD" for row in report.unmatched)
    assert any(
        row.status == "csv_only"
        and row.symbol == "NVDA"
        and row.export_trade_date == date(2024, 12, 30)
        for row in report.unmatched
    )


def test_settle_vs_trade_split_is_matched_settlement_gap():
    report = match_1099b_lots(
        [_lot()],
        [_event()],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=1200.0,
        short_term_cost_basis=1500.0,
        short_term_wash=300.0,
    )
    assert report is not None
    assert report.matched_count == 0
    assert report.gap_count == 1
    assert report.gap[0].status == "matched_settlement_gap"
    assert report.totals_ok is True


def test_short_option_sto_btc_pairs_premium_as_1099_proceeds():
    """FIFO stores STO premium as cost_basis and BTC as sale_proceeds.

    1099-B reports the inverse: proceeds = premium collected, cost = buyback.
    """
    report = match_1099b_lots(
        [
            _lot(
                symbol="CLSK",
                quantity=1,
                date_sold=date(2024, 7, 17),
                proceeds=1500.0,
                cost_basis=400.0,
                wash_sale_disallowed=0.0,
                description="CLSK 12/20/2024 CALL $14.50",
                additional_info="Option sale",
            )
        ],
        [
            _event(
                symbol="CLSK",
                quantity=1,
                sale_date=date(2024, 7, 15),
                settle_date=date(2024, 7, 17),
                cost_basis=1500.0,
                sale_proceeds=400.0,
                pnl=1100.0,
                purchase_date=date(2024, 6, 11),
                asset_type=AssetType.OPTION,
            )
        ],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=1500.0,
        short_term_cost_basis=400.0,
    )
    assert report is not None
    paired = report.matched + report.gap
    assert len(paired) == 1
    assert report.unmatched_count == 0
    row = paired[0]
    assert row.symbol == "CLSK"
    assert row.proceeds_1099 == pytest.approx(1500.0)
    assert row.proceeds_export == pytest.approx(1500.0)
    assert row.cost_basis_1099 == pytest.approx(400.0)
    assert row.cost_basis_export == pytest.approx(400.0)


def test_short_option_mapped_amounts_are_matched_not_false_gap():
    """Same trade date as 1099 sold date: amount flip is not a settlement gap."""
    report = match_1099b_lots(
        [
            _lot(
                symbol="CLSK",
                quantity=1,
                date_sold=date(2024, 7, 15),
                proceeds=1500.0,
                cost_basis=400.0,
                wash_sale_disallowed=0.0,
                additional_info="Option sale",
            )
        ],
        [
            _event(
                symbol="CLSK",
                quantity=1,
                sale_date=date(2024, 7, 15),
                settle_date=date(2024, 7, 17),
                cost_basis=1500.0,
                sale_proceeds=400.0,
                pnl=1100.0,
                purchase_date=date(2024, 6, 11),
                asset_type=AssetType.OPTION,
            )
        ],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=1500.0,
        short_term_cost_basis=400.0,
    )
    assert report is not None
    assert report.matched_count == 1
    assert report.gap_count == 0
    assert report.unmatched_count == 0
    row = report.matched[0]
    assert row.status == "matched"
    assert row.proceeds_export == pytest.approx(1500.0)
    assert row.cost_basis_export == pytest.approx(400.0)


def test_csv_only_short_option_export_columns_use_1099_orientation():
    report = match_1099b_lots(
        [_lot()],
        [
            _event(
                symbol="CLSK",
                quantity=1,
                cost_basis=1500.0,
                sale_proceeds=400.0,
                pnl=1100.0,
                asset_type=AssetType.OPTION,
            )
        ],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=1200.0,
        short_term_cost_basis=1500.0,
        short_term_wash=300.0,
    )
    assert report is not None
    csv_only = [row for row in report.unmatched if row.status == "csv_only"]
    assert len(csv_only) == 1
    assert csv_only[0].symbol == "CLSK"
    assert csv_only[0].proceeds_export == pytest.approx(1500.0)
    assert csv_only[0].cost_basis_export == pytest.approx(400.0)


def test_short_option_oexp_pairs_premium_against_zero_buyback():
    report = match_1099b_lots(
        [
            _lot(
                symbol="SPX",
                quantity=1,
                date_sold=date(2026, 12, 31),
                proceeds=2699.0,
                cost_basis=0.0,
                wash_sale_disallowed=0.0,
            )
        ],
        [
            _event(
                symbol="SPX",
                quantity=1,
                sale_date=date(2026, 12, 31),
                settle_date=date(2027, 1, 2),
                cost_basis=2699.0,
                sale_proceeds=0.0,
                pnl=2699.0,
                purchase_date=date(2026, 12, 15),
                asset_type=AssetType.OPTION,
            )
        ],
        form_1099_tax_year=2026,
        analysis_tax_year=2026,
        short_term_proceeds=2699.0,
        short_term_cost_basis=0.0,
    )
    assert report is not None
    assert report.matched_count == 1
    assert report.gap_count == 0
    assert report.unmatched_count == 0
    assert report.matched[0].proceeds_export == pytest.approx(2699.0)
    assert report.matched[0].cost_basis_export == pytest.approx(0.0)


def test_long_option_stc_still_pairs_on_direct_proceeds():
    report = match_1099b_lots(
        [
            _lot(
                symbol="BTDR",
                quantity=1,
                date_sold=date(2024, 7, 17),
                proceeds=399.94,
                cost_basis=300.03,
                wash_sale_disallowed=0.0,
                additional_info="Option sale",
            )
        ],
        [
            _event(
                symbol="BTDR",
                quantity=1,
                sale_date=date(2024, 7, 15),
                settle_date=date(2024, 7, 17),
                cost_basis=300.03,
                sale_proceeds=399.94,
                pnl=99.91,
                purchase_date=date(2024, 6, 24),
                asset_type=AssetType.OPTION,
            )
        ],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=399.94,
        short_term_cost_basis=300.03,
    )
    assert report is not None
    paired = report.matched + report.gap
    assert len(paired) == 1
    assert paired[0].proceeds_export == pytest.approx(399.94)
    assert paired[0].cost_basis_export == pytest.approx(300.03)


def test_stock_inverted_proceeds_do_not_use_sto_btc_swap():
    report = match_1099b_lots(
        [_lot(proceeds=1500.0, cost_basis=1200.0, wash_sale_disallowed=0.0)],
        [_event(sale_proceeds=1200.0, cost_basis=1500.0, pnl=300.0)],
        form_1099_tax_year=2024,
        analysis_tax_year=2024,
        short_term_proceeds=1500.0,
        short_term_cost_basis=1200.0,
    )
    assert report is not None
    assert report.matched_count == 0
    assert report.gap_count == 0
    statuses = {row.status for row in report.unmatched}
    assert statuses == {"1099_only", "csv_only"}
