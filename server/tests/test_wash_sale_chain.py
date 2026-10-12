"""Read-only wash-sale chain explainer.

Chains describe flags and 1099 wash rows. They do not change tax math.
"""

from __future__ import annotations

import copy
import os
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main
from csv_parser import RealizedEvent
from harvesting import build_portfolio_summary
from ledger import SAMPLE_FIXTURE_PRICES, is_trusted_sample_csv_bytes
from lot_matcher import match_1099b_lots
from models import (
    AssetType,
    Form1099BLot,
    LotMatchReport,
    LotMatchRow,
    Supplemental1099Summary,
    TaxLot,
    Transaction,
    TransCode,
    WashSaleFlag,
)
from pdf_1099_parser import parse_robinhood_1099_pdf
from wash_sale import detect_wash_sales
from wash_sale_chain import build_wash_sale_chains

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_CSV_PATH = REPO_ROOT / "client" / "public" / "sample-robinhood-transactions.csv"
SAMPLE_1099_PATH = REPO_ROOT / "client" / "public" / "sample-robinhood-1099-2026.pdf"

AMD_LINE = (
    "You sold 10 AMD at a $300 loss on Jul 15 and bought 10 back on Jul 24, "
    "so $300 moved into the new lot's basis. This matches your 1099 wash "
    "adjustment of $300."
)
OPTION_LINE = "Option wash sales aren't explained here yet."


def _txn(symbol, code, when, quantity, price, asset=AssetType.STOCK, description=""):
    signed = -1 if code in (TransCode.BUY, TransCode.BTO) else 1
    return Transaction(
        activity_date=when,
        process_date=when,
        settle_date=when,
        instrument=symbol,
        description=description,
        trans_code=code,
        quantity=quantity,
        price=price,
        amount=signed * quantity * price,
        asset_type=asset,
    )


def _event(symbol, sale, qty, basis, proceeds, purchase, *, long_term=False):
    return RealizedEvent(
        sale_date=sale,
        symbol=symbol,
        quantity=qty,
        cost_basis=basis,
        sale_proceeds=proceeds,
        pnl=round(proceeds - basis, 2),
        is_long_term=long_term,
        purchase_date=purchase,
    )


def _lot(symbol, qty, per_share, when, wash=0.0):
    return TaxLot(
        symbol=symbol,
        quantity=qty,
        cost_basis_per_share=per_share,
        total_cost_basis=round(per_share * qty, 2),
        purchase_date=when,
        wash_sale_disallowed=wash,
    )


def _row(**overrides) -> LotMatchRow:
    payload = {
        "status": "matched",
        "symbol": "AAPL",
        "quantity": 10,
        "date_sold_1099": date(2026, 6, 3),
        "export_trade_date": date(2026, 6, 1),
        "proceeds_1099": 1500.0,
        "cost_basis_1099": 2000.0,
        "wash_sale_disallowed": 0.0,
    }
    payload.update(overrides)
    return LotMatchRow(**payload)


def _report(*rows: LotMatchRow, bucket: str = "gap") -> LotMatchReport:
    grouped = {"matched": [], "gap": [], "unmatched": []}
    grouped[bucket] = list(rows)
    return LotMatchReport(**grouped)


def _supplemental(year: int, lots: list[Form1099BLot] | None = None):
    return Supplemental1099Summary(tax_year=year, lots=lots or [])


def _build(**kwargs):
    payload = {
        "transactions": [],
        "wash_sale_flags": [],
        "realized_events": [],
        "tax_lots": [],
        "supplemental": None,
        "lot_match_report": None,
        "tax_year": 2026,
    }
    payload.update(kwargs)
    return build_wash_sale_chains(**payload)


def _partial_transactions():
    return [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 100, 200),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 150),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 30, 160),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 11), 20, 158),
    ]


def _multi_hop_transactions():
    return [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 100),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 80),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 90),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 20), 10, 70),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 25), 10, 72),
    ]


def test_partial_replacement_splits_disallowed_across_buys():
    txns = _partial_transactions()
    flags = detect_wash_sales(txns, tax_year=2026)
    lots = [_lot("AAPL", 30, 160, date(2026, 6, 6))]
    lots_before = copy.deepcopy(lots)
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 100, 20000, 15000, date(2026, 1, 1))
        ],
        tax_lots=lots,
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.disallowed_loss == pytest.approx(2500.0)
    assert hop.engine_disallowed_loss == pytest.approx(2500.0)
    assert hop.engine_adjusted_cost_basis == pytest.approx(
        flags[0].adjusted_cost_basis
    )
    assert hop.engine_adjusted_cost_basis == pytest.approx(7300.0)
    assert hop.reconcile_status == "not_compared"
    assert "No same-year 1099-B was compared" in hop.plain_english
    assert "tax totals" not in hop.plain_english
    assert "illustrative" in hop.plain_english
    allocated = {
        buy.activity_date: buy.allocated_disallowed for buy in hop.replacement_buys
    }
    assert allocated[date(2026, 6, 6)] == pytest.approx(1500.0)
    assert allocated[date(2026, 6, 11)] == pytest.approx(1000.0)
    assert sum(allocated.values()) == pytest.approx(2500.0)
    qtys = {buy.activity_date: buy.allocated_quantity for buy in hop.replacement_buys}
    assert qtys[date(2026, 6, 6)] == pytest.approx(30)
    assert qtys[date(2026, 6, 11)] == pytest.approx(20)
    assert "of that loss" in hop.plain_english
    assert lots == lots_before


def test_multi_hop_chain_keeps_engine_loss_and_shows_carry():
    txns = _multi_hop_transactions()
    flags = detect_wash_sales(txns, tax_year=2026)
    flag_losses = [flag.disallowed_loss for flag in flags]
    events = [
        _event("AAPL", date(2026, 6, 1), 10, 1000, 800, date(2026, 1, 1)),
        _event("AAPL", date(2026, 6, 20), 10, 900, 700, date(2026, 6, 10)),
    ]
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=events,
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 2
    assert chains[0].chain_id == chains[0].hops[0].hop_id
    first, second = chains[0].hops
    assert second.original_purchase_date == date(2026, 6, 10)
    assert second.engine_sale_loss == pytest.approx(200.0)
    assert second.carried_in_disallowed == pytest.approx(200.0)
    assert second.explained_loss_with_carry == pytest.approx(400.0)
    assert second.disallowed_loss == pytest.approx(200.0)
    assert "Hop 2:" in second.plain_english
    assert "tax totals" not in second.plain_english
    assert "source of truth" in second.plain_english
    assert chains[0].plain_english == "AAPL wash-sale chain. Not compared."
    assert "Hop 2:" not in chains[0].plain_english
    assert [flag.disallowed_loss for flag in flags] == flag_losses


def test_reconcile_matches_within_two_cents():
    txns = [
        _txn("AMD", TransCode.BUY, date(2026, 6, 1), 10, 150),
        _txn("AMD", TransCode.SELL, date(2026, 7, 15), 10, 120),
        _txn("AMD", TransCode.BUY, date(2026, 7, 24), 10, 125),
    ]
    flag = WashSaleFlag(
        symbol="AMD",
        sale_date=date(2026, 7, 15),
        sale_quantity=10,
        sale_loss=300,
        repurchase_date=date(2026, 7, 24),
        repurchase_quantity=10,
        disallowed_loss=300,
        adjusted_cost_basis=1550,
    )
    report = _report(
        _row(
            symbol="AMD",
            quantity=10,
            export_trade_date=date(2026, 7, 15),
            date_sold_1099=date(2026, 7, 17),
            proceeds_1099=1200,
            cost_basis_1099=1500,
            wash_sale_disallowed=300.01,
        )
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=[flag],
        realized_events=[
            _event("AMD", date(2026, 7, 15), 10, 1500, 1200, date(2026, 6, 1))
        ],
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    assert chains[0].reconcile_status == "matches"
    assert chains[0].difference is None
    assert chains[0].hops[0].form_1099_wash == pytest.approx(300.01)
    assert flag.disallowed_loss == pytest.approx(300)


def test_reconcile_differs_by_dollars_and_leaves_flag():
    txns = [
        _txn("AMD", TransCode.BUY, date(2026, 6, 1), 10, 150),
        _txn("AMD", TransCode.SELL, date(2026, 7, 15), 10, 120),
        _txn("AMD", TransCode.BUY, date(2026, 7, 24), 10, 125),
    ]
    flag = WashSaleFlag(
        symbol="AMD",
        sale_date=date(2026, 7, 15),
        sale_quantity=10,
        sale_loss=300,
        repurchase_date=date(2026, 7, 24),
        repurchase_quantity=10,
        disallowed_loss=300,
        adjusted_cost_basis=1550,
    )
    report = _report(
        _row(
            symbol="AMD",
            quantity=10,
            export_trade_date=date(2026, 7, 15),
            wash_sale_disallowed=280,
            proceeds_1099=1200,
            cost_basis_1099=1500,
        )
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=[flag],
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "differs"
    assert hop.difference == pytest.approx(20)
    assert "by $20" in hop.plain_english
    assert flag.disallowed_loss == pytest.approx(300)
    assert flag.purchase_date is None


def test_not_on_1099_only_when_same_year_1099_has_no_row():
    txns = [
        _txn("AMD", TransCode.BUY, date(2026, 6, 1), 10, 150),
        _txn("AMD", TransCode.SELL, date(2026, 7, 15), 10, 120),
        _txn("AMD", TransCode.BUY, date(2026, 7, 24), 10, 125),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    bare = _build(transactions=txns, wash_sale_flags=flags)
    assert bare[0].reconcile_status == "not_compared"
    assert "No same-year 1099-B was compared for this sale." in bare[0].plain_english
    assert "found only in this Robinhood export" not in bare[0].plain_english
    assert "1099 wash adjustment" not in bare[0].plain_english

    same_year_other_symbol = _build(
        transactions=txns,
        wash_sale_flags=flags,
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(symbol="MSFT", wash_sale_disallowed=0, export_trade_date=date(2026, 7, 15))
        ),
    )
    assert same_year_other_symbol[0].reconcile_status == "not_on_1099"
    assert "found only in this Robinhood export" in same_year_other_symbol[0].plain_english

    prior_year = _build(
        transactions=txns,
        wash_sale_flags=flags,
        supplemental=_supplemental(
            2025,
            [Form1099BLot(symbol="AMD", quantity=10, wash_sale_disallowed=50)],
        ),
        lot_match_report=_report(
            _row(
                symbol="ZZZ",
                quantity=1,
                export_trade_date=date(2025, 1, 2),
                wash_sale_disallowed=50,
            ),
            bucket="unmatched",
        ),
        tax_year=2026,
    )
    assert prior_year[0].reconcile_status == "not_compared"
    assert "No same-year 1099-B was compared" in prior_year[0].plain_english
    assert all(chain.reconcile_status != "on_1099_only" for chain in prior_year)

    unknown_year = _build(
        transactions=txns,
        wash_sale_flags=flags,
        supplemental=Supplemental1099Summary(tax_year=None),
        lot_match_report=_report(
            _row(
                symbol="AMD",
                quantity=10,
                export_trade_date=date(2026, 7, 15),
                wash_sale_disallowed=300,
            )
        ),
        tax_year=2026,
    )
    assert unknown_year[0].reconcile_status == "not_compared"
    assert unknown_year[0].hops[0].form_1099_wash is None
    assert "No same-year 1099-B was compared" in unknown_year[0].plain_english
    assert all(chain.reconcile_status != "on_1099_only" for chain in unknown_year)


def test_on_1099_only_uses_1099_amount_without_engine_flag():
    row = _row(
        status="1099_only",
        symbol="QQQ",
        quantity=4,
        export_trade_date=None,
        date_sold_1099=date(2026, 3, 2),
        proceeds_1099=400,
        cost_basis_1099=450,
        wash_sale_disallowed=50,
    )
    lot = Form1099BLot(
        symbol="QQQ",
        quantity=4,
        date_sold=date(2026, 3, 2),
        proceeds=400,
        cost_basis=450,
        wash_sale_disallowed=50,
    )
    chains = _build(
        transactions=[],
        wash_sale_flags=[],
        supplemental=_supplemental(2026, [lot]),
        lot_match_report=_report(row, bucket="unmatched"),
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "on_1099_only"
    assert hop.engine_disallowed_loss is None
    assert hop.disallowed_loss == pytest.approx(50)
    assert hop.form_1099_wash == pytest.approx(50)
    assert "did not flag a matching wash" in hop.plain_english
    assert lot.wash_sale_disallowed == pytest.approx(50)


def test_chain_builder_does_not_mutate_inputs():
    txns = _partial_transactions()
    flags = detect_wash_sales(txns, tax_year=2026)
    events = [
        _event("AAPL", date(2026, 6, 1), 100, 20000, 15000, date(2026, 1, 1))
    ]
    lots = [
        _lot("AAPL", 30, 160, date(2026, 6, 6), wash=0),
        _lot("AAPL", 20, 158, date(2026, 6, 11), wash=0),
    ]
    form_lots = [
        Form1099BLot(
            symbol="AAPL",
            quantity=100,
            date_sold=date(2026, 6, 3),
            proceeds=15000,
            cost_basis=20000,
            wash_sale_disallowed=2500,
        )
    ]
    supplemental = _supplemental(2026, form_lots)
    report = _report(
        _row(
            symbol="AAPL",
            quantity=100,
            export_trade_date=date(2026, 6, 1),
            date_sold_1099=date(2026, 6, 3),
            proceeds_1099=15000,
            cost_basis_1099=20000,
            wash_sale_disallowed=2500,
        )
    )
    flags_copy = copy.deepcopy(flags)
    events_copy = copy.deepcopy(events)
    lots_copy = copy.deepcopy(lots)
    txns_copy = copy.deepcopy(txns)
    supplemental_copy = copy.deepcopy(supplemental)
    report_copy = copy.deepcopy(report)
    realized_before = main._compute_realized_summary(events, 2026)
    summary_before = build_portfolio_summary([], [], flags)

    chains = build_wash_sale_chains(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=events,
        tax_lots=lots,
        supplemental=supplemental,
        lot_match_report=report,
        tax_year=2026,
    )

    assert flags == flags_copy
    assert events == events_copy
    assert lots == lots_copy
    assert txns == txns_copy
    assert supplemental == supplemental_copy
    assert report == report_copy
    assert all(flag.purchase_date is None for flag in flags)
    assert main._compute_realized_summary(events, 2026) == realized_before
    assert build_portfolio_summary([], [], flags) == summary_before
    assert chains[0].reconcile_status == "matches"
    assert sum(flag.disallowed_loss for flag in flags) == pytest.approx(
        sum(flag.disallowed_loss for flag in flags_copy)
    )
    assert [lot.total_cost_basis for lot in lots] == [
        lot.total_cost_basis for lot in lots_copy
    ]
    assert [lot.wash_sale_disallowed for lot in lots] == [
        lot.wash_sale_disallowed for lot in lots_copy
    ]
    assert [event.pnl for event in events] == [event.pnl for event in events_copy]


def test_option_only_wash_is_one_not_compared_chain():
    """An option wash is one hop: the sale date, and no dollar figure."""
    txns = [
        _txn("SPX", TransCode.BTO, date(2026, 6, 1), 1, 10, AssetType.OPTION),
        _txn("SPX", TransCode.STC, date(2026, 6, 10), 1, 4, AssetType.OPTION),
        _txn("SPX", TransCode.BTO, date(2026, 6, 12), 1, 5, AssetType.OPTION),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    chains = _build(transactions=txns, wash_sale_flags=flags)
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    hop = chains[0].hops[0]
    assert chains[0].reconcile_status == "not_compared"
    assert chains[0].plain_english == OPTION_LINE
    assert hop.plain_english == OPTION_LINE
    assert hop.asset_type == "option"
    assert hop.symbol == "SPX"
    assert hop.sale_date == date(2026, 6, 10)
    assert hop.engine_disallowed_loss is None
    assert hop.disallowed_loss == pytest.approx(0)
    assert hop.form_1099_wash is None
    assert hop.replacement_buys == []
    assert hop.closed_lots == []
    assert hop.proceeds == pytest.approx(0)
    assert hop.cost_basis == pytest.approx(0)
    assert hop.loss == pytest.approx(0)
    assert hop.engine_adjusted_cost_basis is None
    assert hop.open_lot_total_cost_basis is None
    assert hop.carried_in_disallowed is None
    assert hop.explained_loss_with_carry is None
    assert hop.engine_sale_loss is None
    assert "contracts" not in hop.plain_english
    assert flags[0].sale_loss == pytest.approx(6)
    assert flags[0].disallowed_loss == pytest.approx(6)


def test_multi_lot_sell_aggregates_1099_rows_into_one_match():
    """One sell that closes two FIFO lots is one chain, not extra 1099-only chains."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 40, 100),
        _txn("AAPL", TransCode.BUY, date(2026, 2, 1), 60, 80),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 50),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 100, 55),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    assert len(flags) == 1
    assert flags[0].disallowed_loss == pytest.approx(3800)
    report = _report(
        _row(
            symbol="AAPL",
            quantity=60,
            export_trade_date=date(2026, 6, 1),
            date_sold_1099=date(2026, 6, 3),
            proceeds_1099=3000,
            cost_basis_1099=4800,
            wash_sale_disallowed=1800,
        ),
        _row(
            symbol="AAPL",
            quantity=40,
            export_trade_date=date(2026, 6, 1),
            date_sold_1099=date(2026, 6, 3),
            proceeds_1099=2000,
            cost_basis_1099=4000,
            wash_sale_disallowed=2000,
        ),
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 40, 4000, 2000, date(2026, 1, 1)),
            _event("AAPL", date(2026, 6, 1), 60, 4800, 3000, date(2026, 2, 1)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    assert len(chains) == 1
    assert chains[0].reconcile_status == "matches"
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    hop = chains[0].hops[0]
    assert hop.form_1099_wash == pytest.approx(3800)
    assert hop.disallowed_loss == pytest.approx(3800)
    assert hop.sale_quantity == pytest.approx(100)
    assert hop.hop_id.endswith(":e0")
    assert hop.tacked_acquisition_date is None
    assert hop.carryover_days is None
    assert hop.original_purchase_date is None
    assert "Holding period starts" not in hop.plain_english
    assert [(lot.purchase_date, lot.quantity) for lot in hop.closed_lots] == [
        (date(2026, 1, 1), 40),
        (date(2026, 2, 1), 60),
    ]
    assert flags[0].disallowed_loss == pytest.approx(3800)


def test_option_flag_and_option_1099_rows_are_not_compared():
    """One option entry for the symbol and sale date. No dollar figure."""
    txns = [
        _txn("SPX", TransCode.BTO, date(2026, 1, 1), 1, 10, AssetType.OPTION),
        _txn("SPX", TransCode.STC, date(2026, 6, 10), 1, 4, AssetType.OPTION),
        _txn("SPX", TransCode.BTO, date(2026, 6, 12), 1, 5, AssetType.OPTION),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    assert len(flags) == 1
    assert flags[0].sale_loss == pytest.approx(6)
    assert flags[0].disallowed_loss == pytest.approx(6)
    report = _report(
        _row(
            symbol="SPX",
            quantity=1,
            export_trade_date=date(2026, 6, 10),
            proceeds_1099=4,
            cost_basis_1099=10,
            wash_sale_disallowed=6,
        ),
        _row(
            symbol="SPX",
            quantity=1,
            export_trade_date=date(2026, 6, 10),
            proceeds_1099=400,
            cost_basis_1099=1000,
            wash_sale_disallowed=600,
        ),
    )
    lots = [_lot("SPX", 1, 5, date(2026, 6, 12), wash=6)]
    realized_event = _event("SPX", date(2026, 6, 10), 1, 1000, 400, date(2026, 1, 1))
    realized_event.asset_type = AssetType.OPTION
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[realized_event],
        tax_lots=lots,
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    assert chains[0].reconcile_status == "not_compared"
    assert chains[0].plain_english == OPTION_LINE
    hop = chains[0].hops[0]
    assert hop.asset_type == "option"
    assert hop.sale_date == date(2026, 6, 10)
    assert hop.sale_quantity == pytest.approx(2)
    assert hop.engine_disallowed_loss is None
    assert hop.disallowed_loss == pytest.approx(0)
    assert hop.form_1099_wash is None
    assert hop.replacement_buys == []
    assert hop.open_lot_total_cost_basis is None
    assert hop.engine_adjusted_cost_basis is None
    assert hop.proceeds == pytest.approx(0)
    assert hop.cost_basis == pytest.approx(0)
    assert flags[0].sale_loss == pytest.approx(6)
    assert flags[0].disallowed_loss == pytest.approx(6)
    assert flags[0].adjusted_cost_basis == pytest.approx(11)
    assert lots[0].total_cost_basis == pytest.approx(5)
    assert lots[0].wash_sale_disallowed == pytest.approx(6)


def test_same_day_same_qty_sales_get_distinct_ids():
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 100),
        _txn("AAPL", TransCode.BUY, date(2026, 1, 2), 10, 90),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 80),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 70),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 75),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    assert len(flags) == 2
    assert all(flag.sale_quantity == pytest.approx(10) for flag in flags)
    assert {flag.sale_date for flag in flags} == {date(2026, 6, 1)}
    events = [
        _event("AAPL", date(2026, 6, 1), 10, 1000, 800, date(2026, 1, 1)),
        _event("AAPL", date(2026, 6, 1), 10, 900, 700, date(2026, 1, 2)),
    ]
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=events,
    )
    ids = sorted(chain.chain_id for chain in chains)
    assert ids == ["AAPL:2026-06-01:10:e0", "AAPL:2026-06-01:10:e1"]
    assert len(set(ids)) == 2
    assert all(chain.chain_id == chain.hops[0].hop_id for chain in chains)
    assert len(chains) == 2

    without_events = _build(transactions=txns, wash_sale_flags=flags)
    txn_ids = sorted(chain.chain_id for chain in without_events)
    # No realized lots, so neither sale's proceeds are safe to show.
    assert txn_ids == ["AAPL:2026-06-01:10:f0", "AAPL:2026-06-01:10:f1"]
    assert all(chain.hops[0].proceeds == pytest.approx(0) for chain in without_events)
    assert all(chain.hops[0].proceeds != pytest.approx(700) for chain in without_events)
    assert all(chain.hops[0].proceeds != pytest.approx(800) for chain in without_events)
    assert all(chain.reconcile_status == "not_compared" for chain in without_events)
    assert all(
        "one trade's proceeds and loss" in chain.plain_english
        for chain in without_events
    )


def test_same_day_sales_that_could_claim_the_same_1099_rows_are_not_compared():
    """Two same-day sales that both fit both penny-apart rows are not compared."""
    sale = date(2026, 6, 1)
    flags = [
        WashSaleFlag(
            symbol="IBM",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=130,
        ),
        WashSaleFlag(
            symbol="IBM",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=30,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=30,
            adjusted_cost_basis=140,
        ),
    ]
    txns = [
        _txn("IBM", TransCode.BUY, date(2026, 1, 1), 10, 12),
        _txn("IBM", TransCode.BUY, date(2026, 1, 2), 10, 13),
        _txn("IBM", TransCode.SELL, sale, 10, 10.00),
        _txn("IBM", TransCode.SELL, sale, 10, 10.01),
        _txn("IBM", TransCode.BUY, date(2026, 6, 10), 10, 11),
    ]
    events = [
        _event("IBM", sale, 10, 120, 100, date(2026, 1, 1)),
        _event("IBM", sale, 10, 130.10, 100.10, date(2026, 1, 2)),
    ]
    report = _report(
        _row(
            symbol="IBM",
            quantity=10,
            export_trade_date=sale,
            proceeds_1099=100.00,
            wash_sale_disallowed=20,
        ),
        _row(
            symbol="IBM",
            quantity=10,
            export_trade_date=sale,
            proceeds_1099=100.10,
            wash_sale_disallowed=40,
        ),
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=events,
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    assert len(chains) == 2
    assert {chain.reconcile_status for chain in chains} == {"not_compared"}
    assert all(chain.hops[0].form_1099_wash is None for chain in chains)
    assert all("no wash total was chosen" in chain.plain_english for chain in chains)
    assert flags[0].disallowed_loss == pytest.approx(20)

    apart = _build(
        transactions=[
            _txn("IBM", TransCode.SELL, sale, 10, 10),
            _txn("IBM", TransCode.SELL, sale, 10, 30),
            _txn("IBM", TransCode.BUY, date(2026, 6, 10), 10, 11),
        ],
        wash_sale_flags=flags,
        realized_events=events,
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="IBM",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=100,
                wash_sale_disallowed=20,
            ),
            _row(
                symbol="IBM",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=300,
                wash_sale_disallowed=30,
            ),
        ),
    )
    assert len(apart) == 2
    assert {chain.reconcile_status for chain in apart} == {"matches"}


def test_loss_flag_attaches_to_the_matching_sale_not_the_same_day_gain():
    """The $80 gain is listed first. The $300 flag still uses the $70 sale."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 50),
        _txn("AAPL", TransCode.BUY, date(2026, 1, 2), 10, 100),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 80),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 70),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 90),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    assert len(flags) == 1
    assert flags[0].sale_loss == pytest.approx(300)
    assert flags[0].disallowed_loss == pytest.approx(300)
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 500, 800, date(2026, 1, 1)),
            _event("AAPL", date(2026, 6, 1), 10, 1000, 700, date(2026, 1, 2)),
        ],
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.proceeds == pytest.approx(700)
    assert hop.proceeds != pytest.approx(800)
    assert hop.cost_basis == pytest.approx(1000)
    assert hop.loss == pytest.approx(300)
    assert hop.hop_id.endswith(":e1")
    assert flags[0].sale_loss == pytest.approx(300)


def test_option_sale_lists_no_replacement_buys():
    """An option sale does not list the stock buy or the option buy."""
    stock_buy = _txn("AMD", TransCode.BUY, date(2026, 6, 5), 10, 50)
    option_buy = _txn(
        "AMD", TransCode.BTO, date(2026, 6, 6), 1, 5, AssetType.OPTION
    )
    option_sell = _txn(
        "AMD", TransCode.STC, date(2026, 6, 1), 1, 4, AssetType.OPTION
    )
    flag = WashSaleFlag(
        symbol="AMD",
        sale_date=date(2026, 6, 1),
        sale_quantity=1,
        sale_loss=6,
        repurchase_date=date(2026, 6, 6),
        repurchase_quantity=1,
        disallowed_loss=6,
        adjusted_cost_basis=11,
    )
    event = _event("AMD", date(2026, 6, 1), 1, 1000, 400, date(2026, 1, 1))
    event.asset_type = AssetType.OPTION
    chains = _build(
        transactions=[option_sell, stock_buy, option_buy],
        wash_sale_flags=[flag],
        realized_events=[event],
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    hop = chains[0].hops[0]
    assert hop.asset_type == "option"
    assert hop.plain_english == OPTION_LINE
    assert hop.sale_date == date(2026, 6, 1)
    assert hop.engine_disallowed_loss is None
    assert hop.disallowed_loss == pytest.approx(0)
    assert hop.replacement_buys == []
    assert hop.closed_lots == []
    assert hop.form_1099_wash is None
    assert stock_buy.price == pytest.approx(50)
    assert option_buy.price == pytest.approx(5)
    assert flag.disallowed_loss == pytest.approx(6)
    assert flag.sale_loss == pytest.approx(6)


def test_1099_groups_skip_overshoot_and_aggregate_leftovers():
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 80),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 50),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 10, 55),
    ]
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 6, 1),
        sale_quantity=10,
        sale_loss=300,
        repurchase_date=date(2026, 6, 6),
        repurchase_quantity=10,
        disallowed_loss=300,
        adjusted_cost_basis=850,
    )
    report = _report(
        _row(
            symbol="AAPL",
            quantity=90,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=4500,
            cost_basis_1099=4515,
            wash_sale_disallowed=15,
        ),
        _row(
            symbol="AAPL",
            quantity=10,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=800,
            cost_basis_1099=1800,
            wash_sale_disallowed=999,
        ),
        _row(
            symbol="AAPL",
            quantity=10,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=500,
            cost_basis_1099=800,
            wash_sale_disallowed=300,
        ),
        _row(
            symbol="QQQ",
            quantity=2,
            export_trade_date=None,
            date_sold_1099=date(2026, 3, 2),
            proceeds_1099=200,
            cost_basis_1099=220,
            wash_sale_disallowed=20,
            status="1099_only",
        ),
        _row(
            symbol="QQQ",
            quantity=2,
            export_trade_date=None,
            date_sold_1099=date(2026, 3, 2),
            proceeds_1099=200,
            cost_basis_1099=230,
            wash_sale_disallowed=30,
            status="1099_only",
        ),
        _row(
            symbol="QQQ",
            quantity=1,
            export_trade_date=None,
            date_sold_1099=date(2026, 3, 2),
            proceeds_1099=50,
            cost_basis_1099=57,
            wash_sale_disallowed=7,
            status="1099_only",
        ),
        _row(
            symbol="",
            quantity=1,
            date_sold_1099=date(2026, 4, 1),
            wash_sale_disallowed=9,
            status="1099_only",
        ),
        _row(
            symbol="MSFT",
            quantity=1,
            export_trade_date=None,
            date_sold_1099=None,
            wash_sale_disallowed=9,
            status="1099_only",
        ),
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=[flag],
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    matched = [chain for chain in chains if chain.symbol == "AAPL" and chain.reconcile_status == "matches"]
    assert len(matched) == 1
    assert matched[0].hops[0].form_1099_wash == pytest.approx(300)
    assert matched[0].hops[0].sale_quantity == pytest.approx(10)
    aapl_only = [
        chain for chain in chains
        if chain.symbol == "AAPL" and chain.reconcile_status == "on_1099_only"
    ]
    assert len(aapl_only) == 2
    assert {chain.hops[0].disallowed_loss for chain in aapl_only} == {15, 999}
    qqq = [chain for chain in chains if chain.symbol == "QQQ"]
    assert len(qqq) == 2
    combined = next(chain for chain in qqq if chain.hops[0].sale_quantity == pytest.approx(4))
    assert combined.hops[0].disallowed_loss == pytest.approx(50)
    assert combined.hops[0].hop_id.endswith(":r3")
    assert combined.reconcile_status == "on_1099_only"
    assert all(chain.symbol != "" for chain in chains)
    assert not any(chain.symbol == "MSFT" for chain in chains)


def test_chain_copy_covers_remaining_branches():
    from wash_sale_chain import _event_candidates, _hop_from_1099_group

    assert _event_candidates([], set(), "AAPL", None) == []
    assert _hop_from_1099_group([], transactions=[], events=[], tax_lots=[]) is None
    blank = _row(symbol=" ", quantity=1, wash_sale_disallowed=4)
    assert (
        _hop_from_1099_group(
            [(0, blank)],
            transactions=[],
            events=[],
            tax_lots=[],
        )
        is None
    )

    skipped = WashSaleFlag(
        symbol="ZERO",
        sale_date=date(2026, 6, 1),
        sale_quantity=1,
        sale_loss=0,
        repurchase_date=date(2026, 6, 2),
        repurchase_quantity=1,
        disallowed_loss=0,
        adjusted_cost_basis=1,
    )
    quiet = WashSaleFlag(
        symbol="QUIET",
        sale_date=date(2026, 6, 1),
        sale_quantity=1,
        sale_loss=0,
        repurchase_date=date(2026, 6, 2),
        repurchase_quantity=1,
        disallowed_loss=5,
        adjusted_cost_basis=5,
    )
    fraction = WashSaleFlag(
        symbol="FRAC",
        sale_date=date(2026, 6, 2),
        sale_quantity=1.5,
        sale_loss=10.25,
        repurchase_date=date(2026, 6, 3),
        repurchase_quantity=1.5,
        disallowed_loss=10.25,
        adjusted_cost_basis=10.25,
    )
    orphans = _build(wash_sale_flags=[skipped, quiet, fraction])
    assert [chain.symbol for chain in orphans] == ["QUIET", "FRAC"]
    assert orphans[0].hops[0].hop_id.endswith(":f1")
    assert "loss" not in orphans[0].plain_english
    assert orphans[1].hops[0].hop_id.endswith(":f2")
    assert "1.5" in orphans[1].hops[0].hop_id
    assert "$10.25" in orphans[1].plain_english

    presale = [
        _txn("IBM", TransCode.BUY, date(2025, 12, 20), 10, 100),
        _txn("IBM", TransCode.SELL, date(2026, 1, 10), 10, 80),
    ]
    presale_flags = detect_wash_sales(presale, tax_year=2026)
    presale_chains = _build(transactions=presale, wash_sale_flags=presale_flags)
    assert "Dec 20, 2025" in presale_chains[0].plain_english
    assert "Jan 10, 2026" in presale_chains[0].plain_english
    assert "replacement lot" in presale_chains[0].plain_english

    three = [
        _txn("IBM", TransCode.BUY, date(2026, 1, 1), 30, 10),
        _txn("IBM", TransCode.SELL, date(2026, 6, 15), 30, 4),
        _txn("IBM", TransCode.BUY, date(2026, 6, 16), 10, 6),
        _txn("IBM", TransCode.BUY, date(2026, 6, 17), 10, 6),
        _txn("IBM", TransCode.BUY, date(2026, 6, 18), 10, 6),
    ]
    three_chains = _build(
        transactions=three,
        wash_sale_flags=detect_wash_sales(three, tax_year=2026),
    )
    assert ", and " in three_chains[0].plain_english

    penny_txns = [
        _txn("PEN", TransCode.BUY, date(2026, 1, 1), 10, 100),
        _txn("PEN", TransCode.SELL, date(2026, 6, 1), 10, 90),
        _txn("PEN", TransCode.BUY, date(2026, 6, 2), 1, 10),
        _txn("PEN", TransCode.BUY, date(2026, 6, 3), 1, 10),
        _txn("PEN", TransCode.BUY, date(2026, 6, 4), 1, 10),
    ]
    penny_flag = WashSaleFlag(
        symbol="PEN",
        sale_date=date(2026, 6, 1),
        sale_quantity=10,
        sale_loss=100,
        repurchase_date=date(2026, 6, 2),
        repurchase_quantity=3,
        disallowed_loss=10,
        adjusted_cost_basis=40,
    )
    penny = _build(transactions=penny_txns, wash_sale_flags=[penny_flag])
    allocated = [
        buy.allocated_disallowed
        for buy in penny[0].hops[0].replacement_buys
        if buy.allocated_quantity > 0
    ]
    assert allocated == pytest.approx([3.34, 3.33, 3.33])

    carry_txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 100),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 80),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 90),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 20), 10, 70),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 25), 10, 72),
    ]
    carry_flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 6, 1),
            sale_quantity=10,
            sale_loss=0.01,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=0.01,
            adjusted_cost_basis=900.01,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 6, 20),
            sale_quantity=10,
            sale_loss=100,
            repurchase_date=date(2026, 6, 25),
            repurchase_quantity=10,
            disallowed_loss=100,
            adjusted_cost_basis=820,
        ),
    ]
    carry = _build(
        transactions=carry_txns,
        wash_sale_flags=carry_flags,
        realized_events=[
            # Proceeds match the sells (10×80 and 10×70) so the lots attach.
            _event("AAPL", date(2026, 6, 1), 10, 800.01, 800, date(2026, 1, 1)),
            _event("AAPL", date(2026, 6, 20), 10, 800, 700, date(2026, 6, 10)),
        ],
    )
    assert len(carry) == 1
    assert len(carry[0].hops) == 2
    assert "Hop 2:" in carry[0].hops[1].plain_english
    assert "tax totals" not in carry[0].hops[1].plain_english
    assert carry[0].hops[1].carried_in_disallowed == pytest.approx(0.01)

    cents = _build(
        transactions=[
            _txn("AMD", TransCode.BUY, date(2026, 6, 1), 10, 150),
            _txn("AMD", TransCode.SELL, date(2026, 7, 15), 10, 120),
            _txn("AMD", TransCode.BUY, date(2026, 7, 24), 10, 125),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AMD",
                sale_date=date(2026, 7, 15),
                sale_quantity=10,
                sale_loss=300,
                repurchase_date=date(2026, 7, 24),
                repurchase_quantity=10,
                disallowed_loss=300,
                adjusted_cost_basis=1550,
            )
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AMD",
                quantity=10,
                export_trade_date=date(2026, 7, 15),
                proceeds_1099=1200,
                wash_sale_disallowed=279.50,
            )
        ),
        tax_lots=[_lot("OTHER", 10, 1, date(2026, 7, 24))],
    )
    assert cents[0].hops[0].difference == pytest.approx(20.50)
    assert "$20.50" in cents[0].plain_english
    assert cents[0].hops[0].open_lot_total_cost_basis is None

    warrant_sell = Transaction.model_construct(
        activity_date=date(2026, 6, 15),
        instrument="ZZZ",
        trans_code=TransCode.SELL,
        quantity=1,
        price=3,
        amount=3,
        asset_type="warrant",
    )
    warrant_buy = Transaction.model_construct(
        activity_date=date(2026, 6, 20),
        instrument="ZZZ",
        trans_code=TransCode.BUY,
        quantity=1,
        price=4,
        amount=-4,
        asset_type="warrant",
    )
    warrant = _build(
        transactions=[warrant_sell, warrant_buy],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="ZZZ",
                sale_date=date(2026, 6, 15),
                sale_quantity=1,
                sale_loss=2,
                repurchase_date=date(2026, 6, 20),
                repurchase_quantity=1,
                disallowed_loss=2,
                adjusted_cost_basis=6,
            )
        ],
    )
    assert warrant[0].hops[0].asset_type == "warrant"
    assert warrant[0].hops[0].hop_id.endswith(":t0")

    option_event = _event("SPX", date(2026, 6, 10), 1, 1000, 400, date(2026, 1, 1))
    option_event.asset_type = AssetType.OPTION
    scaled = _build(
        wash_sale_flags=[
            WashSaleFlag(
                symbol="SPX",
                sale_date=date(2026, 6, 10),
                sale_quantity=1,
                sale_loss=6,
                repurchase_date=date(2026, 6, 12),
                repurchase_quantity=1,
                disallowed_loss=6,
                adjusted_cost_basis=11,
            )
        ],
        realized_events=[option_event],
    )
    assert len(scaled) == 1
    assert len(scaled[0].hops) == 1
    assert scaled[0].hops[0].asset_type == "option"
    assert scaled[0].plain_english == OPTION_LINE
    assert scaled[0].hops[0].sale_date == date(2026, 6, 10)
    assert scaled[0].hops[0].engine_disallowed_loss is None
    assert scaled[0].hops[0].disallowed_loss == pytest.approx(0)
    assert scaled[0].hops[0].proceeds == pytest.approx(0)
    assert scaled[0].hops[0].replacement_buys == []
    assert scaled[0].hops[0].form_1099_wash is None
    assert scaled[0].hops[0].hop_id.endswith(":f0")

    unnamed = _event("BARE", date(2026, 6, 10), 1, 10, 4, date(2026, 1, 1))
    unnamed.asset_type = None
    bare = _build(
        wash_sale_flags=[
            WashSaleFlag(
                symbol="BARE",
                sale_date=date(2026, 6, 10),
                sale_quantity=1,
                sale_loss=6,
                repurchase_date=date(2026, 6, 12),
                repurchase_quantity=1,
                disallowed_loss=6,
                adjusted_cost_basis=6,
            )
        ],
        realized_events=[unnamed],
    )
    assert bare[0].hops[0].asset_type == "stock"

    zero_wash = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 20),
            _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 10),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 5), 10, 12),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2026, 6, 1),
                sale_quantity=10,
                sale_loss=100,
                repurchase_date=date(2026, 6, 5),
                repurchase_quantity=10,
                disallowed_loss=100,
                adjusted_cost_basis=220,
            )
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AAPL",
                quantity=10,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=100,
                wash_sale_disallowed=0,
            )
        ),
    )
    assert len(zero_wash) == 1
    assert zero_wash[0].reconcile_status == "differs"
    assert zero_wash[0].difference == pytest.approx(100)
    assert zero_wash[0].hops[0].form_1099_wash == pytest.approx(0)
    assert "found only in this Robinhood export" not in zero_wash[0].plain_english
    assert "differs from your 1099" in zero_wash[0].plain_english

    empty_unit = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 20),
            _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 10),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 5), 10, 12),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2026, 6, 1),
                sale_quantity=10,
                sale_loss=100,
                repurchase_date=date(2026, 6, 5),
                repurchase_quantity=10,
                disallowed_loss=100,
                adjusted_cost_basis=220,
            )
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AAPL",
                quantity=0,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=0,
                wash_sale_disallowed=5,
            )
        ),
    )
    assert any(chain.reconcile_status == "not_on_1099" for chain in empty_unit)
    assert any(chain.reconcile_status == "on_1099_only" for chain in empty_unit)

    dated = _build(
        transactions=[
            _txn("AMD", TransCode.SELL, date(2026, 6, 1), 4, 10),
            _txn("AMD", TransCode.BUY, date(2026, 6, 5), 4, 11),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AMD",
                quantity=4,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=40,
                cost_basis_1099=80,
                wash_sale_disallowed=40,
                status="1099_only",
            ),
            bucket="unmatched",
        ),
        realized_events=[
            _event("AMD", date(2026, 6, 1), 4, 80, 40, date(2026, 5, 1))
        ],
    )
    assert dated[0].hops[0].original_purchase_date == date(2026, 5, 1)
    assert dated[0].hops[0].replacement_buys

    window_only = _build(
        transactions=[_txn("NVDA", TransCode.BUY, date(2026, 6, 5), 2, 9)],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="NVDA",
                quantity=2,
                export_trade_date=None,
                date_sold_1099=date(2026, 6, 1),
                proceeds_1099=10,
                cost_basis_1099=20,
                wash_sale_disallowed=8,
                status="1099_only",
            ),
            bucket="unmatched",
        ),
        tax_lots=[_lot("NVDA", 2, 9, date(2026, 6, 5), wash=8)],
    )
    assert window_only[0].hops[0].replacement_buys
    assert window_only[0].hops[0].open_lot_total_cost_basis == pytest.approx(18)

    presale_two = [
        _txn("ORCL", TransCode.BUY, date(2025, 12, 20), 4, 100),
        _txn("ORCL", TransCode.BUY, date(2025, 12, 28), 6, 100),
        _txn("ORCL", TransCode.SELL, date(2026, 1, 10), 10, 80),
    ]
    presale_two_chains = _build(
        transactions=presale_two,
        wash_sale_flags=detect_wash_sales(presale_two, tax_year=2026),
    )
    assert " and " in presale_two_chains[0].plain_english
    assert "Dec 20, 2025" in presale_two_chains[0].plain_english

    skipped_sells = [
        _txn("AAPL", TransCode.BUY, date(2026, 6, 2), 10, 1),
        _txn("MSFT", TransCode.SELL, date(2026, 6, 1), 10, 1),
        _txn("AAPL", TransCode.SELL, date(2026, 5, 1), 10, 1),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 3, 1),
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 80),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 50),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 10, 55),
    ]
    skipped_events = [
        _event("MSFT", date(2026, 6, 1), 10, 1, 1, date(2026, 1, 1)),
        _event("AAPL", date(2026, 5, 1), 10, 1, 1, date(2026, 1, 1)),
        _event("AAPL", date(2026, 6, 1), 10, 800, 500, date(2026, 1, 1)),
    ]
    claimed = _build(
        transactions=skipped_sells,
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2026, 6, 1),
                sale_quantity=10,
                sale_loss=300,
                repurchase_date=date(2026, 6, 6),
                repurchase_quantity=10,
                disallowed_loss=300,
                adjusted_cost_basis=850,
            )
        ],
        realized_events=skipped_events,
        supplemental=_supplemental(2026),
        lot_match_report=None,
        tax_lots=[_lot("AAPL", 10, 55, date(2026, 1, 1))],
    )
    assert claimed[0].reconcile_status == "not_on_1099"
    assert claimed[0].hops[0].hop_id.endswith(":e2")
    assert claimed[0].hops[0].open_lot_total_cost_basis is None

    short_group = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 20),
            _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 10),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 5), 10, 12),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2026, 6, 1),
                sale_quantity=10,
                sale_loss=100,
                repurchase_date=date(2026, 6, 5),
                repurchase_quantity=10,
                disallowed_loss=100,
                adjusted_cost_basis=220,
            )
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AAPL",
                quantity=3,
                export_trade_date=date(2026, 5, 1),
                proceeds_1099=30,
                wash_sale_disallowed=1,
            ),
            _row(
                symbol="AAPL",
                quantity=3,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=30,
                wash_sale_disallowed=40,
            ),
        ),
    )
    assert any(chain.reconcile_status == "not_on_1099" for chain in short_group)
    assert any(chain.reconcile_status == "on_1099_only" for chain in short_group)

    from_event_price = _event("QQQ", date(2026, 4, 1), 10, 800, 500, date(2026, 1, 1))
    priced = _build(
        wash_sale_flags=[
            WashSaleFlag(
                symbol="QQQ",
                sale_date=date(2026, 4, 1),
                sale_quantity=10,
                sale_loss=50,
                repurchase_date=date(2026, 4, 5),
                repurchase_quantity=10,
                disallowed_loss=50,
                adjusted_cost_basis=50,
            )
        ],
        realized_events=[from_event_price],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="QQQ",
                quantity=0,
                export_trade_date=date(2026, 4, 1),
                proceeds_1099=0,
                wash_sale_disallowed=0,
            ),
            _row(
                symbol="QQQ",
                quantity=10,
                export_trade_date=date(2026, 4, 1),
                proceeds_1099=500,
                cost_basis_1099=550,
                wash_sale_disallowed=50,
            ),
        ),
    )
    assert priced[0].reconcile_status == "matches"
    assert priced[0].hops[0].form_1099_wash == pytest.approx(50)

    no_price = _build(
        wash_sale_flags=[
            WashSaleFlag(
                symbol="QQQ",
                sale_date=date(2026, 4, 2),
                sale_quantity=10,
                sale_loss=50,
                repurchase_date=date(2026, 4, 6),
                repurchase_quantity=10,
                disallowed_loss=50,
                adjusted_cost_basis=50,
            )
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="QQQ",
                quantity=0,
                export_trade_date=date(2026, 4, 2),
                proceeds_1099=0,
                wash_sale_disallowed=0,
            ),
            _row(
                symbol="QQQ",
                quantity=10,
                export_trade_date=date(2026, 4, 2),
                proceeds_1099=100,
                wash_sale_disallowed=50,
            ),
        ),
    )
    assert no_price[0].reconcile_status == "matches"

    unlink = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 100),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 80),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 90),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 15), 10, 70),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 18), 10, 75),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 20), 10, 60),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 25), 10, 65),
    ]
    unlinked = _build(
        transactions=unlink,
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2026, 6, 1),
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=date(2026, 6, 10),
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=1100,
            ),
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2026, 6, 15),
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=date(2026, 6, 18),
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=950,
            ),
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2026, 6, 20),
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=date(2026, 6, 25),
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=850,
            ),
        ],
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 1000, 800, date(2026, 1, 1)),
            _event("AAPL", date(2026, 6, 20), 10, 900, 700, date(2026, 6, 11)),
        ],
    )
    assert len(unlinked) == 3

    carry_zero = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 100),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 80),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 8), 0, 1),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 0, 1),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 90),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 20), 10, 70),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 25), 10, 72),
    ]
    carried = _build(
        transactions=carry_zero,
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2026, 6, 1),
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=date(2026, 6, 10),
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=1100,
            ),
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2026, 6, 20),
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=date(2026, 6, 25),
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=920,
            ),
        ],
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 1000, 800, date(2026, 1, 1)),
            _event("AAPL", date(2026, 6, 20), 10, 900, 700, date(2026, 6, 10)),
        ],
    )
    assert len(carried) == 1
    assert carried[0].hops[1].carried_in_disallowed == pytest.approx(200)

    # No sell: the attached event's per-share proceeds pick the 1099 row.
    from_events = _build(
        wash_sale_flags=[
            WashSaleFlag(
                symbol="QQQ",
                sale_date=date(2026, 4, 1),
                sale_quantity=10,
                sale_loss=300,
                repurchase_date=date(2026, 4, 5),
                repurchase_quantity=10,
                disallowed_loss=300,
                adjusted_cost_basis=800,
            )
        ],
        realized_events=[
            _event("QQQ", date(2026, 4, 1), 10, 800, 500, date(2026, 1, 1))
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="QQQ",
                quantity=10,
                export_trade_date=date(2026, 4, 1),
                proceeds_1099=500,
                wash_sale_disallowed=300,
            )
        ),
    )
    assert from_events[0].reconcile_status == "matches"
    assert from_events[0].hops[0].form_1099_wash == pytest.approx(300)

    blank_purchase = _event("AMD", date(2026, 8, 3), 4, 80, 40, date(2026, 7, 1))
    blank_purchase.purchase_date = None
    no_purchase_date = _build(
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AMD",
                quantity=4,
                export_trade_date=date(2026, 8, 3),
                proceeds_1099=40,
                cost_basis_1099=80,
                wash_sale_disallowed=40,
                status="1099_only",
            ),
            bucket="unmatched",
        ),
        realized_events=[
            _event("MSFT", date(2026, 8, 3), 4, 80, 40, date(2026, 7, 1)),
            _event("AMD", date(2026, 7, 1), 4, 80, 40, date(2026, 6, 1)),
            _event("AMD", date(2026, 8, 3), 2, 40, 20, date(2026, 7, 1)),
            blank_purchase,
        ],
    )
    assert no_purchase_date[0].hops[0].original_purchase_date is None

    shared_event = _build(
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AMD",
                quantity=4,
                export_trade_date=date(2026, 8, 1),
                proceeds_1099=40,
                cost_basis_1099=80,
                wash_sale_disallowed=40,
                status="1099_only",
            ),
            _row(
                symbol="AMD",
                quantity=4,
                export_trade_date=date(2026, 8, 2),
                proceeds_1099=40,
                cost_basis_1099=80,
                wash_sale_disallowed=40,
                status="1099_only",
            ),
            bucket="unmatched",
        ),
        realized_events=[
            _event("AMD", date(2026, 8, 1), 4, 80, 40, date(2026, 7, 2)),
        ],
    )
    august = sorted(shared_event, key=lambda chain: chain.hops[0].sale_date)
    assert august[0].hops[0].original_purchase_date == date(2026, 7, 2)
    assert august[1].hops[0].original_purchase_date is None

    option_event = _event("SPXW", date(2026, 9, 1), 1, 500, 200, date(2026, 8, 1))
    option_event.asset_type = AssetType.OPTION
    from_event_asset = _build(
        supplemental=_supplemental(2026),
        realized_events=[option_event],
        lot_match_report=_report(
            _row(
                symbol="SPXW",
                quantity=1,
                export_trade_date=date(2026, 9, 1),
                proceeds_1099=200,
                cost_basis_1099=500,
                wash_sale_disallowed=300,
                status="1099_only",
            ),
            bucket="unmatched",
        ),
    )
    assert len(from_event_asset) == 1
    assert len(from_event_asset[0].hops) == 1
    assert from_event_asset[0].hops[0].asset_type == "option"
    assert from_event_asset[0].hops[0].original_purchase_date is None
    assert from_event_asset[0].hops[0].replacement_buys == []
    assert from_event_asset[0].hops[0].form_1099_wash is None
    assert from_event_asset[0].hops[0].engine_disallowed_loss is None
    assert from_event_asset[0].plain_english == OPTION_LINE
    assert from_event_asset[0].reconcile_status == "not_compared"

    option_lot = _lot("SPX", 1, 5, date(2026, 9, 5))
    option_lot.asset_type = AssetType.OPTION
    from_lot_asset = _build(
        transactions=[_txn("SPX", TransCode.BUY, date(2026, 9, 5), 1, 5)],
        tax_lots=[option_lot],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="SPX",
                quantity=1,
                export_trade_date=date(2026, 9, 1),
                proceeds_1099=200,
                cost_basis_1099=500,
                wash_sale_disallowed=300,
                status="1099_only",
            ),
            bucket="unmatched",
        ),
    )
    lot_hop = from_lot_asset[0].hops[0]
    assert lot_hop.reconcile_status == "not_compared"
    assert lot_hop.asset_type == "unclassified"
    assert lot_hop.sale_date == date(2026, 9, 1)
    assert lot_hop.disallowed_loss == pytest.approx(0)
    assert lot_hop.replacement_buys == []
    assert "You sold" not in lot_hop.plain_english
    assert "not labeled as stock or an option" in lot_hop.plain_english


def test_chain_status_rolls_up_the_worst_hop():
    """Chip, difference, and headline follow the worst hop, not hop 0."""
    txns = _multi_hop_transactions()
    flags = detect_wash_sales(txns, tax_year=2026)
    assert len(flags) == 2
    first_loss = flags[0].disallowed_loss
    second_loss = flags[1].disallowed_loss
    report = _report(
        _row(
            symbol="AAPL",
            quantity=10,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=800,
            cost_basis_1099=1000,
            wash_sale_disallowed=first_loss,
        ),
        _row(
            symbol="AAPL",
            quantity=10,
            export_trade_date=date(2026, 6, 20),
            proceeds_1099=700,
            cost_basis_1099=900,
            wash_sale_disallowed=second_loss - 20,
        ),
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 1000, 800, date(2026, 1, 1)),
            _event("AAPL", date(2026, 6, 20), 10, 900, 700, date(2026, 6, 10)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    assert len(chains) == 1
    first, second = chains[0].hops
    assert first.reconcile_status == "matches"
    assert second.reconcile_status == "differs"
    assert second.difference == pytest.approx(20)
    assert chains[0].reconcile_status == "differs"
    assert chains[0].difference == pytest.approx(20)
    assert chains[0].plain_english == "AAPL wash-sale chain. Differs."
    assert "Hop 2:" not in chains[0].plain_english
    assert "Hop 2:" in second.plain_english
    assert chains[0].chain_id == first.hop_id


def test_multi_lot_sale_carryover_links_later_hop():
    """A 100-share sale of a 40-lot and a 60-lot keeps carryover and the next hop."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 40, 100),
        _txn("AAPL", TransCode.BUY, date(2026, 2, 1), 60, 80),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 50),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 100, 55),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 20), 100, 40),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 25), 100, 45),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    losses = [flag.disallowed_loss for flag in flags]
    events = [
        _event("AAPL", date(2026, 6, 1), 40, 4000, 2000, date(2026, 1, 1)),
        _event("AAPL", date(2026, 6, 1), 60, 4800, 3000, date(2026, 2, 1)),
        _event("AAPL", date(2026, 6, 20), 100, 5500, 4000, date(2026, 6, 6)),
    ]
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=events,
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 2
    first, second = chains[0].hops
    assert first.sale_quantity == pytest.approx(100)
    assert first.proceeds == pytest.approx(5000)
    assert first.cost_basis == pytest.approx(8800)
    assert first.tacked_acquisition_date is None
    assert first.carryover_days is None
    assert first.original_purchase_date is None
    assert "Holding period starts" not in first.plain_english
    assert [(lot.purchase_date, lot.quantity) for lot in first.closed_lots] == [
        (date(2026, 1, 1), 40),
        (date(2026, 2, 1), 60),
    ]
    assert first.hop_id.endswith(":e0")
    assert second.original_purchase_date == date(2026, 6, 6)
    assert second.hop_index == 1
    assert second.carried_in_disallowed == pytest.approx(losses[0])
    assert [flag.disallowed_loss for flag in flags] == losses
    assert events[0].quantity == pytest.approx(40)


def test_same_day_sales_attach_event_whose_proceeds_match():
    """Qty-only first-fit would take the wrong lot. Proceeds pick the right one."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 110),
        _txn("AAPL", TransCode.BUY, date(2026, 1, 2), 10, 100),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 70),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 80),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 20, 75),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    assert len(flags) == 2
    events = [
        _event("AAPL", date(2026, 6, 1), 10, 1000, 800, date(2026, 1, 2)),
        _event("AAPL", date(2026, 6, 1), 10, 1100, 700, date(2026, 1, 1)),
    ]
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=events,
    )
    hops = [hop for chain in chains for hop in chain.hops]
    by_proceeds = {hop.proceeds: hop for hop in hops}
    assert set(by_proceeds) == {700, 800}
    assert by_proceeds[700].hop_id.endswith(":e1")
    assert by_proceeds[700].loss == pytest.approx(400)
    assert by_proceeds[800].hop_id.endswith(":e0")
    assert by_proceeds[800].loss == pytest.approx(200)
    assert events[0].sale_proceeds == pytest.approx(800)


def test_cent_proceeds_three_share_fill_matches():
    """A 3-share sale still matches $33.33 + $66.67.

    Each row's per-share proceeds is within two cents of the sale price
    (100/3), so the match can use both rows. Leftover clustering is a
    different rule: that half-cent gap is one chain when the rows are
    unclaimed.
    """
    price = 100 / 3
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 3, 40),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 3, price),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 3, 35),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    assert len(flags) == 1
    disallowed = flags[0].disallowed_loss
    report = _report(
        _row(
            symbol="AAPL",
            quantity=1,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=33.33,
            cost_basis_1099=40,
            wash_sale_disallowed=round(disallowed / 3, 2),
        ),
        _row(
            symbol="AAPL",
            quantity=2,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=66.67,
            cost_basis_1099=80,
            wash_sale_disallowed=round(disallowed - round(disallowed / 3, 2), 2),
        ),
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    assert len(chains) == 1
    assert chains[0].reconcile_status == "matches"
    assert chains[0].hops[0].form_1099_wash == pytest.approx(disallowed)
    assert chains[0].hops[0].sale_quantity == pytest.approx(3)


def test_subset_sum_matches_fifty_plus_fifty_not_greedy_order():
    """Rows 10, 60, 50, 50. Greedy takes 10 then 60 and stalls. 50+50 matches."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 100, 80),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 50),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 100, 55),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    disallowed = flags[0].disallowed_loss
    half = round(disallowed / 2, 2)
    report = _report(
        _row(
            symbol="AAPL",
            quantity=10,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=500,
            wash_sale_disallowed=0,
        ),
        _row(
            symbol="AAPL",
            quantity=60,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=3000,
            wash_sale_disallowed=0,
        ),
        _row(
            symbol="AAPL",
            quantity=50,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=2500,
            wash_sale_disallowed=half,
        ),
        _row(
            symbol="AAPL",
            quantity=50,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=2500,
            wash_sale_disallowed=round(disallowed - half, 2),
        ),
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    matched = [chain for chain in chains if chain.reconcile_status == "matches"]
    assert len(matched) == 1
    assert matched[0].hops[0].form_1099_wash == pytest.approx(disallowed)
    assert matched[0].hops[0].sale_quantity == pytest.approx(100)


def test_ambiguous_1099_subsets_are_not_compared():
    """60+40 and 10+50+40 both fill 100 shares and disagree on the wash."""
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 6, 1),
        sale_quantity=100,
        sale_loss=500,
        repurchase_date=date(2026, 6, 6),
        repurchase_quantity=100,
        disallowed_loss=500,
        adjusted_cost_basis=1500,
    )
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 100, 20),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 15),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 100, 16),
    ]
    report = _report(
        _row(
            symbol="AAPL",
            quantity=10,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=150,
            wash_sale_disallowed=10,
        ),
        _row(
            symbol="AAPL",
            quantity=60,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=900,
            wash_sale_disallowed=200,
        ),
        _row(
            symbol="AAPL",
            quantity=50,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=750,
            wash_sale_disallowed=40,
        ),
        _row(
            symbol="AAPL",
            quantity=40,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=600,
            wash_sale_disallowed=80,
        ),
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=[flag],
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    engine = [chain for chain in chains if chain.hops[0].hop_id.endswith(":t2") or chain.symbol == "AAPL" and chain.hops[0].engine_disallowed_loss is not None]
    assert len(engine) == 1
    hop = engine[0].hops[0]
    assert hop.reconcile_status == "not_compared"
    assert hop.form_1099_wash is None
    assert hop.difference is None
    assert "no wash total was chosen" in hop.plain_english
    assert "found only in this Robinhood export" not in hop.plain_english
    assert len(chains) == 1
    assert flag.disallowed_loss == pytest.approx(500)


def test_over_cap_1099_candidates_are_not_compared():
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 6, 1),
        sale_quantity=1,
        sale_loss=5,
        repurchase_date=date(2026, 6, 6),
        repurchase_quantity=1,
        disallowed_loss=5,
        adjusted_cost_basis=15,
    )
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 1, 10),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 1, 5),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 1, 6),
    ]
    rows = [
        _row(
            symbol="AAPL",
            quantity=1,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=5,
            wash_sale_disallowed=0,
        )
        for _index in range(13)
    ]
    chains = _build(
        transactions=txns,
        wash_sale_flags=[flag],
        supplemental=_supplemental(2026),
        lot_match_report=_report(*rows),
    )
    assert len(chains) == 1
    assert chains[0].reconcile_status == "not_compared"
    assert "without guessing" in chains[0].plain_english
    assert chains[0].hops[0].form_1099_wash is None


def test_option_1099_row_is_one_not_compared_entry():
    """A 1099 option row is one hop. No basis, no replacement, no 1099 figure."""
    txns = [
        _txn(
            "SPX",
            TransCode.STC,
            date(2026, 6, 10),
            1,
            4,
            AssetType.OPTION,
            description="SPX Call 6/10",
        ),
        _txn("SPX", TransCode.BTO, date(2026, 6, 12), 1, 5, AssetType.OPTION),
    ]
    row = _row(
        status="1099_only",
        symbol="SPX",
        description="SPX Call",
        quantity=1,
        export_trade_date=date(2026, 6, 10),
        date_sold_1099=date(2026, 6, 12),
        proceeds_1099=400,
        cost_basis_1099=1000,
        wash_sale_disallowed=600,
    )
    zero_wash = _row(
        status="1099_only",
        symbol="SPX",
        description="SPX Put",
        quantity=1,
        export_trade_date=date(2026, 6, 11),
        wash_sale_disallowed=0,
    )
    no_date = _row(
        status="1099_only",
        symbol="SPX",
        description="SPX Call",
        quantity=1,
        export_trade_date=None,
        date_sold_1099=None,
        wash_sale_disallowed=12,
    )
    blank_symbol = _row(
        status="1099_only",
        symbol=" ",
        description="Call",
        quantity=1,
        export_trade_date=date(2026, 6, 10),
        wash_sale_disallowed=9,
    )
    plain_blank = _row(
        status="1099_only",
        symbol=" ",
        description="Shares",
        quantity=1,
        export_trade_date=date(2026, 6, 10),
        wash_sale_disallowed=4,
    )
    dateless_stock = _row(
        status="1099_only",
        symbol="MSFT",
        description="MSFT",
        quantity=1,
        export_trade_date=None,
        date_sold_1099=None,
        wash_sale_disallowed=5,
    )
    already_scaled = _lot("SPX", 1, 5, date(2026, 6, 12), wash=6)
    already_scaled.total_cost_basis = 500
    already_scaled.asset_type = AssetType.OPTION
    chains = _build(
        transactions=txns,
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            row,
            zero_wash,
            no_date,
            blank_symbol,
            plain_blank,
            dateless_stock,
            bucket="unmatched",
        ),
        tax_lots=[already_scaled],
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    hop = chains[0].hops[0]
    assert hop.asset_type == "option"
    assert hop.symbol == "SPX"
    assert chains[0].reconcile_status == "not_compared"
    assert hop.plain_english == OPTION_LINE
    assert hop.engine_disallowed_loss is None
    assert hop.form_1099_wash is None
    assert hop.disallowed_loss == pytest.approx(0)
    assert hop.replacement_buys == []
    assert hop.open_lot_total_cost_basis is None
    assert hop.closed_lots == []
    assert hop.carried_in_disallowed is None
    assert "contracts" not in hop.plain_english
    assert already_scaled.total_cost_basis == pytest.approx(500)
    assert already_scaled.wash_sale_disallowed == pytest.approx(6)


def test_same_wash_total_subsets_do_not_guess_a_different_amount():
    """Two single-row subsets with the same wash still compare that wash."""
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 6, 1),
        sale_quantity=10,
        sale_loss=100,
        repurchase_date=date(2026, 6, 6),
        repurchase_quantity=10,
        disallowed_loss=100,
        adjusted_cost_basis=200,
    )
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 20),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 10),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 10, 11),
    ]
    report = _report(
        _row(
            symbol="AAPL",
            quantity=10,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=100,
            wash_sale_disallowed=100,
        ),
        _row(
            symbol="AAPL",
            quantity=10,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=100.01,
            wash_sale_disallowed=100.01,
        ),
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=[flag],
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    matched = [chain for chain in chains if chain.hops[0].engine_disallowed_loss is not None]
    assert len(chains) == 1
    assert matched[0].reconcile_status == "matches"
    assert matched[0].hops[0].form_1099_wash == pytest.approx(100)


def test_too_many_realized_lots_are_not_guessed():
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 6, 1),
        sale_quantity=1,
        sale_loss=1,
        repurchase_date=date(2026, 6, 6),
        repurchase_quantity=1,
        disallowed_loss=1,
        adjusted_cost_basis=2,
    )
    events = [
        _event("AAPL", date(2026, 6, 1), 1, 2, 1, date(2026, 1, 1))
        for _index in range(13)
    ]
    chains = _build(wash_sale_flags=[flag], realized_events=events)
    assert chains[0].hops[0].hop_id.endswith(":f0")
    assert chains[0].hops[0].original_purchase_date is None
    assert events[0].quantity == pytest.approx(1)


def test_sample_2026_wash_total_stays_924_and_amd_chain(monkeypatch):
    """Guest sample keeps the $924 wash and shows the AMD chain."""
    csv_text = SAMPLE_CSV_PATH.read_text(encoding="utf-8")
    assert (
        "06/01/2026,06/01/2026,06/03/2026,AMD,Advanced Micro Devices,Buy,10,150.00,-1500.00"
        in csv_text
    )
    assert (
        "07/15/2026,07/15/2026,07/17/2026,AMD,Advanced Micro Devices,Sell,10,120.00,1200.00"
        in csv_text
    )
    assert (
        "07/24/2026,07/24/2026,07/28/2026,AMD,Advanced Micro Devices,Buy,10,125.00,-1250.00"
        in csv_text
    )
    assert is_trusted_sample_csv_bytes(SAMPLE_CSV_PATH.read_bytes())

    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None, allow_network=True: (
            {symbol.upper(): SAMPLE_FIXTURE_PRICES[symbol.upper()] for symbol in symbols},
            [],
        ),
    )
    monkeypatch.setattr(
        "main.fetch_option_prices",
        lambda labels, fb=None, allow_network=True: ({}, []),
    )
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda lots: [])
    monkeypatch.setattr("main._save_history_best_effort", lambda *args, **kwargs: None)
    main.reset_guest_analyze_quota()

    client = TestClient(main.app)
    response = client.post(
        "/api/portfolio/analyze?filing_status=single&estimated_income=75000&tax_year=2026",
        files={
            "file": (
                "sample-robinhood-transactions.csv",
                SAMPLE_CSV_PATH.read_bytes(),
                "text/csv",
            ),
            "supplemental_1099": (
                "sample-robinhood-1099-2026.pdf",
                SAMPLE_1099_PATH.read_bytes(),
                "application/pdf",
            ),
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    flags = data["wash_sale_flags"]
    chains = data["wash_sale_chains"]
    form_1099 = data["supplemental_1099"]
    csv_wash = round(sum(float(flag["disallowed_loss"]) for flag in flags), 2)
    broker_wash = round(
        form_1099["short_term_wash_sale_disallowed"]
        + form_1099["long_term_wash_sale_disallowed"],
        2,
    )
    export_st = round(data["summary"]["realized_summary"]["net_st"] + csv_wash)

    assert csv_wash == 924
    assert broker_wash == 924
    assert export_st == 0
    assert all(flag.get("purchase_date") in (None, "") for flag in flags)

    symbols = {chain["symbol"] for chain in chains}
    assert symbols == {"AMD", "NVDA", "TSLA"}
    assert "SPX" not in symbols
    assert all(chain["reconcile_status"] == "matches" for chain in chains)
    engine_sum = round(
        sum(chain["hops"][0]["engine_disallowed_loss"] for chain in chains),
        2,
    )
    assert engine_sum == 924

    amd = next(chain for chain in chains if chain["symbol"] == "AMD")
    assert amd["plain_english"] == AMD_LINE
    hop = amd["hops"][0]
    assert hop["carryover_days"] == 44
    assert hop["tacked_acquisition_date"] == "2026-06-01"
    assert hop["original_purchase_date"] == "2026-06-01"
    assert hop["engine_adjusted_cost_basis"] == pytest.approx(1550)
    assert hop["form_1099_wash"] == pytest.approx(300)
    assert hop["disallowed_loss"] == pytest.approx(300)
    assert hop["proceeds"] == pytest.approx(1200)
    assert hop["cost_basis"] == pytest.approx(1500)
    assert hop["open_lot_total_cost_basis"] == pytest.approx(1550)
    assert hop["replacement_buys"][0]["activity_date"] == "2026-07-24"
    assert hop["replacement_buys"][0]["quantity"] == pytest.approx(10)

    # Builder agrees with the HTTP payload and does not depend on a second parse path.
    from csv_parser import parse_csv

    _lots, transactions, _warnings, realized = parse_csv(csv_text)
    summary = parse_robinhood_1099_pdf(
        SAMPLE_1099_PATH.read_bytes(),
        current_symbols={"NVDA", "AMD", "TSLA", "AAPL", "META", "MSFT", "SPY"},
        filename="sample-robinhood-1099-2026.pdf",
    )
    direct_flags = detect_wash_sales(transactions, tax_year=2026)
    direct = build_wash_sale_chains(
        transactions=transactions,
        wash_sale_flags=direct_flags,
        realized_events=realized,
        tax_lots=[],
        supplemental=summary,
        lot_match_report=match_1099b_lots(
            summary.lots,
            realized,
            form_1099_tax_year=summary.tax_year,
            analysis_tax_year=2026,
            short_term_proceeds=summary.short_term_proceeds,
            long_term_proceeds=summary.long_term_proceeds,
            short_term_cost_basis=summary.short_term_cost_basis,
            long_term_cost_basis=summary.long_term_cost_basis,
            short_term_wash=summary.short_term_wash_sale_disallowed,
            long_term_wash=summary.long_term_wash_sale_disallowed,
        ),
        tax_year=2026,
    )
    amd_direct = next(chain for chain in direct if chain.symbol == "AMD")
    assert amd_direct.plain_english == AMD_LINE
    assert round(sum(flag.disallowed_loss for flag in direct_flags), 2) == 924


def test_ambiguous_candidate_rows_are_not_leftover_on_1099_only():
    """60+40 and 10+50+40 both fill 100 shares. Do not invent a 160-share chain."""
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 6, 1),
        sale_quantity=100,
        sale_loss=500,
        repurchase_date=date(2026, 6, 6),
        repurchase_quantity=100,
        disallowed_loss=500,
        adjusted_cost_basis=1500,
    )
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 100, 20),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 15),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 100, 16),
    ]
    report = _report(
        _row(
            symbol="AAPL",
            quantity=10,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=150,
            wash_sale_disallowed=10,
        ),
        _row(
            symbol="AAPL",
            quantity=60,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=900,
            wash_sale_disallowed=200,
        ),
        _row(
            symbol="AAPL",
            quantity=50,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=750,
            wash_sale_disallowed=40,
        ),
        _row(
            symbol="AAPL",
            quantity=40,
            export_trade_date=date(2026, 6, 1),
            proceeds_1099=600,
            wash_sale_disallowed=80,
        ),
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=[flag],
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "not_compared"
    assert hop.form_1099_wash is None
    assert hop.sale_quantity == pytest.approx(100)
    assert "no wash total was chosen" in hop.plain_english
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    assert all(chain.hops[0].sale_quantity != pytest.approx(160) for chain in chains)


def test_later_sale_of_one_replacement_date_does_not_link_the_other():
    """100 shares bought Jun 5 are not the 40 on Jun 5 plus 60 on Jun 8."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 100, 80),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 50),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 5), 40, 55),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 8), 60, 56),
        _txn("AAPL", TransCode.SELL, date(2026, 7, 20), 100, 40),
        _txn("AAPL", TransCode.BUY, date(2026, 7, 25), 100, 42),
    ]
    first = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 6, 1),
        sale_quantity=100,
        sale_loss=3000,
        repurchase_date=date(2026, 6, 5),
        repurchase_quantity=100,
        disallowed_loss=1000,
        adjusted_cost_basis=6500,
    )
    second = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 7, 20),
        sale_quantity=100,
        sale_loss=500,
        repurchase_date=date(2026, 7, 25),
        repurchase_quantity=100,
        disallowed_loss=500,
        adjusted_cost_basis=4700,
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=[first, second],
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 100, 8000, 5000, date(2026, 1, 1)),
            _event("AAPL", date(2026, 7, 20), 100, 4500, 4000, date(2026, 6, 5)),
        ],
    )
    assert len(chains) == 2
    earlier_chain = next(chain for chain in chains if chain.hops[0].sale_date == date(2026, 6, 1))
    later_chain = next(chain for chain in chains if chain.hops[0].sale_date == date(2026, 7, 20))
    assert len(earlier_chain.hops) == 1
    assert len(later_chain.hops) == 1
    allocated = {
        buy.activity_date: buy.allocated_quantity
        for buy in earlier_chain.hops[0].replacement_buys
    }
    assert allocated[date(2026, 6, 5)] == pytest.approx(40)
    assert allocated[date(2026, 6, 8)] == pytest.approx(60)
    later = later_chain.hops[0]
    assert later.hop_index == 0
    assert later.sale_quantity == pytest.approx(100)
    assert later.carried_in_disallowed == pytest.approx(0)
    assert later.carried_in_disallowed != pytest.approx(1000)
    assert "Part of the replacement was resold" not in later.plain_english
    assert "prior hop carried" not in later.plain_english
    assert [(lot.purchase_date, lot.quantity) for lot in later.closed_lots] == [
        (date(2026, 6, 5), 100),
    ]
    assert first.disallowed_loss == pytest.approx(1000)


def test_exact_resale_carries_once_and_a_later_sale_does_not_reuse_it():
    """Aug 10 resells the Jun 5 lot. Sep 20 does not get that carry again."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 100, 80),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 50),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 5), 100, 55),
        _txn("AAPL", TransCode.SELL, date(2026, 8, 10), 100, 40),
        _txn("AAPL", TransCode.BUY, date(2026, 8, 15), 100, 42),
        _txn("AAPL", TransCode.SELL, date(2026, 9, 20), 100, 30),
        _txn("AAPL", TransCode.BUY, date(2026, 9, 25), 100, 32),
    ]
    first = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 6, 1),
        sale_quantity=100,
        sale_loss=3000,
        repurchase_date=date(2026, 6, 5),
        repurchase_quantity=100,
        disallowed_loss=1000,
        adjusted_cost_basis=6500,
    )
    middle = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 8, 10),
        sale_quantity=100,
        sale_loss=500,
        repurchase_date=date(2026, 8, 15),
        repurchase_quantity=100,
        disallowed_loss=400,
        adjusted_cost_basis=4600,
    )
    last = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 9, 20),
        sale_quantity=100,
        sale_loss=500,
        repurchase_date=date(2026, 9, 25),
        repurchase_quantity=100,
        disallowed_loss=200,
        adjusted_cost_basis=3400,
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=[first, middle, last],
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 100, 8000, 5000, date(2026, 1, 1)),
            _event("AAPL", date(2026, 8, 10), 100, 4500, 4000, date(2026, 6, 5)),
            _event("AAPL", date(2026, 9, 20), 100, 3500, 3000, date(2026, 6, 5)),
        ],
    )
    assert len(chains) == 2
    linked = next(chain for chain in chains if len(chain.hops) == 2)
    alone = next(chain for chain in chains if len(chain.hops) == 1)
    assert [hop.sale_date for hop in linked.hops] == [
        date(2026, 6, 1),
        date(2026, 8, 10),
    ]
    assert linked.hops[1].carried_in_disallowed == pytest.approx(1000)
    assert linked.hops[1].carried_in_disallowed != pytest.approx(400)
    assert linked.hops[1].explained_loss_with_carry == pytest.approx(1500)
    assert "prior hop carried" in linked.hops[1].plain_english
    assert alone.hops[0].sale_date == date(2026, 9, 20)
    assert alone.hops[0].carried_in_disallowed == pytest.approx(0)
    assert alone.hops[0].carried_in_disallowed != pytest.approx(1000)
    assert "prior hop carried" not in alone.hops[0].plain_english
    assert first.disallowed_loss == pytest.approx(1000)


def test_same_year_1099_totals_without_lot_rows_are_not_compared():
    """Broker totals with lot_match_report None are not a missing 1099 row."""
    txns = [
        _txn("AMD", TransCode.BUY, date(2026, 6, 1), 10, 150),
        _txn("AMD", TransCode.SELL, date(2026, 7, 15), 10, 120),
        _txn("AMD", TransCode.BUY, date(2026, 7, 24), 10, 125),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    supplemental = Supplemental1099Summary(
        tax_year=2026,
        short_term_proceeds=1200,
        short_term_wash_sale_disallowed=300,
        lots=[],
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        supplemental=supplemental,
        lot_match_report=None,
        tax_year=2026,
    )
    assert len(chains) == 1
    assert chains[0].reconcile_status == "not_compared"
    assert chains[0].hops[0].form_1099_wash is None
    assert "No same-year 1099-B was compared" in chains[0].plain_english
    assert "found only in this Robinhood export" not in chains[0].plain_english
    assert all(chain.reconcile_status != "not_on_1099" for chain in chains)
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)


def test_1099_stock_stays_unscaled_when_the_symbol_also_has_options():
    """A stock 1099 sale does not take the option buy, and the wash stays in dollars."""
    stock_sell = _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 40)
    other_day = _txn("AAPL", TransCode.SELL, date(2026, 5, 1), 10, 40)
    wrong_proceeds = _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 99)
    other_symbol = _txn("MSFT", TransCode.SELL, date(2026, 6, 1), 10, 40)
    option_sell = _txn(
        "AAPL",
        TransCode.STC,
        date(2026, 6, 1),
        1,
        4,
        AssetType.OPTION,
        description="AAPL Call 6/1",
    )
    option_buy = _txn(
        "AAPL",
        TransCode.BTO,
        date(2026, 6, 5),
        1,
        5,
        AssetType.OPTION,
        description="AAPL Call 6/5",
    )
    ignored_event = _event("AAPL", date(2026, 6, 1), 10, 500, 999, date(2026, 1, 1))
    chains = _build(
        transactions=[
            stock_sell,
            other_day,
            wrong_proceeds,
            other_symbol,
            option_sell,
            option_buy,
        ],
        realized_events=[ignored_event],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="AAPL",
                quantity=10,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=400,
                cost_basis_1099=500,
                wash_sale_disallowed=100,
                description="AAPL",
            ),
            bucket="unmatched",
        ),
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "on_1099_only"
    assert hop.asset_type == "stock"
    assert hop.disallowed_loss == pytest.approx(100)
    assert hop.sale_quantity == pytest.approx(10)
    assert "contracts" not in hop.plain_english
    assert hop.replacement_buys == []
    assert stock_sell.price == pytest.approx(40)
    assert option_sell.price == pytest.approx(4)


def test_conflicting_stock_and_option_sales_are_not_compared():
    """Stock and option both match the 1099 sale, so the scale is not guessed."""
    stock = _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 1, 150)
    option_event = _event("AAPL", date(2026, 6, 1), 1, 200, 150, date(2026, 1, 1))
    option_event.asset_type = AssetType.OPTION
    other_event = _event("MSFT", date(2026, 6, 1), 1, 200, 150, date(2026, 1, 1))
    chains = _build(
        transactions=[
            stock,
            _txn("AAPL", TransCode.BTO, date(2026, 6, 5), 1, 2, AssetType.OPTION),
        ],
        realized_events=[option_event, other_event],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="AAPL",
                quantity=1,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=150,
                cost_basis_1099=200,
                wash_sale_disallowed=50,
            ),
            bucket="unmatched",
        ),
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "not_compared"
    assert hop.asset_type == "option"
    assert hop.plain_english == OPTION_LINE
    assert hop.engine_disallowed_loss is None
    assert hop.form_1099_wash is None
    assert hop.disallowed_loss == pytest.approx(0)
    assert hop.original_purchase_date is None
    assert hop.closed_lots == []
    assert hop.replacement_buys == []
    assert "could not be tied" not in hop.plain_english
    assert option_event.sale_proceeds == pytest.approx(150)


def test_option_wording_does_not_scale_an_unmatched_1099_sale():
    """Call wording rejects the stock sale. An option buy is not a scale."""
    stock = _txn(
        "AAPL",
        TransCode.SELL,
        date(2026, 6, 1),
        1,
        150,
        description="AAPL Call",
    )
    chains = _build(
        transactions=[
            stock,
            _txn("AAPL", TransCode.BTO, date(2026, 6, 5), 1, 2, AssetType.OPTION),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="AAPL",
                description="AAPL Call",
                quantity=1,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=150,
                cost_basis_1099=200,
                wash_sale_disallowed=50,
            ),
            bucket="unmatched",
        ),
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "not_compared"
    assert hop.asset_type == "option"
    assert hop.plain_english == OPTION_LINE
    assert hop.replacement_buys == []
    assert hop.form_1099_wash is None
    assert hop.engine_disallowed_loss is None

    event_only = _event("SPX", date(2026, 1, 1), 1, 10, 4, date(2025, 1, 1))
    event_only.asset_type = AssetType.OPTION
    undetermined = _build(
        realized_events=[event_only],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="SPX",
                quantity=1,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=400,
                cost_basis_1099=500,
                wash_sale_disallowed=80,
            ),
            bucket="unmatched",
        ),
    )
    blank = undetermined[0].hops[0]
    assert undetermined[0].reconcile_status == "not_compared"
    assert blank.asset_type == "unclassified"
    assert blank.disallowed_loss == pytest.approx(0)
    assert blank.proceeds == pytest.approx(0)
    assert blank.form_1099_wash is None
    assert "You sold" not in undetermined[0].plain_english
    assert "not labeled as stock or an option" in blank.plain_english
    assert "1 contracts" not in blank.plain_english


def test_stock_sale_description_rejects_a_plain_1099_row():
    """The broker sale says Call while the 1099 row does not. Do not scale."""
    chains = _build(
        transactions=[
            _txn(
                "AAPL",
                TransCode.SELL,
                date(2026, 6, 1),
                1,
                150,
                description="AAPL Call",
            ),
            _txn("AAPL", TransCode.BTO, date(2026, 6, 5), 1, 2, AssetType.OPTION),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="AAPL",
                description="AAPL",
                quantity=1,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=150,
                cost_basis_1099=200,
                wash_sale_disallowed=50,
            ),
            bucket="unmatched",
        ),
    )
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "not_compared"
    assert hop.asset_type == "unclassified"
    assert hop.disallowed_loss == pytest.approx(0)
    assert hop.replacement_buys == []
    assert "You sold" not in hop.plain_english
    assert "not labeled as stock or an option" in hop.plain_english


def test_two_matching_stock_sales_still_keep_the_closed_lot_date():
    """Several sells identify the stock. The lot date still attaches."""
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 40),
            _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 40),
        ],
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 500, 400, date(2026, 1, 1)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="AAPL",
                quantity=10,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=400,
                cost_basis_1099=500,
                wash_sale_disallowed=100,
            ),
            bucket="unmatched",
        ),
    )
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "on_1099_only"
    assert hop.asset_type == "stock"
    assert hop.original_purchase_date == date(2026, 1, 1)
    assert hop.closed_lots[0].quantity == pytest.approx(10)


def test_cent_proceeds_leftover_three_share_fill_is_one_chain():
    """$33.33 and $66.67 are one leftover sale.

    One share at $33.33 is 33.33 a share. Two shares at $66.67 are 33.335.
    That half-cent gap is rounding noise against the cluster anchor, so
    the rows are one 3-share chain. A full cent stays a separate sale.
    """
    chains = _build(
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="AAPL",
                quantity=1,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=33.33,
                cost_basis_1099=40,
                wash_sale_disallowed=10,
            ),
            _row(
                status="1099_only",
                symbol="AAPL",
                quantity=2,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=66.67,
                cost_basis_1099=80,
                wash_sale_disallowed=20,
            ),
            bucket="unmatched",
        ),
    )
    assert len(chains) == 1
    assert chains[0].hops[0].sale_quantity == pytest.approx(3)
    assert chains[0].hops[0].disallowed_loss == pytest.approx(30)
    assert chains[0].reconcile_status == "on_1099_only"

    anchored = _build(
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="IBM",
                quantity=1,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=10.00,
                wash_sale_disallowed=1,
            ),
            _row(
                status="1099_only",
                symbol="IBM",
                quantity=1,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=10.01,
                wash_sale_disallowed=1,
            ),
            _row(
                status="1099_only",
                symbol="IBM",
                quantity=1,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=10.02,
                wash_sale_disallowed=1,
            ),
            bucket="unmatched",
        ),
    )
    assert len(anchored) == 3
    quantities = sorted(chain.hops[0].sale_quantity for chain in anchored)
    assert quantities == pytest.approx([1, 1, 1])

    middle_first = _build(
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="IBM",
                quantity=1,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=10.01,
                wash_sale_disallowed=1,
            ),
            _row(
                status="1099_only",
                symbol="IBM",
                quantity=1,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=10.00,
                wash_sale_disallowed=1,
            ),
            _row(
                status="1099_only",
                symbol="IBM",
                quantity=1,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=10.02,
                wash_sale_disallowed=1,
            ),
            bucket="unmatched",
        ),
    )
    assert len(middle_first) == 3
    middle_quantities = sorted(chain.hops[0].sale_quantity for chain in middle_first)
    assert middle_quantities == pytest.approx([1, 1, 1])
    assert middle_quantities == quantities

    zeros = _build(
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="MSFT",
                quantity=0,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=0,
                wash_sale_disallowed=4,
            ),
            _row(
                status="1099_only",
                symbol="MSFT",
                quantity=0,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=0,
                wash_sale_disallowed=6,
            ),
            _row(
                status="1099_only",
                symbol="MSFT",
                quantity=1,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=10,
                wash_sale_disallowed=1,
            ),
            bucket="unmatched",
        ),
    )
    assert len(zeros) == 2
    blank = next(chain for chain in zeros if chain.hops[0].sale_quantity == pytest.approx(0))
    assert blank.hops[0].disallowed_loss == pytest.approx(10)
    assert blank.reconcile_status == "on_1099_only"


def test_cumulative_carry_adds_each_prior_disallowance():
    """Three $20 hops carry 0, then 20, then 40. Explained loss follows."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 10),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 8),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 9),
        _txn("AAPL", TransCode.SELL, date(2026, 7, 15), 10, 7),
        _txn("AAPL", TransCode.BUY, date(2026, 7, 20), 10, 8),
        _txn("AAPL", TransCode.SELL, date(2026, 8, 20), 10, 6),
        _txn("AAPL", TransCode.BUY, date(2026, 8, 25), 10, 7),
    ]
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 6, 1),
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=110,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 7, 15),
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 7, 20),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=100,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 8, 20),
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 8, 25),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=90,
        ),
    ]
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 100, 80, date(2026, 1, 1)),
            _event("AAPL", date(2026, 7, 15), 10, 90, 70, date(2026, 6, 10)),
            _event("AAPL", date(2026, 8, 20), 10, 80, 60, date(2026, 7, 20)),
        ],
    )
    assert len(chains) == 1
    hops = chains[0].hops
    assert [hop.carried_in_disallowed for hop in hops] == pytest.approx([0, 20, 40])
    assert [hop.explained_loss_with_carry for hop in hops] == pytest.approx([20, 40, 60])
    assert [hop.disallowed_loss for hop in hops] == pytest.approx([20, 20, 20])
    assert [flag.disallowed_loss for flag in flags] == pytest.approx([20, 20, 20])


def test_1099_only_multi_lot_keeps_each_acquired_date():
    """A 1099-only sale of 40 + 60 shares keeps both dates and no single sentence."""
    chains = _build(
        transactions=[_txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 50)],
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 40, 4000, 2000, date(2026, 1, 1)),
            _event("AAPL", date(2026, 6, 1), 60, 4800, 3000, date(2026, 2, 1)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="AAPL",
                quantity=100,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=5000,
                cost_basis_1099=8800,
                wash_sale_disallowed=1000,
            ),
            bucket="unmatched",
        ),
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "on_1099_only"
    assert hop.asset_type == "stock"
    assert hop.tacked_acquisition_date is None
    assert hop.original_purchase_date is None
    assert "Holding period starts" not in hop.plain_english
    assert [(lot.purchase_date, lot.quantity) for lot in hop.closed_lots] == [
        (date(2026, 1, 1), 40),
        (date(2026, 2, 1), 60),
    ]


def test_mixed_fifo_lot_does_not_link_when_an_acquired_date_is_outside():
    """40 shares from Jan 1 plus 60 of the Jun 6 replacement are not that lot."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 100, 80),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 50),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 100, 55),
        _txn("AAPL", TransCode.SELL, date(2026, 7, 20), 100, 44),
        _txn("AAPL", TransCode.BUY, date(2026, 7, 25), 100, 42),
    ]
    first = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 6, 1),
        sale_quantity=100,
        sale_loss=3000,
        repurchase_date=date(2026, 6, 6),
        repurchase_quantity=100,
        disallowed_loss=1000,
        adjusted_cost_basis=6500,
    )
    second = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 7, 20),
        sale_quantity=100,
        sale_loss=600,
        repurchase_date=date(2026, 7, 25),
        repurchase_quantity=100,
        disallowed_loss=600,
        adjusted_cost_basis=5000,
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=[first, second],
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 100, 8000, 5000, date(2026, 1, 1)),
            _event("AAPL", date(2026, 7, 20), 40, 2000, 1760, date(2026, 1, 1)),
            _event("AAPL", date(2026, 7, 20), 60, 3000, 2640, date(2026, 6, 6)),
        ],
    )
    assert len(chains) == 2
    earlier_chain = next(chain for chain in chains if chain.hops[0].sale_date == date(2026, 6, 1))
    later_chain = next(chain for chain in chains if chain.hops[0].sale_date == date(2026, 7, 20))
    later = later_chain.hops[0]
    assert later.hop_index == 0
    assert later.tacked_acquisition_date is None
    assert later.original_purchase_date is None
    assert "Holding period starts" not in later.plain_english
    assert [(lot.purchase_date, lot.quantity) for lot in later.closed_lots] == [
        (date(2026, 1, 1), 40),
        (date(2026, 6, 6), 60),
    ]
    assert later.carried_in_disallowed == pytest.approx(0)
    assert later.carried_in_disallowed != pytest.approx(1000)
    assert "prior hop carried" not in later.plain_english
    assert "Part of the replacement was resold" not in later.plain_english
    assert earlier_chain.hops[0].disallowed_loss == pytest.approx(1000)
    assert first.disallowed_loss == pytest.approx(1000)
    assert second.disallowed_loss == pytest.approx(600)


def test_partial_resale_hides_the_carry_figure():
    """Selling 50 of a 100-share replacement keeps the hop and hides the carry."""
    partial_copy = (
        "Part of the replacement was resold, so the carry-over isn't shown here."
    )
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 100, 80),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 50),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 5), 100, 55),
        _txn("AAPL", TransCode.SELL, date(2026, 8, 1), 50, 40),
        _txn("AAPL", TransCode.BUY, date(2026, 8, 10), 50, 42),
    ]
    first = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 6, 1),
        sale_quantity=100,
        sale_loss=3000,
        repurchase_date=date(2026, 6, 5),
        repurchase_quantity=100,
        disallowed_loss=1000,
        adjusted_cost_basis=6500,
    )
    second = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 8, 1),
        sale_quantity=50,
        sale_loss=500,
        repurchase_date=date(2026, 8, 10),
        repurchase_quantity=50,
        disallowed_loss=250,
        adjusted_cost_basis=2350,
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=[first, second],
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 100, 8000, 5000, date(2026, 1, 1)),
            _event("AAPL", date(2026, 8, 1), 50, 2500, 2000, date(2026, 6, 5)),
        ],
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 2
    earlier, later = chains[0].hops
    assert later.hop_index == 1
    assert later.sale_quantity == pytest.approx(50)
    assert later.carried_in_disallowed is None
    assert later.explained_loss_with_carry is None
    assert later.disallowed_loss == pytest.approx(250)
    assert partial_copy in later.plain_english
    assert "prior hop carried" not in later.plain_english
    assert partial_copy not in earlier.plain_english
    assert first.disallowed_loss == pytest.approx(1000)
    assert second.disallowed_loss == pytest.approx(250)


def test_same_day_exact_resales_do_not_pick_a_carry_winner():
    """Two later sales on one day both match the replacement. Neither carries it."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 10),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 8),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 9),
        _txn("AAPL", TransCode.SELL, date(2026, 7, 1), 10, 7),
        _txn("AAPL", TransCode.SELL, date(2026, 7, 1), 10, 6),
        _txn("AAPL", TransCode.BUY, date(2026, 7, 10), 10, 8),
    ]
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 6, 1),
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=110,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 7, 1),
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 7, 10),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=100,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 7, 1),
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 7, 10),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=100,
        ),
    ]
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 100, 80, date(2026, 1, 1)),
            _event("AAPL", date(2026, 7, 1), 10, 90, 70, date(2026, 6, 10)),
            _event("AAPL", date(2026, 7, 1), 10, 80, 60, date(2026, 6, 10)),
        ],
    )
    assert len(chains) == 3
    assert all(len(chain.hops) == 1 for chain in chains)
    july = [chain for chain in chains if chain.hops[0].sale_date == date(2026, 7, 1)]
    assert len(july) == 2
    assert all(
        chain.hops[0].original_purchase_date == date(2026, 6, 10) for chain in july
    )
    assert all(chain.hops[0].carried_in_disallowed == pytest.approx(0) for chain in chains)
    assert all("prior hop carried" not in chain.plain_english for chain in chains)
    assert flags[0].disallowed_loss == pytest.approx(20)


def test_hidden_carry_stays_hidden_on_the_next_exact_resale():
    """A hop whose parent hid the carry does not start the figure again."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 100, 80),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 50),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 5), 100, 55),
        _txn("AAPL", TransCode.SELL, date(2026, 7, 15), 50, 40),
        _txn("AAPL", TransCode.BUY, date(2026, 7, 20), 50, 42),
        _txn("AAPL", TransCode.SELL, date(2026, 8, 20), 50, 30),
        _txn("AAPL", TransCode.BUY, date(2026, 8, 25), 50, 32),
    ]
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 6, 1),
            sale_quantity=100,
            sale_loss=3000,
            repurchase_date=date(2026, 6, 5),
            repurchase_quantity=100,
            disallowed_loss=1000,
            adjusted_cost_basis=6500,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 7, 15),
            sale_quantity=50,
            sale_loss=500,
            repurchase_date=date(2026, 7, 20),
            repurchase_quantity=50,
            disallowed_loss=200,
            adjusted_cost_basis=2300,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 8, 20),
            sale_quantity=50,
            sale_loss=500,
            repurchase_date=date(2026, 8, 25),
            repurchase_quantity=50,
            disallowed_loss=100,
            adjusted_cost_basis=1700,
        ),
    ]
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 100, 8000, 5000, date(2026, 1, 1)),
            _event("AAPL", date(2026, 7, 15), 50, 2500, 2000, date(2026, 6, 5)),
            _event("AAPL", date(2026, 8, 20), 50, 2000, 1500, date(2026, 7, 20)),
        ],
    )
    assert len(chains) == 1
    hops = chains[0].hops
    assert len(hops) == 3
    assert hops[1].carried_in_disallowed is None
    assert hops[1].explained_loss_with_carry is None
    assert hops[2].carried_in_disallowed is None
    assert hops[2].explained_loss_with_carry is None
    assert "Part of the replacement was resold" in hops[1].plain_english
    assert "prior hop carried" not in hops[2].plain_english


def test_tacked_date_without_closed_lots_does_not_link():
    """A shared date is not a link unless the closed lots equal the replacement."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 10),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 8),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 9),
        _txn("AAPL", TransCode.SELL, date(2026, 7, 15), 10, 7),
        _txn("AAPL", TransCode.BUY, date(2026, 7, 20), 10, 8),
    ]
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 6, 1),
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=110,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 7, 15),
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 7, 20),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=100,
        ),
    ]
    missing_date = _event("AAPL", date(2026, 7, 15), 10, 90, 70, date(2026, 6, 10))
    missing_date.purchase_date = None
    without_lots = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 100, 80, date(2026, 1, 1)),
            missing_date,
        ],
    )
    assert len(without_lots) == 2
    later = next(
        chain for chain in without_lots if chain.hops[0].sale_date == date(2026, 7, 15)
    )
    assert later.hops[0].closed_lots == [] or later.hops[0].closed_lots[0].purchase_date is None
    assert later.hops[0].carried_in_disallowed == pytest.approx(0)
    assert "prior hop carried" not in later.plain_english

    linked = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 100, 80, date(2026, 1, 1)),
            _event("AAPL", date(2026, 7, 15), 10, 90, 70, date(2026, 6, 10)),
        ],
    )
    assert len(linked) == 1
    assert linked[0].hops[1].carried_in_disallowed == pytest.approx(20)
    assert linked[0].hops[1].explained_loss_with_carry == pytest.approx(40)


def test_exact_lot_match_is_not_a_partial_resale():
    """An exact resale is carry, not the partial-resale sentence."""
    from wash_sale_chain import (
        _BuyDraft,
        _ClosedLot,
        _HopDraft,
        _lots_are_partial_resale,
    )

    earlier = _HopDraft(
        hop_id="AAPL:2026-06-01:10:e0",
        symbol="AAPL",
        asset_type="stock",
        sale_date=date(2026, 6, 1),
        sale_quantity=10,
        proceeds=80,
        cost_basis=100,
        loss=20,
        buys=[
            _BuyDraft(
                activity_date=date(2026, 6, 10),
                quantity=10,
                price=9,
                cash_cost=90,
                allocated_quantity=10,
                allocated_disallowed=20,
                explained_basis=110,
            )
        ],
        disallowed_loss=20,
    )
    later = _HopDraft(
        hop_id="AAPL:2026-07-15:10:e0",
        symbol="AAPL",
        asset_type="stock",
        sale_date=date(2026, 7, 15),
        sale_quantity=10,
        proceeds=70,
        cost_basis=90,
        loss=20,
        closed_lots=[_ClosedLot(quantity=10, purchase_date=date(2026, 6, 10), holding_days=35)],
    )
    assert _lots_are_partial_resale(earlier, later) is False


def test_stock_chain_ignores_same_date_option_replacement_and_open_lot():
    """A same-size option close is not compared, and a later stock hop stays stock.

    June 1 is 10 shares and 10 contracts, so both sides are not compared and
    the option entry does not show a dollar figure. The August stock sale
    still uses the stock buy, not the option buy or the option lot.
    """
    stock_buy = _txn("AMD", TransCode.BUY, date(2026, 6, 5), 10, 55)
    option_buy = _txn("AMD", TransCode.BTO, date(2026, 6, 5), 1, 5, AssetType.OPTION)
    option_sell_same_size = _txn(
        "AMD", TransCode.STC, date(2026, 6, 1), 10, 50, AssetType.OPTION
    )
    txns = [
        _txn("AMD", TransCode.BUY, date(2026, 1, 1), 10, 80),
        option_sell_same_size,
        _txn("AMD", TransCode.SELL, date(2026, 6, 1), 10, 50),
        option_buy,
        stock_buy,
        _txn("AMD", TransCode.SELL, date(2026, 8, 10), 10, 40),
        _txn("AMD", TransCode.BUY, date(2026, 8, 15), 10, 42),
        _txn("AMD", TransCode.STC, date(2026, 7, 1), 1, 4, AssetType.OPTION),
        _txn("AMD", TransCode.BTO, date(2026, 7, 5), 1, 5, AssetType.OPTION),
    ]
    stock_first = WashSaleFlag(
        symbol="AMD",
        sale_date=date(2026, 6, 1),
        sale_quantity=10,
        sale_loss=300,
        repurchase_date=date(2026, 6, 5),
        repurchase_quantity=10,
        disallowed_loss=300,
        adjusted_cost_basis=850,
    )
    stock_later = WashSaleFlag(
        symbol="AMD",
        sale_date=date(2026, 8, 10),
        sale_quantity=10,
        sale_loss=450,
        repurchase_date=date(2026, 8, 15),
        repurchase_quantity=10,
        disallowed_loss=250,
        adjusted_cost_basis=670,
    )
    option_flag = WashSaleFlag(
        symbol="AMD",
        sale_date=date(2026, 7, 1),
        sale_quantity=1,
        sale_loss=6,
        repurchase_date=date(2026, 7, 5),
        repurchase_quantity=1,
        disallowed_loss=6,
        adjusted_cost_basis=11,
    )
    option_event = _event("AMD", date(2026, 6, 1), 10, 800, 500, date(2025, 1, 1))
    option_event.asset_type = AssetType.OPTION
    stock_open = _lot("AMD", 10, 85, date(2026, 6, 5), wash=300)
    stock_open.total_cost_basis = 850
    option_open = _lot("AMD", 1, 5, date(2026, 6, 5), wash=6)
    option_open.asset_type = AssetType.OPTION
    option_open.total_cost_basis = 500
    chains = _build(
        transactions=txns,
        wash_sale_flags=[stock_first, option_flag, stock_later],
        realized_events=[
            option_event,
            _event("AMD", date(2026, 6, 1), 10, 800, 500, date(2026, 1, 1)),
            _event("AMD", date(2026, 8, 10), 10, 850, 400, date(2026, 6, 5)),
        ],
        tax_lots=[option_open, stock_open],
    )
    stock_chains = [chain for chain in chains if chain.hops[0].asset_type == "stock"]
    option_chains = [chain for chain in chains if chain.hops[0].asset_type == "option"]
    # June 1 is 10 shares and 10 contracts. Both sides are not compared.
    june_stock = next(chain for chain in stock_chains if chain.hops[0].sale_date == date(2026, 6, 1))
    august = next(chain for chain in stock_chains if chain.hops[0].sale_date == date(2026, 8, 10))
    assert june_stock.reconcile_status == "not_compared"
    assert june_stock.hops[0].proceeds == pytest.approx(0)
    assert "neither was compared" in june_stock.plain_english
    assert len(june_stock.hops) == 1
    assert august.hops[0].replacement_buys[0].activity_date == date(2026, 8, 15)
    assert august.hops[0].replacement_buys[0].price == pytest.approx(42)
    assert august.hops[0].carried_in_disallowed == pytest.approx(0)
    assert all(chain.hops[0].plain_english == OPTION_LINE for chain in option_chains)
    assert all(chain.hops[0].engine_disallowed_loss is None for chain in option_chains)
    assert all(chain.hops[0].disallowed_loss == pytest.approx(0) for chain in option_chains)
    assert all(chain.hops[0].proceeds == pytest.approx(0) for chain in option_chains)
    assert all(chain.hops[0].replacement_buys == [] for chain in option_chains)
    assert {chain.hops[0].sale_date for chain in option_chains} == {
        date(2026, 6, 1),
        date(2026, 7, 1),
    }
    assert all(len(chain.hops) == 1 for chain in chains)
    assert stock_first.disallowed_loss == pytest.approx(300)
    assert option_flag.disallowed_loss == pytest.approx(6)
    assert option_open.total_cost_basis == pytest.approx(500)
    assert option_open.wash_sale_disallowed == pytest.approx(6)
    assert stock_buy.price == pytest.approx(55)
    assert option_buy.price == pytest.approx(5)


def test_option_sale_does_not_link_to_a_stock_hop():
    """Option and stock sales of one symbol stay one hop each, in both directions."""
    option_may = WashSaleFlag(
        symbol="AMD",
        sale_date=date(2026, 5, 1),
        sale_quantity=1,
        sale_loss=6,
        repurchase_date=date(2026, 5, 10),
        repurchase_quantity=1,
        disallowed_loss=6,
        adjusted_cost_basis=11,
    )
    stock = WashSaleFlag(
        symbol="AMD",
        sale_date=date(2026, 6, 1),
        sale_quantity=1,
        sale_loss=20,
        repurchase_date=date(2026, 6, 15),
        repurchase_quantity=1,
        disallowed_loss=20,
        adjusted_cost_basis=32,
    )
    option_july = WashSaleFlag(
        symbol="AMD",
        sale_date=date(2026, 7, 1),
        sale_quantity=1,
        sale_loss=4,
        repurchase_date=date(2026, 7, 5),
        repurchase_quantity=1,
        disallowed_loss=4,
        adjusted_cost_basis=8,
    )
    july_event = _event("AMD", date(2026, 7, 1), 1, 12, 8, date(2026, 6, 15))
    july_event.asset_type = AssetType.OPTION
    chains = _build(
        transactions=[
            _txn("AMD", TransCode.STC, date(2026, 5, 1), 1, 4, AssetType.OPTION),
            _txn("AMD", TransCode.BTO, date(2026, 5, 10), 1, 5, AssetType.OPTION),
            _txn("AMD", TransCode.BUY, date(2026, 1, 1), 1, 30),
            _txn("AMD", TransCode.SELL, date(2026, 6, 1), 1, 10),
            _txn("AMD", TransCode.BUY, date(2026, 6, 15), 1, 12),
            _txn("AMD", TransCode.STC, date(2026, 7, 1), 1, 3, AssetType.OPTION),
            _txn("AMD", TransCode.BTO, date(2026, 7, 5), 1, 4, AssetType.OPTION),
        ],
        wash_sale_flags=[option_may, stock, option_july],
        realized_events=[
            _event("AMD", date(2026, 6, 1), 1, 30, 10, date(2026, 5, 10)),
            july_event,
        ],
    )
    assert len(chains) == 3
    assert all(len(chain.hops) == 1 for chain in chains)
    stock_chain = next(chain for chain in chains if chain.hops[0].asset_type == "stock")
    option_chains = [chain for chain in chains if chain.hops[0].asset_type == "option"]
    assert len(option_chains) == 2
    hop = stock_chain.hops[0]
    assert hop.carried_in_disallowed == pytest.approx(0)
    assert hop.carried_in_disallowed != pytest.approx(6)
    assert hop.replacement_buys[0].activity_date == date(2026, 6, 15)
    assert hop.replacement_buys[0].allocated_quantity == pytest.approx(1)
    assert hop.closed_lots[0].purchase_date == date(2026, 5, 10)
    assert OPTION_LINE not in hop.plain_english
    assert all(chain.hops[0].plain_english == OPTION_LINE for chain in option_chains)
    assert all(chain.hops[0].replacement_buys == [] for chain in option_chains)
    assert all(chain.hops[0].closed_lots == [] for chain in option_chains)
    assert all(chain.hops[0].engine_disallowed_loss is None for chain in option_chains)
    assert all(chain.hops[0].disallowed_loss == pytest.approx(0) for chain in option_chains)
    assert stock.disallowed_loss == pytest.approx(20)
    assert option_may.disallowed_loss == pytest.approx(6)

    # Same-day stock and option lots: the flag follows the quantity that fits.
    stock_sized = _event("QQQ", date(2026, 4, 1), 10, 1000, 700, date(2026, 1, 1))
    option_sized = _event("QQQ", date(2026, 4, 1), 1, 100, 40, date(2026, 1, 2))
    option_sized.asset_type = AssetType.OPTION
    stock_qty = _build(
        wash_sale_flags=[
            WashSaleFlag(
                symbol="QQQ",
                sale_date=date(2026, 4, 1),
                sale_quantity=10,
                sale_loss=300,
                repurchase_date=date(2026, 4, 5),
                repurchase_quantity=10,
                disallowed_loss=300,
                adjusted_cost_basis=400,
            )
        ],
        realized_events=[option_sized, stock_sized],
    )
    assert stock_qty[0].hops[0].asset_type == "stock"
    assert stock_qty[0].hops[0].closed_lots[0].quantity == pytest.approx(10)
    assert OPTION_LINE not in stock_qty[0].plain_english

    option_qty = _build(
        wash_sale_flags=[
            WashSaleFlag(
                symbol="QQQ",
                sale_date=date(2026, 4, 1),
                sale_quantity=1,
                sale_loss=6,
                repurchase_date=date(2026, 4, 5),
                repurchase_quantity=1,
                disallowed_loss=6,
                adjusted_cost_basis=11,
            )
        ],
        realized_events=[stock_sized, option_sized],
    )
    assert len(option_qty) == 1
    assert option_qty[0].hops[0].plain_english == OPTION_LINE
    assert option_qty[0].hops[0].engine_disallowed_loss is None
    assert option_qty[0].hops[0].disallowed_loss == pytest.approx(0)
    assert option_qty[0].hops[0].closed_lots == []

    neither = _build(
        wash_sale_flags=[
            WashSaleFlag(
                symbol="QQQ",
                sale_date=date(2026, 4, 1),
                sale_quantity=2,
                sale_loss=5,
                repurchase_date=date(2026, 4, 5),
                repurchase_quantity=2,
                disallowed_loss=5,
                adjusted_cost_basis=9,
            )
        ],
        realized_events=[
            stock_sized,
            option_sized,
            _event("IBM", date(2026, 4, 1), 2, 10, 5, date(2026, 1, 1)),
            _event("QQQ", date(2026, 4, 1), 0, 0, 0, date(2026, 1, 1)),
        ],
    )
    assert neither[0].hops[0].asset_type == "stock"
    assert OPTION_LINE not in neither[0].plain_english


def test_option_words_match_plurals_and_the_end_of_the_text():
    """calls, puts, and a word at the end of the description are option text."""
    from wash_sale_chain import _option_text

    assert _option_text("AAPL calls") is True
    assert _option_text("puts") is True
    assert _option_text("option") is True
    assert _option_text("weekly options") is True
    assert _option_text("AAPL 6/10/2026 CALL $10") is True
    assert _option_text("callable") is False
    assert _option_text("recall notice") is False
    assert _option_text("input") is False
    assert _option_text("optional") is False
    chains = _build(
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="AAPL",
                description="AAPL calls",
                quantity=1,
                export_trade_date=date(2026, 6, 10),
                proceeds_1099=400,
                wash_sale_disallowed=600,
            ),
            bucket="unmatched",
        ),
    )
    assert len(chains) == 1
    assert chains[0].hops[0].asset_type == "option"
    assert chains[0].hops[0].sale_date == date(2026, 6, 10)
    assert chains[0].plain_english == OPTION_LINE
    assert chains[0].reconcile_status == "not_compared"


def test_stock_sale_does_not_reconcile_an_option_1099_row():
    """A 1-share sale does not match a same-day 1-contract call row."""
    sale = date(2026, 6, 10)
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 6, 1), 1, 1000),
        _txn("AAPL", TransCode.SELL, sale, 1, 400),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 12), 1, 410),
        _txn(
            "AAPL",
            TransCode.STC,
            sale,
            1,
            4,
            AssetType.OPTION,
            description="AAPL 6/10/2026 CALL $10",
        ),
        _txn("AAPL", TransCode.BTO, date(2026, 6, 12), 1, 5, AssetType.OPTION),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    flag_losses = [flag.disallowed_loss for flag in flags]
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AAPL",
                description="AAPL 6/10/2026 CALL $10",
                quantity=1,
                export_trade_date=sale,
                proceeds_1099=400,
                cost_basis_1099=1000,
                wash_sale_disallowed=600,
            )
        ),
    )
    option = [chain for chain in chains if chain.hops[0].asset_type == "option"]
    stock = [chain for chain in chains if chain.hops[0].asset_type == "stock"]
    assert len(option) == 1
    assert option[0].reconcile_status == "not_compared"
    assert option[0].plain_english == OPTION_LINE
    assert option[0].hops[0].sale_date == sale
    assert option[0].hops[0].engine_disallowed_loss is None
    assert option[0].hops[0].proceeds == pytest.approx(0)
    assert option[0].hops[0].form_1099_wash is None
    assert stock
    assert all(chain.reconcile_status == "not_compared" for chain in stock)
    assert all(chain.hops[0].form_1099_wash is None for chain in stock)
    assert all(chain.reconcile_status != "matches" for chain in chains)
    assert all(chain.reconcile_status != "differs" for chain in chains)
    assert all("You sold" not in chain.hops[0].plain_english or chain.hops[0].form_1099_wash != 600 for chain in chains)
    assert [flag.disallowed_loss for flag in flags] == flag_losses


def test_option_flag_does_not_borrow_same_day_stock_proceeds():
    """An option flag and a same-day same-qty stock sale are both not compared."""
    sale = date(2026, 6, 10)
    first = WashSaleFlag(
        symbol="AAPL",
        sale_date=sale,
        sale_quantity=1,
        sale_loss=6,
        repurchase_date=date(2026, 6, 12),
        repurchase_quantity=1,
        disallowed_loss=6,
        adjusted_cost_basis=11,
    )
    second = WashSaleFlag(
        symbol="AAPL",
        sale_date=sale,
        sale_quantity=1,
        sale_loss=6,
        repurchase_date=date(2026, 6, 12),
        repurchase_quantity=1,
        disallowed_loss=6,
        adjusted_cost_basis=11,
    )
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.STC, sale, 1, 4, AssetType.OPTION),
            _txn("AAPL", TransCode.BTO, date(2026, 6, 12), 1, 5, AssetType.OPTION),
            _txn("AAPL", TransCode.SELL, sale, 1, 400),
        ],
        wash_sale_flags=[first, second],
    )
    option = [chain for chain in chains if chain.hops[0].asset_type == "option"]
    stock = [chain for chain in chains if chain.hops[0].asset_type == "stock"]
    assert len(option) == 1
    assert len(stock) == 2
    assert {chain.hops[0].hop_id.rsplit(":", 1)[-1] for chain in stock} == {"f0", "f1"}
    assert len({chain.chain_id for chain in chains}) == 3
    assert option[0].reconcile_status == "not_compared"
    assert option[0].plain_english == OPTION_LINE
    assert option[0].hops[0].proceeds == pytest.approx(0)
    assert option[0].hops[0].sale_date == sale
    assert option[0].hops[0].engine_disallowed_loss is None
    assert all(chain.reconcile_status == "not_compared" for chain in stock)
    assert all(chain.hops[0].proceeds == pytest.approx(0) for chain in stock)
    assert all("neither was compared" in chain.plain_english for chain in stock)
    assert first.disallowed_loss == pytest.approx(6)
    assert second.disallowed_loss == pytest.approx(6)


def test_overlapping_candidate_row_is_not_compared():
    """A 25-share row in both a 100-share and a 50-share band does not vanish."""
    sale = date(2026, 6, 1)
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=100,
            sale_loss=100,
            repurchase_date=date(2026, 6, 6),
            repurchase_quantity=100,
            disallowed_loss=100,
            adjusted_cost_basis=200,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=50,
            sale_loss=50,
            repurchase_date=date(2026, 6, 6),
            repurchase_quantity=50,
            disallowed_loss=50,
            adjusted_cost_basis=100,
        ),
    ]
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.SELL, sale, 100, 10),
            _txn("AAPL", TransCode.SELL, sale, 50, 10),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 100, 11),
        ],
        wash_sale_flags=flags,
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(symbol="AAPL", quantity=100, export_trade_date=sale, proceeds_1099=1000, wash_sale_disallowed=10),
            _row(symbol="AAPL", quantity=50, export_trade_date=sale, proceeds_1099=500, wash_sale_disallowed=10),
            _row(symbol="AAPL", quantity=25, export_trade_date=sale, proceeds_1099=250, wash_sale_disallowed=5),
        ),
    )
    leftover = [chain for chain in chains if chain.hops[0].sale_quantity == pytest.approx(25)]
    assert len(leftover) == 1
    assert leftover[0].reconcile_status == "not_compared"
    assert leftover[0].hops[0].form_1099_wash == pytest.approx(5)
    assert leftover[0].hops[0].not_compared_reason == "unassigned"
    assert "overlapped more than one possible sale" in leftover[0].plain_english
    assert "You sold" not in leftover[0].plain_english
    sales = [chain for chain in chains if chain.hops[0].sale_quantity != pytest.approx(25)]
    assert len(sales) == 2
    assert all(chain.reconcile_status == "not_compared" for chain in sales)
    assert all(chain.hops[0].form_1099_wash is None for chain in sales)


def test_ambiguous_match_keeps_a_row_that_fits_no_subset():
    """A candidate outside every disagreeing subset is its own not-compared entry."""
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=date(2026, 6, 1),
        sale_quantity=100,
        sale_loss=500,
        repurchase_date=date(2026, 6, 6),
        repurchase_quantity=100,
        disallowed_loss=500,
        adjusted_cost_basis=1500,
    )
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 100, 20),
            _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 100, 15),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 100, 16),
        ],
        wash_sale_flags=[flag],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(symbol="AAPL", quantity=10, export_trade_date=date(2026, 6, 1), proceeds_1099=150, wash_sale_disallowed=10),
            _row(symbol="AAPL", quantity=60, export_trade_date=date(2026, 6, 1), proceeds_1099=900, wash_sale_disallowed=200),
            _row(symbol="AAPL", quantity=50, export_trade_date=date(2026, 6, 1), proceeds_1099=750, wash_sale_disallowed=40),
            _row(symbol="AAPL", quantity=40, export_trade_date=date(2026, 6, 1), proceeds_1099=600, wash_sale_disallowed=80),
            _row(symbol="AAPL", quantity=7, export_trade_date=date(2026, 6, 1), proceeds_1099=105, wash_sale_disallowed=9),
        ),
    )
    extra = [chain for chain in chains if chain.hops[0].sale_quantity == pytest.approx(7)]
    assert len(extra) == 1
    assert extra[0].reconcile_status == "not_compared"
    assert extra[0].hops[0].form_1099_wash == pytest.approx(9)
    assert extra[0].hops[0].not_compared_reason == "unassigned"
    assert "overlapped more than one possible sale" in extra[0].plain_english
    assert "You sold" not in extra[0].plain_english
    engine = [chain for chain in chains if chain.hops[0].engine_disallowed_loss is not None]
    assert len(engine) == 1
    assert engine[0].reconcile_status == "not_compared"
    assert len(chains) == 2


def test_two_washes_and_a_later_partial_stay_unlinked():
    """One 20-share resale that fits two washes does not become a three-hop chain."""
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 6, 1),
            sale_quantity=10,
            sale_loss=300,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=30,
            disallowed_loss=300,
            adjusted_cost_basis=800,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 6, 5),
            sale_quantity=10,
            sale_loss=300,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=30,
            disallowed_loss=300,
            adjusted_cost_basis=700,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=date(2026, 8, 1),
            sale_quantity=20,
            sale_loss=300,
            repurchase_date=date(2026, 8, 10),
            repurchase_quantity=20,
            disallowed_loss=200,
            adjusted_cost_basis=900,
        ),
    ]
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 80),
            _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 50),
            _txn("AAPL", TransCode.BUY, date(2026, 1, 2), 10, 70),
            _txn("AAPL", TransCode.SELL, date(2026, 6, 5), 10, 40),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 30, 55),
            _txn("AAPL", TransCode.SELL, date(2026, 8, 1), 20, 40),
            _txn("AAPL", TransCode.BUY, date(2026, 8, 10), 20, 42),
        ],
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 800, 500, date(2026, 1, 1)),
            _event("AAPL", date(2026, 6, 5), 10, 700, 400, date(2026, 1, 2)),
            _event("AAPL", date(2026, 8, 1), 20, 1100, 800, date(2026, 6, 10)),
        ],
    )
    assert all(len(chain.hops) < 3 for chain in chains)
    later = next(chain for chain in chains if chain.hops[0].sale_date == date(2026, 8, 1))
    assert len(later.hops) == 1
    assert "Part of the replacement was resold" not in later.plain_english
    assert later.hops[0].carried_in_disallowed == pytest.approx(0)


def test_two_realized_lot_sets_omit_acquisition_and_holding_period():
    """Two lot sets that both fit the loss do not pick a purchase date."""
    sale = date(2026, 6, 1)
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.SELL, sale, 10, 70),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 72),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=10,
                sale_loss=300,
                repurchase_date=date(2026, 6, 10),
                repurchase_quantity=10,
                disallowed_loss=300,
                adjusted_cost_basis=1000,
            )
        ],
        realized_events=[
            _event("AAPL", sale, 10, 1000, 700, date(2026, 1, 1)),
            _event("AAPL", sale, 10, 1000, 700, date(2026, 2, 1)),
        ],
    )
    hop = chains[0].hops[0]
    assert hop.original_purchase_date is None
    assert hop.original_holding_days is None
    assert hop.carryover_days is None
    assert hop.tacked_acquisition_date is None
    assert hop.closed_lots == []
    assert "Holding period starts" not in hop.plain_english


def test_penny_apart_washes_stay_two_chains():
    """Two 10-share washes one cent apart are not one leftover cluster."""
    chains = _build(
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="IBM",
                quantity=10,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=100.00,
                wash_sale_disallowed=10,
            ),
            _row(
                status="1099_only",
                symbol="IBM",
                quantity=10,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=100.10,
                wash_sale_disallowed=10,
            ),
            bucket="unmatched",
        ),
    )
    assert len(chains) == 2
    assert sorted(chain.hops[0].sale_quantity for chain in chains) == pytest.approx([10, 10])
    assert {chain.hops[0].proceeds for chain in chains} == {100.0, 100.1}


def test_chain_headline_uses_symbol_and_status():
    """A multi-hop headline is the symbol and status, not the first sale date."""
    from wash_sale_chain import _HopDraft, _chain_headline

    head = _HopDraft(
        hop_id="AAPL:2026-06-01:10:e0",
        symbol="AAPL",
        asset_type="stock",
        sale_date=date(2026, 6, 1),
        sale_quantity=10,
        proceeds=80,
        cost_basis=100,
        loss=20,
    )
    assert _chain_headline(head, "matches") == "AAPL wash-sale chain. Matches."
    assert _chain_headline(head, "differs") == "AAPL wash-sale chain. Differs."
    assert _chain_headline(head, "on_1099_only") == "AAPL wash-sale chain. On your 1099 only."
    assert _chain_headline(head, "not_on_1099") == "AAPL wash-sale chain. Not on your 1099."
    assert _chain_headline(head, "not_compared") == "AAPL wash-sale chain. Not compared."
    assert _chain_headline(head, "other") == "AAPL wash-sale chain. Not compared."
    assert "Jun 1" not in _chain_headline(head, "differs")
    assert "Hop 2:" not in _chain_headline(head, "differs")


def test_dead_display_scale_multiplier_is_gone():
    """Stock math no longer multiplies by a display scale of 1."""
    source = (Path(__file__).resolve().parents[1] / "wash_sale_chain.py").read_text(
        encoding="utf-8"
    )
    assert "scale = 1.0" not in source
    assert "* scale" not in source


def test_attach_flag_sale_skips_an_option_sell_of_the_same_size():
    """A same-size option sell is not the stock sale the flag attaches."""
    from wash_sale_chain import _attach_flag_sale

    sale = date(2026, 6, 10)
    _index, sell, event_set, unresolved = _attach_flag_sale(
        [
            _txn("AAPL", TransCode.STC, sale, 10, 4, AssetType.OPTION),
            _txn("AAPL", TransCode.SELL, sale, 10, 50),
        ],
        [_event("AAPL", sale, 10, 800, 500, date(2026, 1, 1))],
        set(),
        set(),
        symbol="AAPL",
        sale_date=sale,
        quantity=10,
        sale_loss=300,
    )
    assert unresolved is False
    assert sell is not None
    assert sell.asset_type == AssetType.STOCK
    assert event_set
    assert event_set[0][1].sale_proceeds == pytest.approx(500)


def test_open_lot_lookup_skips_an_option_lot_on_the_replacement_date():
    """An option lot on the repurchase date is not the stock open lot."""
    option_lot = _lot("AAPL", 1, 5, date(2026, 6, 10), wash=6)
    option_lot.asset_type = AssetType.OPTION
    stock_lot = _lot("AAPL", 10, 72, date(2026, 6, 10), wash=300)
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 50),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 55),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=date(2026, 6, 1),
                sale_quantity=10,
                sale_loss=300,
                repurchase_date=date(2026, 6, 10),
                repurchase_quantity=10,
                disallowed_loss=300,
                adjusted_cost_basis=1000,
            )
        ],
        tax_lots=[option_lot, stock_lot],
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 800, 500, date(2026, 1, 1)),
        ],
    )
    hop = chains[0].hops[0]
    assert hop.asset_type == "stock"
    assert hop.open_lot_total_cost_basis == pytest.approx(720)
    assert hop.open_lot_wash_sale_disallowed == pytest.approx(300)
    assert option_lot.total_cost_basis == pytest.approx(5)


def test_builder_skips_an_unassigned_row_the_helper_refuses(monkeypatch):
    """A reserved overlap row the helper will not show does not become a chain."""
    import wash_sale_chain

    monkeypatch.setattr(wash_sale_chain, "_unassigned_from_row", lambda _row, _index: None)
    sale = date(2026, 6, 1)
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.SELL, sale, 100, 10),
            _txn("AAPL", TransCode.SELL, sale, 50, 10),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 100, 11),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=100,
                sale_loss=100,
                repurchase_date=date(2026, 6, 6),
                repurchase_quantity=100,
                disallowed_loss=100,
                adjusted_cost_basis=200,
            ),
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=50,
                sale_loss=50,
                repurchase_date=date(2026, 6, 6),
                repurchase_quantity=50,
                disallowed_loss=50,
                adjusted_cost_basis=100,
            ),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(symbol="AAPL", quantity=100, export_trade_date=sale, proceeds_1099=1000, wash_sale_disallowed=10),
            _row(symbol="AAPL", quantity=50, export_trade_date=sale, proceeds_1099=500, wash_sale_disallowed=10),
            _row(symbol="AAPL", quantity=25, export_trade_date=sale, proceeds_1099=250, wash_sale_disallowed=5),
        ),
    )
    assert len(chains) == 2
    assert all(chain.hops[0].sale_quantity != pytest.approx(25) for chain in chains)
    assert all(chain.reconcile_status == "not_compared" for chain in chains)


def test_unassigned_row_without_a_symbol_or_date_is_skipped():
    from wash_sale_chain import _unassigned_from_row

    assert _unassigned_from_row(_row(symbol=" ", wash_sale_disallowed=5), 0) is None
    assert (
        _unassigned_from_row(
            _row(export_trade_date=None, date_sold_1099=None, wash_sale_disallowed=5),
            1,
        )
        is None
    )
    sourced = _unassigned_from_row(
        _row(symbol="AAPL", export_trade_date=date(2026, 6, 1), wash_sale_disallowed=5),
        2,
    )
    assert sourced is not None
    assert sourced.form_1099_wash == pytest.approx(5)
    assert sourced.not_compared_reason == "unassigned"
    blank = _unassigned_from_row(
        _row(symbol="AAPL", export_trade_date=date(2026, 6, 1), wash_sale_disallowed=0),
        3,
    )
    assert blank is not None
    assert blank.form_1099_wash is None


def test_qty_subsets_stops_past_the_candidate_cap():
    """The subset search itself refuses more than 12 candidates."""
    from wash_sale_chain import _qty_subsets

    items = [(index, 1.0) for index in range(13)]
    assert _qty_subsets(items, 1.0, lambda item: item[1]) == []


def test_tied_sales_over_the_subset_cap_finish_without_guessing():
    """Forty same-price rows and two same-day flags are not subset-searched."""
    import time

    sale = date(2026, 6, 1)
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=10,
            repurchase_date=date(2026, 6, 6),
            repurchase_quantity=10,
            disallowed_loss=10,
            adjusted_cost_basis=110,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=20,
            sale_loss=20,
            repurchase_date=date(2026, 6, 6),
            repurchase_quantity=20,
            disallowed_loss=20,
            adjusted_cost_basis=220,
        ),
    ]
    rows = [
        _row(
            symbol="AAPL",
            quantity=1,
            export_trade_date=sale,
            proceeds_1099=10,
            cost_basis_1099=11,
            wash_sale_disallowed=1,
        )
        for _index in range(40)
    ]
    started = time.perf_counter()
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.SELL, sale, 10, 10),
            _txn("AAPL", TransCode.SELL, sale, 20, 10),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 30, 11),
        ],
        wash_sale_flags=flags,
        supplemental=_supplemental(2026),
        lot_match_report=_report(*rows),
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 2
    singles = [
        chain for chain in chains if chain.hops[0].sale_quantity == pytest.approx(1)
    ]
    flagged = [
        chain for chain in chains if chain.hops[0].sale_quantity != pytest.approx(1)
    ]
    assert len(singles) == 40
    assert len(flagged) == 2
    assert sorted(chain.hops[0].sale_quantity for chain in flagged) == pytest.approx([10, 20])
    assert all(chain.reconcile_status == "not_compared" for chain in chains)
    assert all(chain.hops[0].form_1099_wash == pytest.approx(1) for chain in singles)
    assert all(chain.hops[0].not_compared_reason == "unassigned" for chain in singles)
    assert all("You sold" not in chain.plain_english for chain in singles)
    assert all(chain.reconcile_status != "matches" for chain in chains)


def test_option_1099_lots_do_not_hold_back_a_stock_wash():
    """Call lots of 4 and 6 contracts do not overlap a 4-share stock wash."""
    sale = date(2026, 6, 1)
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=sale,
        sale_quantity=4,
        sale_loss=200,
        repurchase_date=date(2026, 6, 6),
        repurchase_quantity=4,
        disallowed_loss=200,
        adjusted_cost_basis=420,
    )
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 4, 100),
            _txn("AAPL", TransCode.SELL, sale, 4, 50),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 4, 55),
        ],
        wash_sale_flags=[flag],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AAPL",
                description="AAPL 06/01/2026 Call $100",
                quantity=4,
                export_trade_date=sale,
                proceeds_1099=40,
                wash_sale_disallowed=4,
            ),
            _row(
                symbol="AAPL",
                description="AAPL 06/01/2026 Call $100",
                quantity=6,
                export_trade_date=sale,
                proceeds_1099=60,
                wash_sale_disallowed=6,
            ),
            _row(
                symbol="AAPL",
                quantity=4,
                export_trade_date=sale,
                proceeds_1099=200,
                cost_basis_1099=400,
                wash_sale_disallowed=200,
            ),
        ),
    )
    stock = [chain for chain in chains if chain.hops[0].asset_type == "stock"]
    option = [chain for chain in chains if chain.hops[0].asset_type == "option"]
    assert len(stock) == 1
    assert len(option) == 1
    assert stock[0].hops[0].sale_quantity == pytest.approx(4)
    assert stock[0].hops[0].proceeds == pytest.approx(200)
    assert "neither was compared" not in stock[0].plain_english
    assert stock[0].hops[0].not_compared_reason != "overlap"
    assert option[0].hops[0].sale_quantity == pytest.approx(10)
    assert option[0].plain_english == OPTION_LINE
    assert option[0].hops[0].form_1099_wash is None
    assert option[0].hops[0].proceeds == pytest.approx(0)
    assert flag.disallowed_loss == pytest.approx(200)


def test_each_overlapping_stock_flag_keeps_a_hop():
    """Two same-size stock sells and one option close each keep a hop."""
    sale = date(2026, 6, 1)
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=300,
            repurchase_date=date(2026, 6, 6),
            repurchase_quantity=10,
            disallowed_loss=300,
            adjusted_cost_basis=800,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=200,
            repurchase_date=date(2026, 6, 6),
            repurchase_quantity=10,
            disallowed_loss=200,
            adjusted_cost_basis=700,
        ),
    ]
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.SELL, sale, 10, 50),
            _txn("AAPL", TransCode.SELL, sale, 10, 40),
            _txn("AAPL", TransCode.STC, sale, 10, 4, AssetType.OPTION),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 10, 55),
        ],
        wash_sale_flags=flags,
    )
    stock = [chain for chain in chains if chain.hops[0].asset_type == "stock"]
    option = [chain for chain in chains if chain.hops[0].asset_type == "option"]
    assert len(stock) == 2
    assert len(option) == 1
    assert {chain.hops[0].hop_id.rsplit(":", 1)[-1] for chain in stock} == {"f0", "f1"}
    assert len({chain.chain_id for chain in chains}) == 3
    assert all(chain.reconcile_status == "not_compared" for chain in stock)
    assert all(chain.hops[0].not_compared_reason == "overlap" for chain in stock)
    assert all(chain.hops[0].proceeds == pytest.approx(0) for chain in stock)
    assert all("neither was compared" in chain.plain_english for chain in stock)
    assert option[0].plain_english == OPTION_LINE
    assert option[0].hops[0].sale_quantity == pytest.approx(10)
    assert [flag.disallowed_loss for flag in flags] == pytest.approx([300, 200])
