"""
Tests for the wash-sale detection module.

Validates the 61-day wash-sale window detection, prospective risk checks,
and cost basis adjustment logic per IRC §1091.
"""

import sys
import os
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import Transaction, TransCode, AssetType, TaxLot, WashSaleFlag
from wash_sale import (
    detect_wash_sales,
    check_prospective_wash_sale_risk,
    adjust_lots_for_wash_sales,
    _compute_sale_loss,
    _find_qualifying_repurchases,
)


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


# --- _compute_sale_loss edge cases ---

class TestComputeSaleLoss:
    def _make_txn(self, instrument, trans_code, activity_date, quantity, price):
        return Transaction(
            activity_date=activity_date,
            process_date=None,
            settle_date=None,
            instrument=instrument,
            description="",
            trans_code=trans_code,
            quantity=quantity,
            price=price,
            amount=quantity * price,
            asset_type=AssetType.STOCK,
        )

    def test_no_buys_returns_zero(self):
        sell = self._make_txn("AAPL", TransCode.SELL, date(2025, 6, 1), 10, 100)
        loss, _ = _compute_sale_loss(sell, [], {})
        assert loss == pytest.approx(0.0)

    def test_no_matching_buys_returns_zero(self):
        sell = self._make_txn("AAPL", TransCode.SELL, date(2025, 6, 1), 10, 100)
        buy = self._make_txn("MSFT", TransCode.BUY, date(2025, 1, 1), 10, 150)
        loss, _ = _compute_sale_loss(sell, [buy], {})
        assert loss == pytest.approx(0.0)


# --- _find_qualifying_repurchases edge cases ---

class TestFindQualifyingRepurchases:
    def _make_txn(self, instrument, trans_code, activity_date, quantity, price):
        return Transaction(
            activity_date=activity_date,
            process_date=None,
            settle_date=None,
            instrument=instrument,
            description="",
            trans_code=trans_code,
            quantity=quantity,
            price=price,
            amount=quantity * price,
            asset_type=AssetType.STOCK,
        )

    def test_no_repurchases_outside_window(self):
        sell = self._make_txn("AAPL", TransCode.SELL, date(2025, 6, 1), 10, 100)
        buy = self._make_txn("AAPL", TransCode.BUY, date(2025, 8, 1), 10, 100)
        result = _find_qualifying_repurchases(sell, [buy])
        assert len(result) == 0

    def test_same_day_included(self):
        """Same-day purchases should be included in wash-sale detection."""
        sell = self._make_txn("AAPL", TransCode.SELL, date(2025, 6, 1), 10, 100)
        buy = self._make_txn("AAPL", TransCode.BUY, date(2025, 6, 1), 10, 100)
        result = _find_qualifying_repurchases(sell, [buy])
        assert len(result) == 1


# --- Partial wash sale ---

class TestPartialWashSale:
    def _make_txn(self, instrument, trans_code, activity_date, quantity, price):
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

    def test_partial_wash_sale_proportional_disallowed(self):
        """Sell 100 at loss, repurchase only 50 → only half the loss disallowed."""
        txns = [
            self._make_txn("AAPL", TransCode.BUY, date(2025, 1, 1), 100, 200),
            self._make_txn("AAPL", TransCode.SELL, date(2025, 6, 1), 100, 150),
            self._make_txn("AAPL", TransCode.BUY, date(2025, 6, 15), 50, 155),
        ]
        flags = detect_wash_sales(txns)
        assert len(flags) == 1
        # Total loss = 100 * (200-150) = 5000
        # Repurchased 50 out of 100 sold → 50% of loss disallowed
        assert flags[0].disallowed_loss == pytest.approx(2500.0)
        assert flags[0].repurchase_quantity == 50


# --- adjust_lots_for_wash_sales ---

class TestAdjustLotsForWashSales:
    def test_adjusts_cost_basis(self):
        lots = [
            TaxLot(
                symbol="AAPL",
                quantity=10,
                cost_basis_per_share=155.0,
                total_cost_basis=1550.0,
                purchase_date=date(2025, 6, 15),
            ),
        ]
        flags = [
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2025, 6, 1),
                sale_quantity=10,
                sale_loss=500.0,
                repurchase_date=date(2025, 6, 15),
                repurchase_quantity=10,
                disallowed_loss=500.0,
                adjusted_cost_basis=2050.0,
                explanation="Wash sale test",
            ),
        ]
        result = adjust_lots_for_wash_sales(lots, flags)
        assert len(result) == 1
        lot = result[0]
        # wash_sale_disallowed should be 500
        assert lot.wash_sale_disallowed == pytest.approx(500.0)
        # cost_basis_per_share += 500/10 = 50 → 205
        assert lot.cost_basis_per_share == pytest.approx(205.0)
        assert lot.total_cost_basis == pytest.approx(2050.0)

    def test_no_matching_lot_no_change(self):
        lots = [
            TaxLot(
                symbol="MSFT",
                quantity=10,
                cost_basis_per_share=300.0,
                total_cost_basis=3000.0,
                purchase_date=date(2025, 6, 15),
            ),
        ]
        flags = [
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2025, 6, 1),
                sale_quantity=10,
                sale_loss=500.0,
                repurchase_date=date(2025, 6, 15),
                repurchase_quantity=10,
                disallowed_loss=500.0,
                adjusted_cost_basis=2050.0,
                explanation="No matching lot",
            ),
        ]
        result = adjust_lots_for_wash_sales(lots, flags)
        assert result[0].cost_basis_per_share == pytest.approx(300.0)

    def test_empty_flags(self):
        lots = [
            TaxLot(
                symbol="AAPL",
                quantity=10,
                cost_basis_per_share=150.0,
                total_cost_basis=1500.0,
                purchase_date=date(2025, 1, 1),
            ),
        ]
        result = adjust_lots_for_wash_sales(lots, [])
        assert result[0].cost_basis_per_share == pytest.approx(150.0)

    def test_zero_quantity_lot_no_division_error(self):
        lots = [
            TaxLot(
                symbol="AAPL",
                quantity=0,
                cost_basis_per_share=155.0,
                total_cost_basis=0.0,
                purchase_date=date(2025, 6, 15),
            ),
        ]
        flags = [
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2025, 6, 1),
                sale_quantity=10,
                sale_loss=500.0,
                repurchase_date=date(2025, 6, 15),
                repurchase_quantity=10,
                disallowed_loss=500.0,
                adjusted_cost_basis=0.0,
                explanation="Zero qty lot",
            ),
        ]
        # Should not raise ZeroDivisionError
        result = adjust_lots_for_wash_sales(lots, flags)
        assert result[0].wash_sale_disallowed == pytest.approx(500.0)
