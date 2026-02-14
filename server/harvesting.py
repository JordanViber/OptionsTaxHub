"""
Tax-loss harvesting suggestion engine for OptionsTaxHub.

Identifies positions with unrealized losses and generates harvesting suggestions
ranked by estimated tax savings. Integrates with the AI advisor for intelligent
replacement candidate recommendations and natural language explanations.

Workflow:
1. Identify all tax lots with unrealized losses
2. Calculate estimated tax savings for each
3. Check for wash-sale risk on each lot
4. Request AI-powered replacement suggestions and explanations
5. Rank by priority (highest tax savings first)

DISCLAIMER: For educational/simulation purposes only — not financial or tax advice.
"""

from __future__ import annotations

import logging
from datetime import date

from models import (
    HarvestingSuggestion,
    PortfolioAnalysis,
    PortfolioSummary,
    Position,
    TaxLot,
    TaxProfile,
    Transaction,
)
from tax_engine import calculate_tax_savings
from wash_sale import check_prospective_wash_sale_risk, detect_wash_sales, adjust_lots_for_wash_sales

logger = logging.getLogger(__name__)

# Fallback replacement candidates when AI is unavailable
# Maps sector/category -> list of tickers that provide similar exposure
# without being "substantially identical"
_XLC = {"symbol": "XLC", "name": "Communication Services Select Sector SPDR"}

FALLBACK_REPLACEMENTS: dict[str, list[dict[str, str]]] = {
    "AAPL": [
        {"symbol": "XLK", "name": "Technology Select Sector SPDR", "reason": "Broad tech sector ETF"},
        {"symbol": "QQQ", "name": "Invesco QQQ Trust", "reason": "Nasdaq-100 index ETF with heavy AAPL weight"},
    ],
    "MSFT": [
        {"symbol": "VGT", "name": "Vanguard Information Technology ETF", "reason": "Broad IT sector exposure"},
        {"symbol": "IGV", "name": "iShares Expanded Tech-Software ETF", "reason": "Software sector ETF"},
    ],
    "GOOGL": [
        {**_XLC, "reason": "Communication services sector ETF"},
        {"symbol": "VOX", "name": "Vanguard Communication Services ETF", "reason": "Broad communication services exposure"},
    ],
    "TSLA": [
        {"symbol": "DRIV", "name": "Global X Autonomous & Electric Vehicles ETF", "reason": "EV and autonomous driving sector ETF"},
        {"symbol": "QCLN", "name": "First Trust NASDAQ Clean Edge Green Energy", "reason": "Clean energy focus including EV"},
    ],
    "NVDA": [
        {"symbol": "SMH", "name": "VanEck Semiconductor ETF", "reason": "Broad semiconductor sector ETF"},
        {"symbol": "SOXX", "name": "iShares Semiconductor ETF", "reason": "Semiconductor index exposure"},
    ],
    "AMZN": [
        {"symbol": "XLY", "name": "Consumer Discretionary Select Sector SPDR", "reason": "Consumer discretionary sector ETF"},
        {"symbol": "IBUY", "name": "Amplify Online Retail ETF", "reason": "E-commerce focused ETF"},
    ],
    "META": [
        {**_XLC, "reason": "Communication services sector"},
        {"symbol": "SOCL", "name": "Global X Social Media ETF", "reason": "Social media focused ETF"},
    ],
    "AMD": [
        {"symbol": "SMH", "name": "VanEck Semiconductor ETF", "reason": "Semiconductor sector ETF"},
        {"symbol": "PSI", "name": "Invesco Dynamic Semiconductors ETF", "reason": "Dynamic semiconductor exposure"},
    ],
    "NFLX": [
        {**_XLC, "reason": "Communication services sector"},
        {"symbol": "PEJ", "name": "Invesco Dynamic Leisure & Entertainment ETF", "reason": "Entertainment sector ETF"},
    ],
    "DIS": [
        {"symbol": "PEJ", "name": "Invesco Dynamic Leisure & Entertainment ETF", "reason": "Leisure and entertainment sector"},
        {**_XLC, "reason": "Communication services exposure"},
    ],
    # Default fallback for unknown symbols
    "_DEFAULT": [
        {"symbol": "VTI", "name": "Vanguard Total Stock Market ETF", "reason": "Broad US market exposure"},
        {"symbol": "SPY", "name": "SPDR S&P 500 ETF", "reason": "S&P 500 index ETF"},
    ],
}


def compute_lot_metrics(
    tax_lots: list[TaxLot],
    reference_date: date | None = None,
) -> list[TaxLot]:
    """
    Compute unrealized P&L, holding period, and long-term status for each lot.

    Args:
        tax_lots: List of tax lots with current_price populated.
        reference_date: Date to calculate holding period from (defaults to today).

    Returns:
        Updated list of tax lots with computed fields.
    """
    if reference_date is None:
        reference_date = date.today()

    for lot in tax_lots:
        if lot.current_price is not None:
            # Unrealized P&L
            lot.unrealized_pnl = round(
                (lot.current_price - lot.cost_basis_per_share) * lot.quantity, 2
            )
            if lot.total_cost_basis > 0:
                lot.unrealized_pnl_pct = round(
                    (lot.unrealized_pnl / lot.total_cost_basis) * 100, 2
                )
            else:
                lot.unrealized_pnl_pct = 0.0

        # Holding period: count from day after purchase to reference date
        lot.holding_period_days = (reference_date - lot.purchase_date).days
        # IRS definition: > 1 year (> 365 days) = long-term
        lot.is_long_term = lot.holding_period_days > 365

    return tax_lots


def aggregate_positions(tax_lots: list[TaxLot]) -> list[Position]:
    """
    Aggregate tax lots into Position summaries grouped by symbol.

    Args:
        tax_lots: List of computed tax lots.

    Returns:
        List of Position objects, one per unique symbol.
    """
    positions_map: dict[str, list[TaxLot]] = {}
    for lot in tax_lots:
        if lot.symbol not in positions_map:
            positions_map[lot.symbol] = []
        positions_map[lot.symbol].append(lot)

    positions: list[Position] = []
    for symbol, lots in positions_map.items():
        total_quantity = sum(lot.quantity for lot in lots)
        total_cost_basis = sum(lot.total_cost_basis for lot in lots)
        avg_cost = total_cost_basis / total_quantity if total_quantity > 0 else 0

        current_price = lots[0].current_price  # All lots for same symbol have same price
        market_value = current_price * total_quantity if current_price else None
        unrealized_pnl = sum(lot.unrealized_pnl or 0 for lot in lots)
        unrealized_pnl_pct = (
            (unrealized_pnl / total_cost_basis * 100) if total_cost_basis > 0 else 0
        )

        earliest_date = min(lot.purchase_date for lot in lots)
        holding_days = max(lot.holding_period_days or 0 for lot in lots)
        is_long_term = any(lot.is_long_term for lot in lots)
        has_wash_risk = any(lot.wash_sale_disallowed > 0 for lot in lots)

        positions.append(
            Position(
                symbol=symbol,
                quantity=total_quantity,
                avg_cost_basis=round(avg_cost, 2),
                total_cost_basis=round(total_cost_basis, 2),
                current_price=current_price,
                market_value=round(market_value, 2) if market_value else None,
                unrealized_pnl=round(unrealized_pnl, 2),
                unrealized_pnl_pct=round(unrealized_pnl_pct, 2),
                earliest_purchase_date=earliest_date,
                holding_period_days=holding_days,
                is_long_term=is_long_term,
                asset_type=lots[0].asset_type,
                tax_lots=lots,
                wash_sale_risk=has_wash_risk,
            )
        )

    return positions


def generate_suggestions(
    tax_lots: list[TaxLot],
    transactions: list[Transaction],
    tax_profile: TaxProfile,
    ai_suggestions: dict | None = None,
) -> list[HarvestingSuggestion]:
    """
    Generate tax-loss harvesting suggestions for positions with unrealized losses.

    Args:
        tax_lots: Computed tax lots with P&L and holding period.
        transactions: All transactions (for wash-sale risk check).
        tax_profile: User's tax profile for savings calculation.
        ai_suggestions: Optional AI-generated suggestions dict
                        (from ai_advisor module).

    Returns:
        List of HarvestingSuggestion objects, ranked by estimated tax savings.
    """
    suggestions: list[HarvestingSuggestion] = []

    # Filter to lots with unrealized losses
    loss_lots = [
        lot for lot in tax_lots
        if lot.unrealized_pnl is not None and lot.unrealized_pnl < 0
    ]

    if not loss_lots:
        return suggestions

    for lot in loss_lots:
        loss_amount = abs(lot.unrealized_pnl)  # type: ignore[arg-type]
        is_lt = lot.is_long_term or False

        # Calculate estimated tax savings
        tax_savings = calculate_tax_savings(
            loss=loss_amount,
            is_long_term=is_lt,
            profile=tax_profile,
        )

        # Check prospective wash-sale risk
        wash_risk, wash_explanation = check_prospective_wash_sale_risk(
            symbol=lot.symbol,
            transactions=transactions,
        )

        # Get replacement candidates
        replacements = _get_replacements(lot.symbol, ai_suggestions)
        ai_explanation = _get_ai_explanation(lot.symbol, ai_suggestions)
        is_ai = ai_suggestions is not None and lot.symbol in (ai_suggestions or {})

        suggestions.append(
            HarvestingSuggestion(
                symbol=lot.symbol,
                action="SELL",
                quantity=lot.quantity,
                current_price=lot.current_price,
                cost_basis_per_share=lot.cost_basis_per_share,
                estimated_loss=round(loss_amount, 2),
                tax_savings_estimate=round(tax_savings, 2),
                holding_period_days=lot.holding_period_days or 0,
                is_long_term=is_lt,
                wash_sale_risk=wash_risk,
                wash_sale_explanation=wash_explanation,
                replacement_candidates=replacements,
                ai_explanation=ai_explanation,
                ai_generated=is_ai,
            )
        )

    # Sort by tax savings (highest first) and assign priority
    suggestions.sort(key=lambda s: s.tax_savings_estimate, reverse=True)
    for idx, suggestion in enumerate(suggestions):
        suggestion.priority = idx + 1

    return suggestions


def _get_replacements(
    symbol: str,
    ai_suggestions: dict | None,
) -> list:
    """Get replacement candidates from AI or fallback mappings."""
    from models import ReplacementCandidate

    # Try AI suggestions first
    if ai_suggestions and symbol in ai_suggestions:
        ai_data = ai_suggestions[symbol]
        if "replacements" in ai_data:
            return [
                ReplacementCandidate(
                    symbol=r.get("symbol", ""),
                    name=r.get("name", ""),
                    reason=r.get("reason", ""),
                )
                for r in ai_data["replacements"]
                if r.get("symbol")
            ]

    # Fallback to hardcoded mappings
    fallback = FALLBACK_REPLACEMENTS.get(symbol, FALLBACK_REPLACEMENTS["_DEFAULT"])
    return [
        ReplacementCandidate(
            symbol=r["symbol"],
            name=r["name"],
            reason=r["reason"],
        )
        for r in fallback
    ]


def _get_ai_explanation(
    symbol: str,
    ai_suggestions: dict | None,
) -> str:
    """Get AI-generated explanation for a suggestion."""
    if ai_suggestions and symbol in ai_suggestions:
        return ai_suggestions[symbol].get("explanation", "")
    return ""


def build_portfolio_summary(
    positions: list[Position],
    suggestions: list[HarvestingSuggestion],
    wash_sale_flags: list,
) -> PortfolioSummary:
    """
    Build high-level portfolio summary metrics for dashboard cards.

    Args:
        positions: Aggregated position summaries.
        suggestions: Generated harvesting suggestions.
        wash_sale_flags: Detected wash sale flags.

    Returns:
        PortfolioSummary with totals and counts.
    """
    total_market_value = sum(p.market_value or 0 for p in positions)
    total_cost_basis = sum(p.total_cost_basis for p in positions)
    total_unrealized_pnl = sum(p.unrealized_pnl or 0 for p in positions)
    total_unrealized_pnl_pct = (
        (total_unrealized_pnl / total_cost_basis * 100) if total_cost_basis > 0 else 0
    )

    total_harvestable = sum(s.estimated_loss for s in suggestions)
    total_tax_savings = sum(s.tax_savings_estimate for s in suggestions)

    lots_with_losses = 0
    lots_with_gains = 0
    for p in positions:
        for lot in p.tax_lots:
            if lot.unrealized_pnl is not None:
                if lot.unrealized_pnl < 0:
                    lots_with_losses += 1
                elif lot.unrealized_pnl > 0:
                    lots_with_gains += 1

    return PortfolioSummary(
        total_market_value=round(total_market_value, 2),
        total_cost_basis=round(total_cost_basis, 2),
        total_unrealized_pnl=round(total_unrealized_pnl, 2),
        total_unrealized_pnl_pct=round(total_unrealized_pnl_pct, 2),
        total_harvestable_losses=round(total_harvestable, 2),
        estimated_tax_savings=round(total_tax_savings, 2),
        positions_count=len(positions),
        lots_with_losses=lots_with_losses,
        lots_with_gains=lots_with_gains,
        wash_sale_flags_count=len(wash_sale_flags),
    )
