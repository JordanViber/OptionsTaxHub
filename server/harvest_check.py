"""Year-end harvest check for open losing stock lots.

For each ticker, look for replacement acquisitions in the 30 days before
or after a prospective sale. The window is the JOR-50 wash window
(``WASH_SALE_WINDOW_DAYS``). A same-account buy on the sale date is not a
replacement. A buy in another account on that date is, including an IRA
buy, which is a permanent disallowance (JOR-175). Only the same ticker
and its bought calls or short puts count. A different fund, including a
cash-parking fund, is not a replacement.

This does not rewrite wash flags, lot basis, or tax totals. The sentences
are informational. TSLA and SPCX never get a harvest or sell prompt.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from account_labels import account_display_name, is_ira_account
from models import (
    AssetType,
    HarvestBuyKind,
    HarvestCheck,
    HarvestTriggerBuy,
    TaxLot,
    Transaction,
    TransCode,
)
from wash_sale import WASH_SALE_WINDOW_DAYS

# These names never get a harvest or sell prompt. The check can still
# report a count or a disallowed amount.
NO_HARVEST_PROMPT_SYMBOLS = frozenset({"TSLA", "SPCX"})

# Pinned so the in-app sample's disallowed amount and clean date do not
# move when the desk is opened after the fixture's last trade.
SAMPLE_HARVEST_AS_OF = date(2026, 8, 12)


def harvest_as_of(sample: bool) -> date:
    """Sample checks stay on the fixture date. A real book uses today."""
    if sample:
        return SAMPLE_HARVEST_AS_OF
    return date.today()

_OPTION_SHARE_MULTIPLIER = 100
_QTY_EPS = 1e-4
_CALL = re.compile(r"\bcalls?\b", re.IGNORECASE)
_PUT = re.compile(r"\bputs?\b", re.IGNORECASE)

_CLEAN_LINE = (
    "No replacement buys found in the 30 days either side; "
    "may be clean to harvest."
)
_NO_PROMPT_CLEAN_LINE = (
    "No replacement buys found in the 30 days either side. "
    "A wash-sale rule may apply, ask a tax pro."
)


def _cents(value: float) -> float:
    return round(float(value), 2)


def _symbol(value: str | None) -> str:
    return (value or "").strip().upper()


def _fmt_money(amount: float) -> str:
    rounded = _cents(amount)
    if abs(rounded - round(rounded)) < 0.001:
        return f"${int(round(rounded)):,}"
    return f"${rounded:,.2f}"


def _account(value: str | None) -> str:
    return value or ""


def _kind(txn: Transaction) -> HarvestBuyKind | None:
    """Replacement type, or None when this trade is not a replacement."""
    description = txn.description or ""
    if txn.asset_type == AssetType.OPTION or txn.trans_code in (
        TransCode.BTO,
        TransCode.STO,
    ):
        if txn.trans_code == TransCode.BTO and _CALL.search(description):
            return "bought_call"
        if txn.trans_code == TransCode.STO and _PUT.search(description):
            return "short_put"
        return None
    if txn.trans_code != TransCode.BUY or txn.asset_type != AssetType.STOCK:
        return None
    lowered = description.lower()
    if "dividend reinvestment" in lowered or re.search(r"\bdrip\b", lowered):
        return "dividend_reinvestment"
    if "recurring" in lowered:
        return "recurring_buy"
    if abs(float(txn.quantity) - round(float(txn.quantity))) > _QTY_EPS:
        return "fractional_buy"
    return "regular_buy"


def _share_equivalent(txn: Transaction, kind: HarvestBuyKind) -> float:
    if kind in ("bought_call", "short_put"):
        return float(txn.quantity) * _OPTION_SHARE_MULTIPLIER
    return float(txn.quantity)


def _in_window(activity: date, as_of: date) -> bool:
    start = as_of - timedelta(days=WASH_SALE_WINDOW_DAYS)
    end = as_of + timedelta(days=WASH_SALE_WINDOW_DAYS)
    return start <= activity <= end


def _losing_stock_lots(tax_lots: list[TaxLot], symbol: str) -> list[TaxLot]:
    rows: list[TaxLot] = []
    for lot in tax_lots:
        if lot.asset_type != AssetType.STOCK:
            continue
        if _symbol(lot.symbol) != symbol:
            continue
        if lot.is_short_position:
            continue
        if is_ira_account(lot.account_label):
            continue
        if lot.quantity <= _QTY_EPS:
            continue
        loss = lot.unrealized_pnl
        if loss is None or loss >= -0.004:
            continue
        rows.append(lot)
    return rows


def _harvested_lots(lots: list[TaxLot], as_of: date) -> list[TaxLot]:
    """Lots a sale on ``as_of`` would close.

    Shares bought before the window are the position. Shares bought inside
    the window are replacement traps unless they are the only losing lots,
    in which case selling them is not a wash against their own purchase.
    """
    window_start = as_of - timedelta(days=WASH_SALE_WINDOW_DAYS)
    older = [lot for lot in lots if lot.purchase_date < window_start]
    if older:
        return older
    return list(lots)


def _line(symbol: str, *, clean: bool, disallowed: float, open_loss: float) -> str:
    if symbol in NO_HARVEST_PROMPT_SYMBOLS:
        if clean:
            return _NO_PROMPT_CLEAN_LINE
        return (
            f"{_fmt_money(disallowed)} of your {_fmt_money(open_loss)} loss "
            "may be disallowed. A wash-sale rule may apply, ask a tax pro."
        )
    if clean:
        return _CLEAN_LINE
    return (
        f"{_fmt_money(disallowed)} of your {_fmt_money(open_loss)} loss "
        "may be disallowed"
    )


def _acquisition_fill(lot: TaxLot) -> float:
    """Buy price before a wash-sale basis add-on.

    ``adjust_lots_for_wash_sales`` raises ``cost_basis_per_share``. The
    CSV fill is still the price on the buy that created the lot.
    """
    quantity = float(lot.quantity)
    disallowed = float(lot.wash_sale_disallowed or 0)
    if quantity > _QTY_EPS and disallowed > 0:
        return float(lot.cost_basis_per_share) - (disallowed / quantity)
    return float(lot.cost_basis_per_share)


def _exclude_shares_being_sold(
    txn: Transaction,
    harvested: list[TaxLot],
    remaining: dict[int, float],
) -> float:
    """Drop the part of this buy that is the lot being sold."""
    if txn.asset_type != AssetType.STOCK:
        return float(txn.quantity)
    left = float(txn.quantity)
    for index, lot in enumerate(harvested):
        if left <= _QTY_EPS:
            break
        room = remaining.get(index, 0.0)
        if room <= _QTY_EPS:
            continue
        if _account(lot.account_label) != _account(txn.account_label):
            continue
        if lot.purchase_date != txn.activity_date:
            continue
        if abs(_acquisition_fill(lot) - float(txn.price)) > 0.02:
            continue
        take = min(left, room)
        remaining[index] = room - take
        left -= take
    return left


def _triggers_for(
    symbol: str,
    transactions: list[Transaction],
    harvested: list[TaxLot],
    as_of: date,
) -> list[tuple[Transaction, HarvestBuyKind, float]]:
    sale_accounts = {_account(lot.account_label) for lot in harvested}
    remaining = {index: float(lot.quantity) for index, lot in enumerate(harvested)}
    found: list[tuple[Transaction, HarvestBuyKind, float]] = []
    for txn in transactions:
        if _symbol(txn.instrument) != symbol:
            continue
        if not _in_window(txn.activity_date, as_of):
            continue
        kind = _kind(txn)
        if kind is None:
            continue
        same_account = _account(txn.account_label) in sale_accounts
        # JOR-50 skips a same-account fill on the sale date. JOR-175 keeps
        # a same-day buy in another account, including an IRA.
        if txn.activity_date == as_of and same_account:
            continue
        if kind in ("bought_call", "short_put"):
            shares = _share_equivalent(txn, kind)
        else:
            shares = _exclude_shares_being_sold(txn, harvested, remaining)
        if shares <= _QTY_EPS:
            continue
        found.append((txn, kind, shares))
    found.sort(
        key=lambda item: (
            item[0].activity_date,
            _account(item[0].account_label),
            item[1],
        )
    )
    return found


def _allocate(
    found: list[tuple[Transaction, HarvestBuyKind, float]],
    *,
    open_loss: float,
    quantity: float,
) -> tuple[float, list[HarvestTriggerBuy]]:
    covered = min(quantity, sum(shares for _txn, _kind, shares in found))
    if covered <= _QTY_EPS or open_loss <= 0:
        disallowed = 0.0
    elif covered + _QTY_EPS >= quantity:
        disallowed = _cents(open_loss)
    else:
        disallowed = _cents(open_loss * (covered / quantity))
    remaining_shares = covered
    remaining_dollars = disallowed
    rows: list[HarvestTriggerBuy] = []
    for txn, kind, shares in found:
        if remaining_shares <= _QTY_EPS or disallowed <= 0:
            portion = 0.0
        else:
            take = min(shares, remaining_shares)
            if take >= remaining_shares - _QTY_EPS:
                portion = remaining_dollars
            else:
                portion = _cents(disallowed * (take / covered))
                remaining_dollars = _cents(remaining_dollars - portion)
            remaining_shares -= take
        permanent = is_ira_account(txn.account_label)
        rows.append(
            HarvestTriggerBuy(
                activity_date=txn.activity_date,
                kind=kind,
                quantity=round(float(txn.quantity), 4),
                share_equivalent=round(shares, 4),
                disallowed_loss=_cents(portion),
                account_label=txn.account_label or None,
                account_name=account_display_name(txn.account_label),
                permanent_disallowance=permanent,
            )
        )
    return disallowed, rows


def _split_symbols(transactions: list[Transaction]) -> set[str]:
    """Symbols whose open lots and buy rows are in different share units.

    A split adjusts lot quantity and leaves the original buy quantity.
    The harvest suggestions already skip those symbols. This check does too,
    instead of allocating a loss against the pre-split share count.
    """
    return {
        _symbol(txn.instrument)
        for txn in transactions
        if txn.asset_type == AssetType.STOCK
        and txn.trans_code in (TransCode.SPR, TransCode.OCA)
        and _symbol(txn.instrument)
    }


def build_harvest_checks(
    tax_lots: list[TaxLot],
    transactions: list[Transaction],
    *,
    as_of: date,
) -> list[HarvestCheck]:
    """One row per ticker that has an open taxable stock loss."""
    skipped = _split_symbols(transactions)
    symbols = sorted(
        {
            _symbol(lot.symbol)
            for lot in tax_lots
            if lot.asset_type == AssetType.STOCK
            and _symbol(lot.symbol)
            and _symbol(lot.symbol) not in skipped
        }
    )
    checks: list[HarvestCheck] = []
    for symbol in symbols:
        losing = _losing_stock_lots(tax_lots, symbol)
        if not losing:
            continue
        harvested = _harvested_lots(losing, as_of)
        open_loss = _cents(sum(abs(float(lot.unrealized_pnl or 0)) for lot in harvested))
        quantity = sum(float(lot.quantity) for lot in harvested)
        if open_loss <= 0 or quantity <= _QTY_EPS:
            continue
        found = _triggers_for(symbol, transactions, harvested, as_of)
        disallowed, triggers = _allocate(found, open_loss=open_loss, quantity=quantity)
        clean = disallowed <= 0 or not triggers
        blocked = symbol in NO_HARVEST_PROMPT_SYMBOLS
        earliest: date | None = None
        safe: date | None = None
        if not clean and not blocked:
            latest = max(row.activity_date for row in triggers)
            earliest = latest + timedelta(days=WASH_SALE_WINDOW_DAYS + 1)
            safe = earliest + timedelta(days=WASH_SALE_WINDOW_DAYS + 1)
        checks.append(
            HarvestCheck(
                symbol=symbol,
                trigger_count=len(triggers),
                clean=clean,
                open_loss=open_loss,
                disallowed_loss=0.0 if clean else disallowed,
                earliest_clean_sell_date=earliest,
                safe_rebuy_date=safe,
                triggers=[] if clean else triggers,
                permanent_disallowance=any(
                    row.permanent_disallowance for row in triggers
                ),
                line=_line(
                    symbol,
                    clean=clean,
                    disallowed=disallowed,
                    open_loss=open_loss,
                ),
                as_of=as_of,
            )
        )
    return checks


def redact_harvest_checks(checks: list[HarvestCheck]) -> list[HarvestCheck]:
    """Free view: per-ticker counts. Dollars, dates, and buy rows stay off."""
    redacted: list[HarvestCheck] = []
    for row in checks:
        redacted.append(
            row.model_copy(
                update={
                    "open_loss": None,
                    "disallowed_loss": None,
                    "earliest_clean_sell_date": None,
                    "safe_rebuy_date": None,
                    "triggers": [],
                    "permanent_disallowance": False,
                    "line": "",
                }
            )
        )
    return redacted
