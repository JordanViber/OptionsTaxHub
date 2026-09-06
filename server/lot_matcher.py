"""Match parsed 1099-B lots to CSV FIFO closes for the year-close packet.

Does not change wash-sale detection. Pairing is one-to-one and happens at
analyze time so the paid PDF can render without raw trades.
"""

from __future__ import annotations

from typing import Any, Optional

from csv_parser import RealizedEvent
from models import Form1099BLot, LotMatchReport, LotMatchRow
from year_close_packet import is_same_year_1099_compare

QTY_EPS = 1e-4
CENTS = 0.02
TOTALS_EPS = 0.05
SETTLE_WINDOW_DAYS = 2


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_cents(value: float) -> float:
    return round(_as_float(value), 2)


def _qty_compatible(lot: Form1099BLot, event: RealizedEvent) -> bool:
    lot_qty = _as_float(lot.quantity)
    event_qty = _as_float(event.quantity)
    if lot_qty <= 0:
        return True
    return abs(lot_qty - event_qty) <= QTY_EPS


def _symbol_compatible(lot: Form1099BLot, event: RealizedEvent) -> bool:
    lot_symbol = (lot.symbol or "").strip().upper()
    event_symbol = (event.symbol or "").strip().upper()
    if not lot_symbol or not event_symbol:
        return True
    return lot_symbol == event_symbol


def _date_score(lot: Form1099BLot, event: RealizedEvent) -> int:
    sold = lot.date_sold
    if sold is None:
        return 0
    settle = event.settle_date
    trade = event.sale_date
    if settle is not None and sold == settle:
        return 3
    if trade is not None and sold == trade:
        return 2
    if settle is not None and abs((settle - sold).days) <= SETTLE_WINDOW_DAYS:
        return 1
    if trade is not None and abs((trade - sold).days) <= SETTLE_WINDOW_DAYS:
        return 1
    return 0


def _candidate_score(lot: Form1099BLot, event: RealizedEvent) -> Optional[tuple[int, int, int]]:
    if not _qty_compatible(lot, event):
        return None
    if abs(_as_float(lot.proceeds) - _as_float(event.sale_proceeds)) > CENTS:
        return None
    if not _symbol_compatible(lot, event):
        return None
    basis_bonus = (
        1
        if abs(_as_float(lot.cost_basis) - _as_float(event.cost_basis)) <= CENTS
        else 0
    )
    return (_date_score(lot, event), basis_bonus, 0)


def _row(
    *,
    status: str,
    lot: Form1099BLot | None = None,
    event: RealizedEvent | None = None,
) -> LotMatchRow:
    symbol = ""
    if lot and lot.symbol:
        symbol = lot.symbol
    elif event:
        symbol = event.symbol
    description = (lot.description if lot else "") or ""
    quantity = _as_float(lot.quantity if lot else None)
    if quantity <= 0 and event is not None:
        quantity = _as_float(event.quantity)
    return LotMatchRow(
        status=status,
        symbol=symbol,
        description=description,
        quantity=quantity,
        date_sold_1099=lot.date_sold if lot else None,
        export_trade_date=event.sale_date if event else None,
        export_settle_date=event.settle_date if event else None,
        proceeds_1099=_round_cents(lot.proceeds if lot else 0.0),
        proceeds_export=_round_cents(event.sale_proceeds if event else 0.0),
        cost_basis_1099=_round_cents(lot.cost_basis if lot else 0.0),
        cost_basis_export=_round_cents(event.cost_basis if event else 0.0),
        wash_sale_disallowed=_round_cents(lot.wash_sale_disallowed if lot else 0.0),
    )


def _matchable_lots(lots: list[Form1099BLot]) -> list[Form1099BLot]:
    return [lot for lot in lots if not lot.is_aggregate]


def match_1099b_lots(
    lots: list[Form1099BLot] | None,
    realized: list[RealizedEvent] | None,
    *,
    form_1099_tax_year: Any,
    analysis_tax_year: Any,
    short_term_proceeds: float = 0.0,
    long_term_proceeds: float = 0.0,
    short_term_cost_basis: float = 0.0,
    long_term_cost_basis: float = 0.0,
    short_term_wash: float = 0.0,
    long_term_wash: float = 0.0,
) -> LotMatchReport | None:
    """Pair same-year 1099-B lots with FIFO closes.

    Returns None when this is not a same-year compare or there are no lots.
    """
    if not is_same_year_1099_compare(form_1099_tax_year, analysis_tax_year):
        return None
    parsed = _matchable_lots(list(lots or []))
    if not parsed:
        return None

    events = list(realized or [])
    used: set[int] = set()
    matched: list[LotMatchRow] = []
    gap: list[LotMatchRow] = []

    for lot in parsed:
        best_idx: int | None = None
        best_score: tuple[int, int, int] | None = None
        for index, event in enumerate(events):
            if index in used:
                continue
            score = _candidate_score(lot, event)
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_score = score
                best_idx = index
        if best_idx is None:
            gap.append(_row(status="gap", lot=lot))
            continue
        used.add(best_idx)
        matched.append(_row(status="matched", lot=lot, event=events[best_idx]))

    unmatched: list[LotMatchRow] = [
        _row(status="unmatched", event=event)
        for index, event in enumerate(events)
        if index not in used
    ]

    lot_proceeds = _round_cents(sum(lot.proceeds for lot in parsed))
    lot_basis = _round_cents(sum(lot.cost_basis for lot in parsed))
    lot_wash = _round_cents(sum(lot.wash_sale_disallowed for lot in parsed))
    summary_proceeds = _round_cents(
        _as_float(short_term_proceeds) + _as_float(long_term_proceeds)
    )
    summary_basis = _round_cents(
        _as_float(short_term_cost_basis) + _as_float(long_term_cost_basis)
    )
    summary_wash = _round_cents(_as_float(short_term_wash) + _as_float(long_term_wash))
    totals_ok = (
        abs(lot_proceeds - summary_proceeds) <= TOTALS_EPS
        and abs(lot_basis - summary_basis) <= TOTALS_EPS
        and abs(lot_wash - summary_wash) <= TOTALS_EPS
    )

    return LotMatchReport(
        matched=matched,
        gap=gap,
        unmatched=unmatched,
        matched_count=len(matched),
        gap_count=len(gap),
        unmatched_count=len(unmatched),
        totals_ok=totals_ok,
        lot_proceeds_total=lot_proceeds,
        lot_cost_basis_total=lot_basis,
        lot_wash_total=lot_wash,
    )
