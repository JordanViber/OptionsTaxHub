"""Year-close packet: unpaid block, $49 checkout (not tips), webhook unlock, PDF contents."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

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
    build_packet_payload,
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

    def save_snapshot(analysis_id, user_id, tax_year, payload, *, session_id=None, paid=False):
        key = (user_id, analysis_id)
        previous = _FAKE_PACKET_SNAPSHOTS.get(key, {})
        _FAKE_PACKET_SNAPSHOTS[key] = {
            "analysis_id": analysis_id,
            "user_id": user_id,
            "tax_year": tax_year,
            "packet_payload": payload,
            "packet_session_id": session_id or previous.get("packet_session_id"),
            "paid_at": "now" if paid else previous.get("paid_at"),
        }
        return {"analysis_id": analysis_id}

    def get_snapshot(analysis_id, user_id):
        return _FAKE_PACKET_SNAPSHOTS.get((user_id, analysis_id)), True

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
    monkeypatch.setattr(main, "mark_packet_snapshot_paid", mark_snapshot_paid)
    monkeypatch.setattr(main, "lookup_packet_grant_for_tax_year", lambda *_args, **_kwargs: (None, True))
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lookup_analysis)
    yield
    _FAKE_PACKET_SNAPSHOTS.clear()
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
):
    metadata = {
        "product": PACKET_METADATA_PRODUCT,
        "analysis_id": "analysis-sample-1",
    }
    if user_id:
        metadata["user_id"] = user_id
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
    monkeypatch.setattr(main, "ensure_analysis_history", lambda *_args: {"id": "history-row"})
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


def test_checkout_keeps_client_analysis_id_when_history_is_missing(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "11111111-1111-4111-8111-111111111111"
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": analysis_id}
    remember_analysis(analysis_id, "test-user-123", analysis)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    monkeypatch.setattr(main, "ensure_analysis_history", lambda *_args: {"id": "history-row"})
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
    analysis_id = SAMPLE_ANALYSIS["analysis_id"]
    remember_analysis(analysis_id, "", SAMPLE_ANALYSIS)
    original = PACKET_STORE[analysis_id]["payload"]
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: ({"result": SAMPLE_ANALYSIS}, True),
    )
    monkeypatch.setattr(main, "ensure_analysis_history", lambda *_args: {"id": "history-row"})
    captured = {}
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "create",
        lambda **kwargs: captured.update(kwargs) or FakeCheckoutSession(**kwargs),
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": analysis_id, "analysis": {"analysis_id": analysis_id}},
    )

    assert response.status_code == 200
    assert response.json()["analysis_id"] == analysis_id
    assert captured["metadata"]["analysis_id"] == analysis_id
    assert PACKET_STORE[analysis_id]["user_id"] == "test-user-123"
    assert PACKET_STORE[analysis_id]["payload"] == original


def test_checkout_reuses_durable_same_year_entitlement_without_charging_again(monkeypatch):
    _test_stripe_env(monkeypatch)
    analysis_id = "analysis-second-upload"
    analysis = {**SAMPLE_ANALYSIS, "analysis_id": analysis_id}
    remember_analysis(analysis_id, "test-user-123", analysis)
    monkeypatch.setattr(main, "ensure_analysis_history", lambda *_args: {"id": "history-row"})
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
    assert _FAKE_PACKET_SNAPSHOTS[("test-user-123", analysis_id)]["paid_at"]


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


def test_checkout_rejects_when_analysis_cannot_be_persisted(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    monkeypatch.setattr(main, "ensure_analysis_history", lambda *_args: None)
    remember_analysis("analysis-sample-1", "", SAMPLE_ANALYSIS)
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
    assert PACKET_STORE["analysis-sample-1"]["user_id"] == ""


def test_checkout_rekeys_local_analysis_per_user(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("local-analysis", "user-A", SAMPLE_ANALYSIS)
    monkeypatch.setitem(main.app.dependency_overrides, get_current_user, lambda: "user-B")
    canonical_ids = []
    monkeypatch.setattr(
        main,
        "ensure_analysis_history",
        lambda analysis_id, _user_id, analysis: canonical_ids.append(
            (analysis_id, analysis.get("analysis_id"))
        ) or {"id": "history-row"},
    )
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
    assert "private packet snapshot" in response.json()["detail"]
    assert canonical_ids and canonical_ids[0][0] != "local-analysis"
    assert captured == {}
    assert PACKET_STORE["local-analysis"]["user_id"] == "user-A"
    assert PACKET_STORE["local-analysis"]["paid"] is False


def test_checkout_copies_same_users_local_snapshot_to_canonical_id(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("local-analysis", "test-user-123", LOT_MATCH_ANALYSIS)
    monkeypatch.setattr(
        main,
        "ensure_analysis_history",
        lambda *_args: {"id": "canonical-history-row"},
    )
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


def test_download_rejects_b_users_session_for_a_users_snapshot(monkeypatch):
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
    history_patches = []
    monkeypatch.setattr(
        main,
        "patch_analysis_result",
        lambda *args: history_patches.append(args) or True,
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
        "patch_analysis_result",
        lambda *_args: pytest.fail("must reject before modifying history"),
    )

    response = client.get(
        "/api/year-close-packet/download",
        params={"analysis_id": "victim-analysis", "session_id": paid_session.id},
    )

    assert response.status_code == 403
    assert history_patches == []
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
    assert PACKET_STORE["analysis-followup"]["user_id"] == "test-user-123"
    assert PACKET_STORE["analysis-followup"]["paid"] is True


def test_reused_session_does_not_unlock_snapshot_stamped_for_another_year(monkeypatch):
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
    assert response.status_code == 503
    assert "private packet snapshot" in response.json()["detail"]


def test_local_analysis_alias_never_rebinds_session_to_caller_payload(monkeypatch):
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

    assert response.status_code == 200
    pdf_text = _pdf_text(response.content)
    assert "17,442.80" in pdf_text
    assert "999,999.00" not in pdf_text


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
    monkeypatch.setattr(main, "ensure_analysis_history", lambda *_args: {"id": "history-row"})
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
    # Client restored analysis_id may be local-analysis; session metadata is canonical.
    assert session_grants_packet(session, "local-analysis") is True
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
    original_payload = PACKET_STORE["guest-analysis"]["payload"]

    assert main.packet_store_belongs_to_user("guest-analysis", "test-user-123") is True
    assert PACKET_STORE["guest-analysis"]["user_id"] == ""
    upsert_payload("guest-analysis", "test-user-123", {"analysis_id": "guest-analysis"})
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


def test_webhook_claims_guest_snapshot_before_persisting_grant(monkeypatch):
    _test_stripe_env(monkeypatch)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_packet_test_secret")
    remember_analysis("analysis-sample-1", "", SAMPLE_ANALYSIS)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: True)

    response = _post_signed_webhook(_packet_checkout_event())

    assert response.status_code == 200
    assert response.json()["granted"] is True
    assert PACKET_STORE["analysis-sample-1"]["user_id"] == "test-user-123"
    assert PACKET_STORE["analysis-sample-1"]["paid"] is True


def test_paid_grant_survives_history_miss_from_owned_private_snapshot(monkeypatch):
    _test_stripe_env(monkeypatch)
    remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    monkeypatch.setattr(main, "patch_analysis_result", lambda *_args: False)
    restored = []
    monkeypatch.setattr(
        main,
        "ensure_analysis_history",
        lambda analysis_id, user_id, analysis: restored.append(
            (analysis_id, user_id, analysis)
        ) or {"id": "restored-history-row"},
    )

    granted = main._persist_packet_grant(
        _packet_checkout_event()["data"]["object"],
        "analysis-sample-1",
        "test-user-123",
    )

    assert granted is True
    assert restored == []
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
    paid_session = SimpleNamespace(
        id="cs_test_hist_harvest",
        payment_status="paid",
        amount_total=PACKET_AMOUNT_CENTS,
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
