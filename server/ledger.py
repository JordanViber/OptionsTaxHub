"""Merge saved Robinhood activity so later uploads only need new trades.

Signed-in users keep a trade book (parsed transactions) with each analysis.
A later CSV is unioned by fingerprint: overlap is dropped, gaps are warned,
FIFO is replayed on the combined book.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from models import Transaction

SAMPLE_CSV_FILENAMES = frozenset({"sample-robinhood-transactions.csv"})


def is_sample_csv_filename(filename: str | None) -> bool:
    name = (filename or "").strip().split("/")[-1].split("\\")[-1]
    return name.lower() in SAMPLE_CSV_FILENAMES


def transaction_fingerprint(txn: Transaction) -> tuple:
    """Stable identity for one Robinhood row across overlapping exports."""
    trans_code = txn.trans_code.value if hasattr(txn.trans_code, "value") else str(txn.trans_code)
    asset_type = txn.asset_type.value if hasattr(txn.asset_type, "value") else str(txn.asset_type)
    activity = txn.activity_date.isoformat() if isinstance(txn.activity_date, date) else str(txn.activity_date)
    description = " ".join((txn.description or "").split())
    return (
        activity,
        trans_code,
        (txn.instrument or "").upper(),
        round(float(txn.quantity), 6),
        round(float(txn.price), 6),
        round(float(txn.amount), 2),
        description,
        bool(txn.is_quantity_out),
        asset_type,
    )


def transactions_from_stored(raw: Any) -> list[Transaction]:
    """Rebuild Transaction objects from JSONB / model_dump output."""
    if not raw:
        return []
    if isinstance(raw, list) and raw and isinstance(raw[0], Transaction):
        return list(raw)
    out: list[Transaction] = []
    for item in raw:
        try:
            if isinstance(item, Transaction):
                out.append(item)
            else:
                out.append(Transaction.model_validate(item))
        except Exception:
            continue
    return out


def _date_bounds(transactions: list[Transaction]) -> tuple[Optional[date], Optional[date]]:
    if not transactions:
        return None, None
    dates = [t.activity_date for t in transactions if t.activity_date]
    if not dates:
        return None, None
    return min(dates), max(dates)


def gap_days_between(prior: list[Transaction], incoming: list[Transaction]) -> int:
    """Calendar days missing between two books. 0 if they overlap or either is empty."""
    if not prior or not incoming:
        return 0
    prior_first, prior_last = _date_bounds(prior)
    incoming_first, incoming_last = _date_bounds(incoming)
    if not prior_first or not prior_last or not incoming_first or not incoming_last:
        return 0
    if prior_first <= incoming_last and incoming_first <= prior_last:
        return 0
    if prior_last < incoming_first:
        return max(0, (incoming_first - prior_last).days - 1)
    return max(0, (prior_first - incoming_last).days - 1)


@dataclass
class MergeResult:
    transactions: list[Transaction]
    added: int
    already_in_book: int
    gap_days: int
    first_activity_date: Optional[date]
    last_activity_date: Optional[date]


def merge_transaction_books(
    prior: list[Transaction],
    incoming: list[Transaction],
) -> MergeResult:
    """Union two books. Identical overlapping rows are kept once (multiset max)."""
    prior_list = list(prior or [])
    incoming_list = list(incoming or [])
    if not prior_list:
        first, last = _date_bounds(incoming_list)
        return MergeResult(
            transactions=incoming_list,
            added=len(incoming_list),
            already_in_book=0,
            gap_days=0,
            first_activity_date=first,
            last_activity_date=last,
        )
    if not incoming_list:
        first, last = _date_bounds(prior_list)
        return MergeResult(
            transactions=prior_list,
            added=0,
            already_in_book=0,
            gap_days=0,
            first_activity_date=first,
            last_activity_date=last,
        )

    prior_counts = Counter(transaction_fingerprint(t) for t in prior_list)
    incoming_counts = Counter(transaction_fingerprint(t) for t in incoming_list)
    incoming_by_fp: dict[tuple, list[Transaction]] = defaultdict(list)
    for txn in incoming_list:
        incoming_by_fp[transaction_fingerprint(txn)].append(txn)

    merged = list(prior_list)
    added = 0
    for fingerprint, incoming_n in incoming_counts.items():
        extra = incoming_n - prior_counts.get(fingerprint, 0)
        if extra <= 0:
            continue
        for txn in incoming_by_fp[fingerprint][:extra]:
            merged.append(txn)
            added += 1

    already = sum(
        min(prior_counts[fp], incoming_counts[fp])
        for fp in incoming_counts
        if fp in prior_counts
    )
    first, last = _date_bounds(merged)
    return MergeResult(
        transactions=merged,
        added=added,
        already_in_book=already,
        gap_days=gap_days_between(prior_list, incoming_list),
        first_activity_date=first,
        last_activity_date=last,
    )


def merge_warning(result: MergeResult, prior_filename: str = "") -> str | None:
    """Human copy for the dashboard. None when there is nothing to say."""
    if result.gap_days > 0:
        label = prior_filename.strip() or "your saved book"
        return (
            f"This file does not overlap {label} — about {result.gap_days} day(s) "
            f"of activity may be missing between the two exports. Export from the "
            f"last date of the previous file so lots stay complete."
        )
    if result.added or result.already_in_book:
        first = (
            result.first_activity_date.strftime("%b %d, %Y")
            if result.first_activity_date
            else "unknown"
        )
        last = (
            result.last_activity_date.strftime("%b %d, %Y")
            if result.last_activity_date
            else "unknown"
        )
        return (
            f"Added {result.added} new trade(s) to your book "
            f"({result.already_in_book} already on file). "
            f"Book now covers {first} – {last}."
        )
    return None


def strip_book_transactions_dict(result: dict | None) -> dict:
    """Drop raw trades from an analysis dict before sending it to the browser."""
    if not result:
        return {}
    public = dict(result)
    book = public.get("activity_book")
    if isinstance(book, dict) and book.get("transactions"):
        public["activity_book"] = {**book, "transactions": []}
    return public
