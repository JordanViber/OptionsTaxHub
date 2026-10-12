"""Read-only wash-sale chain explainer.

Builds a display model from wash flags, FIFO realized events, and same-year
1099-B lot matches. Does not rewrite wash_sale.py, lot basis, or tax totals.
Does not set WashSaleFlag.purchase_date (that field feeds ST/LT wash splits).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from csv_parser import RealizedEvent
from leap_rank import CONTRACT_MULTIPLIER
from lot_matcher import CENTS, QTY_EPS
from models import (
    AssetType,
    LotMatchReport,
    LotMatchRow,
    Supplemental1099Summary,
    TaxLot,
    Transaction,
    TransCode,
    WashReconcileStatus,
    WashReplacementBuy,
    WashSaleChain,
    WashSaleFlag,
    WashSaleHop,
)
from wash_sale import WASH_SALE_WINDOW_DAYS, _find_qualifying_repurchases
from year_close_packet import is_same_year_1099_compare

_BUY_CODES = (TransCode.BUY, TransCode.BTO)
_SELL_CODES = (TransCode.SELL, TransCode.STC)


@dataclass
class _BuyDraft:
    activity_date: date
    quantity: float
    price: float
    cash_cost: float
    allocated_quantity: float
    allocated_disallowed: float
    explained_basis: float


@dataclass
class _HopDraft:
    hop_id: str
    symbol: str
    asset_type: str
    sale_date: date
    sale_quantity: float
    proceeds: float
    cost_basis: float
    loss: float
    buys: list[_BuyDraft] = field(default_factory=list)
    disallowed_loss: float = 0.0
    engine_disallowed_loss: float | None = None
    form_1099_wash: float | None = None
    engine_adjusted_cost_basis: float | None = None
    open_lot_total_cost_basis: float | None = None
    open_lot_wash_sale_disallowed: float | None = None
    original_purchase_date: date | None = None
    original_holding_days: int | None = None
    carryover_days: int | None = None
    tacked_acquisition_date: date | None = None
    carried_in_disallowed: float = 0.0
    engine_sale_loss: float | None = None
    explained_loss_with_carry: float | None = None
    plain_english: str = ""
    reconcile_status: WashReconcileStatus = "not_on_1099"
    difference: float | None = None
    hop_index: int = 0
    from_engine: bool = True


def _cents(value: float) -> float:
    return round(float(value), 2)


def _symbol(value: str | None) -> str:
    return (value or "").strip().upper()


def _qty_close(left: float, right: float) -> bool:
    return abs(float(left) - float(right)) <= QTY_EPS


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


def _fmt_date(value: date, *, with_year: bool) -> str:
    month = value.strftime("%b")
    if with_year:
        return f"{month} {value.day}, {value.year}"
    return f"{month} {value.day}"


def _qty_token(quantity: float) -> str:
    return _fmt_qty(quantity)


def _buys_sorted(transactions: list[Transaction]) -> list[Transaction]:
    buys = [txn for txn in transactions if txn.trans_code in _BUY_CODES]
    return sorted(buys, key=lambda txn: (txn.activity_date, _symbol(txn.instrument)))


def _display_scale(asset_type: str) -> float:
    """Option flag dollars are per-share premiums. Realized events are contract dollars.

    CONTRACT_MULTIPLIER is the engine's contract size. Apply it only to copies
    on the display chain. Do not rewrite flags, lots, or tax totals.
    """
    if asset_type == AssetType.OPTION.value:
        return float(CONTRACT_MULTIPLIER)
    return 1.0


def _claim_sell(
    transactions: list[Transaction],
    used: set[int],
    symbol: str,
    sale_date: date,
    quantity: float,
) -> tuple[int | None, Transaction | None]:
    for index, txn in enumerate(transactions):
        if index in used:
            continue
        if txn.trans_code not in _SELL_CODES:
            continue
        if _symbol(txn.instrument) != symbol:
            continue
        if txn.activity_date != sale_date:
            continue
        if not _qty_close(txn.quantity, quantity):
            continue
        used.add(index)
        return index, txn
    return None, None


def _claim_event(
    events: list[RealizedEvent],
    used: set[int],
    symbol: str,
    sale_date: date | None,
    quantity: float,
) -> tuple[int | None, RealizedEvent | None]:
    if sale_date is None:
        return None, None
    for index, event in enumerate(events):
        if index in used:
            continue
        if _symbol(event.symbol) != symbol:
            continue
        if event.sale_date != sale_date:
            continue
        if not _qty_close(event.quantity, quantity):
            continue
        used.add(index)
        return index, event
    return None, None


def _hop_discriminator(
    *,
    event_index: int | None,
    sell_index: int | None,
    flag_index: int,
) -> str:
    if event_index is not None:
        return f"e{event_index}"
    if sell_index is not None:
        return f"t{sell_index}"
    return f"f{flag_index}"


def _hop_id(symbol: str, sale_date: date, quantity: float, discriminator: str) -> str:
    return (
        f"{symbol}:{sale_date.isoformat()}:{_qty_token(quantity)}:{discriminator}"
    )


def _window_buys(
    transactions: list[Transaction],
    symbol: str,
    center: date,
) -> list[Transaction]:
    """Buys inside the 61-day window, same rule as the engine helper."""
    dated = [
        txn
        for txn in transactions
        if txn.trans_code in _SELL_CODES
        and _symbol(txn.instrument) == symbol
        and txn.activity_date == center
    ]
    buys = _buys_sorted(transactions)
    if dated:
        return [buy for _, buy in _find_qualifying_repurchases(dated[0], buys)]
    start = center - timedelta(days=WASH_SALE_WINDOW_DAYS)
    end = center + timedelta(days=WASH_SALE_WINDOW_DAYS)
    return [
        buy
        for buy in buys
        if _symbol(buy.instrument) == symbol
        and start <= buy.activity_date <= end
        and buy.activity_date != center
    ]


def _allocate_buys(
    raw_buys: list[Transaction],
    *,
    repurchase_quantity: float,
    disallowed: float,
) -> list[_BuyDraft]:
    remaining = max(float(repurchase_quantity), 0.0)
    drafts: list[_BuyDraft] = []
    allocated_qtys: list[float] = []
    for buy in raw_buys:
        take = min(max(float(buy.quantity), 0.0), remaining) if remaining > 0 else 0.0
        remaining = max(0.0, remaining - take)
        allocated_qtys.append(take)
        drafts.append(
            _BuyDraft(
                activity_date=buy.activity_date,
                quantity=float(buy.quantity),
                price=float(buy.price),
                cash_cost=_cents(float(buy.price) * float(buy.quantity)),
                allocated_quantity=take,
                allocated_disallowed=0.0,
                explained_basis=0.0,
            )
        )
    denom = float(repurchase_quantity)
    amounts = [0.0] * len(drafts)
    if denom > QTY_EPS and disallowed:
        for index, qty in enumerate(allocated_qtys):
            if qty <= QTY_EPS:
                continue
            amounts[index] = _cents(disallowed * (qty / denom))
        remainder = _cents(disallowed - sum(amounts))
        for index, qty in enumerate(allocated_qtys):
            if qty > QTY_EPS:
                amounts[index] = _cents(amounts[index] + remainder)
                break
    for draft, amount, qty in zip(drafts, amounts, allocated_qtys):
        draft.allocated_disallowed = amount
        draft.explained_basis = _cents(draft.price * qty + amount)
    return drafts


def _apply_display_scale(drafts: list[_BuyDraft], scale: float) -> None:
    """Put buy dollars on the same scale as the hop's basis and proceeds."""
    for draft in drafts:
        draft.cash_cost = _cents(draft.price * draft.quantity * scale)
        draft.allocated_disallowed = _cents(draft.allocated_disallowed * scale)
        draft.explained_basis = _cents(
            draft.price * draft.allocated_quantity * scale + draft.allocated_disallowed
        )


def _holding_from_event(
    event: RealizedEvent | None,
    sale_date: date,
) -> tuple[date | None, int | None]:
    if event is None or event.purchase_date is None:
        return None, None
    return event.purchase_date, (sale_date - event.purchase_date).days


def _open_lot_numbers(
    tax_lots: list[TaxLot],
    symbol: str,
    purchase_date: date | None,
) -> tuple[float | None, float | None]:
    if purchase_date is None:
        return None, None
    for lot in tax_lots:
        if _symbol(lot.symbol) != symbol:
            continue
        if lot.purchase_date != purchase_date:
            continue
        return float(lot.total_cost_basis), float(lot.wash_sale_disallowed)
    return None, None


def _report_rows(report: LotMatchReport | None) -> list[LotMatchRow]:
    if report is None:
        return []
    return list(report.matched) + list(report.gap) + list(report.unmatched)


@dataclass
class _SaleGroup:
    """1099 rows that are one sale: same symbol, sell date, and unit proceeds."""

    symbol: str
    sale_date: date
    unit_proceeds: float | None
    items: list[tuple[int, LotMatchRow]]


def _unit_proceeds(proceeds: float, quantity: float) -> float | None:
    if abs(quantity) <= QTY_EPS:
        return None
    return _cents(proceeds / quantity)


def _row_sale_date(row: LotMatchRow, *, trade_date_only: bool) -> date | None:
    if trade_date_only:
        return row.export_trade_date
    return row.export_trade_date or row.date_sold_1099


def _group_rows(
    rows: list[LotMatchRow],
    *,
    used: set[int],
    symbol: str | None,
    sale_date: date | None,
    trade_date_only: bool,
    require_wash: bool,
) -> list[_SaleGroup]:
    buckets: dict[tuple, list[tuple[int, LotMatchRow]]] = {}
    order: list[tuple] = []
    for index, row in enumerate(rows):
        if index in used:
            continue
        row_symbol = _symbol(row.symbol)
        if symbol is not None and row_symbol != symbol:
            continue
        row_date = _row_sale_date(row, trade_date_only=trade_date_only)
        if sale_date is not None and row_date != sale_date:
            continue
        if row_date is None:
            continue
        if require_wash and float(row.wash_sale_disallowed or 0.0) < 0.01:
            continue
        unit = _unit_proceeds(float(row.proceeds_1099 or 0.0), float(row.quantity or 0.0))
        key = (row_symbol, row_date, unit)
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append((index, row))
    return [
        _SaleGroup(
            symbol=key[0],
            sale_date=key[1],
            unit_proceeds=key[2],
            items=buckets[key],
        )
        for key in order
    ]


def _greedy_fill(
    group: _SaleGroup,
    quantity: float,
) -> list[tuple[int, LotMatchRow]] | None:
    """Take whole rows until quantity matches. Skip a row that would overshoot."""
    remaining = float(quantity)
    taken: list[tuple[int, LotMatchRow]] = []
    for index, row in group.items:
        row_qty = float(row.quantity or 0.0)
        if row_qty <= QTY_EPS:
            continue
        if row_qty > remaining + QTY_EPS:
            continue
        taken.append((index, row))
        remaining -= row_qty
        if remaining <= QTY_EPS:
            break
    filled = sum(float(row.quantity or 0.0) for _, row in taken)
    if taken and _qty_close(filled, quantity):
        return taken
    return None


def _target_unit_proceeds(
    sell: Transaction | None,
    event: RealizedEvent | None,
    scale: float,
) -> float | None:
    if sell is not None and float(sell.quantity) > QTY_EPS:
        return _cents(float(sell.price) * scale)
    if event is not None and float(event.quantity) > QTY_EPS:
        return _cents(float(event.sale_proceeds) / float(event.quantity))
    return None


def _match_sale_rows(
    rows: list[LotMatchRow],
    *,
    used: set[int],
    symbol: str,
    sale_date: date,
    quantity: float,
    sell: Transaction | None,
    event: RealizedEvent | None,
    scale: float,
) -> list[tuple[int, LotMatchRow]] | None:
    """Pair a flag to the proceeds group whose per-share price is closest.

    One FIFO sell closes several lots, so the 1099 has several rows. Compare
    the flag to the sum of that sale, not to any single row's quantity.
    """
    groups = _group_rows(
        rows,
        used=used,
        symbol=symbol,
        sale_date=sale_date,
        trade_date_only=True,
        require_wash=False,
    )
    if not groups:
        return None
    target = _target_unit_proceeds(sell, event, scale)
    if target is None:
        ordered = groups
    else:
        known = [group for group in groups if group.unit_proceeds is not None]
        if not known:
            return None
        best = min(_cents(abs(group.unit_proceeds - target)) for group in known)
        ordered = [
            group
            for group in known
            if _cents(abs((group.unit_proceeds or 0.0) - target)) == best
        ]
    for group in ordered:
        taken = _greedy_fill(group, quantity)
        if taken is not None:
            return taken
    return None


def _status_for_wash(
    disallowed: float,
    wash: float | None,
) -> tuple[WashReconcileStatus, float | None, float | None]:
    if wash is None or wash < 0.01:
        return "not_on_1099", None, None
    gap = abs(float(disallowed) - wash)
    if gap <= CENTS:
        return "matches", None, _cents(wash)
    return "differs", _cents(gap), _cents(wash)


def _named_asset(asset: object) -> str:
    if isinstance(asset, AssetType):
        return asset.value
    if asset is None:
        return AssetType.STOCK.value
    return str(asset or "stock")


def _asset_type(txn: Transaction | None) -> str:
    if txn is None:
        return AssetType.STOCK.value
    return _named_asset(txn.asset_type)


def _hop_from_flag(
    flag: WashSaleFlag,
    *,
    flag_index: int,
    transactions: list[Transaction],
    events: list[RealizedEvent],
    tax_lots: list[TaxLot],
    rows: list[LotMatchRow],
    used_rows: set[int],
    used_events: set[int],
    used_txns: set[int],
    same_year: bool,
) -> _HopDraft:
    symbol = _symbol(flag.symbol)
    sell_index, sell = _claim_sell(
        transactions, used_txns, symbol, flag.sale_date, flag.sale_quantity
    )
    event_index, event = _claim_event(
        events, used_events, symbol, flag.sale_date, flag.sale_quantity
    )
    asset = _asset_type(sell)
    if sell is None and event is not None:
        asset = _named_asset(event.asset_type)
    scale = _display_scale(asset)
    if event is not None:
        # RealizedEvent basis and proceeds are already contract dollars.
        proceeds = _cents(event.sale_proceeds)
        cost_basis = _cents(event.cost_basis)
    elif sell is not None:
        proceeds = _cents(float(sell.quantity) * float(sell.price) * scale)
        cost_basis = _cents(proceeds + float(flag.sale_loss) * scale)
    else:
        proceeds = 0.0
        cost_basis = _cents(float(flag.sale_loss) * scale)
    acquired, held_days = _holding_from_event(event, flag.sale_date)
    raw_buys = (
        [buy for _, buy in _find_qualifying_repurchases(sell, _buys_sorted(transactions))]
        if sell is not None
        else []
    )
    # Split the engine's unscaled disallowance, then scale the display copies.
    buys = _allocate_buys(
        raw_buys,
        repurchase_quantity=float(flag.repurchase_quantity),
        disallowed=float(flag.disallowed_loss),
    )
    _apply_display_scale(buys, scale)
    loss = _cents(float(flag.sale_loss) * scale)
    disallowed = _cents(float(flag.disallowed_loss) * scale)
    open_basis, open_wash = _open_lot_numbers(
        tax_lots, symbol, flag.repurchase_date
    )
    status: WashReconcileStatus = "not_on_1099"
    difference: float | None = None
    form_wash: float | None = None
    if same_year:
        taken = _match_sale_rows(
            rows,
            used=used_rows,
            symbol=symbol,
            sale_date=flag.sale_date,
            quantity=float(flag.sale_quantity),
            sell=sell,
            event=event,
            scale=scale,
        )
        wash_total = None
        if taken is not None:
            for index, _row in taken:
                used_rows.add(index)
            wash_total = _cents(
                sum(float(row.wash_sale_disallowed or 0.0) for _, row in taken)
            )
        status, difference, form_wash = _status_for_wash(disallowed, wash_total)
    return _HopDraft(
        hop_id=_hop_id(
            symbol,
            flag.sale_date,
            flag.sale_quantity,
            _hop_discriminator(
                event_index=event_index,
                sell_index=sell_index,
                flag_index=flag_index,
            ),
        ),
        symbol=symbol,
        asset_type=asset,
        sale_date=flag.sale_date,
        sale_quantity=float(flag.sale_quantity),
        proceeds=proceeds,
        cost_basis=cost_basis,
        loss=loss,
        buys=buys,
        disallowed_loss=disallowed,
        engine_disallowed_loss=disallowed,
        form_1099_wash=form_wash,
        engine_adjusted_cost_basis=_cents(float(flag.adjusted_cost_basis) * scale),
        open_lot_total_cost_basis=open_basis,
        open_lot_wash_sale_disallowed=open_wash,
        original_purchase_date=acquired,
        original_holding_days=held_days,
        carryover_days=held_days,
        tacked_acquisition_date=acquired,
        engine_sale_loss=loss,
        from_engine=True,
        reconcile_status=status,
        difference=difference,
    )


def _repurchase_cap(buys: list[Transaction], sale_quantity: float) -> float:
    total = sum(float(buy.quantity) for buy in buys)
    if sale_quantity > QTY_EPS:
        return min(total, float(sale_quantity))
    return total


def _hop_from_1099_group(
    items: list[tuple[int, LotMatchRow]],
    *,
    transactions: list[Transaction],
    events: list[RealizedEvent],
    tax_lots: list[TaxLot],
) -> _HopDraft | None:
    if not items:
        return None
    first_index, first = items[0]
    symbol = _symbol(first.symbol)
    sale_date = first.export_trade_date or first.date_sold_1099
    if not symbol or sale_date is None:
        return None
    quantity = sum(float(row.quantity or 0.0) for _, row in items)
    wash = _cents(sum(float(row.wash_sale_disallowed or 0.0) for _, row in items))
    proceeds = _cents(sum(float(row.proceeds_1099 or 0.0) for _, row in items))
    cost_basis = _cents(sum(float(row.cost_basis_1099 or 0.0) for _, row in items))
    loss = _cents(max(0.0, cost_basis - proceeds))
    raw_buys = _window_buys(transactions, symbol, sale_date)
    cap = _repurchase_cap(raw_buys, quantity)
    buys = _allocate_buys(raw_buys, repurchase_quantity=cap, disallowed=wash)
    export_dates = {row.export_trade_date for _, row in items}
    event = None
    export_date = next(iter(export_dates)) if len(export_dates) == 1 else None
    if export_date is not None:
        _index, event = _claim_event(events, set(), symbol, export_date, quantity)
    acquired, held_days = (
        _holding_from_event(event, export_date)
        if export_date is not None and event is not None
        else (None, None)
    )
    earliest = next(
        (buy.activity_date for buy in buys if buy.allocated_quantity > QTY_EPS),
        None,
    )
    open_basis, open_wash = _open_lot_numbers(tax_lots, symbol, earliest)
    return _HopDraft(
        hop_id=_hop_id(symbol, sale_date, quantity, f"r{first_index}"),
        symbol=symbol,
        asset_type=AssetType.STOCK.value,
        sale_date=sale_date,
        sale_quantity=quantity,
        proceeds=proceeds,
        cost_basis=cost_basis,
        loss=loss,
        buys=buys,
        disallowed_loss=wash,
        engine_disallowed_loss=None,
        form_1099_wash=wash,
        engine_adjusted_cost_basis=None,
        open_lot_total_cost_basis=open_basis,
        open_lot_wash_sale_disallowed=open_wash,
        original_purchase_date=acquired,
        original_holding_days=held_days,
        carryover_days=held_days,
        tacked_acquisition_date=acquired,
        engine_sale_loss=None,
        explained_loss_with_carry=loss,
        reconcile_status="on_1099_only",
        difference=None,
        from_engine=False,
    )


def _link_engine_hops(hops: list[_HopDraft]) -> list[list[_HopDraft]]:
    count = len(hops)
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for left in range(count):
        earlier = hops[left]
        for right in range(count):
            if left == right:
                continue
            later = hops[right]
            if later.symbol != earlier.symbol:
                continue
            if later.sale_date <= earlier.sale_date:
                continue
            if later.original_purchase_date is None:
                continue
            for buy in earlier.buys:
                if buy.activity_date != later.original_purchase_date:
                    continue
                if later.sale_quantity <= buy.allocated_quantity + QTY_EPS:
                    union(left, right)
                    break
    groups: dict[int, list[_HopDraft]] = {}
    for index, hop in enumerate(hops):
        groups.setdefault(find(index), []).append(hop)
    return list(groups.values())


def _apply_carry(ordered: list[_HopDraft]) -> None:
    for index, hop in enumerate(ordered):
        hop.hop_index = index
        carried = 0.0
        if index > 0 and hop.original_purchase_date is not None:
            for earlier in reversed(ordered[:index]):
                matched = False
                for buy in earlier.buys:
                    if buy.activity_date != hop.original_purchase_date:
                        continue
                    if buy.allocated_quantity <= QTY_EPS:
                        continue
                    scale = min(1.0, hop.sale_quantity / buy.allocated_quantity)
                    carried = _cents(buy.allocated_disallowed * scale)
                    matched = True
                    break
                if matched:
                    break
        hop.carried_in_disallowed = carried
        hop.explained_loss_with_carry = _cents(
            max(0.0, hop.cost_basis + carried - hop.proceeds)
        )


def _reconcile_clause(hop: _HopDraft) -> str:
    if hop.reconcile_status == "matches":
        return (
            "This matches your 1099 wash adjustment of "
            f"{_fmt_money(hop.form_1099_wash or 0)}."
        )
    if hop.reconcile_status == "differs":
        return (
            "This differs from your 1099 wash adjustment of "
            f"{_fmt_money(hop.form_1099_wash or 0)} by {_fmt_money(hop.difference or 0)}."
        )
    if hop.reconcile_status == "on_1099_only":
        return (
            "Your 1099 reports a "
            f"{_fmt_money(hop.form_1099_wash or hop.disallowed_loss)} "
            "wash adjustment on this sale. This Robinhood export did not flag "
            "a matching wash."
        )
    return (
        "This wash is not on your 1099. It was found only in this Robinhood export."
    )


def _needs_year(sale: date, buys: list[_BuyDraft]) -> bool:
    years = {sale.year}
    years.update(buy.activity_date.year for buy in buys)
    return len(years) > 1


def _contracts(asset_type: str) -> str:
    return " contracts" if asset_type == AssetType.OPTION.value else ""


def _listed_buys(hop: _HopDraft) -> list[_BuyDraft]:
    allocated = [buy for buy in hop.buys if buy.allocated_quantity > QTY_EPS]
    return allocated or list(hop.buys)


def plain_english_line(hop: _HopDraft) -> str:
    """One sentence for a hop. Hop 0 is also the chain headline."""
    listed = _listed_buys(hop)
    with_year = _needs_year(hop.sale_date, listed)
    unit = _contracts(hop.asset_type)
    qty = _fmt_qty(hop.sale_quantity)
    symbol = hop.symbol
    sale_on = _fmt_date(hop.sale_date, with_year=with_year)
    reconcile = _reconcile_clause(hop)
    loss_bit = ""
    if hop.loss > 0.004:
        loss_bit = f" at a {_fmt_money(hop.loss)} loss"
    partial = ""
    allocated_qty = sum(buy.allocated_quantity for buy in hop.buys)
    if allocated_qty + QTY_EPS < hop.sale_quantity and allocated_qty > QTY_EPS:
        partial = " of that loss"
    moved = (
        f"so {_fmt_money(hop.disallowed_loss)}{partial} moved into "
        "the new lot's basis."
    )
    if not listed:
        body = f"You sold {qty}{unit} {symbol}{loss_bit} on {sale_on}."
    elif all(buy.activity_date < hop.sale_date for buy in listed):
        buy_bits = []
        for index, buy in enumerate(listed):
            when = _fmt_date(buy.activity_date, with_year=with_year)
            amount = f"{_fmt_qty(buy.allocated_quantity)}{unit}"
            if index == 0:
                buy_bits.append(f"{amount} {symbol} on {when}")
            else:
                buy_bits.append(f"{amount} on {when}")
        bought = (
            buy_bits[0]
            if len(buy_bits) == 1
            else " and ".join(buy_bits)
            if len(buy_bits) == 2
            else ", ".join(buy_bits[:-1]) + f", and {buy_bits[-1]}"
        )
        body = (
            f"You bought {bought} and sold {qty}{unit}{loss_bit} on {sale_on}, "
            f"so {_fmt_money(hop.disallowed_loss)}{partial} moved into "
            "the replacement lot's basis."
        )
    else:
        buy_bits = []
        for index, buy in enumerate(listed):
            when = _fmt_date(buy.activity_date, with_year=with_year)
            amount = f"{_fmt_qty(buy.allocated_quantity)}{unit}"
            if index == 0:
                buy_bits.append(f"{amount} back on {when}")
            else:
                buy_bits.append(f"{amount} on {when}")
        if len(buy_bits) == 1:
            bought = buy_bits[0]
        elif len(buy_bits) == 2:
            bought = f"{buy_bits[0]} and {buy_bits[1]}"
        else:
            bought = ", ".join(buy_bits[:-1]) + f", and {buy_bits[-1]}"
        body = (
            f"You sold {qty}{unit} {symbol}{loss_bit} on {sale_on} "
            f"and bought {bought}, {moved}"
        )
    line = f"{body} {reconcile}"
    if hop.hop_index > 0:
        line = f"Hop {hop.hop_index + 1}: {line}"
    if (
        hop.hop_index > 0
        and hop.engine_sale_loss is not None
        and hop.explained_loss_with_carry is not None
        and abs(hop.explained_loss_with_carry - hop.engine_sale_loss) > CENTS
    ):
        line += (
            f" The prior hop carried {_fmt_money(hop.carried_in_disallowed)} "
            f"into this lot. The engine still reports a "
            f"{_fmt_money(hop.engine_sale_loss)} loss on this sale, and the "
            "tax totals use that engine figure."
        )
    return line


def _to_model(hop: _HopDraft) -> WashSaleHop:
    return WashSaleHop(
        hop_id=hop.hop_id,
        hop_index=hop.hop_index,
        symbol=hop.symbol,
        asset_type=hop.asset_type,
        sale_date=hop.sale_date,
        sale_quantity=hop.sale_quantity,
        proceeds=hop.proceeds,
        cost_basis=hop.cost_basis,
        loss=hop.loss,
        replacement_buys=[
            WashReplacementBuy(
                activity_date=buy.activity_date,
                quantity=buy.quantity,
                price=buy.price,
                cash_cost=buy.cash_cost,
                allocated_quantity=buy.allocated_quantity,
                allocated_disallowed=buy.allocated_disallowed,
                explained_basis=buy.explained_basis,
            )
            for buy in hop.buys
        ],
        disallowed_loss=hop.disallowed_loss,
        engine_disallowed_loss=hop.engine_disallowed_loss,
        form_1099_wash=hop.form_1099_wash,
        engine_adjusted_cost_basis=hop.engine_adjusted_cost_basis,
        open_lot_total_cost_basis=hop.open_lot_total_cost_basis,
        open_lot_wash_sale_disallowed=hop.open_lot_wash_sale_disallowed,
        original_purchase_date=hop.original_purchase_date,
        original_holding_days=hop.original_holding_days,
        carryover_days=hop.carryover_days,
        tacked_acquisition_date=hop.tacked_acquisition_date,
        carried_in_disallowed=hop.carried_in_disallowed,
        engine_sale_loss=hop.engine_sale_loss,
        explained_loss_with_carry=hop.explained_loss_with_carry,
        plain_english=hop.plain_english,
        reconcile_status=hop.reconcile_status,
        difference=hop.difference,
    )


def _finish(group: list[_HopDraft]) -> WashSaleChain:
    ordered = sorted(group, key=lambda hop: (hop.sale_date, hop.hop_id))
    _apply_carry(ordered)
    for hop in ordered:
        hop.plain_english = plain_english_line(hop)
    head = ordered[0]
    return WashSaleChain(
        chain_id=head.hop_id,
        symbol=head.symbol,
        reconcile_status=head.reconcile_status,
        difference=head.difference,
        plain_english=head.plain_english,
        hops=[_to_model(hop) for hop in ordered],
    )


def build_wash_sale_chains(
    *,
    transactions: list[Transaction],
    wash_sale_flags: list[WashSaleFlag],
    realized_events: list[RealizedEvent],
    tax_lots: list[TaxLot],
    supplemental: Supplemental1099Summary | None,
    lot_match_report: LotMatchReport | None,
    tax_year: int | None,
) -> list[WashSaleChain]:
    """Explain washes already computed. Leaves every input unchanged."""
    same_year = False
    if supplemental is not None:
        same_year = is_same_year_1099_compare(supplemental.tax_year, tax_year)
    rows = _report_rows(lot_match_report) if same_year else []
    used_rows: set[int] = set()
    used_events: set[int] = set()
    used_txns: set[int] = set()
    engine_hops: list[_HopDraft] = []
    for flag_index, flag in enumerate(wash_sale_flags):
        if abs(float(flag.disallowed_loss)) < 0.01:
            continue
        engine_hops.append(
            _hop_from_flag(
                flag,
                flag_index=flag_index,
                transactions=transactions,
                events=realized_events,
                tax_lots=tax_lots,
                rows=rows,
                used_rows=used_rows,
                used_events=used_events,
                used_txns=used_txns,
                same_year=same_year,
            )
        )
    chains = [_finish(group) for group in _link_engine_hops(engine_hops)]
    if same_year:
        leftover = _group_rows(
            rows,
            used=used_rows,
            symbol=None,
            sale_date=None,
            trade_date_only=False,
            require_wash=True,
        )
        for group in leftover:
            hop = _hop_from_1099_group(
                group.items,
                transactions=transactions,
                events=realized_events,
                tax_lots=tax_lots,
            )
            if hop is None:
                continue
            chains.append(_finish([hop]))
    chains.sort(key=lambda chain: (chain.hops[0].sale_date, chain.symbol, chain.chain_id))
    return chains
