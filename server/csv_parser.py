"""
CSV parser for OptionsTaxHub.

Supports two CSV formats:
1. Robinhood transaction history export (Activity Date, Process Date, Settle Date,
   Instrument, Description, Trans Code, Quantity, Price, Amount)
2. Simplified portfolio snapshot (symbol, quantity, purchase_price, current_price)

Auto-detects format by inspecting the header row.
Aggregates transactions into TaxLot positions using FIFO (first-in, first-out).
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime
from typing import Optional

import pandas as pd

from models import AssetType, TaxLot, Transaction, TransCode

logger = logging.getLogger(__name__)

# Column name constants (avoid duplication)
COL_ACTIVITY_DATE = "Activity Date"
COL_TRANS_CODE = "Trans Code"

# Column name mappings for Robinhood format
ROBINHOOD_COLUMNS = {
    COL_ACTIVITY_DATE,
    "Process Date",
    "Settle Date",
    "Instrument",
    "Description",
    COL_TRANS_CODE,
    "Quantity",
    "Price",
    "Amount",
}

# Column name mappings for simplified format
SIMPLE_COLUMNS = {"symbol", "quantity", "purchase_price", "current_price"}

# Options transaction codes
OPTIONS_TRANS_CODES = {"STO", "BTC", "BTO", "STC", "OEXP", "OASGN", "OCA"}

# Non-trading account activity codes (skipped during parsing)
ACCOUNT_ACTIVITY_CODES = {"ACH", "RTP", "FUTSWP", "MINT", "ROC"}

# Corporate action codes (tracked but don't create lots by themselves)
CORPORATE_ACTION_CODES = {"SPR", "OCA"}


def parse_robinhood_amount(value) -> float:
    """
    Parse a Robinhood numeric/currency field.

    Handles: $7.70, ($732.00), ($2,440.10), empty strings, NaN.
    Parenthesized values are treated as negative.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return 0.0
    # Detect negative (parenthesized)
    negative = s.startswith("(") and s.endswith(")")
    # Strip $, commas, parentheses
    s = s.replace("$", "").replace(",", "").replace("(", "").replace(")", "")
    if not s:
        return 0.0
    result = float(s)
    return -result if negative else result


def parse_robinhood_quantity(value) -> float:
    """
    Parse a Robinhood Quantity field.

    Handles: numeric strings, "400S" suffix (S = shares out in corporate actions),
    empty strings, NaN.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return 0.0
    # Strip trailing 'S' (used by Robinhood for share-out in splits/corporate actions)
    s = s.rstrip("Ss")
    if not s:
        return 0.0
    return abs(float(s))


def detect_csv_format(df: pd.DataFrame) -> str:
    """
    Auto-detect the CSV format based on column headers.

    Returns:
        'robinhood' if the CSV matches Robinhood transaction history format.
        'simple' if the CSV matches the simplified portfolio snapshot format.
        'unknown' if neither format is detected.
    """
    columns = set(df.columns.str.strip())

    # Check for Robinhood format (must have key columns)
    robinhood_required = {COL_ACTIVITY_DATE, "Instrument", COL_TRANS_CODE, "Quantity", "Price"}
    if robinhood_required.issubset(columns):
        return "robinhood"

    # Check for simplified format
    simple_required = {"symbol", "quantity", "purchase_price", "current_price"}
    if simple_required.issubset(columns):
        return "simple"

    # Try case-insensitive match for simple format
    columns_lower = set(df.columns.str.strip().str.lower())
    if {"symbol", "quantity", "purchase_price", "current_price"}.issubset(columns_lower):
        return "simple"

    return "unknown"


def parse_robinhood_date(date_str: str) -> Optional[date]:
    """
    Parse a date string in Robinhood's MM/DD/YYYY format.

    Also handles YYYY-MM-DD format as a fallback.
    """
    if not date_str or pd.isna(date_str):
        return None

    date_str = str(date_str).strip()

    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    logger.warning(f"Could not parse date: {date_str}")
    return None


def determine_asset_type(trans_code: str, description: str = "") -> AssetType:
    """
    Determine if a transaction is for a stock or an option.

    Options are identified by their Trans Code (STO, BTC, BTO, STC, OEXP)
    or by keywords in the Description field (Call, Put, strike prices).
    """
    if trans_code in OPTIONS_TRANS_CODES:
        return AssetType.OPTION

    # Check description for option indicators (Call/Put keywords)
    description_lower = description.lower() if description else ""
    if " call " in description_lower or " put " in description_lower:
        return AssetType.OPTION

    return AssetType.STOCK


def _parse_robinhood_row(
    row: pd.Series,
    row_num: int,
) -> tuple[Transaction | None, str | None]:
    """
    Parse a single Robinhood CSV row into a Transaction.

    Returns (Transaction, None) on success, or (None, error_message) on failure.
    Returns (None, None) if the row should be silently skipped.
    """
    # Parse dates
    activity_date = parse_robinhood_date(row.get(COL_ACTIVITY_DATE, ""))
    if not activity_date:
        return None, f"Row {row_num}: Missing or invalid Activity Date"

    process_date = parse_robinhood_date(row.get("Process Date", ""))
    settle_date = parse_robinhood_date(row.get("Settle Date", ""))

    # Parse instrument (ticker symbol)
    instrument = str(row.get("Instrument", "")).strip().upper()
    if not instrument or instrument == "NAN":
        return None, None  # Silently skip account-level rows

    # Parse transaction code
    raw_trans_code = str(row.get(COL_TRANS_CODE, "")).strip()
    try:
        trans_code = TransCode(raw_trans_code)
    except ValueError:
        return None, f"Row {row_num}: Unknown Trans Code '{raw_trans_code}' for {instrument}"

    # Skip non-trading account activity (ACH deposits, interest, etc.)
    if raw_trans_code in ACCOUNT_ACTIVITY_CODES:
        return None, None

    # Parse description
    description = str(row.get("Description", "")).strip()
    if description == "nan":
        description = ""

    # Parse numeric fields (handle $, commas, parentheses, S suffix)
    quantity = parse_robinhood_quantity(row.get("Quantity", 0))
    price = abs(parse_robinhood_amount(row.get("Price", 0)))
    amount = parse_robinhood_amount(row.get("Amount", 0))

    # Determine asset type
    asset_type = determine_asset_type(raw_trans_code, description)

    txn = Transaction(
        activity_date=activity_date,
        process_date=process_date,
        settle_date=settle_date,
        instrument=instrument,
        description=description,
        trans_code=trans_code,
        quantity=quantity,
        price=price,
        amount=amount,
        asset_type=asset_type,
    )
    return txn, None


def parse_robinhood_csv(df: pd.DataFrame) -> tuple[list[Transaction], list[str]]:
    """
    Parse a Robinhood transaction history CSV into Transaction objects.

    Args:
        df: pandas DataFrame read from the CSV file.

    Returns:
        Tuple of (list of Transaction objects, list of error/warning messages).
    """
    transactions: list[Transaction] = []
    errors: list[str] = []

    # Strip whitespace from column names
    df.columns = df.columns.str.strip()

    for raw_idx, row in df.iterrows():
        row_num = int(raw_idx) + 1  # type: ignore[arg-type]
        try:
            txn, error = _parse_robinhood_row(row, row_num)
            if error:
                errors.append(error)
            elif txn:
                transactions.append(txn)
        except Exception as e:
            errors.append(f"Row {row_num}: Error parsing row — {str(e)}")

    return transactions, errors


def parse_simple_csv(df: pd.DataFrame) -> tuple[list[TaxLot], list[str]]:
    """
    Parse a simplified portfolio CSV (symbol, quantity, purchase_price, current_price)
    directly into TaxLot objects.

    This format doesn't have transaction history, so we create one TaxLot per row.
    Purchase date defaults to today (treated as short-term) since the CSV has no dates.

    Args:
        df: pandas DataFrame read from the CSV file.

    Returns:
        Tuple of (list of TaxLot objects, list of error/warning messages).
    """
    tax_lots: list[TaxLot] = []
    errors: list[str] = []
    warnings: list[str] = []

    # Normalize column names to lowercase
    df.columns = df.columns.str.strip().str.lower()

    # Check for optional purchase_date column
    has_dates = "purchase_date" in df.columns

    if not has_dates:
        warnings.append(
            "CSV has no purchase_date column — all positions treated as short-term. "
            "Add a purchase_date column (MM/DD/YYYY) for accurate short-term vs long-term classification."
        )

    for raw_idx, row in df.iterrows():
        row_num = int(raw_idx) + 1  # type: ignore[arg-type]
        try:
            symbol = str(row.get("symbol", "")).strip().upper()
            if not symbol or symbol == "NAN":
                errors.append(f"Row {row_num}: Missing symbol")
                continue

            quantity = float(row.get("quantity", 0))
            if quantity <= 0:
                errors.append(f"Row {row_num}: Invalid quantity for {symbol}")
                continue

            purchase_price = float(row.get("purchase_price", 0))
            current_price = float(row.get("current_price", 0))

            # Parse purchase date if available
            if has_dates:
                purchase_date = parse_robinhood_date(str(row.get("purchase_date", "")))
                if not purchase_date:
                    purchase_date = date.today()
                    warnings.append(
                        f"Row {row_num}: Could not parse purchase_date for {symbol}, using today"
                    )
            else:
                purchase_date = date.today()

            tax_lots.append(
                TaxLot(
                    symbol=symbol,
                    quantity=quantity,
                    cost_basis_per_share=purchase_price,
                    total_cost_basis=purchase_price * quantity,
                    purchase_date=purchase_date,
                    current_price=current_price,
                )
            )

        except Exception as e:
            errors.append(f"Row {row_num}: Error parsing row — {str(e)}")

    return tax_lots, errors + warnings


def _remove_lots_fifo(open_lots: dict[str, list[TaxLot]], symbol: str, quantity: float) -> None:
    """Remove lots for a symbol in FIFO order (used by OEXP, OASGN, etc.)."""
    if symbol not in open_lots:
        return
    lots_to_remove: list[TaxLot] = []
    remaining = quantity
    for lot in open_lots[symbol]:
        if remaining <= 0:
            break
        if lot.quantity <= remaining:
            remaining -= lot.quantity
            lots_to_remove.append(lot)
        else:
            lot.quantity -= remaining
            lot.total_cost_basis = lot.cost_basis_per_share * lot.quantity
            remaining = 0
    for lot in lots_to_remove:
        open_lots[symbol].remove(lot)


def _add_lot(
    open_lots: dict[str, list[TaxLot]],
    symbol: str,
    txn: Transaction,
    asset_type: AssetType | None = None,
) -> None:
    """Create and append a new tax lot for a purchase transaction."""
    lot = TaxLot(
        symbol=symbol,
        quantity=txn.quantity,
        cost_basis_per_share=txn.price,
        total_cost_basis=txn.price * txn.quantity,
        purchase_date=txn.activity_date,
        asset_type=asset_type or txn.asset_type,
    )
    if symbol not in open_lots:
        open_lots[symbol] = []
    open_lots[symbol].append(lot)


def _close_lots_fifo(
    open_lots: dict[str, list[TaxLot]],
    symbol: str,
    txn: Transaction,
    warnings: list[str],
) -> None:
    """Close lots in FIFO order for a sell transaction."""
    remaining_to_sell = txn.quantity

    if symbol not in open_lots or not open_lots[symbol]:
        warnings.append(
            f"Sell of {txn.quantity} {symbol} on {txn.activity_date} "
            f"but no open lots found (short sale or prior history not in CSV)"
        )
        return

    while remaining_to_sell > 0 and open_lots.get(symbol):
        oldest_lot = open_lots[symbol][0]
        if oldest_lot.quantity <= remaining_to_sell:
            remaining_to_sell -= oldest_lot.quantity
            open_lots[symbol].pop(0)
        else:
            oldest_lot.quantity -= remaining_to_sell
            oldest_lot.total_cost_basis = (
                oldest_lot.cost_basis_per_share * oldest_lot.quantity
            )
            remaining_to_sell = 0

    if remaining_to_sell > 0:
        warnings.append(
            f"Sell of {symbol} on {txn.activity_date}: "
            f"{remaining_to_sell} shares could not be matched to open lots"
        )


def transactions_to_tax_lots(transactions: list[Transaction]) -> tuple[list[TaxLot], list[str]]:
    """
    Aggregate Robinhood transactions into TaxLot positions using FIFO.

    Processes Buy transactions to create lots, and Sell transactions to close
    lots in FIFO order. Remaining open lots represent current holdings.

    Args:
        transactions: List of parsed Transaction objects, sorted by date.

    Returns:
        Tuple of (list of open TaxLot positions, list of warnings).
    """
    warnings: list[str] = []

    # Sort transactions by date
    sorted_txns = sorted(transactions, key=lambda t: t.activity_date)

    # Track open lots per symbol (FIFO order)
    open_lots: dict[str, list[TaxLot]] = {}

    for txn in sorted_txns:
        symbol = txn.instrument

        if txn.trans_code in (TransCode.BUY, TransCode.BTO):
            _add_lot(open_lots, symbol, txn)

        elif txn.trans_code in (TransCode.SELL, TransCode.STC):
            _close_lots_fifo(open_lots, symbol, txn, warnings)

        elif txn.trans_code in (TransCode.OEXP, TransCode.OASGN):
            # Option expiration or assignment — remove the lot
            _remove_lots_fifo(open_lots, symbol, txn.quantity)

        elif txn.trans_code == TransCode.STO:
            # Sell to Open — creates a short option position
            _add_lot(open_lots, symbol, txn, asset_type=AssetType.OPTION)

        elif txn.trans_code in (TransCode.SPR, TransCode.OCA):
            # Stock split / reverse split / corporate action — informational.
            pass

    # Flatten all remaining open lots
    all_open_lots = []
    for symbol_lots in open_lots.values():
        all_open_lots.extend(symbol_lots)

    return all_open_lots, warnings


def parse_csv(file_content: str) -> tuple[list[TaxLot], list[Transaction], list[str]]:
    """
    Main entry point: parse a CSV file and return tax lots and transactions.

    Auto-detects the CSV format (Robinhood or simplified) and processes accordingly.

    Args:
        file_content: Raw CSV file content as a string.

    Returns:
        Tuple of (list of TaxLot positions, list of Transaction objects, list of errors/warnings).
        For simplified CSVs, transactions list will be empty.
    """
    try:
        df = pd.read_csv(io.StringIO(file_content), on_bad_lines="skip")
    except Exception as e:
        return [], [], [f"Failed to read CSV file: {str(e)}"]

    if df.empty:
        return [], [], ["CSV file is empty"]

    csv_format = detect_csv_format(df)

    if csv_format == "robinhood":
        logger.info("Detected Robinhood transaction history CSV format")
        transactions, errors = parse_robinhood_csv(df)

        if not transactions:
            return [], [], errors or ["No valid transactions found in CSV"]

        # Aggregate into tax lots via FIFO
        tax_lots, lot_warnings = transactions_to_tax_lots(transactions)
        return tax_lots, transactions, errors + lot_warnings

    elif csv_format == "simple":
        logger.info("Detected simplified portfolio CSV format")
        tax_lots, errors = parse_simple_csv(df)
        return tax_lots, [], errors

    else:
        return [], [], [
            "Unrecognized CSV format. Expected either:\n"
            "  • Robinhood format: Activity Date, Process Date, Settle Date, "
            "Instrument, Description, Trans Code, Quantity, Price, Amount\n"
            "  • Simplified format: symbol, quantity, purchase_price, current_price"
        ]
