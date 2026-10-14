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
from csv_parser import RealizedEvent, transactions_to_tax_lots
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


def test_two_washes_stay_separate_chains_without_carry():
    """Each flag is its own chain. The later sale does not inherit a carry."""
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
    assert len(chains) == 2
    assert all(len(chain.hops) == 1 for chain in chains)
    assert all(chain.chain_id == chain.hops[0].hop_id for chain in chains)
    second = next(chain.hops[0] for chain in chains if chain.hops[0].sale_date == date(2026, 6, 20))
    assert second.original_purchase_date == date(2026, 6, 10)
    assert second.engine_sale_loss == pytest.approx(200.0)
    assert second.disallowed_loss == pytest.approx(200.0)
    assert second.hop_index == 0
    for chain in chains:
        hop = chain.hops[0]
        assert hop.carried_in_disallowed is None
        assert hop.explained_loss_with_carry is None
        assert "Hop 2" not in hop.plain_english
        assert "prior hop" not in hop.plain_english
        assert "Part of the replacement was resold" not in hop.plain_english
        assert chain.plain_english == hop.plain_english
        assert "wash-sale chain." not in chain.plain_english
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
    assert hop.proceeds is None
    assert hop.cost_basis is None
    assert hop.loss == pytest.approx(0)
    assert hop.engine_adjusted_cost_basis is None
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
    assert len(chains) == 2
    assert all(len(chain.hops) == 1 for chain in chains)
    assert all(chain.reconcile_status == "not_compared" for chain in chains)
    assert all(chain.plain_english == OPTION_LINE for chain in chains)
    quantities = sorted(chain.hops[0].sale_quantity for chain in chains)
    assert quantities == pytest.approx([1, 2])
    hop = next(chain.hops[0] for chain in chains if chain.hops[0].sale_quantity == pytest.approx(2))
    assert hop.asset_type == "option"
    assert hop.sale_date == date(2026, 6, 10)
    assert hop.engine_disallowed_loss is None
    assert hop.disallowed_loss == pytest.approx(0)
    assert hop.form_1099_wash is None
    assert hop.replacement_buys == []
    assert hop.engine_adjusted_cost_basis is None
    assert hop.proceeds is None
    assert hop.cost_basis is None
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
    # Both losses fit within CENTS and the proceeds differ by more than
    # CENTS. Neither sale is chosen, so the other sale stays hidden.
    assert ids == ["AAPL:2026-06-01:10:f0", "AAPL:2026-06-01:10:f1"]
    assert len(set(ids)) == 2
    assert all(chain.chain_id == chain.hops[0].hop_id for chain in chains)
    assert len(chains) == 2
    assert all(chain.hops[0].proceeds is None for chain in chains)
    assert all(chain.hops[0].cost_basis is None for chain in chains)
    assert all(chain.hops[0].not_compared_reason == "unidentified" for chain in chains)
    assert all("You sold" not in chain.plain_english for chain in chains)

    without_events = _build(transactions=txns, wash_sale_flags=flags)
    txn_ids = sorted(chain.chain_id for chain in without_events)
    # No realized lots, so neither sale's proceeds are safe to show.
    assert txn_ids == ["AAPL:2026-06-01:10:f0", "AAPL:2026-06-01:10:f1"]
    assert all(chain.hops[0].proceeds is None for chain in without_events)
    assert all(chain.hops[0].cost_basis is None for chain in without_events)
    assert all(chain.hops[0].replacement_buys == [] for chain in without_events)
    assert all(chain.hops[0].not_compared_reason == "unidentified" for chain in without_events)
    assert all(chain.reconcile_status == "not_compared" for chain in without_events)
    assert all(
        chain.plain_english
        == "This sale could not be tied to one trade's proceeds and loss, so it was not compared."
        for chain in without_events
    )
    assert all("illustrative" not in chain.plain_english for chain in without_events)
    assert all("You sold" not in chain.plain_english for chain in without_events)


def test_penny_apart_ten_share_sales_keep_their_own_1099_rows():
    """Ten shares one cent apart differ by $0.10, so each sale keeps its own row."""
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
    matched = next(chain for chain in chains if chain.reconcile_status == "matches")
    differed = next(chain for chain in chains if chain.reconcile_status == "differs")
    assert matched.hops[0].form_1099_wash == pytest.approx(20)
    assert differed.hops[0].form_1099_wash == pytest.approx(40)
    assert differed.difference == pytest.approx(10)
    assert all("no wash total was chosen" not in chain.plain_english for chain in chains)
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
    from wash_sale_chain import (
        _event_candidates,
        _hop_from_1099_group,
        _target_unit_proceeds,
    )

    assert _event_candidates([], set(), "AAPL", None) == []
    assert _target_unit_proceeds(None, []) is None
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
    assert orphans[0].hops[0].not_compared_reason == "unidentified"
    assert orphans[0].hops[0].proceeds is None
    assert orphans[0].hops[0].cost_basis is None
    assert orphans[0].hops[0].replacement_buys == []
    assert "You sold" not in orphans[0].plain_english
    assert orphans[1].hops[0].hop_id.endswith(":f2")
    assert "1.5" in orphans[1].hops[0].hop_id
    assert orphans[1].hops[0].not_compared_reason == "unidentified"
    assert orphans[1].hops[0].reconcile_status == "not_compared"
    assert orphans[1].hops[0].proceeds is None
    assert orphans[1].hops[0].cost_basis is None
    assert orphans[1].hops[0].replacement_buys == []
    assert "You sold" not in orphans[1].plain_english
    assert "$10.25" not in orphans[1].plain_english

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
    assert len(carry) == 2
    assert all(len(chain.hops) == 1 for chain in carry)
    assert all(chain.hops[0].carried_in_disallowed is None for chain in carry)
    assert all(chain.hops[0].explained_loss_with_carry is None for chain in carry)
    assert all("Hop 2" not in chain.plain_english for chain in carry)
    assert all("prior hop" not in chain.plain_english for chain in carry)
    assert all("tax totals" not in chain.plain_english for chain in carry)
    later_penny = next(chain for chain in carry if chain.hops[0].sale_date == date(2026, 6, 20))
    assert later_penny.hops[0].disallowed_loss == pytest.approx(100)
    assert later_penny.hops[0].engine_sale_loss == pytest.approx(100)

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
    assert scaled[0].hops[0].proceeds is None
    assert scaled[0].hops[0].cost_basis is None
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
    # A zero-quantity same-day row blocks the absence claim. The engine hop
    # is the only chain, and it is not_compared rather than not_on_1099.
    assert len(empty_unit) == 1
    assert empty_unit[0].reconcile_status == "not_compared"
    assert empty_unit[0].hops[0].not_compared_reason == "unknown"
    assert empty_unit[0].hops[0].sale_quantity == pytest.approx(10)
    assert all(chain.reconcile_status != "not_on_1099" for chain in empty_unit)

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
    assert dated[0].hops[0].replacement_buys == []
    assert "moved into" not in dated[0].plain_english

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
    assert window_only[0].hops[0].replacement_buys == []
    assert "moved into the new lot's basis" not in window_only[0].plain_english

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
    # No sell, and the lot's loss does not match the flag, so nothing is compared.
    assert len(priced) == 1
    assert priced[0].reconcile_status == "not_compared"
    assert priced[0].hops[0].not_compared_reason == "unidentified"
    assert priced[0].hops[0].proceeds is None
    assert priced[0].hops[0].cost_basis is None
    assert priced[0].hops[0].form_1099_wash is None
    assert priced[0].hops[0].replacement_buys == []
    assert "You sold" not in priced[0].plain_english
    assert all(chain.reconcile_status != "on_1099_only" for chain in priced)

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
    assert len(no_price) == 1
    assert no_price[0].reconcile_status == "not_compared"
    assert no_price[0].hops[0].not_compared_reason == "unidentified"
    assert no_price[0].hops[0].proceeds is None
    assert no_price[0].hops[0].cost_basis is None
    assert no_price[0].hops[0].replacement_buys == []
    assert "You sold" not in no_price[0].plain_english
    assert all(chain.reconcile_status != "on_1099_only" for chain in no_price)

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
    assert len(carried) == 2
    assert all(len(chain.hops) == 1 for chain in carried)
    assert all(chain.hops[0].carried_in_disallowed is None for chain in carried)
    assert all(chain.hops[0].explained_loss_with_carry is None for chain in carried)
    assert all("prior hop" not in chain.plain_english for chain in carried)
    assert all("Hop 2" not in chain.plain_english for chain in carried)

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


def test_each_sale_keeps_its_own_1099_status():
    """A later sale that differs does not change the earlier sale's match."""
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
    assert len(chains) == 2
    assert all(len(chain.hops) == 1 for chain in chains)
    first = next(chain for chain in chains if chain.hops[0].sale_date == date(2026, 6, 1))
    second = next(chain for chain in chains if chain.hops[0].sale_date == date(2026, 6, 20))
    assert first.reconcile_status == "matches"
    assert first.hops[0].reconcile_status == "matches"
    assert first.difference is None
    assert second.reconcile_status == "differs"
    assert second.hops[0].reconcile_status == "differs"
    assert second.difference == pytest.approx(20)
    assert second.hops[0].difference == pytest.approx(20)
    assert second.plain_english == second.hops[0].plain_english
    assert "wash-sale chain." not in second.plain_english
    assert "Hop 2" not in second.plain_english
    assert second.hops[0].carried_in_disallowed is None
    assert second.chain_id == second.hops[0].hop_id


def test_multi_lot_sale_keeps_both_acquired_dates_without_linking():
    """A 100-share sale of a 40-lot and a 60-lot stays separate from the next sale."""
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
    assert len(chains) == 2
    assert all(len(chain.hops) == 1 for chain in chains)
    first = next(chain.hops[0] for chain in chains if chain.hops[0].sale_date == date(2026, 6, 1))
    second = next(chain.hops[0] for chain in chains if chain.hops[0].sale_date == date(2026, 6, 20))
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
    assert second.hop_index == 0
    assert second.carried_in_disallowed is None
    assert second.explained_loss_with_carry is None
    assert "prior hop" not in second.plain_english
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

    A row belongs when its total proceeds are within CENTS of the sale
    price times its quantity. $66.67 is one cent from $33.33 times 2, so
    both rows are that sale. Leftover rows use the same cent match.
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


def test_jun1_jul15_jul24_replacement_holding_period_stays_empty():
    """Buy 10 on Jun 1, sell 10 on Jul 15 at a loss, rebuy 10 on Jul 24.

    The sold lot's acquisition date is known and is not copied onto the
    replacement as a tacked holding period. Per-replacement tacking is JOR-85.
    """
    txns = [
        _txn("AMD", TransCode.BUY, date(2026, 6, 1), 10, 150),
        _txn("AMD", TransCode.SELL, date(2026, 7, 15), 10, 120),
        _txn("AMD", TransCode.BUY, date(2026, 7, 24), 10, 125),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    _lots, _warnings, events = transactions_to_tax_lots(txns)
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=events,
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    hop = chains[0].hops[0]
    assert hop.sale_date == date(2026, 7, 15)
    assert hop.sale_quantity == pytest.approx(10)
    assert hop.original_purchase_date == date(2026, 6, 1)
    assert hop.replacement_buys[0].activity_date == date(2026, 7, 24)
    assert hop.tacked_acquisition_date is None
    assert hop.carryover_days is None
    assert "Holding period" not in hop.plain_english


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
    assert hop["original_purchase_date"] == "2026-06-01"
    assert hop["engine_adjusted_cost_basis"] == pytest.approx(1550)
    assert hop["form_1099_wash"] == pytest.approx(300)
    assert "open_lot_total_cost_basis" not in hop
    assert "open_lot_wash_sale_disallowed" not in hop
    assert hop["disallowed_loss"] == pytest.approx(300)
    assert hop["proceeds"] == pytest.approx(1200)
    assert hop["cost_basis"] == pytest.approx(1500)
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


def test_later_sale_of_one_replacement_date_stays_its_own_chain():
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
    assert later.carried_in_disallowed is None
    assert later.explained_loss_with_carry is None
    assert "Part of the replacement was resold" not in later.plain_english
    assert "prior hop" not in later.plain_english
    assert [(lot.purchase_date, lot.quantity) for lot in later.closed_lots] == [
        (date(2026, 6, 5), 100),
    ]
    assert first.disallowed_loss == pytest.approx(1000)


def test_three_exact_resales_stay_separate_chains():
    """Aug 10 resells the Jun 5 lot. Each sale stays its own chain."""
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
    assert len(chains) == 3
    assert all(len(chain.hops) == 1 for chain in chains)
    by_date = {chain.hops[0].sale_date: chain.hops[0] for chain in chains}
    assert set(by_date) == {date(2026, 6, 1), date(2026, 8, 10), date(2026, 9, 20)}
    assert by_date[date(2026, 8, 10)].disallowed_loss == pytest.approx(400)
    assert by_date[date(2026, 9, 20)].disallowed_loss == pytest.approx(200)
    for hop in by_date.values():
        assert hop.hop_index == 0
        assert hop.carried_in_disallowed is None
        assert hop.explained_loss_with_carry is None
        assert "prior hop" not in hop.plain_english
        assert "Part of the replacement was resold" not in hop.plain_english
        assert "Hop 2" not in hop.plain_english
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
    assert blank.proceeds is None
    assert blank.cost_basis is None
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
    assert hop.replacement_buys == []
    assert hop.tacked_acquisition_date is None
    assert hop.carryover_days is None


def test_leftover_cent_split_keeps_one_fill_and_splits_wider_prices():
    """Leftover rows merge only as the cent split of one fill.

    $33.33 and $66.67 stay one 3-share sale. One-share fills at $10.00,
    $10.01, and $10.02 stay one chain: each total is within CENTS of the
    $10.00 anchor, and $10.02 sits on that two-cent bound. A 1-share
    $10.00 wash and a 100-share $1000.50 wash do not merge: fifty cents is
    outside CENTS. The old half-cent-per-share slack no longer groups them.
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
    assert len(anchored) == 1
    assert anchored[0].hops[0].sale_quantity == pytest.approx(3)
    assert anchored[0].hops[0].disallowed_loss == pytest.approx(3)
    assert anchored[0].reconcile_status == "on_1099_only"

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
    assert len(middle_first) == 1
    assert middle_first[0].hops[0].sale_quantity == pytest.approx(3)
    assert middle_first[0].hops[0].disallowed_loss == pytest.approx(3)
    assert middle_first[0].hops[0].proceeds == pytest.approx(30.03)

    wide = _build(
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
                quantity=100,
                export_trade_date=date(2026, 6, 1),
                proceeds_1099=1000.50,
                wash_sale_disallowed=40,
            ),
            bucket="unmatched",
        ),
    )
    assert len(wide) == 2
    wide_qty = sorted(chain.hops[0].sale_quantity for chain in wide)
    assert wide_qty == pytest.approx([1, 100])
    assert {chain.hops[0].disallowed_loss for chain in wide} == {1, 40}
    assert {chain.hops[0].proceeds for chain in wide} == {10.0, 1000.50}
    assert all(chain.reconcile_status == "on_1099_only" for chain in wide)
    assert all("You sold 101" not in chain.plain_english for chain in wide)
    assert all(chain.hops[0].disallowed_loss != pytest.approx(41) for chain in wide)

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
    assert blank.reconcile_status == "not_compared"
    assert blank.hops[0].not_compared_reason == "unknown"
    assert blank.hops[0].replacement_buys == []
    assert blank.hops[0].cost_basis is None
    assert blank.hops[0].form_1099_wash == pytest.approx(10)
    priced = next(chain for chain in zeros if chain.hops[0].sale_quantity == pytest.approx(1))
    assert priced.reconcile_status == "on_1099_only"
    assert priced.hops[0].disallowed_loss == pytest.approx(1)


def test_three_washes_stay_separate_with_no_carry():
    """Three $20 washes stay three chains. None inherits a prior disallowance."""
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
    assert len(chains) == 3
    assert all(len(chain.hops) == 1 for chain in chains)
    hops = [chain.hops[0] for chain in chains]
    assert all(hop.carried_in_disallowed is None for hop in hops)
    assert all(hop.explained_loss_with_carry is None for hop in hops)
    assert all(hop.hop_index == 0 for hop in hops)
    assert all("prior hop" not in hop.plain_english for hop in hops)
    assert all("Hop 2" not in hop.plain_english for hop in hops)
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


def test_mixed_fifo_lot_stays_its_own_chain():
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
    assert later.carried_in_disallowed is None
    assert later.explained_loss_with_carry is None
    assert "prior hop" not in later.plain_english
    assert "Part of the replacement was resold" not in later.plain_english
    assert earlier_chain.hops[0].disallowed_loss == pytest.approx(1000)
    assert first.disallowed_loss == pytest.approx(1000)
    assert second.disallowed_loss == pytest.approx(600)


def test_partial_resale_is_its_own_chain_without_carry_copy():
    """Selling 50 of a 100-share replacement is a second chain, with no carry copy."""
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
    assert len(chains) == 2
    assert all(len(chain.hops) == 1 for chain in chains)
    earlier = next(chain.hops[0] for chain in chains if chain.hops[0].sale_date == date(2026, 6, 1))
    later = next(chain.hops[0] for chain in chains if chain.hops[0].sale_date == date(2026, 8, 1))
    assert later.hop_index == 0
    assert later.sale_quantity == pytest.approx(50)
    assert later.carried_in_disallowed is None
    assert later.explained_loss_with_carry is None
    assert earlier.carried_in_disallowed is None
    assert later.disallowed_loss == pytest.approx(250)
    assert "Part of the replacement was resold" not in later.plain_english
    assert "Part of the replacement was resold" not in earlier.plain_english
    assert "prior hop" not in later.plain_english
    assert first.disallowed_loss == pytest.approx(1000)
    assert second.disallowed_loss == pytest.approx(250)


def test_same_day_exact_resales_stay_separate_chains():
    """Two later sales on one day both match the replacement. Neither is linked."""
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
    # Both July losses fit, and the proceeds differ by more than CENTS.
    # Neither sale's date or proceeds is shown.
    assert all(chain.hops[0].not_compared_reason == "unidentified" for chain in july)
    assert all(chain.hops[0].proceeds is None for chain in july)
    assert all(chain.hops[0].cost_basis is None for chain in july)
    assert all(chain.hops[0].original_purchase_date is None for chain in july)
    assert all(chain.hops[0].replacement_buys == [] for chain in july)
    assert all("You sold" not in chain.plain_english for chain in july)
    june = next(chain for chain in chains if chain.hops[0].sale_date == date(2026, 6, 1))
    assert june.hops[0].original_purchase_date == date(2026, 1, 1)
    assert june.hops[0].proceeds == pytest.approx(80)
    assert all(chain.hops[0].carried_in_disallowed is None for chain in chains)
    assert all(chain.hops[0].explained_loss_with_carry is None for chain in chains)
    assert all("prior hop" not in chain.plain_english for chain in chains)
    assert all("Hop 2" not in chain.plain_english for chain in chains)
    assert flags[0].disallowed_loss == pytest.approx(20)


def test_partial_then_later_sale_stay_separate_chains():
    """A partial resale and the sale after it are their own chains."""
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
    assert len(chains) == 3
    assert all(len(chain.hops) == 1 for chain in chains)
    hops = [chain.hops[0] for chain in chains]
    assert [hop.sale_date for hop in hops] == [
        date(2026, 6, 1),
        date(2026, 7, 15),
        date(2026, 8, 20),
    ]
    assert [hop.disallowed_loss for hop in hops] == pytest.approx([1000, 200, 100])
    for hop in hops:
        assert hop.hop_index == 0
        assert hop.carried_in_disallowed is None
        assert hop.explained_loss_with_carry is None
        assert "Part of the replacement was resold" not in hop.plain_english
        assert "prior hop" not in hop.plain_english


def test_shared_acquisition_date_does_not_merge_chains():
    """A shared acquisition date does not merge two wash flags into one chain."""
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
    assert later.hops[0].carried_in_disallowed is None
    assert "prior hop" not in later.plain_english

    separate = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 100, 80, date(2026, 1, 1)),
            _event("AAPL", date(2026, 7, 15), 10, 90, 70, date(2026, 6, 10)),
        ],
    )
    assert len(separate) == 2
    assert all(len(chain.hops) == 1 for chain in separate)
    july = next(chain for chain in separate if chain.hops[0].sale_date == date(2026, 7, 15))
    assert july.hops[0].original_purchase_date == date(2026, 6, 10)
    assert july.hops[0].carried_in_disallowed is None
    assert july.hops[0].explained_loss_with_carry is None
    assert july.hops[0].disallowed_loss == pytest.approx(20)
    assert "prior hop" not in july.plain_english
    assert july.hops[0].tacked_acquisition_date is None
    assert july.hops[0].carryover_days is None


def test_stock_chain_ignores_same_date_option_replacement_and_open_lot():
    """A same-size option close is not compared, and a later stock hop stays stock.

    June 1 is 10 shares and 10 contracts, so the stock sale is not compared.
    That option close is not an option wash flag and has no positive-wash
    option 1099 row, so it is not called an option wash. The July option
    flag is the option entry. The August stock sale still uses the stock
    buy, not the option buy or the option lot.
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
    # June 1 has an option close inside the stock flag's window, so that flag
    # is one quiet line. The August stock sale is outside that window.
    june = next(chain for chain in chains if chain.hops[0].sale_date == date(2026, 6, 1))
    july = next(chain for chain in chains if chain.hops[0].sale_date == date(2026, 7, 1))
    august = next(chain for chain in chains if chain.hops[0].sale_date == date(2026, 8, 10))
    assert june.reconcile_status == "not_compared"
    assert june.plain_english == OPTION_LINE
    assert june.hops[0].proceeds is None
    assert june.hops[0].cost_basis is None
    assert june.hops[0].replacement_buys == []
    assert "$" not in june.plain_english
    assert len(june.hops) == 1
    assert july.plain_english == OPTION_LINE
    assert july.hops[0].proceeds is None
    assert july.hops[0].cost_basis is None
    assert july.hops[0].engine_disallowed_loss is None
    assert july.hops[0].disallowed_loss == pytest.approx(0)
    assert july.hops[0].replacement_buys == []
    assert august.hops[0].asset_type == "stock"
    assert august.hops[0].replacement_buys[0].activity_date == date(2026, 8, 15)
    assert august.hops[0].replacement_buys[0].price == pytest.approx(42)
    assert august.hops[0].carried_in_disallowed is None
    assert august.hops[0].explained_loss_with_carry is None
    assert OPTION_LINE not in august.plain_english
    assert all(len(chain.hops) == 1 for chain in chains)
    assert stock_first.disallowed_loss == pytest.approx(300)
    assert option_flag.disallowed_loss == pytest.approx(6)
    assert option_open.total_cost_basis == pytest.approx(500)
    assert option_open.wash_sale_disallowed == pytest.approx(6)
    assert stock_buy.price == pytest.approx(55)
    assert option_buy.price == pytest.approx(5)


def test_option_lot_skewed_flag_loss_does_not_invent_stock_basis():
    """A same-ticker option buy can skew the flag loss. Do not print that loss.

    Buy 1 AMD option at $5, then 10 AMD shares at $100, sell those shares at
    $90, and rebuy 10 within the window. The wash flag's sale loss is $5
    because the option lot is included in the flag's FIFO. The stock realized
    lot is the real $100 loss. The hop must not show the $5 loss, must not
    build basis as proceeds plus that flag loss, and must not claim the
    holding period is missing.
    """
    sale = date(2026, 6, 1)
    option_buy = _txn("AMD", TransCode.BTO, date(2026, 1, 1), 1, 5, AssetType.OPTION)
    stock_buy = _txn("AMD", TransCode.BUY, date(2026, 1, 2), 10, 100)
    stock_sell = _txn("AMD", TransCode.SELL, sale, 10, 90)
    rebuy = _txn("AMD", TransCode.BUY, date(2026, 6, 15), 10, 95)
    txns = [option_buy, stock_buy, stock_sell, rebuy]
    flags = detect_wash_sales(txns, tax_year=2026)
    assert len(flags) == 1
    assert flags[0].sale_loss == pytest.approx(5)
    assert flags[0].disallowed_loss == pytest.approx(5)
    lots, _warnings, events = transactions_to_tax_lots(txns)
    stock_events = [
        event for event in events if event.asset_type != AssetType.OPTION
    ]
    assert len(stock_events) == 1
    assert stock_events[0].sale_proceeds == pytest.approx(900)
    assert stock_events[0].cost_basis == pytest.approx(1000)
    true_loss = stock_events[0].cost_basis - stock_events[0].sale_proceeds
    assert true_loss == pytest.approx(100)
    fabricated_basis = stock_events[0].sale_proceeds + flags[0].sale_loss
    assert fabricated_basis == pytest.approx(905)

    flags_before = copy.deepcopy(flags)
    events_before = copy.deepcopy(events)
    lots_before = copy.deepcopy(lots)
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=events,
        tax_lots=lots,
    )
    assert flags == flags_before
    assert events == events_before
    assert lots == lots_before
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    hop = chains[0].hops[0]
    assert hop.asset_type == "stock"
    assert hop.not_compared_reason == "unidentified"
    assert hop.reconcile_status == "not_compared"
    assert chains[0].reconcile_status == "not_compared"
    assert hop.proceeds is None
    assert hop.cost_basis is None
    assert hop.cost_basis != pytest.approx(fabricated_basis)
    assert hop.proceeds is None or hop.proceeds == pytest.approx(900)
    assert hop.cost_basis is None or hop.cost_basis == pytest.approx(1000)
    assert hop.loss != pytest.approx(5)
    assert hop.engine_sale_loss != pytest.approx(5)
    assert hop.replacement_buys == []
    assert hop.closed_lots == []
    assert hop.tacked_acquisition_date is None
    assert hop.carryover_days is None
    assert hop.original_holding_days is None
    assert hop.plain_english == (
        "This sale could not be tied to one trade's proceeds and loss, "
        "so it was not compared."
    )
    assert "$5" not in hop.plain_english
    assert "holding period" not in hop.plain_english.lower()
    assert "Holding-period carryover is not in this export." not in hop.plain_english
    assert "You sold" not in hop.plain_english
    assert flags[0].sale_loss == pytest.approx(5)
    assert flags[0].disallowed_loss == pytest.approx(5)
    assert option_buy.price == pytest.approx(5)
    assert stock_buy.price == pytest.approx(100)
    assert stock_sell.price == pytest.approx(90)
    assert rebuy.price == pytest.approx(95)


def test_option_sale_stays_separate_from_a_stock_hop():
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
    # The June stock flag's window includes the May option buy and the July
    # option close, so that sale is quiet too. Three dates, no stock dollars.
    assert len(chains) == 3
    assert all(len(chain.hops) == 1 for chain in chains)
    assert all(chain.plain_english == OPTION_LINE for chain in chains)
    assert all(chain.hops[0].replacement_buys == [] for chain in chains)
    assert all(chain.hops[0].closed_lots == [] for chain in chains)
    assert all(chain.hops[0].proceeds is None for chain in chains)
    assert all(chain.hops[0].cost_basis is None for chain in chains)
    assert all(chain.hops[0].engine_disallowed_loss is None for chain in chains)
    assert all(chain.hops[0].disallowed_loss == pytest.approx(0) for chain in chains)
    assert all("$" not in chain.plain_english for chain in chains)
    assert {chain.hops[0].sale_date for chain in chains} == {
        date(2026, 5, 1),
        date(2026, 6, 1),
        date(2026, 7, 1),
    }
    assert stock.disallowed_loss == pytest.approx(20)
    assert option_may.disallowed_loss == pytest.approx(6)

    # Same-day stock and option lots: an option lot in the window quiets the flag.
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
    assert len(stock_qty) == 1
    assert stock_qty[0].hops[0].plain_english == OPTION_LINE
    assert stock_qty[0].hops[0].proceeds is None
    assert stock_qty[0].hops[0].cost_basis is None
    assert stock_qty[0].hops[0].closed_lots == []
    assert "$" not in stock_qty[0].plain_english

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
    assert len(neither) == 1
    assert neither[0].hops[0].plain_english == OPTION_LINE
    assert neither[0].hops[0].proceeds is None
    assert neither[0].hops[0].cost_basis is None
    assert "$" not in neither[0].plain_english


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
    assert len(chains) == 2
    assert all(len(chain.hops) == 1 for chain in chains)
    assert all(chain.hops[0].reconcile_status == "not_compared" for chain in chains)
    assert all(chain.plain_english == OPTION_LINE for chain in chains)
    assert all(chain.hops[0].sale_date == sale for chain in chains)
    assert all(chain.hops[0].engine_disallowed_loss is None for chain in chains)
    assert all(chain.hops[0].proceeds is None for chain in chains)
    assert all(chain.hops[0].cost_basis is None for chain in chains)
    assert all(chain.hops[0].form_1099_wash is None for chain in chains)
    assert all(chain.hops[0].replacement_buys == [] for chain in chains)
    assert all("$" not in chain.plain_english for chain in chains)
    assert all("did not flag a matching wash" not in chain.plain_english for chain in chains)
    assert all(chain.reconcile_status != "matches" for chain in chains)
    assert all(chain.reconcile_status != "differs" for chain in chains)
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    assert [flag.disallowed_loss for flag in flags] == flag_losses


def test_option_flag_does_not_borrow_same_day_stock_proceeds():
    """An option replacement is one option line. The stock sale's dollars stay off."""
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
    assert len(chains) == 2
    assert all(len(chain.hops) == 1 for chain in chains)
    assert len({chain.chain_id for chain in chains}) == 2
    assert all(chain.hops[0].asset_type == "option" for chain in chains)
    assert all(chain.hops[0].not_compared_reason == "option" for chain in chains)
    assert all(chain.reconcile_status == "not_compared" for chain in chains)
    assert all(chain.plain_english == OPTION_LINE for chain in chains)
    assert all(chain.hops[0].plain_english == OPTION_LINE for chain in chains)
    assert all(chain.hops[0].proceeds is None for chain in chains)
    assert all(chain.hops[0].cost_basis is None for chain in chains)
    assert all(chain.hops[0].sale_date == sale for chain in chains)
    assert all(chain.hops[0].sale_quantity == pytest.approx(1) for chain in chains)
    assert all(chain.hops[0].engine_disallowed_loss is None for chain in chains)
    assert all(chain.hops[0].form_1099_wash is None for chain in chains)
    assert all(chain.hops[0].replacement_buys == [] for chain in chains)
    assert all("$" not in chain.plain_english for chain in chains)
    assert all("400" not in chain.plain_english for chain in chains)
    assert all("not a wash" not in chain.plain_english.lower() for chain in chains)
    assert all("You sold" not in chain.plain_english for chain in chains)
    assert first.disallowed_loss == pytest.approx(6)
    assert second.disallowed_loss == pytest.approx(6)


def test_overlapping_candidate_row_is_not_compared():
    """A wrong-size row in both candidate lists does not tie the sales.

    The 25-share row matches the $10 price but fits neither the 100-share
    subset nor the 50-share subset. It stays its own 1099 chain. The two
    sales keep their own rows.
    """
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
    assert leftover[0].reconcile_status == "on_1099_only"
    assert leftover[0].hops[0].form_1099_wash == pytest.approx(5)
    assert leftover[0].hops[0].not_compared_reason != "unassigned"
    assert leftover[0].hops[0].not_compared_reason != "ambiguous"
    sales = [chain for chain in chains if chain.hops[0].sale_quantity != pytest.approx(25)]
    assert len(sales) == 2
    assert {chain.hops[0].sale_quantity for chain in sales} == {100, 50}
    hundred = next(chain for chain in sales if chain.hops[0].sale_quantity == pytest.approx(100))
    fifty = next(chain for chain in sales if chain.hops[0].sale_quantity == pytest.approx(50))
    assert hundred.reconcile_status == "differs"
    assert hundred.hops[0].form_1099_wash == pytest.approx(10)
    assert fifty.reconcile_status == "differs"
    assert fifty.hops[0].form_1099_wash == pytest.approx(10)
    assert all(chain.hops[0].not_compared_reason != "ambiguous" for chain in sales)


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


def test_two_washes_and_a_later_partial_stay_separate_chains():
    """One 20-share resale that fits two washes does not join either of them."""
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
    assert len(chains) == 3
    assert all(len(chain.hops) == 1 for chain in chains)
    later = next(chain for chain in chains if chain.hops[0].sale_date == date(2026, 8, 1))
    assert "Part of the replacement was resold" not in later.plain_english
    assert later.hops[0].carried_in_disallowed is None
    assert later.hops[0].explained_loss_with_carry is None
    assert all(chain.hops[0].hop_index == 0 for chain in chains)


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


def _same_price_tie_inputs(sale: date):
    """Two identified 10-share sales that share one 1099 row, plus a 3-share row.

    One sell is already consumed by the first flag. The second flag still
    attaches by its realized lot, at the same $10 price. Both quantity
    subsets contain the 10-share row. The 3-share row matches the price
    and fits neither subset.
    """
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 6, 6),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=130,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=30,
            repurchase_date=date(2026, 6, 6),
            repurchase_quantity=10,
            disallowed_loss=30,
            adjusted_cost_basis=140,
        ),
    ]
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 20, 13),
        _txn("AAPL", TransCode.SELL, sale, 10, 10),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 6), 10, 11),
    ]
    events = [
        _event("AAPL", sale, 10, 120, 100, date(2026, 1, 1)),
        _event("AAPL", sale, 10, 130, 100, date(2026, 1, 1)),
    ]
    rows = _report(
        _row(symbol="AAPL", quantity=10, export_trade_date=sale, proceeds_1099=100, wash_sale_disallowed=20),
        _row(symbol="AAPL", quantity=3, export_trade_date=sale, proceeds_1099=30, wash_sale_disallowed=5),
    )
    return flags, txns, events, rows


def test_shared_quantity_subset_refuses_both_sales():
    """Sales that share a quantity-valid row are not compared.

    The 3-share row is only a price candidate. It stays its own
    not-compared entry instead of vanishing or joining either sale.
    """
    sale = date(2026, 6, 1)
    flags, txns, events, rows = _same_price_tie_inputs(sale)
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=events,
        supplemental=_supplemental(2026),
        lot_match_report=rows,
    )
    sales = [chain for chain in chains if chain.hops[0].sale_quantity == pytest.approx(10)]
    extra = [chain for chain in chains if chain.hops[0].sale_quantity == pytest.approx(3)]
    assert len(sales) == 2
    assert all(chain.reconcile_status == "not_compared" for chain in sales)
    assert all(chain.hops[0].not_compared_reason == "ambiguous" for chain in sales)
    assert all(chain.hops[0].form_1099_wash is None for chain in sales)
    assert all("no wash total was chosen" in chain.plain_english for chain in sales)
    assert len(extra) == 1
    assert extra[0].hops[0].not_compared_reason == "unassigned"
    assert extra[0].hops[0].form_1099_wash == pytest.approx(5)
    assert "overlapped more than one possible sale" in extra[0].plain_english


def test_builder_skips_an_unassigned_row_the_helper_refuses(monkeypatch):
    """A reserved overlap row the helper will not show does not become a chain."""
    import wash_sale_chain

    monkeypatch.setattr(wash_sale_chain, "_unassigned_from_row", lambda _row, _index: None)
    sale = date(2026, 6, 1)
    flags, txns, events, rows = _same_price_tie_inputs(sale)
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=events,
        supplemental=_supplemental(2026),
        lot_match_report=rows,
    )
    assert len(chains) == 2
    assert all(chain.hops[0].sale_quantity != pytest.approx(3) for chain in chains)
    assert all(chain.reconcile_status == "not_compared" for chain in chains)
    assert all(chain.hops[0].not_compared_reason == "ambiguous" for chain in chains)


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
    assert option[0].hops[0].proceeds is None
    assert option[0].hops[0].cost_basis is None
    assert flag.disallowed_loss == pytest.approx(200)


def test_zero_wash_option_1099_row_does_not_differ_from_the_stock_wash():
    """A $0-wash option 1099 row is not the stock hop's form 1099 wash.

    10 AMD shares, flag loss $100, replacement the next week. There is no
    option close of quantity 10. The only same-day 1099 row is an AMD call
    with a $0 wash column.
    """
    sale = date(2026, 6, 1)
    flag = WashSaleFlag(
        symbol="AMD",
        sale_date=sale,
        sale_quantity=10,
        sale_loss=100,
        repurchase_date=date(2026, 6, 8),
        repurchase_quantity=10,
        disallowed_loss=100,
        adjusted_cost_basis=550,
    )
    chains = _build(
        transactions=[
            _txn("AMD", TransCode.BUY, date(2026, 1, 1), 10, 50),
            _txn("AMD", TransCode.SELL, sale, 10, 40),
            _txn("AMD", TransCode.BUY, date(2026, 6, 8), 10, 45),
        ],
        wash_sale_flags=[flag],
        realized_events=[
            _event("AMD", sale, 10, 500, 400, date(2026, 1, 1)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AMD",
                description="AMD Call",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=400,
                cost_basis_1099=500,
                wash_sale_disallowed=0,
            )
        ),
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.asset_type == "stock"
    assert hop.sale_quantity == pytest.approx(10)
    assert chains[0].reconcile_status == "not_on_1099"
    assert hop.reconcile_status == "not_on_1099"
    assert chains[0].reconcile_status != "differs"
    assert hop.form_1099_wash is None
    assert chains[0].difference is None
    assert "differs from your 1099" not in hop.plain_english
    assert "by $100" not in hop.plain_english
    assert OPTION_LINE not in hop.plain_english
    assert all(chain.hops[0].asset_type != "option" for chain in chains)
    assert flag.disallowed_loss == pytest.approx(100)
    assert flag.sale_loss == pytest.approx(100)


def test_zero_wash_option_row_leaves_a_unique_stock_match():
    """The $0-wash AMD call row does not make the stock 1099 match ambiguous."""
    sale = date(2026, 6, 1)
    flag = WashSaleFlag(
        symbol="AMD",
        sale_date=sale,
        sale_quantity=10,
        sale_loss=100,
        repurchase_date=date(2026, 6, 8),
        repurchase_quantity=10,
        disallowed_loss=100,
        adjusted_cost_basis=550,
    )
    chains = _build(
        transactions=[
            _txn("AMD", TransCode.BUY, date(2026, 1, 1), 10, 50),
            _txn("AMD", TransCode.SELL, sale, 10, 40),
            _txn("AMD", TransCode.BUY, date(2026, 6, 8), 10, 45),
        ],
        wash_sale_flags=[flag],
        realized_events=[
            _event("AMD", sale, 10, 500, 400, date(2026, 1, 1)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AMD",
                description="AMD",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=400,
                cost_basis_1099=500,
                wash_sale_disallowed=100,
            ),
            _row(
                symbol="AMD",
                description="AMD Call",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=400,
                cost_basis_1099=400,
                wash_sale_disallowed=0,
            ),
        ),
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert chains[0].reconcile_status == "matches"
    assert hop.reconcile_status == "matches"
    assert hop.asset_type == "stock"
    assert hop.form_1099_wash == pytest.approx(100)
    assert hop.not_compared_reason != "ambiguous"
    assert "no wash total was chosen" not in hop.plain_english
    assert "differs from your 1099" not in hop.plain_english
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    assert all(chain.hops[0].asset_type != "option" for chain in chains)
    assert flag.disallowed_loss == pytest.approx(100)


def test_stock_and_option_same_quantity_are_not_compared():
    """A profitable same-size option close is not a wash and not a missed 1099 wash.

    10 AMD sold Jun 1 at a $100 loss and rebought Jun 10. An option STC of
    10 the same day is a gain. The 1099 row is 10 shares, proceeds $400,
    basis $500, wash $100.
    """
    sale = date(2026, 6, 1)
    flag = WashSaleFlag(
        symbol="AMD",
        sale_date=sale,
        sale_quantity=10,
        sale_loss=100,
        repurchase_date=date(2026, 6, 10),
        repurchase_quantity=10,
        disallowed_loss=100,
        adjusted_cost_basis=500,
    )
    option_open = _txn(
        "AMD", TransCode.BTO, date(2026, 1, 1), 10, 1, AssetType.OPTION
    )
    option_close = _txn(
        "AMD", TransCode.STC, sale, 10, 2, AssetType.OPTION
    )
    chains = _build(
        transactions=[
            option_open,
            _txn("AMD", TransCode.BUY, date(2026, 1, 15), 10, 50),
            _txn("AMD", TransCode.SELL, sale, 10, 40),
            option_close,
            _txn("AMD", TransCode.BUY, date(2026, 6, 10), 10, 40),
        ],
        wash_sale_flags=[flag],
        realized_events=[
            _event("AMD", sale, 10, 500, 400, date(2026, 1, 15)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AMD",
                description="AMD",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=400,
                cost_basis_1099=500,
                wash_sale_disallowed=100,
            )
        ),
    )
    assert option_close.price > option_open.price
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.not_compared_reason == "option"
    assert hop.reconcile_status == "not_compared"
    assert hop.plain_english == OPTION_LINE
    assert hop.proceeds is None
    assert hop.cost_basis is None
    assert hop.form_1099_wash is None
    assert hop.replacement_buys == []
    assert hop.engine_disallowed_loss is None
    assert hop.disallowed_loss == pytest.approx(0)
    assert "$" not in hop.plain_english
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    assert all("did not flag a matching wash" not in chain.plain_english for chain in chains)
    assert all("Your 1099 reports" not in chain.plain_english for chain in chains)
    assert "not a wash" not in hop.plain_english.lower()
    assert "You sold" not in hop.plain_english
    assert flag.disallowed_loss == pytest.approx(100)
    assert flag.sale_loss == pytest.approx(100)


def test_overlap_reserves_only_the_matching_1099_quantity():
    """An overlap hop does not consume a different same-day sale's 1099 row.

    Flag A is 10 AMD on Jun 1, disallowed $100, with an option STC of 10.
    Flag B is 4 AMD the same day, disallowed $40, and has no option close of 4.
    """
    sale = date(2026, 6, 1)
    flag_a = WashSaleFlag(
        symbol="AMD",
        sale_date=sale,
        sale_quantity=10,
        sale_loss=100,
        repurchase_date=date(2026, 6, 10),
        repurchase_quantity=10,
        disallowed_loss=100,
        adjusted_cost_basis=500,
    )
    flag_b = WashSaleFlag(
        symbol="AMD",
        sale_date=sale,
        sale_quantity=4,
        sale_loss=40,
        repurchase_date=date(2026, 6, 8),
        repurchase_quantity=4,
        disallowed_loss=40,
        adjusted_cost_basis=220,
    )
    chains = _build(
        transactions=[
            _txn("AMD", TransCode.BTO, date(2026, 1, 1), 10, 1, AssetType.OPTION),
            _txn("AMD", TransCode.BUY, date(2026, 1, 15), 10, 50),
            _txn("AMD", TransCode.BUY, date(2026, 1, 16), 4, 50),
            _txn("AMD", TransCode.SELL, sale, 10, 40),
            _txn("AMD", TransCode.SELL, sale, 4, 40),
            _txn("AMD", TransCode.STC, sale, 10, 2, AssetType.OPTION),
            _txn("AMD", TransCode.BUY, date(2026, 6, 10), 10, 40),
            _txn("AMD", TransCode.BUY, date(2026, 6, 8), 4, 45),
        ],
        wash_sale_flags=[flag_a, flag_b],
        realized_events=[
            _event("AMD", sale, 10, 500, 400, date(2026, 1, 15)),
            _event("AMD", sale, 4, 200, 160, date(2026, 1, 16)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AMD",
                description="AMD",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=400,
                cost_basis_1099=500,
                wash_sale_disallowed=100,
            ),
            _row(
                symbol="AMD",
                description="AMD",
                quantity=4,
                export_trade_date=sale,
                proceeds_1099=160,
                cost_basis_1099=200,
                wash_sale_disallowed=40,
            ),
        ),
    )
    # The option close is inside both flags' windows, so each sale is its
    # own quiet line and neither 1099 row is a missed-wash sibling.
    assert len(chains) == 2
    assert all(len(chain.hops) == 1 for chain in chains)
    assert all(chain.hops[0].not_compared_reason == "option" for chain in chains)
    assert all(chain.plain_english == OPTION_LINE for chain in chains)
    assert all(chain.hops[0].proceeds is None for chain in chains)
    assert all(chain.hops[0].cost_basis is None for chain in chains)
    assert all(chain.hops[0].form_1099_wash is None for chain in chains)
    assert all(chain.hops[0].replacement_buys == [] for chain in chains)
    assert all("$" not in chain.plain_english for chain in chains)
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    assert all(chain.reconcile_status != "matches" for chain in chains)
    assert all("did not flag a matching wash" not in chain.plain_english for chain in chains)
    assert flag_a.disallowed_loss == pytest.approx(100)
    assert flag_b.disallowed_loss == pytest.approx(40)


def test_reserve_same_day_rows_uses_subset_sum_within_cents():
    """Subset-sum holds back the sale. Other symbols, days, and prices stay."""
    from wash_sale_chain import _reserve_same_day_1099_rows

    sale = date(2026, 6, 1)
    rows = [
        _row(
            symbol="AMD",
            quantity=10,
            export_trade_date=sale,
            proceeds_1099=400,
            wash_sale_disallowed=100,
        ),
        _row(
            symbol="AMD",
            quantity=4,
            export_trade_date=sale,
            proceeds_1099=160,
            wash_sale_disallowed=40,
        ),
        _row(
            symbol="IBM",
            quantity=10,
            export_trade_date=sale,
            proceeds_1099=400,
            wash_sale_disallowed=1,
        ),
        _row(
            symbol="AMD",
            quantity=10,
            export_trade_date=date(2026, 6, 2),
            proceeds_1099=400,
            wash_sale_disallowed=1,
        ),
        _row(
            symbol="AMD",
            quantity=0,
            export_trade_date=sale,
            proceeds_1099=0,
            wash_sale_disallowed=1,
        ),
    ]
    used: set[int] = set()
    _reserve_same_day_1099_rows(
        rows, used, symbol="AMD", sale_date=sale, quantity=10, unit_target=40
    )
    # The 10-share row is an exact subset. The 4-share row is not.
    assert used == {0}
    _reserve_same_day_1099_rows(
        rows, used, symbol="AMD", sale_date=sale, quantity=10, unit_target=40
    )
    # The 10-share row is already held, so nothing left sums to 10. The
    # leftover same-day candidate is held too. Another symbol, another
    # day, and a zero quantity stay free.
    assert used == {0, 1}

    split = [
        _row(
            symbol="AMD",
            quantity=40,
            export_trade_date=sale,
            proceeds_1099=1600,
            wash_sale_disallowed=400,
        ),
        _row(
            symbol="AMD",
            quantity=60,
            export_trade_date=sale,
            proceeds_1099=2400,
            wash_sale_disallowed=600,
        ),
    ]
    used_split: set[int] = set()
    _reserve_same_day_1099_rows(
        split, used_split, symbol="AMD", sale_date=sale, quantity=100, unit_target=40
    )
    assert used_split == {0, 1}

    short = [
        _row(
            symbol="AMD",
            quantity=3,
            export_trade_date=sale,
            proceeds_1099=120,
            wash_sale_disallowed=30,
        ),
        _row(
            symbol="AMD",
            quantity=4,
            export_trade_date=sale,
            proceeds_1099=160,
            wash_sale_disallowed=40,
        ),
    ]
    used_short: set[int] = set()
    _reserve_same_day_1099_rows(
        short, used_short, symbol="AMD", sale_date=sale, quantity=10, unit_target=40
    )
    assert used_short == {0, 1}

    many = [
        _row(
            symbol="AMD",
            quantity=1,
            export_trade_date=sale,
            proceeds_1099=40,
            wash_sale_disallowed=1,
        )
        for _index in range(13)
    ]
    used_many: set[int] = set()
    _reserve_same_day_1099_rows(
        many, used_many, symbol="AMD", sale_date=sale, quantity=1, unit_target=40
    )
    assert used_many == set(range(13))

    far = [
        _row(
            symbol="AMD",
            quantity=10,
            export_trade_date=sale,
            proceeds_1099=400,
            wash_sale_disallowed=100,
        ),
        _row(
            symbol="AMD",
            quantity=10,
            export_trade_date=sale,
            proceeds_1099=900,
            wash_sale_disallowed=50,
        ),
    ]
    used_far: set[int] = set()
    _reserve_same_day_1099_rows(
        far, used_far, symbol="AMD", sale_date=sale, quantity=10, unit_target=40
    )
    assert used_far == {0}


def test_option_replacement_beside_a_stock_buy_keeps_one_option_hop():
    """A stock buy on the repurchase date does not drop the option wash flag."""
    sale = date(2026, 6, 1)
    repurchase = date(2026, 6, 8)
    flag = WashSaleFlag(
        symbol="AMD",
        sale_date=sale,
        sale_quantity=10,
        sale_loss=600,
        repurchase_date=repurchase,
        repurchase_quantity=10,
        disallowed_loss=600,
        adjusted_cost_basis=1000,
    )
    option_open = _txn("AMD", TransCode.BTO, date(2026, 1, 1), 10, 2, AssetType.OPTION)
    option_close = _txn("AMD", TransCode.STC, sale, 10, 1, AssetType.OPTION)
    chains = _build(
        transactions=[
            option_open,
            _txn("AMD", TransCode.BUY, date(2026, 1, 15), 10, 50),
            _txn("AMD", TransCode.SELL, sale, 10, 40),
            option_close,
            _txn("IBM", TransCode.BTO, repurchase, 10, 1, AssetType.OPTION),
            _txn("AMD", TransCode.BTO, date(2026, 6, 9), 10, 1, AssetType.OPTION),
            _txn("AMD", TransCode.BTO, repurchase, 1, 1, AssetType.OPTION),
            _txn("AMD", TransCode.BUY, repurchase, 10, 45),
            _txn("AMD", TransCode.BTO, repurchase, 10, 1.5, AssetType.OPTION),
        ],
        wash_sale_flags=[flag],
        realized_events=[
            _event("AMD", sale, 10, 500, 400, date(2026, 1, 15)),
        ],
    )
    assert option_close.price < option_open.price
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.asset_type == "option"
    assert hop.not_compared_reason == "option"
    assert hop.reconcile_status == "not_compared"
    assert chains[0].plain_english == OPTION_LINE
    assert hop.plain_english == OPTION_LINE
    assert hop.proceeds is None
    assert hop.cost_basis is None
    assert hop.form_1099_wash is None
    assert hop.replacement_buys == []
    assert hop.closed_lots == []
    assert hop.engine_disallowed_loss is None
    assert hop.disallowed_loss == pytest.approx(0)
    assert "$" not in hop.plain_english
    assert "not a wash" not in hop.plain_english.lower()
    assert "neither was compared" not in hop.plain_english
    assert "You sold" not in hop.plain_english
    assert flag.disallowed_loss == pytest.approx(600)
    assert flag.sale_loss == pytest.approx(600)


def test_split_buy_to_close_is_one_option_hop_without_dollars():
    """Two option lots closed by a buy-to-close are not a stock dollar story."""
    sale = date(2026, 6, 1)
    flag = WashSaleFlag(
        symbol="AMD",
        sale_date=sale,
        sale_quantity=2,
        sale_loss=200,
        repurchase_date=date(2026, 6, 8),
        repurchase_quantity=2,
        disallowed_loss=200,
        adjusted_cost_basis=400,
    )
    lot_a = _event("AMD", sale, 1, 50, 150, date(2026, 1, 1))
    lot_a.asset_type = AssetType.OPTION
    lot_a.pnl = round(50 - 150, 2)
    lot_b = _event("AMD", sale, 1, 50, 150, date(2026, 2, 1))
    lot_b.asset_type = AssetType.OPTION
    lot_b.pnl = round(50 - 150, 2)
    other_stock = _event("AMD", sale, 5, 200, 150, date(2026, 1, 2))
    chains = _build(
        transactions=[
            _txn("AMD", TransCode.STO, date(2026, 1, 1), 1, 0.50, AssetType.OPTION),
            _txn("AMD", TransCode.STO, date(2026, 2, 1), 1, 0.50, AssetType.OPTION),
            _txn("AMD", TransCode.BTC, sale, 0, 1.50, AssetType.OPTION),
            _txn("AMD", TransCode.BTC, sale, 1, 1.50, AssetType.OPTION),
            _txn("AMD", TransCode.BTC, sale, 1, 1.50, AssetType.OPTION),
            _txn("AMD", TransCode.STO, date(2026, 6, 8), 2, 1.20, AssetType.OPTION),
        ],
        wash_sale_flags=[flag],
        realized_events=[lot_a, lot_b, other_stock],
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.asset_type == "option"
    assert hop.not_compared_reason == "option"
    assert hop.reconcile_status == "not_compared"
    assert hop.plain_english == OPTION_LINE
    assert chains[0].plain_english == OPTION_LINE
    assert hop.proceeds is None
    assert hop.cost_basis is None
    assert hop.form_1099_wash is None
    assert hop.replacement_buys == []
    assert hop.closed_lots == []
    assert hop.engine_disallowed_loss is None
    assert hop.disallowed_loss == pytest.approx(0)
    assert "$" not in hop.plain_english
    assert "not a wash" not in hop.plain_english.lower()
    assert "You sold" not in hop.plain_english
    assert "neither was compared" not in hop.plain_english
    assert flag.disallowed_loss == pytest.approx(200)
    assert flag.sale_loss == pytest.approx(200)
    assert lot_a.pnl == pytest.approx(-100)
    assert lot_b.pnl == pytest.approx(-100)

    exact = _build(
        transactions=[
            _txn("AMD", TransCode.BTC, sale, 2, 1.50, AssetType.OPTION),
        ],
        wash_sale_flags=[flag],
    )
    assert len(exact) == 1
    assert exact[0].hops[0].asset_type == "option"
    assert exact[0].hops[0].not_compared_reason == "option"
    assert exact[0].plain_english == OPTION_LINE
    assert exact[0].hops[0].proceeds is None
    assert exact[0].hops[0].cost_basis is None
    assert "$" not in exact[0].plain_english


def test_each_overlapping_stock_flag_keeps_a_hop():
    """Two same-size stock sells each keep a hop. The option close is not a wash."""
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
    assert len(chains) == 2
    assert all(len(chain.hops) == 1 for chain in chains)
    assert all(chain.hops[0].not_compared_reason == "option" for chain in chains)
    assert all(chain.plain_english == OPTION_LINE for chain in chains)
    assert all(chain.hops[0].proceeds is None for chain in chains)
    assert all(chain.hops[0].cost_basis is None for chain in chains)
    assert all(chain.hops[0].replacement_buys == [] for chain in chains)
    assert {chain.hops[0].hop_id[-2:] for chain in chains} == {"f0", "f1"}
    assert all("$" not in chain.plain_english for chain in chains)
    assert [flag.disallowed_loss for flag in flags] == pytest.approx([300, 200])


def test_zero_quantity_row_does_not_match_a_sale_price():
    from wash_sale_chain import _row_total_matches_price

    blank = _row(
        quantity=0,
        proceeds_1099=0,
        export_trade_date=date(2026, 6, 1),
    )
    assert _row_total_matches_price(blank, 10.0) is False


def test_partial_1099_parse_does_not_claim_a_missing_wash():
    """A partial parse can match a row it found, and must not call another sale absent."""
    june = date(2026, 6, 1)
    july = date(2026, 7, 15)
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=june,
            sale_quantity=10,
            sale_loss=200,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=200,
            adjusted_cost_basis=1100,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=july,
            sale_quantity=10,
            sale_loss=200,
            repurchase_date=date(2026, 7, 20),
            repurchase_quantity=10,
            disallowed_loss=200,
            adjusted_cost_basis=1000,
        ),
    ]
    report = _report(
        _row(
            symbol="AAPL",
            quantity=10,
            export_trade_date=june,
            proceeds_1099=800,
            cost_basis_1099=1000,
            wash_sale_disallowed=200,
        )
    )
    report.totals_ok = False
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 100),
            _txn("AAPL", TransCode.SELL, june, 10, 80),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 90),
            _txn("AAPL", TransCode.SELL, july, 10, 70),
            _txn("AAPL", TransCode.BUY, date(2026, 7, 20), 10, 80),
        ],
        wash_sale_flags=flags,
        supplemental=_supplemental(2026),
        lot_match_report=report,
    )
    assert len(chains) == 2
    found = next(chain for chain in chains if chain.hops[0].sale_date == june)
    missing = next(chain for chain in chains if chain.hops[0].sale_date == july)
    assert found.reconcile_status == "matches"
    assert found.hops[0].form_1099_wash == pytest.approx(200)
    assert missing.reconcile_status == "not_compared"
    assert missing.reconcile_status != "not_on_1099"
    assert missing.hops[0].not_compared_reason == "no_1099"
    assert missing.hops[0].form_1099_wash is None
    assert "not on your 1099" not in missing.plain_english
    assert "No same-year 1099-B was compared" in missing.plain_english


def test_fractional_share_1099_rows_group_on_total_dollars():
    """0.03 at $1.00 and 0.07 at $2.33 are one 0.1-share sale at $33.33."""
    sale = date(2026, 6, 1)
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=sale,
        sale_quantity=0.1,
        sale_loss=1.67,
        repurchase_date=date(2026, 6, 8),
        repurchase_quantity=0.1,
        disallowed_loss=1.67,
        adjusted_cost_basis=5.07,
    )
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 0.1, 50),
            _txn("AAPL", TransCode.SELL, sale, 0.1, 33.33),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 8), 0.1, 34),
        ],
        wash_sale_flags=[flag],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AAPL",
                quantity=0.03,
                export_trade_date=sale,
                proceeds_1099=1.00,
                cost_basis_1099=1.50,
                wash_sale_disallowed=1.00,
            ),
            _row(
                symbol="AAPL",
                quantity=0.07,
                export_trade_date=sale,
                proceeds_1099=2.33,
                cost_basis_1099=3.50,
                wash_sale_disallowed=0.50,
            ),
        ),
    )
    assert len(chains) == 1
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    hop = chains[0].hops[0]
    assert hop.sale_quantity == pytest.approx(0.1)
    assert hop.reconcile_status == "differs"
    assert hop.form_1099_wash == pytest.approx(1.50)
    assert hop.difference == pytest.approx(0.17)
    assert hop.proceeds == pytest.approx(3.33)
    assert flag.disallowed_loss == pytest.approx(1.67)


def test_same_day_different_prices_leave_proceeds_unset():
    """Two same-size sells are unidentified until a lot set picks one. Never $0."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 20, 100),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 70),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 10, 90),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 8), 10, 95),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    flag_losses = [flag.disallowed_loss for flag in flags]
    assert len(flags) == 2
    quiet = _build(transactions=txns, wash_sale_flags=flags)
    assert len(quiet) == 2
    assert all(len(chain.hops) == 1 for chain in quiet)
    for chain in quiet:
        hop = chain.hops[0]
        assert hop.proceeds is None
        assert hop.cost_basis is None
        assert hop.replacement_buys == []
        assert hop.not_compared_reason == "unidentified"
        assert hop.reconcile_status == "not_compared"
        assert "illustrative" not in hop.plain_english
        assert "You sold" not in hop.plain_english
        assert hop.plain_english == (
            "This sale could not be tied to one trade's proceeds and loss, "
            "so it was not compared."
        )
    attached = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 1), 10, 1000, 700, date(2026, 1, 1)),
            _event("AAPL", date(2026, 6, 1), 10, 1000, 900, date(2026, 1, 1)),
        ],
    )
    assert len(attached) == 2
    proceeds = sorted(chain.hops[0].proceeds for chain in attached)
    assert proceeds == pytest.approx([700, 900])
    assert all(chain.hops[0].proceeds != pytest.approx(0) for chain in attached)
    assert all(chain.hops[0].not_compared_reason != "unidentified" for chain in attached)
    assert all(chain.hops[0].replacement_buys for chain in attached)
    assert [flag.disallowed_loss for flag in flags] == flag_losses


def test_unmatched_row_without_export_date_matches_the_flag_once():
    """A 1099 sold date matches the flag when the export trade date is missing.

    The row is consumed, so it is not also an on_1099_only chain. A csv_only
    row with the same size is export-only and does not match.
    """
    sale = date(2026, 6, 1)
    price = 15.0
    quantity = 100
    disallowed = 250.0
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=sale,
        sale_quantity=quantity,
        sale_loss=500,
        repurchase_date=date(2026, 6, 8),
        repurchase_quantity=quantity,
        disallowed_loss=disallowed,
        adjusted_cost_basis=2000,
    )
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), quantity, 20),
        _txn("AAPL", TransCode.SELL, sale, quantity, price),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 8), quantity, 16),
    ]
    events = [
        _event(
            "AAPL",
            sale,
            quantity,
            price * quantity + 500,
            price * quantity,
            date(2026, 1, 1),
        )
    ]
    row = _row(
        status="1099_only",
        symbol="AAPL",
        quantity=quantity,
        export_trade_date=None,
        date_sold_1099=sale,
        proceeds_1099=price * quantity,
        wash_sale_disallowed=disallowed,
    )
    chains = _build(
        transactions=txns,
        wash_sale_flags=[flag],
        realized_events=events,
        supplemental=_supplemental(2026),
        lot_match_report=_report(row, bucket="unmatched"),
    )
    assert len(chains) == 1
    assert chains[0].reconcile_status == "matches"
    assert chains[0].hops[0].form_1099_wash == pytest.approx(disallowed)
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)

    csv_only = _build(
        transactions=txns,
        wash_sale_flags=[flag],
        realized_events=events,
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="csv_only",
                symbol="AAPL",
                quantity=quantity,
                export_trade_date=sale,
                date_sold_1099=None,
                proceeds_1099=price * quantity,
                wash_sale_disallowed=0,
            ),
            bucket="unmatched",
        ),
    )
    assert len(csv_only) == 1
    assert csv_only[0].reconcile_status == "not_on_1099"
    assert csv_only[0].hops[0].form_1099_wash is None


def test_buys_before_the_sale_are_not_called_back():
    """A buy before the sale is not "back". The later buy is."""
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 20, 100),
        _txn("AAPL", TransCode.BUY, date(2026, 5, 20), 10, 90),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 1), 20, 80),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 5), 10, 85),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    chains = _build(transactions=txns, wash_sale_flags=flags)
    assert len(chains) == 1
    assert chains[0].plain_english == (
        "You bought 10 AAPL on May 20, sold 20 at a $400 loss on Jun 1, "
        "and bought 10 back on Jun 5, so $400 moved into the new lot's basis. "
        "No same-year 1099-B was compared for this sale. "
        "The per-buy basis split is illustrative. "
        "The engine's adjusted basis is the source of truth."
    )


def test_two_same_day_sells_within_cents_stay_unresolved():
    """Two same-size sells that both fit the lot within CENTS are not chosen."""
    from lot_matcher import CENTS

    sale = date(2026, 6, 1)
    near = 10.001
    assert abs(10 * near - 100.0) <= CENTS
    assert abs(10 * 10.0 - 100.0) <= CENTS
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 20, 12),
        _txn("AAPL", TransCode.SELL, sale, 10, 10.0),
        _txn("AAPL", TransCode.SELL, sale, 10, near),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 11),
    ]
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=130,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=130,
        ),
    ]
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", sale, 10, 120, 100.0, date(2026, 1, 1)),
        ],
    )
    assert len(chains) == 2
    assert all(len(chain.hops) == 1 for chain in chains)
    for chain in chains:
        hop = chain.hops[0]
        assert hop.not_compared_reason == "unidentified"
        assert hop.reconcile_status == "not_compared"
        assert hop.proceeds is None
        assert hop.cost_basis is None
        assert hop.replacement_buys == []
        assert hop.plain_english == (
            "This sale could not be tied to one trade's proceeds and loss, "
            "so it was not compared."
        )


def test_zero_or_missing_1099_quantity_is_not_compared():
    """A 1099 lot with no share count does not allocate buys or invent basis."""
    sale = date(2026, 6, 1)
    window_buy = _txn("IBM", TransCode.BUY, date(2026, 6, 5), 10, 12)

    def _unknown_row(**overrides) -> LotMatchRow:
        payload = dict(
            status="1099_only",
            symbol="IBM",
            description="",
            quantity=0,
            date_sold_1099=sale,
            export_trade_date=sale,
            proceeds_1099=100.0,
            proceeds_export=0.0,
            cost_basis_1099=180.0,
            cost_basis_export=0.0,
            wash_sale_disallowed=25.0,
        )
        payload.update(overrides)
        return LotMatchRow.model_construct(**payload)

    for row in (_unknown_row(quantity=0), _unknown_row(quantity=None)):
        chains = _build(
            transactions=[window_buy],
            supplemental=_supplemental(2026),
            lot_match_report=_report(row, bucket="unmatched"),
            tax_lots=[_lot("IBM", 10, 12, date(2026, 6, 5), wash=25)],
        )
        assert len(chains) == 1
        hop = chains[0].hops[0]
        assert chains[0].reconcile_status == "not_compared"
        assert hop.not_compared_reason == "unknown"
        assert hop.replacement_buys == []
        assert hop.cost_basis is None
        assert hop.proceeds is None
        assert hop.engine_adjusted_cost_basis is None
        assert hop.form_1099_wash == pytest.approx(25)
        assert hop.plain_english == (
            "This 1099 sale has no quantity, so it was not compared."
        )
        assert "You sold" not in hop.plain_english
        assert all(buy.explained_basis == 0 for buy in hop.replacement_buys)


def test_unknown_quantity_same_day_1099_blocks_absence_claim():
    """A totals-valid same-day row with no quantity is not an absence claim.

    The engine hop becomes one not_compared hop (unknown quantity). The 1099
    row is not also a leftover hop, and nothing is marked not_on_1099.
    """
    sale = date(2026, 6, 1)
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=sale,
        sale_quantity=10,
        sale_loss=100,
        repurchase_date=date(2026, 6, 5),
        repurchase_quantity=10,
        disallowed_loss=100,
        adjusted_cost_basis=220,
    )
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 10, 20),
        _txn("AAPL", TransCode.SELL, sale, 10, 10),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 5), 10, 12),
    ]
    events = [_event("AAPL", sale, 10, 200, 100, date(2026, 1, 1))]

    def _blank(**overrides) -> LotMatchRow:
        payload = dict(
            status="matched",
            symbol="AAPL",
            description="",
            quantity=0,
            date_sold_1099=sale,
            export_trade_date=sale,
            proceeds_1099=100.0,
            proceeds_export=0.0,
            cost_basis_1099=200.0,
            cost_basis_export=0.0,
            wash_sale_disallowed=40.0,
        )
        payload.update(overrides)
        return LotMatchRow.model_construct(**payload)

    for quantity in (0, None):
        report = _report(_blank(quantity=quantity))
        assert report.totals_ok is True
        chains = _build(
            transactions=txns,
            wash_sale_flags=[flag],
            realized_events=events,
            supplemental=_supplemental(2026),
            lot_match_report=report,
            tax_year=2026,
        )
        assert len(chains) == 1
        assert len(chains[0].hops) == 1
        hop = chains[0].hops[0]
        assert chains[0].reconcile_status == "not_compared"
        assert hop.reconcile_status == "not_compared"
        assert hop.not_compared_reason == "unknown"
        assert hop.sale_quantity == pytest.approx(10)
        assert hop.engine_disallowed_loss == pytest.approx(100)
        assert hop.form_1099_wash is None
        assert hop.plain_english == (
            "This 1099 sale has no quantity, so it was not compared."
        )
        assert all(chain.reconcile_status != "not_on_1099" for chain in chains)
        assert all(hop.reconcile_status != "not_on_1099" for chain in chains for hop in chain.hops)
        assert flag.disallowed_loss == pytest.approx(100)

    both_blank = _build(
        transactions=txns,
        wash_sale_flags=[flag],
        realized_events=events,
        supplemental=_supplemental(2026),
        lot_match_report=_report(_blank(quantity=0), _blank(quantity=None)),
        tax_year=2026,
    )
    assert len(both_blank) == 1
    assert both_blank[0].hops[0].not_compared_reason == "unknown"
    assert all(chain.reconcile_status != "not_on_1099" for chain in both_blank)

    later = date(2026, 7, 15)
    later_flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=later,
        sale_quantity=10,
        sale_loss=80,
        repurchase_date=date(2026, 7, 20),
        repurchase_quantity=10,
        disallowed_loss=80,
        adjusted_cost_basis=200,
    )
    # 160 - 80 matches the flag loss. A same-day stock lot that misses
    # that loss is unidentified and is not an absence claim.
    later_event = _event("AAPL", later, 10, 160, 80, date(2026, 6, 5))
    shared = _build(
        transactions=txns
        + [
            _txn("AAPL", TransCode.SELL, later, 10, 8),
            _txn("AAPL", TransCode.BUY, date(2026, 7, 20), 10, 9),
        ],
        wash_sale_flags=[flag, later_flag],
        realized_events=events + [later_event],
        supplemental=_supplemental(2026),
        lot_match_report=_report(_blank()),
        tax_year=2026,
    )
    june = [chain for chain in shared if chain.hops[0].sale_date == sale]
    july = [chain for chain in shared if chain.hops[0].sale_date == later]
    assert len(june) == 1
    assert june[0].hops[0].not_compared_reason == "unknown"
    assert len(july) == 1
    assert july[0].reconcile_status == "not_on_1099"
    assert all(
        chain.hops[0].sale_quantity != pytest.approx(0) for chain in shared
    )

    small = WashSaleFlag(
        symbol="AAPL",
        sale_date=sale,
        sale_quantity=4,
        sale_loss=40,
        repurchase_date=date(2026, 6, 5),
        repurchase_quantity=4,
        disallowed_loss=40,
        adjusted_cost_basis=88,
    )
    same_day = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 14, 20),
            _txn("AAPL", TransCode.SELL, sale, 10, 10),
            _txn("AAPL", TransCode.SELL, sale, 4, 10),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 5), 14, 12),
        ],
        wash_sale_flags=[flag, small],
        realized_events=[
            _event("AAPL", sale, 10, 200, 100, date(2026, 1, 1)),
            _event("AAPL", sale, 4, 80, 40, date(2026, 1, 1)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(_blank()),
        tax_year=2026,
    )
    assert len(same_day) == 2
    assert all(chain.reconcile_status == "not_compared" for chain in same_day)
    assert all(chain.hops[0].not_compared_reason == "unknown" for chain in same_day)
    assert all(chain.reconcile_status != "not_on_1099" for chain in same_day)
    assert all(chain.hops[0].sale_quantity != pytest.approx(0) for chain in same_day)


def test_two_single_contract_opens_quiet_a_two_share_stock_loss():
    """Two 1-contract opens in the window quiet a 2-share stock loss.

    The repurchase is 2 shares. Neither open matches that quantity alone.
    The flag is still one quiet line, with no stock dollar story.
    """
    sale = date(2026, 6, 1)
    repurchase = date(2026, 6, 10)
    flag = WashSaleFlag(
        symbol="AMD",
        sale_date=sale,
        sale_quantity=2,
        sale_loss=20,
        repurchase_date=repurchase,
        repurchase_quantity=2,
        disallowed_loss=20,
        adjusted_cost_basis=110,
    )
    chains = _build(
        transactions=[
            _txn("AMD", TransCode.BUY, date(2026, 5, 1), 2, 50),
            _txn("AMD", TransCode.SELL, sale, 2, 40),
            _txn("AMD", TransCode.BTO, date(2026, 6, 5), 1, 3, AssetType.OPTION),
            _txn("AMD", TransCode.BUY, repurchase, 2, 45),
            _txn("AMD", TransCode.BTO, date(2026, 6, 12), 1, 4, AssetType.OPTION),
        ],
        wash_sale_flags=[flag],
        realized_events=[
            _event("AMD", sale, 2, 100, 80, date(2026, 5, 1)),
        ],
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    hop = chains[0].hops[0]
    assert hop.not_compared_reason == "option"
    assert hop.reconcile_status == "not_compared"
    assert hop.plain_english == OPTION_LINE
    assert chains[0].plain_english == OPTION_LINE
    assert hop.proceeds is None
    assert hop.cost_basis is None
    assert hop.replacement_buys == []
    assert hop.closed_lots == []
    assert hop.engine_disallowed_loss is None
    assert hop.disallowed_loss == pytest.approx(0)
    assert "$" not in hop.plain_english
    assert "You sold" not in hop.plain_english
    assert flag.sale_loss == pytest.approx(20)
    assert flag.disallowed_loss == pytest.approx(20)
    assert flag.adjusted_cost_basis == pytest.approx(110)
    assert flag.repurchase_quantity == pytest.approx(2)


def test_undated_1099_only_row_allocates_no_replacement_buys():
    """No export trade date: show the 1099 row, and do not name a replacement."""
    sold = date(2026, 6, 1)
    window_buy = _txn("NVDA", TransCode.BUY, date(2026, 6, 5), 2, 9)
    chains = _build(
        transactions=[window_buy],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="NVDA",
                quantity=2,
                export_trade_date=None,
                date_sold_1099=sold,
                proceeds_1099=10,
                cost_basis_1099=20,
                wash_sale_disallowed=8,
            ),
            bucket="unmatched",
        ),
        tax_lots=[_lot("NVDA", 2, 9, date(2026, 6, 5), wash=8)],
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "on_1099_only"
    assert hop.sale_date == sold
    assert hop.replacement_buys == []
    assert hop.form_1099_wash == pytest.approx(8)
    assert "moved into the new lot's basis" not in hop.plain_english
    assert "moved into the replacement" not in hop.plain_english
    assert "back on" not in hop.plain_english
    assert "did not flag a matching wash" in hop.plain_english
    assert window_buy.quantity == pytest.approx(2)


def test_split_stock_1099_rows_stay_with_the_quiet_option_day():
    """40 + 60 shares are the 100-share sale. They are not a missed wash."""
    sale = date(2026, 6, 1)
    flag = WashSaleFlag(
        symbol="AMD",
        sale_date=sale,
        sale_quantity=100,
        sale_loss=1000,
        repurchase_date=date(2026, 6, 10),
        repurchase_quantity=100,
        disallowed_loss=1000,
        adjusted_cost_basis=5000,
    )
    chains = _build(
        transactions=[
            _txn("AMD", TransCode.BUY, date(2026, 1, 1), 100, 50),
            _txn("AMD", TransCode.SELL, sale, 100, 40),
            _txn("AMD", TransCode.STC, sale, 100, 2, AssetType.OPTION),
            _txn("AMD", TransCode.BUY, date(2026, 6, 10), 100, 45),
        ],
        wash_sale_flags=[flag],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AMD",
                quantity=40,
                export_trade_date=sale,
                proceeds_1099=1600,
                cost_basis_1099=2000,
                wash_sale_disallowed=400,
            ),
            _row(
                symbol="AMD",
                quantity=60,
                export_trade_date=sale,
                proceeds_1099=2400,
                cost_basis_1099=3000,
                wash_sale_disallowed=600,
            ),
        ),
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    hop = chains[0].hops[0]
    assert hop.not_compared_reason == "option"
    assert hop.reconcile_status == "not_compared"
    assert hop.plain_english == OPTION_LINE
    assert hop.proceeds is None
    assert hop.cost_basis is None
    assert hop.form_1099_wash is None
    assert hop.replacement_buys == []
    assert "$" not in hop.plain_english
    assert "did not flag a matching wash" not in hop.plain_english
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    assert flag.disallowed_loss == pytest.approx(1000)
    assert flag.sale_loss == pytest.approx(1000)


def test_same_day_option_open_reserves_the_matching_stock_1099_row():
    """A same-day option open quiets the stock flag and keeps its 1099 row."""
    sale = date(2026, 6, 1)
    repurchase = date(2026, 6, 8)
    flag = WashSaleFlag(
        symbol="AMD",
        sale_date=sale,
        sale_quantity=10,
        sale_loss=100,
        repurchase_date=repurchase,
        repurchase_quantity=10,
        disallowed_loss=100,
        adjusted_cost_basis=550,
    )
    chains = _build(
        transactions=[
            _txn("AMD", TransCode.BUY, date(2026, 1, 1), 10, 50),
            _txn("AMD", TransCode.SELL, sale, 10, 40),
            _txn("AMD", TransCode.BUY, repurchase, 10, 45),
            _txn("AMD", TransCode.BTO, repurchase, 10, 1.5, AssetType.OPTION),
        ],
        wash_sale_flags=[flag],
        realized_events=[
            _event("AMD", sale, 10, 500, 400, date(2026, 1, 1)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AMD",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=400,
                cost_basis_1099=500,
                wash_sale_disallowed=100,
            )
        ),
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    hop = chains[0].hops[0]
    assert hop.not_compared_reason == "option"
    assert hop.reconcile_status == "not_compared"
    assert hop.plain_english == OPTION_LINE
    assert hop.proceeds is None
    assert hop.cost_basis is None
    assert hop.form_1099_wash is None
    assert hop.replacement_buys == []
    assert "$" not in hop.plain_english
    assert "did not flag a matching wash" not in hop.plain_english
    assert "$100" not in hop.plain_english
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    assert flag.disallowed_loss == pytest.approx(100)
    assert flag.adjusted_cost_basis == pytest.approx(550)


def test_buy_to_close_does_not_explain_the_same_day_stock_sell():
    """An option close is not narrated with the same-day stock sell's dollars.

    2 contracts bought to close, and 2 shares sold at $40, share one day.
    The stock fill is not the option flag: no $200 loss sentence and no
    $280 basis built from $80 of stock proceeds plus that loss.
    """
    sale = date(2026, 6, 1)
    flag = WashSaleFlag(
        symbol="AMD",
        sale_date=sale,
        sale_quantity=2,
        sale_loss=200,
        repurchase_date=date(2026, 6, 8),
        repurchase_quantity=2,
        disallowed_loss=200,
        adjusted_cost_basis=400,
    )
    chains = _build(
        transactions=[
            _txn("AMD", TransCode.BTC, sale, 2, 1.50, AssetType.OPTION),
            _txn("AMD", TransCode.SELL, sale, 2, 40),
            _txn("AMD", TransCode.BUY, date(2026, 6, 8), 2, 42),
        ],
        wash_sale_flags=[flag],
        realized_events=[],
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    hop = chains[0].hops[0]
    assert hop.not_compared_reason == "option"
    assert hop.reconcile_status == "not_compared"
    assert hop.plain_english == OPTION_LINE
    assert hop.proceeds is None
    assert hop.cost_basis is None
    assert hop.cost_basis != pytest.approx(280)
    assert hop.proceeds != pytest.approx(80)
    assert hop.replacement_buys == []
    assert hop.engine_adjusted_cost_basis is None
    assert "$" not in hop.plain_english
    assert "You sold" not in hop.plain_english
    assert "sold 2 AMD at a $200 loss" not in hop.plain_english
    assert "$280" not in hop.plain_english
    assert "$200" not in hop.plain_english
    assert flag.sale_loss == pytest.approx(200)
    assert flag.disallowed_loss == pytest.approx(200)
    assert flag.adjusted_cost_basis == pytest.approx(400)


def test_stock_only_over_cap_1099_rows_have_no_sibling():
    """More than 12 stock rows are not compared, and none is a missed wash.

    Each row has a positive wash. A sibling would say the export did not
    flag that wash. The symbol has no option trade.
    """
    sale = date(2026, 6, 1)
    flag = WashSaleFlag(
        symbol="IBM",
        sale_date=sale,
        sale_quantity=1,
        sale_loss=5,
        repurchase_date=date(2026, 6, 6),
        repurchase_quantity=1,
        disallowed_loss=5,
        adjusted_cost_basis=15,
    )
    rows = [
        _row(
            symbol="IBM",
            quantity=1,
            export_trade_date=sale,
            proceeds_1099=5,
            cost_basis_1099=10,
            wash_sale_disallowed=5,
        )
        for _index in range(13)
    ]
    chains = _build(
        transactions=[
            _txn("IBM", TransCode.BUY, date(2026, 1, 1), 1, 10),
            _txn("IBM", TransCode.SELL, sale, 1, 5),
            _txn("IBM", TransCode.BUY, date(2026, 6, 6), 1, 6),
        ],
        wash_sale_flags=[flag],
        realized_events=[
            _event("IBM", sale, 1, 10, 5, date(2026, 1, 1)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(*rows),
    )
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "not_compared"
    assert hop.not_compared_reason == "over_cap"
    assert hop.form_1099_wash is None
    assert "without guessing" in hop.plain_english
    assert "did not flag a matching wash" not in hop.plain_english
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    assert flag.disallowed_loss == pytest.approx(5)


def test_unidentified_flag_reserves_same_day_1099_row():
    """Two same-size same-day sells do not leave a missed-wash sibling.

    The 1099 row is the same day, quantity 10, wash $20. It is held with
    the unidentified flags instead of becoming an on_1099_only chain.
    """
    from lot_matcher import CENTS

    sale = date(2026, 6, 1)
    near = 10.001
    assert abs(10 * near - 100.0) <= CENTS
    assert abs(10 * 10.0 - 100.0) <= CENTS
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 20, 12),
        _txn("AAPL", TransCode.SELL, sale, 10, 10.0),
        _txn("AAPL", TransCode.SELL, sale, 10, near),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 11),
    ]
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=130,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=20,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=20,
            adjusted_cost_basis=130,
        ),
    ]
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", sale, 10, 120, 100.0, date(2026, 1, 1)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AAPL",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=100,
                wash_sale_disallowed=20,
            )
        ),
    )
    assert len(chains) == 2
    assert all(len(chain.hops) == 1 for chain in chains)
    assert all(chain.hops[0].not_compared_reason == "unidentified" for chain in chains)
    assert all(chain.reconcile_status == "not_compared" for chain in chains)
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    assert all("did not flag a matching wash" not in chain.plain_english for chain in chains)
    assert all(chain.hops[0].proceeds is None for chain in chains)
    assert all(chain.hops[0].replacement_buys == [] for chain in chains)


def test_confirmed_sells_with_different_proceeds_stay_unidentified():
    """Losses both fit within CENTS. Proceeds differ by more than CENTS.

    The hop stays unidentified. The other sale's proceeds are not shown.
    """
    from lot_matcher import CENTS

    sale = date(2026, 6, 1)
    assert abs(100 - 120) > CENTS
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.SELL, sale, 10, 10),
            _txn("AAPL", TransCode.SELL, sale, 10, 12),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 11),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=10,
                sale_loss=20,
                repurchase_date=date(2026, 6, 10),
                repurchase_quantity=10,
                disallowed_loss=20,
                adjusted_cost_basis=140,
            )
        ],
        realized_events=[
            _event("AAPL", sale, 10, 120, 100, date(2026, 1, 1)),
            _event("AAPL", sale, 10, 140, 120, date(2026, 1, 2)),
        ],
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.not_compared_reason == "unidentified"
    assert hop.reconcile_status == "not_compared"
    assert hop.proceeds is None
    assert hop.cost_basis is None
    assert hop.replacement_buys == []
    assert hop.original_purchase_date is None
    assert hop.closed_lots == []
    assert "You sold" not in hop.plain_english
    assert "Jan 1" not in hop.plain_english
    assert "Jan 2" not in hop.plain_english


def test_option_inside_sale_window_when_repurchase_is_earlier():
    """Sale Jun 30, repurchase Jun 1, option Jul 10 is still inside the window."""
    sale = date(2026, 6, 30)
    repurchase = date(2026, 6, 1)
    chains = _build(
        transactions=[
            _txn("AMD", TransCode.BUY, date(2026, 5, 1), 10, 50),
            _txn("AMD", TransCode.SELL, sale, 10, 40),
            _txn("AMD", TransCode.BUY, repurchase, 10, 45),
            _txn("AMD", TransCode.BTO, date(2026, 7, 10), 1, 2, AssetType.OPTION),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AMD",
                sale_date=sale,
                sale_quantity=10,
                sale_loss=100,
                repurchase_date=repurchase,
                repurchase_quantity=10,
                disallowed_loss=100,
                adjusted_cost_basis=550,
            )
        ],
        realized_events=[
            _event("AMD", sale, 10, 500, 400, date(2026, 5, 1)),
        ],
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.not_compared_reason == "option"
    assert hop.reconcile_status == "not_compared"
    assert hop.plain_english == OPTION_LINE
    assert hop.proceeds is None
    assert hop.cost_basis is None
    assert hop.replacement_buys == []


def test_option_window_ends_are_quiet_and_the_days_outside_are_not():
    """Exactly 30 days before the sale and 30 days after the later date are quiet.

    One day outside either end is a normal stock hop.
    """
    from datetime import timedelta

    sale = date(2026, 6, 30)
    repurchase = date(2026, 7, 20)
    start = sale - timedelta(days=30)
    end = max(sale, repurchase) + timedelta(days=30)
    assert start == date(2026, 5, 31)
    assert end == date(2026, 8, 19)

    def _chains(option_day: date):
        return _build(
            transactions=[
                _txn("AMD", TransCode.BUY, date(2026, 1, 1), 10, 50),
                _txn("AMD", TransCode.SELL, sale, 10, 40),
                _txn("AMD", TransCode.BUY, repurchase, 10, 45),
                _txn("AMD", TransCode.BTO, option_day, 1, 2, AssetType.OPTION),
            ],
            wash_sale_flags=[
                WashSaleFlag(
                    symbol="AMD",
                    sale_date=sale,
                    sale_quantity=10,
                    sale_loss=100,
                    repurchase_date=repurchase,
                    repurchase_quantity=10,
                    disallowed_loss=100,
                    adjusted_cost_basis=550,
                )
            ],
            realized_events=[
                _event("AMD", sale, 10, 500, 400, date(2026, 1, 1)),
            ],
        )

    for option_day in (start, end):
        chains = _chains(option_day)
        assert len(chains) == 1
        hop = chains[0].hops[0]
        assert hop.not_compared_reason == "option"
        assert hop.plain_english == OPTION_LINE
        assert hop.proceeds is None
        assert hop.replacement_buys == []

    for option_day in (start - timedelta(days=1), end + timedelta(days=1)):
        chains = _chains(option_day)
        assert len(chains) == 1
        hop = chains[0].hops[0]
        assert hop.not_compared_reason != "option"
        assert hop.proceeds is not None
        assert "You sold" in hop.plain_english
        assert OPTION_LINE not in hop.plain_english


def test_two_same_day_stock_flags_stay_separate_quiet_hops():
    """Two same-day stock flags and one nearby option open stay two hops."""
    sale = date(2026, 6, 15)
    flags = [
        WashSaleFlag(
            symbol="AMD",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=100,
            repurchase_date=date(2026, 6, 20),
            repurchase_quantity=10,
            disallowed_loss=100,
            adjusted_cost_basis=500,
        ),
        WashSaleFlag(
            symbol="AMD",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=80,
            repurchase_date=date(2026, 6, 20),
            repurchase_quantity=10,
            disallowed_loss=80,
            adjusted_cost_basis=480,
        ),
    ]
    chains = _build(
        transactions=[
            _txn("AMD", TransCode.BUY, date(2026, 1, 1), 20, 50),
            _txn("AMD", TransCode.SELL, sale, 10, 40),
            _txn("AMD", TransCode.SELL, sale, 10, 42),
            _txn("AMD", TransCode.BTO, date(2026, 6, 18), 1, 3, AssetType.OPTION),
            _txn("AMD", TransCode.BUY, date(2026, 6, 20), 10, 45),
        ],
        wash_sale_flags=flags,
        realized_events=[
            _event("AMD", sale, 10, 500, 400, date(2026, 1, 1)),
            _event("AMD", sale, 10, 500, 420, date(2026, 1, 1)),
        ],
    )
    assert len(chains) == 2
    assert all(len(chain.hops) == 1 for chain in chains)
    assert len({chain.chain_id for chain in chains}) == 2
    assert all(chain.hops[0].not_compared_reason == "option" for chain in chains)
    assert all(chain.reconcile_status == "not_compared" for chain in chains)
    assert all(chain.plain_english == OPTION_LINE for chain in chains)
    assert all(chain.hops[0].proceeds is None for chain in chains)
    assert all(chain.hops[0].replacement_buys == [] for chain in chains)


def test_1099_only_match_of_the_second_sale_allocates_no_buys():
    """Two same-day stock sells. The wash row matches only the second.

    No replacement buys are allocated, and the copy has no moved-basis sentence.
    """
    sale = date(2026, 6, 1)
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.SELL, sale, 10, 10),
            _txn("AAPL", TransCode.SELL, sale, 10, 20),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 5), 10, 15),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="AAPL",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=200,
                cost_basis_1099=250,
                wash_sale_disallowed=50,
            ),
            bucket="unmatched",
        ),
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "on_1099_only"
    assert hop.replacement_buys == []
    assert hop.tacked_acquisition_date is None
    assert hop.carryover_days is None
    assert "moved into" not in hop.plain_english
    assert "did not flag a matching wash" in hop.plain_english


def _penny_apart_quiet_rows(sale: date):
    """10 @ $10.00 and 10 @ $10.01, each with a positive wash. Outside CENTS."""
    from lot_matcher import CENTS

    assert abs(100.10 - 100.00) > CENTS
    return (
        _row(
            symbol="AAPL",
            quantity=10,
            export_trade_date=sale,
            proceeds_1099=100.00,
            cost_basis_1099=120.00,
            wash_sale_disallowed=20,
        ),
        _row(
            symbol="AAPL",
            quantity=10,
            export_trade_date=sale,
            proceeds_1099=100.10,
            cost_basis_1099=120.10,
            wash_sale_disallowed=15,
        ),
    )


def _assert_quiet_without_dollars(chains):
    assert len(chains) == 1
    assert len(chains[0].hops) == 1
    hop = chains[0].hops[0]
    assert hop.reconcile_status != "on_1099_only"
    assert hop.proceeds is None
    assert hop.cost_basis is None
    assert hop.form_1099_wash is None
    assert hop.replacement_buys == []
    assert "$" not in hop.plain_english
    assert "did not flag a matching wash" not in hop.plain_english
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    assert all("$" not in chain.plain_english for chain in chains)
    assert all(
        "did not flag a matching wash" not in chain.plain_english for chain in chains
    )
    return hop


def test_quiet_flag_reserves_every_same_day_stock_sell_price():
    """Two fills a penny apart both hold their 1099 rows.

    Sells on 2026-06-01 are 10 @ $10.00 and 10 @ $10.01. One qty-10 stock
    flag is quieted (a) by an option in the window and (b) because both
    lots fit the flag loss within CENTS. Each price has a positive wash.
    Neither row is an on_1099_only chain, and no dollars are shown.
    """
    from lot_matcher import CENTS

    sale = date(2026, 6, 1)
    loss = 20.0
    assert abs((120.0 - 100.0) - loss) <= CENTS
    assert abs((120.10 - 100.10) - loss) <= CENTS
    sells = [
        _txn("AAPL", TransCode.SELL, sale, 10, 10.00),
        _txn("AAPL", TransCode.SELL, sale, 10, 10.01),
    ]
    events = [
        _event("AAPL", sale, 10, 120.0, 100.0, date(2026, 1, 1)),
        _event("AAPL", sale, 10, 120.10, 100.10, date(2026, 1, 2)),
    ]
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=sale,
        sale_quantity=10,
        sale_loss=loss,
        repurchase_date=date(2026, 6, 10),
        repurchase_quantity=10,
        disallowed_loss=loss,
        adjusted_cost_basis=130,
    )
    rows = _penny_apart_quiet_rows(sale)

    option_quiet = _build(
        transactions=sells
        + [
            _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 11),
            _txn("AAPL", TransCode.BTO, date(2026, 6, 5), 1, 2, AssetType.OPTION),
        ],
        wash_sale_flags=[flag],
        realized_events=events,
        supplemental=_supplemental(2026),
        lot_match_report=_report(*rows),
    )
    option_hop = _assert_quiet_without_dollars(option_quiet)
    assert option_hop.not_compared_reason == "option"
    assert option_hop.plain_english == OPTION_LINE
    assert flag.disallowed_loss == pytest.approx(loss)
    assert flag.sale_loss == pytest.approx(loss)

    unidentified = _build(
        transactions=sells + [_txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 11)],
        wash_sale_flags=[flag],
        realized_events=events,
        supplemental=_supplemental(2026),
        lot_match_report=_report(*rows),
    )
    unidentified_hop = _assert_quiet_without_dollars(unidentified)
    assert unidentified_hop.not_compared_reason == "unidentified"
    assert unidentified_hop.proceeds is None
    assert flag.disallowed_loss == pytest.approx(loss)


def test_ambiguous_cross_price_row_stays_quiet():
    """A 1099 row inside two fills' cent match is not a missed wash.

    Two $10.00 sells are one search. $10.004 is a second price. The
    10-share row at $100.04 is outside CENTS of $10.00. The 3-share row
    matches both prices and sums to neither sale, so it stays quiet along
    with the two 10-share rows.
    """
    from wash_sale_chain import _row_total_matches_price

    sale = date(2026, 6, 1)
    ten_at_four = _row(quantity=10, proceeds_1099=100.04, export_trade_date=sale)
    three = _row(quantity=3, proceeds_1099=30.02, export_trade_date=sale)
    assert _row_total_matches_price(ten_at_four, 10.00) is False
    assert _row_total_matches_price(three, 10.00) is True
    assert _row_total_matches_price(three, 10.004) is True
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=sale,
        sale_quantity=10,
        sale_loss=20,
        repurchase_date=date(2026, 6, 10),
        repurchase_quantity=10,
        disallowed_loss=20,
        adjusted_cost_basis=130,
    )
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.SELL, sale, 10, 10.00),
            _txn("AAPL", TransCode.SELL, sale, 10, 10.00),
            _txn("AAPL", TransCode.SELL, sale, 10, 10.004),
            _txn("AAPL", TransCode.BTO, date(2026, 6, 5), 1, 2, AssetType.OPTION),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 11),
        ],
        wash_sale_flags=[flag],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AAPL",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=100.00,
                wash_sale_disallowed=20,
            ),
            _row(
                symbol="AAPL",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=100.04,
                wash_sale_disallowed=15,
            ),
            _row(
                symbol="AAPL",
                quantity=3,
                export_trade_date=sale,
                proceeds_1099=30.02,
                wash_sale_disallowed=4,
            ),
        ),
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.not_compared_reason == "option"
    assert hop.reconcile_status != "on_1099_only"
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
    assert all("did not flag a matching wash" not in chain.plain_english for chain in chains)
    assert all("$" not in chain.plain_english for chain in chains)
    assert hop.form_1099_wash is None
    assert flag.disallowed_loss == pytest.approx(20)


def test_sold_lot_inside_the_window_is_not_the_replacement():
    """The shares that were sold are not shown as the replacement buy.

    Buy 10 AAPL at $100 on Jun 1, sell 10 at $80 on Jun 20, buy 10 at $90
    on Jun 25. No 1099. The Jun 25 buy is allocated and named. Jun 1 is
    the closed lot, so it is not allocated and not called the replacement.
    The engine's adjusted basis was priced off that closed lot, so it is
    not labeled as the Jun 25 buy's basis.
    """
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 6, 1), 10, 100),
        _txn("AAPL", TransCode.SELL, date(2026, 6, 20), 10, 80),
        _txn("AAPL", TransCode.BUY, date(2026, 6, 25), 10, 90),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    assert len(flags) == 1
    assert flags[0].repurchase_date == date(2026, 6, 1)
    assert flags[0].adjusted_cost_basis == pytest.approx(1200)
    before = copy.deepcopy(txns)
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", date(2026, 6, 20), 10, 1000, 800, date(2026, 6, 1)),
        ],
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert [buy.activity_date for buy in hop.replacement_buys] == [date(2026, 6, 25)]
    assert hop.replacement_buys[0].allocated_quantity == pytest.approx(10)
    assert hop.replacement_buys[0].allocated_disallowed == pytest.approx(200)
    assert hop.replacement_buys[0].explained_basis == pytest.approx(1100)
    assert hop.replacement_buys[0].explained_basis != pytest.approx(
        flags[0].adjusted_cost_basis
    )
    assert hop.engine_adjusted_cost_basis is None
    assert "Jun 25" in hop.plain_english
    assert "Jun 1" not in hop.plain_english
    assert "moved into" in hop.plain_english
    assert flags[0].adjusted_cost_basis == pytest.approx(1200)
    assert flags[0].disallowed_loss == pytest.approx(200)
    assert [(txn.activity_date, txn.quantity, txn.price) for txn in txns] == [
        (txn.activity_date, txn.quantity, txn.price) for txn in before
    ]


def test_leftover_half_nickel_on_ten_shares_stays_two_chains():
    """10 shares at $100.00 and 10 shares at $100.05 are not one sale.

    Five cents is outside CENTS. They do not become "You sold 20" or a
    $70 wash.
    """
    sale = date(2026, 6, 1)
    chains = _build(
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                status="1099_only",
                symbol="AAPL",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=100.00,
                wash_sale_disallowed=30,
            ),
            _row(
                status="1099_only",
                symbol="AAPL",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=100.05,
                wash_sale_disallowed=40,
            ),
            bucket="unmatched",
        ),
    )
    assert len(chains) == 2
    assert sorted(chain.hops[0].sale_quantity for chain in chains) == pytest.approx([10, 10])
    assert {chain.hops[0].disallowed_loss for chain in chains} == {30, 40}
    assert {chain.hops[0].proceeds for chain in chains} == {100.0, 100.05}
    assert all("You sold 20" not in chain.plain_english for chain in chains)
    assert all("$70" not in chain.plain_english for chain in chains)
    assert all(chain.hops[0].disallowed_loss != pytest.approx(70) for chain in chains)


def test_same_day_flags_a_fraction_of_a_cent_apart_match_their_own_rows():
    """$10.00 and $10.004 each match their own 1099 row.

    Four cents of proceeds is outside CENTS, so the rows are not a tie
    and neither hop is ambiguous.
    """
    sale = date(2026, 6, 1)
    flags = [
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=100,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=100,
            adjusted_cost_basis=210,
        ),
        WashSaleFlag(
            symbol="AAPL",
            sale_date=sale,
            sale_quantity=10,
            sale_loss=80,
            repurchase_date=date(2026, 6, 10),
            repurchase_quantity=10,
            disallowed_loss=80,
            adjusted_cost_basis=190.04,
        ),
    ]
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 20, 20),
            _txn("AAPL", TransCode.SELL, sale, 10, 10.00),
            _txn("AAPL", TransCode.SELL, sale, 10, 10.004),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 10), 10, 11),
        ],
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", sale, 10, 200, 100.00, date(2026, 1, 1)),
            _event("AAPL", sale, 10, 180.04, 100.04, date(2026, 1, 1)),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AAPL",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=100.00,
                wash_sale_disallowed=100,
            ),
            _row(
                symbol="AAPL",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=100.04,
                wash_sale_disallowed=80,
            ),
        ),
    )
    assert len(chains) == 2
    assert all(chain.reconcile_status == "matches" for chain in chains)
    assert {chain.hops[0].form_1099_wash for chain in chains} == {100, 80}
    assert {chain.hops[0].disallowed_loss for chain in chains} == {100, 80}
    assert all(chain.hops[0].not_compared_reason != "ambiguous" for chain in chains)
    assert all("no wash total was chosen" not in chain.plain_english for chain in chains)
    assert all(chain.hops[0].sale_quantity == pytest.approx(10) for chain in chains)


def test_two_buys_on_the_closed_date_leave_the_replacement_quiet():
    """Either Jun 1 buy could be the closed lot, so no replacement is named."""
    sale = date(2026, 6, 20)
    opened = date(2026, 6, 1)
    flag = WashSaleFlag(
        symbol="AAPL",
        sale_date=sale,
        sale_quantity=10,
        sale_loss=200,
        repurchase_date=opened,
        repurchase_quantity=10,
        disallowed_loss=200,
        adjusted_cost_basis=1200,
    )
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, opened, 10, 100),
            _txn("AAPL", TransCode.BUY, opened, 10, 95),
            _txn("AAPL", TransCode.SELL, sale, 10, 80),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 25), 10, 90),
        ],
        wash_sale_flags=[flag],
        realized_events=[
            _event("AAPL", sale, 10, 1000, 800, opened),
        ],
    )
    hop = chains[0].hops[0]
    assert hop.replacement_buys == []
    assert hop.engine_adjusted_cost_basis is None
    assert "moved into" not in hop.plain_english
    assert "Jun 1" not in hop.plain_english
    assert "Jun 25" not in hop.plain_english
    assert flag.adjusted_cost_basis == pytest.approx(1200)


def test_partial_closed_buy_keeps_only_the_unconsumed_shares():
    """One 20-share buy on the closed date keeps the 10 shares that remain."""
    sale = date(2026, 6, 20)
    opened = date(2026, 6, 1)
    buy = _txn("AAPL", TransCode.BUY, opened, 20, 100)
    chains = _build(
        transactions=[
            buy,
            _txn("AAPL", TransCode.SELL, sale, 10, 80),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 25), 10, 90),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=opened,
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=1200,
            )
        ],
        realized_events=[
            _event("AAPL", sale, 10, 1000, 800, opened),
        ],
    )
    hop = chains[0].hops[0]
    allocated = {
        buy.activity_date: buy.allocated_quantity for buy in hop.replacement_buys
    }
    assert allocated[opened] == pytest.approx(10)
    assert allocated.get(date(2026, 6, 25), 0) == pytest.approx(0)
    assert hop.engine_adjusted_cost_basis is None
    assert "Jun 25" not in hop.plain_english
    assert buy.quantity == pytest.approx(20)


def test_undated_closed_lot_does_not_guess_a_pre_sale_replacement():
    """A closed lot with no acquisition date quiets every pre-sale window buy."""
    sale = date(2026, 6, 20)
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, date(2026, 6, 1), 10, 100),
            _txn("AAPL", TransCode.SELL, sale, 10, 80),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 25), 10, 90),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=date(2026, 6, 25),
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=1100,
            )
        ],
        realized_events=[
            _event("AAPL", sale, 10, 1000, 800, None),
        ],
    )
    hop = chains[0].hops[0]
    assert hop.replacement_buys == []
    assert "moved into" not in hop.plain_english
    assert hop.engine_adjusted_cost_basis is None
    assert hop.tacked_acquisition_date is None
    assert hop.carryover_days is None
    assert hop.not_compared_reason == "unallocated"
    assert "illustrative" not in hop.plain_english


def test_undated_closed_lot_keeps_a_buy_after_the_sale():
    """A buy after the sale cannot be the undated closed lot."""
    sale = date(2026, 6, 20)
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.SELL, sale, 10, 80),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 25), 10, 90),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=date(2026, 6, 25),
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=1100,
            )
        ],
        realized_events=[
            _event("AAPL", sale, 10, 1000, 800, None),
        ],
    )
    hop = chains[0].hops[0]
    assert [buy.activity_date for buy in hop.replacement_buys] == [date(2026, 6, 25)]
    assert hop.replacement_buys[0].allocated_quantity == pytest.approx(10)
    assert "Jun 25" in hop.plain_english
    assert "moved into" in hop.plain_english


def test_closed_lot_larger_than_its_only_window_buy_stays_quiet():
    """A 5-share window buy cannot be a 10-share closed lot."""
    sale = date(2026, 6, 20)
    opened = date(2026, 6, 1)
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, opened, 5, 100),
            _txn("AAPL", TransCode.SELL, sale, 10, 80),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 25), 10, 90),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=opened,
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=1200,
            )
        ],
        realized_events=[
            _event("AAPL", sale, 10, 1000, 800, opened),
        ],
    )
    hop = chains[0].hops[0]
    assert hop.replacement_buys == []
    assert "moved into" not in hop.plain_english
    assert hop.engine_adjusted_cost_basis is None


def test_same_day_buys_that_cannot_sum_to_the_closed_lot_stay_quiet():
    """6 and 6 do not make a 10-share closed lot, so nothing is allocated."""
    sale = date(2026, 6, 20)
    opened = date(2026, 6, 1)
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, opened, 6, 100),
            _txn("AAPL", TransCode.BUY, opened, 6, 90),
            _txn("AAPL", TransCode.SELL, sale, 10, 80),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=opened,
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=1200,
            )
        ],
        realized_events=[
            _event("AAPL", sale, 10, 1000, 800, opened),
        ],
    )
    hop = chains[0].hops[0]
    assert hop.replacement_buys == []
    assert "moved into" not in hop.plain_english


def test_unique_same_day_subset_drops_only_the_closed_shares():
    """A 10-share buy is the closed lot. The other 5 shares do not cover the repurchase.

    Five shares cannot explain a 10-share repurchase. The hop stays quiet
    instead of parking the disallowance on that leftover buy.
    """
    sale = date(2026, 6, 20)
    opened = date(2026, 6, 1)
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, opened, 10, 100),
            _txn("AAPL", TransCode.BUY, opened, 5, 90),
            _txn("AAPL", TransCode.SELL, sale, 10, 80),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=opened,
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=1200,
            )
        ],
        realized_events=[
            _event("AAPL", sale, 10, 1000, 800, opened),
        ],
    )
    hop = chains[0].hops[0]
    assert hop.replacement_buys == []
    assert hop.reconcile_status == "not_compared"
    assert "moved into" not in hop.plain_english
    assert "basis" not in hop.plain_english.lower()
    assert hop.engine_adjusted_cost_basis is None


def test_post_sale_buys_short_of_repurchase_quantity_stay_quiet():
    """50 closed pre-sale shares plus 50 bought after do not cover a 100-share repurchase.

    The sale closed the Jun 1 lot. The Jun 25 buy is only half of
    repurchase_quantity. It is not allocated, the disallowance is not
    parked on it, and the sentence does not say the loss moved into a basis.
    """
    sale = date(2026, 6, 20)
    opened = date(2026, 6, 1)
    later = date(2026, 6, 25)
    txns = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 1), 50, 80),
        _txn("AAPL", TransCode.BUY, opened, 50, 100),
        _txn("AAPL", TransCode.SELL, sale, 100, 70),
        _txn("AAPL", TransCode.BUY, later, 50, 90),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    assert len(flags) == 1
    flag = flags[0]
    assert flag.sale_quantity == pytest.approx(100)
    assert flag.repurchase_quantity == pytest.approx(100)
    assert flag.repurchase_date == opened
    assert flag.disallowed_loss == pytest.approx(2000)
    assert flag.adjusted_cost_basis == pytest.approx(7000)
    before_txns = copy.deepcopy(txns)
    before_loss = flag.disallowed_loss
    before_basis = flag.adjusted_cost_basis
    before_qty = flag.repurchase_quantity
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", sale, 50, 4000, 3500, date(2026, 1, 1)),
            _event("AAPL", sale, 50, 5000, 3500, opened),
        ],
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.reconcile_status == "not_compared"
    assert hop.replacement_buys == []
    assert hop.engine_adjusted_cost_basis is None
    assert all(buy.allocated_quantity == 0 for buy in hop.replacement_buys)
    assert all(buy.allocated_disallowed == 0 for buy in hop.replacement_buys)
    assert all(buy.explained_basis == 0 for buy in hop.replacement_buys)
    assert "moved into" not in hop.plain_english
    assert "basis" not in hop.plain_english.lower()
    assert "Jun 25" not in hop.plain_english
    assert "$6,500" not in hop.plain_english
    assert "$5,500" not in hop.plain_english
    assert flag.disallowed_loss == pytest.approx(before_loss)
    assert flag.adjusted_cost_basis == pytest.approx(before_basis)
    assert flag.repurchase_quantity == pytest.approx(before_qty)
    assert flag.disallowed_loss == pytest.approx(2000)
    assert flag.adjusted_cost_basis == pytest.approx(7000)
    assert [(txn.activity_date, txn.quantity, txn.price) for txn in txns] == [
        (txn.activity_date, txn.quantity, txn.price) for txn in before_txns
    ]


def test_prior_sale_lot_is_not_published_when_replacement_cannot_be_allocated():
    """Buy 80 on May 25, sell them Jun 1, buy 100 Jun 5, sell those Jun 20.

    Jun 20's window still holds the May 25 lot that Jun 1 already closed.
    Those 80 shares do not cover the repurchase, so the hop does not publish
    the engine basis (priced off that $10,000 lot), a tacked date, or
    carryover days, and it does not call a missing split illustrative.
    """
    may = date(2026, 5, 25)
    june_sale = date(2026, 6, 1)
    june_buy = date(2026, 6, 5)
    june_later = date(2026, 6, 20)
    txns = [
        _txn("AAPL", TransCode.BUY, may, 80, 125),
        _txn("AAPL", TransCode.SELL, june_sale, 80, 100),
        _txn("AAPL", TransCode.BUY, june_buy, 100, 110),
        _txn("AAPL", TransCode.SELL, june_later, 100, 90),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    later_flag = next(flag for flag in flags if flag.sale_date == june_later)
    assert later_flag.repurchase_date == may
    assert later_flag.adjusted_cost_basis == pytest.approx(12000)
    before = copy.deepcopy(txns)
    before_basis = later_flag.adjusted_cost_basis
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", june_sale, 80, 10000, 8000, may),
            _event("AAPL", june_later, 100, 11000, 9000, june_buy),
        ],
    )
    hop = next(
        chain.hops[0] for chain in chains if chain.hops[0].sale_date == june_later
    )
    assert hop.replacement_buys == []
    assert hop.engine_adjusted_cost_basis is None
    assert hop.tacked_acquisition_date is None
    assert hop.carryover_days is None
    assert hop.not_compared_reason == "unallocated"
    assert hop.reconcile_status == "not_compared"
    assert "illustrative" not in hop.plain_english
    earlier = next(
        chain.hops[0] for chain in chains if chain.hops[0].sale_date == june_sale
    )
    assert earlier.not_compared_reason != "unidentified"
    assert [buy.activity_date for buy in earlier.replacement_buys] == [june_buy]
    assert later_flag.adjusted_cost_basis == pytest.approx(before_basis)
    assert [(txn.activity_date, txn.quantity, txn.price) for txn in txns] == [
        (txn.activity_date, txn.quantity, txn.price) for txn in before
    ]


def test_unallocated_sale_does_not_take_the_sibling_1099_row():
    """10 shares at $80 and 10 at $70 on the same day keep their own rows.

    The $80 closed lot is two May 10 buys, so those shares cannot be picked
    out and that hop stays unallocated. Reserving only the $80 price leaves
    the $70 row for the sale that can be allocated.
    """
    sale = date(2026, 6, 1)
    opened = date(2026, 5, 10)
    other = date(2026, 5, 20)
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, opened, 6, 100),
            _txn("AAPL", TransCode.BUY, opened, 6, 90),
            _txn("AAPL", TransCode.BUY, other, 10, 100),
            _txn("AAPL", TransCode.SELL, sale, 10, 80),
            _txn("AAPL", TransCode.SELL, sale, 10, 70),
            _txn("AAPL", TransCode.BUY, date(2026, 6, 5), 10, 75),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=opened,
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=1200,
            ),
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=10,
                sale_loss=300,
                repurchase_date=date(2026, 6, 5),
                repurchase_quantity=10,
                disallowed_loss=300,
                adjusted_cost_basis=1050,
            ),
        ],
        realized_events=[
            _event("AAPL", sale, 10, 1000, 800, opened),
            _event("AAPL", sale, 10, 1000, 700, other),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AAPL",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=800,
                wash_sale_disallowed=200,
            ),
            _row(
                symbol="AAPL",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=700,
                wash_sale_disallowed=300,
            ),
        ),
    )
    assert len(chains) == 2
    quiet = next(chain for chain in chains if chain.hops[0].proceeds == pytest.approx(800))
    matched = next(chain for chain in chains if chain.hops[0].proceeds == pytest.approx(700))
    hop = quiet.hops[0]
    assert quiet.reconcile_status == "not_compared"
    assert hop.not_compared_reason == "unallocated"
    assert hop.engine_adjusted_cost_basis is None
    assert hop.tacked_acquisition_date is None
    assert hop.carryover_days is None
    assert hop.form_1099_wash is None
    assert "illustrative" not in hop.plain_english
    assert matched.reconcile_status == "matches"
    assert matched.hops[0].form_1099_wash == pytest.approx(300)
    assert matched.hops[0].not_compared_reason != "unallocated"
    assert all(chain.reconcile_status != "not_on_1099" for chain in chains)
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)


def test_all_filtered_window_buys_are_unallocated():
    """Every window buy is the lot this sale closed, so nothing remains.

    The engine still flags that pre-sale buy as a repurchase. Leaving the
    hop comparable would report the full disallowance against a same-year
    1099 row whose wash column is $0. An empty survivor list stays quiet
    with no basis and no comparison.
    """
    opened = date(2026, 5, 20)
    sale = date(2026, 6, 1)
    txns = [
        _txn("AAPL", TransCode.BUY, opened, 10, 100),
        _txn("AAPL", TransCode.SELL, sale, 10, 80),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    assert len(flags) == 1
    assert flags[0].disallowed_loss == pytest.approx(200)
    before = copy.deepcopy(txns)
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", sale, 10, 1000, 800, opened),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AAPL",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=800,
                cost_basis_1099=1000,
                wash_sale_disallowed=0,
            ),
        ),
    )
    assert len(chains) == 1
    hop = chains[0].hops[0]
    assert hop.replacement_buys == []
    assert hop.not_compared_reason == "unallocated"
    assert hop.reconcile_status == "not_compared"
    assert hop.engine_adjusted_cost_basis is None
    assert hop.tacked_acquisition_date is None
    assert hop.carryover_days is None
    assert hop.form_1099_wash is None
    assert hop.difference is None
    assert "illustrative" not in hop.plain_english
    assert "$200" not in hop.plain_english
    assert [(txn.activity_date, txn.quantity, txn.price) for txn in txns] == [
        (txn.activity_date, txn.quantity, txn.price) for txn in before
    ]


def test_unallocated_reservation_follows_same_day_tie_handling():
    """A quiet 10-share sale must not take the rows a 20-share sibling needs.

    Three 10-share 1099 lots at one price are every exact subset of the
    unallocated sale. Reserving them before same-day tie handling leaves
    the allocatable 20-share sale with no row, so it shows not_on_1099.
    The 20-share sale claims a pair; the 10-share hop stays quiet.
    """
    sale = date(2026, 6, 1)
    ambiguous = date(2026, 5, 10)
    closed = date(2026, 5, 15)
    replacement = date(2026, 6, 5)
    txns = [
        _txn("AAPL", TransCode.BUY, ambiguous, 6, 100),
        _txn("AAPL", TransCode.BUY, ambiguous, 6, 100),
        _txn("AAPL", TransCode.BUY, closed, 20, 100),
        _txn("AAPL", TransCode.SELL, sale, 10, 80),
        _txn("AAPL", TransCode.SELL, sale, 20, 80),
        _txn("AAPL", TransCode.BUY, replacement, 20, 90),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    small = next(flag for flag in flags if flag.sale_quantity == 10)
    large = next(flag for flag in flags if flag.sale_quantity == 20)
    assert small.disallowed_loss == pytest.approx(200)
    assert large.disallowed_loss == pytest.approx(400)
    chains = _build(
        transactions=txns,
        wash_sale_flags=flags,
        realized_events=[
            _event("AAPL", sale, 10, 1000, 800, ambiguous),
            _event("AAPL", sale, 20, 2000, 1600, closed),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            *[
                _row(
                    symbol="AAPL",
                    quantity=10,
                    export_trade_date=sale,
                    proceeds_1099=800,
                    cost_basis_1099=1000,
                    wash_sale_disallowed=200,
                )
                for _ in range(3)
            ],
        ),
    )
    assert len(chains) == 2
    quiet = next(
        chain for chain in chains if chain.hops[0].sale_quantity == pytest.approx(10)
    )
    matched = next(
        chain for chain in chains if chain.hops[0].sale_quantity == pytest.approx(20)
    )
    hop = quiet.hops[0]
    assert hop.not_compared_reason == "unallocated"
    assert quiet.reconcile_status == "not_compared"
    assert hop.engine_adjusted_cost_basis is None
    assert hop.tacked_acquisition_date is None
    assert hop.carryover_days is None
    assert hop.form_1099_wash is None
    assert matched.reconcile_status == "matches"
    assert matched.hops[0].form_1099_wash == pytest.approx(400)
    assert matched.hops[0].not_compared_reason != "unallocated"
    assert all(chain.reconcile_status != "not_on_1099" for chain in chains)
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)


def test_unallocated_ten_share_row_does_not_tie_twenty_and_thirty():
    """A quiet 10-share row must not make the 20- and 30-share sales ambiguous.

    The 30-share hop can also be 10+20. That overlap is only the unallocated
    sale's row. Each comparable sale has its own 1099 row and matches it.
    """
    sale = date(2026, 6, 1)
    ambiguous = date(2026, 5, 10)
    closed_20 = date(2026, 5, 15)
    closed_30 = date(2026, 5, 20)
    replacement = date(2026, 6, 5)
    chains = _build(
        transactions=[
            _txn("AAPL", TransCode.BUY, ambiguous, 6, 100),
            _txn("AAPL", TransCode.BUY, ambiguous, 6, 100),
            _txn("AAPL", TransCode.BUY, closed_20, 20, 100),
            _txn("AAPL", TransCode.BUY, closed_30, 30, 100),
            _txn("AAPL", TransCode.SELL, sale, 10, 80),
            _txn("AAPL", TransCode.SELL, sale, 20, 80),
            _txn("AAPL", TransCode.SELL, sale, 30, 80),
            _txn("AAPL", TransCode.BUY, replacement, 30, 90),
        ],
        wash_sale_flags=[
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=10,
                sale_loss=200,
                repurchase_date=ambiguous,
                repurchase_quantity=10,
                disallowed_loss=200,
                adjusted_cost_basis=1200,
            ),
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=20,
                sale_loss=400,
                repurchase_date=replacement,
                repurchase_quantity=20,
                disallowed_loss=400,
                adjusted_cost_basis=2200,
            ),
            WashSaleFlag(
                symbol="AAPL",
                sale_date=sale,
                sale_quantity=30,
                sale_loss=600,
                repurchase_date=replacement,
                repurchase_quantity=30,
                disallowed_loss=600,
                adjusted_cost_basis=3300,
            ),
        ],
        realized_events=[
            _event("AAPL", sale, 10, 1000, 800, ambiguous),
            _event("AAPL", sale, 20, 2000, 1600, closed_20),
            _event("AAPL", sale, 30, 3000, 2400, closed_30),
        ],
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(
                symbol="AAPL",
                quantity=10,
                export_trade_date=sale,
                proceeds_1099=800,
                cost_basis_1099=1000,
                wash_sale_disallowed=50,
            ),
            _row(
                symbol="AAPL",
                quantity=20,
                export_trade_date=sale,
                proceeds_1099=1600,
                cost_basis_1099=2000,
                wash_sale_disallowed=400,
            ),
            _row(
                symbol="AAPL",
                quantity=30,
                export_trade_date=sale,
                proceeds_1099=2400,
                cost_basis_1099=3000,
                wash_sale_disallowed=600,
            ),
        ),
    )
    assert len(chains) == 3
    by_qty = {chain.hops[0].sale_quantity: chain for chain in chains}
    quiet = by_qty[10]
    hop = quiet.hops[0]
    assert hop.not_compared_reason == "unallocated"
    assert quiet.reconcile_status == "not_compared"
    assert hop.engine_adjusted_cost_basis is None
    assert hop.tacked_acquisition_date is None
    assert hop.carryover_days is None
    assert hop.form_1099_wash is None
    assert by_qty[20].reconcile_status == "matches"
    assert by_qty[20].hops[0].form_1099_wash == pytest.approx(400)
    assert by_qty[30].reconcile_status == "matches"
    assert by_qty[30].hops[0].form_1099_wash == pytest.approx(600)
    assert all(chain.reconcile_status != "not_on_1099" for chain in chains)
    assert all(chain.hops[0].not_compared_reason != "ambiguous" for chain in chains)
    assert all(chain.reconcile_status != "on_1099_only" for chain in chains)
