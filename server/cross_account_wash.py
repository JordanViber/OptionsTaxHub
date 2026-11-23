"""Cross-account wash estimates for the JOR-50 chain view.

A loss in one labeled account and a buy of the same stock in another
account, inside the 61-day window, becomes its own chain. It does not
enter wash_sale_flags, lot basis, or tax totals. An IRA buy is a
permanent disallowance: the loss does not add to the IRA's basis.

Same-account uploads, and any book with a single label, return no rows.
"""

from __future__ import annotations

from datetime import date, timedelta

from account_labels import account_display_name, group_by_account, is_ira_account
from harvest_check import _shares_still_held
from models import (
    AssetType,
    Transaction,
    TransCode,
    WashReplacementBuy,
    WashSaleChain,
    WashSaleFlag,
    WashSaleHop,
)
from wash_sale import WASH_SALE_WINDOW_DAYS, _compute_sale_loss

_BUY_CODES = (TransCode.BUY, TransCode.BTO)
_SELL_CODES = (TransCode.SELL, TransCode.STC)
_QTY_EPS = 1e-4


def _cents(value: float) -> float:
    return round(float(value), 2)


def _symbol(value: str | None) -> str:
    return (value or "").strip().upper()


def _qty_close(left: float, right: float) -> bool:
    return abs(float(left) - float(right)) <= _QTY_EPS


def _fmt_money(amount: float) -> str:
    rounded = _cents(amount)
    if abs(rounded - round(rounded)) < 0.001:
        return f"${int(round(rounded)):,}"
    return f"${rounded:,.2f}"


def _fmt_qty(quantity: float) -> str:
    rounded = round(float(quantity), 4)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    return f"{rounded:.4f}".rstrip("0").rstrip(".")


_PRE_ARTICLED = frozenset({"this account", "another account"})
_IRA_PERMANENT_LINE = (
    "Permanent: a loss washed by an IRA buy is lost for good. "
    "It does not add to the IRA's basis."
)


def _article_phrase(name: str) -> str:
    """'an Individual', 'an IRA (traditional)', 'an Other/agentic'."""
    if name in _PRE_ARTICLED:
        return name
    article = "an" if name[:1].lower() in "aeiou" else "a"
    return f"{article} {name}"


def _sentence_phrase(name: str) -> str:
    phrase = _article_phrase(name)
    return phrase[:1].upper() + phrase[1:]


def _stock_buys(transactions: list[Transaction]) -> list[Transaction]:
    return sorted(
        (
            txn
            for txn in transactions
            if txn.trans_code in _BUY_CODES and txn.asset_type == AssetType.STOCK
        ),
        key=lambda txn: txn.activity_date,
    )


def _flag_matches_sale(flag: WashSaleFlag, sell: Transaction) -> bool:
    if _symbol(flag.symbol) != _symbol(sell.instrument):
        return False
    if flag.sale_date != sell.activity_date:
        return False
    if not _qty_close(flag.sale_quantity, sell.quantity):
        return False
    flag_label = getattr(flag, "account_label", None) or ""
    sell_label = sell.account_label or ""
    if flag_label and sell_label and flag_label != sell_label:
        return False
    return True


def _assign_same_account_disallowed(
    sells: list[Transaction],
    losses: list[float],
    flags: list[WashSaleFlag],
) -> list[float]:
    """Give each same-account flag to one sale.

    Two sales that share a date and quantity used to each receive the sum of
    every matching flag. That treated both as fully disallowed and dropped
    the uncovered cross-account remainder. Identical sales take flags one
    at a time. A single sale still takes every flag up to its own loss.
    """
    assigned = [0.0] * len(sells)
    used: set[int] = set()
    buckets: dict[tuple, list[int]] = {}
    for index, sell in enumerate(sells):
        key = (
            _symbol(sell.instrument),
            sell.activity_date,
            round(float(sell.quantity), 4),
            sell.account_label or "",
        )
        buckets.setdefault(key, []).append(index)

    def matching_flags(sell: Transaction) -> list[int]:
        return [
            flag_index
            for flag_index, flag in enumerate(flags)
            if _flag_matches_sale(flag, sell)
        ]

    for indexes in buckets.values():
        matching = matching_flags(sells[indexes[0]])
        if len(indexes) == 1:
            sell_index = indexes[0]
            room = losses[sell_index]
            total = 0.0
            for flag_index in matching:
                if total >= room - 0.01:
                    break
                amount = float(flags[flag_index].disallowed_loss)
                used.add(flag_index)
                total += amount
            assigned[sell_index] = _cents(min(total, room))
            continue
        while True:
            progressed = False
            for sell_index in indexes:
                room = losses[sell_index] - assigned[sell_index]
                if room <= 0.01:
                    continue
                choice = next(
                    (
                        flag_index
                        for flag_index in matching
                        if flag_index not in used
                    ),
                    None,
                )
                if choice is None:
                    continue
                amount = min(float(flags[choice].disallowed_loss), room)
                used.add(choice)
                assigned[sell_index] = _cents(assigned[sell_index] + amount)
                progressed = True
            if not progressed:
                break
    return assigned


def _consumed_by_same_account_flags(
    groups: list[list[Transaction]],
    flags: list[WashSaleFlag],
) -> dict[int, float]:
    """Shares a same-account wash flag already uses as its replacement.

    ``repurchase_quantity`` is every same-account buy in the 61-day window,
    while ``repurchase_date`` is only the earliest of those fills. Walk the
    window buys in date order and reserve up to that quantity. A later loss
    in another account can take only the shares the flag did not already claim.
    """
    consumed: dict[int, float] = {}
    for flag in flags:
        need = float(flag.repurchase_quantity or 0)
        if need <= _QTY_EPS:
            continue
        label = getattr(flag, "account_label", None) or ""
        symbol = _symbol(flag.symbol)
        window_start = flag.sale_date - timedelta(days=WASH_SALE_WINDOW_DAYS)
        window_end = flag.sale_date + timedelta(days=WASH_SALE_WINDOW_DAYS)
        for group in groups:
            if not group or (group[0].account_label or "") != label:
                continue
            buys = [
                txn
                for txn in group
                if txn.trans_code in _BUY_CODES
                and txn.asset_type == AssetType.STOCK
                and _symbol(txn.instrument) == symbol
                and window_start <= txn.activity_date <= window_end
                and txn.activity_date != flag.sale_date
            ]
            buys.sort(key=lambda txn: txn.activity_date)
            for txn in buys:
                if need <= _QTY_EPS:
                    break
                already = consumed.get(id(txn), 0.0)
                room = txn.quantity - already
                take = min(room, need)
                if take <= _QTY_EPS:
                    continue
                consumed[id(txn)] = already + take
                need -= take
    return consumed


def _unclaimed_replacement_qty(
    txn: Transaction,
    txn_index: int | None,
    sale_date: date,
    held: dict[int, float],
    consumed: dict[int, float],
) -> float:
    """Shares of this fill not already reserved, and still open on the sale.

    ``held`` is the per-account FIFO from ``_shares_still_held`` through
    ``sale_date``. A stock buy on or before the sale counts only for the
    shares of that fill the sale left open. A later buy counts in full,
    because a sale after the loss does not unwind the replacement. Stock
    BTO rows are not in that FIFO, so they stay at the fill quantity.
    """
    open_qty = float(txn.quantity)
    if (
        txn.trans_code == TransCode.BUY
        and txn.activity_date <= sale_date
        and txn_index is not None
    ):
        open_qty = min(open_qty, held.get(txn_index, 0.0))
    return open_qty - consumed.get(id(txn), 0.0)


def _plain_english(
    *,
    sell: Transaction,
    loss: float,
    covered: float,
    estimated: float,
    sale_name: str,
    buy_names: str,
    permanent_amount: float,
    deferred_amount: float,
) -> str:
    text = (
        f"Not on your 1099: cross-account. "
        f"{_sentence_phrase(sale_name)} sale of {_fmt_qty(sell.quantity)} {_symbol(sell.instrument)} "
        f"on {sell.activity_date.strftime('%b')} {sell.activity_date.day} "
        f"lost {_fmt_money(loss)}. "
        f"A buy in {buy_names} within 30 days before or after that sale "
        f"covers {_fmt_qty(covered)} shares. "
        f"Estimated disallowed amount {_fmt_money(estimated)}."
    )
    if permanent_amount > 0 and deferred_amount > 0:
        text += (
            f" {_IRA_PERMANENT_LINE} "
            f"{_fmt_money(deferred_amount)} is added to the taxable replacement's basis."
        )
    elif permanent_amount > 0:
        text += f" {_IRA_PERMANENT_LINE}"
    return text


def build_cross_account_chains(
    transactions: list[Transaction],
    wash_sale_flags: list[WashSaleFlag] | None = None,
    *,
    tax_year: int | None = None,
) -> list[WashSaleChain]:
    """Chain rows for losses replaced in a different labeled account."""
    groups = group_by_account(transactions)
    if len(groups) <= 1:
        return []

    flags = list(wash_sale_flags or [])
    chains: list[WashSaleChain] = []
    # Same-account flags already claim their replacement shares. What remains
    # can cover a loss in another account, and only once.
    consumed = _consumed_by_same_account_flags(groups, flags)
    txn_index = {id(txn): index for index, txn in enumerate(transactions)}
    held_on_date: dict[tuple[str, date], dict[int, float]] = {}
    planned: list[tuple[Transaction, list[Transaction], float, float, float]] = []
    for group in groups:
        # A loss inside an IRA is not deducted, so it is not a wash to disallow.
        # IRA buys still replace taxable sales further down.
        if group and is_ira_account(group[0].account_label):
            continue
        buys = _stock_buys(group)
        sells = [
            txn
            for txn in group
            if txn.trans_code in _SELL_CODES and txn.asset_type == AssetType.STOCK
        ]
        sells.sort(key=lambda txn: txn.activity_date)
        used: dict[int, float] = {}
        priced: list[tuple[Transaction, float]] = []
        for sell in sells:
            # Replay every sale so an earlier close consumes its lot. Only a
            # loss in the requested tax year becomes a cross-account candidate.
            loss = _compute_sale_loss(sell, buys, used)
            if tax_year is not None and sell.activity_date.year != tax_year:
                continue
            if loss <= _QTY_EPS:
                continue
            priced.append((sell, _cents(loss)))
        allowances = _assign_same_account_disallowed(
            [sell for sell, _loss in priced],
            [loss for _sell, loss in priced],
            flags,
        )
        for (sell, loss), already in zip(priced, allowances):
            remaining_loss = _cents(max(0.0, loss - already))
            if remaining_loss <= 0:
                continue
            replaced_shares = sell.quantity * min(1.0, already / loss) if loss else 0.0
            shares_left = max(0.0, sell.quantity - replaced_shares)
            if shares_left <= _QTY_EPS:
                continue
            planned.append((sell, group, loss, remaining_loss, shares_left))
    planned.sort(
        key=lambda item: (item[0].activity_date, item[0].account_label or "", _symbol(item[0].instrument))
    )
    for sell, group, loss, remaining_loss, shares_left in planned:
        window_start = sell.activity_date - timedelta(days=WASH_SALE_WINDOW_DAYS)
        window_end = sell.activity_date + timedelta(days=WASH_SALE_WINDOW_DAYS)
        symbol = _symbol(sell.instrument)
        held_key = (symbol, sell.activity_date)
        held = held_on_date.get(held_key)
        if held is None:
            held = _shares_still_held(transactions, symbol, sell.activity_date)
            held_on_date[held_key] = held
        replacements: list[Transaction] = []
        for other in groups:
            if other is group:
                continue
            for txn in other:
                if txn.trans_code not in _BUY_CODES:
                    continue
                if txn.asset_type != AssetType.STOCK:
                    continue
                if _symbol(txn.instrument) != symbol:
                    continue
                # A same-day buy in another account cannot be the lot this
                # sale closed. It stays inside the replacement window.
                if not window_start <= txn.activity_date <= window_end:
                    continue
                remaining_qty = _unclaimed_replacement_qty(
                    txn,
                    txn_index.get(id(txn)),
                    sell.activity_date,
                    held,
                    consumed,
                )
                if remaining_qty <= _QTY_EPS:
                    continue
                replacements.append(txn)
        if not replacements:
            continue
        replacements.sort(key=lambda txn: (txn.activity_date, txn.account_label or ""))
        allocated: list[tuple[Transaction, float]] = []
        still = shares_left
        for txn in replacements:
            if still <= _QTY_EPS:
                break
            remaining_qty = _unclaimed_replacement_qty(
                txn,
                txn_index.get(id(txn)),
                sell.activity_date,
                held,
                consumed,
            )
            take = min(still, remaining_qty)
            if take <= _QTY_EPS:
                continue
            allocated.append((txn, take))
            consumed[id(txn)] = consumed.get(id(txn), 0.0) + take
            still -= take
        covered = shares_left - still
        if covered <= _QTY_EPS:
            continue
        estimated = _cents(min(remaining_loss, loss * (covered / sell.quantity)))
        if estimated <= 0:
            continue
        sale_label = sell.account_label or None
        sale_name = account_display_name(sale_label) or "this account"
        buy_names = ", ".join(
            dict.fromkeys(
                _article_phrase(
                    account_display_name(buy.account_label) or "another account"
                )
                for buy, _take in allocated
            )
        )
        buy_rows: list[WashReplacementBuy] = []
        remaining_dollars = estimated
        permanent_amount = 0.0
        for index, (buy, take) in enumerate(allocated):
            if index == len(allocated) - 1:
                portion = remaining_dollars
            else:
                portion = _cents(estimated * (take / covered))
                remaining_dollars = _cents(remaining_dollars - portion)
            buy_permanent = is_ira_account(buy.account_label)
            if buy_permanent:
                permanent_amount += portion
            cash = _cents(buy.price * take)
            explained = cash if buy_permanent else _cents(cash + portion)
            buy_rows.append(
                WashReplacementBuy(
                    activity_date=buy.activity_date,
                    quantity=buy.quantity,
                    price=buy.price,
                    cash_cost=_cents(buy.price * buy.quantity),
                    allocated_quantity=round(take, 4),
                    allocated_disallowed=portion,
                    explained_basis=explained,
                    account_label=buy.account_label or None,
                    account_name=account_display_name(buy.account_label),
                )
            )
        permanent_amount = _cents(permanent_amount)
        deferred_amount = _cents(max(0.0, estimated - permanent_amount))
        permanent = permanent_amount > 0
        chain_id = (
            f"cross-{symbol}-{sell.activity_date.isoformat()}-"
            f"{sale_label or 'account'}-{len(chains)}"
        )
        english = _plain_english(
            sell=sell,
            loss=loss,
            covered=covered,
            estimated=estimated,
            sale_name=sale_name,
            buy_names=buy_names,
            permanent_amount=permanent_amount,
            deferred_amount=deferred_amount,
        )
        hop = WashSaleHop(
            hop_id=f"{chain_id}:h0",
            hop_index=0,
            symbol=symbol,
            asset_type="stock",
            sale_date=sell.activity_date,
            sale_quantity=sell.quantity,
            proceeds=_cents(sell.quantity * sell.price),
            cost_basis=_cents(sell.quantity * sell.price + loss),
            loss=loss,
            replacement_buys=buy_rows,
            disallowed_loss=estimated,
            engine_disallowed_loss=None,
            form_1099_wash=None,
            engine_adjusted_cost_basis=None,
            plain_english=english,
            reconcile_status="not_on_1099",
            account_label=sale_label,
            account_name=sale_name if sale_label else None,
            cross_account=True,
            permanent_disallowance=permanent,
        )
        chains.append(
            WashSaleChain(
                chain_id=chain_id,
                symbol=symbol,
                reconcile_status="not_on_1099",
                plain_english=english,
                hops=[hop],
                cross_account=True,
                permanent_disallowance=permanent,
            )
        )
    return chains


def _sale_label(
    transactions: list[Transaction],
    *,
    symbol: str,
    sale_date,
    quantity: float,
) -> str | None:
    labels: list[str] = []
    for txn in transactions:
        if txn.trans_code not in _SELL_CODES:
            continue
        if txn.asset_type != AssetType.STOCK:
            continue
        if _symbol(txn.instrument) != _symbol(symbol):
            continue
        if txn.activity_date != sale_date:
            continue
        if not _qty_close(txn.quantity, quantity):
            continue
        label = txn.account_label or ""
        if label and label not in labels:
            labels.append(label)
    if len(labels) == 1:
        return labels[0]
    return None


def _buy_label(
    transactions: list[Transaction],
    *,
    symbol: str,
    activity_date,
    quantity: float,
    prefer: str | None,
) -> str | None:
    labels: list[str] = []
    for txn in transactions:
        if txn.trans_code not in _BUY_CODES:
            continue
        if _symbol(txn.instrument) != _symbol(symbol):
            continue
        if txn.activity_date != activity_date:
            continue
        if not _qty_close(txn.quantity, quantity):
            continue
        label = txn.account_label or ""
        if label and label not in labels:
            labels.append(label)
    if prefer and prefer in labels:
        return prefer
    if len(labels) == 1:
        return labels[0]
    return None


def stamp_chain_accounts(
    chains: list[WashSaleChain],
    transactions: list[Transaction],
) -> list[WashSaleChain]:
    """Copy export labels onto existing chain rows. Does not change copy or dollars."""
    if not any(txn.account_label for txn in transactions):
        return chains
    stamped: list[WashSaleChain] = []
    for chain in chains:
        if chain.cross_account:
            stamped.append(chain)
            continue
        hops: list[WashSaleHop] = []
        for hop in chain.hops:
            sale_label = hop.account_label or _sale_label(
                transactions,
                symbol=hop.symbol,
                sale_date=hop.sale_date,
                quantity=hop.sale_quantity,
            )
            buys = []
            for buy in hop.replacement_buys:
                buy_label = buy.account_label or _buy_label(
                    transactions,
                    symbol=hop.symbol,
                    activity_date=buy.activity_date,
                    quantity=buy.quantity,
                    prefer=sale_label,
                )
                buys.append(
                    buy.model_copy(
                        update={
                            "account_label": buy_label,
                            "account_name": account_display_name(buy_label),
                        }
                    )
                )
            hops.append(
                hop.model_copy(
                    update={
                        "replacement_buys": buys,
                        "account_label": sale_label,
                        "account_name": account_display_name(sale_label),
                    }
                )
            )
        stamped.append(chain.model_copy(update={"hops": hops}))
    return stamped
