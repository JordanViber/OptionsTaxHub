"""Year-end harvest check for open losing stock lots.

For each ticker, look for replacement acquisitions in the 30 days before
or after a prospective sale. The window is the JOR-50 wash window
(``WASH_SALE_WINDOW_DAYS``). A same-account buy on the sale date is not a
replacement. A buy in another account on that date is, including an IRA
buy, which is a permanent disallowance (JOR-175). Only the same ticker
and its bought calls or short puts count. A different fund, including a
cash-parking fund, is not a replacement.

This does not rewrite wash flags, lot basis, or tax totals. The sentences
are informational. TSLA and SPCX are omitted from the rows.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from fractions import Fraction
from itertools import groupby

from account_labels import account_display_name, account_key, is_ira_account
from csv_parser import _intraday_rank
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

# These names never appear as a harvest-check row or a sell prompt.
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


def _line(*, clean: bool, disallowed: float, open_loss: float) -> str:
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


def _split_ratio(rows: list[Transaction]) -> float | None:
    """Shares-in divided by shares-out for one Robinhood split pair."""
    if not rows:
        return None
    qty_out = sum(txn.quantity for txn in rows if txn.is_quantity_out)
    qty_in = sum(txn.quantity for txn in rows if not txn.is_quantity_out)
    if qty_out <= 0 or qty_in <= 0:
        return None
    return qty_in / qty_out


def _consume_fifo(open_buys: list[tuple[int, float]], quantity: float) -> None:
    """Close oldest stock buys, same rounding as the lot builder."""
    remaining_to_sell = round(float(quantity), 6)
    while remaining_to_sell > 0 and open_buys:
        index, lot_qty = open_buys[0]
        if lot_qty <= remaining_to_sell:
            remaining_to_sell = round(remaining_to_sell - lot_qty, 6)
            open_buys.pop(0)
            continue
        open_buys[0] = (index, round(lot_qty - remaining_to_sell, 6))
        remaining_to_sell = 0


def _shares_still_held(
    transactions: list[Transaction],
    symbol: str,
    as_of: date,
) -> dict[int, float]:
    """Stock-buy quantity still open at ``as_of``, keyed by transaction index.

    Each account is FIFO in the lot builder's order, with splits applied.
    Buys on or before ``as_of`` are included for the shares a sale on or
    before that date has left open.
    """
    symbol = _symbol(symbol)
    order: list[str] = []
    buckets: dict[str, list[tuple[int, Transaction]]] = {}
    for index, txn in enumerate(transactions):
        key = account_key(txn)
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append((index, txn))

    held: dict[int, float] = {}
    for key in order:
        ordered = [
            item
            for item in sorted(
                buckets[key],
                key=lambda item: (
                    item[1].activity_date,
                    item[1].process_date or item[1].activity_date,
                    item[1].settle_date or item[1].activity_date,
                    _intraday_rank(item[1]),
                    -item[0],
                ),
            )
            if item[1].activity_date <= as_of
        ]
        open_buys: list[tuple[int, float]] = []
        for _day, day_items in groupby(ordered, key=lambda item: item[1].activity_date):
            day_list = list(day_items)
            ratio = _split_ratio(
                [
                    txn
                    for _index, txn in day_list
                    if txn.trans_code == TransCode.SPR
                    and _symbol(txn.instrument) == symbol
                ]
            )
            if ratio is not None:
                open_buys = [
                    (index, round(qty * ratio, 6)) for index, qty in open_buys
                ]
            for index, txn in day_list:
                if _symbol(txn.instrument) != symbol or txn.asset_type != AssetType.STOCK:
                    continue
                if txn.trans_code == TransCode.BUY:
                    open_buys.append((index, round(float(txn.quantity), 6)))
                elif txn.trans_code == TransCode.SELL:
                    _consume_fifo(open_buys, txn.quantity)
        for index, qty in open_buys:
            if qty > _QTY_EPS:
                held[index] = qty
    return held


def _triggers_for(
    symbol: str,
    transactions: list[Transaction],
    harvested: list[TaxLot],
    as_of: date,
) -> list[tuple[Transaction, HarvestBuyKind, float]]:
    sale_accounts = {_account(lot.account_label) for lot in harvested}
    own_fills, covered_lots = _own_fill_indexes(transactions, harvested, as_of)
    still_held = _shares_still_held(transactions, symbol, as_of)
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
            # This fill counts only for shares still open on the sale date.
            if txn.activity_date <= as_of:
                shares = min(shares, still_held.get(txn_index, 0.0))
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


# Share quantities move through the matcher in 1/10000 share units.
_SHARE_SCALE = 10000


def _share_units(quantity: float) -> int:
    return int(round(float(quantity) * _SHARE_SCALE))


def _loss_cents(lot: TaxLot) -> int:
    return int(round(abs(float(lot.unrealized_pnl or 0)) * 100))


def _add_flow_edge(
    graph: list[list[list[object]]],
    src: int,
    dst: int,
    cap: int,
    cost: Fraction,
) -> None:
    graph[src].append([dst, len(graph[dst]), cap, cost])
    graph[dst].append([src, len(graph[src]) - 1, 0, -cost])


def _matched_loss_cents(
    buy_units: list[int],
    buy_eligible: list[list[int]],
    lot_units: list[int],
    lot_cents: list[int],
) -> list[int]:
    """Maximum disallowed cents, split per buy.

    A same-day buy lists only the lots outside its own account. An ordinary
    buy, including an IRA buy, lists every lot. Covering a lot-share is worth
    that lot's own per-share loss, so the weight does not depend on which
    eligible buy covers it. Min-cost flow finds a maximum-weight assignment
    and can move an earlier match when a constrained buy needs the lot.
    An earlier buy wins a tie, by far less than one cent, so a single lot
    still fills in date order.
    """
    buy_count = len(buy_units)
    lot_count = len(lot_units)
    if buy_count == 0 or lot_count == 0:
        return [0] * buy_count
    source = 0
    sink = 1
    buy_node = [2 + index for index in range(buy_count)]
    lot_node = [2 + buy_count + index for index in range(lot_count)]
    graph: list[list[list[object]]] = [[] for _ in range(2 + buy_count + lot_count)]
    for index, units in enumerate(buy_units):
        if units > 0:
            _add_flow_edge(graph, source, buy_node[index], units, Fraction(0))
    for index, units in enumerate(lot_units):
        if units > 0:
            _add_flow_edge(graph, lot_node[index], sink, units, Fraction(0))
    watched: list[tuple[int, int, int, int]] = []
    for buy_index, units in enumerate(buy_units):
        if units <= 0:
            continue
        for lot_index in buy_eligible[buy_index]:
            if lot_units[lot_index] <= 0 or lot_cents[lot_index] <= 0:
                continue
            # Higher true loss first. The second term only orders equal losses.
            cost = -Fraction(lot_cents[lot_index], lot_units[lot_index]) - Fraction(
                buy_count - buy_index, 10**18
            )
            _add_flow_edge(
                graph,
                buy_node[buy_index],
                lot_node[lot_index],
                min(units, lot_units[lot_index]),
                cost,
            )
            watched.append(
                (
                    buy_node[buy_index],
                    len(graph[buy_node[buy_index]]) - 1,
                    buy_index,
                    lot_index,
                )
            )
    node_count = len(graph)
    while True:
        dist: list[Fraction | None] = [None] * node_count
        dist[source] = Fraction(0)
        parent = [(-1, -1)] * node_count
        for _ in range(node_count - 1):
            updated = False
            for node, edges in enumerate(graph):
                if dist[node] is None:
                    continue
                for edge_index, edge in enumerate(edges):
                    cap = int(edge[2])
                    if cap <= 0:
                        continue
                    nxt = dist[node] + edge[3]
                    dest = int(edge[0])
                    if dist[dest] is None or nxt < dist[dest]:
                        dist[dest] = nxt
                        parent[dest] = (node, edge_index)
                        updated = True
            if not updated:
                break
        if dist[sink] is None:
            break
        bottleneck = 10**18
        node = sink
        while node != source:
            prev, edge_index = parent[node]
            bottleneck = min(bottleneck, int(graph[prev][edge_index][2]))
            node = prev
        if bottleneck <= 0:
            break
        node = sink
        while node != source:
            prev, edge_index = parent[node]
            edge = graph[prev][edge_index]
            edge[2] = int(edge[2]) - bottleneck
            reverse = graph[int(edge[0])][int(edge[1])]
            reverse[2] = int(reverse[2]) + bottleneck
            node = prev
    cents = [0] * buy_count
    for node, edge_index, buy_index, lot_index in watched:
        edge = graph[node][edge_index]
        flowed = min(buy_units[buy_index], lot_units[lot_index]) - int(edge[2])
        if flowed <= 0:
            continue
        cents[buy_index] += int(
            round(Fraction(flowed * lot_cents[lot_index], lot_units[lot_index]))
        )
    return cents


def _allocate_found(
    found: list[tuple[Transaction, HarvestBuyKind, float]],
    harvested: list[TaxLot],
    *,
    as_of: date,
    open_loss: float,
    quantity: float,
) -> tuple[float, list[HarvestTriggerBuy]]:
    """Assign replacement shares to lots so the disallowed loss is maximal.

    ``open_loss`` and ``quantity`` stay in the signature for the caller.
    The dollars come from the lots that the assignment covers.
    """
    del open_loss, quantity
    sale_accounts = {_account(lot.account_label) for lot in harvested}
    eligible: list[list[int]] = []
    for txn, _kind, _shares in found:
        if _same_sale_account(txn, sale_accounts, as_of):
            eligible.append(
                [
                    index
                    for index, lot in enumerate(harvested)
                    if _account(lot.account_label) != _account(txn.account_label)
                ]
            )
        else:
            eligible.append(list(range(len(harvested))))
    matched = _matched_loss_cents(
        [_share_units(shares) for _txn, _kind, shares in found],
        eligible,
        [_share_units(lot.quantity) for lot in harvested],
        [_loss_cents(lot) for lot in harvested],
    )
    rows = [
        _trigger_row(txn, kind, shares, matched[index] / 100)
        for index, (txn, kind, shares) in enumerate(found)
    ]
    return _cents(sum(row.disallowed_loss for row in rows)), rows


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
        if symbol in NO_HARVEST_PROMPT_SYMBOLS:
            continue
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
        earliest: date | None = None
        safe: date | None = None
        if not clean:
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
        if row.symbol in NO_HARVEST_PROMPT_SYMBOLS:
            continue
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
