"""
Wash-sale rule detector for OptionsTaxHub.

Implements IRS wash-sale rule detection (IRC §1091):
- Disallows loss deduction when substantially identical securities are
  purchased within a 61-day window (30 days before OR 30 days after a sale at a loss).
- Adjusts cost basis of replacement shares by adding the disallowed loss.
- Handles partial wash sales (e.g., sell 100, re-buy only 75).

Also detects prospective wash-sale risk for unrealized positions
(would selling today trigger a wash sale due to recent/upcoming purchases?).

DISCLAIMER: For educational/simulation purposes only — not financial or tax advice.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from models import TaxLot, Transaction, TransCode, WashSaleFlag

logger = logging.getLogger(__name__)

# The wash-sale window: 30 days before and 30 days after the sale
WASH_SALE_WINDOW_DAYS = 30


def _compute_sale_loss(
    sell: Transaction,
    buys_sorted: list[Transaction],
    used_buy_quantities: dict[int, float],
) -> float:
    """Compute the realized loss for a sell transaction using FIFO cost basis."""
    symbol_buys = [
        (i, b)
        for i, b in enumerate(buys_sorted)
        if b.instrument == sell.instrument and b.activity_date <= sell.activity_date
    ]
    if not symbol_buys:
        return 0.0

    remaining_sell_qty = sell.quantity
    total_cost_basis = 0.0

    for buy_idx, buy in symbol_buys:
        if remaining_sell_qty <= 0:
            break
        available = buy.quantity - used_buy_quantities.get(buy_idx, 0)
        if available <= 0:
            continue
        matched = min(available, remaining_sell_qty)
        total_cost_basis += matched * buy.price
        remaining_sell_qty -= matched

    sale_proceeds = sell.quantity * sell.price
    return total_cost_basis - sale_proceeds


def _find_qualifying_repurchases(
    sell: Transaction,
    buys_sorted: list[Transaction],
) -> list[tuple[int, Transaction]]:
    """Find repurchases within the 61-day wash-sale window around a sale."""
    window_start = sell.activity_date - timedelta(days=WASH_SALE_WINDOW_DAYS)
    window_end = sell.activity_date + timedelta(days=WASH_SALE_WINDOW_DAYS)

    return [
        (i, b)
        for i, b in enumerate(buys_sorted)
        if b.instrument == sell.instrument
        and window_start <= b.activity_date <= window_end
        and b.activity_date != sell.activity_date
    ]


def _build_wash_sale_explanation(
    sell: Transaction,
    repurchase_txn: Transaction,
    loss: float,
    disallowed_loss: float,
) -> str:
    """Build human-readable explanation with correct chronological language."""
    if repurchase_txn.activity_date < sell.activity_date:
        return (
            f"Wash sale: Bought {repurchase_txn.quantity} {sell.instrument} on "
            f"{repurchase_txn.activity_date.strftime('%m/%d/%Y')}, then sold "
            f"{sell.quantity} shares at a loss of ${loss:,.2f} on "
            f"{sell.activity_date.strftime('%m/%d/%Y')} (within 30 days of "
            f"the purchase). ${disallowed_loss:,.2f} of the loss is disallowed "
            f"and added to the cost basis of the replacement shares."
        )
    return (
        f"Wash sale: Sold {sell.quantity} {sell.instrument} on "
        f"{sell.activity_date.strftime('%m/%d/%Y')} at a loss of "
        f"${loss:,.2f}, then repurchased {repurchase_txn.quantity} shares "
        f"on {repurchase_txn.activity_date.strftime('%m/%d/%Y')} (within "
        f"30 days of the sale). ${disallowed_loss:,.2f} of the loss is "
        f"disallowed and added to the cost basis of the replacement shares."
    )


def detect_wash_sales(
    transactions: list[Transaction],
) -> list[WashSaleFlag]:
    """
    Detect wash sales in a list of realized transactions.

    Scans all Sell transactions that resulted in a loss and checks whether
    substantially identical securities were purchased within the 61-day window
    (30 days before through 30 days after the sale date).

    Args:
        transactions: All parsed transactions, including both buys and sells.

    Returns:
        List of WashSaleFlag objects for each detected wash sale.
    """
    if not transactions:
        return []

    buys = [
        t for t in transactions
        if t.trans_code in (TransCode.BUY, TransCode.BTO)
    ]
    sells = [
        t for t in transactions
        if t.trans_code in (TransCode.SELL, TransCode.STC)
    ]

    buys_sorted = sorted(buys, key=lambda t: t.activity_date)
    sells_sorted = sorted(sells, key=lambda t: t.activity_date)

    wash_sale_flags: list[WashSaleFlag] = []
    used_buy_quantities: dict[int, float] = {}

    for sell in sells_sorted:
        loss = _compute_sale_loss(sell, buys_sorted, used_buy_quantities)
        if loss <= 0:
            continue

        qualifying = _find_qualifying_repurchases(sell, buys_sorted)
        if not qualifying:
            continue

        total_repurchase_qty = sum(b.quantity for _, b in qualifying)
        if total_repurchase_qty >= sell.quantity:
            disallowed_loss = loss
        else:
            disallowed_loss = loss * (total_repurchase_qty / sell.quantity)

        earliest_repurchase = min(qualifying, key=lambda x: x[1].activity_date)
        repurchase_txn = earliest_repurchase[1]
        original_cost = repurchase_txn.price * min(repurchase_txn.quantity, sell.quantity)
        adjusted_cost = original_cost + disallowed_loss

        explanation = _build_wash_sale_explanation(sell, repurchase_txn, loss, disallowed_loss)

        wash_sale_flags.append(
            WashSaleFlag(
                symbol=sell.instrument,
                sale_date=sell.activity_date,
                sale_quantity=sell.quantity,
                sale_loss=loss,
                repurchase_date=repurchase_txn.activity_date,
                repurchase_quantity=min(total_repurchase_qty, sell.quantity),
                disallowed_loss=round(disallowed_loss, 2),
                adjusted_cost_basis=round(adjusted_cost, 2),
                explanation=explanation,
            )
        )

    return wash_sale_flags


def check_prospective_wash_sale_risk(
    symbol: str,
    transactions: list[Transaction],
    reference_date: date | None = None,
) -> tuple[bool, str]:
    """
    Check if selling a position TODAY would trigger a wash-sale due to
    recent purchases of the same symbol within the past 30 days.

    Also warns if there's a repurchase risk (user bought recently and
    might buy again).

    Args:
        symbol: The ticker symbol to check.
        transactions: All parsed transactions.
        reference_date: Date to check from (defaults to today).

    Returns:
        Tuple of (risk: bool, explanation: str).
    """
    if reference_date is None:
        reference_date = date.today()

    window_start = reference_date - timedelta(days=WASH_SALE_WINDOW_DAYS)

    # Find recent buys of this symbol within the past 30 days
    recent_buys = [
        t
        for t in transactions
        if t.instrument == symbol
        and t.trans_code in (TransCode.BUY, TransCode.BTO)
        and window_start <= t.activity_date <= reference_date
    ]

    if recent_buys:
        latest_buy = max(recent_buys, key=lambda t: t.activity_date)
        days_ago = (reference_date - latest_buy.activity_date).days
        return True, (
            f"Wash-sale risk: {symbol} was purchased {days_ago} day(s) ago on "
            f"{latest_buy.activity_date.strftime('%m/%d/%Y')}. Selling now and "
            f"repurchasing within 30 days would trigger a wash sale, disallowing "
            f"the loss deduction. Consider waiting until "
            f"{(latest_buy.activity_date + timedelta(days=31)).strftime('%m/%d/%Y')} "
            f"to avoid this."
        )

    return False, ""


def adjust_lots_for_wash_sales(
    tax_lots: list[TaxLot],
    wash_sale_flags: list[WashSaleFlag],
) -> list[TaxLot]:
    """
    Adjust the cost basis of tax lots affected by wash sales.

    When a wash sale is triggered, the disallowed loss is added to the
    cost basis of the replacement shares.

    Args:
        tax_lots: Current open tax lots.
        wash_sale_flags: Detected wash sale flags.

    Returns:
        Updated list of tax lots with adjusted cost basis.
    """
    for flag in wash_sale_flags:
        # Find the replacement lot (the repurchase)
        for lot in tax_lots:
            if (
                lot.symbol == flag.symbol
                and lot.purchase_date == flag.repurchase_date
            ):
                # Add the disallowed loss to cost basis
                lot.wash_sale_disallowed += flag.disallowed_loss
                per_share_adjustment = flag.disallowed_loss / lot.quantity if lot.quantity > 0 else 0
                lot.cost_basis_per_share += per_share_adjustment
                lot.total_cost_basis = lot.cost_basis_per_share * lot.quantity
                logger.info(
                    f"Adjusted cost basis for {lot.symbol} lot "
                    f"(purchased {lot.purchase_date}): "
                    f"+${flag.disallowed_loss:,.2f} wash-sale adjustment"
                )
                break

    return tax_lots
