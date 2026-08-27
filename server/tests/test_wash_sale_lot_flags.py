"""
Prove the existing wash-sale engine flags the replacement lot for a known
Robinhood CSV, and leaves a clean CSV untouched.

Does not change wash_sale.py detection logic — it only runs detect +
adjust_lots_for_wash_sales against fixtures.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from csv_parser import parse_csv
from wash_sale import adjust_lots_for_wash_sales, detect_wash_sales

FIXTURES = Path(__file__).parent / "fixtures"
PUBLIC_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "client"
    / "public"
    / "sample-robinhood-transactions.csv"
)


def _lots_and_flags(csv_path: Path, tax_year: int = 2025):
    lots, transactions, _errors, _realized = parse_csv(csv_path.read_text())
    flags = detect_wash_sales(transactions, tax_year=tax_year)
    lots = adjust_lots_for_wash_sales(lots, flags)
    return lots, flags


class TestWashSaleLotFlagsFromCsv:
    def test_wash_sale_csv_flags_amd_replacement_lot(self):
        lots, flags = _lots_and_flags(FIXTURES / "robinhood_wash_sale.csv")

        assert len(flags) == 1
        flag = flags[0]
        assert flag.symbol == "AMD"
        assert flag.sale_date == date(2025, 7, 15)
        assert flag.repurchase_date == date(2025, 7, 25)
        assert flag.disallowed_loss == 300.0

        amd_lots = [lot for lot in lots if lot.symbol == "AMD"]
        washed = [lot for lot in amd_lots if lot.wash_sale_disallowed > 0]
        clean_amd = [lot for lot in amd_lots if lot.wash_sale_disallowed == 0]
        assert len(washed) == 1
        assert washed[0].purchase_date == date(2025, 7, 25)
        assert washed[0].wash_sale_disallowed == 300.0
        # Original AMD lot was fully sold — only the replacement remains.
        assert clean_amd == []

        aapl_lots = [lot for lot in lots if lot.symbol == "AAPL"]
        assert aapl_lots
        assert all(lot.wash_sale_disallowed == 0 for lot in aapl_lots)

    def test_clean_csv_does_not_flag_any_lot(self):
        lots, flags = _lots_and_flags(FIXTURES / "robinhood_clean.csv")

        assert flags == []
        assert lots
        assert all(lot.wash_sale_disallowed == 0 for lot in lots)

    def test_public_sample_csv_flags_amd_for_tax_year_2026(self):
        lots, flags = _lots_and_flags(PUBLIC_SAMPLE, tax_year=2026)

        amd_flags = [flag for flag in flags if flag.symbol == "AMD"]
        assert len(amd_flags) == 1
        assert amd_flags[0].disallowed_loss == 300.0
        assert amd_flags[0].sale_date == date(2026, 7, 15)
        assert amd_flags[0].repurchase_date == date(2026, 7, 24)

        washed = [
            lot
            for lot in lots
            if lot.symbol == "AMD" and lot.wash_sale_disallowed > 0
        ]
        assert len(washed) == 1
        assert washed[0].purchase_date == date(2026, 7, 24)
        assert washed[0].wash_sale_disallowed == 300.0

        aapl_lots = [lot for lot in lots if lot.symbol == "AAPL"]
        assert aapl_lots
        assert all(lot.wash_sale_disallowed == 0 for lot in aapl_lots)

        years = [lot.purchase_date.year for lot in lots]
        assert years.count(2026) > len(years) / 2
        assert all(year in (2025, 2026) for year in years)
        prior_year_lots = [lot for lot in lots if lot.purchase_date.year < 2026]
        assert prior_year_lots
        assert all(lot.quantity > 0 for lot in prior_year_lots)
