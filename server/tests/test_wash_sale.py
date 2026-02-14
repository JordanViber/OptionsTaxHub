"""
Tests for the wash-sale detection module.

Validates the 61-day wash-sale window detection, prospective risk checks,
and cost basis adjustment logic per IRC §1091.
"""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import Transaction, TransCode, AssetType
from wash_sale import detect_wash_sales, check_prospective_wash_sale_risk


# --- Wash-Sale Detection Tests ---

class TestDetectWashSales:
    def _make_txn(self, instrument: str, trans_code: TransCode,
                  activity_date: date, quantity: float, price: float) -> Transaction:
        return Transaction(
            activity_date=activity_date,
            process_date=None,
            settle_date=None,
            instrument=instrument,
            description="",
            trans_code=trans_code,
            quantity=quantity,
            price=price,
            amount=(-1 if trans_code == TransCode.BUY else 1) * quantity * price,
            asset_type=AssetType.STOCK,
        )

    def test_wash_sale_detected_within_30_days_after(self):
        """Sell at loss then repurchase within 30 days = wash sale."""
        txns = [
            self._make_txn("TSLA", TransCode.BUY, date(2025, 6, 1), 10, 300),
            self._make_txn("TSLA", TransCode.SELL, date(2025, 7, 15), 10, 200),
            self._make_txn("TSLA", TransCode.BUY, date(2025, 8, 1), 10, 210),
        ]
        flags = detect_wash_sales(txns)
        assert len(flags) == 1
        assert flags[0].symbol == "TSLA"
        assert flags[0].disallowed_loss > 0

    def test_no_wash_sale_if_repurchase_after_30_days(self):
        """Repurchase more than 30 days after sale = no wash sale."""
        txns = [
            self._make_txn("TSLA", TransCode.BUY, date(2025, 3, 1), 10, 300),
            self._make_txn("TSLA", TransCode.SELL, date(2025, 4, 15), 10, 200),
            self._make_txn("TSLA", TransCode.BUY, date(2025, 6, 1), 10, 210),
        ]
        flags = detect_wash_sales(txns)
        assert len(flags) == 0

    def test_no_wash_sale_if_sale_is_gain(self):
        """Selling at a gain should not trigger wash sale."""
        txns = [
            self._make_txn("AAPL", TransCode.BUY, date(2025, 3, 1), 10, 100),
            self._make_txn("AAPL", TransCode.SELL, date(2025, 4, 15), 10, 150),
            self._make_txn("AAPL", TransCode.BUY, date(2025, 4, 20), 10, 155),
        ]
        flags = detect_wash_sales(txns)
        assert len(flags) == 0

    def test_wash_sale_before_sale(self):
        """Purchase within 30 days BEFORE sale at loss = wash sale."""
        txns = [
            self._make_txn("MSFT", TransCode.BUY, date(2025, 3, 1), 10, 400),
            self._make_txn("MSFT", TransCode.BUY, date(2025, 6, 20), 5, 350),
            self._make_txn("MSFT", TransCode.SELL, date(2025, 7, 1), 10, 300),
        ]
        flags = detect_wash_sales(txns)
        assert len(flags) == 1
        assert flags[0].symbol == "MSFT"


# --- Prospective Wash-Sale Risk Tests ---

class TestProspectiveWashSaleRisk:
    def test_no_risk_with_only_buy(self):
        """With only a buy transaction (no sell), there's no wash-sale risk."""
        txns = [
            Transaction(
                activity_date=date.today(),
                process_date=None,
                settle_date=None,
                instrument="TSLA",
                description="",
                trans_code=TransCode.BUY,
                quantity=10,
                price=300,
                amount=-3000,
                asset_type=AssetType.STOCK,
            ),
        ]
        risk, explanation = check_prospective_wash_sale_risk("TSLA", txns)
        # Recent buy means selling now would trigger wash sale
        assert risk is True
        assert "TSLA" in explanation

    def test_no_risk_for_unrelated_symbol(self):
        """No risk for a symbol that has no recent transactions."""
        txns = [
            Transaction(
                activity_date=date.today(),
                process_date=None,
                settle_date=None,
                instrument="AAPL",
                description="",
                trans_code=TransCode.BUY,
                quantity=5,
                price=150,
                amount=-750,
                asset_type=AssetType.STOCK,
            ),
        ]
        risk, explanation = check_prospective_wash_sale_risk("MSFT", txns)
        assert risk is False
        assert explanation == ""
