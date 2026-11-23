"""Merge saved Robinhood activity so later uploads only need new trades.

Signed-in users keep a trade book (parsed transactions) with each analysis.
A later CSV is unioned by fingerprint: overlap is dropped, gaps are warned,
FIFO is replayed on the combined book.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from account_labels import is_ira_account
from models import Transaction

SAMPLE_CSV_FILENAMES = frozenset({"sample-robinhood-transactions.csv"})
SAMPLE_1099_FILENAMES = frozenset({"sample-robinhood-1099-2026.pdf"})
# SHA-256 of client/public sample fixtures. Filenames are not trusted.
SAMPLE_CSV_SHA256 = (
    "19e099efe5d478750d3dab4f5d3cbf5e25763bac538bba399dfe36e7c5a43f79"
)
SAMPLE_1099_SHA256 = (
    "16a2d2af4b80c4fed6febf6e798489911f27a0acbb17d0733a77f0baf0096ba8"
)
# Optional CSVs the in-app sample may attach. Any other extra file is a
# real upload and must not inherit the sample exemption.
SAMPLE_EXTRA_CSV_SHA256 = frozenset({
    "b67106939c445430c585ccd78ce1d5070ef3dd131b8a21877e22cf82fc55b82f",
    "8ae64f765d8bede5c62b5bc427a7ba37c32af86e936afe29037212474f11a3c7",
})
# Snapshot quotes for the in-app 2026 sample so Open sample does not wait on Yahoo.
SAMPLE_FIXTURE_PRICES = {
    "AAPL": 315.0,
    "AMD": 477.0,
    "META": 571.0,
    "MSFT": 505.0,
    "NVDA": 228.0,
    "SPY": 771.0,
    "SOFI": 10.0,
    "TSLA": 355.0,
    # Open lot on the sample IRA replacement. Unused by the single-file sample.
    "QQQ": 510.0,
}


def _upload_basename(filename: str | None) -> str:
    return (filename or "").strip().split("/")[-1].split("\\")[-1]


def is_sample_csv_filename(filename: str | None) -> bool:
    return _upload_basename(filename).lower() in SAMPLE_CSV_FILENAMES


def is_sample_1099_filename(filename: str | None) -> bool:
    return _upload_basename(filename).lower() in SAMPLE_1099_FILENAMES


def sha256_hex(data: bytes | None) -> str:
    if not data:
        return ""
    return hashlib.sha256(data).hexdigest()


def is_trusted_sample_csv_bytes(contents: bytes | None) -> bool:
    if not contents:
        return False
    # The checked-in CSV is text, so Windows may check it out with CRLF even
    # though the browser uploads those exact bytes. Authenticate the content
    # independent of Git's line-ending conversion while keeping every other
    # byte covered by the fixture hash.
    canonical_contents = contents.replace(b"\r\n", b"\n")
    return sha256_hex(canonical_contents) == SAMPLE_CSV_SHA256


def is_trusted_sample_1099_bytes(contents: bytes | None) -> bool:
    return sha256_hex(contents) == SAMPLE_1099_SHA256


def is_trusted_sample_extra_bytes(contents: bytes | None) -> bool:
    if not contents:
        return False
    canonical_contents = contents.replace(b"\r\n", b"\n")
    return sha256_hex(canonical_contents) in SAMPLE_EXTRA_CSV_SHA256


def is_trusted_in_app_sample(
    csv_bytes: bytes | None,
    pdf_bytes: bytes | None,
    extra_csvs: list[bytes] | None = None,
) -> bool:
    """True only when every upload is a checked-in public sample fixture.

    Extra files are optional. A file that is not one of those fixtures
    keeps the run on the normal unpaid path, even when the primary CSV
    and 1099 are the sample.
    """
    if not (
        is_trusted_sample_csv_bytes(csv_bytes)
        and is_trusted_sample_1099_bytes(pdf_bytes)
    ):
        return False
    return all(is_trusted_sample_extra_bytes(extra) for extra in (extra_csvs or []))


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
        (txn.account_label or ""),
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


def _fingerprint_without_account(txn: Transaction) -> tuple:
    return transaction_fingerprint(txn)[:-1]


def _legacy_account_label(
    prior: list[Transaction],
    incoming: list[Transaction],
    base: tuple,
) -> str | None:
    """The one account a blank legacy row may adopt for this trade.

    A saved unlabeled row matches a later labeled copy of the same trade
    only when every labeled copy of that trade names the same account.
    Two accounts with the same fill are left distinct.
    """
    labels: set[str] = set()
    for txn in list(prior) + list(incoming):
        if _fingerprint_without_account(txn) != base:
            continue
        label = txn.account_label or ""
        if label:
            labels.add(label)
    if len(labels) == 1:
        return next(iter(labels))
    return None


def merge_transaction_books(
    prior: list[Transaction],
    incoming: list[Transaction],
) -> MergeResult:
    """Union two books. Identical overlapping rows are kept once (multiset max).

    An unlabeled saved row and a labeled re-upload of that same trade are
    one row. The kept row takes the label. A different account stays added.
    """
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

    used_prior: set[int] = set()
    upgraded: dict[int, Transaction] = {}
    added_txns: list[Transaction] = []
    already = 0

    def take_prior(matches) -> tuple[int, Transaction] | None:
        for index, txn in enumerate(prior_list):
            if index in used_prior:
                continue
            if matches(txn):
                used_prior.add(index)
                return index, txn
        return None

    for txn in incoming_list:
        fingerprint = transaction_fingerprint(txn)
        exact = take_prior(lambda prior_txn, fp=fingerprint: transaction_fingerprint(prior_txn) == fp)
        if exact is not None:
            already += 1
            continue
        base = _fingerprint_without_account(txn)
        allowed = _legacy_account_label(prior_list, incoming_list, base)
        incoming_label = txn.account_label or ""
        if allowed is None or incoming_label not in ("", allowed):
            added_txns.append(txn)
            continue
        legacy = take_prior(
            lambda prior_txn, trade=base, label=allowed, incoming_label=incoming_label: (
                _fingerprint_without_account(prior_txn) == trade
                and (prior_txn.account_label or "") in ("", label)
                and (prior_txn.account_label or "") != incoming_label
            )
        )
        if legacy is None:
            added_txns.append(txn)
            continue
        index, prior_txn = legacy
        kept_label = incoming_label or (prior_txn.account_label or "") or allowed
        if (prior_txn.account_label or "") != kept_label:
            upgraded[index] = prior_txn.model_copy(update={"account_label": kept_label})
        already += 1

    merged = [upgraded.get(index, txn) for index, txn in enumerate(prior_list)]
    merged.extend(added_txns)
    merged = _settle_unlabeled_leftovers(merged)
    first, last = _date_bounds(merged)
    return MergeResult(
        transactions=merged,
        added=len(added_txns),
        already_in_book=already,
        gap_days=gap_days_between(prior_list, incoming_list),
        first_activity_date=first,
        last_activity_date=last,
    )


def _settle_unlabeled_leftovers(merged: list[Transaction]) -> list[Transaction]:
    """Keep a legacy unlabeled row from becoming a second account.

    A blank row whose fill was re-uploaded under one label takes that label.
    The same fill under two labels drops the blank copy. Older unlabeled
    rows adopt the only taxable label in the book, so a continuation plus
    an IRA file stays one taxable account. Two taxable labels are not guessed.
    """
    taxable_labels: list[str] = []
    labeled_bases: dict[tuple, set[str]] = {}
    for txn in merged:
        label = txn.account_label or ""
        if not label:
            continue
        if not is_ira_account(label) and label not in taxable_labels:
            taxable_labels.append(label)
        labeled_bases.setdefault(_fingerprint_without_account(txn), set()).add(label)
    sole = taxable_labels[0] if len(taxable_labels) == 1 else None
    kept: list[Transaction] = []
    for txn in merged:
        label = txn.account_label or ""
        if label:
            kept.append(txn)
            continue
        labels = labeled_bases.get(_fingerprint_without_account(txn), set())
        if len(labels) > 1:
            continue
        if len(labels) == 1:
            kept.append(txn.model_copy(update={"account_label": next(iter(labels))}))
            continue
        if sole:
            kept.append(txn.model_copy(update={"account_label": sole}))
            continue
        kept.append(txn)
    return kept


HISTORICAL_BOOK_UNRECOVERABLE_WARNING = (
    "Earlier uploads were saved without their trade rows, so this file was "
    "analyzed on its own and was not merged with that history. Upload a full "
    "Robinhood activity export to rebuild the book."
)
ACTIVITY_BOOK_LOAD_FAILED_WARNING = (
    "Your saved trade book could not be loaded, so this file was analyzed on "
    "its own and was not saved over that book. Try the upload again."
)
ACTIVITY_BOOK_SAVE_FAILED_WARNING = (
    "This upload could not be saved onto your trade book, so its trades were "
    "not added. Your saved book is unchanged. Try the upload again."
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
    """Drop raw trades from an analysis dict before sending it to the browser.

    A legacy row may still store a top-level transactions array. The copy
    returned here omits that key. The stored row is left unchanged.
    """
    if not result:
        return {}
    public = dict(result)
    book = public.get("activity_book")
    if isinstance(book, dict) and book.get("transactions"):
        public["activity_book"] = {**book, "transactions": []}
    public.pop("transactions", None)
    return public
