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
    assert hop.reconcile_status == "not_on_1099"
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
    assert "tax totals use that engine figure" in second.plain_english
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


def test_not_on_1099_when_export_only_or_prior_year():
    txns = [
        _txn("AMD", TransCode.BUY, date(2026, 6, 1), 10, 150),
        _txn("AMD", TransCode.SELL, date(2026, 7, 15), 10, 120),
        _txn("AMD", TransCode.BUY, date(2026, 7, 24), 10, 125),
    ]
    flags = detect_wash_sales(txns, tax_year=2026)
    bare = _build(transactions=txns, wash_sale_flags=flags)
    assert bare[0].reconcile_status == "not_on_1099"
    assert "found only in this Robinhood export" in bare[0].plain_english

    same_year_other_symbol = _build(
        transactions=txns,
        wash_sale_flags=flags,
        supplemental=_supplemental(2026),
        lot_match_report=_report(
            _row(symbol="MSFT", wash_sale_disallowed=0, export_trade_date=date(2026, 7, 15))
        ),
    )
    assert same_year_other_symbol[0].reconcile_status == "not_on_1099"

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
    assert prior_year[0].reconcile_status == "not_on_1099"
    assert all(chain.reconcile_status != "on_1099_only" for chain in prior_year)


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
    assert "contracts" in chains[0].plain_english


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
