"""
Tests for the CSV parser module.

Validates CSV format auto-detection, Robinhood CSV parsing,
simplified CSV parsing, and FIFO tax lot aggregation.
"""

import sys
import os
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from csv_parser import (
    detect_csv_format,
    parse_robinhood_csv,
    parse_simple_csv,
    transactions_to_tax_lots,
    parse_csv,
    parse_robinhood_amount,
    parse_robinhood_quantity,
)


# --- Format Detection Tests ---

class TestDetectCsvFormat:
    def test_detects_robinhood(self):
        df = pd.DataFrame(columns=[
            "Activity Date", "Process Date", "Settle Date",
            "Instrument", "Description", "Trans Code",
            "Quantity", "Price", "Amount",
        ])
        assert detect_csv_format(df) == "robinhood"

    def test_detects_simple(self):
        df = pd.DataFrame(columns=["symbol", "quantity", "purchase_price", "current_price"])
        assert detect_csv_format(df) == "simple"

    def test_unknown_format(self):
        df = pd.DataFrame(columns=["foo", "bar", "baz"])
        assert detect_csv_format(df) == "unknown"

    def test_robinhood_with_whitespace(self):
        df = pd.DataFrame(columns=[
            " Activity Date ", "Process Date", "Settle Date",
            "Instrument", "Description", "Trans Code",
            "Quantity", "Price", "Amount",
        ])
        assert detect_csv_format(df) == "robinhood"


# --- Robinhood CSV Parsing Tests ---

class TestParseRobinhoodCsv:
    def _make_df(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_parse_buy_transaction(self):
        rows = [{
            "Activity Date": "07/01/2025",
            "Process Date": "07/01/2025",
            "Settle Date": "07/03/2025",
            "Instrument": "AAPL",
            "Description": "Apple Inc",
            "Trans Code": "Buy",
            "Quantity": "10",
            "Price": "150.00",
            "Amount": "-1500.00",
        }]
        transactions, errors = parse_robinhood_csv(self._make_df(rows))
        assert len(errors) == 0
        assert len(transactions) == 1
        t = transactions[0]
        assert t.instrument == "AAPL"
        assert t.trans_code.value == "Buy"
        assert t.quantity == pytest.approx(10.0)
        assert t.price == pytest.approx(150.0)

    def test_parse_sell_transaction(self):
        rows = [{
            "Activity Date": "07/15/2025",
            "Process Date": "07/15/2025",
            "Settle Date": "07/17/2025",
            "Instrument": "TSLA",
            "Description": "Tesla",
            "Trans Code": "Sell",
            "Quantity": "5",
            "Price": "200.00",
            "Amount": "1000.00",
        }]
        transactions, errors = parse_robinhood_csv(self._make_df(rows))
        assert len(errors) == 0
        assert len(transactions) == 1
        assert transactions[0].trans_code.value == "Sell"

    def test_missing_activity_date_skipped(self):
        rows = [{
            "Activity Date": "",
            "Process Date": "",
            "Settle Date": "",
            "Instrument": "AAPL",
            "Description": "",
            "Trans Code": "Buy",
            "Quantity": "10",
            "Price": "150",
            "Amount": "-1500",
        }]
        transactions, errors = parse_robinhood_csv(self._make_df(rows))
        assert len(transactions) == 0
        assert len(errors) == 1
        assert "Activity Date" in errors[0]

    def test_invalid_trans_code_skipped(self):
        rows = [{
            "Activity Date": "07/01/2025",
            "Process Date": "07/01/2025",
            "Settle Date": "07/03/2025",
            "Instrument": "AAPL",
            "Description": "",
            "Trans Code": "INVALID",
            "Quantity": "10",
            "Price": "150",
            "Amount": "-1500",
        }]
        transactions, errors = parse_robinhood_csv(self._make_df(rows))
        assert len(transactions) == 0
        assert len(errors) == 1
        assert "Unknown Trans Code" in errors[0]

    def test_options_detection(self):
        rows = [{
            "Activity Date": "07/01/2025",
            "Process Date": "07/01/2025",
            "Settle Date": "07/03/2025",
            "Instrument": "AAPL",
            "Description": "",
            "Trans Code": "STO",
            "Quantity": "1",
            "Price": "5.00",
            "Amount": "500",
        }]
        transactions, _ = parse_robinhood_csv(self._make_df(rows))
        assert len(transactions) == 1
        assert transactions[0].asset_type.value == "option"


# --- Simple CSV Parsing Tests ---

class TestParseSimpleCsv:
    def _make_df(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_parse_basic(self):
        rows = [
            {"symbol": "AAPL", "quantity": 10, "purchase_price": 150, "current_price": 160},
            {"symbol": "MSFT", "quantity": 5, "purchase_price": 300, "current_price": 310},
        ]
        lots, _ = parse_simple_csv(self._make_df(rows))
        # Messages include a warning about missing purchase_date column
        assert len(lots) == 2
        assert lots[0].symbol == "AAPL"
        assert lots[0].quantity == 10
        assert lots[0].cost_basis_per_share == 150
        assert lots[0].current_price == 160

    def test_missing_symbol_skipped(self):
        rows = [
            {"symbol": "", "quantity": 10, "purchase_price": 150, "current_price": 160},
        ]
        lots, errors = parse_simple_csv(self._make_df(rows))
        assert len(lots) == 0
        assert any("Missing symbol" in e for e in errors)

    def test_with_purchase_date(self):
        rows = [
            {"symbol": "AAPL", "quantity": 10, "purchase_price": 150,
             "current_price": 160, "purchase_date": "01/15/2024"},
        ]
        lots, _ = parse_simple_csv(self._make_df(rows))
        assert len(lots) == 1
        assert lots[0].purchase_date == date(2024, 1, 15)


# --- FIFO Tax Lot Aggregation Tests ---

class TestTransactionsToTaxLots:
    def test_buy_creates_lot(self):
        from models import Transaction, TransCode, AssetType
        txns = [
            Transaction(
                activity_date=date(2025, 1, 15),
                process_date=None,
                settle_date=None,
                instrument="AAPL",
                description="",
                trans_code=TransCode.BUY,
                quantity=10,
                price=150.0,
                amount=-1500.0,
                asset_type=AssetType.STOCK,
            ),
        ]
        lots, _ = transactions_to_tax_lots(txns)
        assert len(lots) == 1
        assert lots[0].symbol == "AAPL"
        assert lots[0].quantity == 10
        assert lots[0].cost_basis_per_share == pytest.approx(150.0)

    def test_sell_closes_fifo(self):
        from models import Transaction, TransCode, AssetType
        txns = [
            Transaction(
                activity_date=date(2025, 1, 15),
                process_date=None,
                settle_date=None,
                instrument="AAPL",
                description="",
                trans_code=TransCode.BUY,
                quantity=10,
                price=100.0,
                amount=-1000.0,
                asset_type=AssetType.STOCK,
            ),
            Transaction(
                activity_date=date(2025, 3, 1),
                process_date=None,
                settle_date=None,
                instrument="AAPL",
                description="",
                trans_code=TransCode.BUY,
                quantity=5,
                price=120.0,
                amount=-600.0,
                asset_type=AssetType.STOCK,
            ),
            Transaction(
                activity_date=date(2025, 6, 1),
                process_date=None,
                settle_date=None,
                instrument="AAPL",
                description="",
                trans_code=TransCode.SELL,
                quantity=12,
                price=130.0,
                amount=1560.0,
                asset_type=AssetType.STOCK,
            ),
        ]
        lots, _ = transactions_to_tax_lots(txns)
        # Sold 12: first 10 from lot 1, 2 from lot 2 → 3 left from lot 2
        assert len(lots) == 1
        assert lots[0].quantity == 3
        assert lots[0].cost_basis_per_share == pytest.approx(120.0)


# --- Full parse_csv Integration ---

class TestParseCsv:
    def test_simple_csv_integration(self):
        csv_text = "symbol,quantity,purchase_price,current_price\nAAPL,10,150,160\nMSFT,5,300,310\n"
        lots, transactions, _ = parse_csv(csv_text)
        assert len(lots) == 2
        assert len(transactions) == 0  # Simple CSV has no transactions

    def test_robinhood_csv_integration(self):
        csv_text = (
            "Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount\n"
            "07/01/2025,07/01/2025,07/03/2025,AAPL,Apple Inc,Buy,10,150.00,-1500.00\n"
        )
        lots, transactions, _ = parse_csv(csv_text)
        assert len(lots) == 1
        assert lots[0].symbol == "AAPL"
        assert len(transactions) == 1


# --- Robinhood Amount/Quantity Parsing ---


class TestParseRobinhoodAmount:
    def test_plain_number(self):
        assert parse_robinhood_amount("154.09") == pytest.approx(154.09)

    def test_dollar_prefix(self):
        assert parse_robinhood_amount("$7.70") == pytest.approx(7.70)

    def test_negative_parentheses(self):
        assert parse_robinhood_amount("($732.00)") == pytest.approx(-732.00)

    def test_dollar_comma_parentheses(self):
        assert parse_robinhood_amount("($2,440.10)") == pytest.approx(-2440.10)

    def test_positive_dollar_comma(self):
        assert parse_robinhood_amount("$6,261.50") == pytest.approx(6261.50)

    def test_empty_string(self):
        assert parse_robinhood_amount("") == pytest.approx(0.0)

    def test_none(self):
        assert parse_robinhood_amount(None) == pytest.approx(0.0)

    def test_nan_float(self):
        import math
        assert parse_robinhood_amount(float("nan")) == pytest.approx(0.0)

    def test_nan_string(self):
        assert parse_robinhood_amount("nan") == pytest.approx(0.0)

    def test_plain_negative(self):
        assert parse_robinhood_amount("-1500.00") == pytest.approx(-1500.00)


class TestParseRobinhoodQuantity:
    def test_plain_number(self):
        assert parse_robinhood_quantity("20") == pytest.approx(20.0)

    def test_s_suffix(self):
        assert parse_robinhood_quantity("400S") == pytest.approx(400.0)

    def test_lowercase_s(self):
        assert parse_robinhood_quantity("10s") == pytest.approx(10.0)

    def test_empty_string(self):
        assert parse_robinhood_quantity("") == pytest.approx(0.0)

    def test_none(self):
        assert parse_robinhood_quantity(None) == pytest.approx(0.0)

    def test_nan_float(self):
        import math
        assert parse_robinhood_quantity(float("nan")) == pytest.approx(0.0)

    def test_decimal_quantity(self):
        assert parse_robinhood_quantity("1.5") == pytest.approx(1.5)


# --- Real Robinhood CSV Patterns ---


class TestRealRobinhoodPatterns:
    def _make_df(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_dollar_price_and_parenthesized_amount(self):
        """Real Robinhood rows have $-prefixed prices and ()-wrapped negative amounts."""
        rows = [{
            "Activity Date": "02/12/2026",
            "Process Date": "02/12/2026",
            "Settle Date": "02/13/2026",
            "Instrument": "ALAB",
            "Description": "Astera Labs, Inc.",
            "Trans Code": "Buy",
            "Quantity": "5",
            "Price": "$146.40",
            "Amount": "($732.00)",
        }]
        transactions, errors = parse_robinhood_csv(self._make_df(rows))
        assert len(errors) == 0
        assert len(transactions) == 1
        assert transactions[0].price == pytest.approx(146.40)
        assert transactions[0].amount == pytest.approx(-732.00)
        assert transactions[0].quantity == pytest.approx(5.0)

    def test_quantity_with_s_suffix(self):
        """Robinhood uses 'S' suffix in Quantity for corporate actions like splits."""
        rows = [{
            "Activity Date": "02/06/2026",
            "Process Date": "02/06/2026",
            "Settle Date": "02/06/2026",
            "Instrument": "ASST",
            "Description": "Strive, Inc.",
            "Trans Code": "SPR",
            "Quantity": "400S",
            "Price": "",
            "Amount": "",
        }]
        transactions, errors = parse_robinhood_csv(self._make_df(rows))
        assert len(errors) == 0
        assert len(transactions) == 1
        assert transactions[0].quantity == pytest.approx(400.0)

    def test_option_assignment_parsed(self):
        """OASGN (option assignment) should be recognized as a valid TransCode."""
        rows = [{
            "Activity Date": "02/11/2026",
            "Process Date": "02/11/2026",
            "Settle Date": "02/12/2026",
            "Instrument": "UPXI",
            "Description": "UPXI 3/20/2026 Put $7.50",
            "Trans Code": "OASGN",
            "Quantity": "1",
            "Price": "",
            "Amount": "",
        }]
        transactions, errors = parse_robinhood_csv(self._make_df(rows))
        assert len(errors) == 0
        assert len(transactions) == 1
        assert transactions[0].trans_code.value == "OASGN"
        assert transactions[0].asset_type.value == "option"

    def test_account_activity_skipped(self):
        """ACH, RTP, FUTSWP, MINT, ROC rows should be silently skipped."""
        rows = [
            {
                "Activity Date": "02/02/2026", "Process Date": "02/02/2026",
                "Settle Date": "02/02/2026", "Instrument": "STRC",
                "Description": "Return of Capital", "Trans Code": "ROC",
                "Quantity": "", "Price": "", "Amount": "$9.17",
            },
        ]
        transactions, errors = parse_robinhood_csv(self._make_df(rows))
        assert len(transactions) == 0
        assert len(errors) == 0  # Should not produce error messages

    def test_empty_instrument_skipped_silently(self):
        """Rows with empty Instrument (ACH, transfers) should be skipped without errors."""
        rows = [{
            "Activity Date": "02/02/2026",
            "Process Date": "02/02/2026",
            "Settle Date": "02/03/2026",
            "Instrument": "",
            "Description": "ACH Deposit",
            "Trans Code": "ACH",
            "Quantity": "",
            "Price": "",
            "Amount": "$100.00",
        }]
        transactions, errors = parse_robinhood_csv(self._make_df(rows))
        assert len(transactions) == 0
        assert len(errors) == 0

    def test_options_sell_to_open_and_buy_to_close(self):
        """STO and BTC with dollar-prefixed prices should parse correctly."""
        rows = [
            {
                "Activity Date": "01/30/2026", "Process Date": "01/30/2026",
                "Settle Date": "02/02/2026", "Instrument": "TSLA",
                "Description": "TSLA 2/27/2026 Put $415.00", "Trans Code": "STO",
                "Quantity": "1", "Price": "$14.88", "Amount": "$1,487.95",
            },
            {
                "Activity Date": "01/30/2026", "Process Date": "01/30/2026",
                "Settle Date": "02/02/2026", "Instrument": "TSLA",
                "Description": "TSLA 2/27/2026 Put $400.00", "Trans Code": "BTO",
                "Quantity": "1", "Price": "$9.53", "Amount": "($953.04)",
            },
        ]
        transactions, errors = parse_robinhood_csv(self._make_df(rows))
        assert len(errors) == 0
        assert len(transactions) == 2
        assert transactions[0].price == pytest.approx(14.88)
        assert transactions[0].amount == pytest.approx(1487.95)
        assert transactions[1].price == pytest.approx(9.53)
        assert transactions[1].amount == pytest.approx(-953.04)

    def test_trailing_disclaimer_row_skipped(self):
        """CSV with Robinhood's trailing disclaimer row should not crash."""
        csv_text = (
            '"Activity Date","Process Date","Settle Date","Instrument","Description","Trans Code","Quantity","Price","Amount"\n'
            '"02/12/2026","02/12/2026","02/13/2026","ALAB","Astera Labs, Inc.","Buy","5","$146.40","($732.00)"\n'
            '""\n'
            '"","","","","","","","","","The data provided is for informational purposes only."\n'
        )
        lots, _, _ = parse_csv(csv_text)
        assert len(lots) == 1
        assert lots[0].symbol == "ALAB"
        assert lots[0].cost_basis_per_share == pytest.approx(146.40)

    def test_multiline_description(self):
        """Robinhood descriptions can span multiple lines within quoted fields."""
        csv_text = (
            '"Activity Date","Process Date","Settle Date","Instrument","Description","Trans Code","Quantity","Price","Amount"\n'
            '"02/05/2026","02/05/2026","02/06/2026","UPXI","Upexi\n'
            'CUSIP: 39959A205\n'
            '2 UPXI Options Assigned","Buy","200","$5.00","($1,000.00)"\n'
        )
        _, transactions, _ = parse_csv(csv_text)
        assert len(transactions) == 1
        assert transactions[0].instrument == "UPXI"
        assert transactions[0].price == pytest.approx(5.00)
        assert transactions[0].quantity == pytest.approx(200.0)

    def test_option_assignment_removes_lot(self):
        """OASGN should remove option lots (similar to OEXP)."""
        from models import Transaction, TransCode, AssetType
        txns = [
            Transaction(
                activity_date=date(2026, 1, 20),
                process_date=None, settle_date=None,
                instrument="UPXI",
                description="UPXI 3/20/2026 Put $7.50",
                trans_code=TransCode.STO,
                quantity=1, price=5.00, amount=500.0,
                asset_type=AssetType.OPTION,
            ),
            Transaction(
                activity_date=date(2026, 1, 29),
                process_date=None, settle_date=None,
                instrument="UPXI",
                description="UPXI 3/20/2026 Put $7.50",
                trans_code=TransCode.OASGN,
                quantity=1, price=0.0, amount=0.0,
                asset_type=AssetType.OPTION,
            ),
        ]
        lots, _ = transactions_to_tax_lots(txns)
        # Option lot should be removed by assignment
        assert len([l for l in lots if l.symbol == "UPXI"]) == 0
