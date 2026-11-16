"""Cross-account wash estimates for the JOR-50 chain view.

A loss in one labeled account and a buy of the same stock in another
account, inside the 61-day window, becomes its own chain. It does not
enter wash_sale_flags, lot basis, or tax totals. An IRA buy is described
as a permanent disallowance with no basis carryover.

Same-account uploads, and any book with a single label, return no rows.
"""

from __future__ import annotations

from datetime import timedelta

from account_labels import account_display_name, group_by_account, is_ira_account
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


def _stock_buys(transactions: list[Transaction]) -> list[Transaction]:
    return sorted(
        (
            txn
            for txn in transactions
            if txn.trans_code in _BUY_CODES and txn.asset_type == AssetType.STOCK
        ),
        key=lambda txn: txn.activity_date,
    )


def _same_account_disallowed(
    sell: Transaction,
    flags: list[WashSaleFlag],
) -> float:
    total = 0.0
    sell_label = sell.account_label or ""
    for flag in flags:
        if _symbol(flag.symbol) != _symbol(sell.instrument):
            continue
        if flag.sale_date != sell.activity_date:
            continue
        if not _qty_close(flag.sale_quantity, sell.quantity):
            continue
        flag_label = getattr(flag, "account_label", None) or ""
        if flag_label and sell_label and flag_label != sell_label:
            continue
        total += float(flag.disallowed_loss)
    return _cents(total)


def _plain_english(
    *,
    sell: Transaction,
    loss: float,
    covered: float,
    estimated: float,
    sale_name: str,
    buy_names: str,
    permanent: bool,
) -> str:
    text = (
        f"Not on your 1099: cross-account. "
        f"A {sale_name} sale of {_fmt_qty(sell.quantity)} {_symbol(sell.instrument)} "
        f"on {sell.activity_date.strftime('%b')} {sell.activity_date.day} "
        f"lost {_fmt_money(loss)}. "
        f"A buy in {buy_names} within 30 days before or after that sale "
        f"covers {_fmt_qty(covered)} shares. "
        f"Estimated disallowed amount {_fmt_money(estimated)}."
    )
    if permanent:
        text += (
            " An IRA replacement may apply, ask a tax pro. No basis carryover."
        )
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
    for group in groups:
        buys = _stock_buys(group)
        sells = [
            txn
            for txn in group
            if txn.trans_code in _SELL_CODES and txn.asset_type == AssetType.STOCK
        ]
        if tax_year is not None:
            sells = [txn for txn in sells if txn.activity_date.year == tax_year]
        sells.sort(key=lambda txn: txn.activity_date)
        used: dict[int, float] = {}
        for sell in sells:
            loss = _compute_sale_loss(sell, buys, used)
            if loss <= _QTY_EPS:
                continue
            loss = _cents(loss)
            already = _same_account_disallowed(sell, flags)
            remaining_loss = _cents(max(0.0, loss - already))
            if remaining_loss <= 0:
                continue
            window_start = sell.activity_date - timedelta(days=WASH_SALE_WINDOW_DAYS)
            window_end = sell.activity_date + timedelta(days=WASH_SALE_WINDOW_DAYS)
            replacements: list[Transaction] = []
            for other in groups:
                if other is group:
                    continue
                for txn in other:
                    if txn.trans_code not in _BUY_CODES:
                        continue
                    if txn.asset_type != AssetType.STOCK:
                        continue
                    if _symbol(txn.instrument) != _symbol(sell.instrument):
                        continue
                    if txn.activity_date == sell.activity_date:
                        continue
                    if not window_start <= txn.activity_date <= window_end:
                        continue
                    replacements.append(txn)
            if not replacements:
                continue
            replacements.sort(key=lambda txn: (txn.activity_date, txn.account_label or ""))
            replaced_shares = sell.quantity * min(1.0, already / loss) if loss else 0.0
            shares_left = max(0.0, sell.quantity - replaced_shares)
            if shares_left <= _QTY_EPS:
                continue
            allocated: list[tuple[Transaction, float]] = []
            still = shares_left
            for txn in replacements:
                if still <= _QTY_EPS:
                    break
                take = min(still, txn.quantity)
                if take <= _QTY_EPS:
                    continue
                allocated.append((txn, take))
                still -= take
            covered = shares_left - still
            if covered <= _QTY_EPS:
                continue
            estimated = _cents(min(remaining_loss, loss * (covered / sell.quantity)))
            if estimated <= 0:
                continue
            permanent = any(is_ira_account(txn.account_label) for txn, _take in allocated)
            sale_label = sell.account_label or None
            sale_name = account_display_name(sale_label) or "this account"
            buy_names = ", ".join(
                dict.fromkeys(
                    account_display_name(txn.account_label) or "another account"
                    for txn, _take in allocated
                )
            )
            buy_rows: list[WashReplacementBuy] = []
            remaining_dollars = estimated
            for index, (txn, take) in enumerate(allocated):
                if index == len(allocated) - 1:
                    portion = remaining_dollars
                else:
                    portion = _cents(estimated * (take / covered))
                    remaining_dollars = _cents(remaining_dollars - portion)
                cash = _cents(txn.price * take)
                explained = cash if permanent else _cents(cash + portion)
                buy_rows.append(
                    WashReplacementBuy(
                        activity_date=txn.activity_date,
                        quantity=txn.quantity,
                        price=txn.price,
                        cash_cost=_cents(txn.price * txn.quantity),
                        allocated_quantity=round(take, 4),
                        allocated_disallowed=portion,
                        explained_basis=explained,
                        account_label=txn.account_label or None,
                        account_name=account_display_name(txn.account_label),
                    )
                )
            symbol = _symbol(sell.instrument)
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
                permanent=permanent,
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
