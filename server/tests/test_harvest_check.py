"""Year-end harvest check. Display only: it does not change the $924 wash total."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from csv_parser import parse_csv
from harvest_check import (
    SAMPLE_HARVEST_AS_OF,
    build_harvest_checks,
    redact_harvest_checks,
)
from harvesting import compute_lot_metrics
from ledger import SAMPLE_FIXTURE_PRICES
from models import AssetType, HarvestCheck, PortfolioAnalysis, TaxLot, Transaction, TransCode

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_CSV = REPO_ROOT / "client" / "public" / "sample-robinhood-transactions.csv"
SAMPLE_1099 = REPO_ROOT / "client" / "public" / "sample-robinhood-1099-2026.pdf"
AS_OF = date(2026, 8, 12)


def _lot(
    symbol: str,
    quantity: float,
    cost: float,
    price: float,
    purchased: date,
    *,
    account: str | None = None,
) -> TaxLot:
    loss = round((price - cost) * quantity, 2)
    return TaxLot(
        symbol=symbol,
        quantity=quantity,
        cost_basis_per_share=cost,
        total_cost_basis=round(cost * quantity, 2),
        purchase_date=purchased,
        current_price=price,
        unrealized_pnl=loss,
        account_label=account,
    )


def _buy(
    symbol: str,
    quantity: float,
    price: float,
    purchased: date,
    description: str = "",
    *,
    code: TransCode = TransCode.BUY,
    asset: AssetType = AssetType.STOCK,
    account: str | None = None,
) -> Transaction:
    return Transaction(
        activity_date=purchased,
        instrument=symbol,
        description=description,
        trans_code=code,
        quantity=quantity,
        price=price,
        amount=-(quantity * price),
        asset_type=asset,
        account_label=account,
    )


def _sofi_book() -> tuple[list[TaxLot], list[Transaction]]:
    lots = [
        _lot("SOFI", 10, 20, 10, date(2026, 6, 1)),
        _lot("SOFI", 2, 18, 10, date(2026, 8, 1)),
        _lot("SOFI", 0.5, 12, 10, date(2026, 8, 5)),
    ]
    txns = [
        _buy("SOFI", 10, 20, date(2026, 6, 1), "SoFi Technologies"),
        _buy("SOFI", 2, 18, date(2026, 8, 1), "Recurring investment"),
        _buy("SOFI", 0.5, 12, date(2026, 8, 5), "Dividend Reinvestment"),
        _buy(
            "SOFI",
            1,
            1.5,
            date(2026, 8, 10),
            "SOFI 12/18/2026 Call $15.00",
            code=TransCode.BTO,
            asset=AssetType.OPTION,
        ),
    ]
    return lots, txns


def test_sample_shape_disallows_the_june_lot_and_names_each_trap():
    lots, txns = _sofi_book()
    checks = build_harvest_checks(lots, txns, as_of=AS_OF)
    row = checks[0]
    assert row.symbol == "SOFI"
    assert row.clean is False
    assert row.open_loss == pytest.approx(100)
    assert row.disallowed_loss == pytest.approx(100)
    assert row.line == "$100 of your $100 loss may be disallowed"
    assert row.earliest_clean_sell_date == date(2026, 9, 10)
    assert row.safe_rebuy_date == date(2026, 10, 11)
    assert [(item.kind, item.disallowed_loss) for item in row.triggers] == [
        ("recurring_buy", 20),
        ("dividend_reinvestment", 5),
        ("bought_call", 75),
    ]
    assert "sell" not in row.line.lower()
    assert "harvest" not in row.line.lower()


def test_clean_when_nothing_sits_in_the_window():
    lots = [_lot("NVDA", 10, 50, 40, date(2026, 5, 1))]
    txns = [_buy("NVDA", 10, 50, date(2026, 5, 1))]
    row = build_harvest_checks(lots, txns, as_of=AS_OF)[0]
    assert row.clean is True
    assert row.trigger_count == 0
    assert row.line == (
        "No replacement buys found in the 30 days either side; "
        "may be clean to harvest."
    )
    assert row.earliest_clean_sell_date is None


def test_sgov_does_not_replace_another_ticker_and_is_checked_on_its_own():
    lots = [
        _lot("AAPL", 10, 50, 40, date(2026, 5, 1)),
        _lot("SGOV", 10, 100, 90, date(2026, 5, 1)),
    ]
    txns = [
        _buy("AAPL", 10, 50, date(2026, 5, 1)),
        _buy("SGOV", 10, 100, date(2026, 5, 1)),
        _buy("SGOV", 2, 100, date(2026, 8, 1), "Recurring investment"),
    ]
    rows = {row.symbol: row for row in build_harvest_checks(lots, txns, as_of=AS_OF)}
    assert rows["AAPL"].clean is True
    assert rows["AAPL"].trigger_count == 0
    assert rows["SGOV"].clean is False
    assert rows["SGOV"].triggers[0].kind == "recurring_buy"
    assert rows["SGOV"].disallowed_loss == pytest.approx(20)


def test_only_a_bought_call_or_a_short_put_on_the_same_stock_counts():
    lots = [_lot("AMD", 10, 20, 10, date(2026, 5, 1))]
    txns = [
        _buy("AMD", 10, 20, date(2026, 5, 1)),
        _buy(
            "AMD",
            1,
            2,
            date(2026, 8, 2),
            "AMD 9/18/2026 Put $15.00",
            code=TransCode.BTO,
            asset=AssetType.OPTION,
        ),
        _buy(
            "AMD",
            1,
            2,
            date(2026, 8, 3),
            "AMD 9/18/2026 Call $15.00",
            code=TransCode.STO,
            asset=AssetType.OPTION,
        ),
        _buy(
            "NVDA",
            1,
            3,
            date(2026, 8, 4),
            "NVDA 9/18/2026 Call $100.00",
            code=TransCode.BTO,
            asset=AssetType.OPTION,
        ),
        _buy(
            "AMD",
            1,
            1,
            date(2026, 8, 5),
            "AMD 9/18/2026 Put $12.00",
            code=TransCode.STO,
            asset=AssetType.OPTION,
        ),
    ]
    row = build_harvest_checks(lots, txns, as_of=AS_OF)[0]
    assert [item.kind for item in row.triggers] == ["short_put"]
    assert row.disallowed_loss == pytest.approx(100)


def test_buy_31_days_before_is_outside_and_30_days_before_is_inside():
    lots = [_lot("MSFT", 10, 20, 10, date(2026, 1, 2))]
    inside = _buy("MSFT", 1, 15, date(2026, 7, 13), "Fractional")
    inside = inside.model_copy(update={"quantity": 1.5, "description": "MSFT"})
    outside = _buy("MSFT", 4, 15, date(2026, 7, 12))
    txns = [
        _buy("MSFT", 10, 20, date(2026, 1, 2)),
        outside,
        inside,
    ]
    row = build_harvest_checks(lots, txns, as_of=AS_OF)[0]
    assert row.trigger_count == 1
    assert row.triggers[0].kind == "fractional_buy"
    assert row.triggers[0].activity_date == date(2026, 7, 13)


def test_same_day_buy_is_kept_only_when_it_is_another_account():
    lots = [_lot("QQQ", 10, 20, 10, date(2026, 1, 2), account="individual")]
    txns = [
        _buy("QQQ", 10, 20, date(2026, 1, 2), account="individual"),
        _buy("QQQ", 1, 12, AS_OF, account="individual"),
        _buy("QQQ", 2, 12, AS_OF, account="ira_roth"),
    ]
    row = build_harvest_checks(lots, txns, as_of=AS_OF)[0]
    assert row.trigger_count == 1
    assert row.triggers[0].permanent_disallowance is True
    assert row.permanent_disallowance is True
    assert row.triggers[0].account_name == "IRA (Roth)"
    assert row.disallowed_loss == pytest.approx(20)


def test_cross_account_taxable_buy_counts_and_is_not_permanent():
    lots = [_lot("META", 10, 20, 10, date(2026, 1, 2), account="individual")]
    txns = [
        _buy("META", 10, 20, date(2026, 1, 2), account="individual"),
        _buy("META", 4, 12, date(2026, 8, 1), account="other"),
    ]
    row = build_harvest_checks(lots, txns, as_of=AS_OF)[0]
    assert row.triggers[0].account_name == "Other/agentic"
    assert row.triggers[0].permanent_disallowance is False
    assert row.disallowed_loss == pytest.approx(40)


def test_ira_loss_is_not_a_harvest_row():
    lots = [_lot("QQQ", 4, 20, 10, date(2026, 1, 2), account="ira_traditional")]
    txns = [_buy("QQQ", 4, 20, date(2026, 1, 2), account="ira_traditional")]
    assert build_harvest_checks(lots, txns, as_of=AS_OF) == []


def test_tsla_and_spcx_lines_do_not_prompt_a_sale():
    lots = [
        _lot("TSLA", 4, 20, 10, date(2026, 1, 2)),
        _lot("SPCX", 4, 20, 10, date(2026, 1, 2)),
    ]
    txns = [
        _buy("TSLA", 4, 20, date(2026, 1, 2)),
        _buy("TSLA", 1, 12, date(2026, 8, 1)),
        _buy("SPCX", 4, 20, date(2026, 1, 2)),
    ]
    rows = {row.symbol: row for row in build_harvest_checks(lots, txns, as_of=AS_OF)}
    tsla = rows["TSLA"]
    assert tsla.clean is False
    assert tsla.earliest_clean_sell_date is None
    assert tsla.safe_rebuy_date is None
    assert "sell" not in tsla.line.lower()
    assert "harvest" not in tsla.line.lower()
    assert "ask a tax pro" in tsla.line
    spcx = rows["SPCX"]
    assert spcx.clean is True
    assert "harvest" not in spcx.line.lower()
    assert "ask a tax pro" in spcx.line


def test_a_recent_lot_is_not_a_wash_against_its_own_purchase():
    lots = [_lot("SHOP", 5, 20, 10, date(2026, 8, 1))]
    txns = [_buy("SHOP", 5, 20, date(2026, 8, 1))]
    row = build_harvest_checks(lots, txns, as_of=AS_OF)[0]
    assert row.clean is True
    assert row.trigger_count == 0


def test_redact_keeps_the_count_and_drops_dollars_and_dates():
    lots, txns = _sofi_book()
    full = build_harvest_checks(lots, txns, as_of=AS_OF)
    hidden = redact_harvest_checks(full)[0]
    assert hidden.trigger_count == 3
    assert hidden.clean is False
    assert hidden.symbol == "SOFI"
    assert hidden.line == ""
    assert hidden.open_loss is None
    assert hidden.disallowed_loss is None
    assert hidden.earliest_clean_sell_date is None
    assert hidden.triggers == []
    assert hidden.permanent_disallowance is False


def test_public_analysis_gate_matches_sample_and_season_unlock():
    lots, txns = _sofi_book()
    checks = build_harvest_checks(lots, txns, as_of=AS_OF)
    analysis = PortfolioAnalysis(harvest_checks=checks)
    stripped = main._public_analysis(analysis)
    assert stripped.harvest_checks[0].line == ""
    assert stripped.harvest_checks[0].trigger_count == 3

    opened = main._public_analysis(analysis, filing_detail=True)
    assert opened.harvest_checks[0].disallowed_loss == pytest.approx(100)
    assert opened.season_detail is True

    closed = main._public_analysis(
        analysis.model_copy(update={"packet_unlocked": True}),
        filing_detail=False,
    )
    assert closed.harvest_checks[0].disallowed_loss is None
    assert closed.season_detail is False

    sample = main._public_analysis(
        analysis.model_copy(update={"sample_run": True}),
        keep_lot_rows=True,
    )
    assert sample.harvest_checks[0].line.startswith("$100 of your $100")


def _stub_prices(monkeypatch):
    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None, allow_network=True: (
            {
                symbol.upper(): SAMPLE_FIXTURE_PRICES[symbol.upper()]
                for symbol in symbols
            },
            [],
        ),
    )
    monkeypatch.setattr(
        "main.fetch_option_prices",
        lambda labels, fb=None, allow_network=True: ({}, []),
    )
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda lots: [])
    monkeypatch.setattr("main._save_history_best_effort", lambda *args, **kwargs: None)


def test_sample_analyze_keeps_924_amd_chain_and_shows_sofi(monkeypatch):
    _stub_prices(monkeypatch)
    main.reset_guest_analyze_quota()
    client = TestClient(main.app)
    response = client.post(
        "/api/portfolio/analyze?filing_status=single&estimated_income=75000&tax_year=2026",
        files={
            "file": (
                "sample-robinhood-transactions.csv",
                SAMPLE_CSV.read_bytes(),
                "text/csv",
            ),
            "supplemental_1099": (
                "sample-robinhood-1099-2026.pdf",
                SAMPLE_1099.read_bytes(),
                "application/pdf",
            ),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    flags = body["wash_sale_flags"]
    assert round(sum(float(flag["disallowed_loss"]) for flag in flags), 2) == 924
    assert {flag["symbol"] for flag in flags} == {"AMD", "NVDA", "TSLA"}
    amd = next(chain for chain in body["wash_sale_chains"] if chain["symbol"] == "AMD")
    assert amd["hops"][0]["replacement_buys"][0]["activity_date"] == "2026-07-24"
    assert amd["hops"][0]["disallowed_loss"] == pytest.approx(300)
    sell_prompts = [
        row["symbol"]
        for row in body["suggestions"]
        if row.get("action") == "SELL"
    ]
    assert "TSLA" not in sell_prompts
    assert "SPCX" not in sell_prompts

    sofi = next(row for row in body["harvest_checks"] if row["symbol"] == "SOFI")
    assert sofi["as_of"] == SAMPLE_HARVEST_AS_OF.isoformat()
    assert sofi["open_loss"] == pytest.approx(100)
    assert sofi["disallowed_loss"] == pytest.approx(100)
    assert sofi["line"] == "$100 of your $100 loss may be disallowed"
    assert sofi["earliest_clean_sell_date"] == "2026-09-10"
    assert sofi["safe_rebuy_date"] == "2026-10-11"
    assert [item["kind"] for item in sofi["triggers"]] == [
        "recurring_buy",
        "dividend_reinvestment",
        "bought_call",
    ]
    assert [item["disallowed_loss"] for item in sofi["triggers"]] == [20, 5, 75]

    tsla = next(row for row in body["harvest_checks"] if row["symbol"] == "TSLA")
    assert tsla["clean"] is True
    assert "harvest" not in tsla["line"].lower()
    assert "sell" not in tsla["line"].lower()
    assert all(row["symbol"] != "SGOV" for row in body["harvest_checks"])


def test_custom_csv_harvest_detail_stays_counted_until_season_unlock(monkeypatch):
    monkeypatch.setattr("main.harvest_as_of", lambda _sample: date(2026, 8, 12))
    _stub_prices(monkeypatch)
    main.reset_guest_analyze_quota()
    csv_text = (
        "Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount\n"
        "06/01/2026,06/01/2026,06/03/2026,SOFI,SoFi Technologies,Buy,10,20.00,-200.00\n"
        "08/01/2026,08/01/2026,08/04/2026,SOFI,Recurring investment,Buy,2,18.00,-36.00\n"
    )
    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None, allow_network=True: (
            {symbol.upper(): 10.0 for symbol in symbols},
            [],
        ),
    )
    client = TestClient(main.app)
    response = client.post(
        "/api/portfolio/analyze?tax_year=2026",
        files={"file": ("book.csv", csv_text.encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    row = body["harvest_checks"][0]
    assert row["symbol"] == "SOFI"
    assert row["trigger_count"] == 1
    assert row["line"] == ""
    assert row["disallowed_loss"] is None
    assert row["triggers"] == []
    assert "season_detail" not in body


def test_parsed_sample_rows_feed_the_same_check():
    _lots, transactions, _warnings, _realized = parse_csv(SAMPLE_CSV.read_text(encoding="utf-8"))
    priced = []
    for txn in transactions:
        if txn.instrument != "SOFI" or txn.asset_type != AssetType.STOCK:
            continue
        if txn.trans_code != TransCode.BUY:
            continue
        lot = TaxLot(
            symbol="SOFI",
            quantity=txn.quantity,
            cost_basis_per_share=txn.price,
            total_cost_basis=txn.price * txn.quantity,
            purchase_date=txn.activity_date,
            current_price=10,
        )
        priced.append(lot)
    priced = compute_lot_metrics(priced, reference_date=SAMPLE_HARVEST_AS_OF)
    rows = build_harvest_checks(priced, transactions, as_of=SAMPLE_HARVEST_AS_OF)
    sofi = next(row for row in rows if row.symbol == "SOFI")
    assert isinstance(sofi, HarvestCheck)
    assert sofi.disallowed_loss == pytest.approx(100)
    assert sofi.earliest_clean_sell_date == date(2026, 9, 10)
