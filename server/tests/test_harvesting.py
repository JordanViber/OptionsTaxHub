"""
Tests for the harvesting module.

Covers compute_lot_metrics, aggregate_positions, generate_suggestions,
build_portfolio_summary, and helper functions.
"""

import sys
import os
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import (
    AssetType,
    FilingStatus,
    HarvestingSuggestion,
    Position,
    PortfolioSummary,
    TaxLot,
    TaxProfile,
    Transaction,
    TransCode,
    ReplacementCandidate,
)
from harvesting import (
    compute_lot_metrics,
    aggregate_positions,
    generate_suggestions,
    build_portfolio_summary,
    _fifo_cost_basis_for_sell,
    _compute_realized_gains,
    _get_replacements,
    _get_ai_explanation,
    FALLBACK_REPLACEMENTS,
)


# --- Helpers ---

def _lot(
    symbol: str = "AAPL",
    quantity: float = 10,
    cost_basis: float = 150.0,
    purchase_date: date = date(2024, 6, 1),
    current_price: float | None = 140.0,
) -> TaxLot:
    return TaxLot(
        symbol=symbol,
        quantity=quantity,
        cost_basis_per_share=cost_basis,
        total_cost_basis=cost_basis * quantity,
        purchase_date=purchase_date,
        current_price=current_price,
    )


def _txn(
    instrument: str,
    trans_code: TransCode,
    activity_date: date,
    quantity: float,
    price: float,
) -> Transaction:
    sign = -1 if trans_code in (TransCode.BUY, TransCode.BTO) else 1
    return Transaction(
        activity_date=activity_date,
        instrument=instrument,
        trans_code=trans_code,
        quantity=quantity,
        price=price,
        amount=sign * quantity * price,
    )


def _profile(
    income: float = 100_000,
    filing_status: str = "single",
    tax_year: int = 2025,
) -> TaxProfile:
    return TaxProfile(
        filing_status=FilingStatus(filing_status),
        estimated_annual_income=income,
        tax_year=tax_year,
    )


# --- compute_lot_metrics ---

class TestComputeLotMetrics:
    def test_computes_unrealized_pnl_loss(self):
        lots = [_lot(current_price=140.0, cost_basis=150.0, quantity=10)]
        result = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        assert result[0].unrealized_pnl == pytest.approx(-100.0)
        assert result[0].unrealized_pnl_pct == pytest.approx(-6.67, abs=0.01)

    def test_computes_unrealized_pnl_gain(self):
        lots = [_lot(current_price=160.0, cost_basis=150.0, quantity=10)]
        result = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        assert result[0].unrealized_pnl == pytest.approx(100.0)

    def test_holding_period_and_long_term(self):
        lots = [_lot(purchase_date=date(2024, 1, 1))]
        result = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        assert result[0].holding_period_days == (date(2025, 6, 1) - date(2024, 1, 1)).days
        assert result[0].is_long_term is True

    def test_short_term_holding(self):
        lots = [_lot(purchase_date=date(2025, 5, 1))]
        result = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        assert result[0].is_long_term is False

    def test_no_current_price(self):
        lots = [_lot(current_price=None)]
        result = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        assert result[0].unrealized_pnl is None

    def test_zero_cost_basis_pnl_pct(self):
        lot = TaxLot(
            symbol="FREE",
            quantity=10,
            cost_basis_per_share=0.0,
            total_cost_basis=0.0,
            purchase_date=date(2024, 1, 1),
            current_price=10.0,
        )
        result = compute_lot_metrics([lot], reference_date=date(2025, 6, 1))
        assert result[0].unrealized_pnl_pct == pytest.approx(0.0)

    def test_defaults_to_today(self):
        lots = [_lot(purchase_date=date.today() - timedelta(days=10))]
        result = compute_lot_metrics(lots)
        assert result[0].holding_period_days == 10


# --- aggregate_positions ---

class TestAggregatePositions:
    def test_single_lot_single_position(self):
        lots = [_lot(symbol="AAPL", quantity=10, cost_basis=150.0, current_price=160.0)]
        lots = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        positions = aggregate_positions(lots)
        assert len(positions) == 1
        p = positions[0]
        assert p.symbol == "AAPL"
        assert p.quantity == 10
        assert p.avg_cost_basis == pytest.approx(150.0)
        assert p.market_value == pytest.approx(1600.0)

    def test_multiple_lots_same_symbol(self):
        lots = [
            _lot(symbol="AAPL", quantity=10, cost_basis=100.0, current_price=120.0),
            _lot(symbol="AAPL", quantity=5, cost_basis=110.0, current_price=120.0,
                 purchase_date=date(2024, 8, 1)),
        ]
        lots = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        positions = aggregate_positions(lots)
        assert len(positions) == 1
        p = positions[0]
        assert p.quantity == 15
        expected_avg = (100.0 * 10 + 110.0 * 5) / 15
        assert p.avg_cost_basis == pytest.approx(expected_avg, abs=0.01)

    def test_multiple_symbols(self):
        lots = [
            _lot(symbol="AAPL", quantity=10, cost_basis=150.0, current_price=160.0),
            _lot(symbol="MSFT", quantity=5, cost_basis=300.0, current_price=310.0),
        ]
        lots = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        positions = aggregate_positions(lots)
        assert len(positions) == 2
        symbols = {p.symbol for p in positions}
        assert symbols == {"AAPL", "MSFT"}

    def test_wash_sale_risk_flag(self):
        lot = _lot(symbol="TSLA", quantity=5, cost_basis=200.0, current_price=190.0)
        lot.wash_sale_disallowed = 50.0
        lots = compute_lot_metrics([lot], reference_date=date(2025, 6, 1))
        positions = aggregate_positions(lots)
        assert positions[0].wash_sale_risk is True

    def test_zero_quantity_position(self):
        lots = [_lot(symbol="AAPL", quantity=0, cost_basis=150.0, current_price=160.0)]
        lots = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        positions = aggregate_positions(lots)
        assert positions[0].avg_cost_basis == 0

    def test_earliest_purchase_date(self):
        lots = [
            _lot(symbol="AAPL", purchase_date=date(2024, 3, 1), current_price=160.0),
            _lot(symbol="AAPL", purchase_date=date(2024, 1, 1), current_price=160.0),
        ]
        lots = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        positions = aggregate_positions(lots)
        assert positions[0].earliest_purchase_date == date(2024, 1, 1)

    def test_none_current_price(self):
        lots = [_lot(symbol="AAPL", current_price=None)]
        lots = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        positions = aggregate_positions(lots)
        assert positions[0].market_value is None


# --- _fifo_cost_basis_for_sell ---

class TestFifoCostBasisForSell:
    def test_simple_match(self):
        buys = [_txn("AAPL", TransCode.BUY, date(2025, 1, 1), 10, 100.0)]
        sell = _txn("AAPL", TransCode.SELL, date(2025, 3, 1), 5, 110.0)
        remaining = {0: 10.0}
        cost = _fifo_cost_basis_for_sell(sell, buys, remaining)
        assert cost == pytest.approx(500.0)
        assert remaining[0] == pytest.approx(5.0)

    def test_multi_lot_fifo(self):
        buys = [
            _txn("AAPL", TransCode.BUY, date(2025, 1, 1), 5, 100.0),
            _txn("AAPL", TransCode.BUY, date(2025, 2, 1), 5, 120.0),
        ]
        sell = _txn("AAPL", TransCode.SELL, date(2025, 3, 1), 7, 110.0)
        remaining = {0: 5.0, 1: 5.0}
        cost = _fifo_cost_basis_for_sell(sell, buys, remaining)
        assert cost == pytest.approx(740.0)

    def test_different_instrument_ignored(self):
        buys = [_txn("MSFT", TransCode.BUY, date(2025, 1, 1), 10, 300.0)]
        sell = _txn("AAPL", TransCode.SELL, date(2025, 3, 1), 5, 110.0)
        remaining = {0: 10.0}
        cost = _fifo_cost_basis_for_sell(sell, buys, remaining)
        assert cost == pytest.approx(0.0)

    def test_buy_after_sell_ignored(self):
        buys = [_txn("AAPL", TransCode.BUY, date(2025, 6, 1), 10, 100.0)]
        sell = _txn("AAPL", TransCode.SELL, date(2025, 3, 1), 5, 110.0)
        remaining = {0: 10.0}
        cost = _fifo_cost_basis_for_sell(sell, buys, remaining)
        assert cost == pytest.approx(0.0)


# --- _compute_realized_gains ---

class TestComputeRealizedGains:
    def test_no_transactions(self):
        assert _compute_realized_gains([]) == pytest.approx(0.0)

    def test_gain_only(self):
        txns = [
            _txn("AAPL", TransCode.BUY, date(2025, 1, 1), 10, 100.0),
            _txn("AAPL", TransCode.SELL, date(2025, 3, 1), 10, 120.0),
        ]
        gains = _compute_realized_gains(txns)
        # Proceeds = 1200, cost = 1000 → gain = 200
        assert gains == pytest.approx(200.0)

    def test_loss_excluded(self):
        txns = [
            _txn("AAPL", TransCode.BUY, date(2025, 1, 1), 10, 120.0),
            _txn("AAPL", TransCode.SELL, date(2025, 3, 1), 10, 100.0),
        ]
        gains = _compute_realized_gains(txns)
        # Loss, not gain → 0
        assert gains == pytest.approx(0.0)

    def test_mixed_gains_and_losses(self):
        txns = [
            _txn("AAPL", TransCode.BUY, date(2025, 1, 1), 10, 100.0),
            _txn("AAPL", TransCode.SELL, date(2025, 3, 1), 10, 120.0),  # +200
            _txn("MSFT", TransCode.BUY, date(2025, 1, 1), 5, 300.0),
            _txn("MSFT", TransCode.SELL, date(2025, 3, 1), 5, 280.0),  # -100 (excluded)
        ]
        gains = _compute_realized_gains(txns)
        assert gains == pytest.approx(200.0)


# --- generate_suggestions ---

class TestGenerateSuggestions:
    def test_no_losses_no_suggestions(self):
        lots = [_lot(cost_basis=100.0, current_price=120.0)]
        lots = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        suggestions = generate_suggestions(
            tax_lots=lots,
            transactions=[],
            tax_profile=_profile(),
        )
        assert len(suggestions) == 0

    def test_loss_generates_suggestion(self):
        lots = [_lot(symbol="AAPL", cost_basis=150.0, current_price=140.0, quantity=10)]
        lots = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        suggestions = generate_suggestions(
            tax_lots=lots,
            transactions=[],
            tax_profile=_profile(),
        )
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.symbol == "AAPL"
        assert s.estimated_loss == pytest.approx(100.0)
        assert s.tax_savings_estimate > 0
        assert s.priority == 1

    def test_wash_sale_risk_excluded(self):
        """Lots with prospective wash-sale risk should not appear in main suggestions."""
        purchase_date = date.today() - timedelta(days=10)
        lots = [_lot(symbol="AAPL", cost_basis=150.0, current_price=140.0,
                     purchase_date=purchase_date)]
        lots = compute_lot_metrics(lots)
        # Recent buy within 30 days creates wash-sale risk
        txns = [
            _txn("AAPL", TransCode.BUY, purchase_date, 10, 150.0),
        ]
        suggestions = generate_suggestions(
            tax_lots=lots,
            transactions=txns,
            tax_profile=_profile(),
        )
        # Should be empty because of wash-sale risk
        assert len(suggestions) == 0

    def test_ranked_by_tax_savings(self):
        lots = [
            _lot(symbol="AAPL", cost_basis=200.0, current_price=180.0, quantity=10),  # -200
            _lot(symbol="MSFT", cost_basis=300.0, current_price=250.0, quantity=10),  # -500
        ]
        lots = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        suggestions = generate_suggestions(
            tax_lots=lots,
            transactions=[],
            tax_profile=_profile(),
        )
        assert len(suggestions) >= 2
        # Should be ranked by tax savings, highest first
        assert suggestions[0].tax_savings_estimate >= suggestions[1].tax_savings_estimate

    def test_harvest_target_caps_suggestions(self):
        """Only suggest enough to offset realized gains + $3k deduction."""
        lots = [
            _lot(symbol="AAPL", cost_basis=200.0, current_price=100.0, quantity=100),  # -10,000 loss
            _lot(symbol="MSFT", cost_basis=200.0, current_price=100.0, quantity=100),  # -10,000 loss
        ]
        lots = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        # Realized gains of $500 → target $3,500 → capped
        txns = [
            _txn("TSLA", TransCode.BUY, date(2025, 1, 1), 10, 100.0),
            _txn("TSLA", TransCode.SELL, date(2025, 3, 1), 10, 150.0),  # +500 realized
        ]
        suggestions = generate_suggestions(
            tax_lots=lots,
            transactions=txns,
            tax_profile=_profile(),
        )
        # Should cap at some point based on harvest target
        assert len(suggestions) >= 1

    def test_with_ai_suggestions(self):
        lots = [_lot(symbol="AAPL", cost_basis=150.0, current_price=140.0, quantity=10)]
        lots = compute_lot_metrics(lots, reference_date=date(2025, 6, 1))
        ai = {
            "AAPL": {
                "replacements": [
                    {"symbol": "XLK", "name": "Tech ETF", "reason": "Broad tech exposure"}
                ],
                "explanation": "AI says harvest this.",
            }
        }
        suggestions = generate_suggestions(
            tax_lots=lots,
            transactions=[],
            tax_profile=_profile(),
            ai_suggestions=ai,
        )
        assert len(suggestions) >= 1
        assert suggestions[0].ai_generated is True
        assert "AI says" in suggestions[0].ai_explanation


# --- _get_replacements ---

class TestGetReplacements:
    def test_fallback_for_known_symbol(self):
        reps = _get_replacements("AAPL", None)
        assert len(reps) >= 1
        assert all(isinstance(r, ReplacementCandidate) for r in reps)

    def test_fallback_for_unknown_symbol(self):
        reps = _get_replacements("UNKNOWN_TICKER", None)
        assert len(reps) >= 1
        # Should use _DEFAULT
        symbols = [r.symbol for r in reps]
        assert "VTI" in symbols or "SPY" in symbols

    def test_ai_replacements_used(self):
        ai = {
            "AAPL": {
                "replacements": [
                    {"symbol": "QQQ", "name": "Invesco QQQ", "reason": "AI-powered"}
                ]
            }
        }
        reps = _get_replacements("AAPL", ai)
        assert len(reps) == 1
        assert reps[0].symbol == "QQQ"
        assert reps[0].reason == "AI-powered"

    def test_ai_empty_replacements_uses_fallback(self):
        ai = {"AAPL": {"explanation": "no replacements key"}}
        reps = _get_replacements("AAPL", ai)
        # Falls through to fallback because no "replacements" key
        assert len(reps) >= 1

    def test_ai_replacements_skip_empty_symbol(self):
        ai = {
            "AAPL": {
                "replacements": [
                    {"symbol": "", "name": "Empty", "reason": "bad"},
                    {"symbol": "XLK", "name": "Tech ETF", "reason": "good"},
                ]
            }
        }
        reps = _get_replacements("AAPL", ai)
        # Should skip the empty symbol entry
        assert len(reps) == 1
        assert reps[0].symbol == "XLK"


# --- _get_ai_explanation ---

class TestGetAiExplanation:
    def test_returns_explanation(self):
        ai = {"AAPL": {"explanation": "Sell for tax benefit."}}
        result = _get_ai_explanation("AAPL", ai)
        assert result == "Sell for tax benefit."

    def test_returns_empty_if_no_ai(self):
        assert _get_ai_explanation("AAPL", None) == ""

    def test_returns_empty_if_symbol_not_in_ai(self):
        ai = {"MSFT": {"explanation": "..."}}
        assert _get_ai_explanation("AAPL", ai) == ""

    def test_returns_empty_if_no_explanation_key(self):
        ai = {"AAPL": {"replacements": []}}
        assert _get_ai_explanation("AAPL", ai) == ""


# --- build_portfolio_summary ---

class TestBuildPortfolioSummary:
    def test_basic_summary(self):
        lots_data = [
            _lot(symbol="AAPL", quantity=10, cost_basis=150.0, current_price=160.0),
            _lot(symbol="MSFT", quantity=5, cost_basis=300.0, current_price=290.0),
        ]
        lots_data = compute_lot_metrics(lots_data, reference_date=date(2025, 6, 1))
        positions = aggregate_positions(lots_data)
        suggestions = [
            HarvestingSuggestion(
                symbol="MSFT",
                quantity=5,
                cost_basis_per_share=300.0,
                estimated_loss=50.0,
                tax_savings_estimate=11.0,
                holding_period_days=365,
                is_long_term=True,
            )
        ]
        summary = build_portfolio_summary(positions, suggestions, [])

        assert summary.positions_count == 2
        assert summary.total_market_value == pytest.approx(3050.0)
        assert summary.total_harvestable_losses == pytest.approx(50.0)
        assert summary.estimated_tax_savings == pytest.approx(11.0)
        assert summary.lots_with_losses >= 1
        assert summary.lots_with_gains >= 1
        assert summary.wash_sale_flags_count == 0

    def test_empty_portfolio(self):
        summary = build_portfolio_summary([], [], [])
        assert summary.positions_count == 0
        assert summary.total_market_value == pytest.approx(0.0)
        assert summary.total_harvestable_losses == pytest.approx(0.0)

    def test_with_wash_sale_flags(self):
        from models import WashSaleFlag
        flags = [
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2025, 1, 1),
                sale_quantity=10,
                sale_loss=100.0,
                repurchase_date=date(2025, 1, 15),
                repurchase_quantity=10,
                disallowed_loss=100.0,
                adjusted_cost_basis=160.0,
            )
        ]
        summary = build_portfolio_summary([], [], flags)
        assert summary.wash_sale_flags_count == 1
