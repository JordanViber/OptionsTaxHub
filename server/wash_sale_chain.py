"""Read-only wash-sale chain explainer.

Builds a display model from wash flags, FIFO realized events, and same-year
1099-B lot matches. Does not rewrite wash_sale.py, lot basis, or tax totals.
Does not set WashSaleFlag.purchase_date (that field feeds ST/LT wash splits).

v1 explains stock sales only. An option flag or option 1099 row is one
not-compared entry per symbol and sale date, with no dollar figure. A row
whose instrument cannot be told is not compared and is not explained as stock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from csv_parser import RealizedEvent
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
    proceeds: float | None
    cost_basis: float | None
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
    # v1 does not carry a disallowance from an earlier sale. These stay empty.
    carried_in_disallowed: float | None = None
    engine_sale_loss: float | None = None
    explained_loss_with_carry: float | None = None
    plain_english: str = ""
    reconcile_status: WashReconcileStatus = "not_on_1099"
    difference: float | None = None
    hop_index: int = 0
    from_engine: bool = True
    # Why a hop was not compared. Drives copy only; not a tax field.
    # "" | "no_1099" | "ambiguous" | "over_cap" | "unidentified" | "option"
    # | "unclassified" | "overlap" | "unassigned"
    not_compared_reason: str = ""
    # Display-only match bookkeeping. Not copied onto the API model.
    row_target: float | None = None
    compare_1099: bool = False


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


def _attach_flag_sale(
    transactions: list[Transaction],
    events: list[RealizedEvent],
    used_txns: set[int],
    used_events: set[int],
    *,
    symbol: str,
    sale_date: date,
    quantity: float,
    sale_loss: float,
) -> tuple[int | None, Transaction | None, list[tuple[int, RealizedEvent]], bool]:
    """Attach one sell only when quantity, proceeds, and loss agree.

    Two same-day sells of the same size are not claimed by list order. A
    sell is eligible when a lot set matches its proceeds and the flag loss
    within CENTS. No eligible sell, and more than one candidate, leaves the
    hop unidentified so another sale's proceeds are not shown.
    """
    candidates: list[tuple[int, Transaction]] = []
    for index, txn in enumerate(transactions):
        if index in used_txns:
            continue
        if txn.trans_code not in _SELL_CODES:
            continue
        if _symbol(txn.instrument) != symbol:
            continue
        if txn.activity_date != sale_date:
            continue
        if not _qty_close(txn.quantity, quantity):
            continue
        # An option sell is not this stock sale, even on the same day and size.
        if _is_option_asset(txn.asset_type):
            continue
        candidates.append((index, txn))
    confirmed: list[tuple[int, Transaction, list[tuple[int, RealizedEvent]]]] = []
    for index, txn in candidates:
        event_set = _claim_event_set(
            events,
            set(used_events),
            symbol,
            sale_date,
            quantity,
            sale_loss=sale_loss,
            sell=txn,
        )
        if event_set:
            confirmed.append((index, txn, event_set))
    if confirmed:
        index, txn, event_set = confirmed[0]
        used_txns.add(index)
        for event_index, _event in event_set:
            used_events.add(event_index)
        return index, txn, event_set, False
    if len(candidates) == 1:
        index, txn = candidates[0]
        used_txns.add(index)
        return index, txn, [], False
    if not candidates:
        event_set = _claim_event_set(
            events,
            used_events,
            symbol,
            sale_date,
            quantity,
            sale_loss=sale_loss,
            sell=None,
        )
        return None, None, event_set, False
    return None, None, [], True


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
        and not _is_option_asset(event.asset_type)
    ]


def _qty_subsets(items: list, quantity: float, qty_of) -> list[list]:
    """Every subset whose quantities sum to ``quantity`` within QTY_EPS.

    More than ``_MAX_MATCH_CANDIDATES`` items is not searched. Callers treat
    that as not compared and do not guess a subset.
    """
    count = len(items)
    if count > _MAX_MATCH_CANDIDATES:
        return []
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
        proceeds = sum(float(event.sale_proceeds) for _, event in subset)
        basis = sum(float(event.cost_basis) for _, event in subset)
        if abs((basis - proceeds) - float(sale_loss)) > CENTS:
            continue
        if sell is not None and float(sell.quantity) > QTY_EPS:
            target = float(sell.quantity) * float(sell.price)
            if abs(proceeds - target) > CENTS:
                continue
        good.append(subset)
    # Two sets that both fit the loss would be a guess. Show no lots or dates.
    if len(good) != 1:
        return []
    chosen = good[0]
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
    per-lot list is the display.
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


def _buys_for_asset(buys: list[Transaction], asset: str) -> list[Transaction]:
    """Replacement buys of the sale's instrument. A stock buy is not an option replacement."""
    return [buy for buy in buys if _named_asset(buy.asset_type) == asset]


def _apply_display_scale(
    drafts: list[_BuyDraft],
    raw_buys: list[Transaction],
    *,
    scale_disallowed: bool = True,
) -> None:
    """Round each stock buy. Option buys are not passed in.

    1099 wash amounts are already dollars, so a 1099-only hop passes
    scale_disallowed=False.
    """
    for draft, _txn in zip(drafts, raw_buys):
        raw_price = draft.price
        draft.cash_cost = _cents(raw_price * draft.quantity)
        if scale_disallowed:
            draft.allocated_disallowed = _cents(draft.allocated_disallowed)
        draft.explained_basis = _cents(
            raw_price * draft.allocated_quantity + draft.allocated_disallowed
        )
        draft.price = _cents(raw_price)


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
    """Stock open-lot basis and wash for this replacement date.

    An option lot with the same symbol and date is not the stock lot.
    """
    if purchase_date is None:
        return None, None
    for lot in tax_lots:
        if _symbol(lot.symbol) != symbol:
            continue
        if lot.purchase_date != purchase_date:
            continue
        if _named_asset(lot.asset_type) != AssetType.STOCK.value:
            continue
        return _cents(float(lot.total_cost_basis)), _cents(float(lot.wash_sale_disallowed))
    return None, None


def _report_rows(report: LotMatchReport | None) -> list[LotMatchRow]:
    if report is None:
        return []
    return list(report.matched) + list(report.gap) + list(report.unmatched)


@dataclass
class _SaleGroup:
    """1099 rows that are one sale: same symbol and sell date.

    Rows are ordered by per-share proceeds. The first row's per-share
    proceeds is the anchor price. A later row stays in that cluster when
    its total proceeds are within CENTS of anchor price times its quantity.
    A one-share fill at $10.00, $10.01, and $10.02 is one cluster, because
    each total is within two cents of $10.00. Ten shares a penny apart
    differ by ten cents and stay apart. $33.33 and $66.67 (one share and
    two shares at that price) are one sale. A zero-quantity row only joins
    another zero-quantity row.
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

    Cluster by symbol and sale date. Sort by per-share proceeds, then start
    a new cluster unless the row's total proceeds are within CENTS of the
    anchor price times its quantity. The anchor is the first row's per-share
    proceeds. A zero-quantity row only joins another zero-quantity row.
    Equal prices keep their original index so the hop id stays on the first
    of those rows.
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
        ordered = sorted(
            pending[key],
            key=lambda item: (
                _row_unit_proceeds(item[1]) is not None,
                _row_unit_proceeds(item[1]) or 0.0,
                item[0],
            ),
        )
        clusters: list[_SaleGroup] = []
        for index, row in ordered:
            unit = _row_unit_proceeds(row)
            current = clusters[-1] if clusters else None
            anchor = current.unit_proceeds if current is not None else None
            same_blank = unit is None and anchor is None
            close = anchor is not None and _row_total_matches_price(row, anchor)
            if current is not None and (same_blank or close):
                current.items.append((index, row))
                continue
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
) -> float | None:
    if sell is not None and float(sell.quantity) > QTY_EPS:
        return float(sell.price)
    total_qty = sum(float(event.quantity) for event in events)
    if events and total_qty > QTY_EPS:
        proceeds = sum(float(event.sale_proceeds) for event in events)
        return proceeds / total_qty
    return None


def _row_total_matches_price(row: LotMatchRow, price: float) -> bool:
    """True when total proceeds are within CENTS of price times quantity.

    Fractional shares use the same dollar test as whole shares. A
    zero-quantity row has no total to compare and does not match.
    """
    quantity = float(row.quantity or 0.0)
    if abs(quantity) <= QTY_EPS:
        return False
    proceeds = float(row.proceeds_1099 or 0.0)
    return abs(proceeds - float(price) * quantity) <= CENTS


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
    """Same-symbol trade-date rows whose total proceeds match the sale price.

    A row belongs when abs(proceeds - target price * quantity) is within
    CENTS. With no target price, any row that has a quantity is a candidate.
    A zero-quantity row is not.
    """
    candidates: list[tuple[int, LotMatchRow]] = []
    for index, row in enumerate(rows):
        if index in used:
            continue
        if _symbol(row.symbol) != symbol:
            continue
        if row.export_trade_date != sale_date:
            continue
        if _row_unit_proceeds(row) is None:
            continue
        if target is not None and not _row_total_matches_price(row, target):
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
    unit_target: float | None,
    unassigned: set[int],
) -> tuple[list[tuple[int, LotMatchRow]] | None, str]:
    """Bounded subset-sum of 1099 rows for one flag.

    Returns (rows, reason). reason is "" when a single wash total fits,
    "ambiguous" when two subsets sum to the quantity but their wash totals
    differ by more than CENTS, and "over_cap" when more than 12 rows are
    close enough on total proceeds to be candidates. Neither reason
    chooses a wash total. Over-cap reserves every candidate. An ambiguous
    result reserves only rows that sit in a disagreeing subset. Any other
    candidate is marked unassigned so it becomes its own not-compared entry
    instead of disappearing. A single wash total also reserves every row
    used by any tied subset, not only the lex-smallest one, so one sale
    does not leave its other grouping as a second chain. Two sales that
    could claim the same rows are refused before this runs. None rows and
    reason "" means nothing matched, and those rows stay available.
    """
    candidates = _match_candidates(
        rows,
        used=used,
        symbol=symbol,
        sale_date=sale_date,
        target=unit_target,
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
        in_subset: set[int] = set()
        for subset in subsets:
            for index, _row in subset:
                in_subset.add(index)
                used.add(index)
        for index, _row in candidates:
            if index not in in_subset:
                used.add(index)
                unassigned.add(index)
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


def _is_option_asset(asset: object) -> bool:
    return _named_asset(asset) == AssetType.OPTION.value


_OPTION_WASH_LINE = "Option wash sales aren't explained here yet."
_UNCLASSIFIED_LINE = (
    "This 1099 sale is not labeled as stock or an option, so it was not compared."
)
_UNIDENTIFIED_LINE = (
    "This sale could not be tied to one trade's proceeds and loss, "
    "so it was not compared."
)
_OPTION_WORD = re.compile(r"\b(?:calls?|puts?|options?)\b", re.IGNORECASE)


def _overlap_line(symbol: str, sale_date: date) -> str:
    when = _fmt_date(sale_date, with_year=True)
    return (
        f"{symbol} on {when} has a stock sale and an option close of the same "
        "quantity, so neither was compared."
    )


def _unassigned_line(symbol: str, sale_date: date) -> str:
    when = _fmt_date(sale_date, with_year=True)
    return (
        f"{symbol} on {when} overlapped more than one possible sale, "
        "so it was not compared."
    )


def _flag_sale_is_option(
    transactions: list[Transaction],
    events: list[RealizedEvent],
    *,
    symbol: str,
    sale_date: date,
    quantity: float,
) -> bool:
    """True when this flag's sale is an option.

    A stock sell of the same symbol keeps the flag on the stock path.
    Option events are not stock lots, and a stock lot is not an option sale.
    """
    saw_stock_sell = False
    saw_option_sell = False
    for txn in transactions:
        if txn.trans_code not in _SELL_CODES:
            continue
        if _symbol(txn.instrument) != symbol or txn.activity_date != sale_date:
            continue
        if not _qty_close(txn.quantity, quantity):
            continue
        if _is_option_asset(txn.asset_type):
            saw_option_sell = True
        else:
            saw_stock_sell = True
    if saw_stock_sell:
        return False
    if saw_option_sell:
        return True
    stock_events: list[RealizedEvent] = []
    option_events: list[RealizedEvent] = []
    for event in events:
        if _symbol(event.symbol) != symbol or event.sale_date != sale_date:
            continue
        if float(event.quantity) <= QTY_EPS:
            continue
        if _is_option_asset(event.asset_type):
            option_events.append(event)
        else:
            stock_events.append(event)
    if not option_events:
        return False
    if not stock_events:
        return True
    if any(_qty_close(float(event.quantity), quantity) for event in stock_events):
        return False
    return any(_qty_close(float(event.quantity), quantity) for event in option_events)


def _option_hop(
    *,
    hop_id: str,
    symbol: str,
    sale_date: date,
    sale_quantity: float,
) -> _HopDraft:
    """One option entry. No dollar figure until option washes are explained."""
    return _HopDraft(
        hop_id=hop_id,
        symbol=symbol,
        asset_type=AssetType.OPTION.value,
        sale_date=sale_date,
        sale_quantity=float(sale_quantity),
        proceeds=0.0,
        cost_basis=0.0,
        loss=0.0,
        buys=[],
        disallowed_loss=0.0,
        engine_disallowed_loss=None,
        form_1099_wash=None,
        engine_adjusted_cost_basis=None,
        open_lot_total_cost_basis=None,
        open_lot_wash_sale_disallowed=None,
        original_purchase_date=None,
        original_holding_days=None,
        carryover_days=None,
        tacked_acquisition_date=None,
        closed_lots=[],
        carried_in_disallowed=None,
        engine_sale_loss=None,
        explained_loss_with_carry=None,
        plain_english=_OPTION_WASH_LINE,
        reconcile_status="not_compared",
        difference=None,
        from_engine=False,
        not_compared_reason="option",
        compare_1099=False,
    )


def _bare_not_compared_hop(
    *,
    hop_id: str,
    symbol: str,
    asset_type: str,
    sale_date: date,
    sale_quantity: float,
    reason: str,
    proceeds: float | None = 0.0,
    cost_basis: float | None = 0.0,
    loss: float = 0.0,
    form_1099_wash: float | None = None,
) -> _HopDraft:
    """A row we will not explain. Dollars stay off unless the caller passes facts."""
    return _HopDraft(
        hop_id=hop_id,
        symbol=symbol,
        asset_type=asset_type,
        sale_date=sale_date,
        sale_quantity=float(sale_quantity),
        proceeds=proceeds,
        cost_basis=cost_basis,
        loss=loss,
        buys=[],
        disallowed_loss=0.0,
        engine_disallowed_loss=None,
        form_1099_wash=form_1099_wash,
        engine_adjusted_cost_basis=None,
        open_lot_total_cost_basis=None,
        open_lot_wash_sale_disallowed=None,
        original_purchase_date=None,
        original_holding_days=None,
        carryover_days=None,
        tacked_acquisition_date=None,
        closed_lots=[],
        carried_in_disallowed=None,
        engine_sale_loss=None,
        explained_loss_with_carry=None,
        reconcile_status="not_compared",
        difference=None,
        from_engine=False,
        not_compared_reason=reason,
        compare_1099=False,
    )


def _hop_from_flag(
    flag: WashSaleFlag,
    *,
    flag_index: int,
    transactions: list[Transaction],
    events: list[RealizedEvent],
    tax_lots: list[TaxLot],
    used_events: set[int],
    used_txns: set[int],
    same_year: bool,
) -> _HopDraft:
    symbol = _symbol(flag.symbol)
    if _flag_sale_is_option(
        transactions,
        events,
        symbol=symbol,
        sale_date=flag.sale_date,
        quantity=float(flag.sale_quantity),
    ):
        return _option_hop(
            hop_id=_hop_id(
                symbol,
                flag.sale_date,
                flag.sale_quantity,
                f"f{flag_index}",
            ),
            symbol=symbol,
            sale_date=flag.sale_date,
            sale_quantity=float(flag.sale_quantity),
        )
    sell_index, sell, event_set, unresolved = _attach_flag_sale(
        transactions,
        events,
        used_txns,
        used_events,
        symbol=symbol,
        sale_date=flag.sale_date,
        quantity=float(flag.sale_quantity),
        sale_loss=float(flag.sale_loss),
    )
    event_index = min((index for index, _event in event_set), default=None)
    asset = _asset_type(sell)
    if sell is None and event_set:
        asset = _named_asset(event_set[0][1].asset_type)
    closed = _closed_lots_from_events(event_set, flag.sale_date) if event_set else []
    acquired, held_days = _shared_acquisition(closed)
    if unresolved:
        # Two same-day sells of the same size. Do not pick one's proceeds,
        # basis, or replacement buys, and do not show those fields as zero.
        return _bare_not_compared_hop(
            hop_id=_hop_id(
                symbol,
                flag.sale_date,
                flag.sale_quantity,
                _hop_discriminator(
                    event_index=None,
                    sell_index=None,
                    flag_index=flag_index,
                ),
            ),
            symbol=symbol,
            asset_type=asset,
            sale_date=flag.sale_date,
            sale_quantity=float(flag.sale_quantity),
            reason="unidentified",
            proceeds=None,
            cost_basis=None,
        )
    if event_set:
        # RealizedEvent basis and proceeds are already contract dollars.
        proceeds = _cents(sum(float(event.sale_proceeds) for _index, event in event_set))
        cost_basis = _cents(sum(float(event.cost_basis) for _index, event in event_set))
    elif sell is not None:
        proceeds = _cents(float(sell.quantity) * float(sell.price))
        cost_basis = _cents(proceeds + float(flag.sale_loss))
    else:
        proceeds = 0.0
        cost_basis = _cents(float(flag.sale_loss))
    raw_buys = (
        [buy for _, buy in _find_qualifying_repurchases(sell, _buys_sorted(transactions))]
        if sell is not None
        else []
    )
    raw_buys = _buys_for_asset(raw_buys, asset)
    buys = _allocate_buys(
        raw_buys,
        repurchase_quantity=float(flag.repurchase_quantity),
        disallowed=float(flag.disallowed_loss),
    )
    _apply_display_scale(buys, raw_buys)
    loss = _cents(float(flag.sale_loss))
    disallowed = _cents(float(flag.disallowed_loss))
    open_basis, open_wash = _open_lot_numbers(
        tax_lots, symbol, flag.repurchase_date
    )
    status: WashReconcileStatus = "not_compared"
    not_compared_reason = "no_1099"
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
        engine_adjusted_cost_basis=_cents(float(flag.adjusted_cost_basis)),
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
        difference=None,
        not_compared_reason=not_compared_reason,
        row_target=_target_unit_proceeds(
            sell,
            [event for _index, event in event_set],
        ),
        compare_1099=same_year and not unresolved,
    )


def _apply_1099_match(
    hop: _HopDraft,
    rows: list[LotMatchRow],
    used_rows: set[int],
    unassigned: set[int],
    *,
    claim_absence: bool,
) -> None:
    """Compare one flag to 1099 rows. Same-day ties are marked before this runs.

    ``claim_absence`` is false when the 1099 parse is partial (totals did
    not match). A row that was found can still match or differ. A sale with
    no row is not compared, because the missing row may simply be unparsed.
    """
    taken, blocked = _match_sale_rows(
        rows,
        used=used_rows,
        symbol=hop.symbol,
        sale_date=hop.sale_date,
        quantity=hop.sale_quantity,
        unit_target=hop.row_target,
        unassigned=unassigned,
    )
    if blocked:
        hop.reconcile_status = "not_compared"
        hop.not_compared_reason = blocked
        hop.form_1099_wash = None
        hop.difference = None
        return
    if taken is None:
        if claim_absence:
            hop.reconcile_status = "not_on_1099"
            hop.not_compared_reason = ""
        else:
            hop.reconcile_status = "not_compared"
            hop.not_compared_reason = "no_1099"
        hop.form_1099_wash = None
        hop.difference = None
        return
    for index, _row in taken:
        used_rows.add(index)
    wash_total = _cents(
        sum(float(row.wash_sale_disallowed or 0.0) for _, row in taken)
    )
    status, difference, form_wash = _status_for_wash(hop.disallowed_loss, wash_total)
    hop.reconcile_status = status
    hop.difference = difference
    hop.form_1099_wash = form_wash
    hop.not_compared_reason = ""


def _option_text(value: str | None) -> bool:
    """True when the description names a call, put, or option.

    Plurals count, including at the end of the text. A longer word such as
    callable, recall, input, or optional does not.
    """
    return _OPTION_WORD.search(value or "") is not None


def _description_allows(txn: Transaction, _row_text: str) -> bool:
    """Reject a stock sale whose own description names an option.

    A 1099 row that says call, put, or option is already an option entry,
    so this match does not read that row text.
    """
    is_option = _named_asset(txn.asset_type) == AssetType.OPTION.value
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
    target = float(txn.quantity) * float(txn.price)
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


def _row_involves_option(
    row: LotMatchRow,
    *,
    transactions: list[Transaction],
    events: list[RealizedEvent],
) -> bool:
    """True when this 1099 row is an option sale.

    Call, put, or option wording counts. So does a matching option trade.
    An option somewhere else on the symbol does not.
    """
    if _option_text(row.description):
        return True
    symbol = _symbol(row.symbol)
    sale_date = row.export_trade_date or row.date_sold_1099
    if not symbol or sale_date is None:
        return False
    quantity = float(row.quantity or 0.0)
    proceeds = float(row.proceeds_1099 or 0.0)
    for txn in transactions:
        if not _is_option_asset(txn.asset_type):
            continue
        if _sale_matches_txn(
            txn,
            symbol=symbol,
            sale_date=sale_date,
            quantity=quantity,
            proceeds=proceeds,
        ):
            return True
    for event in events:
        if not _is_option_asset(event.asset_type):
            continue
        if _sale_matches_event(
            event,
            symbol=symbol,
            sale_date=sale_date,
            quantity=quantity,
            proceeds=proceeds,
        ):
            return True
    return False


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
    if not determined:
        # The instrument is unknown. Do not narrate the row as shares.
        return _bare_not_compared_hop(
            hop_id=_hop_id(symbol, sale_date, quantity, f"r{first_index}"),
            symbol=symbol,
            asset_type="unclassified",
            sale_date=sale_date,
            sale_quantity=quantity,
            reason="unclassified",
        )
    # Option buys are not stock replacements. The sale keeps buys of its own
    # asset. Nothing here is scaled by contract size.
    raw_buys = _buys_for_asset(
        [
            buy
            for buy in _window_buys(transactions, symbol, sale_date)
            if not _is_option_asset(buy.asset_type)
        ],
        asset,
    )
    cap = _repurchase_cap(raw_buys, quantity)
    buys = _allocate_buys(raw_buys, repurchase_quantity=cap, disallowed=wash)
    _apply_display_scale(buys, raw_buys, scale_disallowed=False)
    export_dates = {row.export_trade_date for _, row in items}
    export_date = next(iter(export_dates)) if len(export_dates) == 1 else None
    closed: list[_ClosedLot] = []
    if export_date is not None:
        dollar_loss = max(0.0, cost_basis - proceeds)
        event_set = _claim_event_set(
            events,
            used_events if used_events is not None else set(),
            symbol,
            export_date,
            quantity,
            sale_loss=dollar_loss,
            sell=matched_sell,
        )
        closed = _closed_lots_from_events(event_set, export_date)
    acquired, held_days = _shared_acquisition(closed)
    earliest = next(
        (buy.activity_date for buy in buys if buy.allocated_quantity > QTY_EPS),
        None,
    )
    open_basis, open_wash = _open_lot_numbers(tax_lots, symbol, earliest)
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
        reconcile_status="on_1099_only",
        difference=None,
        from_engine=False,
        not_compared_reason="",
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
        return "No same-year 1099-B was compared for this sale."
    return (
        "This wash is not on your 1099. It was found only in this Robinhood export."
    )


def _needs_year(sale: date, buys: list[_BuyDraft]) -> bool:
    years = {sale.year}
    years.update(buy.activity_date.year for buy in buys)
    return len(years) > 1


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
    """One sentence for a hop. A one-hop chain uses this as its headline."""
    if hop.not_compared_reason == "option":
        return _OPTION_WASH_LINE
    if hop.not_compared_reason == "unclassified":
        return _UNCLASSIFIED_LINE
    if hop.not_compared_reason == "overlap":
        return _overlap_line(hop.symbol, hop.sale_date)
    if hop.not_compared_reason == "unassigned":
        return _unassigned_line(hop.symbol, hop.sale_date)
    if hop.not_compared_reason == "unidentified":
        return _UNIDENTIFIED_LINE
    listed = _listed_buys(hop)
    with_year = _needs_year(hop.sale_date, listed)
    unit = ""
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
        not_compared_reason=hop.not_compared_reason,
    )


def _finish(hop: _HopDraft) -> WashSaleChain:
    """One wash flag is one chain. A later sale is not linked to this one."""
    hop.hop_index = 0
    hop.carried_in_disallowed = None
    hop.explained_loss_with_carry = None
    hop.plain_english = plain_english_line(hop)
    return WashSaleChain(
        chain_id=hop.hop_id,
        symbol=hop.symbol,
        reconcile_status=hop.reconcile_status,
        difference=hop.difference,
        plain_english=hop.plain_english,
        hops=[_to_model(hop)],
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


def _has_instrument_close(
    transactions: list[Transaction],
    events: list[RealizedEvent],
    *,
    symbol: str,
    sale_date: date,
    quantity: float,
    option: bool,
) -> bool:
    """True when a sell or realized lot of this instrument matches the key."""
    for txn in transactions:
        if txn.trans_code not in _SELL_CODES:
            continue
        if _symbol(txn.instrument) != symbol or txn.activity_date != sale_date:
            continue
        if not _qty_close(txn.quantity, quantity):
            continue
        if _is_option_asset(txn.asset_type) is option:
            return True
    for event in events:
        if _symbol(event.symbol) != symbol or event.sale_date != sale_date:
            continue
        if float(event.quantity) <= QTY_EPS:
            continue
        if not _qty_close(float(event.quantity), quantity):
            continue
        if _is_option_asset(event.asset_type) is option:
            return True
    return False


def _unassigned_from_row(row: LotMatchRow, index: int) -> _HopDraft | None:
    """A 1099 row that overlapped a match and was not part of a chosen subset."""
    symbol = _symbol(row.symbol)
    sale_date = _row_sale_date(row)
    if not symbol or sale_date is None:
        return None
    quantity = float(row.quantity or 0.0)
    proceeds = _cents(float(row.proceeds_1099 or 0.0))
    cost_basis = _cents(float(row.cost_basis_1099 or 0.0))
    wash = _cents(float(row.wash_sale_disallowed or 0.0))
    return _bare_not_compared_hop(
        hop_id=_hop_id(symbol, sale_date, quantity, f"u{index}"),
        symbol=symbol,
        asset_type=AssetType.STOCK.value,
        sale_date=sale_date,
        sale_quantity=quantity,
        reason="unassigned",
        proceeds=proceeds,
        cost_basis=cost_basis,
        loss=_cents(max(0.0, cost_basis - proceeds)),
        form_1099_wash=wash if wash >= 0.01 else None,
    )


def _refuse_same_day_1099_ties(
    hops: list[_HopDraft],
    rows: list[LotMatchRow],
    used_rows: set[int],
    unassigned: set[int],
) -> None:
    """Two same-day sales that could claim one 1099 row are both not compared.

    Rows that sit in a quantity subset stay with those sales. A candidate
    that fits no subset is its own not-compared entry. No sale is chosen.
    """
    comparable = [hop for hop in hops if hop.compare_1099]
    candidates_for: dict[int, list[int]] = {}
    for hop in comparable:
        candidates_for[id(hop)] = [
            index
            for index, _row in _match_candidates(
                rows,
                used=used_rows,
                symbol=hop.symbol,
                sale_date=hop.sale_date,
                target=hop.row_target,
            )
        ]
    by_day: dict[tuple[str, date], list[_HopDraft]] = {}
    for hop in comparable:
        by_day.setdefault((hop.symbol, hop.sale_date), []).append(hop)
    for day_hops in by_day.values():
        parent = list(range(len(day_hops)))

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

        for left in range(len(day_hops)):
            left_rows = set(candidates_for[id(day_hops[left])])
            for right in range(left + 1, len(day_hops)):
                right_rows = set(candidates_for[id(day_hops[right])])
                if left_rows & right_rows:
                    union(left, right)
        components: dict[int, list[_HopDraft]] = {}
        for index, hop in enumerate(day_hops):
            components.setdefault(find(index), []).append(hop)
        for group in components.values():
            if len(group) < 2:
                continue
            subset_rows: set[int] = set()
            candidate_rows: set[int] = set()
            for hop in group:
                hop.reconcile_status = "not_compared"
                hop.not_compared_reason = "ambiguous"
                hop.form_1099_wash = None
                hop.difference = None
                hop.compare_1099 = False
                chosen = [
                    (index, rows[index])
                    for index in candidates_for[id(hop)]
                    if 0 <= index < len(rows)
                ]
                candidate_rows.update(index for index, _row in chosen)
                # More than 12 rows is not searched. Those candidates stay
                # unassigned, so the tie does not guess a grouping.
                if len(chosen) > _MAX_MATCH_CANDIDATES:
                    continue
                for subset in _qty_subsets(
                    chosen,
                    hop.sale_quantity,
                    lambda item: float(item[1].quantity or 0.0),
                ):
                    for index, _row in subset:
                        subset_rows.add(index)
            for index in candidate_rows:
                used_rows.add(index)
                if index not in subset_rows:
                    unassigned.add(index)


def _option_1099_hops(
    rows: list[LotMatchRow],
    used: set[int],
    *,
    transactions: list[Transaction],
    events: list[RealizedEvent],
) -> list[_HopDraft]:
    """Reserve option 1099 rows and return one hop per symbol and sale date.

    The hop quantity is the total of that day's option lots. A lot quantity
    is not an option close. The rows leave the stock candidate pools.
    """
    grouped: dict[tuple[str, date], list[tuple[int, LotMatchRow]]] = {}
    for index, row in enumerate(rows):
        if float(row.wash_sale_disallowed or 0.0) < 0.01:
            continue
        if not _row_involves_option(row, transactions=transactions, events=events):
            continue
        symbol = _symbol(row.symbol)
        sale_date = row.export_trade_date or row.date_sold_1099
        if not symbol or sale_date is None:
            continue
        used.add(index)
        grouped.setdefault((symbol, sale_date), []).append((index, row))
    hops: list[_HopDraft] = []
    for (symbol, sale_date), members in grouped.items():
        index = members[0][0]
        quantity = sum(float(row.quantity or 0.0) for _index, row in members)
        hops.append(
            _option_hop(
                hop_id=_hop_id(symbol, sale_date, quantity, f"r{index}"),
                symbol=symbol,
                sale_date=sale_date,
                sale_quantity=quantity,
            )
        )
    return hops


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
    # would otherwise say the sale was missing from the 1099. A report whose
    # totals do not match is a partial parse: rows that were found can still
    # match, but a missing row is not claimed absent.
    has_totals = _supplemental_has_totals(supplemental)
    rows_unusable = same_year and lot_match_report is None and has_totals
    comparable = same_year and not rows_unusable
    claim_absence = lot_match_report is None or lot_match_report.totals_ok
    rows = _report_rows(lot_match_report) if comparable else []
    used_rows: set[int] = set()
    unassigned: set[int] = set()
    used_events: set[int] = set()
    used_txns: set[int] = set()
    # Option 1099 rows leave the stock pools before any tie or match.
    option_hops = (
        _option_1099_hops(
            rows,
            used_rows,
            transactions=transactions,
            events=realized_events,
        )
        if comparable
        else []
    )
    option_by_day: dict[tuple[str, date], _HopDraft] = {}
    for hop in option_hops:
        option_by_day.setdefault((hop.symbol, hop.sale_date), hop)
    engine_hops: list[_HopDraft] = []
    for flag_index, flag in enumerate(wash_sale_flags):
        if abs(float(flag.disallowed_loss)) < 0.01:
            continue
        symbol = _symbol(flag.symbol)
        quantity = float(flag.sale_quantity)
        stock_close = _has_instrument_close(
            transactions,
            realized_events,
            symbol=symbol,
            sale_date=flag.sale_date,
            quantity=quantity,
            option=False,
        )
        # A 1099 option lot of this quantity is not a close. Only a real
        # option sale or realized option lot holds the stock wash back.
        option_close = _has_instrument_close(
            transactions,
            realized_events,
            symbol=symbol,
            sale_date=flag.sale_date,
            quantity=quantity,
            option=True,
        )
        if stock_close and option_close:
            # One hop per flag. Do not borrow the other side's proceeds.
            # The option id is separate so it does not share the stock chain id.
            engine_hops.append(
                _bare_not_compared_hop(
                    hop_id=_hop_id(symbol, flag.sale_date, quantity, f"f{flag_index}"),
                    symbol=symbol,
                    asset_type=AssetType.STOCK.value,
                    sale_date=flag.sale_date,
                    sale_quantity=quantity,
                    reason="overlap",
                )
            )
            option_by_day.setdefault(
                (symbol, flag.sale_date),
                _option_hop(
                    hop_id=_hop_id(symbol, flag.sale_date, quantity, f"o{flag_index}"),
                    symbol=symbol,
                    sale_date=flag.sale_date,
                    sale_quantity=quantity,
                ),
            )
        else:
            hop = _hop_from_flag(
                flag,
                flag_index=flag_index,
                transactions=transactions,
                events=realized_events,
                tax_lots=tax_lots,
                used_events=used_events,
                used_txns=used_txns,
                same_year=comparable,
            )
            if hop.not_compared_reason == "option":
                option_by_day.setdefault((hop.symbol, hop.sale_date), hop)
            else:
                engine_hops.append(hop)
    _refuse_same_day_1099_ties(engine_hops, rows, used_rows, unassigned)
    for hop in engine_hops:
        if hop.compare_1099 and hop.not_compared_reason != "ambiguous":
            _apply_1099_match(
                hop,
                rows,
                used_rows,
                unassigned,
                claim_absence=claim_absence,
            )
    # Each flag is its own chain. A later sale of the replacement is not linked.
    chains = [_finish(hop) for hop in engine_hops]
    for hop in option_by_day.values():
        chains.append(_finish(hop))
    if comparable:
        for index in sorted(unassigned):
            hop = _unassigned_from_row(rows[index], index)
            if hop is None:
                continue
            chains.append(_finish(hop))
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
            chains.append(_finish(hop))
    chains.sort(key=lambda chain: (chain.hops[0].sale_date, chain.symbol, chain.chain_id))
    return chains
