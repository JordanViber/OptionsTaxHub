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


def _matches_lot_fill(txn: Transaction, lot: TaxLot) -> bool:
    """True when this row is the stock buy that opened the lot."""
    if txn.trans_code != TransCode.BUY or txn.asset_type != AssetType.STOCK:
        return False
    if _symbol(txn.instrument) != _symbol(lot.symbol):
        return False
    if _account(lot.account_label) != _account(txn.account_label):
        return False
    if lot.purchase_date != txn.activity_date:
        return False
    return abs(_acquisition_fill(lot) - float(txn.price)) <= 0.02


def _own_fill_indexes(
    transactions: list[Transaction],
    harvested: list[TaxLot],
    as_of: date,
) -> tuple[set[int], set[int]]:
    """Buys that opened a harvested lot, including shares already sold.

    A partial sale leaves the open lot smaller than the CSV buy. Those
    sold shares are not a replacement. When several same-day fills cover
    the lot, the smallest one is the fill this lot came from. Returns
    the transaction indexes to skip and the lot indexes those fills cover.
    """
    used: set[int] = set()
    covered: set[int] = set()
    for lot_index, lot in enumerate(harvested):
        best: int | None = None
        best_qty = 0.0
        for txn_index, txn in enumerate(transactions):
            if txn_index in used:
                continue
            if not _in_window(txn.activity_date, as_of):
                continue
            if not _matches_lot_fill(txn, lot):
                continue
            qty = float(txn.quantity)
            if qty + _QTY_EPS < float(lot.quantity):
                continue
            if best is None or qty < best_qty:
                best = txn_index
                best_qty = qty
        if best is not None:
            used.add(best)
            covered.add(lot_index)
    return used, covered


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
        if not _matches_lot_fill(txn, lot):
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
    own_fills, covered_lots = _own_fill_indexes(transactions, harvested, as_of)
    remaining = {
        index: 0.0 if index in covered_lots else float(lot.quantity)
        for index, lot in enumerate(harvested)
    }
    found: list[tuple[Transaction, HarvestBuyKind, float]] = []
    for txn_index, txn in enumerate(transactions):
        if txn_index in own_fills:
            continue
        if _symbol(txn.instrument) != symbol:
            continue
        if not _in_window(txn.activity_date, as_of):
            continue
        kind = _kind(txn)
        if kind is None:
            continue
        buy_account = _account(txn.account_label)
        # JOR-50 skips a same-account fill on the sale date. A buy in one
        # sale account still replaces lots held in the other accounts.
        other_qty: float | None = None
        if txn.activity_date == as_of and buy_account in sale_accounts:
            other_qty = sum(
                float(lot.quantity)
                for lot in harvested
                if _account(lot.account_label) != buy_account
            )
            if other_qty <= _QTY_EPS:
                continue
        if kind in ("bought_call", "short_put"):
            shares = _share_equivalent(txn, kind)
        else:
            shares = _exclude_shares_being_sold(txn, harvested, remaining)
        if other_qty is not None:
            shares = min(shares, other_qty)
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


def _trigger_row(
    txn: Transaction,
    kind: HarvestBuyKind,
    shares: float,
    portion: float,
) -> HarvestTriggerBuy:
    return HarvestTriggerBuy(
        activity_date=txn.activity_date,
        kind=kind,
        quantity=round(float(txn.quantity), 4),
        share_equivalent=round(shares, 4),
        disallowed_loss=_cents(portion),
        account_label=txn.account_label or None,
        account_name=account_display_name(txn.account_label),
        permanent_disallowance=is_ira_account(txn.account_label),
    )


def _same_sale_account(txn: Transaction, sale_accounts: set[str], as_of: date) -> bool:
    return txn.activity_date == as_of and _account(txn.account_label) in sale_accounts


def _allocate_found(
    found: list[tuple[Transaction, HarvestBuyKind, float]],
    harvested: list[TaxLot],
    *,
    as_of: date,
    open_loss: float,
    quantity: float,
) -> tuple[float, list[HarvestTriggerBuy]]:
    """Price every buy from each lot's own remaining loss.

    Ordinary buys run first. They take lots a same-day sale-account buy
    cannot replace, then the other lots. Same-day buys then take whatever
    is left in the other accounts. A share is never priced at the ticker
    average, and a lot's dollars are spent once.
    """
    del open_loss, quantity
    sale_accounts = {_account(lot.account_label) for lot in harvested}
    blocked_accounts = {
        _account(txn.account_label)
        for txn, _kind, _shares in found
        if _same_sale_account(txn, sale_accounts, as_of)
    }
    lot_order = sorted(
        range(len(harvested)),
        key=lambda index: (
            0 if _account(harvested[index].account_label) in blocked_accounts else 1,
            _account(harvested[index].account_label),
            harvested[index].purchase_date,
            index,
        ),
    )
    pool_qty = {index: float(lot.quantity) for index, lot in enumerate(harvested)}
    pool_loss = {
        index: abs(float(lot.unrealized_pnl or 0))
        for index, lot in enumerate(harvested)
    }

    def take_loss(txn: Transaction, shares: float) -> float:
        eligible = [
            index
            for index in lot_order
            if pool_qty[index] > _QTY_EPS
            and (
                not _same_sale_account(txn, sale_accounts, as_of)
                or _account(harvested[index].account_label) != _account(txn.account_label)
            )
        ]
        left = shares
        portion = 0.0
        for index in eligible:
            if left <= _QTY_EPS:
                break
            avail = pool_qty[index]
            take = min(left, avail)
            if take >= avail - _QTY_EPS:
                slice_loss = _cents(pool_loss[index])
                pool_qty[index] = 0.0
                pool_loss[index] = 0.0
            else:
                slice_loss = _cents(pool_loss[index] * (take / avail))
                pool_qty[index] = avail - take
                pool_loss[index] = _cents(pool_loss[index] - slice_loss)
            portion = _cents(portion + slice_loss)
            left -= take
        return portion

    ordinary = [
        item
        for item in found
        if not _same_sale_account(item[0], sale_accounts, as_of)
    ]
    cross_day = [
        item for item in found if _same_sale_account(item[0], sale_accounts, as_of)
    ]
    priced = {
        id(txn): _trigger_row(txn, kind, shares, take_loss(txn, shares))
        for txn, kind, shares in [*ordinary, *cross_day]
    }
    ordered = [priced[id(txn)] for txn, _kind, _shares in found]
    return _cents(sum(row.disallowed_loss for row in ordered)), ordered


def _split_symbols(transactions: list[Transaction], as_of: date) -> set[str]:
    """Symbols with a pre-split buy still inside the harvest window.

    A split multiplies lots that were already open and leaves the CSV buy
    quantity unchanged. A buy after the split is already in current units,
    so an older split does not hide the row. A corporate action does not
    change the share count.
    """
    latest_split: dict[str, date] = {}
    for txn in transactions:
        if txn.asset_type != AssetType.STOCK or txn.trans_code != TransCode.SPR:
            continue
        symbol = _symbol(txn.instrument)
        if not symbol:
            continue
        previous = latest_split.get(symbol)
        if previous is None or txn.activity_date > previous:
            latest_split[symbol] = txn.activity_date
    skipped: set[str] = set()
    for txn in transactions:
        symbol = _symbol(txn.instrument)
        split_on = latest_split.get(symbol)
        if split_on is None or txn.activity_date >= split_on:
            continue
        if not _in_window(txn.activity_date, as_of):
            continue
        if _kind(txn) not in (
            "regular_buy",
            "recurring_buy",
            "fractional_buy",
            "dividend_reinvestment",
        ):
            continue
        skipped.add(symbol)
    return skipped


def build_harvest_checks(
    tax_lots: list[TaxLot],
    transactions: list[Transaction],
    *,
    as_of: date,
) -> list[HarvestCheck]:
    """One row per ticker that has an open taxable stock loss."""
    skipped = _split_symbols(transactions, as_of)
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
        disallowed, triggers = _allocate_found(
            found,
            harvested,
            as_of=as_of,
            open_loss=open_loss,
            quantity=quantity,
        )
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
