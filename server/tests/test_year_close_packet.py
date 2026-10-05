"""Year-close packet: unpaid block, $49 checkout (not tips), webhook unlock, PDF contents."""

from __future__ import annotations

import hashlib
import hmac
import json
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
                return None
            tax_year = previous["tax_year"]
            payload = previous.get("packet_payload")
            session_id = previous.get("packet_session_id")
            paid_at = previous["paid_at"]
        else:
            paid_at = "now" if paid and session_id else None
        _FAKE_PACKET_SNAPSHOTS[key] = {
            "analysis_id": analysis_id,
            "user_id": user_id,
            "tax_year": tax_year,
            "packet_payload": payload,
            "packet_session_id": session_id or previous.get("packet_session_id"),
            "paid_at": paid_at,
        }
        return {"analysis_id": analysis_id}

    def get_snapshot(analysis_id, user_id):
        return _FAKE_PACKET_SNAPSHOTS.get((user_id, analysis_id)), True

    def save_entitlement(analysis_id, user_id, tax_year, session_id):
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
            if owner == user_id and year == int(tax_year)
        ]
        return (rows[-1] if rows else None), True

    def mark_snapshot_paid(analysis_id, user_id, tax_year, session_id):
        row = _FAKE_PACKET_SNAPSHOTS.get((user_id, analysis_id))
        if not row or int(row["tax_year"]) != int(tax_year):
            return False
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

    monkeypatch.setattr(main, "save_packet_snapshot", save_snapshot)
    monkeypatch.setattr(main, "get_packet_snapshot", get_snapshot)
    monkeypatch.setattr(main, "save_packet_entitlement", save_entitlement)
    monkeypatch.setattr(main, "lookup_packet_entitlement_for_tax_year", lookup_entitlement)
    monkeypatch.setattr(main, "mark_packet_snapshot_paid", mark_snapshot_paid)
    monkeypatch.setattr(main, "lookup_packet_grant_for_tax_year", lambda *_args, **_kwargs: (None, True))
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lookup_analysis)
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
                "id": "cs_test_paid_1",
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
    assert created == []
    assert _FAKE_PACKET_SNAPSHOTS == {}


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
    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": "victim-analysis", "session_id": paid_session.id},
    )

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
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
    assert response.status_code == 409
    assert "source document" in response.json()["detail"]


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
    monkeypatch.setattr(main, "get_packet_snapshot", lambda *_args: (None, True))
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
    """Primary key miss, embedded miss, then the alias query fails."""

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
        if self.calls < 3:
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

    assert response.status_code == 200
    assert response.json()["granted"] is False


def test_webhook_does_not_claim_blank_guest_snapshot_by_id(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    remember_analysis("analysis-sample-1", "", SAMPLE_ANALYSIS)
    original_payload = deepcopy(PACKET_STORE["analysis-sample-1"]["payload"])
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: True)

    response = _post_signed_webhook(_packet_checkout_event())

    assert response.status_code == 200
    assert response.json()["granted"] is False
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
    unavailable = client.get(
        "/api/year-close-packet/download",
        params={
            "analysis_id": "analysis-sample-1",
            "session_id": "cs_test_missing_packet_document",
        },
    )
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"] == main.PACKET_MISSING_SOURCE_DETAIL

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
    assert stale_session.status_code == 403

    missing_session = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": analysis_id},
    )
    assert missing_session.status_code == 200
    assert missing_session.headers["content-type"] == "application/pdf"


def test_grant_refuses_snapshot_stamped_for_another_session_tax_year(monkeypatch):
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

    assert granted is False
    assert _FAKE_PACKET_SNAPSHOTS[("test-user-123", "analysis-sample-1")]["paid_at"] is None
    assert PACKET_STORE["analysis-sample-1"]["paid"] is False


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
        "tax_year": 2025,
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
    remember_analysis(analysis_id, "test-user-123", LOT_MATCH_ANALYSIS)
    mark_paid(analysis_id, "cs_test_paid_rows", user_id="test-user-123")
    paid_session = SimpleNamespace(
        id="cs_test_paid_rows",
        payment_status="paid",
        amount_total=PACKET_AMOUNT_CENTS,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": analysis_id,
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
    assert response.status_code == 200
    pdf_text = _pdf_text(response.content)
    assert "NVDA" in pdf_text
    assert "SPX" in pdf_text
    assert "1099_only" in pdf_text


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

    def save_entitlement(analysis_id, user_id, tax_year, session_id):
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
    monkeypatch.setattr(
        main,
        "get_analysis_by_id",
        lambda analysis_id, user_id, client=None: (
            {
                "id": analysis_id,
                "user_id": user_id,
                "result": {"analysis_id": analysis_id},
            }
            if analysis_id == first_id
            else None
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
    monkeypatch.setattr(main, "get_analysis_by_id", db.get_analysis_by_id)
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
