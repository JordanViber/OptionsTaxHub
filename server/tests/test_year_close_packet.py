"""Year-close packet: unpaid block, $49 checkout (not tips), webhook unlock, PDF contents."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

import db
import main
from auth import get_current_user, get_current_user_with_token
from stripe import StripeObject

from year_close_packet import (
    COMPARE_GAP_COPY,
    COMPARE_TITLE,
    HARVEST_TITLE,
    LOT_MATCH_TITLE,
    OPTIONS_WASH_SALE_FAQ,
    PACKET_AMOUNT_CENTS,
    PACKET_CHECKOUT_DESCRIPTION,
    PACKET_CHECKOUT_NAME,
    PACKET_CHECKOUT_SUBMIT_MESSAGE,
    PACKET_DISCLAIMER,
    PACKET_METADATA_PRODUCT,
    PACKET_STORE,
    SETTLEMENT_DATE_FAQ,
    UNKNOWN_1099_YEAR_COPY,
    _two_col_row,
    adopt_guest_packet,
    build_packet_payload,
    claimable_guest_packet_payload,
    classified_csv_wash,
    export_net_matching_1099,
    export_realized_totals,
    is_same_year_1099_compare,
    wash_flag_is_long_term,
    _harvest_lines,
    harvest_plain_text,
    packet_plain_text,
    packet_requires_test_stripe,
    purge_packet_store,
    get_payload,
    remember_analysis,
    render_packet_pdf,
    reset_packet_store,
    upsert_payload,
    resolve_packet_stripe_secret_key,
    same_year_compare_plain_text,
    session_grants_packet,
    paid_session_for_user_year,
    mark_paid,
)


def mock_get_current_user() -> str:
    return "test-user-123"


def mock_get_current_user_with_token() -> tuple[str, str]:
    return "test-user-123", "test-token-123"


main.app.dependency_overrides[get_current_user] = mock_get_current_user
main.app.dependency_overrides[get_current_user_with_token] = mock_get_current_user_with_token

client = TestClient(main.app)
_FAKE_PACKET_SNAPSHOTS = {}
_FAKE_PACKET_ENTITLEMENTS = {}


class _FakeEntitlementQuery:
    def __init__(self, data):
        self.data = data

    def upsert(self, *_args, **_kwargs):
        return self

    def insert(self, *_args, **_kwargs):
        return self

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self.data)


class _FakeEntitlementDatabase:
    def __init__(self, *results):
        self.results = list(results)

    def table(self, name):
        assert name == "year_close_packet_entitlements"
        data = self.results.pop(0) if self.results else []
        return _FakeEntitlementQuery(data)

SAMPLE_ANALYSIS = {
    "analysis_id": "analysis-sample-1",
    "tax_profile": {"tax_year": 2025, "filing_status": "single"},
    "supplemental_1099": {
        "source_filename": "c15f7458-e9d5-4dfb-a985-351df5a36cde.pdf",
        "broker_name": "Robinhood",
        "tax_year": 2024,
        "short_term_proceeds": 281823.83,
        "short_term_cost_basis": 264439.89,
        "short_term_wash_sale_disallowed": 17409.64,
        "short_term_net_gain": 34793.58,
        "long_term_proceeds": 108.56,
        "long_term_cost_basis": 141.72,
        "long_term_wash_sale_disallowed": 33.16,
        "long_term_net_gain": 0.0,
    },
    "wash_sale_flags": [
        {
            "symbol": "AMD",
            "sale_date": "2025-07-15",
            "sale_quantity": 10,
            "sale_loss": 300.0,
            "repurchase_date": "2025-07-25",
            "repurchase_quantity": 10,
            "disallowed_loss": 300.0,
            "adjusted_cost_basis": 1550.0,
            "explanation": "Wash sale on AMD",
        }
    ],
    "tax_lots": [
        {
            "symbol": "AMD",
            "quantity": 10,
            "purchase_date": "2025-07-25",
            "wash_sale_disallowed": 300.0,
        }
    ],
}


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _pdf_text_normalized(pdf_bytes: bytes) -> str:
    return " ".join(_pdf_text(pdf_bytes).split())


def _expected_packet_pdf_pages(payload: dict) -> int:
    """Match render_packet_pdf: cover + Harvest + compare + lot-match."""
    count = 1
    if payload.get("harvest_opportunities"):
        count += 1
    if payload.get("same_year_compare"):
        count += 1
    if payload.get("lot_match_report"):
        count += 1
    return count


def _assert_three_identity(
    row: dict,
    pdf_text: str,
    *,
    qty: float,
    opened: str,
    suggestion_id: str,
    lot_details: str = "",
) -> None:
    """quantity, purchase date, and suggestion_id/lot_details all present."""
    assert row["quantity"] == qty
    assert row["purchase_date"] == opened
    assert row["suggestion_id"] == suggestion_id
    assert str(row.get("lot_details") or "") == lot_details
    assert f"qty {qty:g}  {opened}" in pdf_text
    lot_key = lot_details or suggestion_id
    pdf_norm = " ".join(pdf_text.split())
    assert lot_key in pdf_text or lot_key in pdf_norm


@pytest.fixture(autouse=True)
def _clean_store(monkeypatch):
    reset_packet_store()
    _FAKE_PACKET_SNAPSHOTS.clear()
    _FAKE_PACKET_ENTITLEMENTS.clear()
    monkeypatch.setattr(db, "get_supabase", lambda: None)
    monkeypatch.setattr(main, "get_supabase", lambda: None)

    def save_snapshot(analysis_id, user_id, tax_year, payload, *, session_id=None, paid=False):
        key = (user_id, analysis_id)
        previous = _FAKE_PACKET_SNAPSHOTS.get(key, {})
        if previous.get("paid_at"):
            if int(previous["tax_year"]) != int(tax_year):
                raise db.PacketSnapshotYearConflict(
                    previous.get("analysis_id") or analysis_id,
                    previous.get("tax_year"),
                    tax_year,
                )
            tax_year = previous["tax_year"]
            payload = previous.get("packet_payload")
            session_id = previous.get("packet_session_id")
            paid_at = previous["paid_at"]
        else:
            # Unpaid, including another year: replace tax_year and payload on
            # this same key. Do not mint a second row.
            paid_at = "now" if paid and session_id else None
        _FAKE_PACKET_SNAPSHOTS[key] = {
            "analysis_id": previous.get("analysis_id") or analysis_id,
            "user_id": user_id,
            "tax_year": tax_year,
            "packet_payload": payload,
            "packet_session_id": session_id or previous.get("packet_session_id"),
            "paid_at": paid_at,
        }
        return {"analysis_id": _FAKE_PACKET_SNAPSHOTS[key]["analysis_id"]}

    def get_snapshot(analysis_id, user_id, client=None, *, tax_year=None):
        row = _FAKE_PACKET_SNAPSHOTS.get((user_id, analysis_id))
        if row is None:
            return None, True
        if tax_year is not None and int(row.get("tax_year")) != int(tax_year):
            return None, True
        return row, True

    def save_entitlement(analysis_id, user_id, tax_year, session_id, client=None, **_kwargs):
        row = {
            "analysis_id": analysis_id,
            "user_id": user_id,
            "tax_year": int(tax_year),
            "packet_session_id": session_id,
        }
        _FAKE_PACKET_ENTITLEMENTS[(user_id, int(tax_year), session_id)] = row
        return row

    def lookup_entitlement(user_id, tax_year):
        rows = [
            row
            for (owner, year, _session), row in _FAKE_PACKET_ENTITLEMENTS.items()
            if owner == user_id
            and year == int(tax_year)
            and not str(row.get("analysis_id") or "").startswith("conflict:")
        ]
        return (rows[-1] if rows else None), True

    def mark_snapshot_paid(analysis_id, user_id, tax_year, session_id):
        row = _FAKE_PACKET_SNAPSHOTS.get((user_id, analysis_id))
        if not row or int(row["tax_year"]) != int(tax_year):
            return False
        if row.get("paid_at"):
            return True
        row["packet_session_id"] = session_id
        row["paid_at"] = "now"
        return True

    def lookup_analysis(analysis_id, user_id):
        record = PACKET_STORE.get(analysis_id)
        if not record or not main.packet_store_belongs_to_user(analysis_id, user_id):
            return None, True
        payload = record.get("payload")
        return {
            "id": analysis_id,
            "user_id": user_id,
            "result": {
                "analysis_id": analysis_id,
                "analysis_tax_year": payload.get("analysis_tax_year") if isinstance(payload, dict) else None,
                "tax_profile": payload.get("tax_profile") if isinstance(payload, dict) else {},
            },
        }, True

    def _fake_identity_rows(analysis_id, user_id):
        rows = []
        for (owner, stored_id), row in _FAKE_PACKET_SNAPSHOTS.items():
            if owner != user_id:
                continue
            if stored_id == analysis_id or main._analysis_ids_match(stored_id, analysis_id):
                rows.append(row)
        return rows

    def list_snapshots(analysis_id, user_id, client=None):
        return _fake_identity_rows(analysis_id, user_id), True

    def lookup_entitlements_for_analysis(user_id, analysis_id, client=None):
        rows = []
        for (owner, _year, _session), row in _FAKE_PACKET_ENTITLEMENTS.items():
            if owner != user_id:
                continue
            stored_id = str(row.get("analysis_id") or "")
            if stored_id.startswith("conflict:"):
                continue
            if stored_id == analysis_id or main._analysis_ids_match(stored_id, analysis_id):
                rows.append(row)
        return rows, True

    monkeypatch.setattr(main, "save_packet_snapshot", save_snapshot)
    monkeypatch.setattr(main, "get_packet_snapshot", get_snapshot)
    monkeypatch.setattr(main, "save_packet_entitlement", save_entitlement)
    monkeypatch.setattr(main, "lookup_packet_entitlement_for_tax_year", lookup_entitlement)
    monkeypatch.setattr(main, "lookup_packet_entitlements_for_analysis", lookup_entitlements_for_analysis)
    monkeypatch.setattr(main, "list_packet_snapshots_for_identity", list_snapshots)
    monkeypatch.setattr(main, "mark_packet_snapshot_paid", mark_snapshot_paid)
    monkeypatch.setattr(main, "lookup_packet_grant_for_tax_year", lambda *_args, **_kwargs: (None, True))
    def patch_result(analysis_id, user_id, patch):
        # Year alignment and unlock flags must not depend on a live database
        # in these tests. Callers that need a failure replace this stub.
        return True

    monkeypatch.setattr(main, "patch_analysis_result", patch_result)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lookup_analysis)
    monkeypatch.setattr(
        main,
        "lookup_packet_session_entitlement",
        lambda *_args, **_kwargs: (None, True),
    )
    monkeypatch.setattr(
        main,
        "save_analysis_history",
        lambda user_id, filename, summary, *, result_data: {
            "id": result_data["analysis_id"],
            "user_id": user_id,
            "filename": filename,
            "summary": summary,
            "result": result_data,
        },
    )
    yield
    _FAKE_PACKET_SNAPSHOTS.clear()
    _FAKE_PACKET_ENTITLEMENTS.clear()
    reset_packet_store()


def _test_stripe_env(monkeypatch, *, frontend="https://options-tax-hub-client-staging.onrender.com"):
    monkeypatch.setenv("FRONTEND_URL", frontend)
    monkeypatch.setenv("STRIPE_FORCE_TEST_MODE", "true")
    monkeypatch.setenv("STRIPE_SECRET_KEY_TEST", "sk_test_packet_key")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_should_not_be_used")
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "sk_live_should_not_be_used")
    monkeypatch.setattr(main, "FRONTEND_URL", frontend)


class FakeCheckoutSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.id = "cs_test_packet_abc"
        self.url = "https://checkout.stripe.com/c/pay/cs_test_packet_abc"
        self.payment_status = "unpaid"
        self.amount_total = PACKET_AMOUNT_CENTS
        self.metadata = kwargs.get("metadata") or {}

def _stripe_object_session(**fields):
    """Real stripe.checkout.Session.retrieve shape (StripeObject, not dict)."""
    payload = {
        "object": "checkout.session",
        "id": "cs_test_retrieve",
        "status": "complete",
        "payment_status": "paid",
        "amount_total": PACKET_AMOUNT_CENTS,
        "currency": "usd",
        "mode": "payment",
        "livemode": False,
    }
    metadata = {
        "product": PACKET_METADATA_PRODUCT,
        "analysis_id": "analysis-sample-1",
        "user_id": "test-user-123",
    }
    metadata.update(fields.pop("metadata", {}) or {})
    payload["metadata"] = metadata
    payload.update(fields)
    return StripeObject.construct_from(payload, "sk_test")


def _signed_webhook_payload(event, secret="whsec_packet_test_secret"):
    payload = json.dumps(event, separators=(",", ":")).encode()
    timestamp = int(time.time())
    signed = f"{timestamp}.".encode() + payload
    signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return payload, f"t={timestamp},v1={signature}"


def _post_signed_webhook(event, *, secret="whsec_packet_test_secret"):
    payload, signature = _signed_webhook_payload(event, secret)
    return client.post(
        "/api/year-close-packet/webhook",
        content=payload,
        headers={"Stripe-Signature": signature, "Content-Type": "application/json"},
    )


def _packet_checkout_event(
    *,
    event_type="checkout.session.completed",
    payment_status="paid",
    event_id="evt_packet",
    livemode=False,
    user_id="test-user-123",
    analysis_id="analysis-sample-1",
    tax_year=None,
    session_id="cs_test_paid_1",
):
    metadata = {
        "product": PACKET_METADATA_PRODUCT,
        "analysis_id": analysis_id,
    }
    if user_id:
        metadata["user_id"] = user_id
    if tax_year is not None:
        metadata["tax_year"] = str(tax_year)
    return {
        "id": event_id,
        "object": "event",
        "type": event_type,
        "data": {
            "object": {
                "object": "checkout.session",
                "id": session_id,
                "status": "complete",
                "payment_status": payment_status,
                "amount_total": PACKET_AMOUNT_CENTS,
                "currency": "usd",
                "mode": "payment",
                "livemode": livemode,
                "metadata": metadata,
            }
        },
    }


def test_payload_and_pdf_contain_1099_totals_amd_and_faqs():
    payload = build_packet_payload(SAMPLE_ANALYSIS, analysis_id="analysis-sample-1")
    text = packet_plain_text(payload)
    assert "1099 tax year: 2024" in text
    assert "$281,823.83" in text
    assert "$17,442.80" in text
    assert "AMD $300.00 disallowed" in text
    assert text.count("AMD $300.00 disallowed") == 1
    assert "sale 2025-07-15" in text
    assert "repurchase 2025-07-25" in text
    assert "Replacement lot with wash-sale disallowed" not in text
    assert SETTLEMENT_DATE_FAQ in text
    assert OPTIONS_WASH_SALE_FAQ in text
    assert PACKET_DISCLAIMER in text
    assert "not a filed Form 8949" in text
    assert "Lot-matched 1099-B" in PACKET_DISCLAIMER
    assert "we do not parse settlement-date lots" not in text.lower()

    pdf_bytes = render_packet_pdf(payload)
    pdf_text = _pdf_text(pdf_bytes)
    assert "2024" in pdf_text
    assert "281,823.83" in pdf_text
    assert "17,442.80" in pdf_text
    assert "AMD" in pdf_text
    assert "300.00" in pdf_text
    assert "settlement date" in pdf_text.lower()
    assert "credit-spread" in pdf_text.lower() or "credit spread" in pdf_text.lower()


def test_analysis_with_history_suggestions_finds_embedded_analysis_id(monkeypatch):
    monkeypatch.setattr(main, "get_analysis_by_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main,
        "get_analysis_by_result_analysis_id",
        lambda *_args, **_kwargs: {
            "result": {"suggestions": [{"symbol": "AMD"}]}
        },
    )

    result = main._analysis_with_history_suggestions(
        "analysis-uuid", "test-user-123", {"positions": []}
    )

    assert result["suggestions"] == [{"symbol": "AMD"}]


def test_unpaid_download_is_403(monkeypatch):
    _test_stripe_env(monkeypatch)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    response = client.get("/api/year-close-packet/download?analysis_id=analysis-sample-1")
    assert response.status_code == 403
    assert "payment" in response.json()["detail"].lower()


def test_checkout_session_is_4900_cents_year_close_packet_not_tips(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeCheckoutSession(**kwargs)

    monkeypatch.setattr(main.stripe.checkout.Session, "create", fake_create)

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "analysis-sample-1", "analysis": SAMPLE_ANALYSIS},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["amount"] == 4900
    assert body["product"] == "Year-close packet"
    assert body["stripe_mode"] == "test"
    assert "checkout.stripe.com" in body["checkout_url"]

    line_items = captured["line_items"]
    price_data = line_items[0]["price_data"]
    assert price_data["unit_amount"] == 4900
    assert price_data["product_data"]["name"] == PACKET_CHECKOUT_NAME
    assert price_data["product_data"]["description"] == PACKET_CHECKOUT_DESCRIPTION
    assert "CPA" in price_data["product_data"]["description"]
    assert captured["custom_text"]["submit"]["message"] == PACKET_CHECKOUT_SUBMIT_MESSAGE
    assert captured["mode"] == "payment"
    assert captured["metadata"]["product"] == PACKET_METADATA_PRODUCT
    # Must not reuse TipJar price IDs
    assert "price" not in line_items[0]
    assert captured["success_url"].startswith(
        "https://options-tax-hub-client-staging.onrender.com/dashboard"
    )
    assert "packet_session={CHECKOUT_SESSION_ID}" in captured["success_url"]
    assert captured["api_key"] == "sk_test_packet_key"
    assert captured["idempotency_key"] == (
        "year-close-packet:test-user-123:analysis-sample-1:2025"
    )
    packet_source = Path(main.__file__).read_text(encoding="utf-8")
    assert "stripe.api_key =" not in packet_source
    assert "stripe.api_key=" not in packet_source


def test_checkout_keeps_client_analysis_id_when_history_is_missing(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "11111111-1111-4111-8111-111111111111"
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": analysis_id}
    remember_analysis(analysis_id, "test-user-123", analysis)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    captured = {}
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: captured.update(kwargs) or FakeCheckoutSession(**kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": analysis_id, "analysis": analysis},
    )

    assert response.status_code == 200
    assert response.json()["analysis_id"] == analysis_id
    assert captured["metadata"]["analysis_id"] == analysis_id
    assert captured["success_url"].endswith(f"packet_analysis={analysis_id}")


class _UuidSnapshotQuery:
    """Returns a snapshot only when a UUID key is matched case-insensitively."""

    def __init__(self, rows):
        self.rows = rows
        self.eqs = []
        self.filters = []

    def select(self, *_args, **_kwargs):
        return self

    def update(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.eqs.append((column, value))
        return self

    def filter(self, column, operator, value):
        self.filters.append((column, operator, value))
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        matched = []
        for row in self.rows:
            if any(row.get(column) != value for column, value in self.eqs):
                continue
            kept = True
            for column, operator, value in self.filters:
                if operator != "ilike" or str(row.get(column) or "").lower() != str(value).lower():
                    kept = False
                    break
            if kept:
                matched.append(dict(row))
        return SimpleNamespace(data=matched[:1])


class _UuidSnapshotClient:
    def __init__(self, rows):
        self.rows = rows

    def rpc(self, *_args, **_kwargs):
        raise RuntimeError("cleanup unavailable")

    def table(self, name):
        assert name == "year_close_packet_snapshots"
        return _UuidSnapshotQuery(self.rows)


def _year_scoped_order_key(row, column, desc, nullsfirst):
    value = row.get(column)
    is_null = value is None
    nulls_first = bool(desc) if nullsfirst is None else bool(nullsfirst)
    null_rank = 0 if is_null == nulls_first else 1
    if is_null:
        ordered = ""
    elif desc:
        ordered = _YearScopedDesc(value)
    else:
        ordered = value
    return (null_rank, ordered)


class _YearScopedDesc:
    def __init__(self, value):
        self.value = value

    def __lt__(self, other):
        return self.value > other.value


class _YearScopedSnapshotClient:
    """eq/ilike/is_ snapshot store. order() sorts before limit.

    Entitlement inserts stay off the snapshot list so a conflict receipt
    cannot look like a second snapshot row.
    """

    def __init__(self, rows):
        self.rows = [dict(row) for row in rows]
        self.entitlements = []

    def rpc(self, *_args, **_kwargs):
        raise RuntimeError("cleanup unavailable")

    def table(self, name):
        source = self.entitlements if name == "year_close_packet_entitlements" else self.rows
        return _YearScopedSnapshotQuery(self, source)


class _YearScopedSnapshotQuery:
    def __init__(self, client, rows):
        self.client = client
        self.rows = rows
        self.op = "select"
        self.payload = None
        self.eqs = []
        self.filters = []
        self.isnull = []
        self.orders = []
        self.limit_n = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.eqs.append((column, value))
        return self

    def filter(self, column, operator, value):
        self.filters.append((column, operator, value))
        return self

    def is_(self, column, value):
        self.isnull.append((column, value))
        return self

    def order(self, column, desc=False, nullsfirst=None, **_kwargs):
        self.orders.append((column, desc, nullsfirst))
        return self

    def limit(self, count):
        self.limit_n = count
        return self

    def update(self, row):
        self.op = "update"
        self.payload = dict(row)
        return self

    def insert(self, row):
        self.op = "insert"
        self.payload = dict(row)
        return self

    def _matches(self, row):
        for column, value in self.eqs:
            if row.get(column) != value:
                return False
        for column, operator, value in self.filters:
            if operator != "ilike" or str(row.get(column) or "").lower() != str(value).lower():
                return False
        for column, value in self.isnull:
            if value == "null" and row.get(column) is not None:
                return False
        return True

    def execute(self):
        if self.op == "insert":
            stored = dict(self.payload)
            self.rows.append(stored)
            return SimpleNamespace(data=[stored])
        matched = [row for row in self.rows if self._matches(row)]
        if self.op == "select" and self.orders:
            for column, desc, nullsfirst in reversed(self.orders):
                matched.sort(
                    key=lambda row, column=column, desc=desc, nullsfirst=nullsfirst: (
                        _year_scoped_order_key(row, column, desc, nullsfirst)
                    )
                )
        if self.op == "select" and self.limit_n is not None:
            matched = matched[: self.limit_n]
        if self.op == "update":
            for row in matched:
                row.update(self.payload)
            return SimpleNamespace(data=[dict(row) for row in matched])
        return SimpleNamespace(data=[dict(row) for row in matched])


def test_history_restore_finds_private_packet_for_uuid_case_variant(monkeypatch):
    stored = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    requested = stored.lower()
    payload = {
        "analysis_id": stored,
        "tax_profile": {"tax_year": 2025},
        "summary": {},
    }
    monkeypatch.setattr(
        db,
        "get_supabase",
        lambda: _UuidSnapshotClient([{
            "analysis_id": stored,
            "user_id": "test-user-123",
            "tax_year": 2025,
            "packet_payload": payload,
            "packet_session_id": None,
            "paid_at": None,
            "expires_at": "2099-01-01T00:00:00+00:00",
        }]),
    )
    monkeypatch.setattr(main, "get_packet_snapshot", db.get_packet_snapshot)
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: ({
            "id": "history-row",
            "user_id": "test-user-123",
            "result": {"analysis_id": stored},
        }, True),
    )

    response = client.post(
        "/api/portfolio/history",
        json={
            "filename": "guest.csv",
            "analysis": {"analysis_id": requested, "summary": {}, "tax_profile": {"tax_year": 2025}},
        },
    )

    assert response.status_code == 200, response.text
    assert "missing its private packet data" not in response.text
    assert response.json()["id"] == "history-row"


def test_checkout_accepts_uuid_case_variant_and_keeps_stored_spelling(monkeypatch):
    _test_stripe_env(monkeypatch)
    stored = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    requested = stored.lower()
    payload = {
        **SAMPLE_ANALYSIS,
        "analysis_id": requested,
        "tax_profile": {"tax_year": 2025},
    }
    remember_analysis(stored, "test-user-123", payload)
    snapshot_client = _wire_real_snapshot_client(monkeypatch, [])
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: ({
            "id": "history-row",
            "user_id": "test-user-123",
            "result": {"analysis_id": stored, "tax_profile": {"tax_year": 2025}},
        }, True),
    )
    captured = {}
    captured_keys = []

    def _create_checkout(**kwargs):
        captured.update(kwargs)
        captured_keys.append(kwargs["idempotency_key"])
        return FakeCheckoutSession(**kwargs)

    monkeypatch.setattr(main.stripe.checkout.Session, "create", _create_checkout)
    panel_analysis = {**SAMPLE_ANALYSIS, "analysis_id": requested}
    paid_session = SimpleNamespace(
        id="cs_test_packet_abc",
        payment_status="paid",
        amount_total=4900,
        status="complete",
        currency="usd",
        mode="payment",
        livemode=False,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": stored,
            "user_id": "test-user-123",
            "tax_year": "2025",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args, **_kwargs: True)

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": requested, "analysis": panel_analysis},
    )

    assert response.status_code == 200, response.text
    canonical_key = main._packet_checkout_idempotency_key(
        "test-user-123", requested, 2025
    )
    assert captured["metadata"]["analysis_id"] == requested
    assert response.json()["analysis_id"] == requested
    assert captured["idempotency_key"] == canonical_key

    replay = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": stored, "analysis": {**SAMPLE_ANALYSIS, "analysis_id": stored}},
    )
    assert replay.status_code == 200, replay.text
    assert captured_keys == [canonical_key, canonical_key]
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0]["analysis_id"] == requested

    confirm = client.post(
        "/api/year-close-packet/confirm",
        json={
            "analysis_id": stored,
            "packet_analysis": requested,
            "session_id": "cs_test_packet_abc",
            "analysis": panel_analysis,
        },
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["paid"] is True
    assert confirm.json()["analysis_id"] == requested
    assert snapshot_client.rows[0]["paid_at"]
    assert snapshot_client.rows[0]["analysis_id"] == requested

    downloaded = client.post(
        "/api/year-close-packet/download",
        json={
            "analysis_id": stored,
            "session_id": "cs_test_packet_abc",
            "analysis": panel_analysis,
        },
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["content-type"] == "application/pdf"
    assert downloaded.content.startswith(b"%PDF")
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0]["analysis_id"] == requested


def test_checkout_claims_guest_snapshot_and_keeps_its_full_payload(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "guest-with-private-lots"
    private_analysis = {
        **SAMPLE_ANALYSIS,
        "analysis_id": analysis_id,
        "lot_match_report": {
            "matched": [{"symbol": "AAPL", "quantity": 10}],
            "gap": [{"symbol": "NVDA", "quantity": 2}],
            "unmatched": [],
            "matched_count": 1,
            "gap_count": 1,
            "unmatched_count": 0,
        },
    }
    public_analysis = {
        **private_analysis,
        "lot_match_report": {
            **private_analysis["lot_match_report"],
            "matched": [],
            "gap": [],
        },
    }
    remember_analysis(analysis_id, "", private_analysis)
    original = PACKET_STORE[analysis_id]["payload"]
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: ({"result": private_analysis}, True),
    )
    captured = {}
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: captured.update(kwargs) or FakeCheckoutSession(**kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": analysis_id, "analysis": public_analysis},
    )

    assert response.status_code == 200
    assert response.json()["analysis_id"] == analysis_id
    assert captured["metadata"]["analysis_id"] == analysis_id
    assert PACKET_STORE[analysis_id]["user_id"] == "test-user-123"
    assert PACKET_STORE[analysis_id]["payload"] == original
    assert PACKET_STORE[analysis_id]["payload"]["lot_match_report"]["matched"] == [
        {"symbol": "AAPL", "quantity": 10}
    ]
    assert _FAKE_PACKET_SNAPSHOTS[("test-user-123", analysis_id)]["packet_payload"] == original


def test_checkout_rejects_guest_snapshot_claimed_with_only_an_id(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = SAMPLE_ANALYSIS["analysis_id"]
    remember_analysis(analysis_id, "", SAMPLE_ANALYSIS)
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: (None, True),
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **_kwargs: pytest.fail("unverified guest claims cannot open Checkout"),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": analysis_id, "analysis": {"analysis_id": analysis_id}},
    )

    assert response.status_code == 409
    assert PACKET_STORE[analysis_id]["user_id"] == ""
    assert ("test-user-123", analysis_id) not in _FAKE_PACKET_SNAPSHOTS


def test_checkout_reuses_durable_same_year_entitlement_without_charging_again(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-second-upload"
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": analysis_id}
    remember_analysis(analysis_id, "test-user-123", analysis)
    monkeypatch.setattr(
        main,
        "lookup_packet_grant_for_tax_year",
        lambda *_args: ("cs_test_prior_year", True),
    )
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs) or FakeCheckoutSession(**kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": analysis_id, "analysis": analysis},
    )

    assert response.status_code == 200, response.text
    assert response.json()["already_paid"] is True
    assert response.json()["session_id"] == "cs_test_prior_year"
    assert created == []
    assert _FAKE_PACKET_SNAPSHOTS[("test-user-123", analysis_id)]["paid_at"]


def test_checkout_restores_private_snapshot_after_worker_restart(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-cold-checkout"
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": analysis_id}
    remember_analysis(analysis_id, "test-user-123", analysis)
    packet_payload = PACKET_STORE[analysis_id]["payload"]
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", analysis_id)] = {
        "analysis_id": analysis_id,
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": packet_payload,
        "paid_at": None,
    }
    reset_packet_store()
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs) or FakeCheckoutSession(**kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": analysis_id, "analysis": analysis},
    )

    assert response.status_code == 200, response.text
    assert response.json()["analysis_id"] == analysis_id
    assert created[0]["metadata"]["analysis_id"] == analysis_id
    assert created[0]["metadata"]["tax_year"] == "2025"
    assert PACKET_STORE[analysis_id]["payload"] == packet_payload


def test_cold_checkout_recognizes_paid_year_without_creating_another_session(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-cold-paid-year"
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": analysis_id}
    remember_analysis(analysis_id, "test-user-123", analysis)
    packet_payload = PACKET_STORE[analysis_id]["payload"]
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", analysis_id)] = {
        "analysis_id": analysis_id,
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": packet_payload,
        "packet_session_id": "cs_test_prior_year",
        "paid_at": "now",
    }
    reset_packet_store()
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    monkeypatch.setattr(
        main,
        "lookup_packet_grant_for_tax_year",
        lambda *_args: ("cs_test_prior_year", True),
    )
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": analysis_id, "analysis": analysis},
    )

    assert response.status_code == 200, response.text
    assert response.json()["already_paid"] is True
    assert response.json()["session_id"] == "cs_test_prior_year"
    assert created == []
    assert PACKET_STORE[analysis_id]["payload"] == packet_payload


def test_checkout_retrieves_deleted_document_entitlement_before_reuse(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-new-for-paid-year"
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": analysis_id}
    remember_analysis(analysis_id, "test-user-123", analysis)
    source_id = "analysis-deleted-source"
    _FAKE_PACKET_ENTITLEMENTS[(
        "test-user-123", 2025, "cs_test_deleted_source"
    )] = {
        "analysis_id": source_id,
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_session_id": "cs_test_deleted_source",
    }
    original_receipt = {
        "analysis_id": source_id,
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_session_id": "cs_test_deleted_source",
    }
    monkeypatch.setattr(main, "save_packet_entitlement", db.save_packet_entitlement)
    monkeypatch.setattr(
        db,
        "get_supabase",
        lambda: _FakeEntitlementDatabase([], [original_receipt]),
    )
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    prior_session = _stripe_object_session(
        id="cs_test_deleted_source",
        metadata={"analysis_id": source_id, "tax_year": "2025"},
    )
    retrieved = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda session_id, **_kwargs: retrieved.append(session_id) or prior_session,
    )
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": analysis_id, "analysis": analysis},
    )

    assert response.status_code == 200, response.text
    assert response.json()["already_paid"] is True
    assert response.json()["session_id"] == "cs_test_deleted_source"
    assert response.json()["analysis_id"] == analysis_id
    assert retrieved == ["cs_test_deleted_source"]
    assert created == []


def test_checkout_and_grant_ignore_paid_other_year_case_variant(monkeypatch):
    """One canonical paid year blocks checkout and a second grant."""
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    canonical = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    user = "test-user-123"
    paid_payload = {
        "analysis_id": canonical,
        "marker": "paid-2024",
        "tax_profile": {"tax_year": 2024, "filing_status": "single"},
    }
    snapshot_client = _wire_real_snapshot_client(
        monkeypatch,
        [{
            "analysis_id": canonical,
            "user_id": user,
            "tax_year": 2024,
            "packet_payload": paid_payload,
            "packet_session_id": "cs_other_year",
            "paid_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
        }],
    )
    year_2025 = {
        **SAMPLE_ANALYSIS,
        "analysis_id": canonical,
        "analysis_tax_year": 2025,
        "tax_profile": {"tax_year": 2025, "filing_status": "single"},
    }
    remember_analysis(canonical, user, year_2025)
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs) or FakeCheckoutSession(**kwargs),
    )

    checkout = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": canonical.upper(), "analysis": year_2025},
    )

    assert checkout.status_code == 409, checkout.text
    assert checkout.json()["detail"] == _PAID_OTHER_YEAR_DETAIL
    assert created == []
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0]["analysis_id"] == canonical
    assert snapshot_client.rows[0]["tax_year"] == 2024
    assert snapshot_client.rows[0]["packet_session_id"] == "cs_other_year"
    assert snapshot_client.rows[0]["paid_at"] == "2026-01-01T00:00:00+00:00"
    assert snapshot_client.rows[0]["packet_payload"]["marker"] == "paid-2024"

    granted = _post_signed_webhook(
        _packet_checkout_event(
            analysis_id=canonical.upper(),
            tax_year="2025",
            event_id="evt_second_year_blocked",
        )
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["granted"] is False
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0]["tax_year"] == 2024
    assert snapshot_client.rows[0]["packet_session_id"] == "cs_other_year"
    receipt = _FAKE_PACKET_ENTITLEMENTS[(user, 2025, "cs_test_paid_1")]
    assert receipt["analysis_id"] == f"conflict:{canonical}"
    assert receipt["tax_year"] == 2025


def test_checkout_already_paid_accepts_entitlement_analysis_id_case_variant(monkeypatch):
    _test_stripe_env(monkeypatch)
    stored = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    other = stored.lower()
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": other}
    remember_analysis(other, "test-user-123", analysis)
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", other)] = {
        "analysis_id": other,
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": analysis,
        "packet_session_id": None,
        "paid_at": None,
    }
    _FAKE_PACKET_ENTITLEMENTS[(
        "test-user-123", 2025, "cs_test_case_variant"
    )] = {
        "analysis_id": other,
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_session_id": "cs_test_case_variant",
    }
    prior_session = _stripe_object_session(
        id="cs_test_case_variant",
        metadata={
            "analysis_id": other,
            "user_id": "test-user-123",
            "tax_year": "2025",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: prior_session,
    )
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": stored, "analysis": {**analysis, "analysis_id": stored}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["already_paid"] is True
    assert response.json()["session_id"] == "cs_test_case_variant"
    assert created == []


def test_checkout_rejects_deleted_document_entitlement_for_wrong_tax_year(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-2026-for-2025-payment"
    analysis = {
        **SAMPLE_ANALYSIS,
        "analysis_id": analysis_id,
        "tax_profile": {"tax_year": 2026, "filing_status": "single"},
    }
    remember_analysis(analysis_id, "test-user-123", analysis)
    _FAKE_PACKET_ENTITLEMENTS[(
        "test-user-123", 2026, "cs_test_wrong_year"
    )] = {
        "analysis_id": "analysis-2025-source",
        "user_id": "test-user-123",
        "tax_year": 2026,
        "packet_session_id": "cs_test_wrong_year",
    }
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    prior_session = _stripe_object_session(
        id="cs_test_wrong_year",
        metadata={"analysis_id": "analysis-2025-source", "tax_year": "2025"},
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: prior_session,
    )
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": analysis_id, "analysis": analysis},
    )

    assert response.status_code == 503
    assert created == []


def test_download_does_not_serve_cached_packet_after_durable_payload_delete(monkeypatch):
    remember_analysis("analysis-deleted-document", "test-user-123", SAMPLE_ANALYSIS)
    assert mark_paid(
        "analysis-deleted-document",
        "cs_test_deleted_document",
        user_id="test-user-123",
    )
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", "analysis-deleted-document")] = {
        "analysis_id": "analysis-deleted-document",
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": None,
        "packet_session_id": "cs_test_deleted_document",
        "paid_at": "now",
    }

    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": "analysis-deleted-document"},
    )

    assert response.status_code == 409
    assert "source document" in response.json()["detail"]


def test_checkout_does_not_insert_history_for_idless_analysis_key(monkeypatch):
    _test_stripe_env(monkeypatch)
    inserted = []
    monkeypatch.setattr(
        main,
        "save_analysis_history",
        lambda *args, **kwargs: inserted.append((args, kwargs)),
    )
    analysis = {key: value for key, value in SAMPLE_ANALYSIS.items() if key != "analysis_id"}

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "local-deadbeef", "analysis": analysis},
    )

    assert response.status_code == 400
    assert inserted == []


def test_checkout_stops_before_stripe_when_history_lookup_fails(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, False))
    created = []
    monkeypatch.setattr(main.stripe.checkout.Session, "create", lambda **kwargs: created.append(kwargs))

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "analysis-sample-1", "analysis": SAMPLE_ANALYSIS},
    )

    assert response.status_code == 503
    assert created == []


def test_checkout_rejects_analysis_snapshot_owned_by_another_user(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("analysis-sample-1", "user-A", SAMPLE_ANALYSIS)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    created = []
    monkeypatch.setattr(main.stripe.checkout.Session, "create", lambda **kwargs: created.append(kwargs))

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "analysis-sample-1", "analysis": SAMPLE_ANALYSIS},
    )

    assert response.status_code == 409
    assert created == []
    assert PACKET_STORE["analysis-sample-1"]["user_id"] == "user-A"


def test_checkout_claims_guest_snapshot_after_durable_copy(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    remember_analysis("analysis-sample-1", "", SAMPLE_ANALYSIS)
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs) or FakeCheckoutSession(**kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "analysis-sample-1", "analysis": SAMPLE_ANALYSIS},
    )
    assert response.status_code == 200, response.text
    assert created
    assert PACKET_STORE["analysis-sample-1"]["user_id"] == "test-user-123"


def test_checkout_rekeys_local_analysis_per_user(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("local-analysis", "user-A", SAMPLE_ANALYSIS)
    monkeypatch.setitem(main.app.dependency_overrides, get_current_user, lambda: "user-B")
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return FakeCheckoutSession(**kwargs)

    monkeypatch.setattr(main.stripe.checkout.Session, "create", create)
    analysis_without_id = {
        key: value for key, value in SAMPLE_ANALYSIS.items() if key != "analysis_id"
    }
    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "local-analysis", "analysis": analysis_without_id},
    )

    assert response.status_code == 409
    assert "owner-scoped private snapshot" in response.json()["detail"]
    assert captured == {}
    assert PACKET_STORE["local-analysis"]["user_id"] == "user-A"
    assert PACKET_STORE["local-analysis"]["paid"] is False


def test_checkout_copies_same_users_local_snapshot_to_canonical_id(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("local-analysis", "test-user-123", LOT_MATCH_ANALYSIS)
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: FakeCheckoutSession(**kwargs),
    )
    analysis_without_id = {
        key: value for key, value in LOT_MATCH_ANALYSIS.items() if key != "analysis_id"
    }

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "local-analysis", "analysis": analysis_without_id},
    )

    assert response.status_code == 200, response.text
    canonical_id = response.json()["analysis_id"]
    assert canonical_id != "local-analysis"
    assert PACKET_STORE[canonical_id]["user_id"] == "test-user-123"
    assert PACKET_STORE[canonical_id]["payload"]["analysis_id"] == canonical_id
    report = PACKET_STORE[canonical_id]["payload"]["lot_match_report"]
    assert [row["symbol"] for row in report["matched"]] == ["AMD"]
    assert [row["symbol"] for row in report["gap"]] == ["NVDA"]
    assert {row["symbol"] for row in report["unmatched"]} == {"SPX", "META"}
    assert PACKET_STORE["local-analysis"]["paid"] is False
    saved_snapshot = _FAKE_PACKET_SNAPSHOTS[("test-user-123", canonical_id)]
    assert saved_snapshot["packet_payload"]["lot_match_report"] == report


def test_checkout_rejects_local_alias_when_analysis_does_not_match_snapshot(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("local-analysis", "test-user-123", LOT_MATCH_ANALYSIS)
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs),
    )
    mismatched_analysis = {
        key: value for key, value in LOT_MATCH_ANALYSIS.items() if key != "analysis_id"
    }
    mismatched_analysis["tax_profile"] = {
        **mismatched_analysis["tax_profile"],
        "tax_year": 2025,
    }

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "local-analysis", "analysis": mismatched_analysis},
    )

    assert response.status_code == 409
    assert created == []
    assert "local-analysis" not in PACKET_STORE or not PACKET_STORE["local-analysis"].get("paid")


def test_checkout_does_not_create_paid_checkout_without_deletable_history(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    monkeypatch.setattr(main, "save_analysis_history", lambda *_args, **_kwargs: None)
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "analysis-sample-1", "analysis": SAMPLE_ANALYSIS},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Could not save this analysis before checkout. Please retry."
    )
    assert created == []
    # The unpaid snapshot is saved before history ensure. A failed ensure must
    # not open Stripe or mark that row paid.
    assert _FAKE_PACKET_SNAPSHOTS
    assert all(not row.get("paid_at") for row in _FAKE_PACKET_SNAPSHOTS.values())


def test_checkout_refuses_to_adopt_unowned_local_snapshot(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("local-analysis", "", LOT_MATCH_ANALYSIS)
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs),
    )
    analysis = {key: value for key, value in LOT_MATCH_ANALYSIS.items() if key != "analysis_id"}

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "local-analysis", "analysis": analysis},
    )

    assert response.status_code == 409
    assert created == []
    assert PACKET_STORE["local-analysis"]["user_id"] == ""


def test_checkout_refuses_live_key_on_staging(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://options-tax-hub-client-staging.onrender.com")
    monkeypatch.setenv("STRIPE_FORCE_TEST_MODE", "true")
    monkeypatch.delenv("STRIPE_SECRET_KEY_TEST", raising=False)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_fake_live_key")
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "sk_live_fake_live_key")
    monkeypatch.setattr(
        main, "FRONTEND_URL", "https://options-tax-hub-client-staging.onrender.com"
    )
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "analysis-sample-1", "analysis": SAMPLE_ANALYSIS},
    )
    assert response.status_code == 503
    assert "TEST" in response.json()["detail"]


def test_webhook_unlocks_download(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: True)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    receipt = {
        "analysis_id": "analysis-sample-1",
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_session_id": "cs_test_paid_1",
    }
    monkeypatch.setattr(main, "save_packet_entitlement", db.save_packet_entitlement)
    monkeypatch.setattr(
        db,
        "get_supabase",
        lambda: _FakeEntitlementDatabase([receipt], [], [receipt]),
    )

    unpaid = client.get("/api/year-close-packet/download?analysis_id=analysis-sample-1")
    assert unpaid.status_code == 403

    webhook = _post_signed_webhook(_packet_checkout_event())
    assert webhook.status_code == 200
    assert webhook.json()["granted"] is True
    duplicate = _post_signed_webhook(_packet_checkout_event())
    assert duplicate.status_code == 200
    assert duplicate.json()["granted"] is True
    assert PACKET_STORE["analysis-sample-1"]["session_ids"] == {"cs_test_paid_1"}

    paid = client.get("/api/year-close-packet/download?analysis_id=analysis-sample-1")
    assert paid.status_code == 200
    assert paid.headers["content-type"].startswith("application/pdf")
    pdf_text = _pdf_text(paid.content)
    assert "281,823.83" in pdf_text
    assert "AMD" in pdf_text
    assert "300.00" in pdf_text


def test_confirm_session_unlocks_download(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: True)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)

    paid_session = SimpleNamespace(
        id="cs_test_paid_confirm",
        payment_status="paid",
        amount_total=4900,
        status="complete",
        currency="usd",
        mode="payment",
        livemode=False,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
            "user_id": "test-user-123",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda session_id, **_kwargs: paid_session,
    )

    confirm = client.post(
        "/api/year-close-packet/confirm",
        json={
            "analysis_id": "analysis-sample-1",
            "session_id": "cs_test_paid_confirm",
        },
    )
    assert confirm.status_code == 200
    assert confirm.json()["paid"] is True

    paid = client.get(
        "/api/year-close-packet/download",
        params={
            "analysis_id": "analysis-sample-1",
            "session_id": "cs_test_paid_confirm",
        },
    )
    assert paid.status_code == 200
    assert "year-close-packet.pdf" in paid.headers.get("content-disposition", "")


def test_confirm_does_not_grant_transient_access_when_persistence_fails(monkeypatch):
    _test_stripe_env(monkeypatch)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: None)
    monkeypatch.setattr(main, "mark_packet_snapshot_paid", lambda *_args: None)
    paid_session = _stripe_object_session(
        id="cs_test_paid_persistence_failure",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
            "user_id": "test-user-123",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )

    response = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": paid_session.id, "analysis_id": "analysis-sample-1"},
    )

    assert response.status_code == 503
    assert PACKET_STORE["analysis-sample-1"]["paid"] is False


def test_confirm_retry_keeps_original_entitlement_analysis_id(monkeypatch):
    """A second confirm stays paid and does not replace the stored analysis id."""
    _test_stripe_env(monkeypatch)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    session = _stripe_object_session(
        id="cs_test_confirm_retry",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
            "user_id": "test-user-123",
            "tax_year": "2025",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )

    class _MemoryEntitlements:
        def __init__(self):
            self.rows = []
            self._mode = "read"
            self._filters = {}
            self._pending = None
            self._table = None

        def table(self, name):
            self._table = name
            self._mode = "read"
            self._filters = {}
            self._pending = None
            return self

        def select(self, *_args):
            if self._mode != "insert":
                self._mode = "read"
            return self

        def eq(self, key, value):
            self._filters[key] = value
            return self

        def limit(self, *_args):
            return self

        def insert(self, row):
            self._mode = "insert"
            self._pending = dict(row)
            return self

        def execute(self):
            if self._table != "year_close_packet_entitlements":
                return SimpleNamespace(data=[])
            if self._mode == "insert":
                pending = self._pending
                for row in self.rows:
                    if (
                        row["user_id"] == pending["user_id"]
                        and int(row["tax_year"]) == int(pending["tax_year"])
                        and row["packet_session_id"] == pending["packet_session_id"]
                    ):
                        error = Exception(
                            "duplicate key value violates unique constraint"
                        )
                        error.code = "23505"
                        raise error
                self.rows.append(pending)
                self._mode = "read"
                return SimpleNamespace(data=[dict(pending)])
            matched = [
                dict(row)
                for row in self.rows
                if all(row.get(key) == value for key, value in self._filters.items())
            ]
            return SimpleNamespace(data=matched[:1])

    store = _MemoryEntitlements()
    monkeypatch.setattr(db, "get_supabase", lambda: store)
    monkeypatch.setattr(main, "save_packet_entitlement", db.save_packet_entitlement)

    body = {"session_id": session.id, "analysis_id": "analysis-sample-1"}
    first = client.post("/api/year-close-packet/confirm", json=body)
    second = client.post("/api/year-close-packet/confirm", json=body)

    assert first.status_code == 200, first.text
    assert first.json()["paid"] is True
    assert second.status_code == 200, second.text
    assert second.json()["paid"] is True
    kept = db.save_packet_entitlement(
        "followup-analysis",
        "test-user-123",
        2025,
        session.id,
    )
    assert kept["analysis_id"] == "analysis-sample-1"
    assert len(store.rows) == 1
    assert store.rows[0]["analysis_id"] == "analysis-sample-1"


def test_confirm_returns_open_checkout_to_client_for_resume(monkeypatch):
    _test_stripe_env(monkeypatch)
    session = _stripe_object_session(
        id="cs_test_open_checkout",
        status="open",
        payment_status="unpaid",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
            "user_id": "test-user-123",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )

    response = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": session.id, "analysis_id": "analysis-sample-1"},
    )

    assert response.status_code == 409
    assert "still open" in response.json()["detail"].lower()


def test_confirm_retires_expired_checkout_session(monkeypatch):
    _test_stripe_env(monkeypatch)
    session = _stripe_object_session(
        id="cs_test_expired_checkout",
        status="expired",
        payment_status="unpaid",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
            "user_id": "test-user-123",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )

    response = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": session.id, "analysis_id": "analysis-sample-1"},
    )

    assert response.status_code == 410


def test_confirm_rejects_checkout_session_owned_by_another_user(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("victim-analysis", "user-A", SAMPLE_ANALYSIS)
    monkeypatch.setitem(main.app.dependency_overrides, get_current_user, lambda: "user-B")
    paid_session = _stripe_object_session(
        id="cs_test_wrong_owner",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "victim-analysis",
            "user_id": "user-A",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )

    response = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": paid_session.id, "analysis_id": "victim-analysis"},
    )

    assert response.status_code == 403
    assert PACKET_STORE["victim-analysis"]["user_id"] == "user-A"
    assert PACKET_STORE["victim-analysis"]["paid"] is False


def test_download_uses_owner_snapshot_when_packet_cache_belongs_to_another_user(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("victim-analysis", "user-A", SAMPLE_ANALYSIS)
    monkeypatch.setitem(main.app.dependency_overrides, get_current_user, lambda: "user-B")
    _FAKE_PACKET_SNAPSHOTS[("user-B", "victim-analysis")] = {
        "analysis_id": "victim-analysis",
        "user_id": "user-B",
        "tax_year": 2025,
        "packet_payload": build_packet_payload(
            {**SAMPLE_ANALYSIS, "analysis_id": "victim-analysis"},
            analysis_id="victim-analysis",
        ),
        "packet_session_id": None,
        "paid_at": None,
    }
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda analysis_id, user_id: (
            {
                "id": "user-b-history-row",
                "user_id": user_id,
                "result": {
                    "analysis_id": analysis_id,
                    "tax_profile": {"tax_year": 2025},
                },
            },
            True,
        ),
    )
    paid_session = _stripe_object_session(
        id="cs_test_cross_owner_analysis",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "victim-analysis",
            "user_id": "user-B",
            "tax_year": "2025",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )
    monkeypatch.setattr(
        main,
        "_grant_packet_from_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("download must not grant")
        ),
    )
    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": "victim-analysis", "session_id": paid_session.id},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Year-close packet download requires payment."
    assert not response.content.startswith(b"%PDF")
    row = _FAKE_PACKET_SNAPSHOTS[("user-B", "victim-analysis")]
    assert row["analysis_id"] == "victim-analysis"
    assert row["tax_year"] == 2025
    assert row["paid_at"] is None
    assert PACKET_STORE["victim-analysis"]["user_id"] == "user-A"
    assert PACKET_STORE["victim-analysis"]["paid"] is False


def test_reused_same_year_checkout_survives_packet_store_restart(monkeypatch):
    _test_stripe_env(monkeypatch)
    session_id = "cs_test_year_grant"
    target = {
        "id": "history-target",
        "user_id": "test-user-123",
        "result": {
            "analysis_id": "analysis-followup",
            "packet_unlocked": True,
            "packet_session_id": session_id,
            "tax_profile": {"tax_year": 2025},
        },
    }
    # The source analysis may have been deleted; the owner-scoped target row
    # records the inherited entitlement and is sufficient proof.
    rows = {"analysis-followup": target}
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", "analysis-followup")] = {
        "analysis_id": "analysis-followup",
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": build_packet_payload(
            {
                **LOT_MATCH_ANALYSIS,
                "analysis_id": "analysis-followup",
                "tax_profile": {"tax_year": 2025, "filing_status": "single"},
            },
            analysis_id="analysis-followup",
        ),
        "packet_session_id": session_id,
        "paid_at": "now",
    }
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda analysis_id, _user_id: (rows.get(analysis_id), True),
    )
    monkeypatch.setattr(
        main,
        "get_analysis_by_id",
        lambda analysis_id, _user_id, client=None: rows.get(analysis_id),
    )
    monkeypatch.setattr(
        main,
        "get_analysis_by_result_analysis_id",
        lambda analysis_id, _user_id, client=None: rows.get(analysis_id),
    )
    reused_session = _stripe_object_session(
        id=session_id,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-source",
            "user_id": "test-user-123",
            "tax_year": "2025",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: reused_session,
    )
    reset_packet_store()

    response = client.post(
        "/api/year-close-packet/download",
        json={
            "analysis_id": "analysis-followup",
            "session_id": session_id,
            "analysis": {**SAMPLE_ANALYSIS, "analysis_id": "analysis-followup"},
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    restored_pdf_text = _pdf_text(response.content)
    assert "Matched (1)" in restored_pdf_text
    assert "Gap (1)" in restored_pdf_text
    assert "Unmatched (2)" in restored_pdf_text
    assert "matched AMD" in restored_pdf_text
    assert "matched_settlement_gap NVDA" in restored_pdf_text
    assert "1099_only SPX" in restored_pdf_text
    assert "csv_only META" in restored_pdf_text
    assert "analysis-followup" not in PACKET_STORE


def test_durable_paid_snapshot_rejects_session_stamped_for_another_tax_year(monkeypatch):
    _test_stripe_env(monkeypatch)
    session_id = "cs_test_year_mismatch"
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", "analysis-followup")] = {
        "analysis_id": "analysis-followup",
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": build_packet_payload(
            {**SAMPLE_ANALYSIS, "analysis_id": "analysis-followup"},
            analysis_id="analysis-followup",
        ),
        "packet_session_id": session_id,
        "paid_at": "now",
    }
    paid_session = _stripe_object_session(
        id=session_id,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-source",
            "user_id": "test-user-123",
            "tax_year": "2026",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )

    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": "analysis-followup", "session_id": session_id},
    )

    assert response.status_code == 403


def test_reused_packet_session_does_not_unlock_a_different_tax_year(monkeypatch):
    _test_stripe_env(monkeypatch)
    session_id = "cs_test_year_grant"
    source = {
        "id": "history-source",
        "user_id": "test-user-123",
        "result": {
            "analysis_id": "analysis-source",
            "packet_unlocked": True,
            "packet_session_id": session_id,
            "tax_profile": {"tax_year": 2025},
        },
    }
    target = {
        "id": "history-target",
        "user_id": "test-user-123",
        "result": {
            "analysis_id": "analysis-followup",
            "packet_unlocked": False,
            "packet_session_id": None,
            "tax_profile": {"tax_year": 2026},
        },
    }
    rows = {"analysis-source": source, "analysis-followup": target}
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda analysis_id, _user_id: (rows.get(analysis_id), True),
    )
    monkeypatch.setattr(
        main,
        "get_analysis_by_id",
        lambda analysis_id, _user_id, client=None: rows.get(analysis_id),
    )
    monkeypatch.setattr(
        main,
        "get_analysis_by_result_analysis_id",
        lambda analysis_id, _user_id, client=None: rows.get(analysis_id),
    )
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda analysis_id, _user_id: (rows.get(analysis_id), True),
    )
    reused_session = _stripe_object_session(
        id=session_id,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-source",
            "user_id": "test-user-123",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: reused_session,
    )
    reset_packet_store()

    response = client.post(
        "/api/year-close-packet/download",
        json={
            "analysis_id": "analysis-followup",
            "session_id": session_id,
            "analysis": {
                **SAMPLE_ANALYSIS,
                "analysis_id": "analysis-followup",
                "tax_profile": {"tax_year": 2026},
            },
        },
    )

    assert response.status_code == 403
    assert "analysis-followup" not in PACKET_STORE


def test_same_year_download_database_failure_is_retryable(monkeypatch):
    _test_stripe_env(monkeypatch)
    reused_session = _stripe_object_session(
        id="cs_test_same_year_outage",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-source",
            "user_id": "test-user-123",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: reused_session,
    )
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: (None, False),
    )

    response = client.get(
        "/api/year-close-packet/download",
        params={
            "analysis_id": "analysis-followup",
            "session_id": reused_session.id,
        },
    )

    assert response.status_code == 503


def test_three_dollar_tip_does_not_unlock_packet(monkeypatch):
    _test_stripe_env(monkeypatch)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)

    tip_session = SimpleNamespace(
        id="cs_test_tip_coffee",
        payment_status="paid",
        amount_total=300,
        metadata={"product": "tip", "tier": "coffee"},
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda session_id, **_kwargs: tip_session,
    )

    confirm = client.post(
        "/api/year-close-packet/confirm",
        json={
            "analysis_id": "analysis-sample-1",
            "session_id": "cs_test_tip_coffee",
        },
    )
    assert confirm.status_code == 403

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    webhook = _post_signed_webhook({
        "id": "evt_tip", "object": "event", "type": "checkout.session.completed",
        "data": {"object": {
            "object": "checkout.session", "id": "cs_test_tip_coffee",
            "status": "complete", "payment_status": "paid", "amount_total": 300,
            "currency": "usd", "mode": "payment", "livemode": False,
            "metadata": {"tier": "coffee"},
        }},
    })
    assert webhook.status_code == 200
    assert webhook.json()["granted"] is False

    unpaid = client.get("/api/year-close-packet/download?analysis_id=analysis-sample-1")
    assert unpaid.status_code == 403


def test_tips_checkout_does_not_set_packet_entitlement(monkeypatch):
    _test_stripe_env(monkeypatch)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)

    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "sk_test_tips")
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: FakeCheckoutSession(**kwargs),
    )
    tip = client.post("/api/tips/checkout", json={"tier": "coffee"})
    assert tip.status_code == 200
    unpaid = client.get("/api/year-close-packet/download?analysis_id=analysis-sample-1")
    assert unpaid.status_code == 403


def test_session_grants_packet_accepts_uuid_case_variant():
    stored = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    session = SimpleNamespace(
        id="cs_case",
        payment_status="paid",
        amount_total=4900,
        status="complete",
        currency="usd",
        mode="payment",
        livemode=False,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": stored,
            "user_id": "test-user-123",
        },
    )
    assert session_grants_packet(session, stored.lower()) is True
    assert session_grants_packet(session, stored) is True
    assert session_grants_packet(session, "not-a-uuid") is False
    assert session_grants_packet(session, "local-analysis") is False


def test_session_grants_packet_rejects_wrong_product_and_amount():
    ok = SimpleNamespace(
        payment_status="paid",
        amount_total=4900,
        status="complete",
        currency="usd",
        mode="payment",
        livemode=False,
        metadata={"product": PACKET_METADATA_PRODUCT, "analysis_id": "a1"},
    )
    assert session_grants_packet(ok, "a1") is True
    tip = SimpleNamespace(
        payment_status="paid",
        amount_total=300,
        metadata={"product": "tip", "analysis_id": "a1"},
    )
    assert session_grants_packet(tip, "a1") is False
    cheap = SimpleNamespace(
        payment_status="paid",
        amount_total=300,
        metadata={"product": PACKET_METADATA_PRODUCT, "analysis_id": "a1"},
    )
    assert session_grants_packet(cheap, "a1") is False


def test_staging_uses_test_key_not_live(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://options-tax-hub-client-staging.onrender.com")
    monkeypatch.setenv("STRIPE_FORCE_TEST_MODE", "true")
    monkeypatch.setenv("STRIPE_SECRET_KEY_TEST", "sk_test_abc")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_abc")
    assert packet_requires_test_stripe() is True
    key, reason = resolve_packet_stripe_secret_key("sk_live_abc")
    assert key == "sk_test_abc"
    assert reason == "test"


def test_custom_domain_frontend_uses_live_stripe(monkeypatch):
    monkeypatch.delenv("STRIPE_FORCE_TEST_MODE", raising=False)
    monkeypatch.setenv("FRONTEND_URL", "https://www.optionstaxhub.com")
    monkeypatch.delenv("RENDER_SERVICE_NAME", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert packet_requires_test_stripe() is False


def test_post_download_rebuilds_from_analysis_json(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: True)
    saved_analysis = {**SAMPLE_ANALYSIS, "analysis_id": "fresh-id"}
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: ({"result": saved_analysis}, True),
    )
    paid_session = SimpleNamespace(
        id="cs_test_paid_post",
        payment_status="paid",
        amount_total=4900,
        status="complete",
        currency="usd",
        mode="payment",
        livemode=False,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "fresh-id",
            "user_id": "test-user-123",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda session_id, **_kwargs: paid_session,
    )
    monkeypatch.setattr(
        main,
        "_grant_packet_from_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("download must not grant")
        ),
    )
    monkeypatch.setattr(
        main,
        "_persist_packet_grant",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("download must not grant")
        ),
    )
    response = client.post(
        "/api/year-close-packet/download",
        json={
            "analysis_id": "fresh-id",
            "session_id": "cs_test_paid_post",
            "analysis": {
                **saved_analysis,
                "summary": {"realized_summary": {"total_net": 999999}},
            },
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Year-close packet download requires payment."
    assert not response.content.startswith(b"%PDF")


def test_history_flags_alone_do_not_authorize_reused_year_grant(monkeypatch):
    session = _stripe_object_session(
        id="cs_test_history_flag_only",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "source-analysis",
            "user_id": "test-user-123",
            "tax_year": "2025",
        },
    )
    monkeypatch.setattr(main, "get_packet_snapshot", lambda *_args, **_kwargs: (None, True))
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: (
            {
                "result": {
                    "analysis_id": "target-analysis",
                    "tax_profile": {"tax_year": 2025},
                    "packet_unlocked": True,
                    "packet_session_id": session.id,
                }
            },
            True,
        ),
    )

    assert (
        main._is_persisted_same_year_packet_grant(
            "target-analysis", session.id, "test-user-123", session
        )
        is False
    )


def test_local_analysis_alias_cannot_select_another_paid_analysis(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: True)
    saved_analysis = {**SAMPLE_ANALYSIS, "analysis_id": "analysis-sample-1"}
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", "analysis-sample-1")] = {
        "analysis_id": "analysis-sample-1",
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": build_packet_payload(saved_analysis, analysis_id="analysis-sample-1"),
        "packet_session_id": "cs_test_paid_alias",
        "paid_at": "now",
    }
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: ({"result": saved_analysis}, True),
    )
    paid_session = _stripe_object_session(
        id="cs_test_paid_alias",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
            "user_id": "test-user-123",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )

    response = client.post(
        "/api/year-close-packet/download",
        json={
            "analysis_id": "local-analysis",
            "session_id": paid_session.id,
            "analysis": {"summary": {"realized_summary": {"total_net": 999999}}},
        },
    )

    assert response.status_code == 400
    assert "Re-run" in response.json()["detail"]


def test_confirm_rejects_body_for_a_different_analysis_before_grant(monkeypatch):
    _test_stripe_env(monkeypatch)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    paid_session = _stripe_object_session(
        id="cs_test_paid_mismatched_body",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
            "user_id": "test-user-123",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )
    monkeypatch.setattr(
        main,
        "patch_analysis_result",
        lambda *_args: pytest.fail("mismatched body must be rejected before grant"),
    )

    response = client.post(
        "/api/year-close-packet/confirm",
        json={
            "analysis_id": "analysis-sample-1",
            "session_id": paid_session.id,
            "analysis": {"analysis_id": "analysis-attacker"},
        },
    )

    assert response.status_code == 400
    assert PACKET_STORE["analysis-sample-1"]["paid"] is False


def test_checkout_missing_analysis_id_is_400(monkeypatch):
    _test_stripe_env(monkeypatch)
    response = client.post("/api/year-close-packet/checkout", json={"analysis_id": ""})
    assert response.status_code == 400


def test_webhook_ignores_other_events(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    response = _post_signed_webhook({
        "id": "evt_other", "object": "event", "type": "payment_intent.succeeded",
        "data": {"object": {}},
    })
    assert response.status_code == 200
    assert response.json()["granted"] is False


def test_checkout_stripe_error_is_502(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    remember_analysis("analysis-sample-1", "", SAMPLE_ANALYSIS)

    def boom(**_kwargs):
        raise main.stripe.StripeError("nope")

    monkeypatch.setattr(main.stripe.checkout.Session, "create", boom)
    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "analysis-sample-1", "analysis": SAMPLE_ANALYSIS},
    )
    assert response.status_code == 502
    assert PACKET_STORE["analysis-sample-1"]["user_id"] == ""


def test_session_grants_packet_accepts_valid_stripe_object(monkeypatch):
    _test_stripe_env(monkeypatch)
    session = _stripe_object_session(
        payment_status="paid",
        status="complete",
        amount_total=4900,
        currency="usd",
        mode="payment",
        livemode=False,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
        },
    )
    assert session_grants_packet(session, "analysis-sample-1") is True
    assert session_grants_packet(session, "local-analysis") is False
    assert session_grants_packet(session, "") is False
    assert session_grants_packet(session, "some-other-analysis") is False


def test_session_grants_packet_never_accepts_local_analysis_as_canonical_id():
    session = _stripe_object_session(
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "local-analysis",
            "user_id": "test-user-123",
        },
    )
    assert session_grants_packet(session, "local-analysis") is False


def test_session_grants_packet_rejects_complete_but_unpaid_checkout():
    session = _stripe_object_session(
        payment_status="unpaid",
        status="complete",
        amount_total=4900,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
        },
    )
    assert session_grants_packet(session, "analysis-sample-1") is False
    missing_payment = _stripe_object_session(
        payment_status=None,
        status="complete",
        amount_total=4900,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
        },
    )
    assert session_grants_packet(missing_payment, "analysis-sample-1") is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"currency": "eur"},
        {"mode": "subscription"},
        {"livemode": True},
        {"status": "open"},
        {"payment_status": "no_payment_required"},
    ],
)
def test_session_grants_packet_requires_settled_expected_stripe_checkout(monkeypatch, overrides):
    _test_stripe_env(monkeypatch)
    fields = {
        "payment_status": "paid", "status": "complete", "amount_total": 4900,
        "currency": "usd", "mode": "payment", "livemode": False,
        "metadata": {"product": PACKET_METADATA_PRODUCT, "analysis_id": "analysis-sample-1"},
    }
    fields.update(overrides)
    assert session_grants_packet(_stripe_object_session(**fields), "analysis-sample-1") is False


def test_session_grants_packet_accepts_live_session_only_in_production(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://www.optionstaxhub.com")
    monkeypatch.setattr(main, "FRONTEND_URL", "https://www.optionstaxhub.com")
    monkeypatch.setenv("RENDER_SERVICE_NAME", "options-tax-hub-server-prod")
    monkeypatch.delenv("STRIPE_FORCE_TEST_MODE", raising=False)
    common = {
        "payment_status": "paid", "status": "complete", "amount_total": 4900,
        "currency": "usd", "mode": "payment",
        "metadata": {"product": PACKET_METADATA_PRODUCT, "analysis_id": "analysis-sample-1"},
    }
    assert session_grants_packet(
        _stripe_object_session(**common, livemode=True), "analysis-sample-1"
    ) is True
    assert session_grants_packet(
        _stripe_object_session(**common, livemode=False), "analysis-sample-1"
    ) is False


def test_download_rejects_paid_session_for_another_analysis(monkeypatch):
    _test_stripe_env(monkeypatch)
    main.remember_analysis("another-analysis", "test-user-123", SAMPLE_ANALYSIS)
    paid_session = _stripe_object_session(
        id="cs_test_paid_for_sample",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: (None, True),
    )
    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": "another-analysis", "session_id": paid_session.id},
    )
    assert response.status_code == 403
    assert not response.content.startswith(b"%PDF")
    assert PACKET_STORE["another-analysis"]["paid"] is False
    assert PACKET_STORE["another-analysis"]["session_ids"] == set()


def test_download_does_not_reuse_another_users_in_memory_grant(monkeypatch):
    _test_stripe_env(monkeypatch)
    main.remember_analysis("private-analysis", "test-user-123", SAMPLE_ANALYSIS)
    mark_paid("private-analysis", "cs_test_private", user_id="test-user-123")
    monkeypatch.setitem(
        main.app.dependency_overrides,
        get_current_user,
        lambda: "another-user",
    )
    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": "private-analysis"},
    )
    assert response.status_code == 403
    assert not response.content.startswith(b"%PDF")


def test_authorized_download_unresolvable_year_is_503(monkeypatch):
    """A paid session for this user and analysis still 503s when no year exists."""
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-unresolvable-year"
    remember_analysis(analysis_id, "test-user-123", {"analysis_id": analysis_id})
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    monkeypatch.setattr(
        main,
        "lookup_packet_entitlements_for_analysis",
        lambda *_args, **_kwargs: ([], True),
    )
    paid_session = _stripe_object_session(
        id="cs_test_authorized_no_year",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": analysis_id,
            "user_id": "test-user-123",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )

    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": analysis_id, "session_id": paid_session.id},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Could not determine the packet tax year. Please retry."
    assert not response.content.startswith(b"%PDF")
    assert PACKET_STORE[analysis_id]["paid"] is False
    assert PACKET_STORE[analysis_id]["session_ids"] == set()


def test_mark_paid_refuses_to_change_another_users_packet_record():
    remember_analysis("shared-analysis", "user-A", SAMPLE_ANALYSIS)

    assert mark_paid("shared-analysis", "cs_test_user_b", user_id="user-B") is False
    assert PACKET_STORE["shared-analysis"]["user_id"] == "user-A"
    assert PACKET_STORE["shared-analysis"]["paid"] is False


def test_same_year_grant_does_not_report_unlocked_after_owner_conflict(monkeypatch):
    analysis = {
        "analysis_id": "same-year-conflict",
        "tax_profile": {"tax_year": 2025, "filing_status": "single"},
    }
    result = main.PortfolioAnalysis.model_validate(analysis)
    remember_analysis(result.analysis_id, "user-A", analysis)
    monkeypatch.setattr(
        main,
        "lookup_packet_grant_for_tax_year",
        lambda *_args: ("cs_test_paid_by_other_owner", True),
    )

    granted = main._apply_packet_year_grant(result, "user-B")

    assert granted.packet_unlocked is False
    assert granted.packet_session_id is None
    assert PACKET_STORE[result.analysis_id]["user_id"] == "user-A"
    assert PACKET_STORE[result.analysis_id]["paid"] is False


def test_signed_in_user_can_claim_guest_snapshot_without_losing_private_payload():
    remember_analysis("guest-analysis", "", SAMPLE_ANALYSIS)
    original_payload = deepcopy(PACKET_STORE["guest-analysis"]["payload"])

    assert main.packet_store_belongs_to_user("guest-analysis", "test-user-123") is False
    assert PACKET_STORE["guest-analysis"]["user_id"] == ""
    # An id-only touch must not transfer the blank guest row or replace its payload.
    upsert_payload("guest-analysis", "test-user-123", {"analysis_id": "guest-analysis"})
    assert mark_paid("guest-analysis", "cs_test_guest", user_id="test-user-123") is False
    assert claimable_guest_packet_payload(
        "guest-analysis",
        "test-user-123",
        {"analysis_id": "guest-analysis"},
    ) is None
    assert PACKET_STORE["guest-analysis"]["user_id"] == ""
    assert PACKET_STORE["guest-analysis"]["payload"] == original_payload
    assert PACKET_STORE["guest-analysis"]["paid"] is False

    claimed_payload = claimable_guest_packet_payload(
        "guest-analysis",
        "test-user-123",
        SAMPLE_ANALYSIS,
    )
    assert claimed_payload == original_payload
    assert adopt_guest_packet("guest-analysis", "test-user-123", claimed_payload) is True
    assert mark_paid("guest-analysis", "cs_test_guest", user_id="test-user-123") is True

    claimed = PACKET_STORE["guest-analysis"]
    assert claimed["user_id"] == "test-user-123"
    assert claimed["payload"] == original_payload
    assert claimed["paid"] is True


def test_in_memory_packet_grant_requires_authenticated_owner():
    remember_analysis("private-analysis", "test-user-123", SAMPLE_ANALYSIS)
    mark_paid("private-analysis", "cs_test_private", user_id="test-user-123")

    assert main.is_packet_paid("private-analysis") is False
    assert main.is_packet_paid("private-analysis", user_id="test-user-123") is True


@pytest.mark.parametrize("headers,secret,expected_status", [
    ({}, "whsec_packet_test_secret", 400),
    ({"Stripe-Signature": "invalid-current-signature"}, "whsec_packet_test_secret", 400),
    ({"Stripe-Signature": "invalid-current-signature"}, "", 503),
])
def test_webhook_requires_configured_secret_and_valid_signature(monkeypatch, headers, secret, expected_status):
    _test_stripe_env(monkeypatch)
    if secret:
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    else:
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    request_headers = {"Content-Type": "application/json", **headers}
    if request_headers.get("Stripe-Signature") == "invalid-current-signature":
        request_headers["Stripe-Signature"] = f"t={int(time.time())},v1=bad"
    response = client.post(
        "/api/year-close-packet/webhook",
        content=json.dumps(_packet_checkout_event()).encode(),
        headers=request_headers,
    )
    assert response.status_code == expected_status


def test_async_payment_succeeded_event_unlocks_packet(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    persisted = []
    monkeypatch.setattr(
        main,
        "patch_analysis_result",
        lambda analysis_id, user_id, patch: persisted.append(
            (analysis_id, user_id, patch)
        ) or True,
    )
    event = _packet_checkout_event(
        event_type="checkout.session.async_payment_succeeded", event_id="evt_async_paid"
    )
    response = _post_signed_webhook(event)
    assert response.status_code == 200
    assert response.json()["granted"] is True
    duplicate = _post_signed_webhook(event)
    assert duplicate.status_code == 200
    assert duplicate.json()["granted"] is True
    assert persisted == [
        (
            "analysis-sample-1",
            "test-user-123",
            {"packet_unlocked": True, "packet_session_id": "cs_test_paid_1"},
        ),
        (
            "analysis-sample-1",
            "test-user-123",
            {"packet_unlocked": True, "packet_session_id": "cs_test_paid_1"},
        ),
    ]
    assert PACKET_STORE["analysis-sample-1"]["user_id"] == "test-user-123"
    assert PACKET_STORE["analysis-sample-1"]["session_ids"] == {"cs_test_paid_1"}


_LEGACY_PAYMENT_ANALYSIS = "11111111-1111-4111-8111-111111111111"


class _AliasStepClient:
    """Primary-key miss, embedded eq miss, embedded ilike miss, then alias fails."""

    def __init__(self, alias_error):
        self.alias_error = alias_error
        self.calls = 0

    def table(self, _name):
        return self

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def contains(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        self.calls += 1
        # UUID lookup is primary key, embedded eq, then the temporary ilike
        # fallback, and only then the alias table.
        if self.calls < 4:
            return SimpleNamespace(data=[])
        raise self.alias_error


def _missing_alias_table_error():
    error = Exception('relation "portfolio_analysis_id_aliases" does not exist')
    error.code = "42P01"
    return error


def _alias_column_schema_error():
    error = Exception(
        "Could not find the 'canonical_analysis_id' column of "
        "'portfolio_analysis_id_aliases' in the schema cache"
    )
    error.code = "PGRST204"
    return error


def _alias_schema_cache_error():
    error = Exception(
        "Could not find the table 'public.portfolio_analysis_id_aliases' in the schema cache"
    )
    error.code = "PGRST205"
    return error


@pytest.mark.parametrize(
    "alias_error, confirm_status, webhook_status",
    [
        (_missing_alias_table_error(), 409, 200),
        (_alias_schema_cache_error(), 503, 500),
        (_alias_column_schema_error(), 503, 500),
        (Exception("timeout"), 503, 500),
    ],
)
def test_alias_lookup_errors_on_confirm_and_webhook(
    monkeypatch, alias_error, confirm_status, webhook_status
):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        db.lookup_analysis_for_entitlement,
    )
    monkeypatch.setattr(
        db,
        "get_supabase",
        lambda: _AliasStepClient(alias_error),
    )
    paid_session = _stripe_object_session(
        id="cs_alias_lookup",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": _LEGACY_PAYMENT_ANALYSIS,
            "user_id": "test-user-123",
            "tax_year": "2025",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )

    confirm = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": paid_session.id, "analysis_id": _LEGACY_PAYMENT_ANALYSIS},
    )
    assert confirm.status_code == confirm_status
    if confirm_status == 409:
        assert "source document" in confirm.json()["detail"]

    webhook = _post_signed_webhook(
        _packet_checkout_event(
            analysis_id=_LEGACY_PAYMENT_ANALYSIS,
            tax_year="2025",
            event_id="evt_alias_lookup",
        )
    )
    assert webhook.status_code == webhook_status
    if webhook_status == 200:
        assert webhook.json()["granted"] is False


def test_webhook_retries_when_durable_packet_grant_fails(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: None)
    monkeypatch.setattr(main, "mark_packet_snapshot_paid", lambda *_args: None)
    response = _post_signed_webhook(
        _packet_checkout_event(event_type="checkout.session.async_payment_succeeded")
    )
    assert response.status_code == 500
    assert PACKET_STORE["analysis-sample-1"]["paid"] is False


def test_webhook_grants_when_history_row_disappeared_after_checkout(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: False)

    response = _post_signed_webhook(
        _packet_checkout_event(event_type="checkout.session.async_payment_succeeded")
    )

    assert response.status_code == 200
    assert response.json()["granted"] is True
    assert PACKET_STORE["analysis-sample-1"]["paid"] is True


def test_webhook_acknowledges_missing_history_when_no_snapshot_can_restore(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: False)

    response = _post_signed_webhook(_packet_checkout_event())

    assert response.status_code == 500, response.text
    assert "PACKET_GRANT_YEAR_UNKNOWN" in response.text
    assert response.json().get("granted") is not True


def test_webhook_does_not_claim_blank_guest_snapshot_by_id(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    remember_analysis("analysis-sample-1", "", SAMPLE_ANALYSIS)
    original_payload = deepcopy(PACKET_STORE["analysis-sample-1"]["payload"])
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: True)

    response = _post_signed_webhook(_packet_checkout_event())

    assert response.status_code == 500, response.text
    assert "PACKET_GRANT_YEAR_UNKNOWN" in response.text
    assert response.json().get("granted") is not True
    assert PACKET_STORE["analysis-sample-1"]["user_id"] == ""
    assert PACKET_STORE["analysis-sample-1"]["paid"] is False
    assert PACKET_STORE["analysis-sample-1"]["payload"] == original_payload


def test_grant_reads_tax_year_from_attribute_style_stripe_metadata(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: ({"result": {}}, True),
    )
    session = SimpleNamespace(
        id="cs_test_attribute_metadata",
        payment_status="paid",
        amount_total=4900,
        status="complete",
        currency="usd",
        mode="payment",
        livemode=False,
        metadata=SimpleNamespace(
            product=PACKET_METADATA_PRODUCT,
            analysis_id="analysis-sample-1",
            user_id="test-user-123",
            tax_year="2025",
        ),
    )

    granted = main._persist_packet_grant(
        session,
        "analysis-sample-1",
        "test-user-123",
    )

    assert granted is True
    assert _FAKE_PACKET_SNAPSHOTS[("test-user-123", "analysis-sample-1")]["tax_year"] == 2025


def test_grant_records_separate_entitlement_without_claiming_missing_document_is_downloadable(monkeypatch):
    _test_stripe_env(monkeypatch)
    reset_packet_store()
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", "analysis-sample-1")] = {
        "analysis_id": "analysis-sample-1",
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": None,
        "paid_at": None,
    }
    session = _stripe_object_session(
        id="cs_test_missing_packet_document",
        metadata={"tax_year": "2025"},
    )

    granted = main._persist_packet_grant(
        session,
        "analysis-sample-1",
        "test-user-123",
    )

    assert granted == main.PACKET_GRANT_MISSING_SOURCE
    assert "analysis-sample-1" not in PACKET_STORE
    saved = _FAKE_PACKET_SNAPSHOTS[("test-user-123", "analysis-sample-1")]
    assert saved["paid_at"] is None
    assert saved["packet_payload"] is None
    assert _FAKE_PACKET_ENTITLEMENTS[
        ("test-user-123", 2025, "cs_test_missing_packet_document")
    ]["analysis_id"] == "analysis-sample-1"
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )
    confirm = client.post(
        "/api/year-close-packet/confirm",
        json={
            "analysis_id": "analysis-sample-1",
            "session_id": "cs_test_missing_packet_document",
        },
    )
    assert confirm.status_code == 409
    assert confirm.json()["detail"] == main.PACKET_MISSING_SOURCE_DETAIL
    # Unpaid null payload is 403. The paid null-payload 409 stays on confirm.
    unavailable = client.get(
        "/api/year-close-packet/download",
        params={
            "analysis_id": "analysis-sample-1",
            "session_id": "cs_test_missing_packet_document",
        },
    )
    assert unavailable.status_code == 403
    assert unavailable.json()["detail"] == "Year-close packet download requires payment."
    assert not unavailable.content.startswith(b"%PDF")

    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **_kwargs: pytest.fail("a saved paid entitlement must not be charged again"),
    )
    retry_checkout = client.post(
        "/api/year-close-packet/checkout",
        json={
            "analysis_id": "analysis-sample-1",
            "analysis": {**SAMPLE_ANALYSIS, "analysis_id": "analysis-sample-1"},
        },
    )
    assert retry_checkout.status_code == 409
    assert retry_checkout.json()["detail"] == main.PACKET_MISSING_SOURCE_DETAIL


def test_checkout_rejects_deleted_paid_document_for_same_analysis_id(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-deleted-paid-document"
    remember_analysis(analysis_id, "test-user-123", SAMPLE_ANALYSIS)
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", analysis_id)] = {
        "analysis_id": analysis_id,
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": None,
        "packet_session_id": "cs_test_deleted_document",
        "paid_at": "2026-09-30T00:00:00+00:00",
    }
    _FAKE_PACKET_ENTITLEMENTS[("test-user-123", 2025, "cs_test_deleted_document")] = {
        "analysis_id": analysis_id,
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_session_id": "cs_test_deleted_document",
    }
    prior_session = _stripe_object_session(
        id="cs_test_deleted_document",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": analysis_id,
            "user_id": "test-user-123",
            "tax_year": "2025",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: prior_session,
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **_kwargs: pytest.fail("must not create another charge"),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={
            "analysis_id": analysis_id,
            "analysis": {**SAMPLE_ANALYSIS, "analysis_id": analysis_id},
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == main.PACKET_MISSING_SOURCE_DETAIL


def test_download_uses_durable_paid_snapshot_during_stripe_outage(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-durable-paid"
    payload = build_packet_payload(SAMPLE_ANALYSIS, analysis_id=analysis_id)
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", analysis_id)] = {
        "analysis_id": analysis_id,
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": payload,
        "packet_session_id": "cs_test_durable_paid",
        "paid_at": "2026-09-30T00:00:00+00:00",
    }
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(main.stripe.StripeError("offline")),
    )

    response = client.get(
        "/api/year-close-packet/download",
        params={
            "analysis_id": analysis_id,
            "session_id": "cs_test_durable_paid",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"

    stale_session = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": analysis_id, "session_id": "cs_test_stale"},
    )
    assert stale_session.status_code == 503
    assert stale_session.json()["detail"] == (
        "Could not verify the Checkout session. Please retry."
    )

    missing_session = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": analysis_id},
    )
    assert missing_session.status_code == 200
    assert missing_session.headers["content-type"] == "application/pdf"


def test_grant_refuses_snapshot_stamped_for_another_session_tax_year(monkeypatch):
    """History year 2025 disagrees with session year 2026, so the grant conflicts.

    The unpaid draft may be re-yeared by the snapshot save. History is not
    relabeled, the row is not marked paid, and the charged session is a
    conflict receipt.
    """
    _test_stripe_env(monkeypatch)
    remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", "analysis-sample-1")] = {
        "analysis_id": "analysis-sample-1",
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": PACKET_STORE["analysis-sample-1"]["payload"],
        "paid_at": None,
    }
    session = _stripe_object_session(
        id="cs_test_wrong_snapshot_year",
        metadata={"tax_year": "2026"},
    )

    granted = main._persist_packet_grant(
        session,
        "analysis-sample-1",
        "test-user-123",
    )

    row = _FAKE_PACKET_SNAPSHOTS[("test-user-123", "analysis-sample-1")]
    assert granted == main.PACKET_GRANT_YEAR_CONFLICT
    assert row["paid_at"] is None
    assert PACKET_STORE["analysis-sample-1"]["paid"] is not True
    assert PACKET_STORE["analysis-sample-1"]["payload"]["analysis_tax_year"] == 2025
    receipt = _FAKE_PACKET_ENTITLEMENTS[
        ("test-user-123", 2026, "cs_test_wrong_snapshot_year")
    ]
    assert receipt["analysis_id"] == "conflict:analysis-sample-1"


def _no_history_lookup(*_args, **_kwargs):
    return None, True


def test_first_year_grant_leaves_paid_case_variant_untouched(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    canonical = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    user = "test-user-123"
    year_x = {
        "analysis_id": canonical,
        "analysis_tax_year": 2025,
        "tax_profile": {"tax_year": 2025},
        "marker": "paid-x",
    }
    _FAKE_PACKET_SNAPSHOTS[(user, canonical)] = {
        "analysis_id": canonical,
        "user_id": user,
        "tax_year": 2025,
        "packet_payload": year_x,
        "packet_session_id": "cs_paid_x",
        "paid_at": "2026-01-01T00:00:00+00:00",
    }
    year_y = {
        "analysis_id": canonical,
        "analysis_tax_year": 2026,
        "tax_profile": {"tax_year": 2026},
        "marker": "new-y",
    }
    remember_analysis(canonical, user, year_y)

    response = _post_signed_webhook(
        _packet_checkout_event(
            analysis_id=canonical.upper(),
            tax_year=2026,
            event_id="evt_variant_y",
        )
    )

    assert response.status_code == 200, response.text
    assert response.json()["granted"] is False
    assert list(_FAKE_PACKET_SNAPSHOTS) == [(user, canonical)]
    untouched = _FAKE_PACKET_SNAPSHOTS[(user, canonical)]
    assert untouched["paid_at"] == "2026-01-01T00:00:00+00:00"
    assert untouched["packet_session_id"] == "cs_paid_x"
    assert untouched["packet_payload"] == year_x
    assert untouched["analysis_id"] == canonical
    assert untouched["tax_year"] == 2025


def test_checkout_reyears_unpaid_same_spelling_and_blocks_paid_other_year(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    aid = "analysis-sample-1"
    created = []

    def fake_create(**kwargs):
        created.append(kwargs)
        return FakeCheckoutSession(**kwargs)

    monkeypatch.setattr(main.stripe.checkout.Session, "create", fake_create)
    year_y = {
        **SAMPLE_ANALYSIS,
        "analysis_id": aid,
        "analysis_tax_year": 2026,
        "tax_profile": {"tax_year": 2026, "filing_status": "single"},
    }
    remember_analysis(aid, user, year_y)
    _FAKE_PACKET_SNAPSHOTS[(user, aid)] = {
        "analysis_id": aid,
        "user_id": user,
        "tax_year": 2025,
        "packet_payload": {"analysis_tax_year": 2025, "marker": "draft-x"},
        "packet_session_id": None,
        "paid_at": None,
    }

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": aid, "analysis": year_y},
    )

    assert response.status_code == 200, response.text
    assert created[0]["metadata"]["tax_year"] == "2026"
    row = _FAKE_PACKET_SNAPSHOTS[(user, aid)]
    assert row["analysis_id"] == aid
    assert int(row["tax_year"]) == 2026
    assert row["packet_payload"].get("marker") != "draft-x"
    assert main._packet_result_tax_year(row["packet_payload"]) == 2026

    created.clear()
    _FAKE_PACKET_SNAPSHOTS[(user, aid)] = {
        "analysis_id": aid,
        "user_id": user,
        "tax_year": 2025,
        "packet_payload": {"analysis_tax_year": 2025, "marker": "paid-x"},
        "packet_session_id": "cs_paid_x",
        "paid_at": "2026-01-01T00:00:00+00:00",
    }
    blocked = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": aid, "analysis": year_y},
    )
    assert blocked.status_code == 409, blocked.text
    assert created == []
    paid = _FAKE_PACKET_SNAPSHOTS[(user, aid)]
    assert paid["paid_at"] == "2026-01-01T00:00:00+00:00"
    assert paid["packet_session_id"] == "cs_paid_x"
    assert paid["packet_payload"]["marker"] == "paid-x"
    assert paid["tax_year"] == 2025
    assert paid["analysis_id"] == aid


def test_legacy_session_without_metadata_year_grants_history_year(monkeypatch):
    """History 2026 plus a paid 2025 row is a conflict, not a second packet."""
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    user = "test-user-123"
    canonical = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    history_payload = {
        "analysis_id": canonical,
        "tax_profile": {"tax_year": 2026, "filing_status": "single"},
        "marker": "history-2026",
    }
    remember_analysis(canonical, user, history_payload)
    paid_2025 = {
        "analysis_id": canonical,
        "user_id": user,
        "tax_year": 2025,
        "packet_payload": {"analysis_id": canonical, "marker": "paid-2025"},
        "packet_session_id": "cs_paid_2025",
        "paid_at": "2026-01-01T00:00:00+00:00",
    }
    _FAKE_PACKET_SNAPSHOTS[(user, canonical)] = dict(paid_2025)
    session = _stripe_object_session(
        id="cs_legacy_history_year",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": canonical.upper(),
            "user_id": user,
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )

    response = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": session.id, "analysis_id": canonical.upper()},
    )

    assert response.status_code == 409, response.text
    assert "PACKET_GRANT_YEAR_CONFLICT" in response.text
    assert list(_FAKE_PACKET_SNAPSHOTS) == [(user, canonical)]
    untouched = _FAKE_PACKET_SNAPSHOTS[(user, canonical)]
    assert untouched["tax_year"] == 2025
    assert untouched["paid_at"] == "2026-01-01T00:00:00+00:00"
    assert untouched["packet_session_id"] == "cs_paid_2025"
    assert untouched["packet_payload"]["marker"] == "paid-2025"

    webhook = _post_signed_webhook(
        _packet_checkout_event(
            analysis_id=canonical.upper(),
            user_id=user,
            event_id="evt_legacy_history_conflict",
        )
    )
    assert webhook.status_code == 200, webhook.text
    assert webhook.json()["granted"] is False
    assert _FAKE_PACKET_SNAPSHOTS[(user, canonical)]["tax_year"] == 2025
    assert _FAKE_PACKET_SNAPSHOTS[(user, canonical)]["packet_session_id"] == "cs_paid_2025"
    confirm_receipt = _FAKE_PACKET_ENTITLEMENTS[(user, 2026, "cs_legacy_history_year")]
    assert confirm_receipt["analysis_id"] == f"conflict:{canonical}"
    webhook_receipt = _FAKE_PACKET_ENTITLEMENTS[(user, 2026, "cs_test_paid_1")]
    assert webhook_receipt["analysis_id"] == f"conflict:{canonical}"


def test_legacy_session_without_metadata_year_grants_history_year_when_unpaid(monkeypatch):
    """An unpaid snapshot is re-yeared to the history year and granted once."""
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    canonical = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    history_payload = {
        "analysis_id": canonical,
        "analysis_tax_year": 2026,
        "tax_profile": {"tax_year": 2026, "filing_status": "single"},
        "marker": "history-2026",
    }
    remember_analysis(canonical, user, history_payload)
    _FAKE_PACKET_SNAPSHOTS[(user, canonical)] = {
        "analysis_id": canonical,
        "user_id": user,
        "tax_year": 2025,
        "packet_payload": {"analysis_id": canonical, "marker": "draft-2025"},
        "packet_session_id": None,
        "paid_at": None,
    }
    session = _stripe_object_session(
        id="cs_legacy_unpaid_reyear",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": canonical.upper(),
            "user_id": user,
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )

    response = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": session.id, "analysis_id": canonical},
    )

    assert response.status_code == 200, response.text
    assert list(_FAKE_PACKET_SNAPSHOTS) == [(user, canonical)]
    row = _FAKE_PACKET_SNAPSHOTS[(user, canonical)]
    assert row["analysis_id"] == canonical
    assert int(row["tax_year"]) == 2026
    assert row["paid_at"]
    assert row["packet_session_id"] == session.id


def test_legacy_grant_accepts_case_variant_without_restamp(monkeypatch):
    """One paid snapshot names the year. An already-paid row is not restamped."""
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    canonical = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    paid_variant = {
        "analysis_id": canonical,
        "user_id": user,
        "tax_year": 2024,
        "packet_payload": {"analysis_id": canonical, "marker": "other-year"},
        "packet_session_id": "cs_other_year",
        "paid_at": "2026-01-01T00:00:00+00:00",
    }
    _FAKE_PACKET_SNAPSHOTS[(user, canonical)] = dict(paid_variant)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", _no_history_lookup)
    session = _stripe_object_session(
        id="cs_legacy_other_year_variant",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": canonical.upper(),
            "user_id": user,
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )

    response = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": session.id, "analysis_id": canonical.upper()},
    )

    assert response.status_code == 200, response.text
    assert "PACKET_GRANT_YEAR_UNKNOWN" not in response.text
    assert list(_FAKE_PACKET_SNAPSHOTS) == [(user, canonical)]
    row = _FAKE_PACKET_SNAPSHOTS[(user, canonical)]
    assert row["packet_session_id"] == "cs_other_year"
    assert row["tax_year"] == 2024
    assert row["paid_at"] == "2026-01-01T00:00:00+00:00"


def test_legacy_grant_ambiguous_year_logs_and_confirm_409(monkeypatch, caplog):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    user = "test-user-123"
    analysis_id = "analysis-legacy-ambiguous"
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", _no_history_lookup)
    session = _stripe_object_session(
        id="cs_legacy_ambiguous",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": analysis_id,
            "user_id": user,
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )

    with caplog.at_level(logging.ERROR, logger="main"):
        webhook = _post_signed_webhook(
            _packet_checkout_event(
                analysis_id=analysis_id,
                user_id=user,
                event_id="evt_year_unknown",
            )
        )
    assert webhook.status_code == 500, webhook.text
    assert "PACKET_GRANT_YEAR_UNKNOWN" in webhook.text
    assert webhook.json().get("granted") is not True
    unknown_logs = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR and "PACKET_GRANT_YEAR_UNKNOWN" in record.message
    ]
    assert unknown_logs
    assert "cs_test_paid_1" in unknown_logs[0].message
    assert user in unknown_logs[0].message
    assert analysis_id in unknown_logs[0].message

    confirm = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": session.id, "analysis_id": analysis_id},
    )
    assert confirm.status_code == 409, confirm.text
    assert "PACKET_GRANT_YEAR_UNKNOWN" in confirm.json()["detail"]

    canonical = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    for year, session_id in ((2024, "cs_2024"), (2025, "cs_2025")):
        _FAKE_PACKET_ENTITLEMENTS[(user, year, session_id)] = {
            "analysis_id": canonical,
            "user_id": user,
            "tax_year": year,
            "packet_session_id": session_id,
        }
    several = _stripe_object_session(
        id="cs_legacy_several_rows",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": canonical.upper(),
            "user_id": user,
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: several,
    )
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="main"):
        several_hook = _post_signed_webhook(
            _packet_checkout_event(
                analysis_id=canonical.upper(),
                user_id=user,
                event_id="evt_year_unknown_several",
            )
        )
    assert several_hook.status_code == 500, several_hook.text
    assert "PACKET_GRANT_YEAR_UNKNOWN" in several_hook.text
    assert "PACKET_GRANT_YEAR_UNKNOWN" in caplog.text
    assert _FAKE_PACKET_SNAPSHOTS == {}
    assert _FAKE_PACKET_ENTITLEMENTS[(user, 2024, "cs_2024")]["packet_session_id"] == "cs_2024"
    assert _FAKE_PACKET_ENTITLEMENTS[(user, 2025, "cs_2025")]["analysis_id"] == canonical
    several_confirm = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": several.id, "analysis_id": canonical},
    )
    assert several_confirm.status_code == 409, several_confirm.text
    assert "PACKET_GRANT_YEAR_UNKNOWN" in several_confirm.json()["detail"]


def test_legacy_grant_year_resolution_outage_is_503(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    analysis_id = "analysis-legacy-outage"
    session = _stripe_object_session(
        id="cs_legacy_outage",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": analysis_id,
            "user_id": user,
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: (None, False),
    )

    history_outage = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": session.id, "analysis_id": analysis_id},
    )
    assert history_outage.status_code == 503, history_outage.text

    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", _no_history_lookup)
    monkeypatch.setattr(
        main,
        "lookup_packet_entitlements_for_analysis",
        lambda *_args, **_kwargs: ([], False),
    )

    def list_must_not_run(*_args, **_kwargs):
        raise AssertionError("snapshot list ran after an entitlement outage")

    monkeypatch.setattr(main, "list_packet_snapshots_for_identity", list_must_not_run)
    entitlement_outage = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": session.id, "analysis_id": analysis_id},
    )
    assert entitlement_outage.status_code == 503, entitlement_outage.text


def test_paid_year_conflict_confirm_409_and_webhook_logs(monkeypatch, caplog):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    user = "test-user-123"
    aid = "analysis-sample-1"
    payload = {"analysis_tax_year": 2025, "marker": "paid-x"}
    _FAKE_PACKET_SNAPSHOTS[(user, aid)] = {
        "analysis_id": aid,
        "user_id": user,
        "tax_year": 2025,
        "packet_payload": payload,
        "packet_session_id": "cs_paid_x",
        "paid_at": "2026-01-01T00:00:00+00:00",
    }
    remember_analysis(aid, user, {**SAMPLE_ANALYSIS, "analysis_id": aid})
    session = _stripe_object_session(
        id="cs_year_conflict",
        metadata={"tax_year": "2026"},
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )

    confirm = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": "cs_year_conflict", "analysis_id": aid},
    )
    assert confirm.status_code == 409, confirm.text
    assert "PACKET_GRANT_YEAR_CONFLICT" in confirm.text
    unchanged = _FAKE_PACKET_SNAPSHOTS[(user, aid)]
    assert unchanged["paid_at"] == "2026-01-01T00:00:00+00:00"
    assert unchanged["packet_session_id"] == "cs_paid_x"
    assert unchanged["packet_payload"] == payload
    assert unchanged["tax_year"] == 2025

    with caplog.at_level(logging.ERROR, logger="main"):
        webhook = _post_signed_webhook(
            _packet_checkout_event(tax_year=2026, event_id="evt_year_conflict")
        )
    assert webhook.status_code == 200, webhook.text
    assert webhook.json() == {"received": True, "granted": False}
    assert "PACKET_GRANT_YEAR_CONFLICT" in caplog.text
    assert "2025" in caplog.text
    assert "2026" in caplog.text
    assert "cs_test_paid_1" in caplog.text
    assert user in caplog.text
    assert aid in caplog.text
    receipt = _FAKE_PACKET_ENTITLEMENTS[(user, 2026, "cs_test_paid_1")]
    assert receipt["analysis_id"] == "conflict:analysis-sample-1"
    assert receipt["tax_year"] == 2026
    assert receipt["packet_session_id"] == "cs_test_paid_1"
    assert _FAKE_PACKET_SNAPSHOTS[(user, aid)]["tax_year"] == 2025
    assert _FAKE_PACKET_SNAPSHOTS[(user, aid)]["packet_session_id"] == "cs_paid_x"
    assert main.lookup_packet_entitlement_for_tax_year(user, 2026) == (None, True)


def test_checkout_unknown_year_uses_one_snapshot_or_409(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    aid = "analysis-no-caller-year"
    created = []

    def fake_create(**kwargs):
        created.append(kwargs)
        return FakeCheckoutSession(**kwargs)

    monkeypatch.setattr(main.stripe.checkout.Session, "create", fake_create)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", _no_history_lookup)
    payload = {"analysis_id": aid, "analysis_tax_year": 2024, "summary": {}}
    _FAKE_PACKET_SNAPSHOTS[(user, aid)] = {
        "analysis_id": aid,
        "user_id": user,
        "tax_year": 2024,
        "packet_payload": payload,
        "paid_at": None,
        "expires_at": "2099-01-01T00:00:00+00:00",
    }

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": aid, "analysis": {"analysis_id": aid, "summary": {}}},
    )
    assert response.status_code == 200, response.text
    assert created[0]["metadata"]["tax_year"] == "2024"
    assert "expired" not in response.text.lower()

    created.clear()
    _FAKE_PACKET_SNAPSHOTS.clear()
    expired = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": aid, "analysis": {"analysis_id": aid, "summary": {}}},
    )
    assert expired.status_code == 409, expired.text
    assert expired.json()["detail"] == main.PACKET_SNAPSHOT_EXPIRED_DETAIL
    assert created == []

    canonical = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    for year, session_id in ((2024, "cs_2024"), (2025, "cs_2025")):
        _FAKE_PACKET_ENTITLEMENTS[(user, year, session_id)] = {
            "analysis_id": canonical,
            "user_id": user,
            "tax_year": year,
            "packet_session_id": session_id,
        }
    ambiguous = client.post(
        "/api/year-close-packet/checkout",
        json={
            "analysis_id": canonical,
            "analysis": {"analysis_id": canonical, "summary": {}},
        },
    )
    assert ambiguous.status_code == 409, ambiguous.text
    assert ambiguous.json()["detail"] == main.PACKET_YEAR_AMBIGUOUS_DETAIL
    assert created == []


def test_download_stripe_miss_picks_named_year_or_single_paid_row(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    monkeypatch.setattr(
        main,
        "render_packet_pdf",
        lambda payload: f"YEAR:{payload.get('analysis_tax_year')}".encode(),
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(main.stripe.StripeError("offline")),
    )

    aid = "analysis-history-year"
    other = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    remember_analysis(
        aid,
        user,
        {"analysis_id": aid, "analysis_tax_year": 2026, "tax_profile": {"tax_year": 2026}},
    )
    _FAKE_PACKET_SNAPSHOTS[(user, aid)] = {
        "analysis_id": aid,
        "user_id": user,
        "tax_year": 2026,
        "packet_payload": {"analysis_tax_year": 2026, "marker": "from-history"},
        "packet_session_id": "cs_hist",
        "paid_at": "2026-02-01T00:00:00+00:00",
    }
    _FAKE_PACKET_SNAPSHOTS[(user, other)] = {
        "analysis_id": other,
        "user_id": user,
        "tax_year": 2025,
        "packet_payload": {"analysis_tax_year": 2025, "marker": "other"},
        "packet_session_id": "cs_other",
        "paid_at": "2026-01-01T00:00:00+00:00",
    }
    history_pdf = client.get("/api/year-close-packet/download", params={"analysis_id": aid})
    assert history_pdf.status_code == 200, history_pdf.text
    assert history_pdf.content == b"YEAR:2026"

    ent_id = "analysis-entitlement-year"
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", _no_history_lookup)
    _FAKE_PACKET_ENTITLEMENTS[(user, 2024, "cs_ent_year")] = {
        "analysis_id": ent_id,
        "user_id": user,
        "tax_year": 2024,
        "packet_session_id": "cs_ent_year",
    }
    _FAKE_PACKET_SNAPSHOTS[(user, ent_id)] = {
        "analysis_id": ent_id,
        "user_id": user,
        "tax_year": 2024,
        "packet_payload": {"analysis_tax_year": 2024, "marker": "from-entitlement"},
        "packet_session_id": "cs_ent_year",
        "paid_at": "2026-03-01T00:00:00+00:00",
    }
    entitlement_pdf = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": ent_id},
    )
    assert entitlement_pdf.status_code == 200, entitlement_pdf.text
    assert entitlement_pdf.content == b"YEAR:2024"

    single_id = "analysis-single-paid"
    _FAKE_PACKET_ENTITLEMENTS.clear()
    _FAKE_PACKET_SNAPSHOTS.clear()
    _FAKE_PACKET_SNAPSHOTS[(user, single_id)] = {
        "analysis_id": single_id,
        "user_id": user,
        "tax_year": 2023,
        "packet_payload": {"analysis_tax_year": 2023, "marker": "only-paid"},
        "packet_session_id": "cs_only",
        "paid_at": "2026-04-01T00:00:00+00:00",
    }
    single_pdf = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": single_id, "session_id": "cs_only"},
    )
    assert single_pdf.status_code == 200, single_pdf.text
    assert single_pdf.content == b"YEAR:2023"

    no_session_pdf = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": single_id},
    )
    assert no_session_pdf.status_code == 200, no_session_pdf.text
    assert no_session_pdf.content == b"YEAR:2023"

    mismatched = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": single_id, "session_id": "cs_other_session"},
    )
    assert mismatched.status_code == 503, mismatched.text
    assert mismatched.json()["detail"] == (
        "Could not verify the Checkout session. Please retry."
    )


def test_download_snapshot_lookup_outage_is_503(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", _no_history_lookup)
    monkeypatch.setattr(
        main,
        "lookup_packet_entitlements_for_analysis",
        lambda *_args, **_kwargs: ([], False),
    )

    def list_must_not_run(*_args, **_kwargs):
        raise AssertionError("snapshot list ran after an entitlement outage")

    monkeypatch.setattr(main, "list_packet_snapshots_for_identity", list_must_not_run)
    # Identity must pass before year resolution. Entitlement outage is then 503
    # and must not fall through to the snapshot list.
    paid_session = _stripe_object_session(
        id="cs_entitlement_outage",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-entitlement-outage",
            "user_id": "test-user-123",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )
    entitlement_outage = client.get(
        "/api/year-close-packet/download",
        params={
            "analysis_id": "analysis-entitlement-outage",
            "session_id": paid_session.id,
        },
    )
    assert entitlement_outage.status_code == 503
    assert not entitlement_outage.content.startswith(b"%PDF")

    monkeypatch.setattr(
        main,
        "lookup_packet_entitlements_for_analysis",
        lambda *_args, **_kwargs: ([], True),
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(main.stripe.StripeError("offline")),
    )
    monkeypatch.setattr(
        main,
        "list_packet_snapshots_for_identity",
        lambda *_args, **_kwargs: ([], False),
    )
    list_outage = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": "analysis-list-outage", "session_id": "cs_list_outage"},
    )
    assert list_outage.status_code == 503
    assert not list_outage.content.startswith(b"%PDF")


def _forbid_download_writes(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("download must not grant")

    monkeypatch.setattr(main, "_grant_packet_from_session", _boom)
    monkeypatch.setattr(main, "save_packet_snapshot", _boom)
    monkeypatch.setattr(main, "mark_packet_snapshot_paid", _boom)


def _use_ilike_snapshot_client(monkeypatch, rows):
    snapshot_client = _YearScopedSnapshotClient(rows)
    monkeypatch.setattr(db, "get_supabase", lambda: snapshot_client)
    monkeypatch.setattr(main, "get_packet_snapshot", db.get_packet_snapshot)
    return snapshot_client


def _wire_real_snapshot_client(monkeypatch, rows):
    """Point packet snapshot reads and writes at the real db helpers."""
    snapshot_client = _YearScopedSnapshotClient(rows)
    monkeypatch.setattr(db, "get_supabase", lambda: snapshot_client)
    monkeypatch.setattr(main, "get_packet_snapshot", db.get_packet_snapshot)
    monkeypatch.setattr(main, "save_packet_snapshot", db.save_packet_snapshot)
    monkeypatch.setattr(main, "mark_packet_snapshot_paid", db.mark_packet_snapshot_paid)
    monkeypatch.setattr(
        main,
        "list_packet_snapshots_for_identity",
        db.list_packet_snapshots_for_identity,
    )
    return snapshot_client


_PAID_OTHER_YEAR_DETAIL = (
    "This analysis already has a paid packet for another tax year. "
    "Start a new analysis for the year you want to buy."
)


def _paid_2025_session_fixture(monkeypatch):
    _test_stripe_env(monkeypatch)
    aid = "analysis-2025-paid"
    user = "test-user-123"
    paid_source = {
        **SAMPLE_ANALYSIS,
        "analysis_id": aid,
        "wash_sale_flags": [{
            **SAMPLE_ANALYSIS["wash_sale_flags"][0],
            "symbol": "PAID2025",
        }],
    }
    paid_payload = build_packet_payload(paid_source, analysis_id=aid)
    snapshot_client = _use_ilike_snapshot_client(
        monkeypatch,
        [{
            "analysis_id": aid,
            "user_id": user,
            "tax_year": 2025,
            "packet_payload": paid_payload,
            "packet_session_id": "cs_paid_2025",
            "paid_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
        }],
    )
    year_2026 = {
        **SAMPLE_ANALYSIS,
        "analysis_id": aid,
        "tax_profile": {"tax_year": 2026, "filing_status": "single"},
        "tax_lots": [{"symbol": "UNPAID2026", "quantity": 1}],
        "wash_sale_flags": [{
            **SAMPLE_ANALYSIS["wash_sale_flags"][0],
            "symbol": "UNPAID2026",
        }],
    }
    mark_paid(aid, "cs_paid_2025", user_id=user)
    remember_analysis(aid, user, year_2026)
    paid_session = _stripe_object_session(
        id="cs_paid_2025",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": aid,
            "user_id": user,
            "tax_year": "2025",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )
    _forbid_download_writes(monkeypatch)
    return aid, snapshot_client, year_2026


def test_download_get_uses_session_year_when_history_year_differs(monkeypatch):
    """GET with the paid 2025 session serves that PDF, not the 2026 re-run."""
    aid, snapshot_client, _year_2026 = _paid_2025_session_fixture(monkeypatch)
    original = deepcopy(snapshot_client.rows[0])

    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": aid, "session_id": "cs_paid_2025"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    text = _pdf_text(response.content)
    assert "PAID2025" in text
    assert b"UNPAID2026" not in response.content
    assert snapshot_client.rows[0] == original


def test_download_post_403_when_body_year_disagrees_with_session_year(monkeypatch):
    """A 2026 body cannot override a 2025 Checkout session."""
    aid, snapshot_client, year_2026 = _paid_2025_session_fixture(monkeypatch)
    original = deepcopy(snapshot_client.rows[0])

    response = client.post(
        "/api/year-close-packet/download",
        json={
            "analysis_id": aid,
            "session_id": "cs_paid_2025",
            "analysis": year_2026,
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Year-close packet download requires payment."
    assert not response.content.startswith(b"%PDF")
    assert b"UNPAID2026" not in response.content
    assert b"PAID2025" not in response.content
    assert snapshot_client.rows[0] == original


def test_download_rejects_second_spelling_post_with_other_sessions(monkeypatch):
    """POST B's id and 2026 profile with A's 2025 session is not B's PDF."""
    _test_stripe_env(monkeypatch)
    stored = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    other = stored.lower()
    user = "test-user-123"
    paid_payload = {
        "analysis_id": stored,
        "marker": "paid-A-2025",
        "tax_profile": {"tax_year": 2025, "filing_status": "single"},
    }
    snapshot_client = _use_ilike_snapshot_client(
        monkeypatch,
        [{
            "analysis_id": other,
            "user_id": user,
            "tax_year": 2025,
            "packet_payload": {**paid_payload, "analysis_id": other},
            "packet_session_id": "cs_A",
            "paid_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
        }],
    )
    paid_session = _stripe_object_session(
        id="cs_A",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": stored,
            "user_id": user,
            "tax_year": "2025",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )
    _forbid_download_writes(monkeypatch)
    original = deepcopy(snapshot_client.rows[0])

    response = client.post(
        "/api/year-close-packet/download",
        json={
            "analysis_id": other,
            "session_id": "cs_A",
            "analysis": {
                "analysis_id": other,
                "tax_profile": {"tax_year": 2026, "filing_status": "single"},
                "tax_lots": [{"symbol": "B-UNPAID-2026", "quantity": 1}],
            },
        },
    )

    assert response.status_code == 403, response.text
    assert not response.content.startswith(b"%PDF")
    assert b"B-UNPAID-2026" not in response.content
    assert b"paid-A-2025" not in response.content
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0] == original
    assert snapshot_client.rows[0]["analysis_id"] == other


def test_download_rejects_unpaid_snapshot_despite_memory_paid_flag(monkeypatch):
    """An unpaid year-scoped row plus a sticky paid flag is 403 and does not grant."""
    _test_stripe_env(monkeypatch)
    aid = "analysis-unpaid-2025"
    user = "test-user-123"
    unpaid_payload = {
        "analysis_id": aid,
        "marker": "UNPAID-LOTS",
        "tax_profile": {"tax_year": 2025, "filing_status": "single"},
    }
    snapshot_client = _use_ilike_snapshot_client(
        monkeypatch,
        [{
            "analysis_id": aid,
            "user_id": user,
            "tax_year": 2025,
            "packet_payload": unpaid_payload,
            "packet_session_id": None,
            "paid_at": None,
            "expires_at": "2099-01-01T00:00:00+00:00",
        }],
    )
    analysis = {
        **SAMPLE_ANALYSIS,
        "analysis_id": aid,
        "tax_profile": {"tax_year": 2025, "filing_status": "single"},
    }
    remember_analysis(aid, user, analysis)
    mark_paid(aid, "cs_unpaid_year", user_id=user)
    assert PACKET_STORE[aid]["paid"] is True
    paid_session = _stripe_object_session(
        id="cs_unpaid_year",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": aid,
            "user_id": user,
            "tax_year": "2025",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )
    _forbid_download_writes(monkeypatch)
    original = deepcopy(snapshot_client.rows[0])

    response = client.post(
        "/api/year-close-packet/download",
        json={
            "analysis_id": aid,
            "session_id": "cs_unpaid_year",
            "analysis": analysis,
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Year-close packet download requires payment."
    assert not response.content.startswith(b"%PDF")
    assert b"UNPAID-LOTS" not in response.content
    assert snapshot_client.rows[0] == original
    assert _FAKE_PACKET_ENTITLEMENTS == {}
    assert PACKET_STORE[aid]["paid"] is True


def test_save_packet_snapshot_rejects_paid_other_spelling_other_year(monkeypatch):
    """Pay 2025 first. A later 2026 save or checkout cannot open a second row."""
    upper = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    lower = upper.lower()
    user = "test-user-123"
    snapshot_client = _YearScopedSnapshotClient([])

    saved = db.save_packet_snapshot(
        upper,
        user,
        2025,
        {"marker": "draft-A"},
        client=snapshot_client,
    )
    assert saved["analysis_id"] == lower
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0]["analysis_id"] == lower
    assert snapshot_client.rows[0]["tax_year"] == 2025
    assert snapshot_client.rows[0]["paid_at"] is None

    assert db.mark_packet_snapshot_paid(
        upper, user, 2025, "cs_paid_A", client=snapshot_client
    ) is True
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0]["analysis_id"] == lower
    assert snapshot_client.rows[0]["tax_year"] == 2025
    assert snapshot_client.rows[0]["paid_at"]
    assert snapshot_client.rows[0]["packet_session_id"] == "cs_paid_A"
    assert snapshot_client.rows[0]["packet_payload"] == {"marker": "draft-A"}

    with pytest.raises(db.PacketSnapshotYearConflict) as raised:
        db.save_packet_snapshot(
            lower,
            user,
            2026,
            {"marker": "new-B"},
            client=snapshot_client,
        )
    assert raised.value.stored_tax_year == 2025
    assert raised.value.requested_tax_year == 2026
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0]["tax_year"] == 2025
    assert snapshot_client.rows[0]["paid_at"]
    assert snapshot_client.rows[0]["packet_payload"] == {"marker": "draft-A"}

    _test_stripe_env(monkeypatch)
    http_client = _wire_real_snapshot_client(monkeypatch, [])
    db.save_packet_snapshot(
        upper, user, 2025, {"marker": "draft-A"}, client=http_client
    )
    assert db.mark_packet_snapshot_paid(
        upper, user, 2025, "cs_paid_A", client=http_client
    ) is True
    year_2026 = {
        **SAMPLE_ANALYSIS,
        "analysis_id": lower,
        "analysis_tax_year": 2026,
        "tax_profile": {"tax_year": 2026, "filing_status": "single"},
    }
    remember_analysis(lower, user, year_2026)
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs) or FakeCheckoutSession(**kwargs),
    )
    blocked = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": upper, "analysis": year_2026},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"] == _PAID_OTHER_YEAR_DETAIL
    assert created == []
    assert len(http_client.rows) == 1
    assert http_client.rows[0]["analysis_id"] == lower
    assert http_client.rows[0]["tax_year"] == 2025
    assert http_client.rows[0]["paid_at"]
    assert http_client.rows[0]["packet_session_id"] == "cs_paid_A"


def test_unpaid_upper_reyear_then_pay_leaves_one_paid_year(monkeypatch):
    """Jordan's order: unpaid upper 2025, save lower 2026, pay 2025, block 2026.

    The unpaid re-year runs before payment, so the safety property is one
    primary-key row and at most one paid year, not a frozen 2025 draft.
    """
    _test_stripe_env(monkeypatch)
    upper = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    lower = upper.lower()
    user = "test-user-123"
    snapshot_client = _wire_real_snapshot_client(monkeypatch, [])
    year_2025 = {
        **SAMPLE_ANALYSIS,
        "analysis_id": lower,
        "analysis_tax_year": 2025,
        "tax_profile": {"tax_year": 2025, "filing_status": "single"},
    }
    remember_analysis(lower, user, year_2025)

    first = db.save_packet_snapshot(
        upper, user, 2025, {"marker": "draft-A"}, client=snapshot_client
    )
    assert first["analysis_id"] == lower
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0]["tax_year"] == 2025
    assert snapshot_client.rows[0]["paid_at"] is None

    second = db.save_packet_snapshot(
        lower, user, 2026, {"marker": "draft-B"}, client=snapshot_client
    )
    assert second["analysis_id"] == lower
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0]["tax_year"] == 2026
    assert snapshot_client.rows[0]["paid_at"] is None

    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs) or FakeCheckoutSession(**kwargs),
    )
    pay = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": upper, "analysis": year_2025},
    )
    assert pay.status_code == 200, pay.text
    assert len(created) == 1
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0]["tax_year"] == 2025
    assert db.mark_packet_snapshot_paid(
        upper, user, 2025, "cs_paid_A", client=snapshot_client
    ) is True
    assert snapshot_client.rows[0]["paid_at"]
    assert snapshot_client.rows[0]["tax_year"] == 2025

    with pytest.raises(db.PacketSnapshotYearConflict):
        db.save_packet_snapshot(
            lower, user, 2026, {"marker": "new-B"}, client=snapshot_client
        )
    year_2026 = {
        **year_2025,
        "analysis_tax_year": 2026,
        "tax_profile": {"tax_year": 2026, "filing_status": "single"},
    }
    blocked = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": lower, "analysis": year_2026},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"] == _PAID_OTHER_YEAR_DETAIL
    assert len(created) == 1
    paid_years = {
        int(row["tax_year"]) for row in snapshot_client.rows if row.get("paid_at")
    }
    assert len(snapshot_client.rows) == 1
    assert len(paid_years) == 1
    assert snapshot_client.rows[0]["analysis_id"] == lower


def test_download_get_serves_unmigrated_upper_paid_row(monkeypatch):
    """New code reads a legacy UPPER paid row when the canonical eq misses."""
    _test_stripe_env(monkeypatch)
    upper = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    lower = upper.lower()
    user = "test-user-123"
    payload = build_packet_payload(
        {**SAMPLE_ANALYSIS, "analysis_id": lower},
        analysis_id=lower,
    )
    snapshot_client = _wire_real_snapshot_client(
        monkeypatch,
        [{
            "analysis_id": upper,
            "user_id": user,
            "tax_year": 2025,
            "packet_payload": payload,
            "packet_session_id": "cs_upper_paid",
            "paid_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
        }],
    )
    session = _stripe_object_session(
        id="cs_upper_paid",
        metadata={
            "analysis_id": upper,
            "user_id": user,
            "tax_year": "2025",
        },
    )
    calls = {"n": 0}

    def retrieve(*_args, **_kwargs):
        calls["n"] += 1
        return session

    monkeypatch.setattr(main.stripe.checkout.Session, "retrieve", retrieve)
    _forbid_download_writes(monkeypatch)

    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": lower, "session_id": "cs_upper_paid"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert calls["n"] == 1
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0]["analysis_id"] == upper


def test_checkout_other_year_conflicts_with_unmigrated_upper_paid_row(monkeypatch):
    """Do not insert a lowercase row beside a legacy UPPER paid year."""
    _test_stripe_env(monkeypatch)
    upper = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    lower = upper.lower()
    user = "test-user-123"
    snapshot_client = _wire_real_snapshot_client(
        monkeypatch,
        [{
            "analysis_id": upper,
            "user_id": user,
            "tax_year": 2025,
            "packet_payload": {"marker": "paid-A", "analysis_id": upper},
            "packet_session_id": "cs_upper_paid",
            "paid_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
        }],
    )
    year_2026 = {
        **SAMPLE_ANALYSIS,
        "analysis_id": lower,
        "analysis_tax_year": 2026,
        "tax_profile": {"tax_year": 2026, "filing_status": "single"},
    }
    remember_analysis(lower, user, year_2026)
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs) or FakeCheckoutSession(**kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": upper, "analysis": year_2026},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == _PAID_OTHER_YEAR_DETAIL
    assert created == []
    assert len(snapshot_client.rows) == 1
    assert snapshot_client.rows[0]["analysis_id"] == upper
    assert snapshot_client.rows[0]["tax_year"] == 2025
    assert snapshot_client.rows[0]["paid_at"]


def test_download_serves_pdf_when_stripe_outage_has_matching_paid_session(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-durable-proof"
    user = "test-user-123"
    payload = build_packet_payload(SAMPLE_ANALYSIS, analysis_id=analysis_id)
    _FAKE_PACKET_SNAPSHOTS[(user, analysis_id)] = {
        "analysis_id": analysis_id,
        "user_id": user,
        "tax_year": 2025,
        "packet_payload": payload,
        "packet_session_id": "cs_durable_match",
        "paid_at": "2026-09-30T00:00:00+00:00",
    }
    calls = {"n": 0}

    def retrieve(*_args, **_kwargs):
        calls["n"] += 1
        raise main.stripe.StripeError("offline")

    monkeypatch.setattr(main.stripe.checkout.Session, "retrieve", retrieve)
    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": analysis_id, "session_id": "cs_durable_match"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert calls["n"] == 1


def test_download_stripe_outage_without_matching_paid_session_is_503(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-no-durable-proof"
    user = "test-user-123"
    payload = build_packet_payload(SAMPLE_ANALYSIS, analysis_id=analysis_id)
    _FAKE_PACKET_SNAPSHOTS[(user, analysis_id)] = {
        "analysis_id": analysis_id,
        "user_id": user,
        "tax_year": 2025,
        "packet_payload": payload,
        "packet_session_id": "cs_real_paid",
        "paid_at": "2026-09-30T00:00:00+00:00",
    }
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(main.stripe.StripeError("offline")),
    )
    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": analysis_id, "session_id": "cs_not_this_buyer"},
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == (
        "Could not verify the Checkout session. Please retry."
    )
    assert not response.content.startswith(b"%PDF")


def test_checkout_updates_existing_history_tax_year(monkeypatch):
    canonical = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    user = "test-user-123"
    record = {
        "id": "history-row",
        "user_id": user,
        "result": {
            "analysis_id": canonical,
            "analysis_tax_year": 2025,
            "tax_profile": {"tax_year": 2025, "filing_status": "single"},
        },
    }
    patches = []

    def lookup(_analysis_id, _user_id):
        return record, True

    def patch(_analysis_id, _user_id, body):
        patches.append(dict(body))
        result = record["result"]
        result.update(body)
        return True

    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lookup)
    monkeypatch.setattr(main, "patch_analysis_result", patch)
    monkeypatch.setattr(main, "canonical_history_embedded_on_other_row", lambda *_args: False)

    with pytest.raises(db.PacketSnapshotYearConflict):
        main._ensure_packet_history_row(canonical, user, 2026)
    assert patches == []
    assert record["id"] == "history-row"
    assert record["result"]["analysis_tax_year"] == 2025
    assert record["result"]["tax_profile"]["tax_year"] == 2025
    assert record["result"]["tax_profile"]["filing_status"] == "single"

    assert main._ensure_packet_history_row(canonical, user, 2025) is True
    assert patches == []

    record["result"].pop("analysis_tax_year")
    record["result"]["tax_profile"].pop("tax_year")
    assert main._ensure_packet_history_row(canonical, user, 2026) is True
    assert patches == [{
        "analysis_tax_year": 2026,
        "tax_profile": {"tax_year": 2026, "filing_status": "single"},
    }]
    assert record["id"] == "history-row"

    record["result"]["analysis_id"] = canonical.upper()
    record["result"]["analysis_tax_year"] = 2026
    record["result"]["tax_profile"] = {"tax_year": 2026, "filing_status": "single"}
    patches.clear()
    assert main._ensure_packet_history_row(canonical, user, 2026) is True
    assert patches == [{"analysis_id": canonical}]
    assert record["result"]["tax_profile"]["filing_status"] == "single"

    record["result"]["analysis_id"] = canonical.upper()
    record["result"]["analysis_tax_year"] = 2025
    patches.clear()
    with pytest.raises(db.PacketSnapshotYearConflict):
        main._ensure_packet_history_row(canonical, user, 2026)
    assert patches == []
    assert record["result"]["analysis_id"] == canonical.upper()


def test_download_retrieves_checkout_session_once(monkeypatch):
    _test_stripe_env(monkeypatch)
    aid = "analysis-retrieve-once"
    user = "test-user-123"
    payload = build_packet_payload(
        {**SAMPLE_ANALYSIS, "analysis_id": aid},
        analysis_id=aid,
    )
    _FAKE_PACKET_SNAPSHOTS[(user, aid)] = {
        "analysis_id": aid,
        "user_id": user,
        "tax_year": 2025,
        "packet_payload": payload,
        "packet_session_id": "cs_once",
        "paid_at": "2026-01-01T00:00:00+00:00",
    }
    calls = {"n": 0}
    session = _stripe_object_session(
        id="cs_once",
        metadata={
            "analysis_id": aid,
            "user_id": user,
            "tax_year": "2025",
        },
    )

    def retrieve(*_args, **_kwargs):
        calls["n"] += 1
        return session

    monkeypatch.setattr(main.stripe.checkout.Session, "retrieve", retrieve)
    got = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": aid, "session_id": "cs_once"},
    )
    assert got.status_code == 200, got.text
    assert calls["n"] == 1

    calls["n"] = 0
    posted = client.post(
        "/api/year-close-packet/download",
        json={
            "analysis_id": aid,
            "session_id": "cs_once",
            "analysis": {**SAMPLE_ANALYSIS, "analysis_id": aid},
        },
    )
    assert posted.status_code == 200, posted.text
    assert calls["n"] == 1

    calls["n"] = 0
    bare = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": aid},
    )
    assert bare.status_code == 200, bare.text
    assert calls["n"] == 0


def test_legacy_webhook_uses_single_snapshot_year(monkeypatch, caplog):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    user = "test-user-123"
    canonical = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", _no_history_lookup)
    stored = canonical.upper()
    snapshot_client = _wire_real_snapshot_client(
        monkeypatch,
        [{
            "analysis_id": stored,
            "user_id": user,
            "tax_year": 2024,
            "packet_payload": {"analysis_id": stored, "marker": "snap-2024"},
            "packet_session_id": None,
            "paid_at": None,
            "expires_at": "2099-01-01T00:00:00+00:00",
        }],
    )
    with caplog.at_level(logging.ERROR, logger="main"):
        webhook = _post_signed_webhook(
            _packet_checkout_event(
                analysis_id=canonical.upper(),
                user_id=user,
                event_id="evt_single_snapshot_year",
            )
        )
    assert webhook.status_code == 200, webhook.text
    assert webhook.json()["granted"] is True
    assert "PACKET_GRANT_YEAR_UNKNOWN" not in caplog.text
    assert len(snapshot_client.rows) == 1
    row = snapshot_client.rows[0]
    assert row["analysis_id"] == stored
    assert int(row["tax_year"]) == 2024
    assert row["paid_at"]
    assert row["packet_session_id"] == "cs_test_paid_1"
    entitlement = _FAKE_PACKET_ENTITLEMENTS[(user, 2024, "cs_test_paid_1")]
    assert entitlement["analysis_id"] == canonical


def test_legacy_webhook_unknown_year_is_retryable(monkeypatch, caplog):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    user = "test-user-123"
    analysis_id = "analysis-year-unknown"
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", _no_history_lookup)
    session = _stripe_object_session(
        id="cs_year_unknown_retry",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": analysis_id,
            "user_id": user,
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )
    with caplog.at_level(logging.ERROR, logger="main"):
        webhook = _post_signed_webhook(
            _packet_checkout_event(
                analysis_id=analysis_id,
                user_id=user,
                event_id="evt_year_unknown_retry",
            )
        )
    assert webhook.status_code == 500, webhook.text
    assert webhook.json().get("granted") is not True
    unknown_logs = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR and "PACKET_GRANT_YEAR_UNKNOWN" in record.message
    ]
    assert len(unknown_logs) == 1
    assert "cs_test_paid_1" in unknown_logs[0].message
    assert user in unknown_logs[0].message
    assert analysis_id in unknown_logs[0].message
    assert _FAKE_PACKET_ENTITLEMENTS == {}
    confirm = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": session.id, "analysis_id": analysis_id},
    )
    assert confirm.status_code == 409, confirm.text
    assert "PACKET_GRANT_YEAR_UNKNOWN" in confirm.json()["detail"]


def test_paid_grant_survives_history_miss_from_owned_private_snapshot(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: False)

    granted = main._persist_packet_grant(
        _packet_checkout_event()["data"]["object"],
        "analysis-sample-1",
        "test-user-123",
    )

    assert granted is True
    assert PACKET_STORE["analysis-sample-1"]["paid"] is True


def test_webhook_grants_live_mode_event_in_production(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    monkeypatch.setenv("FRONTEND_URL", "https://www.optionstaxhub.com")
    monkeypatch.setattr(main, "FRONTEND_URL", "https://www.optionstaxhub.com")
    monkeypatch.setenv("RENDER_SERVICE_NAME", "options-tax-hub-server-prod")
    monkeypatch.delenv("STRIPE_FORCE_TEST_MODE", raising=False)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: True)
    response = _post_signed_webhook(_packet_checkout_event(livemode=True))
    assert response.status_code == 200
    assert response.json()["granted"] is True
    assert PACKET_STORE["analysis-sample-1"]["user_id"] == "test-user-123"


def test_webhook_acknowledges_paid_session_without_owner_without_granting(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)

    response = _post_signed_webhook(_packet_checkout_event(user_id=""))

    assert response.status_code == 200
    assert response.json()["granted"] is False
    assert PACKET_STORE["analysis-sample-1"]["paid"] is False


def test_unpaid_completed_webhook_does_not_unlock_packet(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    response = _post_signed_webhook(_packet_checkout_event(payment_status="unpaid"))
    assert response.status_code == 200
    assert response.json()["granted"] is False
    assert client.get("/api/year-close-packet/download?analysis_id=analysis-sample-1").status_code == 403


def test_confirm_packet_analysis_when_client_analysis_id_missing(monkeypatch):
    """Success URL has packet_analysis; client analysis_id is optional / local-analysis."""
    _test_stripe_env(monkeypatch)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: True)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)

    paid_session = _stripe_object_session(
        id="cs_test_paid_packet_analysis",
        payment_status="paid",
        status="complete",
        amount_total=4900,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda session_id, **_kwargs: paid_session,
    )

    confirm = client.post(
        "/api/year-close-packet/confirm",
        json={
            "session_id": "cs_test_paid_packet_analysis",
            "packet_analysis": "analysis-sample-1",
            "analysis": SAMPLE_ANALYSIS,
        },
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["paid"] is True
    assert confirm.json()["analysis_id"] == "analysis-sample-1"

    paid = client.get(
        "/api/year-close-packet/download",
        params={
            "analysis_id": "analysis-sample-1",
            "session_id": "cs_test_paid_packet_analysis",
        },
    )
    assert paid.status_code == 200
    assert "year-close-packet.pdf" in paid.headers.get("content-disposition", "")


def test_confirm_uses_session_analysis_id_when_client_sends_local_analysis(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: True)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)

    paid_session = _stripe_object_session(
        id="cs_test_paid_local",
        payment_status="paid",
        status="complete",
        amount_total=4900,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda session_id, **_kwargs: paid_session,
    )

    confirm = client.post(
        "/api/year-close-packet/confirm",
        json={
            "analysis_id": "local-analysis",
            "session_id": "cs_test_paid_local",
            "analysis": SAMPLE_ANALYSIS,
        },
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["analysis_id"] == "analysis-sample-1"


def test_local_alias_download_uses_paid_owner_snapshot_not_client_body(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-sample-1"
    saved_payload = build_packet_payload(SAMPLE_ANALYSIS, analysis_id=analysis_id)
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", analysis_id)] = {
        "analysis_id": analysis_id,
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": saved_payload,
        "packet_session_id": "cs_test_paid_local_alias",
        "paid_at": "now",
    }
    paid_session = _stripe_object_session(
        id="cs_test_paid_local_alias",
        metadata={"analysis_id": analysis_id, "tax_year": "2025"},
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: paid_session,
    )
    reset_packet_store()
    foreign_body = {
        "tax_profile": {"tax_year": 2026},
        "supplemental_1099": {"short_term_proceeds": 999999.0},
        "summary": {"realized_summary": {"net_st": 999999.0}},
    }

    response = client.post(
        "/api/year-close-packet/download",
        json={
            "analysis_id": "local-analysis",
            "session_id": paid_session.id,
            "analysis": foreign_body,
        },
    )

    assert response.status_code == 400, response.text
    assert "re-run" in response.json()["detail"].lower()


def test_three_dollar_stripe_object_tip_does_not_unlock():
    tip = _stripe_object_session(
        payment_status="paid",
        status="complete",
        amount_total=300,
        metadata={"product": "tip", "tier": "coffee"},
    )
    assert session_grants_packet(tip, "analysis-sample-1") is False
    wrong_amount = _stripe_object_session(
        payment_status="paid",
        status="complete",
        amount_total=300,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
        },
    )
    assert session_grants_packet(wrong_amount, "analysis-sample-1") is False


def test_anonymous_packet_store_expires(monkeypatch):
    """Anonymous PACKET_STORE entries expire so guest analyses cannot accumulate forever."""
    import year_close_packet as packet_mod

    monkeypatch.setattr(packet_mod, "ANON_PACKET_TTL_SECONDS", 10)
    remember_analysis("anon-1", "", SAMPLE_ANALYSIS)
    assert "anon-1" in PACKET_STORE
    PACKET_STORE["anon-1"]["created_at"] = 0
    purge_packet_store(now=100)
    assert "anon-1" not in PACKET_STORE


def test_anonymous_packet_store_evicts_oldest_when_over_cap(monkeypatch):
    """Anonymous PACKET_STORE is capped; oldest unpaid guest snapshots are evicted first."""
    import year_close_packet as packet_mod

    monkeypatch.setattr(packet_mod, "ANON_PACKET_STORE_MAX", 2)
    remember_analysis("anon-a", "", SAMPLE_ANALYSIS)
    remember_analysis("anon-b", "", SAMPLE_ANALYSIS)
    remember_analysis("anon-c", "", SAMPLE_ANALYSIS)
    assert "anon-a" not in PACKET_STORE
    assert "anon-b" in PACKET_STORE
    assert "anon-c" in PACKET_STORE


def test_authenticated_packet_store_survives_anon_cap(monkeypatch):
    """Signed-in snapshots are not evicted by the anonymous cap."""
    import year_close_packet as packet_mod

    monkeypatch.setattr(packet_mod, "ANON_PACKET_STORE_MAX", 1)
    remember_analysis("auth-1", "test-user-123", SAMPLE_ANALYSIS)
    remember_analysis("anon-a", "", SAMPLE_ANALYSIS)
    remember_analysis("anon-b", "", SAMPLE_ANALYSIS)
    assert "auth-1" in PACKET_STORE
    assert "anon-a" not in PACKET_STORE
    assert "anon-b" in PACKET_STORE


def test_paid_session_reused_for_same_user_and_year():
    reset_packet_store()
    remember_analysis("first", "test-user-123", SAMPLE_ANALYSIS)
    mark_paid("first", "cs_test_repeat", user_id="test-user-123")
    assert paid_session_for_user_year("test-user-123", 2025) == "cs_test_repeat"
    assert paid_session_for_user_year("someone-else", 2026) is None


SAME_YEAR_ANALYSIS = {
    **SAMPLE_ANALYSIS,
    "analysis_id": "analysis-same-year-2024",
    "tax_profile": {"tax_year": 2024, "filing_status": "single"},
    "summary": {
        "realized_summary": {
            "tax_year": 2024,
            "st_gains": 0.0,
            "st_losses": -300.0,
            "lt_gains": 0.0,
            "lt_losses": 0.0,
            "net_st": -300.0,
            "net_lt": 0.0,
            "total_net": -300.0,
            "transactions_count": 1,
        }
    },
    "wash_sale_flags": [
        {
            "symbol": "AMD",
            "sale_date": "2024-07-15",
            "sale_quantity": 10,
            "sale_loss": 300.0,
            "repurchase_date": "2024-07-24",
            "repurchase_quantity": 10,
            "disallowed_loss": 300.0,
            "adjusted_cost_basis": 1550.0,
            "explanation": "Wash sale on AMD",
        }
    ],
}

MISMATCH_2026_ANALYSIS = {
    **SAMPLE_ANALYSIS,
    "analysis_id": "analysis-2026-sample",
    "tax_profile": {"tax_year": 2026, "filing_status": "single"},
    "summary": {
        "realized_summary": {
            "tax_year": 2026,
            "st_gains": 0.0,
            "st_losses": -300.0,
            "lt_gains": 0.0,
            "lt_losses": 0.0,
            "net_st": -300.0,
            "net_lt": 0.0,
            "total_net": -300.0,
            "transactions_count": 1,
        }
    },
}


def test_same_year_is_decided_by_1099_year_equals_dashboard_year():
    assert is_same_year_1099_compare(2024, 2024) is True
    assert is_same_year_1099_compare(2024, 2026) is False
    assert is_same_year_1099_compare(2024, 2025) is False
    assert is_same_year_1099_compare(None, 2024) is False
    assert is_same_year_1099_compare(2024, None) is False


def test_export_net_matching_1099_folds_classified_wash():
    assert export_net_matching_1099(-300.0, 300.0) == 0.0
    assert export_net_matching_1099(-300.0, 0.0) == -300.0
    st_wash, lt_wash = classified_csv_wash(SAME_YEAR_ANALYSIS)
    assert st_wash == 300.0
    assert lt_wash == 0.0


LT_WASH_FLAG = {
    "symbol": "AMD",
    "purchase_date": "2023-01-01",
    "sale_date": "2024-07-15",
    "sale_quantity": 10,
    "sale_loss": 300.0,
    "repurchase_date": "2024-07-24",
    "repurchase_quantity": 10,
    "disallowed_loss": 300.0,
    "adjusted_cost_basis": 1550.0,
    "explanation": "Long-term wash sale on AMD",
}

LT_WASH_ANALYSIS = {
    **SAME_YEAR_ANALYSIS,
    "analysis_id": "analysis-lt-wash-2024",
    "summary": {
        "realized_summary": {
            "tax_year": 2024,
            "st_gains": 0.0,
            "st_losses": -1000.0,
            "lt_gains": 0.0,
            "lt_losses": -300.0,
            "net_st": -1000.0,
            "net_lt": -300.0,
            "total_net": -1300.0,
            "transactions_count": 2,
        }
    },
    "wash_sale_flags": [LT_WASH_FLAG],
    "supplemental_1099": {
        "source_filename": "lt-wash.pdf",
        "broker_name": "Robinhood",
        "tax_year": 2024,
        "short_term_proceeds": 5000.0,
        "short_term_cost_basis": 6000.0,
        "short_term_wash_sale_disallowed": 0.0,
        "short_term_net_gain": -1000.0,
        "long_term_proceeds": 1200.0,
        "long_term_cost_basis": 1500.0,
        "long_term_wash_sale_disallowed": 300.0,
        "long_term_net_gain": 0.0,
    },
}

MISSING_REALIZED_ANALYSIS = {
    **SAME_YEAR_ANALYSIS,
    "analysis_id": "analysis-missing-realized-2024",
    "summary": {"realized_summary": None},
    "supplemental_1099": {
        "source_filename": "wash-aligned.pdf",
        "broker_name": "Robinhood",
        "tax_year": 2024,
        "short_term_proceeds": 1200.0,
        "short_term_cost_basis": 1500.0,
        "short_term_wash_sale_disallowed": 300.0,
        "short_term_net_gain": 0.0,
        "long_term_proceeds": 0.0,
        "long_term_cost_basis": 0.0,
        "long_term_wash_sale_disallowed": 0.0,
        "long_term_net_gain": 0.0,
    },
}


def test_classified_csv_wash_uses_flag_holding_term_not_st_loss_buckets():
    assert wash_flag_is_long_term(LT_WASH_FLAG) is True
    st_wash, lt_wash = classified_csv_wash(LT_WASH_ANALYSIS)
    assert st_wash == 0.0
    assert lt_wash == 300.0
    totals = export_realized_totals(LT_WASH_ANALYSIS)
    assert totals["short_term_net"] == -1000.0
    assert totals["long_term_net"] == 0.0

    payload = build_packet_payload(LT_WASH_ANALYSIS, analysis_id="analysis-lt-wash-2024")
    assert payload["same_year_compare"] is True
    assert payload["export_short_term_net"] == -1000.0
    assert payload["export_long_term_net"] == 0.0
    assert payload["export_wash_sale_disallowed"] == 300.0
    compare = same_year_compare_plain_text(payload)
    assert _two_col_row("Short-term", "$-1,000.00", "$-1,000.00") in compare
    assert _two_col_row("Long-term", "$0.00", "$0.00") in compare
    assert "$-700.00" not in compare
    pdf_text = _pdf_text(render_packet_pdf(payload))
    assert COMPARE_TITLE in pdf_text
    assert "$-1,000.00" in pdf_text
    assert "$-700.00" not in pdf_text


def test_missing_realized_summary_does_not_synthesize_short_term_net():
    totals = export_realized_totals(MISSING_REALIZED_ANALYSIS)
    assert totals["short_term_net"] == 0.0
    assert totals["long_term_net"] == 0.0
    assert totals["wash_sale_disallowed"] == 300.0
    no_summary = {**MISSING_REALIZED_ANALYSIS, "summary": None}
    assert export_realized_totals(no_summary)["short_term_net"] == 0.0

    payload = build_packet_payload(
        MISSING_REALIZED_ANALYSIS, analysis_id="analysis-missing-realized-2024"
    )
    assert payload["same_year_compare"] is True
    assert payload["export_short_term_net"] == 0.0
    assert payload["export_long_term_net"] == 0.0
    assert payload["export_wash_sale_disallowed"] == 300.0
    compare = same_year_compare_plain_text(payload)
    assert _two_col_row("Short-term", "$0.00", "$0.00") in compare
    assert _two_col_row("Wash-sale disallowed", "$300.00", "$300.00") in compare
    pdf_text = _pdf_text(render_packet_pdf(payload))
    assert COMPARE_TITLE in pdf_text
    assert _two_col_row("Short-term", "$0.00", "$0.00") in pdf_text or "$0.00" in pdf_text


def test_mismatch_2026_sample_plus_2024_fixture_is_previous_year_supplement():
    analysis = {
        **MISMATCH_2026_ANALYSIS,
        "suggestions": [
            {
                "symbol": "AMD",
                "display_label": "AMD",
                "quantity": 10,
                "estimated_loss": 250.0,
                "tax_savings_estimate": 37.5,
                "is_long_term": True,
            }
        ],
    }
    payload = build_packet_payload(analysis, analysis_id="analysis-2026-sample")
    assert payload["same_year_compare"] is False
    assert payload["form_1099_tax_year"] == 2024
    assert payload["analysis_tax_year"] == 2026
    assert payload.get("harvest_opportunities")
    text = packet_plain_text(payload)
    assert "previous-year supplement" in text
    assert "included as a dedicated page" not in text
    pdf_bytes = render_packet_pdf(payload)
    pdf_text = _pdf_text(pdf_bytes)
    assert HARVEST_TITLE in pdf_text
    assert COMPARE_TITLE not in pdf_text
    assert "settlement date" in pdf_text.lower()
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) == _expected_packet_pdf_pages(payload)


def test_harvest_opportunities_add_a_packet_pdf_page():
    """render_packet_pdf appends Harvest when harvest_opportunities are present."""
    bare = build_packet_payload(
        MISMATCH_2026_ANALYSIS, analysis_id="analysis-mismatch-no-harvest"
    )
    harvested = build_packet_payload(
        {
            **MISMATCH_2026_ANALYSIS,
            "suggestions": [
                {
                    "symbol": "AMD",
                    "display_label": "AMD",
                    "quantity": 10,
                    "estimated_loss": 250.0,
                    "tax_savings_estimate": 37.5,
                    "is_long_term": True,
                }
            ],
        },
        analysis_id="analysis-mismatch-harvest",
    )
    assert not bare.get("harvest_opportunities")
    assert harvested.get("harvest_opportunities")
    bare_pdf = render_packet_pdf(bare)
    harvested_pdf = render_packet_pdf(harvested)
    assert len(PdfReader(BytesIO(bare_pdf)).pages) == 1
    assert len(PdfReader(BytesIO(harvested_pdf)).pages) == 2
    assert HARVEST_TITLE not in _pdf_text(bare_pdf)
    assert HARVEST_TITLE in _pdf_text(harvested_pdf)
    assert COMPARE_TITLE not in _pdf_text(harvested_pdf)


def test_same_year_packet_pdf_has_two_column_compare_page():
    payload = build_packet_payload(SAME_YEAR_ANALYSIS, analysis_id="analysis-same-year-2024")
    assert payload["same_year_compare"] is True
    assert payload["form_1099_tax_year"] == 2024
    assert payload["analysis_tax_year"] == 2024
    assert payload["short_term_net_gain"] == 34793.58
    assert payload["export_short_term_net"] == 0.0
    assert payload["export_wash_sale_disallowed"] == 300.0
    assert payload["compare_gap_copy"] == COMPARE_GAP_COPY

    compare = same_year_compare_plain_text(payload)
    assert COMPARE_TITLE in compare
    assert "Broker 1099 (settlement date)" in compare
    assert "This export (trade date)" in compare
    assert "$34,793.58" in compare
    assert "$0.00" in compare
    assert "$-300.00" not in compare
    assert "$17,442.80" in compare
    assert "$300.00" in compare
    assert COMPARE_GAP_COPY in compare
    assert "SPX 12/31" in compare
    assert "not a software bug" in compare
    assert "r/options" in compare

    pdf_bytes = render_packet_pdf(payload)
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) == _expected_packet_pdf_pages(payload)
    pdf_text = _pdf_text_normalized(pdf_bytes)
    assert COMPARE_TITLE in pdf_text
    assert "Broker 1099 (settlement date)" in pdf_text
    assert "This export (trade date)" in pdf_text
    assert "34,793.58" in pdf_text
    assert "300.00" in pdf_text
    assert "not a software bug" in pdf_text.lower()
    assert "r/options" in pdf_text.lower()


WASH_ALIGNED_1099 = {
    "source_filename": "wash-aligned.pdf",
    "broker_name": "Robinhood",
    "tax_year": 2024,
    "short_term_proceeds": 1200.0,
    "short_term_cost_basis": 1500.0,
    "short_term_wash_sale_disallowed": 300.0,
    "short_term_net_gain": 0.0,
    "long_term_proceeds": 0.0,
    "long_term_cost_basis": 0.0,
    "long_term_wash_sale_disallowed": 0.0,
    "long_term_net_gain": 0.0,
}

WASH_ALIGNED_ANALYSIS = {
    **SAME_YEAR_ANALYSIS,
    "analysis_id": "analysis-wash-aligned-2024",
    "supplemental_1099": WASH_ALIGNED_1099,
}

UNKNOWN_YEAR_ANALYSIS = {
    **SAMPLE_ANALYSIS,
    "analysis_id": "analysis-unknown-year",
    "tax_profile": {"tax_year": 2024, "filing_status": "single"},
    "supplemental_1099": {
        **SAMPLE_ANALYSIS["supplemental_1099"],
        "tax_year": None,
    },
}


def test_three_hundred_loss_plus_wash_does_not_look_like_settlement_gap():
    payload = build_packet_payload(
        WASH_ALIGNED_ANALYSIS, analysis_id="analysis-wash-aligned-2024"
    )
    assert payload["same_year_compare"] is True
    assert payload["short_term_net_gain"] == 0.0
    assert payload["export_short_term_net"] == 0.0
    assert payload["wash_sale_disallowed_1099"] == 300.0
    assert payload["export_wash_sale_disallowed"] == 300.0

    compare = same_year_compare_plain_text(payload)
    assert "Short-term" in compare
    assert compare.count("$0.00") >= 2
    assert "$-300.00" not in compare
    assert "$300.00" in compare
    assert _two_col_row("Short-term", "$0.00", "$0.00") in compare
    assert _two_col_row("Wash-sale disallowed", "$300.00", "$300.00") in compare

    pdf_text = _pdf_text(render_packet_pdf(payload))
    assert COMPARE_TITLE in pdf_text
    assert "$-300.00" not in pdf_text
    assert "$0.00" in pdf_text
    assert "$300.00" in pdf_text
    reader = PdfReader(BytesIO(render_packet_pdf(payload)))
    assert len(reader.pages) == _expected_packet_pdf_pages(payload)


def test_unknown_1099_year_is_not_previous_year_mismatch_or_same_year_compare():
    analysis = {
        **UNKNOWN_YEAR_ANALYSIS,
        "suggestions": [
            {
                "symbol": "AMD",
                "display_label": "AMD",
                "quantity": 10,
                "estimated_loss": 250.0,
                "tax_savings_estimate": 37.5,
                "is_long_term": True,
            }
        ],
    }
    payload = build_packet_payload(analysis, analysis_id="analysis-unknown-year")
    assert payload["same_year_compare"] is False
    assert payload["unknown_1099_year"] is True
    assert payload["form_1099_tax_year"] is None
    assert payload["form_1099_applied"] is True
    assert payload.get("harvest_opportunities")
    text = packet_plain_text(payload)
    assert UNKNOWN_1099_YEAR_COPY in text
    assert "1099 tax year: unknown" in text
    assert "previous-year supplement" not in text
    assert "does not match this export" not in text
    assert "included as a dedicated page" not in text
    pdf_bytes = render_packet_pdf(payload)
    pdf_text = _pdf_text(pdf_bytes)
    assert HARVEST_TITLE in pdf_text
    assert COMPARE_TITLE not in pdf_text
    assert "could not be determined" in pdf_text
    assert "not a previous-year mismatch" in pdf_text
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) == _expected_packet_pdf_pages(payload)


def test_same_year_compare_is_visible_without_payment_but_download_stays_gated(monkeypatch):
    _test_stripe_env(monkeypatch)
    main.remember_analysis("analysis-same-year-2024", "test-user-123", SAME_YEAR_ANALYSIS)
    payload = build_packet_payload(SAME_YEAR_ANALYSIS)
    assert payload["same_year_compare"] is True
    unpaid = client.get("/api/year-close-packet/download?analysis_id=analysis-same-year-2024")
    assert unpaid.status_code == 403
    assert "payment" in unpaid.json()["detail"].lower()


def test_tipjar_still_does_not_unlock_same_year_packet(monkeypatch):
    _test_stripe_env(monkeypatch)
    main.remember_analysis("analysis-same-year-2024", "test-user-123", SAME_YEAR_ANALYSIS)
    tip_session = SimpleNamespace(
        id="cs_test_tip_coffee",
        payment_status="paid",
        amount_total=300,
        metadata={"product": "tip", "tier": "coffee"},
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda session_id, **_kwargs: tip_session,
    )
    confirm = client.post(
        "/api/year-close-packet/confirm",
        json={
            "analysis_id": "analysis-same-year-2024",
            "session_id": "cs_test_tip_coffee",
        },
    )
    assert confirm.status_code == 403
    unpaid = client.get("/api/year-close-packet/download?analysis_id=analysis-same-year-2024")
    assert unpaid.status_code == 403


LOT_MATCH_ANALYSIS = {
    **SAME_YEAR_ANALYSIS,
    "analysis_id": "analysis-lot-match-2024",
    "lot_match_report": {
        "matched": [
            {
                "status": "matched",
                "symbol": "AMD",
                "quantity": 10,
                "date_sold_1099": "2026-07-15",
                "export_trade_date": "2026-07-15",
                "proceeds_1099": 1200.0,
                "proceeds_export": 1200.0,
            }
        ],
        "gap": [
            {
                "status": "matched_settlement_gap",
                "symbol": "NVDA",
                "quantity": 12,
                "date_sold_1099": "2026-02-20",
                "export_trade_date": "2026-02-18",
                "proceeds_1099": 2976.0,
                "proceeds_export": 2976.0,
            }
        ],
        "unmatched": [
            {
                "status": "1099_only",
                "symbol": "SPX",
                "quantity": 1,
                "date_sold_1099": "2027-01-02",
                "export_trade_date": None,
                "proceeds_1099": 2699.0,
                "proceeds_export": 0.0,
            },
            {
                "status": "csv_only",
                "symbol": "META",
                "quantity": 4,
                "date_sold_1099": None,
                "export_trade_date": "2026-03-20",
                "proceeds_1099": 0.0,
                "proceeds_export": 2880.0,
            },
        ],
        "matched_count": 1,
        "gap_count": 1,
        "unmatched_count": 2,
        "totals_ok": True,
    },
}


def test_paid_pdf_has_matched_gap_unmatched_lot_sections():
    payload = build_packet_payload(
        LOT_MATCH_ANALYSIS, analysis_id="analysis-lot-match-2024"
    )
    text = packet_plain_text(payload)
    assert LOT_MATCH_TITLE in text
    assert "1 matched" in text
    assert "1 gap" in text
    pdf_text = _pdf_text(render_packet_pdf(payload))
    assert LOT_MATCH_TITLE in pdf_text
    assert "Matched (1)" in pdf_text
    assert "Gap (1)" in pdf_text
    assert "Unmatched (2)" in pdf_text
    assert "matched AMD" in pdf_text
    assert "matched_settlement_gap NVDA" in pdf_text
    assert "1099_only SPX" in pdf_text
    assert "csv_only META" in pdf_text
    assert "not a filed Form 8949" in pdf_text
    assert "we do not parse settlement" not in pdf_text.lower()


def test_paid_pdf_has_harvest_rows_and_single_wash_event():
    analysis = {
        **LOT_MATCH_ANALYSIS,
        "suggestions": [
            {
                "symbol": "TSLA",
                "display_label": "TSLA",
                "action": "SELL",
                "quantity": 1,
                "cost_basis_per_share": 250,
                "estimated_loss": 50,
                "tax_savings_estimate": 12.0,
                "holding_period_days": 120,
                "is_long_term": False,
            },
            {
                "symbol": "AMD",
                "display_label": "AMD",
                "action": "SELL",
                "quantity": 10,
                "cost_basis_per_share": 125,
                "estimated_loss": 300,
                "tax_savings_estimate": 74.0,
                "holding_period_days": 400,
                "is_long_term": True,
                "wash_sale_risk": True,
                "wash_sale_explanation": "Recent AMD buy inside 30 days.",
            },
        ],
    }
    payload = build_packet_payload(analysis, analysis_id="analysis-harvest")
    harvest = payload["harvest_opportunities"]
    assert len(harvest) == 2
    assert harvest[0]["symbol"] == "TSLA"
    assert harvest[0]["term"] == "ST"
    assert harvest[0]["estimated_federal_savings"] == 12.0
    assert harvest[0]["wash_sale_risk"] is False
    assert harvest[1]["term"] == "LT"
    assert harvest[1]["wash_sale_risk"] is True
    pdf_text = _pdf_text(render_packet_pdf(payload))
    pdf_norm = _pdf_text_normalized(render_packet_pdf(payload))
    assert HARVEST_TITLE in pdf_text
    assert "TSLA  ST  qty 1  estimated federal savings $12.00" in pdf_text
    assert "AMD  LT  qty 10  wash-sale risk - not clean federal savings $74.00" in pdf_text
    assert "Recent AMD buy inside 30 days." in pdf_text
    assert "Recent AMD buy inside 30 days." in pdf_norm
    assert "AMD  LT  estimated federal savings $74.00" not in pdf_text
    assert pdf_text.count("AMD $300.00 disallowed") == 1
    assert "sale 2024-07-15" in pdf_text
    assert "repurchase 2024-07-24" in pdf_text
    assert LOT_MATCH_TITLE in pdf_text
    assert COMPARE_TITLE in pdf_text
    assert "not a filed Form 8949" in pdf_text


def test_harvest_pdf_rows_keep_per_lot_identity():
    analysis = {
        **LOT_MATCH_ANALYSIS,
        "suggestions": [
            {
                "symbol": "AMD",
                "display_label": "AMD",
                "suggestion_id": "AMD::stock::stock-lot::2024-01-02::100::10",
                "lot_details": "Tax lot opened Jan 02, 2024 at $100.00/share",
                "quantity": 10,
                "cost_basis_per_share": 100.0,
                "estimated_loss": 40,
                "tax_savings_estimate": 10.0,
                "is_long_term": False,
            },
            {
                "symbol": "AMD",
                "display_label": "AMD",
                "suggestion_id": "AMD::stock::stock-lot::2025-06-01::125::10",
                "lot_details": "Tax lot opened Jun 01, 2025 at $125.00/share",
                "quantity": 10,
                "cost_basis_per_share": 125.0,
                "estimated_loss": 40,
                "tax_savings_estimate": 10.0,
                "is_long_term": False,
            },
        ],
    }
    payload = build_packet_payload(analysis, analysis_id="analysis-two-amd-lots")
    harvest = payload["harvest_opportunities"]
    assert len(harvest) == 2
    assert harvest[0]["term"] == harvest[1]["term"] == "ST"
    first_lines = [_harvest_lines(row)[0] for row in harvest]
    assert first_lines[0] != first_lines[1]
    pdf_text = _pdf_text(render_packet_pdf(payload))
    _assert_three_identity(
        harvest[0],
        pdf_text,
        qty=10.0,
        opened="2024-01-02",
        suggestion_id="AMD::stock::stock-lot::2024-01-02::100::10",
        lot_details="Tax lot opened Jan 02, 2024 at $100.00/share",
    )
    _assert_three_identity(
        harvest[1],
        pdf_text,
        qty=10.0,
        opened="2025-06-01",
        suggestion_id="AMD::stock::stock-lot::2025-06-01::125::10",
        lot_details="Tax lot opened Jun 01, 2025 at $125.00/share",
    )
    assert pdf_text.count("AMD  ST  qty 10") == 2


def test_lot_match_pdf_paginates_instead_of_dropping_rows():
    rows = [
        {
            "status": "matched",
            "symbol": f"S{i:03d}",
            "quantity": 1,
            "date_sold_1099": "2026-01-02",
            "export_trade_date": "2026-01-01",
            "proceeds_1099": 10.0 + i,
            "proceeds_export": 10.0 + i,
        }
        for i in range(55)
    ]
    analysis = {
        **LOT_MATCH_ANALYSIS,
        "lot_match_report": {
            "matched": rows,
            "gap": [],
            "unmatched": [],
            "matched_count": 55,
            "gap_count": 0,
            "unmatched_count": 0,
            "totals_ok": True,
        },
    }
    pdf_bytes = render_packet_pdf(build_packet_payload(analysis, analysis_id="many-lots"))
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) > 2
    pdf_text = _pdf_text(pdf_bytes)
    assert "S000" in pdf_text
    assert "S054" in pdf_text


def test_counts_only_client_payload_does_not_wipe_server_lot_rows():
    """Paid PDF keeps full rows even if checkout/confirm sends redacted analyze JSON."""
    analysis_id = "analysis-preserve-rows"
    full = {
        **LOT_MATCH_ANALYSIS,
        "analysis_id": analysis_id,
    }
    remember_analysis(analysis_id, "test-user-123", full)
    counts_only = {
        **full,
        "lot_match_report": {
            "matched": [],
            "gap": [],
            "unmatched": [],
            "matched_count": 1,
            "gap_count": 1,
            "unmatched_count": 1,
            "totals_ok": True,
        },
    }
    remember_analysis(analysis_id, "test-user-123", counts_only)
    upsert_payload(analysis_id, "test-user-123", counts_only)
    payload = get_payload(analysis_id)
    assert payload is not None
    report = payload["lot_match_report"]
    assert any(row["symbol"] == "NVDA" for row in report["gap"])
    assert any(row["symbol"] == "SPX" for row in report["unmatched"])
    pdf_text = _pdf_text(render_packet_pdf(payload))
    assert "NVDA" in pdf_text
    assert "1099_only SPX" in pdf_text


def test_compact_client_payload_does_not_wipe_harvest_rows():
    analysis_id = "analysis-preserve-harvest"
    full = {
        **LOT_MATCH_ANALYSIS,
        "analysis_id": analysis_id,
        "suggestions": [
            {
                "symbol": "TSLA",
                "display_label": "TSLA",
                "estimated_loss": 50,
                "tax_savings_estimate": 12.0,
                "is_long_term": False,
                "wash_sale_risk": False,
            }
        ],
    }
    remember_analysis(analysis_id, "test-user-123", full)
    compact = {k: v for k, v in full.items() if k != "suggestions"}
    remember_analysis(analysis_id, "test-user-123", compact)
    payload = get_payload(analysis_id)
    assert payload is not None
    harvest = payload["harvest_opportunities"]
    assert harvest
    assert harvest[0]["symbol"] == "TSLA"
    assert harvest[0]["estimated_federal_savings"] == 12.0
    pdf_text = _pdf_text(render_packet_pdf(payload))
    assert HARVEST_TITLE in pdf_text
    assert "TSLA  ST  qty" in pdf_text
    assert "estimated federal savings $12.00" in pdf_text


def test_reconstruct_harvest_from_compact_suggestions():
    compact = {
        **LOT_MATCH_ANALYSIS,
        "suggestions": [
            {
                "symbol": "TSLA",
                "display_label": "TSLA",
                "suggestion_id": "TSLA::stock::stock-lot::2025-01-01::250::1",
                "lot_details": "Tax lot opened Jan 01, 2025 at $250.00/share",
                "quantity": 1,
                "estimated_loss": 50,
                "tax_savings_estimate": 12.0,
                "is_long_term": False,
                "wash_sale_risk": True,
                "wash_sale_explanation": "Recent TSLA buy inside 30 days.",
            }
        ],
    }
    payload = build_packet_payload(compact, analysis_id="analysis-compact-harvest")
    harvest = payload["harvest_opportunities"]
    assert len(harvest) == 1
    assert harvest[0]["wash_sale_risk"] is True
    pdf_text = _pdf_text(render_packet_pdf(payload))
    pdf_norm = _pdf_text_normalized(render_packet_pdf(payload))
    _assert_three_identity(
        harvest[0],
        pdf_text,
        qty=1.0,
        opened="2025-01-01",
        suggestion_id="TSLA::stock::stock-lot::2025-01-01::250::1",
        lot_details="Tax lot opened Jan 01, 2025 at $250.00/share",
    )
    assert "wash-sale risk - not clean federal savings $12.00" in pdf_text
    assert "Recent TSLA buy inside 30 days." in pdf_text
    assert "Recent TSLA buy inside 30 days." in pdf_norm


def test_reconstruct_two_amd_lots_from_compact_suggestions():
    """Compact checkout JSON still yields two distinct AMD harvest rows."""
    compact = {
        **LOT_MATCH_ANALYSIS,
        "suggestions": [
            {
                "symbol": "AMD",
                "display_label": "AMD",
                "suggestion_id": "AMD::stock::stock-lot::2024-01-02::100::10",
                "lot_details": "Tax lot opened Jan 02, 2024 at $100.00/share",
                "quantity": 10,
                "purchase_date": "2024-01-02",
                "cost_basis_per_share": 100.0,
                "estimated_loss": 40,
                "tax_savings_estimate": 10.0,
                "is_long_term": False,
                "wash_sale_risk": False,
            },
            {
                "symbol": "AMD",
                "display_label": "AMD",
                "suggestion_id": "AMD::stock::stock-lot::2025-06-01::125::10",
                "lot_details": "Tax lot opened Jun 01, 2025 at $125.00/share",
                "quantity": 10,
                "purchase_date": "2025-06-01",
                "cost_basis_per_share": 125.0,
                "estimated_loss": 40,
                "tax_savings_estimate": 10.0,
                "is_long_term": False,
                "wash_sale_risk": False,
            },
        ],
    }
    payload = build_packet_payload(compact, analysis_id="analysis-compact-two-amd")
    harvest = payload["harvest_opportunities"]
    assert [row["term"] for row in harvest] == ["ST", "ST"]
    first_lines = [_harvest_lines(row)[0] for row in harvest]
    assert first_lines[0] != first_lines[1]
    pdf_text = _pdf_text(render_packet_pdf(payload))
    _assert_three_identity(
        harvest[0],
        pdf_text,
        qty=10.0,
        opened="2024-01-02",
        suggestion_id="AMD::stock::stock-lot::2024-01-02::100::10",
        lot_details="Tax lot opened Jan 02, 2024 at $100.00/share",
    )
    _assert_three_identity(
        harvest[1],
        pdf_text,
        qty=10.0,
        opened="2025-06-01",
        suggestion_id="AMD::stock::stock-lot::2025-06-01::125::10",
        lot_details="Tax lot opened Jun 01, 2025 at $125.00/share",
    )
    assert pdf_text.count("AMD  ST  qty 10") == 2


def test_same_symbol_same_term_lots_distinct_without_lot_details():
    """Compact JSON that omitted lot_details still cannot collapse two AMD ST lots."""
    compact = {
        **LOT_MATCH_ANALYSIS,
        "suggestions": [
            {
                "symbol": "AMD",
                "display_label": "AMD",
                "suggestion_id": "AMD::stock::stock-lot::2024-01-02::100.000000::10.000000",
                "quantity": 10,
                "cost_basis_per_share": 100.0,
                "estimated_loss": 40,
                "tax_savings_estimate": 10.0,
                "is_long_term": False,
            },
            {
                "symbol": "AMD",
                "display_label": "AMD",
                "suggestion_id": "AMD::stock::stock-lot::2025-06-01::125.000000::10.000000",
                "quantity": 10,
                "cost_basis_per_share": 125.0,
                "estimated_loss": 40,
                "tax_savings_estimate": 10.0,
                "is_long_term": False,
            },
        ],
    }
    payload = build_packet_payload(compact, analysis_id="analysis-amd-st-no-details")
    harvest = payload["harvest_opportunities"]
    first_lines = [_harvest_lines(row)[0] for row in harvest]
    assert first_lines[0] != first_lines[1]
    pdf_text = _pdf_text(render_packet_pdf(payload))
    _assert_three_identity(
        harvest[0],
        pdf_text,
        qty=10.0,
        opened="2024-01-02",
        suggestion_id="AMD::stock::stock-lot::2024-01-02::100.000000::10.000000",
    )
    _assert_three_identity(
        harvest[1],
        pdf_text,
        qty=10.0,
        opened="2025-06-01",
        suggestion_id="AMD::stock::stock-lot::2025-06-01::125.000000::10.000000",
    )
    assert pdf_text.count("AMD  ST  qty 10") == 2


def test_harvest_plain_text_same_qty_rows_keep_qty_date_and_lot_details():
    payload = build_packet_payload(
        {
            **LOT_MATCH_ANALYSIS,
            "suggestions": [
                {
                    "symbol": "AMD",
                    "display_label": "AMD",
                    "suggestion_id": "AMD::stock::stock-lot::2024-01-02::100::10",
                    "lot_details": "Tax lot opened Jan 02, 2024 at $100.00/share",
                    "quantity": 10,
                    "purchase_date": "2024-01-02",
                    "estimated_loss": 40,
                    "tax_savings_estimate": 10.0,
                    "is_long_term": False,
                },
                {
                    "symbol": "AMD",
                    "display_label": "AMD",
                    "suggestion_id": "AMD::stock::stock-lot::2025-06-01::125::10",
                    "lot_details": "Tax lot opened Jun 01, 2025 at $125.00/share",
                    "quantity": 10,
                    "purchase_date": "2025-06-01",
                    "estimated_loss": 40,
                    "tax_savings_estimate": 10.0,
                    "is_long_term": False,
                },
            ],
        },
        analysis_id="analysis-harvest-plain-identity",
    )
    harvest = payload["harvest_opportunities"]
    text = harvest_plain_text(payload)
    identity = [
        line for line in text.splitlines() if line.startswith("AMD  ST  qty 10")
    ]
    assert len(identity) == 2
    assert identity[0] != identity[1]
    _assert_three_identity(
        harvest[0],
        text,
        qty=10.0,
        opened="2024-01-02",
        suggestion_id="AMD::stock::stock-lot::2024-01-02::100::10",
        lot_details="Tax lot opened Jan 02, 2024 at $100.00/share",
    )
    _assert_three_identity(
        harvest[1],
        text,
        qty=10.0,
        opened="2025-06-01",
        suggestion_id="AMD::stock::stock-lot::2025-06-01::125::10",
        lot_details="Tax lot opened Jun 01, 2025 at $125.00/share",
    )


def test_long_lot_details_do_not_drop_purchase_date_from_first_line():
    """Wrap cannot collapse two AMD ST qty-10 lots to the same first line."""
    long_a = "Tax lot opened Jan 02, 2024 at $100.00/share " + ("note-a " * 16)
    long_b = "Tax lot opened Jun 01, 2025 at $125.00/share " + ("note-b " * 16)
    payload = build_packet_payload(
        {
            **LOT_MATCH_ANALYSIS,
            "suggestions": [
                {
                    "symbol": "AMD",
                    "display_label": "AMD",
                    "suggestion_id": "AMD::stock::stock-lot::2024-01-02::100::10",
                    "lot_details": long_a,
                    "quantity": 10,
                    "estimated_loss": 40,
                    "tax_savings_estimate": 10.0,
                    "is_long_term": False,
                },
                {
                    "symbol": "AMD",
                    "display_label": "AMD",
                    "suggestion_id": "AMD::stock::stock-lot::2025-06-01::125::10",
                    "lot_details": long_b,
                    "quantity": 10,
                    "estimated_loss": 40,
                    "tax_savings_estimate": 10.0,
                    "is_long_term": False,
                },
            ],
        },
        analysis_id="analysis-harvest-wrap-identity",
    )
    harvest = payload["harvest_opportunities"]
    first_a = _harvest_lines(harvest[0], colliding=True)[0]
    first_b = _harvest_lines(harvest[1], colliding=True)[0]
    assert first_a != first_b
    pdf_text = _pdf_text(render_packet_pdf(payload))
    _assert_three_identity(
        harvest[0],
        pdf_text,
        qty=10.0,
        opened="2024-01-02",
        suggestion_id="AMD::stock::stock-lot::2024-01-02::100::10",
        lot_details=long_a,
    )
    _assert_three_identity(
        harvest[1],
        pdf_text,
        qty=10.0,
        opened="2025-06-01",
        suggestion_id="AMD::stock::stock-lot::2025-06-01::125::10",
        lot_details=long_b,
    )
    assert "note-a" in pdf_text
    assert "note-b" in pdf_text


def test_compact_explicit_purchase_date_distinguishes_lots():
    """Compact JSON can send purchase_date even when suggestion_id is not parseable."""
    compact = {
        **LOT_MATCH_ANALYSIS,
        "suggestions": [
            {
                "symbol": "AMD",
                "display_label": "AMD",
                "suggestion_id": "amd-lot-jan",
                "quantity": 10,
                "purchase_date": "2024-01-02",
                "estimated_loss": 40,
                "tax_savings_estimate": 10.0,
                "is_long_term": False,
            },
            {
                "symbol": "AMD",
                "display_label": "AMD",
                "suggestion_id": "amd-lot-jun",
                "quantity": 10,
                "purchase_date": "2025-06-01",
                "estimated_loss": 40,
                "tax_savings_estimate": 10.0,
                "is_long_term": False,
            },
        ],
    }
    payload = build_packet_payload(compact, analysis_id="analysis-explicit-dates")
    harvest = payload["harvest_opportunities"]
    pdf_text = _pdf_text(render_packet_pdf(payload))
    _assert_three_identity(
        harvest[0],
        pdf_text,
        qty=10.0,
        opened="2024-01-02",
        suggestion_id="amd-lot-jan",
    )
    _assert_three_identity(
        harvest[1],
        pdf_text,
        qty=10.0,
        opened="2025-06-01",
        suggestion_id="amd-lot-jun",
    )


def test_paid_download_reloads_suggestions_from_history_on_store_miss(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: True)
    analysis_id = "analysis-history-harvest"
    packet_source = {
        **LOT_MATCH_ANALYSIS,
        "analysis_id": analysis_id,
        "suggestions": [
            {
                "symbol": "TSLA",
                "display_label": "TSLA",
                "estimated_loss": 50,
                "tax_savings_estimate": 12.0,
                "is_long_term": False,
                "wash_sale_risk": False,
            }
        ],
    }
    _FAKE_PACKET_SNAPSHOTS[("test-user-123", analysis_id)] = {
        "analysis_id": analysis_id,
        "user_id": "test-user-123",
        "tax_year": 2024,
        "packet_payload": build_packet_payload(packet_source, analysis_id=analysis_id),
        "packet_session_id": "cs_test_hist_harvest",
        "paid_at": "now",
    }
    reset_packet_store()
    mark_paid(analysis_id, "cs_test_hist_harvest", user_id="test-user-123")
    compact = {
        **LOT_MATCH_ANALYSIS,
        "analysis_id": analysis_id,
        "suggestions": [],
    }
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda aid, _uid: (
            {
                "id": aid,
                "result": {
                    "analysis_id": aid,
                    "tax_lots": LOT_MATCH_ANALYSIS["tax_lots"],
                    "lot_match_report": LOT_MATCH_ANALYSIS["lot_match_report"],
                "suggestions": [
                    {
                        "symbol": "TSLA",
                        "display_label": "TSLA",
                        "estimated_loss": 50,
                        "tax_savings_estimate": 12.0,
                        "is_long_term": False,
                        "wash_sale_risk": False,
                    }
                    ],
                },
            },
            True,
        ),
    )
    paid_session = _stripe_object_session(
        id="cs_test_hist_harvest",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": analysis_id,
            "user_id": "test-user-123",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda session_id, **_kwargs: paid_session,
    )
    response = client.post(
        "/api/year-close-packet/download",
        json={
            "analysis_id": analysis_id,
            "session_id": "cs_test_hist_harvest",
            "analysis": compact,
        },
    )
    assert response.status_code == 200
    pdf_text = _pdf_text(response.content)
    assert HARVEST_TITLE in pdf_text
    assert "TSLA  ST  qty" in pdf_text
    assert "estimated federal savings $12.00" in pdf_text


def test_paid_download_uses_server_lot_rows_not_redacted_client_json(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-paid-server-rows"
    user = "test-user-123"
    remember_analysis(analysis_id, user, LOT_MATCH_ANALYSIS)
    mark_paid(analysis_id, "cs_test_paid_rows", user_id=user)
    payload = build_packet_payload(LOT_MATCH_ANALYSIS, analysis_id=analysis_id)
    _FAKE_PACKET_SNAPSHOTS[(user, analysis_id)] = {
        "analysis_id": analysis_id,
        "user_id": user,
        "tax_year": 2024,
        "packet_payload": payload,
        "packet_session_id": "cs_test_paid_rows",
        "paid_at": "2026-01-01T00:00:00+00:00",
    }
    paid_session = _stripe_object_session(
        id="cs_test_paid_rows",
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": analysis_id,
            "user_id": user,
            "tax_year": "2024",
        },
    )
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda session_id, **_kwargs: paid_session,
    )
    redacted = {
        **LOT_MATCH_ANALYSIS,
        "analysis_id": analysis_id,
        "lot_match_report": {
            "matched": [],
            "gap": [],
            "unmatched": [],
            "matched_count": 1,
            "gap_count": 1,
            "unmatched_count": 1,
        },
    }
    response = client.post(
        "/api/year-close-packet/download",
        json={
            "analysis_id": analysis_id,
            "session_id": "cs_test_paid_rows",
            "analysis": redacted,
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    pdf_text = _pdf_text(response.content)
    assert "matched_settlement_gap NVDA" in pdf_text
    assert "1099_only SPX" in pdf_text
    assert PACKET_STORE[analysis_id]["paid"] is True


def test_checkout_encodes_packet_analysis_and_hashes_long_idempotency_keys(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis&id"
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": analysis_id}
    remember_analysis(analysis_id, "test-user-123", analysis)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    captured = {}
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: captured.update(kwargs) or FakeCheckoutSession(**kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": analysis_id, "analysis": analysis},
    )

    assert response.status_code == 200, response.text
    assert "packet_analysis=analysis%26id" in captured["success_url"]
    assert "packet_analysis=analysis&id" not in captured["success_url"]
    assert captured["idempotency_key"] == (
        "year-close-packet:test-user-123:analysis&id:2025"
    )
    assert captured["api_key"] == "sk_test_packet_key"
    long_key = main._packet_checkout_idempotency_key("u" * 240, "a" * 80, 2025)
    assert len(long_key) <= 255
    assert long_key.startswith("year-close-packet:")
    assert long_key != f"year-close-packet:{'u' * 240}:{'a' * 80}:2025"


def test_packet_helpers_do_not_assign_global_stripe_api_key():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "stripe.api_key =" not in source
    assert "stripe.api_key=" not in source


def test_claim_matches_redacted_public_analysis_and_rejects_a_different_run():
    from year_close_packet import claimable_guest_packet_payload

    analysis_id = "guest-public-claim"
    private = {
        **SAMPLE_ANALYSIS,
        "analysis_id": analysis_id,
        "supplemental_1099": {
            **SAMPLE_ANALYSIS["supplemental_1099"],
            "lots": [{"symbol": "AMD", "proceeds": 10}],
        },
        "activity_book": {"transactions": [{"symbol": "AMD", "quantity": 1}]},
        "lot_match_report": {
            "matched": [{"symbol": "AMD", "quantity": 1}],
            "gap": [{"symbol": "NVDA", "quantity": 2}],
            "unmatched": [],
            "matched_count": 1,
            "gap_count": 1,
            "unmatched_count": 0,
        },
    }
    remember_analysis(analysis_id, "", private)
    public = {
        **private,
        "supplemental_1099": {**private["supplemental_1099"], "lots": []},
        "activity_book": {"transactions": []},
        "lot_match_report": {
            **private["lot_match_report"],
            "matched": [],
            "gap": [],
            "unmatched": [],
        },
    }

    claimed = claimable_guest_packet_payload(analysis_id, "test-user-123", public)
    assert claimed is not None
    assert claimed["lot_match_report"]["matched"] == [{"symbol": "AMD", "quantity": 1}]
    claimed_private = claimable_guest_packet_payload(
        analysis_id, "test-user-123", private
    )
    assert claimed_private is not None
    assert claimed_private["lot_match_report"]["gap"] == [{"symbol": "NVDA", "quantity": 2}]

    mismatch = {
        **public,
        "lot_match_report": {**public["lot_match_report"], "matched_count": 9},
    }
    assert claimable_guest_packet_payload(analysis_id, "test-user-123", mismatch) is None
    assert (
        claimable_guest_packet_payload(
            analysis_id,
            "test-user-123",
            {"analysis_id": analysis_id},
        )
        is None
    )
    assert PACKET_STORE[analysis_id]["user_id"] == ""


def test_concurrent_guest_history_returns_the_existing_row(monkeypatch):
    analysis_id = "guest-race"
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": analysis_id}
    remember_analysis(analysis_id, "", analysis)
    existing = {
        "id": "hist-existing",
        "user_id": "test-user-123",
        "result": analysis,
    }
    lookups = iter([(None, True), (existing, True)])
    inserts = []
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: next(lookups),
    )

    def save_history(**kwargs):
        inserts.append(kwargs)
        raise main.HistoryInsertConflict("duplicate key")

    monkeypatch.setattr(main, "save_analysis_history", save_history)

    response = client.post(
        "/api/portfolio/history",
        json={"filename": "guest.csv", "analysis": analysis},
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == "hist-existing"
    assert len(inserts) == 1
    assert PACKET_STORE[analysis_id]["user_id"] == "test-user-123"


def test_new_history_row_rolls_back_when_guest_snapshot_fails(monkeypatch):
    analysis_id = "guest-rollback"
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": analysis_id}
    remember_analysis(analysis_id, "", analysis)
    deleted = []
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: (None, True),
    )
    monkeypatch.setattr(
        main,
        "save_analysis_history",
        lambda **kwargs: {"id": "hist-new", **kwargs},
    )
    monkeypatch.setattr(main, "save_packet_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main,
        "delete_analysis_by_id",
        lambda row_id, user_id: deleted.append((row_id, user_id)) or True,
    )

    response = client.post(
        "/api/portfolio/history",
        json={"filename": "guest.csv", "analysis": analysis},
    )

    assert response.status_code == 503
    assert deleted == [("hist-new", "test-user-123")]
    assert PACKET_STORE[analysis_id]["user_id"] == ""


def test_existing_history_is_not_deleted_when_guest_snapshot_fails(monkeypatch):
    analysis_id = "guest-existing"
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": analysis_id}
    remember_analysis(analysis_id, "", analysis)
    existing = {"id": "hist-old", "user_id": "test-user-123", "result": analysis}
    deleted = []
    inserts = []
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: (existing, True),
    )
    monkeypatch.setattr(
        main,
        "save_analysis_history",
        lambda **kwargs: inserts.append(kwargs),
    )
    monkeypatch.setattr(main, "save_packet_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main,
        "delete_analysis_by_id",
        lambda row_id, user_id: deleted.append((row_id, user_id)) or True,
    )

    response = client.post(
        "/api/portfolio/history",
        json={"filename": "guest.csv", "analysis": analysis},
    )

    assert response.status_code == 503
    assert inserts == []
    assert deleted == []
    assert PACKET_STORE[analysis_id]["user_id"] == ""


def test_webhook_after_history_delete_does_not_resurrect_private_payload(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    analysis_id = "analysis-sample-1"
    private = {
        **SAMPLE_ANALYSIS,
        "analysis_id": analysis_id,
        "lot_match_report": {
            "matched": [{"symbol": "AAPL", "quantity": 10}],
            "gap": [],
            "unmatched": [],
            "matched_count": 1,
            "gap_count": 0,
            "unmatched_count": 0,
        },
    }
    remember_analysis(analysis_id, "test-user-123", private)
    assert PACKET_STORE[analysis_id]["payload"]["lot_match_report"]["matched"]
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: False)
    event = _packet_checkout_event()
    event["data"]["object"]["metadata"]["tax_year"] = "2025"

    response = _post_signed_webhook(event)

    assert response.status_code == 200
    assert response.json()["granted"] is False
    snapshot = _FAKE_PACKET_SNAPSHOTS.get(("test-user-123", analysis_id))
    assert snapshot is None or not (
        isinstance(snapshot.get("packet_payload"), dict)
        and (snapshot["packet_payload"].get("lot_match_report") or {}).get("matched")
    )
    assert PACKET_STORE[analysis_id]["payload"]["lot_match_report"]["matched"] == [
        {"symbol": "AAPL", "quantity": 10}
    ]


def test_webhook_replay_keeps_grant_when_paid_snapshot_history_was_deleted(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    analysis_id = "analysis-sample-1"
    payload = {**SAMPLE_ANALYSIS, "analysis_id": analysis_id}
    paid_at = "2026-01-01T00:00:00+00:00"
    snapshot_client = _YearScopedSnapshotClient([{
        "analysis_id": analysis_id,
        "user_id": "test-user-123",
        "tax_year": 2025,
        "packet_payload": payload,
        "packet_session_id": "cs_test_paid_1",
        "paid_at": paid_at,
        "expires_at": None,
    }])
    monkeypatch.setattr(db, "get_supabase", lambda: snapshot_client)
    monkeypatch.setattr(main, "get_packet_snapshot", db.get_packet_snapshot)
    monkeypatch.setattr(main, "mark_packet_snapshot_paid", db.mark_packet_snapshot_paid)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: False)

    response = _post_signed_webhook(_packet_checkout_event(tax_year="2025"))

    assert response.status_code == 200, response.text
    assert response.json() == {"received": True, "granted": True}
    assert snapshot_client.rows[0]["paid_at"] == paid_at
    assert snapshot_client.rows[0]["packet_session_id"] == "cs_test_paid_1"
    assert snapshot_client.rows[0]["packet_payload"] == payload


def _stub_signed_in_analyze(monkeypatch, *, history_row, snapshot_result):
    saved_history = []
    snapshots = []

    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None, allow_network=True: (
            {symbol.upper(): 100.0 for symbol in symbols},
            [],
        ),
    )
    monkeypatch.setattr(
        "main.fetch_option_prices",
        lambda labels, fb=None, allow_network=True: ({}, []),
    )
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda lots: [])
    monkeypatch.setattr(
        "main.load_activity_book_for_merge",
        lambda *args, **kwargs: db.ActivityBookLookup(),
    )
    monkeypatch.setattr(
        "main.upsert_activity_book",
        lambda *args, **kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        "main.lookup_packet_grant_for_tax_year",
        lambda *args, **kwargs: ("cs_test_yeargrant", True),
    )
    monkeypatch.setattr("main.patch_analysis_result", lambda *args, **kwargs: True)

    def save_history(user_id, filename, summary, result_data=None):
        saved_history.append(result_data)
        return history_row

    def save_snapshot(*args, **kwargs):
        snapshots.append((args, kwargs))
        return snapshot_result

    monkeypatch.setattr(main, "save_analysis_history", save_history)
    monkeypatch.setattr(main, "save_packet_snapshot", save_snapshot)
    return saved_history, snapshots


def test_same_year_grant_snapshot_failure_keeps_private_lots_out_of_history(monkeypatch):
    from auth import get_optional_user

    saved_history, snapshots = _stub_signed_in_analyze(
        monkeypatch,
        history_row={"id": "hist-grant"},
        snapshot_result=None,
    )
    csv_path = Path(__file__).resolve().parent / "fixtures" / "year_close_2024.csv"
    pdf_path = Path(__file__).resolve().parents[2] / "docs" / "c15f7458-e9d5-4dfb-a985-351df5a36cde.pdf"
    monkeypatch.setitem(
        main.app.dependency_overrides,
        get_optional_user,
        lambda: "test-user-123",
    )
    response = client.post(
        "/api/portfolio/analyze?tax_year=2024",
        files={
            "file": ("year_close_2024.csv", csv_path.read_bytes(), "text/csv"),
            "supplemental_1099": (
                pdf_path.name,
                pdf_path.read_bytes(),
                "application/pdf",
            ),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["packet_unlocked"] is False
    assert body["packet_session_id"] is None
    public_report = body["lot_match_report"]
    assert public_report["matched"] == []
    assert public_report["gap"] == []
    assert public_report["unmatched"] == []
    assert (
        public_report["matched_count"]
        + public_report["gap_count"]
        + public_report["unmatched_count"]
        >= 1
    )
    stored_report = PACKET_STORE[body["analysis_id"]]["payload"]["lot_match_report"]
    assert stored_report["matched"] or stored_report["gap"] or stored_report["unmatched"]
    assert saved_history
    saved_report = saved_history[0]["lot_match_report"]
    assert saved_report["matched"] == []
    assert saved_report["gap"] == []
    assert saved_report["unmatched"] == []
    assert not (saved_history[0].get("supplemental_1099") or {}).get("lots")
    assert not (saved_history[0].get("activity_book") or {}).get("transactions")
    assert snapshots

    monkeypatch.setattr(
        main,
        "get_analysis_by_id",
        lambda analysis_id, user_id, client=None: {
            "id": analysis_id,
            "user_id": user_id,
            "result": saved_history[0],
        },
    )
    monkeypatch.setattr(main, "get_supabase", lambda: object())
    loaded = client.get("/api/portfolio/analysis/hist-grant")
    assert loaded.status_code == 200, loaded.text
    loaded_report = loaded.json()["result"]["lot_match_report"]
    assert loaded_report["matched"] == []
    assert loaded_report["gap"] == []
    assert loaded_report["unmatched"] == []
    assert not (loaded.json()["result"].get("supplemental_1099") or {}).get("lots")


def test_paid_snapshot_is_not_saved_when_history_insert_fails(monkeypatch):
    from auth import get_optional_user

    _saved_history, snapshots = _stub_signed_in_analyze(
        monkeypatch,
        history_row=None,
        snapshot_result={"analysis_id": "should-not-save"},
    )
    csv_path = Path(__file__).resolve().parent / "fixtures" / "year_close_2024.csv"
    monkeypatch.setitem(
        main.app.dependency_overrides,
        get_optional_user,
        lambda: "test-user-123",
    )
    response = client.post(
        "/api/portfolio/analyze?tax_year=2024",
        files={
            "file": ("year_close_2024.csv", csv_path.read_bytes(), "text/csv"),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["packet_unlocked"] is False
    assert body["packet_session_id"] is None
    assert body["analysis_id"] not in PACKET_STORE
    assert snapshots == []
    book = body.get("activity_book") or {}
    assert not book.get("transactions")


def test_paid_year_survives_restart_deleted_history_and_newer_rows(monkeypatch):
    """Pay once, then a later 2026 upload on a fresh process stays unlocked.

    The webhook is the only fulfillment path. Restart clears PACKET_STORE.
    History for the paid run is deleted, and any history read sees 21 newer
    rows that do not contain the paid Checkout session.
    """
    import uuid
    from auth import get_optional_user

    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None, allow_network=True: (
            {symbol.upper(): 100.0 for symbol in symbols},
            [],
        ),
    )
    monkeypatch.setattr(
        "main.fetch_option_prices",
        lambda labels, fb=None, allow_network=True: ({}, []),
    )
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda lots: [])
    monkeypatch.setattr(
        "main.load_activity_book_for_merge",
        lambda *args, **kwargs: db.ActivityBookLookup(),
    )
    monkeypatch.setattr(
        "main.upsert_activity_book",
        lambda *args, **kwargs: {"ok": True},
    )

    inserted_rows = []

    class _HistoryInsert:
        def __init__(self):
            self.row = None
            self.op = None

        def table(self, name):
            assert name == "portfolio_analyses"
            self.op = None
            self.row = None
            return self

        def insert(self, row):
            self.op = "insert"
            self.row = row
            return self

        def select(self, *_args):
            if self.op != "insert":
                self.op = "select"
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def execute(self):
            if self.op != "insert":
                return SimpleNamespace(data=[])
            inserted_rows.append(self.row)
            return SimpleNamespace(data=[{"id": self.row.get("id")}])

    def save_history(user_id, filename, summary, result_data=None):
        previous = db.get_supabase
        monkeypatch.setattr(db, "get_supabase", lambda: _HistoryInsert())
        try:
            return db.save_analysis_history(
                user_id,
                filename,
                summary,
                result_data=result_data,
            )
        finally:
            monkeypatch.setattr(db, "get_supabase", previous)

    def save_entitlement(analysis_id, user_id, tax_year, session_id, client=None, **_kwargs):
        key = (user_id, int(tax_year), session_id)
        existing = _FAKE_PACKET_ENTITLEMENTS.get(key)
        if existing:
            return dict(existing)
        row = {
            "analysis_id": analysis_id,
            "user_id": user_id,
            "tax_year": int(tax_year),
            "packet_session_id": session_id,
        }
        _FAKE_PACKET_ENTITLEMENTS[key] = row
        return dict(row)

    def lookup_grant(user_id, tax_year, client=None):
        for (owner, _analysis_id), row in _FAKE_PACKET_SNAPSHOTS.items():
            if owner != user_id or int(row.get("tax_year") or -1) != int(tax_year):
                continue
            session_id = row.get("packet_session_id")
            if (
                row.get("paid_at")
                and isinstance(session_id, str)
                and session_id.startswith("cs_")
            ):
                return session_id, True
        return None, True

    monkeypatch.setattr(main, "save_analysis_history", save_history)
    monkeypatch.setattr(main, "save_packet_entitlement", save_entitlement)
    monkeypatch.setattr(main, "lookup_packet_grant_for_tax_year", lookup_grant)

    header = (
        "Activity Date,Process Date,Settle Date,Instrument,Description,"
        "Trans Code,Quantity,Price,Amount\n"
    )
    first_csv = (
        header
        + "01/01/2026,01/01/2026,01/03/2026,AAPL,Apple,Buy,2,180.00,-360.00\n"
    ).encode()
    second_csv = (
        header
        + "02/02/2026,02/02/2026,02/04/2026,MSFT,Microsoft,Buy,1,400.00,-400.00\n"
    ).encode()
    monkeypatch.setitem(
        main.app.dependency_overrides,
        get_optional_user,
        lambda: "test-user-123",
    )
    first_response = client.post(
        "/api/portfolio/analyze?tax_year=2026",
        files={"file": ("ytd-2026.csv", first_csv, "text/csv")},
    )

    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    first_id = first["analysis_id"]
    uuid.UUID(first_id)
    assert inserted_rows[0]["id"] == first_id
    assert inserted_rows[0]["result"]["analysis_id"] == first_id

    event = _packet_checkout_event()
    event["data"]["object"]["id"] = "cs_paid_restart"
    event["data"]["object"]["metadata"]["analysis_id"] = first_id
    event["data"]["object"]["metadata"]["user_id"] = "test-user-123"
    event["data"]["object"]["metadata"]["tax_year"] = "2026"
    webhook = _post_signed_webhook(event)
    assert webhook.status_code == 200, webhook.text
    assert webhook.json()["granted"] is True
    entitlement_key = ("test-user-123", 2026, "cs_paid_restart")
    assert list(_FAKE_PACKET_ENTITLEMENTS) == [entitlement_key]
    assert _FAKE_PACKET_ENTITLEMENTS[entitlement_key]["analysis_id"] == first_id

    reset_packet_store()
    deleted = []
    packet_lookup = main.lookup_analysis_for_entitlement
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda analysis_id, user_id: (
            (
                {
                    "id": analysis_id,
                    "user_id": user_id,
                    "result": {"analysis_id": analysis_id},
                },
                True,
            )
            if analysis_id == first_id
            else (None, True)
        ),
    )
    monkeypatch.setattr(
        main,
        "delete_analysis_by_id",
        lambda row_id, user_id: deleted.append((row_id, user_id)) or True,
    )
    removed = client.delete(f"/api/portfolio/analysis/{first_id}")
    assert removed.status_code == 200, removed.text
    assert deleted == [(first_id, "test-user-123")]
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", packet_lookup)
    for row in _FAKE_PACKET_SNAPSHOTS.values():
        if row.get("paid_at") and row.get("analysis_id") == first_id:
            row["packet_payload"] = None
            assert row["packet_session_id"] == "cs_paid_restart"

    history_reads = []

    def get_history(user_id, limit=20, client=None):
        rows = [
            {
                "id": f"newer-{index}",
                "user_id": user_id,
                "summary": {},
                "result": {
                    "analysis_id": f"newer-{index}",
                    "packet_unlocked": True,
                    "packet_session_id": f"cs_decoy_{index}",
                    "tax_profile": {"tax_year": 2026},
                },
            }
            for index in range(21)
        ]
        history_reads.append({"user_id": user_id, "limit": limit, "rows": rows})
        return rows

    monkeypatch.setattr(main, "get_analysis_history", get_history)
    monkeypatch.setattr(db, "get_analysis_history", get_history)

    second_response = client.post(
        "/api/portfolio/analyze?tax_year=2026",
        files={"file": ("later-2026.csv", second_csv, "text/csv")},
    )

    assert second_response.status_code == 200, second_response.text
    second = second_response.json()
    second_id = second["analysis_id"]
    assert second_id != first_id
    assert second["packet_unlocked"] is True
    assert second["packet_session_id"] == "cs_paid_restart"
    assert inserted_rows[1]["id"] == second_id
    for read in history_reads:
        assert len(read["rows"]) == 21
        sessions = {
            (row.get("result") or {}).get("packet_session_id") for row in read["rows"]
        }
        assert "cs_paid_restart" not in sessions
        assert second["packet_session_id"] not in sessions

    created = []

    def create(*_args, **kwargs):
        created.append(kwargs)
        raise AssertionError("stripe.checkout.Session.create must not run")

    monkeypatch.setattr(main.stripe.checkout.Session, "create", create)
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: _stripe_object_session(
            id="cs_paid_restart",
            metadata={
                "product": PACKET_METADATA_PRODUCT,
                "analysis_id": first_id,
                "user_id": "test-user-123",
                "tax_year": "2026",
            },
        ),
    )
    checkout = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": second_id, "analysis": second},
    )
    assert checkout.status_code == 200, checkout.text
    assert checkout.json()["already_paid"] is True
    assert checkout.json()["session_id"] == "cs_paid_restart"
    assert created == []
    assert _FAKE_PACKET_ENTITLEMENTS[entitlement_key]["analysis_id"] == first_id


_FOLLOWUP_UUID = "abababab-abab-4aba-8aba-abababababab"


def _marker_pdf(monkeypatch):
    monkeypatch.setattr(
        main,
        "render_packet_pdf",
        lambda payload: f"YEAR:{payload.get('marker')}".encode(),
    )


def _snapshot_row(analysis_id, user, year, payload, *, session_id, paid_at, expires_at=None):
    return {
        "analysis_id": analysis_id,
        "user_id": user,
        "tax_year": year,
        "packet_payload": payload,
        "packet_session_id": session_id,
        "paid_at": paid_at,
        "expires_at": expires_at,
    }


def test_persist_non_string_analysis_id_is_400():
    response = client.post(
        "/api/portfolio/history",
        json={
            "filename": "guest.csv",
            "analysis": {"analysis_id": 12345, "positions": []},
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "analysis_id is required"


def test_ensure_skips_recase_when_canonical_history_row_exists(monkeypatch):
    canonical = _FOLLOWUP_UUID
    user = "test-user-123"
    upper_row = {
        "id": "row-a",
        "user_id": user,
        "result": {
            "analysis_id": canonical.upper(),
            "analysis_tax_year": 2025,
            "tax_profile": {"tax_year": 2025, "filing_status": "single"},
        },
    }
    patches = []

    def lookup(_analysis_id, _user_id):
        return upper_row, True

    def patch(_analysis_id, _user_id, body):
        patches.append(dict(body))
        upper_row["result"].update(body)
        return True

    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lookup)
    monkeypatch.setattr(main, "patch_analysis_result", patch)
    monkeypatch.setattr(main, "canonical_history_embedded_on_other_row", lambda *_args: True)

    assert main._ensure_packet_history_row(canonical, user, 2025) is True
    assert patches == []
    assert upper_row["result"]["analysis_id"] == canonical.upper()

    monkeypatch.setattr(main, "canonical_history_embedded_on_other_row", lambda *_args: False)
    assert main._ensure_packet_history_row(canonical, user, 2025) is True
    assert patches == [{"analysis_id": canonical}]
    assert upper_row["result"]["analysis_id"] == canonical


class _HistoryEmbedClient:
    def __init__(self, eq_data, ilike_data):
        self.eq_data = eq_data
        self.ilike_data = ilike_data

    def table(self, _name):
        return _HistoryEmbedQuery(self)


class _HistoryEmbedQuery:
    def __init__(self, owner):
        self.owner = owner
        self.operator = "eq"

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def filter(self, _column, operator, _value):
        self.operator = operator
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        data = self.owner.ilike_data if self.operator == "ilike" else self.owner.eq_data
        return SimpleNamespace(data=data)


def test_canonical_history_ilike_fallback_skips_recase(monkeypatch):
    canonical = _FOLLOWUP_UUID
    user = "test-user-123"
    upper_row = {
        "id": "row-upper",
        "user_id": user,
        "result": {
            "analysis_id": canonical.upper(),
            "analysis_tax_year": 2025,
            "tax_profile": {"tax_year": 2025, "filing_status": "single"},
        },
    }
    other = {
        "id": "row-other",
        "user_id": user,
        "result": {"analysis_id": canonical.upper()},
    }
    patches = []
    monkeypatch.setattr(db, "get_supabase", lambda: _HistoryEmbedClient([], [other]))
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_a: (upper_row, True))
    monkeypatch.setattr(
        main,
        "patch_analysis_result",
        lambda *_a: patches.append(True) or True,
    )
    assert db.canonical_history_embedded_on_other_row(user, canonical, "row-upper") is True
    assert main._ensure_packet_history_row(canonical, user, 2025) is True
    assert patches == []
    assert upper_row["result"]["analysis_id"] == canonical.upper()


def test_canonical_history_non_list_payload_does_not_recase(monkeypatch):
    canonical = _FOLLOWUP_UUID
    user = "test-user-123"
    upper_row = {
        "id": "row-upper",
        "user_id": user,
        "result": {
            "analysis_id": canonical.upper(),
            "analysis_tax_year": 2025,
            "tax_profile": {"tax_year": 2025, "filing_status": "single"},
        },
    }
    patches = []
    monkeypatch.setattr(db, "get_supabase", lambda: _HistoryEmbedClient(None, []))
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_a: (upper_row, True))
    monkeypatch.setattr(
        main,
        "patch_analysis_result",
        lambda *_a: patches.append(True) or True,
    )
    assert db.canonical_history_embedded_on_other_row(user, canonical, "row-upper") is None
    assert main._ensure_packet_history_row(canonical, user, 2025) is None
    assert patches == []
    assert upper_row["result"]["analysis_id"] == canonical.upper()


def test_checkout_year_conflict_does_not_relabel_history(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    aid = "analysis-history-stays-2025"
    payload = {
        "analysis_id": aid,
        "analysis_tax_year": 2025,
        "tax_profile": {"tax_year": 2025, "filing_status": "single"},
        "marker": "pdf-2025",
    }
    remember_analysis(aid, user, payload)
    _wire_real_snapshot_client(
        monkeypatch,
        [_snapshot_row(
            aid, user, 2025, payload, session_id="cs_A", paid_at="2026-01-01T00:00:00+00:00",
        )],
    )
    _marker_pdf(monkeypatch)
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs),
    )
    history_patches = []

    def tracking_patch(analysis_id, user_id, body):
        history_patches.append(body)
        return True

    monkeypatch.setattr(main, "patch_analysis_result", tracking_patch)

    first = client.get("/api/year-close-packet/download", params={"analysis_id": aid})
    assert first.status_code == 200, first.text
    assert b"pdf-2025" in first.content

    checkout = client.post(
        "/api/year-close-packet/checkout",
        json={
            "analysis_id": aid,
            "analysis": {
                **payload,
                "analysis_tax_year": 2026,
                "tax_profile": {"tax_year": 2026, "filing_status": "single"},
            },
        },
    )
    assert checkout.status_code == 409, checkout.text
    assert checkout.json()["detail"] == _PAID_OTHER_YEAR_DETAIL
    assert created == []
    assert history_patches == []
    stored = PACKET_STORE[aid]["payload"]
    assert stored["analysis_tax_year"] == 2025

    second = client.get("/api/year-close-packet/download", params={"analysis_id": aid})
    assert second.status_code == 200, second.text
    assert b"pdf-2025" in second.content


def test_checkout_scan_outage_is_503_and_does_not_open_stripe(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    aid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": aid}
    remember_analysis(aid, user, analysis)

    class _ScanOutageClient:
        def __init__(self):
            self.inserts = []
            self.selects = 0

        def rpc(self, *_args, **_kwargs):
            raise RuntimeError("cleanup unavailable")

        def table(self, _name):
            return _ScanOutageQuery(self)

    class _ScanOutageQuery:
        def __init__(self, owner):
            self.owner = owner
            self.filters = []
            self.op = "select"

        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def order(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def is_(self, *_args, **_kwargs):
            return self

        def filter(self, _column, operator, _value):
            self.filters.append(operator)
            return self

        def insert(self, row):
            self.op = "insert"
            self.owner.inserts.append(row)
            return self

        def execute(self):
            if self.op == "insert":
                return SimpleNamespace(data=[{"analysis_id": aid}])
            self.owner.selects += 1
            if "ilike" in self.filters:
                raise RuntimeError("paid-other-spelling scan down")
            return SimpleNamespace(data=[])

    outage = _ScanOutageClient()
    monkeypatch.setattr(db, "get_supabase", lambda: outage)
    # The year is already known, so the preliminary snapshot read is the autouse
    # miss. The legacy ilike that fails is the unscoped save scan.
    monkeypatch.setattr(main, "save_packet_snapshot", db.save_packet_snapshot)
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs) or (_ for _ in ()).throw(AssertionError("stripe")),
    )
    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": aid, "analysis": analysis},
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == (
        "Could not securely save the packet before checkout. Please retry."
    )
    assert created == []
    assert outage.inserts == []
    assert response.json().get("already_paid") is not True
    assert outage.selects >= 1
    assert PACKET_STORE[aid]["paid"] is not True


def test_same_spelling_two_entitlement_years_stay_ambiguous(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    canonical = _FOLLOWUP_UUID
    snapshot_client = _wire_real_snapshot_client(monkeypatch, [])
    snapshot_client.entitlements.extend([
        {
            "analysis_id": canonical,
            "user_id": user,
            "tax_year": 2024,
            "packet_session_id": "cs_x",
        },
        {
            "analysis_id": canonical,
            "user_id": user,
            "tax_year": 2025,
            "packet_session_id": "cs_y",
        },
    ])
    monkeypatch.setattr(main, "lookup_packet_entitlements_for_analysis", db.lookup_packet_entitlements_for_analysis)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_a, **_k: (None, True))
    rows, ok = db.lookup_packet_entitlements_for_analysis(user, canonical, client=snapshot_client)
    assert ok is True
    assert {row["packet_session_id"] for row in rows} == {"cs_x", "cs_y"}
    assert {int(row["tax_year"]) for row in rows} == {2024, 2025}
    assert main._resolve_packet_tax_year_for_identity(canonical, user) == (None, True)
    created = []
    monkeypatch.setattr(main.stripe.checkout.Session, "create", lambda **kwargs: created.append(kwargs))
    checkout = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": canonical, "analysis": {"analysis_id": canonical, "summary": {}}},
    )
    assert checkout.status_code == 409, checkout.text
    assert checkout.json()["detail"] == main.PACKET_YEAR_AMBIGUOUS_DETAIL
    assert created == []


def test_checkout_history_mismatch_does_not_reyear_unpaid_snapshot(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    aid = "analysis-draft-stays-2025"
    payload = {
        "analysis_id": aid,
        "analysis_tax_year": 2025,
        "tax_profile": {"tax_year": 2025, "filing_status": "single"},
        "marker": "draft-2025",
    }
    remember_analysis(aid, user, payload)
    # remember_analysis folds tax_profile.tax_year into analysis_tax_year and
    # drops the profile. Checkout history is the stored payload shape.
    PACKET_STORE[aid]["payload"] = payload
    snapshot_client = _wire_real_snapshot_client(
        monkeypatch,
        [_snapshot_row(
            aid, user, 2025, payload,
            session_id=None, paid_at=None, expires_at="2099-01-01T00:00:00+00:00",
        )],
    )
    created = []
    monkeypatch.setattr(main.stripe.checkout.Session, "create", lambda **kwargs: created.append(kwargs))
    checkout = client.post(
        "/api/year-close-packet/checkout",
        json={
            "analysis_id": aid,
            "analysis": {
                **payload,
                "analysis_tax_year": 2026,
                "tax_profile": {"tax_year": 2026, "filing_status": "single"},
            },
        },
    )
    assert checkout.status_code == 409, checkout.text
    assert checkout.json()["detail"] == main.PACKET_HISTORY_YEAR_MISMATCH_DETAIL
    assert checkout.json()["detail"] != main.PACKET_PAID_OTHER_YEAR_DETAIL
    assert created == []
    assert snapshot_client.rows[0]["tax_year"] == 2025
    loaded, loaded_ok = db.get_packet_snapshot(aid, user, tax_year=2025, client=snapshot_client)
    assert loaded_ok is True
    assert loaded["packet_payload"]["marker"] == "draft-2025"
    stored = PACKET_STORE[aid]["payload"]
    assert stored["analysis_tax_year"] == 2025
    assert stored["tax_profile"]["tax_year"] == 2025


def test_paid_same_year_profile_mismatch_is_already_paid(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    aid = "analysis-paid-profile-mismatch"
    payload = {
        "analysis_id": aid,
        "analysis_tax_year": 2025,
        "tax_profile": {"tax_year": 2024, "filing_status": "single"},
        "marker": "paid-2025",
    }
    remember_analysis(aid, user, payload)
    # Same fold: keep analysis_tax_year 2025 and tax_profile.tax_year 2024
    # as separate stored fields so only the profile disagrees.
    PACKET_STORE[aid]["payload"] = payload
    _wire_real_snapshot_client(
        monkeypatch,
        [_snapshot_row(
            aid, user, 2025, payload,
            session_id="cs_paid", paid_at="2026-01-01T00:00:00+00:00",
        )],
    )
    monkeypatch.setattr(
        main,
        "lookup_packet_grant_for_tax_year",
        lambda *_args, **_kwargs: ("cs_paid", True),
    )
    created = []
    monkeypatch.setattr(main.stripe.checkout.Session, "create", lambda **kwargs: created.append(kwargs))
    checkout = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": aid, "analysis": payload},
    )
    assert checkout.status_code == 200, checkout.text
    assert checkout.json()["already_paid"] is True
    assert checkout.json()["session_id"] == "cs_paid"
    assert checkout.json().get("detail") != main.PACKET_PAID_OTHER_YEAR_DETAIL
    assert created == []
    assert PACKET_STORE[aid]["payload"]["tax_profile"]["tax_year"] == 2024
    assert PACKET_STORE[aid]["payload"]["analysis_tax_year"] == 2025


def test_conflict_prefix_analysis_id_is_400(monkeypatch):
    _test_stripe_env(monkeypatch)
    reserved = f"conflict:{_FOLLOWUP_UUID}"
    created = []
    monkeypatch.setattr(main.stripe.checkout.Session, "create", lambda **kwargs: created.append(kwargs))
    history = client.post(
        "/api/portfolio/history",
        json={"filename": "guest.csv", "analysis": {"analysis_id": reserved, "positions": []}},
    )
    assert history.status_code == 400, history.text
    assert history.json()["detail"] == "analysis_id is reserved"
    checkout = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": reserved, "analysis": {"analysis_id": reserved, "summary": {}}},
    )
    assert checkout.status_code == 400, checkout.text
    assert checkout.json()["detail"] == "analysis_id is reserved"
    assert created == []
    assert _FAKE_PACKET_SNAPSHOTS == {}
    fetched = client.get(f"/api/portfolio/analysis/{reserved}")
    deleted = client.delete(f"/api/portfolio/analysis/{reserved}")
    downloaded = client.get("/api/year-close-packet/download", params={"analysis_id": reserved})
    assert fetched.status_code == 400 and fetched.json()["detail"] == "analysis_id is reserved"
    assert deleted.status_code == 400 and deleted.json()["detail"] == "analysis_id is reserved"
    assert downloaded.status_code == 400 and downloaded.json()["detail"] == "analysis_id is reserved"


def test_existing_conflict_receipt_stays_conflict_after_year_clears(monkeypatch, caplog):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    canonical = _FOLLOWUP_UUID
    snapshot_client = _wire_real_snapshot_client(
        monkeypatch,
        [_snapshot_row(
            canonical, user, 2025,
            {"marker": "unpaid", "analysis_tax_year": 2025},
            session_id=None, paid_at=None, expires_at="2099-01-01T00:00:00+00:00",
        )],
    )
    snapshot_client.entitlements.append({
        "analysis_id": f"conflict:{canonical}",
        "user_id": user,
        "tax_year": 2025,
        "packet_session_id": "cs_charged",
    })
    monkeypatch.setattr(main, "lookup_packet_session_entitlement", db.lookup_packet_session_entitlement)
    monkeypatch.setattr(main, "save_packet_entitlement", db.save_packet_entitlement)
    session = _stripe_object_session(
        id="cs_charged",
        metadata={
            "analysis_id": canonical,
            "user_id": user,
            "tax_year": "2025",
        },
    )
    with caplog.at_level(logging.ERROR):
        granted = main._persist_packet_grant(session, canonical, user)
    assert granted == main.PACKET_GRANT_CONFLICT_RECEIPT
    assert snapshot_client.rows[0]["paid_at"] is None
    assert len(snapshot_client.entitlements) == 1
    assert snapshot_client.entitlements[0]["analysis_id"] == f"conflict:{canonical}"
    looked, looked_ok = db.lookup_packet_entitlement_for_tax_year(user, 2025, client=snapshot_client)
    assert looked_ok is True and looked is None
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_history_not_ready_grant_scans_paid_other_year(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    canonical = _FOLLOWUP_UUID
    snapshot_client = _wire_real_snapshot_client(
        monkeypatch,
        [
            _snapshot_row(
                canonical, user, 2026,
                {"marker": "draft-2026", "analysis_tax_year": 2026},
                session_id=None, paid_at=None, expires_at="2099-01-01T00:00:00+00:00",
            ),
            _snapshot_row(
                canonical.upper(), user, 2025,
                {"marker": "paid-2025", "analysis_tax_year": 2025},
                session_id="cs_paid", paid_at="2026-01-01T00:00:00+00:00",
            ),
        ],
    )
    monkeypatch.setattr(main, "_ensure_packet_history_row", lambda *_a, **_k: False)
    monkeypatch.setattr(main, "save_packet_entitlement", db.save_packet_entitlement)
    session = _stripe_object_session(
        id="cs_charged_2026",
        metadata={"analysis_id": canonical, "user_id": user, "tax_year": "2026"},
    )
    granted = main._persist_packet_grant(session, canonical, user)
    assert granted == main.PACKET_GRANT_YEAR_CONFLICT
    assert snapshot_client.rows[0]["paid_at"] is None
    assert snapshot_client.rows[1]["paid_at"] == "2026-01-01T00:00:00+00:00"
    assert snapshot_client.rows[1]["packet_session_id"] == "cs_paid"
    receipts = [
        row for row in snapshot_client.entitlements
        if str(row.get("analysis_id") or "").startswith("conflict:")
    ]
    assert len(receipts) == 1
    assert receipts[0]["packet_session_id"] == "cs_charged_2026"
    assert int(receipts[0]["tax_year"]) == 2026


def test_download_two_paid_spellings_without_session_is_503(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    canonical = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", _no_history_lookup)
    _marker_pdf(monkeypatch)
    _wire_real_snapshot_client(
        monkeypatch,
        [
            _snapshot_row(
                canonical.upper(), user, 2024,
                {"marker": "YEAR:2024", "analysis_tax_year": 2024},
                session_id="cs_2024", paid_at="2026-01-01T00:00:00+00:00",
            ),
            _snapshot_row(
                canonical, user, 2025,
                {"marker": "YEAR:2025", "analysis_tax_year": 2025},
                session_id="cs_2025", paid_at="2026-02-01T00:00:00+00:00",
            ),
        ],
    )
    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": canonical},
    )
    assert response.status_code == 503, response.text
    assert not response.content.startswith(b"%PDF")
    assert b"YEAR:" not in response.content


def test_both_spellings_same_year_download_checkout_confirm_mark(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    user = "test-user-123"
    canonical = _FOLLOWUP_UUID
    paid_payload = {"analysis_id": canonical.upper(), "marker": "PAID", "analysis_tax_year": 2025}
    unpaid_payload = {"analysis_id": canonical, "marker": "UNPAID", "analysis_tax_year": 2025}
    snapshot_client = _wire_real_snapshot_client(
        monkeypatch,
        [
            _snapshot_row(
                canonical, user, 2025, unpaid_payload,
                session_id=None, paid_at=None, expires_at="2099-01-01T00:00:00+00:00",
            ),
            _snapshot_row(
                canonical.upper(), user, 2025, paid_payload,
                session_id="cs_test_paid_1", paid_at="2026-01-01T00:00:00+00:00",
            ),
        ],
    )
    remember_analysis(canonical, user, {**SAMPLE_ANALYSIS, "analysis_id": canonical, "analysis_tax_year": 2025, "tax_profile": {"tax_year": 2025}})
    _marker_pdf(monkeypatch)

    def grant(user_id, tax_year, client=None):
        for row in snapshot_client.rows:
            if row.get("paid_at") and int(row["tax_year"]) == int(tax_year):
                return row.get("packet_session_id"), True
        return None, True

    monkeypatch.setattr(main, "lookup_packet_grant_for_tax_year", grant)
    created = []
    monkeypatch.setattr(main.stripe.checkout.Session, "create", lambda **kwargs: created.append(kwargs))
    session = _stripe_object_session(
        id="cs_test_paid_1",
        metadata={"analysis_id": canonical.upper(), "user_id": user, "tax_year": "2025"},
    )
    monkeypatch.setattr(main.stripe.checkout.Session, "retrieve", lambda *_a, **_k: session)

    downloaded = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": canonical, "session_id": "cs_test_paid_1"},
    )
    assert downloaded.status_code == 200, downloaded.text
    assert b"PAID" in downloaded.content
    assert b"UNPAID" not in downloaded.content

    checkout = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": canonical, "analysis": {**SAMPLE_ANALYSIS, "analysis_id": canonical, "analysis_tax_year": 2025, "tax_profile": {"tax_year": 2025}}},
    )
    assert checkout.status_code == 200, checkout.text
    assert checkout.json()["already_paid"] is True
    assert created == []
    assert snapshot_client.rows[0]["paid_at"] is None
    paid_at_before = snapshot_client.rows[1]["paid_at"]
    session_before = snapshot_client.rows[1]["packet_session_id"]

    webhook = _post_signed_webhook(
        _packet_checkout_event(
            analysis_id=canonical.upper(),
            tax_year="2025",
            event_id="evt_same_year_replay",
        )
    )
    assert webhook.status_code == 200, webhook.text
    assert webhook.json()["granted"] is True
    assert snapshot_client.rows[0]["analysis_id"] == canonical
    assert snapshot_client.rows[0]["paid_at"] is None
    assert snapshot_client.rows[1]["paid_at"] == paid_at_before
    assert snapshot_client.rows[1]["packet_session_id"] == session_before

    confirmed = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": "cs_test_paid_1", "analysis_id": canonical},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert snapshot_client.rows[0]["paid_at"] is None
    assert snapshot_client.rows[1]["paid_at"] == paid_at_before
    assert snapshot_client.rows[1]["packet_session_id"] == session_before
    assert db.mark_packet_snapshot_paid(canonical, user, 2025, "cs_test_paid_1", client=snapshot_client) is True
    assert snapshot_client.rows[0]["paid_at"] is None
    assert snapshot_client.rows[1]["paid_at"] == paid_at_before
    assert snapshot_client.rows[1]["packet_session_id"] == session_before


def test_different_session_same_year_mark_keeps_unpaid_sibling_and_one_conflict_receipt(monkeypatch, caplog):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    user = "test-user-123"
    canonical = _FOLLOWUP_UUID
    snapshot_client = _wire_real_snapshot_client(
        monkeypatch,
        [
            _snapshot_row(
                canonical, user, 2025,
                {"marker": "UNPAID", "analysis_tax_year": 2025},
                session_id=None, paid_at=None, expires_at="2099-01-01T00:00:00+00:00",
            ),
            _snapshot_row(
                canonical.upper(), user, 2025,
                {"marker": "PAID", "analysis_tax_year": 2025},
                session_id="cs_test_paid_1", paid_at="2026-01-01T00:00:00+00:00",
            ),
        ],
    )
    monkeypatch.setattr(main, "save_packet_entitlement", db.save_packet_entitlement)
    monkeypatch.setattr(main, "lookup_packet_grant_for_tax_year", lambda *_a, **_k: (None, True))
    remember_analysis(
        canonical, user,
        {**SAMPLE_ANALYSIS, "analysis_id": canonical, "analysis_tax_year": 2025, "tax_profile": {"tax_year": 2025}},
    )
    session = _stripe_object_session(
        id="cs_other",
        metadata={"analysis_id": canonical, "user_id": user, "tax_year": "2025"},
    )
    monkeypatch.setattr(main.stripe.checkout.Session, "retrieve", lambda *_a, **_k: session)
    with caplog.at_level(logging.ERROR):
        webhook = _post_signed_webhook(
            _packet_checkout_event(
                analysis_id=canonical,
                tax_year="2025",
                event_id="evt_same_year_other_session",
                session_id="cs_other",
            )
        )
        confirm = client.post(
            "/api/year-close-packet/confirm",
            json={"session_id": "cs_other", "analysis_id": canonical},
        )
    assert webhook.status_code == 200, webhook.text
    assert webhook.json()["granted"] is False
    assert confirm.status_code == 409, confirm.text
    assert "different tax year" not in confirm.text
    receipts = [
        row for row in snapshot_client.entitlements
        if str(row.get("analysis_id") or "").startswith("conflict:")
    ]
    assert len(receipts) == 1
    assert receipts[0]["packet_session_id"] == "cs_other"
    assert snapshot_client.rows[0]["paid_at"] is None
    assert snapshot_client.rows[1]["paid_at"] == "2026-01-01T00:00:00+00:00"
    assert snapshot_client.rows[1]["packet_session_id"] == "cs_test_paid_1"
    error_lines = [
        record.message for record in caplog.records if record.levelno >= logging.ERROR
    ]
    assert len(error_lines) == 1
    assert "PACKET_GRANT_SAME_YEAR_DUPLICATE" in error_lines[0]
    assert "cs_other" in error_lines[0]


def test_both_spellings_cross_year_download_checkout_webhook_mark(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    user = "test-user-123"
    canonical = _FOLLOWUP_UUID
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", _no_history_lookup)
    _marker_pdf(monkeypatch)
    snapshot_client = _wire_real_snapshot_client(
        monkeypatch,
        [
            _snapshot_row(
                canonical, user, 2025,
                {"marker": "Y2025", "analysis_tax_year": 2025},
                session_id="cs_a", paid_at="2026-01-01T00:00:00+00:00",
            ),
            _snapshot_row(
                canonical.upper(), user, 2026,
                {"marker": "Y2026", "analysis_tax_year": 2026},
                session_id="cs_b", paid_at="2026-02-01T00:00:00+00:00",
            ),
        ],
    )
    created = []
    monkeypatch.setattr(main.stripe.checkout.Session, "create", lambda **kwargs: created.append(kwargs))

    def retrieve(session_id, **_kwargs):
        year = "2025" if session_id == "cs_a" else "2026"
        return _stripe_object_session(
            id=session_id,
            metadata={"analysis_id": canonical, "user_id": user, "tax_year": year},
        )

    monkeypatch.setattr(main.stripe.checkout.Session, "retrieve", retrieve)
    checkout = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": canonical, "analysis": {"analysis_id": canonical, "summary": {}}},
    )
    assert checkout.status_code == 409, checkout.text
    assert checkout.json()["detail"] == main.PACKET_YEAR_AMBIGUOUS_DETAIL
    assert created == []

    got_a = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": canonical, "session_id": "cs_a"},
    )
    got_b = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": canonical.upper(), "session_id": "cs_b"},
    )
    assert got_a.status_code == 200 and b"Y2025" in got_a.content
    assert got_b.status_code == 200 and b"Y2026" in got_b.content

    before = [dict(row) for row in snapshot_client.rows]
    webhook = _post_signed_webhook(
        _packet_checkout_event(
            analysis_id=canonical.upper(),
            tax_year="2027",
            event_id="evt_third_year",
        )
    )
    assert webhook.status_code == 200, webhook.text
    assert webhook.json()["granted"] is False
    assert snapshot_client.rows == before
    receipt = _FAKE_PACKET_ENTITLEMENTS[(user, 2027, "cs_test_paid_1")]
    assert receipt["analysis_id"] == f"conflict:{canonical}"
    with pytest.raises(db.PacketSnapshotYearConflict):
        db.mark_packet_snapshot_paid(canonical, user, 2027, "cs_new", client=snapshot_client)
    assert snapshot_client.rows == before


def test_checkout_upper_paid_blocks_canonical_other_year(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    canonical = _FOLLOWUP_UUID
    _wire_real_snapshot_client(
        monkeypatch,
        [
            _snapshot_row(
                canonical, user, 2026,
                {"marker": "unpaid-2026", "analysis_tax_year": 2026},
                session_id=None, paid_at=None, expires_at="2099-01-01T00:00:00+00:00",
            ),
            _snapshot_row(
                canonical.upper(), user, 2025,
                {"marker": "paid-2025", "analysis_tax_year": 2025},
                session_id="cs_A", paid_at="2026-01-01T00:00:00+00:00",
            ),
        ],
    )
    created = []
    monkeypatch.setattr(main.stripe.checkout.Session, "create", lambda **kwargs: created.append(kwargs))
    year_2026 = {
        **SAMPLE_ANALYSIS,
        "analysis_id": canonical,
        "analysis_tax_year": 2026,
        "tax_profile": {"tax_year": 2026, "filing_status": "single"},
    }
    remember_analysis(canonical, user, year_2026)
    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": canonical, "analysis": year_2026},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == _PAID_OTHER_YEAR_DETAIL
    assert created == []


def test_webhook_conflict_receipt_insert_failure_is_500(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    user = "test-user-123"
    aid = "analysis-sample-1"
    payload = {"analysis_tax_year": 2025, "marker": "paid-x"}
    _FAKE_PACKET_SNAPSHOTS[(user, aid)] = {
        "analysis_id": aid,
        "user_id": user,
        "tax_year": 2025,
        "packet_payload": payload,
        "packet_session_id": "cs_paid_x",
        "paid_at": "2026-01-01T00:00:00+00:00",
    }
    remember_analysis(aid, user, {**SAMPLE_ANALYSIS, "analysis_id": aid})
    monkeypatch.setattr(main, "save_packet_entitlement", lambda *_args, **_kwargs: None)
    webhook = _post_signed_webhook(
        _packet_checkout_event(tax_year=2026, event_id="evt_receipt_fail")
    )
    assert webhook.status_code == 500, webhook.text
    assert webhook.json()["detail"] == (
        "Unable to persist packet payment; Stripe may retry this event."
    )
    assert webhook.json().get("granted") is not False or "granted" not in webhook.json()
    assert _FAKE_PACKET_SNAPSHOTS[(user, aid)]["packet_payload"] == payload
    assert _FAKE_PACKET_SNAPSHOTS[(user, aid)]["paid_at"] == "2026-01-01T00:00:00+00:00"


def test_conflict_receipt_never_unlocks_download_or_shows_paid(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    canonical = _FOLLOWUP_UUID
    store = _YearScopedSnapshotClient([])
    store.entitlements.append({
        "analysis_id": f"conflict:{canonical}",
        "user_id": user,
        "tax_year": 2025,
        "packet_session_id": "cs_charged",
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    monkeypatch.setattr(db, "get_supabase", lambda: store)
    monkeypatch.setattr(main, "lookup_packet_entitlement_for_tax_year", db.lookup_packet_entitlement_for_tax_year)
    monkeypatch.setattr(main, "lookup_packet_entitlements_for_analysis", db.lookup_packet_entitlements_for_analysis)
    monkeypatch.setattr(main, "list_packet_snapshots_for_identity", db.list_packet_snapshots_for_identity)
    monkeypatch.setattr(main, "get_packet_snapshot", db.get_packet_snapshot)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", _no_history_lookup)
    assert db.lookup_packet_entitlement_for_tax_year(user, 2025, client=store) == (None, True)
    assert db.lookup_packet_entitlements_for_analysis(user, canonical, client=store) == ([], True)
    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": canonical, "session_id": "cs_charged"},
    )
    assert response.status_code in (403, 503)
    assert not response.content.startswith(b"%PDF")
    created = []
    monkeypatch.setattr(main.stripe.checkout.Session, "create", lambda **kwargs: created.append(kwargs))
    checkout = client.post(
        "/api/year-close-packet/checkout",
        json={
            "analysis_id": canonical,
            "analysis": {
                "analysis_id": canonical,
                "analysis_tax_year": 2025,
                "tax_profile": {"tax_year": 2025},
                "summary": {},
            },
        },
    )
    assert checkout.json().get("already_paid") is not True
    assert created == [] or checkout.status_code >= 400


def test_conflict_receipt_webhook_retry_and_confirm_are_one_row(monkeypatch, caplog):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    user = "test-user-123"
    canonical = _FOLLOWUP_UUID
    snapshot_client = _wire_real_snapshot_client(
        monkeypatch,
        [_snapshot_row(
            canonical, user, 2025,
            {"marker": "paid-2025", "analysis_tax_year": 2025},
            session_id="cs_paid", paid_at="2026-01-01T00:00:00+00:00",
        )],
    )
    monkeypatch.setattr(main, "save_packet_entitlement", db.save_packet_entitlement)
    monkeypatch.setattr(main, "mark_packet_snapshot_paid", db.mark_packet_snapshot_paid)
    remember_analysis(
        canonical, user,
        {**SAMPLE_ANALYSIS, "analysis_id": canonical, "analysis_tax_year": 2025, "tax_profile": {"tax_year": 2025}},
    )
    session = _stripe_object_session(
        id="cs_test_paid_1",
        metadata={"analysis_id": canonical, "user_id": user, "tax_year": "2026"},
    )
    monkeypatch.setattr(main.stripe.checkout.Session, "retrieve", lambda *_a, **_k: session)

    def receipts():
        return [
            row for row in snapshot_client.entitlements
            if row.get("packet_session_id") == "cs_test_paid_1"
        ]

    with caplog.at_level(logging.ERROR):
        first = _post_signed_webhook(
            _packet_checkout_event(analysis_id=canonical, tax_year="2026", event_id="evt_once")
        )
        second = _post_signed_webhook(
            _packet_checkout_event(analysis_id=canonical, tax_year="2026", event_id="evt_retry")
        )
    assert first.status_code == 200 and first.json()["granted"] is False
    assert second.status_code == 200 and second.json()["granted"] is False
    assert len(receipts()) == 1
    assert receipts()[0]["analysis_id"] == f"conflict:{canonical}"
    assert receipts()[0]["tax_year"] == 2026
    confirm = client.post(
        "/api/year-close-packet/confirm",
        json={"session_id": "cs_test_paid_1", "analysis_id": canonical},
    )
    assert confirm.status_code == 409, confirm.text
    assert "PACKET_GRANT_YEAR_CONFLICT" in confirm.text
    assert len(receipts()) == 1
    assert snapshot_client.rows[0]["packet_session_id"] == "cs_paid"
    assert snapshot_client.rows[0]["paid_at"] == "2026-01-01T00:00:00+00:00"
    assert "cs_test_paid_1" in caplog.text
    assert user in caplog.text
    assert canonical in caplog.text
    assert "2025" in caplog.text and "2026" in caplog.text


def test_ensure_non_int_years_are_missing_and_patch_none_is_checkout_503(monkeypatch):
    """Non-int year keys are missing. A patch that returns None is checkout 503."""
    user = "test-user-123"
    aid = "analysis-non-int-year"
    patches = []

    def lookup(_analysis_id, _user_id):
        return {
            "id": "hist-non-int",
            "user_id": user,
            "result": {
                "analysis_id": _FOLLOWUP_UUID.upper(),
                "analysis_tax_year": "not-a-year",
                "tax_profile": {"tax_year": "also-not", "filing_status": "single"},
            },
        }, True

    def patch(_analysis_id, _user_id, body):
        patches.append(dict(body))
        return True

    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lookup)
    monkeypatch.setattr(main, "patch_analysis_result", patch)
    monkeypatch.setattr(main, "canonical_history_embedded_on_other_row", lambda *_args: False)
    assert main._ensure_packet_history_row(_FOLLOWUP_UUID, user, 2026) is True
    assert patches[0]["analysis_tax_year"] == 2026
    assert patches[0]["tax_profile"]["tax_year"] == 2026
    assert patches[0]["tax_profile"]["filing_status"] == "single"
    assert patches[0]["analysis_id"] == _FOLLOWUP_UUID

    monkeypatch.setattr(main, "canonical_history_embedded_on_other_row", lambda *_args: None)
    assert main._ensure_packet_history_row(_FOLLOWUP_UUID, user, 2026) is None

    def lookup_matching(_analysis_id, _user_id):
        return {
            "id": "hist-match",
            "user_id": user,
            "result": {
                "analysis_id": _FOLLOWUP_UUID.upper(),
                "analysis_tax_year": 2025,
                "tax_profile": {"tax_year": 2025, "filing_status": "single"},
            },
        }, True

    def patch_none(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lookup_matching)
    monkeypatch.setattr(main, "patch_analysis_result", patch_none)
    monkeypatch.setattr(main, "canonical_history_embedded_on_other_row", lambda *_args: False)
    assert main._ensure_packet_history_row(_FOLLOWUP_UUID, user, 2025) is None

    _test_stripe_env(monkeypatch)
    remember_analysis(aid, user, {**SAMPLE_ANALYSIS, "analysis_id": aid})

    def lookup_missing_year(_analysis_id, _user_id):
        return {
            "id": "hist-fill",
            "user_id": user,
            "result": {"analysis_id": aid, "tax_profile": {"filing_status": "single"}},
        }, True

    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lookup_missing_year)
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs),
    )
    response = client.post(
        "/api/year-close-packet/checkout",
        json={
            "analysis_id": aid,
            "analysis": {
                **SAMPLE_ANALYSIS,
                "analysis_id": aid,
                "tax_profile": {"tax_year": 2025, "filing_status": "single"},
            },
        },
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == (
        "Could not save this analysis before checkout. Please retry."
    )
    assert created == []


def test_checkout_no_caller_year_skips_bad_entitlement_rows(monkeypatch):
    _test_stripe_env(monkeypatch)
    user = "test-user-123"
    aid = "analysis-no-caller-year"
    created = []
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: created.append(kwargs),
    )
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    monkeypatch.setattr(
        main,
        "lookup_packet_entitlements_for_analysis",
        lambda *_args, **_kwargs: ([], False),
    )
    body = {"analysis_id": aid, "summary": {}}
    outage = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": aid, "analysis": body},
    )
    assert outage.status_code == 503, outage.text
    assert outage.json()["detail"] == (
        "Could not verify existing packet access. Please retry."
    )
    assert created == []

    monkeypatch.setattr(
        main,
        "lookup_packet_entitlements_for_analysis",
        lambda *_args, **_kwargs: (
            [
                "not-a-row",
                {"tax_year": "not-int", "analysis_id": aid},
                {"tax_year": 2025, "analysis_id": aid, "packet_session_id": "cs_ok"},
            ],
            True,
        ),
    )
    monkeypatch.setattr(
        main,
        "list_packet_snapshots_for_identity",
        lambda *_args, **_kwargs: ([], False),
    )
    listed = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": aid, "analysis": body},
    )
    assert listed.status_code == 503, listed.text
    assert listed.json()["detail"] == (
        "Could not load the saved packet snapshot. Please retry."
    )
    assert created == []


def test_download_authorize_and_durable_year_edges(monkeypatch):
    user = "test-user-123"
    aid = "analysis-durable-edges"
    with pytest.raises(main.HTTPException) as missing_durable:
        main._authorize_packet_download_identity(
            aid, "cs_missing", user, None, durable_year=None,
        )
    assert missing_durable.value.status_code == 503
    assert missing_durable.value.detail == (
        "Could not verify the Checkout session. Please retry."
    )

    monkeypatch.setattr(
        main,
        "list_packet_snapshots_for_identity",
        lambda *_args, **_kwargs: (None, False),
    )
    with pytest.raises(main.HTTPException) as list_down:
        main._authorize_packet_download_identity(
            aid, "cs_down", user, None, durable_year=2025,
        )
    assert list_down.value.status_code == 503
    assert list_down.value.detail == (
        "Could not verify the saved packet entitlement. Please retry."
    )

    monkeypatch.setattr(
        main,
        "list_packet_snapshots_for_identity",
        lambda *_args, **_kwargs: ([], True),
    )
    with pytest.raises(main.HTTPException) as list_miss:
        main._authorize_packet_download_identity(
            aid, "cs_miss", user, None, durable_year=2025,
        )
    assert list_miss.value.status_code == 503
    assert list_miss.value.detail == (
        "Could not verify the Checkout session. Please retry."
    )

    retrieves = []

    def configure():
        raise main.HTTPException(status_code=503, detail="stripe down")

    monkeypatch.setattr(main, "_configure_packet_stripe", configure)
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: retrieves.append(1),
    )
    assert main._retrieve_packet_download_session("cs_configure") == (None, None, True)
    assert retrieves == []

    def list_rows(*_args, **_kwargs):
        return [
            {"paid_at": None, "packet_session_id": "cs_bad_year", "tax_year": 2024},
            {"paid_at": "2026-01-01T00:00:00+00:00", "packet_session_id": "cs_other", "tax_year": 2025},
            {"paid_at": "2026-01-01T00:00:00+00:00", "packet_session_id": "cs_bad_year", "tax_year": "nope"},
        ], True

    monkeypatch.setattr(main, "list_packet_snapshots_for_identity", list_rows)
    assert main._durable_paid_snapshot_year(aid, user, "cs_bad_year") == (None, False)
