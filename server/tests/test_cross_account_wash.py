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
from csv_parser import RealizedEvent, parse_labeled_exports, transactions_to_tax_lots
from harvesting import _compute_realized_gains, generate_suggestions
from ledger import SAMPLE_FIXTURE_PRICES
from lot_matcher import match_1099b_lots
from models import (
    AssetType,
    Form1099BLot,
    TaxLot,
    TaxProfile,
    TransCode,
    Transaction,
)
from wash_sale import adjust_lots_for_wash_sales, detect_wash_sales
from wash_sale_chain import build_wash_sale_chains

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
    assert chain.plain_english == (
        "Not on your 1099: cross-account. "
        "An Individual sale of 4 QQQ on Feb 10 lost $120. "
        "A buy in an IRA (traditional) within 30 days before or after that sale "
        "covers 4 shares. "
        "Estimated disallowed amount $120. "
        "Permanent: a loss washed by an IRA buy is lost for good. "
        "It does not add to the IRA's basis."
    )
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


def test_same_day_buy_in_another_account_is_flagged():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 4, 500, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 10), 4, 480, "ira_roth"),
    ]
    chains = build_cross_account_chains(transactions, [], tax_year=2026)
    assert len(chains) == 1
    assert chains[0].permanent_disallowance is True
    assert chains[0].hops[0].disallowed_loss == pytest.approx(120)
    assert chains[0].hops[0].replacement_buys[0].activity_date == date(2026, 2, 10)


def test_one_replacement_buy_is_not_reused_by_a_later_sale():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 8, 500, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 11), 4, 470, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 4, 480, "ira_traditional"),
    ]
    flags = detect_wash_sales(transactions, tax_year=2026)
    chains = build_cross_account_chains(transactions, flags, tax_year=2026)
    assert len(chains) == 1
    assert chains[0].hops[0].sale_date == date(2026, 2, 10)
    assert chains[0].hops[0].disallowed_loss == pytest.approx(120)
    assert sum(chain.hops[0].disallowed_loss for chain in chains) == pytest.approx(120)


def test_ira_internal_wash_does_not_enter_flags_or_basis():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 4, 500, "ira_roth"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "ira_roth"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 4, 480, "ira_roth"),
    ]
    flags = detect_wash_sales(transactions, tax_year=2026)
    assert flags == []
    lots = transactions_to_tax_lots(transactions)[0]
    adjusted = adjust_lots_for_wash_sales(lots, flags)
    ira = next(lot for lot in adjusted if lot.symbol == "QQQ")
    assert ira.wash_sale_disallowed == 0
    assert ira.total_cost_basis == pytest.approx(1920)


def test_ordinary_chain_ignores_a_buy_in_another_account():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 4, 500, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 15), 4, 480, "ira_roth"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 4, 490, "individual"),
    ]
    flags = detect_wash_sales(transactions, tax_year=2026)
    assert len(flags) == 1
    assert flags[0].account_label == "individual"
    chains = build_wash_sale_chains(
        transactions=transactions,
        wash_sale_flags=flags,
        realized_events=[],
        tax_lots=[],
        supplemental=None,
        lot_match_report=None,
        tax_year=2026,
    )
    ordinary = [chain for chain in chains if not chain.cross_account]
    assert len(ordinary) == 1
    buys = ordinary[0].hops[0].replacement_buys
    assert [buy.activity_date for buy in buys] == [date(2026, 2, 20)]


def test_ira_lot_is_not_a_harvest_candidate():
    ira = TaxLot(
        symbol="QQQ",
        quantity=4,
        cost_basis_per_share=500,
        total_cost_basis=2000,
        purchase_date=date(2026, 1, 6),
        current_price=400,
        unrealized_pnl=-400,
        account_label="ira_roth",
    )
    taxable = TaxLot(
        symbol="AAPL",
        quantity=1,
        cost_basis_per_share=100,
        total_cost_basis=100,
        purchase_date=date(2026, 1, 6),
        current_price=80,
        unrealized_pnl=-20,
        account_label="individual",
    )
    kept, warnings = main._filter_suggestion_tax_lots([ira, taxable], [_txn(
        "AAPL", TransCode.BUY, date(2026, 1, 6), 1, 100, "individual",
    )])
    assert [lot.symbol for lot in kept] == ["AAPL"]
    assert any("IRA" in warning for warning in warnings)
    suggestions = generate_suggestions(kept, [], TaxProfile(), None)
    assert all(item.symbol != "QQQ" for item in suggestions)


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
    assert "An Individual sale of 4 QQQ" in cross[0]["plain_english"]
    assert "A buy in an IRA (traditional)" in cross[0]["plain_english"]
    assert (
        "Permanent: a loss washed by an IRA buy is lost for good. "
        "It does not add to the IRA's basis."
    ) in cross[0]["plain_english"]
    assert "A Individual" not in cross[0]["plain_english"]
    assert "may apply, ask a tax pro" not in cross[0]["plain_english"]
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


def test_custom_export_beside_the_sample_does_not_unlock_chains(monkeypatch):
    monkeypatch.setattr("main.fetch_current_prices", lambda symbols, fb=None, allow_network=True: ({}, []))
    monkeypatch.setattr("main.fetch_option_prices", lambda labels, fb=None, allow_network=True: ({}, []))
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda lots: [])
    monkeypatch.setattr("main._save_history_best_effort", lambda *args, **kwargs: None)
    main.reset_guest_analyze_quota()
    custom = (
        "Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount\n"
        "01/06/2026,01/06/2026,01/08/2026,XYZ,Custom,Buy,4,100.00,-400.00\n"
        "02/10/2026,02/10/2026,02/12/2026,XYZ,Custom,Sell,4,70.00,280.00\n"
        "02/20/2026,02/20/2026,02/24/2026,XYZ,Custom,Buy,4,80.00,-320.00\n"
    )
    client = TestClient(main.app)
    response = client.post(
        "/api/portfolio/analyze?filing_status=single&estimated_income=75000&tax_year=2026",
        files=[
            ("file", ("sample-robinhood-transactions.csv", SAMPLE_CSV.read_bytes(), "text/csv")),
            ("extra_files", ("custom.csv", custom.encode(), "text/csv")),
            ("supplemental_1099", ("sample-robinhood-1099-2026.pdf", SAMPLE_1099.read_bytes(), "application/pdf")),
            ("account_labels", (None, "individual")),
            ("account_labels", (None, "other")),
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("sample_run") is not True
    assert body["wash_sale_chains"] == []
    assert any(flag["symbol"] == "XYZ" for flag in body["wash_sale_flags"])


def test_exports_past_the_cap_are_rejected_before_they_are_read(monkeypatch):
    reads: list[str] = []

    async def _spy(file, guest=False):
        reads.append(file.filename or "")
        return b"Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount\n"

    monkeypatch.setattr(main, "_read_csv_upload", _spy)
    main.reset_guest_analyze_quota()
    files = [("file", ("primary.csv", b"x", "text/csv"))]
    files.extend(
        ("extra_files", (f"extra-{index}.csv", b"x", "text/csv"))
        for index in range(6)
    )
    response = TestClient(main.app).post("/api/portfolio/analyze", files=files)
    assert response.status_code == 400
    assert "at most 6" in response.json()["detail"]["message"].lower()
    assert reads == ["primary.csv"]


def test_prior_year_sale_is_consumed_before_the_tax_year_loss():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2024, 6, 1), 4, 500, "individual"),
        _txn("QQQ", TransCode.SELL, date(2025, 6, 1), 4, 400, "individual"),
        _txn("QQQ", TransCode.BUY, date(2025, 11, 1), 4, 400, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 4, 480, "ira_traditional"),
    ]
    assert build_cross_account_chains(transactions, [], tax_year=2026) == []


def test_same_account_replacement_is_not_reused_across_accounts():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 1), 4, 500, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 1, 1), 4, 500, "other"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 15), 4, 470, "other"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 6, 480, "other"),
    ]
    flags = detect_wash_sales(transactions, tax_year=2026)
    assert len(flags) == 1
    assert flags[0].account_label == "other"
    assert flags[0].repurchase_quantity == pytest.approx(4)
    chains = build_cross_account_chains(transactions, flags, tax_year=2026)
    assert len(chains) == 1
    assert chains[0].hops[0].account_label == "individual"
    assert chains[0].hops[0].disallowed_loss == pytest.approx(60)
    assert chains[0].hops[0].replacement_buys[0].allocated_quantity == pytest.approx(2)


def test_later_same_account_window_buy_is_not_reused_across_accounts():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 1), 4, 500, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 1, 1), 4, 500, "other"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "other"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 12), 2, 480, "other"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 15), 4, 470, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 4, 480, "other"),
    ]
    flags = detect_wash_sales(transactions, tax_year=2026)
    assert len(flags) == 1
    assert flags[0].account_label == "other"
    assert flags[0].repurchase_date == date(2026, 2, 12)
    assert flags[0].repurchase_quantity == pytest.approx(4)
    chains = build_cross_account_chains(transactions, flags, tax_year=2026)
    assert len(chains) == 1
    assert chains[0].hops[0].account_label == "individual"
    assert chains[0].hops[0].disallowed_loss == pytest.approx(60)
    assert chains[0].hops[0].replacement_buys[0].activity_date == date(2026, 2, 20)
    assert chains[0].hops[0].replacement_buys[0].allocated_quantity == pytest.approx(2)


def test_ira_loss_sale_does_not_become_a_cross_account_chain():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 4, 500, "ira_roth"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "ira_roth"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 4, 480, "individual"),
    ]
    assert build_cross_account_chains(transactions, [], tax_year=2026) == []


def test_mixed_replacement_keeps_basis_on_the_taxable_buy():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 4, 500, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 12), 2, 480, "other"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 18), 2, 470, "ira_traditional"),
    ]
    chains = build_cross_account_chains(transactions, [], tax_year=2026)
    assert len(chains) == 1
    hop = chains[0].hops[0]
    by_account = {buy.account_label: buy for buy in hop.replacement_buys}
    assert by_account["other"].explained_basis == pytest.approx(1020)
    assert by_account["ira_traditional"].explained_basis == pytest.approx(940)
    assert chains[0].permanent_disallowance is True
    assert "An Individual sale" in chains[0].plain_english
    assert "an Other/agentic" in chains[0].plain_english
    assert "an IRA (traditional)" in chains[0].plain_english
    assert "Permanent: a loss washed by an IRA buy is lost for good." in chains[0].plain_english
    assert "It does not add to the IRA's basis." in chains[0].plain_english
    assert "$60 is added to the taxable replacement's basis" in chains[0].plain_english


def test_identical_sales_do_not_each_consume_every_same_account_flag():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 8, 500, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 2, 480, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 8, 480, "ira_roth"),
    ]
    flags = detect_wash_sales(transactions, tax_year=2026)
    assert len(flags) == 2
    assert [flag.disallowed_loss for flag in flags] == pytest.approx([60, 60])
    chains = build_cross_account_chains(transactions, flags, tax_year=2026)
    assert len(chains) == 2
    assert sum(chain.hops[0].disallowed_loss for chain in chains) == pytest.approx(120)


def test_chain_closed_lot_stays_in_the_flag_account():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 4, 500, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 4, 470, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 2, 20), 4, 480, "individual"),
    ]
    flags = detect_wash_sales(transactions, tax_year=2026)
    events = [
        RealizedEvent(
            sale_date=date(2026, 2, 10),
            symbol="QQQ",
            quantity=4,
            cost_basis=2000,
            sale_proceeds=1880,
            pnl=-120,
            is_long_term=False,
            purchase_date=date(2026, 1, 6),
            account_label="individual",
        ),
        RealizedEvent(
            sale_date=date(2026, 2, 10),
            symbol="QQQ",
            quantity=4,
            cost_basis=2000,
            sale_proceeds=1880,
            pnl=-120,
            is_long_term=False,
            purchase_date=date(2026, 1, 7),
            account_label="ira_traditional",
        ),
    ]
    chains = build_wash_sale_chains(
        transactions=transactions,
        wash_sale_flags=flags,
        realized_events=events,
        tax_lots=[],
        supplemental=None,
        lot_match_report=None,
        tax_year=2026,
    )
    hop = chains[0].hops[0]
    assert [lot.purchase_date for lot in hop.closed_lots] == [date(2026, 1, 6)]


def test_ira_realized_loss_stays_out_of_taxable_totals_and_1099():
    transactions = [
        _txn("AAPL", TransCode.BUY, date(2026, 1, 6), 1, 10, "individual"),
        _txn("AAPL", TransCode.SELL, date(2026, 2, 10), 1, 20, "individual"),
        _txn("QQQ", TransCode.BUY, date(2026, 1, 6), 1, 50, "ira_roth"),
        _txn("QQQ", TransCode.SELL, date(2026, 2, 10), 1, 40, "ira_roth"),
    ]
    _lots, _warnings, realized = transactions_to_tax_lots(transactions)
    assert {event.account_label for event in realized} == {"individual", "ira_roth"}
    summary = main._compute_realized_summary(realized, 2026)
    assert summary.total_net == pytest.approx(10)
    assert summary.st_losses == pytest.approx(0)
    report = match_1099b_lots(
        [
            Form1099BLot(
                symbol="QQQ",
                quantity=1,
                date_sold=date(2026, 2, 10),
                proceeds=40,
                cost_basis=50,
                term="short",
            )
        ],
        realized,
        form_1099_tax_year=2026,
        analysis_tax_year=2026,
        short_term_proceeds=40,
        short_term_cost_basis=50,
    )
    assert report is not None
    assert report.matched_count == 0
    assert not any(
        row.symbol == "QQQ" and row.status == "csv_only" for row in report.unmatched
    )


def test_harvest_gains_do_not_consume_another_accounts_shares():
    transactions = [
        _txn("QQQ", TransCode.BUY, date(2026, 1, 2), 100, 100, "other"),
        _txn("QQQ", TransCode.BUY, date(2026, 6, 1), 100, 10, "individual"),
        _txn("QQQ", TransCode.SELL, date(2026, 6, 2), 100, 20, "individual"),
    ]
    assert _compute_realized_gains(transactions, tax_year=2026) == pytest.approx(1000)
    lots = [
        TaxLot(
            symbol=symbol,
            quantity=10,
            cost_basis_per_share=200,
            total_cost_basis=2000,
            purchase_date=date(2026, 1, 6),
            current_price=50,
            unrealized_pnl=-1500,
            account_label="individual",
        )
        for symbol in ("AAPL", "MSFT", "NVDA")
    ]
    suggestions = generate_suggestions(lots, transactions, TaxProfile(), None)
    assert [item.symbol for item in suggestions if item.action == "SELL"] == [
        "AAPL",
        "MSFT",
        "NVDA",
    ]


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
