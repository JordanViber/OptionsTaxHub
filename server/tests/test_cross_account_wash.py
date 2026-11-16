"""Cross-account wash flags stay out of the $924 engine total."""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main
from cross_account_wash import build_cross_account_chains
from csv_parser import parse_labeled_exports, transactions_to_tax_lots
from ledger import SAMPLE_FIXTURE_PRICES
from models import AssetType, TransCode, Transaction
from wash_sale import adjust_lots_for_wash_sales, detect_wash_sales

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_CSV = REPO_ROOT / "client" / "public" / "sample-robinhood-transactions.csv"
SAMPLE_TAXABLE = REPO_ROOT / "client" / "public" / "sample-cross-account-taxable.csv"
SAMPLE_IRA = REPO_ROOT / "client" / "public" / "sample-robinhood-ira.csv"
SAMPLE_1099 = REPO_ROOT / "client" / "public" / "sample-robinhood-1099-2026.pdf"

AMD_LINE = (
    "You sold 10 AMD at a $300 loss on Jul 15 and bought 10 back on Jul 24, "
    "so $300 moved into the new lot's basis. This matches your 1099 wash "
    "adjustment of $300."
)


def _txn(symbol, code, when, quantity, price, account=None):
    signed = -1 if code == TransCode.BUY else 1
    return Transaction(
        activity_date=when,
        process_date=when,
        settle_date=when,
        instrument=symbol,
        description=symbol,
        trans_code=code,
        quantity=quantity,
        price=price,
        amount=signed * quantity * price,
        asset_type=AssetType.STOCK,
        account_label=account,
    )


def test_single_account_wash_total_ignores_labels():
    unlabeled = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 4, 500),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 4, 480),
    ]
    labeled = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 4, 500, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 4, 480, "individual"),
    ]
    bare = detect_wash_sales(unlabeled, tax_year=2026)
    same = detect_wash_sales(labeled, tax_year=2026)
    assert round(sum(flag.disallowed_loss for flag in bare), 2) == 120
    assert round(sum(flag.disallowed_loss for flag in same), 2) == 120
    assert build_cross_account_chains(labeled, same, tax_year=2026) == []


def test_ira_replacement_is_estimated_and_does_not_change_basis():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 4, 500, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 4, 480, "ira_traditional"),
    ]
    flags = detect_wash_sales(transactions, tax_year=2026)
    assert flags == []
    chains = build_cross_account_chains(transactions, flags, tax_year=2026)
    assert len(chains) == 1
    chain = chains[0]
    assert chain.cross_account is True
    assert chain.permanent_disallowance is True
    assert chain.reconcile_status == "not_on_1099"
    assert chain.hops[0].disallowed_loss == pytest.approx(120)
    assert "Not on your 1099: cross-account" in chain.plain_english
    assert "may apply, ask a tax pro" in chain.plain_english
    assert "No basis carryover" in chain.plain_english
    assert chain.hops[0].account_name == "Individual"
    assert chain.hops[0].replacement_buys[0].account_name == "IRA (traditional)"
    assert chain.hops[0].replacement_buys[0].explained_basis == pytest.approx(1920)

    lots, _warnings, _realized = transactions_to_tax_lots(transactions)
    adjusted = adjust_lots_for_wash_sales(lots, flags)
    ira_lots = [lot for lot in adjusted if lot.account_label == "ira_traditional"]
    assert len(ira_lots) == 1
    assert ira_lots[0].wash_sale_disallowed == 0
    assert ira_lots[0].total_cost_basis == pytest.approx(1920)
    assert ira_lots[0].account_name == "IRA (traditional)"


def test_buy_outside_the_61_day_window_is_not_flagged():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 4, 500, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 3, 13), 4, 480, "ira_roth"),
    ]
    assert build_cross_account_chains(transactions, [], tax_year=2026) == []


def test_guest_multi_file_keeps_the_count_and_hides_the_rows(monkeypatch):
    monkeypatch.setattr("main.fetch_current_prices", lambda symbols, fb=None, allow_network=True: ({}, []))
    monkeypatch.setattr("main.fetch_option_prices", lambda labels, fb=None, allow_network=True: ({}, []))
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda lots: [])
    monkeypatch.setattr("main._save_history_best_effort", lambda *args, **kwargs: None)
    main.reset_guest_analyze_quota()
    individual = (
        "Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount\n"
        "01/06/2026,01/06/2026,01/08/2026,QQQ,Invesco QQQ Trust,Buy,4,500.00,-2000.00\n"
        "02/10/2026,02/10/2026,02/12/2026,QQQ,Invesco QQQ Trust,Sell,4,470.00,1880.00\n"
    )
    ira = (
        "Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount\n"
        "02/20/2026,02/20/2026,02/24/2026,QQQ,Invesco QQQ Trust,Buy,4,480.00,-1920.00\n"
    )
    client = TestClient(main.app)
    response = client.post(
        "/api/portfolio/analyze?filing_status=single&estimated_income=75000&tax_year=2026",
        files=[
            ("file", ("individual.csv", individual.encode(), "text/csv")),
            ("extra_files", ("ira.csv", ira.encode(), "text/csv")),
            ("account_labels", (None, "individual")),
            ("account_labels", (None, "ira_traditional")),
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cross_account_wash_count"] == 1
    assert body["wash_sale_chains"] == []
    assert body["wash_sale_flags"] == []
    assert body.get("sample_run") is not True
    qqq = next(lot for lot in body["tax_lots"] if lot["symbol"] == "QQQ")
    assert qqq["account_name"] == "IRA (traditional)"
    assert qqq["wash_sale_disallowed"] == 0


def test_sample_keeps_924_and_amd_and_adds_one_ira_example(monkeypatch):
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
        files=[
            ("file", ("sample-robinhood-transactions.csv", SAMPLE_CSV.read_bytes(), "text/csv")),
            ("extra_files", ("sample-cross-account-taxable.csv", SAMPLE_TAXABLE.read_bytes(), "text/csv")),
            ("extra_files", ("sample-robinhood-ira.csv", SAMPLE_IRA.read_bytes(), "text/csv")),
            ("supplemental_1099", ("sample-robinhood-1099-2026.pdf", SAMPLE_1099.read_bytes(), "application/pdf")),
            ("account_labels", (None, "individual")),
            ("account_labels", (None, "individual")),
            ("account_labels", (None, "ira_traditional")),
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    flags = body["wash_sale_flags"]
    assert round(sum(float(flag["disallowed_loss"]) for flag in flags), 2) == 924
    assert {flag["symbol"] for flag in flags} == {"AMD", "NVDA", "TSLA"}
    assert body["sample_run"] is True
    assert body["cross_account_wash_count"] == 1
    amd = next(chain for chain in body["wash_sale_chains"] if chain["symbol"] == "AMD")
    assert amd["plain_english"] == AMD_LINE
    assert amd["hops"][0]["disallowed_loss"] == pytest.approx(300)
    assert amd["hops"][0]["account_name"] == "Individual"
    assert amd["hops"][0]["replacement_buys"][0]["activity_date"] == "2026-07-24"
    cross = [chain for chain in body["wash_sale_chains"] if chain.get("cross_account")]
    assert len(cross) == 1
    assert cross[0]["symbol"] == "QQQ"
    assert cross[0]["permanent_disallowance"] is True
    assert "Not on your 1099: cross-account" in cross[0]["plain_english"]
    assert "may apply, ask a tax pro" in cross[0]["plain_english"]
    ira = next(
        lot
        for lot in body["tax_lots"]
        if lot["symbol"] == "QQQ" and lot["account_label"] == "ira_traditional"
    )
    assert ira["account_name"] == "IRA (traditional)"
    assert ira["wash_sale_disallowed"] == 0
    assert ira["total_cost_basis"] == pytest.approx(1920)
    individual = next(lot for lot in body["tax_lots"] if lot["symbol"] == "AAPL")
    assert individual["account_name"] == "Individual"


def test_labeled_parse_matches_the_public_sample_files():
    parts = [
        (SAMPLE_CSV.read_text(encoding="utf-8"), "individual"),
        (SAMPLE_TAXABLE.read_text(encoding="utf-8"), "individual"),
        (SAMPLE_IRA.read_text(encoding="utf-8"), "ira_traditional"),
    ]
    _lots, transactions, errors, _realized = parse_labeled_exports(parts)
    assert not any("Unrecognized" in error for error in errors)
    labels = {txn.account_label for txn in transactions if txn.instrument == "QQQ"}
    assert labels == {"individual", "ira_traditional"}
