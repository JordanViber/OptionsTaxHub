"""Match parsed 1099-B lots to CSV FIFO closes for the year-close packet.

Does not change wash-sale detection. Pairing is one-to-one and happens at
analyze time so the paid PDF can render without raw trades.
"""

from __future__ import annotations

from typing import Any, Optional

from csv_parser import RealizedEvent
from models import AssetType, Form1099BLot, LotMatchReport, LotMatchRow
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


def _cents_close(left: Any, right: Any) -> bool:
    return abs(_as_float(left) - _as_float(right)) <= CENTS


def _is_option_event(event: RealizedEvent) -> bool:
    return event.asset_type == AssetType.OPTION


def _event_is_short_option(event: RealizedEvent) -> bool:
    """FIFO STO/BTC: pnl = premium (cost_basis) - buyback (sale_proceeds)."""
    if not _is_option_event(event):
        return False
    cost = _as_float(event.cost_basis)
    proceeds = _as_float(event.sale_proceeds)
    pnl = _as_float(event.pnl)
    short_pnl = cost - proceeds
    long_pnl = proceeds - cost
    return _cents_close(pnl, short_pnl) and not _cents_close(pnl, long_pnl)


def _sto_btc_proceeds_alignment(lot: Form1099BLot, event: RealizedEvent) -> bool:
    """1099 proceeds are premium collected; FIFO stored that as cost_basis."""
    if not _is_option_event(event):
        return False
    if _cents_close(lot.proceeds, event.sale_proceeds):
        return False
    return _cents_close(lot.proceeds, event.cost_basis)


def _export_proceeds_and_basis(
    lot: Form1099BLot | None,
    event: RealizedEvent | None,
) -> tuple[float, float]:
    if event is None:
        return 0.0, 0.0
    swap = (
        _sto_btc_proceeds_alignment(lot, event)
        if lot is not None
        else _event_is_short_option(event)
    )
    if swap:
        return _round_cents(event.cost_basis), _round_cents(event.sale_proceeds)
    return _round_cents(event.sale_proceeds), _round_cents(event.cost_basis)


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
    export_proceeds, export_basis = _export_proceeds_and_basis(lot, event)
    if abs(_as_float(lot.proceeds) - export_proceeds) > CENTS:
        return None
    if not _symbol_compatible(lot, event):
        return None
    date_score = _date_score(lot, event)
    if date_score == 0:
        return None
    basis_bonus = 1 if _cents_close(lot.cost_basis, export_basis) else 0
    return (date_score, basis_bonus, 0)


def _paired_status(lot: Form1099BLot, event: RealizedEvent) -> str:
    """matched if 1099 sold date equals export trade date; else settlement gap."""
    sold = lot.date_sold
    trade = event.sale_date
    if sold is None or trade is None or sold == trade:
        return "matched"
    return "matched_settlement_gap"


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
    proceeds_export, cost_basis_export = _export_proceeds_and_basis(lot, event)
    return LotMatchRow(
        status=status,
        symbol=symbol,
        description=description,
        quantity=quantity,
        date_sold_1099=lot.date_sold if lot else None,
        export_trade_date=event.sale_date if event else None,
        export_settle_date=event.settle_date if event else None,
        proceeds_1099=_round_cents(lot.proceeds if lot else 0.0),
        proceeds_export=proceeds_export,
        cost_basis_1099=_round_cents(lot.cost_basis if lot else 0.0),
        cost_basis_export=cost_basis_export,
        wash_sale_disallowed=_round_cents(lot.wash_sale_disallowed if lot else 0.0),
    )


def _matchable_lots(lots: list[Form1099BLot]) -> list[Form1099BLot]:
    return [lot for lot in lots if not lot.is_aggregate]


def _realized_in_tax_year(
    realized: list[RealizedEvent] | None,
    tax_year: int,
) -> list[RealizedEvent]:
    """Same year filter as realized_summary: export trade date (sale_date), not settle_date.

    A December trade that settles in January stays in the trade-date year.
    """
    return [
        event
        for event in realized or []
        if event.sale_date is not None and event.sale_date.year == tax_year
    ]


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

    events = _realized_in_tax_year(realized, int(analysis_tax_year))
    used: set[int] = set()
    matched: list[LotMatchRow] = []
    gap: list[LotMatchRow] = []
    unmatched: list[LotMatchRow] = []

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
            unmatched.append(_row(status="1099_only", lot=lot))
            continue
        used.add(best_idx)
        event = events[best_idx]
        status = _paired_status(lot, event)
        row = _row(status=status, lot=lot, event=event)
        if status == "matched_settlement_gap":
            gap.append(row)
        else:
            matched.append(row)

    unmatched.extend(
        _row(status="csv_only", event=event)
        for index, event in enumerate(events)
        if index not in used
    )

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
