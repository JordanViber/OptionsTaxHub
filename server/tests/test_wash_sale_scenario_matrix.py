"""JOR-206 scenario matrix. Each row id is one parametrized case."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main
from cross_account_wash import build_cross_account_chains, stamp_chain_accounts
from csv_parser import parse_labeled_exports, transactions_to_tax_lots
from ledger import SAMPLE_FIXTURE_PRICES
from models import (
    AssetType,
    LotMatchReport,
    LotMatchRow,
    Supplemental1099Summary,
    TransCode,
    Transaction,
    WashSaleFlag,
)
from wash_sale import detect_wash_sales
from wash_sale_chain import build_wash_sale_chains

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = Path(__file__).parent / "fixtures" / "wash-sale-scenario-matrix.json"
MATRIX = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
ROWS = MATRIX["rows"]


def _date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _txn(item: dict) -> Transaction:
    side = item["side"]
    if side == "buy":
        code = TransCode.BUY
    elif side == "sell":
        code = TransCode.SELL
    elif side == "bto":
        code = TransCode.BTO
    else:
        raise AssertionError(f"unknown side {side}")
    quantity = float(item["qty"])
    price = float(item["price"])
    signed = -1 if code in (TransCode.BUY, TransCode.BTO) else 1
    return Transaction(
        activity_date=_date(item["date"]),
        process_date=_date(item["date"]),
        settle_date=_date(item["date"]),
        instrument=item["symbol"],
        description=item.get("description") or item["symbol"],
        trans_code=code,
        quantity=quantity,
        price=price,
        amount=signed * quantity * price,
        asset_type=AssetType.STOCK,
        account_label=item.get("account_label"),
    )


def _transactions(row: dict) -> list[Transaction]:
    return [_txn(item) for item in row["inputs"].get("transactions") or []]


def _csv_path(path_str: str) -> Path:
    path = REPO_ROOT / path_str
    if path.is_file():
        return path
    fixture = Path(__file__).parent / "fixtures" / Path(path_str).name
    if fixture.is_file():
        return fixture
    return path


def _file_transactions(row: dict) -> tuple[list[Transaction], list]:
    inputs = row["inputs"]
    copies = inputs.get("fixture_copies")
    files = inputs["files"]
    if copies:
        paths = [_csv_path(path) for path in copies]
        labels = [item["label"] for item in files]
    else:
        paths = [_csv_path(item["path"]) for item in files]
        labels = [item["label"] for item in files]
    parts = [
        (path.read_text(encoding="utf-8"), label)
        for path, label in zip(paths, labels)
    ]
    _lots, transactions, errors, realized = parse_labeled_exports(parts)
    assert not any("Unrecognized" in error for error in errors)
    return transactions, realized


def _same_account_chains(transactions, flags, realized, *, supplemental=None, report=None):
    chains = build_wash_sale_chains(
        transactions=transactions,
        wash_sale_flags=flags,
        realized_events=realized,
        tax_lots=[],
        supplemental=supplemental,
        lot_match_report=report,
        tax_year=2026,
    )
    return stamp_chain_accounts(chains, transactions)


def _synthetic_flag(payload: dict) -> WashSaleFlag:
    return WashSaleFlag(
        symbol=payload["symbol"],
        sale_date=_date(payload["sale_date"]),
        sale_quantity=payload["sale_quantity"],
        sale_loss=payload["disallowed_loss"],
        repurchase_date=_date(payload["repurchase_date"]),
        repurchase_quantity=payload["repurchase_quantity"],
        disallowed_loss=payload["disallowed_loss"],
        adjusted_cost_basis=payload["adjusted_cost_basis"],
    )


def _s13_report() -> LotMatchReport:
    row = LotMatchRow(
        status="matched",
        symbol="AAPL",
        quantity=10,
        date_sold_1099=date(2026, 6, 3),
        export_trade_date=date(2026, 6, 1),
        proceeds_1099=800.0,
        cost_basis_1099=1000.0,
        wash_sale_disallowed=0.0,
    )
    return LotMatchReport(matched=[], gap=[row], unmatched=[])


def _near(actual, expected) -> bool:
    if isinstance(expected, bool) or expected is None:
        return actual == expected
    if isinstance(expected, (int, float)):
        return actual == pytest.approx(expected)
    return actual == expected


def _flag_matches(flag, expected: dict) -> bool:
    checks = {
        "symbol": flag.symbol,
        "sale_date": flag.sale_date.isoformat(),
        "sale_quantity": flag.sale_quantity,
        "disallowed_loss": flag.disallowed_loss,
    }
    for key in (
        "repurchase_date",
        "repurchase_quantity",
        "account_label",
        "adjusted_cost_basis",
    ):
        if key not in expected:
            continue
        if key == "repurchase_date":
            checks[key] = flag.repurchase_date.isoformat()
        elif key == "account_label":
            checks[key] = flag.account_label
        elif key == "repurchase_quantity":
            checks[key] = flag.repurchase_quantity
        else:
            checks[key] = flag.adjusted_cost_basis
    return all(_near(checks[key], expected[key]) for key in checks)


def _assert_flags(flags, expected: dict) -> None:
    items = expected.get("items") or []
    assert len(flags) == expected["count"]
    remaining = list(flags)
    for item in items:
        match = next((flag for flag in remaining if _flag_matches(flag, item)), None)
        assert match is not None, item
        remaining.remove(match)
    if "total_disallowed" in expected:
        total = round(sum(flag.disallowed_loss for flag in flags), 2)
        assert total == pytest.approx(expected["total_disallowed"])


def _buy_matches(buy, expected: dict) -> bool:
    if buy.activity_date.isoformat() != expected["activity_date"]:
        return False
    for key, actual in (
        ("allocated_quantity", buy.allocated_quantity),
        ("quantity", buy.quantity),
        ("account_label", buy.account_label),
        ("account_name", buy.account_name),
        ("allocated_disallowed", buy.allocated_disallowed),
        ("explained_basis", buy.explained_basis),
    ):
        if key in expected and not _near(actual, expected[key]):
            return False
    return True


def _chain_matches(chain, expected: dict) -> bool:
    hop = chain.hops[0]
    if chain.symbol != expected["symbol"]:
        return False
    if "sale_date" in expected and hop.sale_date.isoformat() != expected["sale_date"]:
        return False
    if "sale_quantity" in expected and not _near(hop.sale_quantity, expected["sale_quantity"]):
        return False
    if "disallowed_loss" in expected and not _near(hop.disallowed_loss, expected["disallowed_loss"]):
        return False
    pairs = (
        ("cross_account", chain.cross_account),
        ("permanent_disallowance", chain.permanent_disallowance),
        ("reconcile_status", chain.reconcile_status),
        ("not_compared_reason", hop.not_compared_reason),
        ("account_label", hop.account_label),
        ("account_name", hop.account_name),
        ("plain_english", chain.plain_english),
        ("original_purchase_date", hop.original_purchase_date.isoformat() if hop.original_purchase_date else None),
        ("proceeds", hop.proceeds),
        ("cost_basis", hop.cost_basis),
        ("engine_adjusted_cost_basis", hop.engine_adjusted_cost_basis),
        ("form_1099_wash", hop.form_1099_wash),
    )
    for key, actual in pairs:
        if key not in expected or expected[key] is None:
            continue
        if not _near(actual, expected[key]):
            return False
    if "allocated_shares" in expected:
        allocated = sum(buy.allocated_quantity for buy in hop.replacement_buys)
        if not _near(allocated, expected["allocated_shares"]):
            return False
    if expected.get("replacement_buys") == []:
        return hop.replacement_buys == []
    for buy_expected in expected.get("replacement_buys") or []:
        if not any(_buy_matches(buy, buy_expected) for buy in hop.replacement_buys):
            return False
    return True


def _assert_chains(actual, expected: list[dict]) -> None:
    if not expected:
        assert actual == []
        return
    remaining = list(actual)
    complete = all("sale_date" in item and "sale_quantity" in item for item in expected)
    for item in expected:
        match = next((chain for chain in remaining if _chain_matches(chain, item)), None)
        assert match is not None, item
        remaining.remove(match)
    if complete:
        assert remaining == []


def _assert_engine(row: dict, transactions, realized) -> None:
    tax_year = row["tax_year"]
    flags = detect_wash_sales(transactions, tax_year=tax_year)
    expected = row["expected"]
    if row["inputs"]["source"] == "transactions_plus_synthetic_flag":
        assert flags == []
        flags = [_synthetic_flag(row["inputs"]["synthetic_flag"])]
        supplemental = Supplemental1099Summary(tax_year=tax_year)
        report = _s13_report()
        same = _same_account_chains(
            transactions,
            flags,
            realized,
            supplemental=supplemental,
            report=report,
        )
    else:
        _assert_flags(flags, expected["same_account_flags"])
        same = _same_account_chains(transactions, flags, realized)
    cross_flags = [] if row["inputs"]["source"] == "transactions_plus_synthetic_flag" else flags
    cross = build_cross_account_chains(transactions, cross_flags, tax_year=tax_year)
    _assert_chains(same, expected["same_account_chains"])
    _assert_chains(cross, expected["cross_account_chains"])


def _analyze(monkeypatch, row: dict):
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
    files = []
    labeled = []
    for index, item in enumerate(row["inputs"]["files"]):
        path = _csv_path(item["path"])
        field = "file" if index == 0 else "extra_files"
        files.append((field, (path.name, path.read_bytes(), "text/csv")))
        labeled.append(item["label"])
    supplemental = row["inputs"].get("supplemental_1099")
    if supplemental:
        path = REPO_ROOT / supplemental
        files.append(("supplemental_1099", (path.name, path.read_bytes(), "application/pdf")))
    if any(label for label in labeled):
        for label in labeled:
            files.append(("account_labels", (None, label)))
    response = TestClient(main.app).post(
        "/api/portfolio/analyze?" + row["inputs"]["analyze_query"],
        files=files,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _assert_sample(body: dict, row: dict) -> None:
    expected = row["expected"]
    flags = body["wash_sale_flags"]
    assert len(flags) == expected["same_account_flags"]["count"]
    for item in expected["same_account_flags"]["items"]:
        assert any(
            flag["symbol"] == item["symbol"]
            and flag["sale_date"] == item["sale_date"]
            and _near(flag["sale_quantity"], item["sale_quantity"])
            and _near(flag["disallowed_loss"], item["disallowed_loss"])
            and flag["repurchase_date"] == item["repurchase_date"]
            and _near(flag["repurchase_quantity"], item["repurchase_quantity"])
            and flag.get("account_label") == item.get("account_label")
            for flag in flags
        ), item
    if "total_disallowed" in expected["same_account_flags"]:
        total = round(sum(float(flag["disallowed_loss"]) for flag in flags), 2)
        assert total == pytest.approx(expected["same_account_flags"]["total_disallowed"])
    chains = body["wash_sale_chains"]
    same = [chain for chain in chains if not chain.get("cross_account")]
    cross = [chain for chain in chains if chain.get("cross_account")]
    _assert_json_chains(same, expected["same_account_chains"])
    _assert_json_chains(cross, expected["cross_account_chains"])
    other = expected.get("other") or {}
    if "sample_run" in other:
        assert body["sample_run"] is other["sample_run"]
    if "cross_account_wash_count" in other:
        assert body["cross_account_wash_count"] == other["cross_account_wash_count"]
    if "season_detail" in other:
        assert body.get("season_detail") == other["season_detail"]
    if "errors" in other:
        assert body["errors"] == other["errors"]
    if "broker_wash" in other:
        form = body["supplemental_1099"]
        broker = round(
            form["short_term_wash_sale_disallowed"] + form["long_term_wash_sale_disallowed"],
            2,
        )
        assert broker == pytest.approx(other["broker_wash"])
    for symbol in other.get("no_sell_prompts_for") or []:
        prompted = [
            suggestion["symbol"]
            for suggestion in body["suggestions"]
            if suggestion.get("action") == "SELL"
        ]
        assert symbol not in prompted
    harvest = expected.get("harvest_checks")
    if not harvest:
        return
    rows = body["harvest_checks"]
    assert len(rows) == harvest["count"]
    assert {row["symbol"] for row in rows} == set(harvest["symbols"])
    for symbol in harvest["excluded_symbols"]:
        assert symbol not in {row["symbol"] for row in rows}
    sofi = next(row for row in rows if row["symbol"] == "SOFI")
    spec = harvest["sofi"]
    assert sofi["open_loss"] == pytest.approx(spec["open_loss"])
    assert sofi["disallowed_loss"] == pytest.approx(spec["disallowed_loss"])
    assert sofi["line"] == spec["line"]
    assert sofi["earliest_clean_sell_date"] == spec["earliest_clean_sell_date"]
    assert sofi["safe_rebuy_date"] == spec["safe_rebuy_date"]
    assert sofi["as_of"] == harvest["as_of"]
    assert [
        (item["kind"], item["disallowed_loss"]) for item in sofi["triggers"]
    ] == [(item["kind"], item["disallowed_loss"]) for item in spec["triggers"]]
    for spec_row in harvest["other_rows_confirmed_at_a4b8441"]:
        actual = next(row for row in rows if row["symbol"] == spec_row["symbol"])
        assert actual["open_loss"] == pytest.approx(spec_row["open_loss"])
        assert actual["disallowed_loss"] == pytest.approx(spec_row["disallowed_loss"])


def _assert_json_chains(actual: list[dict], expected: list[dict]) -> None:
    if not expected:
        assert actual == []
        return
    remaining = list(actual)
    complete = all("sale_date" in item and "sale_quantity" in item for item in expected)
    for item in expected:
        match = next((chain for chain in remaining if _json_chain_matches(chain, item)), None)
        assert match is not None, item
        remaining.remove(match)
    if complete:
        assert remaining == []


def _json_chain_matches(chain: dict, expected: dict) -> bool:
    hop = chain["hops"][0]
    if chain["symbol"] != expected["symbol"]:
        return False
    if "sale_date" in expected and hop["sale_date"] != expected["sale_date"]:
        return False
    if "disallowed_loss" in expected and not _near(hop["disallowed_loss"], expected["disallowed_loss"]):
        return False
    if "reconcile_status" in expected and chain["reconcile_status"] != expected["reconcile_status"]:
        return False
    if expected.get("plain_english") and chain["plain_english"] != expected["plain_english"]:
        return False
    if "permanent_disallowance" in expected and chain["permanent_disallowance"] is not expected["permanent_disallowance"]:
        return False
    if "cross_account" in expected and bool(chain.get("cross_account")) is not expected["cross_account"]:
        return False
    for buy_expected in expected.get("replacement_buys") or []:
        if not any(
            buy["activity_date"] == buy_expected["activity_date"]
            and all(
                key not in buy_expected or _near(buy.get(key), buy_expected[key])
                for key in (
                    "allocated_quantity",
                    "quantity",
                    "account_label",
                    "account_name",
                    "allocated_disallowed",
                    "explained_basis",
                )
            )
            for buy in hop["replacement_buys"]
        ):
            return False
    return True


@pytest.mark.parametrize("row", ROWS, ids=[row["id"] for row in ROWS])
def test_wash_sale_scenario_matrix(row, monkeypatch):
    assert len(ROWS) == 25
    source = row["inputs"]["source"]
    if row["inputs"].get("supplemental_1099", "") and str(
        row["inputs"].get("supplemental_1099")
    ).endswith(".pdf"):
        _assert_sample(_analyze(monkeypatch, row), row)
        return
    if source == "component_props":
        transactions = _transactions(row)
        _lots, _warnings, realized = transactions_to_tax_lots(transactions)
        flags = detect_wash_sales(transactions, tax_year=row["tax_year"])
        assert len(flags) == 1
        assert _flag_matches(flags[0], row["inputs"]["component_flags"][0])
        same = _same_account_chains(transactions, flags, realized)
        assert same[0].hops[0].not_compared_reason == "no_1099"
        assert same[0].hops[0].reconcile_status == "not_compared"
        return
    if source == "files":
        transactions, realized = _file_transactions(row)
    else:
        transactions = _transactions(row)
        _lots, _warnings, realized = transactions_to_tax_lots(transactions)
    _assert_engine(row, transactions, realized)
