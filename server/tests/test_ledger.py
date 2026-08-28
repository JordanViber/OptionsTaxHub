"""Tests for incremental trade-book merge."""

from datetime import date

from ledger import (
    gap_days_between,
    is_sample_csv_filename,
    merge_transaction_books,
    merge_warning,
    transaction_fingerprint,
    transactions_from_stored,
)
from models import AssetType, TransCode, Transaction


def _txn(
    day: str,
    code: str = "Buy",
    instrument: str = "AAPL",
    qty: float = 10,
    price: float = 100,
    amount: float | None = None,
    description: str = "Apple",
) -> Transaction:
    d = date.fromisoformat(day)
    if amount is None:
        amount = -qty * price if code == "Buy" else qty * price
    return Transaction(
        activity_date=d,
        process_date=d,
        settle_date=d,
        instrument=instrument,
        description=description,
        trans_code=TransCode(code),
        quantity=qty,
        price=price,
        amount=amount,
        asset_type=AssetType.STOCK,
    )


class TestFingerprint:
    def test_whitespace_in_description_does_not_fork_identity(self):
        a = _txn("2026-01-02", description="Tesla\nCUSIP: 88160R101")
        b = _txn("2026-01-02", description="Tesla CUSIP: 88160R101")
        assert transaction_fingerprint(a) == transaction_fingerprint(b)

    def test_price_difference_is_a_different_row(self):
        a = _txn("2026-03-27", code="Sell", qty=5, price=365.04, amount=1825.2)
        b = _txn("2026-03-27", code="Sell", qty=5, price=365.63, amount=1828.15)
        assert transaction_fingerprint(a) != transaction_fingerprint(b)


class TestMerge:
    def test_continuation_with_overlap_does_not_double_count(self):
        prior = [
            _txn("2023-06-01", qty=10, price=100),
            _txn("2026-01-01", qty=2, price=180),
        ]
        incoming = [
            _txn("2026-01-01", qty=2, price=180),
            _txn("2026-03-01", qty=5, price=150),
        ]
        merged = merge_transaction_books(prior, incoming)
        assert merged.added == 1
        assert merged.already_in_book == 1
        assert merged.gap_days == 0
        assert len(merged.transactions) == 3
        assert merged.first_activity_date == date(2023, 6, 1)
        assert merged.last_activity_date == date(2026, 3, 1)

    def test_identical_same_day_fills_keep_multiset_count(self):
        prior = [_txn("2026-02-04", qty=1, price=400), _txn("2026-02-04", qty=1, price=400)]
        incoming = [_txn("2026-02-04", qty=1, price=400), _txn("2026-02-04", qty=1, price=400)]
        merged = merge_transaction_books(prior, incoming)
        assert merged.added == 0
        assert merged.already_in_book == 2
        assert len(merged.transactions) == 2

    def test_incoming_can_add_a_third_identical_fill(self):
        prior = [_txn("2026-02-04", qty=1, price=400)]
        incoming = [
            _txn("2026-02-04", qty=1, price=400),
            _txn("2026-02-04", qty=1, price=400),
        ]
        merged = merge_transaction_books(prior, incoming)
        assert merged.added == 1
        assert len(merged.transactions) == 2

    def test_gap_is_reported_when_ranges_do_not_touch(self):
        prior = [_txn("2023-06-01"), _txn("2026-01-01")]
        incoming = [_txn("2026-03-01")]
        merged = merge_transaction_books(prior, incoming)
        assert merged.gap_days == (date(2026, 3, 1) - date(2026, 1, 1)).days - 1
        warning = merge_warning(merged, "full.csv")
        assert warning is not None
        assert "missing" in warning.lower()

    def test_empty_prior_is_just_the_new_file(self):
        incoming = [_txn("2026-03-01")]
        merged = merge_transaction_books([], incoming)
        assert merged.added == 1
        assert merged.transactions == incoming

    def test_round_trip_stored_json(self):
        original = [_txn("2025-07-15", code="Sell", qty=5, price=90, amount=450)]
        dumped = [t.model_dump(mode="json") for t in original]
        restored = transactions_from_stored(dumped)
        assert len(restored) == 1
        assert transaction_fingerprint(restored[0]) == transaction_fingerprint(original[0])


class TestHelpers:
    def test_sample_filename(self):
        assert is_sample_csv_filename("sample-robinhood-transactions.csv")
        assert is_sample_csv_filename("path/Sample-Robinhood-Transactions.csv")
        assert not is_sample_csv_filename("robinhood-2026.csv")

    def test_touching_dates_are_not_a_gap(self):
        prior = [_txn("2026-01-01")]
        incoming = [_txn("2026-01-02")]
        assert gap_days_between(prior, incoming) == 0
