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


def test_2026_sample_matches_nvda_tsla_amd_and_spx_is_gap():
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
    matched_symbols = {row.symbol for row in report.matched}
    assert {"NVDA", "TSLA", "AMD"} <= matched_symbols
    assert any(row.symbol == "SPX" for row in report.gap)
    assert report.matched_count >= 3
    assert report.gap_count >= 1
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
    assert report.matched_count == 1
    assert report.unmatched_count == 1
    assert report.unmatched[0].symbol == "NVDA"


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
    assert report.matched_count == 1
    assert report.gap_count == 1


def test_aligned_amd_wash_is_matched_not_gap():
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
    assert report.matched_count == 1
    assert report.gap_count == 0
    assert report.matched[0].status == "matched"
    assert report.totals_ok is True
