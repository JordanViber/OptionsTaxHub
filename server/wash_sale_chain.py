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
    WashClosedLot,
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
class _ClosedLot:
    quantity: float
    purchase_date: date | None
    holding_days: int | None


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
    closed_lots: list[_ClosedLot] = field(default_factory=list)
    carried_in_disallowed: float = 0.0
    engine_sale_loss: float | None = None
    explained_loss_with_carry: float | None = None
    plain_english: str = ""
    reconcile_status: WashReconcileStatus = "not_on_1099"
    difference: float | None = None
    hop_index: int = 0
    from_engine: bool = True
    # Why a hop was not compared. Drives copy only; not a tax field.
    # "" | "no_1099" | "ambiguous" | "over_cap" | "undetermined"
    not_compared_reason: str = ""


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


# Subset search stays exhaustive only up to this many rows or lots.
# Past the cap the explainer says not_compared instead of guessing.
_MAX_MATCH_CANDIDATES = 12


def _event_candidates(
    events: list[RealizedEvent],
    used: set[int],
    symbol: str,
    sale_date: date | None,
) -> list[tuple[int, RealizedEvent]]:
    if sale_date is None:
        return []
    return [
        (index, event)
        for index, event in enumerate(events)
        if index not in used
        and _symbol(event.symbol) == symbol
        and event.sale_date == sale_date
        and float(event.quantity) > QTY_EPS
    ]


def _qty_subsets(items: list, quantity: float, qty_of) -> list[list]:
    """Every subset whose quantities sum to ``quantity`` within QTY_EPS."""
    count = len(items)
    found: list[list] = []
    for mask in range(1, 1 << count):
        chosen = []
        total = 0.0
        for bit in range(count):
            if mask & (1 << bit):
                chosen.append(items[bit])
                total += float(qty_of(items[bit]))
        if _qty_close(total, quantity):
            found.append(chosen)
    return found


def _subset_scale(
    subset: list[tuple[int, RealizedEvent]],
    sell: Transaction | None,
) -> float:
    asset = _asset_type(sell)
    if sell is None and subset:
        asset = _named_asset(subset[0][1].asset_type)
    return _display_scale(asset)


def _claim_event_set(
    events: list[RealizedEvent],
    used: set[int],
    symbol: str,
    sale_date: date | None,
    quantity: float,
    *,
    sale_loss: float,
    sell: Transaction | None,
) -> list[tuple[int, RealizedEvent]]:
    """Lots closed by one sale.

    A 100-share sell is often two FIFO events (40 and 60). Attach a set only
    when the quantities add up to the flag and the set's proceeds and basis
    imply the flag loss within CENTS. When a sell is known, the set's proceeds
    must also match that sell, so two same-day sales do not take each other's
    lots. More than 12 candidate lots are left unattached.
    """
    candidates = _event_candidates(events, used, symbol, sale_date)
    if not candidates or len(candidates) > _MAX_MATCH_CANDIDATES:
        return []
    subsets = _qty_subsets(candidates, quantity, lambda item: item[1].quantity)
    good: list[list[tuple[int, RealizedEvent]]] = []
    for subset in subsets:
        scale = _subset_scale(subset, sell)
        proceeds = sum(float(event.sale_proceeds) for _, event in subset)
        basis = sum(float(event.cost_basis) for _, event in subset)
        if abs((basis - proceeds) - float(sale_loss) * scale) > CENTS:
            continue
        if sell is not None and float(sell.quantity) > QTY_EPS:
            target = float(sell.quantity) * float(sell.price) * scale
            if abs(proceeds - target) > CENTS:
                continue
        good.append(subset)
    if not good:
        return []
    chosen = min(good, key=lambda subset: tuple(index for index, _event in subset))
    for index, _event in chosen:
        used.add(index)
    return chosen


def _closed_lots_from_events(
    event_set: list[tuple[int, RealizedEvent]],
    sale_date: date,
) -> list[_ClosedLot]:
    lots: list[_ClosedLot] = []
    for _index, event in event_set:
        acquired, held = _holding_from_event(event, sale_date)
        lots.append(
            _ClosedLot(
                quantity=float(event.quantity),
                purchase_date=acquired,
                holding_days=held,
            )
        )
    return lots


def _shared_acquisition(lots: list[_ClosedLot]) -> tuple[date | None, int | None]:
    """One tacked date only when every lot was acquired that day.

    A missing date or two different dates is not the earliest date. The
    per-lot list is the display, and a later hop can link from any of them.
    """
    if not lots:
        return None, None
    dates = [lot.purchase_date for lot in lots]
    if any(value is None for value in dates):
        return None, None
    if len(set(dates)) != 1:
        return None, None
    return dates[0], lots[0].holding_days


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


def _apply_display_scale(
    drafts: list[_BuyDraft],
    scale: float,
    *,
    scale_disallowed: bool = True,
) -> None:
    """Put buy dollars on the contract scale used for basis and proceeds.

    ``price`` on the draft becomes the same dollars (option premium × the
    contract multiplier). 1099 wash amounts are already dollars, so a
    1099-only hop passes scale_disallowed=False and does not multiply them.
    """
    for draft in drafts:
        raw_price = draft.price
        draft.cash_cost = _cents(raw_price * draft.quantity * scale)
        if scale_disallowed:
            draft.allocated_disallowed = _cents(draft.allocated_disallowed * scale)
        draft.explained_basis = _cents(
            raw_price * draft.allocated_quantity * scale + draft.allocated_disallowed
        )
        draft.price = _cents(raw_price * scale)


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
    scale: float,
) -> tuple[float | None, float | None]:
    """Open-lot basis and wash on the same contract-dollar scale as the hop.

    csv_parser already stores option total_cost_basis × CONTRACT_MULTIPLIER.
    adjust_lots_for_wash_sales rewrites it as per-share premium × quantity.
    Scale the premium form up. Leave an already-scaled basis alone.
    The wash amount on the lot is the engine's unscaled disallowance.
    """
    if purchase_date is None:
        return None, None
    for lot in tax_lots:
        if _symbol(lot.symbol) != symbol:
            continue
        if lot.purchase_date != purchase_date:
            continue
        basis = float(lot.total_cost_basis)
        wash = float(lot.wash_sale_disallowed)
        if scale != 1.0:
            premium = float(lot.cost_basis_per_share) * float(lot.quantity)
            contract = premium * scale
            already_contract = (
                abs(basis - contract) <= CENTS and abs(basis - premium) > CENTS
            )
            if not already_contract:
                basis = basis * scale
            wash = wash * scale
        return _cents(basis), _cents(wash)
    return None, None


def _report_rows(report: LotMatchReport | None) -> list[LotMatchRow]:
    if report is None:
        return []
    return list(report.matched) + list(report.gap) + list(report.unmatched)


@dataclass
class _SaleGroup:
    """1099 rows that are one sale: same symbol and sell date.

    Per-share proceeds stay with the first row. Later rows join when they
    are within CENTS of that anchor. Rounding the unit into the group key
    would split a $33.33 + $66.67 fill.
    """

    symbol: str
    sale_date: date
    unit_proceeds: float | None
    items: list[tuple[int, LotMatchRow]]


def _row_sale_date(row: LotMatchRow) -> date | None:
    """Leftover 1099 rows may only have a settlement date."""
    return row.export_trade_date or row.date_sold_1099


def _group_rows(
    rows: list[LotMatchRow],
    *,
    used: set[int],
    require_wash: bool,
) -> list[_SaleGroup]:
    """Group unused 1099 rows that still carry a wash amount.

    Cluster by symbol and sale date. A row joins the earliest cluster whose
    first per-share proceeds are within CENTS. A zero-quantity row only
    joins another zero-quantity row. First-seen order is the hop id.
    """
    pending: dict[tuple, list[tuple[int, LotMatchRow]]] = {}
    order: list[tuple] = []
    for index, row in enumerate(rows):
        if index in used:
            continue
        row_symbol = _symbol(row.symbol)
        row_date = _row_sale_date(row)
        if row_date is None:
            continue
        if require_wash and float(row.wash_sale_disallowed or 0.0) < 0.01:
            continue
        key = (row_symbol, row_date)
        if key not in pending:
            order.append(key)
            pending[key] = []
        pending[key].append((index, row))
    groups: list[_SaleGroup] = []
    for key in order:
        clusters: list[_SaleGroup] = []
        for index, row in pending[key]:
            unit = _row_unit_proceeds(row)
            placed = False
            for cluster in clusters:
                anchor = cluster.unit_proceeds
                if unit is None and anchor is None:
                    cluster.items.append((index, row))
                    placed = True
                    break
                if unit is None or anchor is None:
                    continue
                if abs(unit - anchor) <= CENTS:
                    cluster.items.append((index, row))
                    placed = True
                    break
            if not placed:
                clusters.append(
                    _SaleGroup(
                        symbol=key[0],
                        sale_date=key[1],
                        unit_proceeds=unit,
                        items=[(index, row)],
                    )
                )
        groups.extend(clusters)
    return groups


def _target_unit_proceeds(
    sell: Transaction | None,
    events: list[RealizedEvent],
    scale: float,
) -> float | None:
    if sell is not None and float(sell.quantity) > QTY_EPS:
        return float(sell.price) * scale
    total_qty = sum(float(event.quantity) for event in events)
    if events and total_qty > QTY_EPS:
        proceeds = sum(float(event.sale_proceeds) for event in events)
        return proceeds / total_qty
    return None


def _row_unit_proceeds(row: LotMatchRow) -> float | None:
    quantity = float(row.quantity or 0.0)
    if abs(quantity) <= QTY_EPS:
        return None
    return float(row.proceeds_1099 or 0.0) / quantity


def _match_candidates(
    rows: list[LotMatchRow],
    *,
    used: set[int],
    symbol: str,
    sale_date: date,
    target: float | None,
) -> list[tuple[int, LotMatchRow]]:
    """Same-symbol trade-date rows whose per-share proceeds are within CENTS."""
    candidates: list[tuple[int, LotMatchRow]] = []
    for index, row in enumerate(rows):
        if index in used:
            continue
        if _symbol(row.symbol) != symbol:
            continue
        if row.export_trade_date != sale_date:
            continue
        unit = _row_unit_proceeds(row)
        if unit is None:
            continue
        if target is not None and abs(unit - target) > CENTS:
            continue
        candidates.append((index, row))
    return candidates


def _match_sale_rows(
    rows: list[LotMatchRow],
    *,
    used: set[int],
    symbol: str,
    sale_date: date,
    quantity: float,
    sell: Transaction | None,
    events: list[RealizedEvent],
    scale: float,
) -> tuple[list[tuple[int, LotMatchRow]] | None, str]:
    """Bounded subset-sum of 1099 rows for one flag.

    Returns (rows, reason). reason is "" when a single wash total fits,
    "ambiguous" when two subsets sum to the quantity but their wash totals
    differ by more than CENTS, and "over_cap" when more than 12 rows are
    close enough on per-share proceeds to be candidates. Those two reasons
    do not choose a wash total, but they reserve every candidate row so the
    leftover pass cannot rebuild them as an on_1099_only chain. A single
    wash total also reserves every row used by any tied subset, not only
    the lex-smallest one. None rows and reason "" means nothing matched,
    and those rows stay available.
    """
    target = _target_unit_proceeds(sell, events, scale)
    candidates = _match_candidates(
        rows,
        used=used,
        symbol=symbol,
        sale_date=sale_date,
        target=target,
    )
    if not candidates:
        return None, ""
    if len(candidates) > _MAX_MATCH_CANDIDATES:
        for index, _row in candidates:
            used.add(index)
        return None, "over_cap"
    subsets = _qty_subsets(
        candidates,
        quantity,
        lambda item: float(item[1].quantity or 0.0),
    )
    if not subsets:
        return None, ""
    totals = [
        _cents(sum(float(row.wash_sale_disallowed or 0.0) for _index, row in subset))
        for subset in subsets
    ]
    distinct: list[float] = []
    for total in totals:
        if any(abs(total - seen) <= CENTS for seen in distinct):
            continue
        distinct.append(total)
    if len(distinct) > 1:
        for index, _row in candidates:
            used.add(index)
        return None, "ambiguous"
    chosen = min(subsets, key=lambda subset: tuple(index for index, _row in subset))
    # Tied wash totals still disagree about which rows are the sale. Reserve
    # every row that appears in any of those subsets. A candidate that fits
    # no subset stays available for another sale.
    for subset in subsets:
        for index, _row in subset:
            used.add(index)
    return chosen, ""


def _status_for_wash(
    disallowed: float,
    wash: float,
) -> tuple[WashReconcileStatus, float | None, float | None]:
    """A matched 1099 row. A $0 wash column differs by the full disallowance.

    ``not_on_1099`` is not used here. That status is only for a same-year
    1099 that was compared and had no row for this sale.
    """
    gap = abs(float(disallowed) - float(wash))
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
    event_set = _claim_event_set(
        events,
        used_events,
        symbol,
        flag.sale_date,
        float(flag.sale_quantity),
        sale_loss=float(flag.sale_loss),
        sell=sell,
    )
    event_index = min((index for index, _event in event_set), default=None)
    asset = _asset_type(sell)
    if sell is None and event_set:
        asset = _named_asset(event_set[0][1].asset_type)
    scale = _display_scale(asset)
    closed = _closed_lots_from_events(event_set, flag.sale_date) if event_set else []
    acquired, held_days = _shared_acquisition(closed)
    if event_set:
        # RealizedEvent basis and proceeds are already contract dollars.
        proceeds = _cents(sum(float(event.sale_proceeds) for _index, event in event_set))
        cost_basis = _cents(sum(float(event.cost_basis) for _index, event in event_set))
    elif sell is not None:
        proceeds = _cents(float(sell.quantity) * float(sell.price) * scale)
        cost_basis = _cents(proceeds + float(flag.sale_loss) * scale)
    else:
        proceeds = 0.0
        cost_basis = _cents(float(flag.sale_loss) * scale)
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
        tax_lots, symbol, flag.repurchase_date, scale
    )
    status: WashReconcileStatus = "not_compared"
    difference: float | None = None
    form_wash: float | None = None
    not_compared_reason = "no_1099"
    if same_year:
        taken, blocked = _match_sale_rows(
            rows,
            used=used_rows,
            symbol=symbol,
            sale_date=flag.sale_date,
            quantity=float(flag.sale_quantity),
            sell=sell,
            events=[event for _index, event in event_set],
            scale=scale,
        )
        if blocked:
            status = "not_compared"
            not_compared_reason = blocked
        elif taken is None:
            status = "not_on_1099"
            not_compared_reason = ""
        else:
            for index, _row in taken:
                used_rows.add(index)
            wash_total = _cents(
                sum(float(row.wash_sale_disallowed or 0.0) for _, row in taken)
            )
            status, difference, form_wash = _status_for_wash(disallowed, wash_total)
            not_compared_reason = ""
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
        closed_lots=closed,
        engine_sale_loss=loss,
        from_engine=True,
        reconcile_status=status,
        difference=difference,
        not_compared_reason=not_compared_reason,
    )


def _option_text(value: str | None) -> bool:
    """True when the description names a call, put, or option.

    A generic stock description is not a stock hint. Padding keeps "call"
    from matching inside another word.
    """
    padded = f" {(value or '').lower()} "
    return " call " in padded or " put " in padded or " option " in padded


def _description_allows(txn: Transaction, row_text: str) -> bool:
    """Reject a numeric match whose descriptions name a different instrument."""
    is_option = _named_asset(txn.asset_type) == AssetType.OPTION.value
    if _option_text(row_text) and not is_option:
        return False
    if _option_text(txn.description) and not is_option:
        return False
    return True


def _sale_matches_txn(
    txn: Transaction,
    *,
    symbol: str,
    sale_date: date,
    quantity: float,
    proceeds: float,
) -> bool:
    if txn.trans_code not in _SELL_CODES:
        return False
    if _symbol(txn.instrument) != symbol:
        return False
    if txn.activity_date != sale_date:
        return False
    if not _qty_close(txn.quantity, quantity):
        return False
    scale = _display_scale(_named_asset(txn.asset_type))
    target = float(txn.quantity) * float(txn.price) * scale
    return abs(target - proceeds) <= CENTS


def _sale_matches_event(
    event: RealizedEvent,
    *,
    symbol: str,
    sale_date: date,
    quantity: float,
    proceeds: float,
) -> bool:
    """A single realized lot whose quantity and dollar proceeds are this sale."""
    if _symbol(event.symbol) != symbol:
        return False
    if event.sale_date != sale_date:
        return False
    if not _qty_close(event.quantity, quantity):
        return False
    return abs(float(event.sale_proceeds) - proceeds) <= CENTS


def _symbol_has_option(
    symbol: str,
    transactions: list[Transaction],
    events: list[RealizedEvent],
    tax_lots: list[TaxLot],
) -> bool:
    """True when this symbol has some option activity. Not an identification."""
    for txn in transactions:
        if _symbol(txn.instrument) == symbol and _named_asset(txn.asset_type) == AssetType.OPTION.value:
            return True
    for event in events:
        if _symbol(event.symbol) == symbol and _named_asset(event.asset_type) == AssetType.OPTION.value:
            return True
    for lot in tax_lots:
        if _symbol(lot.symbol) == symbol and _named_asset(lot.asset_type) == AssetType.OPTION.value:
            return True
    return False


def _infer_sale_asset(
    *,
    symbol: str,
    sale_date: date,
    quantity: float,
    proceeds: float,
    row_text: str,
    transactions: list[Transaction],
    events: list[RealizedEvent],
    tax_lots: list[TaxLot],
) -> tuple[str, bool, Transaction | None]:
    """Instrument from the sale that matches this 1099 row.

    Returns (asset, determined, sell). ``determined`` is false when no sale
    matches and the symbol has option activity, or when stock and option
    sales both match. An option elsewhere in the export is not a scale.
    ``sell`` is set only when exactly one sell identified the instrument.
    """
    sells: list[Transaction] = []
    kinds: set[str] = set()
    for txn in transactions:
        if not _sale_matches_txn(
            txn,
            symbol=symbol,
            sale_date=sale_date,
            quantity=quantity,
            proceeds=proceeds,
        ):
            continue
        if not _description_allows(txn, row_text):
            continue
        sells.append(txn)
        kinds.add(_named_asset(txn.asset_type))
    for event in events:
        if not _sale_matches_event(
            event,
            symbol=symbol,
            sale_date=sale_date,
            quantity=quantity,
            proceeds=proceeds,
        ):
            continue
        kinds.add(_named_asset(event.asset_type))
    if len(kinds) == 1:
        asset = next(iter(kinds))
        same = [txn for txn in sells if _named_asset(txn.asset_type) == asset]
        sell = same[0] if len(same) == 1 else None
        return asset, True, sell
    if kinds or _symbol_has_option(symbol, transactions, events, tax_lots):
        return AssetType.STOCK.value, False, None
    return AssetType.STOCK.value, True, None


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
    used_events: set[int] | None = None,
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
    row_text = " ".join((row.description or "") for _, row in items)
    asset, determined, matched_sell = _infer_sale_asset(
        symbol=symbol,
        sale_date=sale_date,
        quantity=quantity,
        proceeds=proceeds,
        row_text=row_text,
        transactions=transactions,
        events=events,
        tax_lots=tax_lots,
    )
    # Undetermined stays on the share scale. Do not multiply by the contract
    # size just because an option exists for the symbol.
    scale = _display_scale(asset) if determined else 1.0
    raw_buys = _window_buys(transactions, symbol, sale_date)
    cap = _repurchase_cap(raw_buys, quantity)
    buys = _allocate_buys(raw_buys, repurchase_quantity=cap, disallowed=wash)
    # 1099 wash is already dollars. Scale only the premium paid for the buy.
    _apply_display_scale(buys, scale, scale_disallowed=False)
    closed: list[_ClosedLot] = []
    if determined:
        export_dates = {row.export_trade_date for _, row in items}
        export_date = next(iter(export_dates)) if len(export_dates) == 1 else None
        if export_date is not None:
            dollar_loss = max(0.0, cost_basis - proceeds)
            event_set = _claim_event_set(
                events,
                used_events if used_events is not None else set(),
                symbol,
                export_date,
                quantity,
                sale_loss=(dollar_loss / scale) if scale else dollar_loss,
                sell=matched_sell,
            )
            closed = _closed_lots_from_events(event_set, export_date)
    acquired, held_days = _shared_acquisition(closed)
    earliest = next(
        (buy.activity_date for buy in buys if buy.allocated_quantity > QTY_EPS),
        None,
    )
    open_basis, open_wash = _open_lot_numbers(tax_lots, symbol, earliest, scale)
    status: WashReconcileStatus = "on_1099_only" if determined else "not_compared"
    return _HopDraft(
        hop_id=_hop_id(symbol, sale_date, quantity, f"r{first_index}"),
        symbol=symbol,
        asset_type=asset,
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
        closed_lots=closed,
        engine_sale_loss=None,
        explained_loss_with_carry=loss,
        reconcile_status=status,
        difference=None,
        from_engine=False,
        not_compared_reason="" if determined else "undetermined",
    )


def _later_acquisition_dates(later: _HopDraft) -> set[date]:
    """Dates a later hop might have acquired. Any lot counts.

    Empty closed lots fall back to the single tacked date. Two different
    lot dates leave that tacked date empty, so the lot list is the link.
    """
    dates = {
        lot.purchase_date
        for lot in later.closed_lots
        if lot.purchase_date is not None
    }
    if dates:
        return dates
    if later.original_purchase_date is not None:
        return {later.original_purchase_date}
    return set()


def _covering_replacement_buys(
    earlier: _HopDraft,
    later: _HopDraft,
) -> list[_BuyDraft]:
    """Replacement buys whose combined allocation can cover a later sale.

    Any of the later hop's acquired dates may match a replacement buy.
    Quantity is the sum of every allocated replacement on that hop, so a
    40-share buy and a 60-share buy together cover a later 100-share sale.
    """
    dates = _later_acquisition_dates(later)
    if not dates:
        return []
    positive = [
        buy for buy in earlier.buys if buy.allocated_quantity > QTY_EPS
    ]
    if not any(buy.activity_date in dates for buy in positive):
        return []
    return positive


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
            covering = _covering_replacement_buys(earlier, later)
            covered = sum(buy.allocated_quantity for buy in covering)
            if covering and later.sale_quantity <= covered + QTY_EPS:
                union(left, right)
    groups: dict[int, list[_HopDraft]] = {}
    for index, hop in enumerate(hops):
        groups.setdefault(find(index), []).append(hop)
    return list(groups.values())


def _apply_carry(ordered: list[_HopDraft]) -> None:
    """Carry the prior hop's disallowed loss forward, including what it carried in.

    The nearest earlier hop whose replacement buys cover any later lot date
    supplies the amount. It is that hop's own carried-in plus its new
    disallowance, not a fraction of the replacement quantity.
    """
    for index, hop in enumerate(ordered):
        hop.hop_index = index
        carried = 0.0
        if index > 0:
            for earlier in reversed(ordered[:index]):
                covering = _covering_replacement_buys(earlier, hop)
                covered = sum(buy.allocated_quantity for buy in covering)
                if covered <= QTY_EPS:
                    continue
                carried = _cents(
                    earlier.carried_in_disallowed + earlier.disallowed_loss
                )
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
    if hop.reconcile_status == "not_compared":
        if hop.not_compared_reason == "ambiguous":
            return (
                "More than one 1099 grouping fits this sale, so no wash total was chosen."
            )
        if hop.not_compared_reason == "over_cap":
            return (
                "Too many 1099 rows match this sale to compare them without guessing."
            )
        if hop.not_compared_reason == "undetermined":
            return (
                "This 1099 sale could not be tied to one stock or option trade "
                "in the export, so it was not compared as shares or contracts."
            )
        return "No same-year 1099-B was compared for this sale."
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


def _basis_split(hop: _HopDraft) -> float:
    return _cents(
        sum(
            buy.explained_basis
            for buy in hop.buys
            if buy.allocated_quantity > QTY_EPS
        )
    )


def _basis_split_is_illustrative(hop: _HopDraft) -> bool:
    """True when the per-buy split is not the engine's adjusted basis."""
    if hop.engine_adjusted_cost_basis is None:
        return False
    return abs(_basis_split(hop) - hop.engine_adjusted_cost_basis) > CENTS


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
            f"{_fmt_money(hop.engine_sale_loss)} loss on this sale. "
            "That engine figure is the source of truth."
        )
    if _basis_split_is_illustrative(hop):
        line += (
            " The per-buy basis split is illustrative. "
            "The engine's adjusted basis is the source of truth."
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
        closed_lots=[
            WashClosedLot(
                quantity=lot.quantity,
                purchase_date=lot.purchase_date,
                holding_days=lot.holding_days,
            )
            for lot in hop.closed_lots
        ],
        carried_in_disallowed=hop.carried_in_disallowed,
        engine_sale_loss=hop.engine_sale_loss,
        explained_loss_with_carry=hop.explained_loss_with_carry,
        plain_english=hop.plain_english,
        reconcile_status=hop.reconcile_status,
        difference=hop.difference,
    )


# Worst first. on_1099_only outranks not_on_1099: a 1099 wash the engine
# missed is a stronger mismatch than an export wash the 1099 does not list.
# not_compared is milder than either, because no same-year figure was chosen.
_STATUS_RANK = {
    "differs": 4,
    "on_1099_only": 3,
    "not_on_1099": 2,
    "not_compared": 1,
    "matches": 0,
}


def _worst_hop(hops: list[_HopDraft]) -> _HopDraft:
    """Highest rank, then the larger dollar gap, then the earlier hop."""
    return max(
        hops,
        key=lambda hop: (
            _STATUS_RANK.get(hop.reconcile_status, 0),
            abs(hop.difference or 0.0),
            -hop.hop_index,
        ),
    )


def _finish(group: list[_HopDraft]) -> WashSaleChain:
    ordered = sorted(group, key=lambda hop: (hop.sale_date, hop.hop_id))
    _apply_carry(ordered)
    for hop in ordered:
        hop.plain_english = plain_english_line(hop)
    head = ordered[0]
    worst = _worst_hop(ordered)
    return WashSaleChain(
        chain_id=head.hop_id,
        symbol=head.symbol,
        reconcile_status=worst.reconcile_status,
        difference=worst.difference,
        plain_english=worst.plain_english,
        hops=[_to_model(hop) for hop in ordered],
    )


def _supplemental_has_totals(supplemental: Supplemental1099Summary | None) -> bool:
    """True when the 1099 summary has broker totals, even with no lot rows."""
    if supplemental is None:
        return False
    amounts = (
        supplemental.short_term_proceeds,
        supplemental.short_term_cost_basis,
        supplemental.short_term_wash_sale_disallowed,
        supplemental.short_term_net_gain,
        supplemental.long_term_proceeds,
        supplemental.long_term_cost_basis,
        supplemental.long_term_wash_sale_disallowed,
        supplemental.long_term_net_gain,
    )
    return any(abs(float(value or 0.0)) >= 0.01 for value in amounts)


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
    # Totals with no lot report were not compared row by row. An empty search
    # would otherwise say the sale was missing from the 1099.
    has_totals = _supplemental_has_totals(supplemental)
    rows_unusable = same_year and lot_match_report is None and has_totals
    comparable = same_year and not rows_unusable
    rows = _report_rows(lot_match_report) if comparable else []
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
                same_year=comparable,
            )
        )
    chains = [_finish(group) for group in _link_engine_hops(engine_hops)]
    if comparable:
        leftover = _group_rows(
            rows,
            used=used_rows,
            require_wash=True,
        )
        for group in leftover:
            hop = _hop_from_1099_group(
                group.items,
                transactions=transactions,
                events=realized_events,
                tax_lots=tax_lots,
                used_events=used_events,
            )
            if hop is None:
                continue
            chains.append(_finish([hop]))
    chains.sort(key=lambda chain: (chain.hops[0].sale_date, chain.symbol, chain.chain_id))
    return chains
