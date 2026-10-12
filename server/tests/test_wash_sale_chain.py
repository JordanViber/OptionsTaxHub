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


def _txn(symbol, code, when, quantity, price, asset=AssetType.STOCK):
    signed = -1 if code in (TransCode.BUY, TransCode.BTO) else 1
    return Transaction(
        activity_date=when,
        process_date=when,
        settle_date=when,
        instrument=symbol,
        description="",
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
    assert chains[0].plain_english == first.plain_english
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


def test_option_copy_says_contracts():
    txns = [
        _txn("SPX", TransCode.BTO, date(2026, 6, 1), 1, 10, AssetType.OPTION),
        _txn("SPX", TransCode.STC, date(2026, 6, 10), 1, 4, AssetType.OPTION),
        _txn("SPX", TransCode.BTO, date(2026, 6, 12), 1, 5, AssetType.OPTION),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    chains = _build(transactions=txns, wash_sale_flags=flags)
    assert chains
    hop = chains[0].hops[0]
    assert "contracts" in chains[0].plain_english
    assert hop.loss == pytest.approx(600)
    assert hop.disallowed_loss == pytest.approx(600)
    assert hop.proceeds == pytest.approx(400)
    assert hop.cost_basis == pytest.approx(1000)
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
    assert hop.tacked_acquisition_date == date(2026, 1, 1)
    assert flags[0].disallowed_loss == pytest.approx(3800)


def test_option_chain_scales_display_with_contract_multiplier():
    """Flag premiums stay unscaled. The chain matches realized-event dollars."""
    from leap_rank import CONTRACT_MULTIPLIER

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
    engine = [
        chain for chain in chains if chain.reconcile_status != "on_1099_only"
    ]
    assert len(engine) == 1
    hop = engine[0].hops[0]
    assert hop.loss == pytest.approx(6 * CONTRACT_MULTIPLIER)
    assert hop.disallowed_loss == pytest.approx(600)
    assert hop.cost_basis == pytest.approx(1000)
    assert hop.proceeds == pytest.approx(400)
    assert hop.engine_disallowed_loss == pytest.approx(600)
    assert hop.engine_sale_loss == pytest.approx(600)
    assert hop.engine_adjusted_cost_basis == pytest.approx(11 * CONTRACT_MULTIPLIER)
    assert hop.form_1099_wash == pytest.approx(600)
    assert hop.reconcile_status == "matches"
    assert hop.open_lot_total_cost_basis == pytest.approx(5 * CONTRACT_MULTIPLIER)
    assert hop.open_lot_wash_sale_disallowed == pytest.approx(6 * CONTRACT_MULTIPLIER)
    assert hop.replacement_buys[0].price == pytest.approx(5 * CONTRACT_MULTIPLIER)
    assert hop.replacement_buys[0].allocated_disallowed == pytest.approx(600)
    assert hop.replacement_buys[0].cash_cost == pytest.approx(500)
    assert "contracts" in hop.plain_english
    assert "$600" in hop.plain_english
    only = [chain for chain in chains if chain.reconcile_status == "on_1099_only"]
    assert len(only) == 1
    assert only[0].hops[0].disallowed_loss == pytest.approx(6)
    assert flags[0].sale_loss == pytest.approx(6)
    assert flags[0].disallowed_loss == pytest.approx(6)
    assert flags[0].adjusted_cost_basis == pytest.approx(11)
    assert lots[0].total_cost_basis == pytest.approx(5)


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
    assert txn_ids == ["AAPL:2026-06-01:10:t2", "AAPL:2026-06-01:10:t3"]


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
    from wash_sale_chain import _claim_event, _event_candidates, _hop_from_1099_group

    assert _claim_event([], set(), "AAPL", None, 1) == (None, None)
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
    assert scaled[0].hops[0].asset_type == "option"
    assert scaled[0].hops[0].loss == pytest.approx(600)
    assert scaled[0].hops[0].proceeds == pytest.approx(400)
    assert scaled[0].hops[0].cost_basis == pytest.approx(1000)
    assert scaled[0].hops[0].hop_id.endswith(":e0")

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
    assert from_event_asset[0].hops[0].asset_type == "option"

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
    assert from_lot_asset[0].hops[0].asset_type == "option"


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
    assert chains[0].plain_english == second.plain_english
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
    assert first.tacked_acquisition_date == date(2026, 1, 1)
    assert first.carryover_days == (date(2026, 6, 1) - date(2026, 1, 1)).days
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
    """$33.33 + $66.67 is one 3-share sale even though unit cents differ."""
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


def test_option_1099_only_scales_replacement_and_open_lot():
    """A 1099-only option is not labeled stock, and basis stays in contract dollars."""
    from leap_rank import CONTRACT_MULTIPLIER

    txns = [
        _txn("SPX", TransCode.BTO, date(2026, 6, 12), 1, 5, AssetType.OPTION),
    ]
    row = _row(
        status="1099_only",
        symbol="SPX",
        quantity=1,
        export_trade_date=date(2026, 6, 10),
        date_sold_1099=date(2026, 6, 12),
        proceeds_1099=400,
        cost_basis_1099=1000,
        wash_sale_disallowed=600,
    )
    already_scaled = _lot("SPX", 1, 5, date(2026, 6, 12), wash=6)
    already_scaled.total_cost_basis = 5 * CONTRACT_MULTIPLIER
    already_scaled.asset_type = AssetType.OPTION
    chains = _build(
        transactions=txns,
        supplemental=_supplemental(2026),
        lot_match_report=_report(row, bucket="unmatched"),
        tax_lots=[already_scaled],
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.asset_type == "option"
    assert hop.disallowed_loss == pytest.approx(600)
    assert hop.replacement_buys[0].price == pytest.approx(5 * CONTRACT_MULTIPLIER)
    assert hop.replacement_buys[0].cash_cost == pytest.approx(5 * CONTRACT_MULTIPLIER)
    assert hop.replacement_buys[0].allocated_disallowed == pytest.approx(600)
    assert hop.replacement_buys[0].explained_basis == pytest.approx(1100)
    assert hop.open_lot_total_cost_basis == pytest.approx(500)
    assert hop.open_lot_wash_sale_disallowed == pytest.approx(600)
    assert "contracts" in hop.plain_english
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


def test_later_sale_links_union_of_replacement_buys_with_carry():
    """40 and 60 replacement buys together cover a later 100-share wash."""
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
    assert len(chains) == 1
    assert len(chains[0].hops) == 2
    earlier, later = chains[0].hops
    allocated = {
        buy.activity_date: buy.allocated_quantity for buy in earlier.replacement_buys
    }
    assert allocated[date(2026, 6, 5)] == pytest.approx(40)
    assert allocated[date(2026, 6, 8)] == pytest.approx(60)
    assert later.hop_index == 1
    assert later.sale_quantity == pytest.approx(100)
    assert later.carried_in_disallowed == pytest.approx(1000)
    assert "Hop 2:" in later.plain_english
    assert "carried" in later.plain_english
    assert first.disallowed_loss == pytest.approx(1000)


def test_carry_skips_an_earlier_hop_whose_replacements_do_not_match():
    """Hop 3's purchase matches hop 1, not hop 2, so carry comes from hop 1."""
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
    assert len(chains) == 1
    assert [hop.hop_index for hop in chains[0].hops] == [0, 1, 2]
    assert chains[0].hops[2].carried_in_disallowed == pytest.approx(1000)
    assert chains[0].hops[2].carried_in_disallowed != pytest.approx(400)
    assert "Hop 3:" in chains[0].hops[2].plain_english


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
