"""Year-close packet: unpaid block, $49 checkout (not tips), webhook unlock, PDF contents."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

import main
from auth import get_current_user, get_current_user_with_token
from year_close_packet import (
    OPTIONS_WASH_SALE_FAQ,
    PACKET_AMOUNT_CENTS,
    PACKET_DISCLAIMER,
    PACKET_METADATA_PRODUCT,
    PACKET_PRODUCT_NAME,
    SETTLEMENT_DATE_FAQ,
    build_packet_payload,
    packet_plain_text,
    packet_requires_test_stripe,
    render_packet_pdf,
    reset_packet_store,
    resolve_packet_stripe_secret_key,
    session_grants_packet,
)


def mock_get_current_user() -> str:
    return "test-user-123"


def mock_get_current_user_with_token() -> tuple[str, str]:
    return "test-user-123", "test-token-123"


main.app.dependency_overrides[get_current_user] = mock_get_current_user
main.app.dependency_overrides[get_current_user_with_token] = mock_get_current_user_with_token

client = TestClient(main.app)

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


@pytest.fixture(autouse=True)
def _clean_store():
    reset_packet_store()
    yield
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


class StripeObjectLike:
    """Attribute-access fake of a Stripe retrieve result; not a dict."""

    def __init__(self, **fields):
        data = {}
        for key, value in fields.items():
            if isinstance(value, dict):
                value = StripeObjectLike(**value)
            data[key] = value
            object.__setattr__(self, key, value)
        object.__setattr__(self, "_data", data)

    def __iter__(self):
        raise TypeError("StripeObjectLike is not a dict")

    def get(self, key, default=None):
        return self._data.get(key, default)

    def to_dict(self):
        return {
            key: (value.to_dict() if isinstance(value, StripeObjectLike) else value)
            for key, value in self._data.items()
        }

    def to_dict_recursive(self):
        return self.to_dict()


def _stripe_object_session(**fields):
    """StripeObject-like retrieve result: attributes, not a plain dict."""
    payload = {"id": "cs_test_retrieve"}
    payload.update(fields)
    return StripeObjectLike(**payload)


def test_payload_and_pdf_contain_1099_totals_amd_and_faqs():
    payload = build_packet_payload(SAMPLE_ANALYSIS, analysis_id="analysis-sample-1")
    text = packet_plain_text(payload)
    assert "1099 tax year: 2024" in text
    assert "$281,823.83" in text
    assert "$17,442.80" in text
    assert "AMD $300.00 disallowed" in text
    assert SETTLEMENT_DATE_FAQ in text
    assert OPTIONS_WASH_SALE_FAQ in text
    assert PACKET_DISCLAIMER in text
    assert "not a filed Form 8949" in text
    assert "not a rebuild of lots" in text

    pdf_bytes = render_packet_pdf(payload)
    pdf_text = _pdf_text(pdf_bytes)
    assert "2024" in pdf_text
    assert "281,823.83" in pdf_text
    assert "17,442.80" in pdf_text
    assert "AMD" in pdf_text
    assert "300.00" in pdf_text
    assert "settlement date" in pdf_text.lower()
    assert "credit-spread" in pdf_text.lower() or "credit spread" in pdf_text.lower()


def test_unpaid_download_is_403(monkeypatch):
    _test_stripe_env(monkeypatch)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)
    response = client.get("/api/year-close-packet/download?analysis_id=analysis-sample-1")
    assert response.status_code == 403
    assert "payment" in response.json()["detail"].lower()


def test_checkout_session_is_4900_cents_year_close_packet_not_tips(monkeypatch):
    _test_stripe_env(monkeypatch)
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
    assert price_data["product_data"]["name"] == "Year-close packet"
    assert captured["mode"] == "payment"
    assert captured["metadata"]["product"] == PACKET_METADATA_PRODUCT
    # Must not reuse TipJar price IDs
    assert "price" not in line_items[0]
    assert captured["success_url"].startswith(
        "https://options-tax-hub-client-staging.onrender.com/dashboard"
    )
    assert "packet_session={CHECKOUT_SESSION_ID}" in captured["success_url"]


def test_checkout_refuses_live_key_on_staging(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://options-tax-hub-client-staging.onrender.com")
    monkeypatch.setenv("STRIPE_FORCE_TEST_MODE", "true")
    monkeypatch.delenv("STRIPE_SECRET_KEY_TEST", raising=False)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_fake_live_key")
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "sk_live_fake_live_key")
    monkeypatch.setattr(
        main, "FRONTEND_URL", "https://options-tax-hub-client-staging.onrender.com"
    )

    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "analysis-sample-1", "analysis": SAMPLE_ANALYSIS},
    )
    assert response.status_code == 503
    assert "TEST" in response.json()["detail"]


def test_webhook_unlocks_download(monkeypatch):
    _test_stripe_env(monkeypatch)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)

    unpaid = client.get("/api/year-close-packet/download?analysis_id=analysis-sample-1")
    assert unpaid.status_code == 403

    webhook = client.post(
        "/api/year-close-packet/webhook",
        json={
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_paid_1",
                    "payment_status": "paid",
                    "amount_total": 4900,
                    "metadata": {
                        "product": PACKET_METADATA_PRODUCT,
                        "analysis_id": "analysis-sample-1",
                    },
                }
            },
        },
    )
    assert webhook.status_code == 200
    assert webhook.json()["granted"] is True

    paid = client.get("/api/year-close-packet/download?analysis_id=analysis-sample-1")
    assert paid.status_code == 200
    assert paid.headers["content-type"].startswith("application/pdf")
    pdf_text = _pdf_text(paid.content)
    assert "281,823.83" in pdf_text
    assert "AMD" in pdf_text
    assert "300.00" in pdf_text


def test_confirm_session_unlocks_download(monkeypatch):
    _test_stripe_env(monkeypatch)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)

    paid_session = SimpleNamespace(
        id="cs_test_paid_confirm",
        payment_status="paid",
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

    webhook = client.post(
        "/api/year-close-packet/webhook",
        json={
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_tip_coffee",
                    "payment_status": "paid",
                    "amount_total": 300,
                    "metadata": {"tier": "coffee"},
                }
            },
        },
    )
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


def test_post_download_rebuilds_from_analysis_json(monkeypatch):
    _test_stripe_env(monkeypatch)
    paid_session = SimpleNamespace(
        id="cs_test_paid_post",
        payment_status="paid",
        amount_total=4900,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "fresh-id",
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
            "analysis": SAMPLE_ANALYSIS,
        },
    )
    assert response.status_code == 200
    pdf_text = _pdf_text(response.content)
    assert "17,442.80" in pdf_text
    assert "AMD" in pdf_text


def test_checkout_missing_analysis_id_is_400(monkeypatch):
    _test_stripe_env(monkeypatch)
    response = client.post("/api/year-close-packet/checkout", json={"analysis_id": ""})
    assert response.status_code == 400


def test_webhook_ignores_other_events():
    response = client.post(
        "/api/year-close-packet/webhook",
        json={"type": "payment_intent.succeeded", "data": {"object": {}}},
    )
    assert response.status_code == 200
    assert response.json()["granted"] is False


def test_checkout_stripe_error_is_502(monkeypatch):
    _test_stripe_env(monkeypatch)

    def boom(**_kwargs):
        raise main.stripe.StripeError("nope")

    monkeypatch.setattr(main.stripe.checkout.Session, "create", boom)
    response = client.post(
        "/api/year-close-packet/checkout",
        json={"analysis_id": "analysis-sample-1", "analysis": SAMPLE_ANALYSIS},
    )
    assert response.status_code == 502


def test_session_grants_packet_reads_stripe_object_not_only_dict():
    """Mira FAIL: retrieve returned StripeObject; dict-only metadata read 403'd paid TEST."""
    session = _stripe_object_session(
        payment_status="paid",
        status="complete",
        amount_total=4900,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
        },
    )
    assert not isinstance(session, dict)
    assert not isinstance(session.metadata, dict)
    assert session_grants_packet(session, "analysis-sample-1") is True
    # Client restored analysis_id may be local-analysis; session metadata is canonical.
    assert session_grants_packet(session, "local-analysis") is True
    assert session_grants_packet(session, "") is True


def test_session_grants_packet_complete_status_without_exact_paid():
    """Sandbox Checkout can be status=complete while payment_status is not paid."""
    session = _stripe_object_session(
        payment_status="unpaid",
        status="complete",
        amount_total=4900,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
        },
    )
    assert session_grants_packet(session, "analysis-sample-1") is True
    missing_payment = _stripe_object_session(
        status="complete",
        amount_total=4900,
        metadata={
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "analysis-sample-1",
        },
    )
    assert session_grants_packet(missing_payment, "analysis-sample-1") is True


def test_confirm_packet_analysis_when_client_analysis_id_missing(monkeypatch):
    """Success URL has packet_analysis; client analysis_id is optional / local-analysis."""
    _test_stripe_env(monkeypatch)
    main.remember_analysis("analysis-sample-1", "test-user-123", SAMPLE_ANALYSIS)

    paid_session = _stripe_object_session(
        id="cs_test_paid_packet_analysis",
        payment_status="unpaid",
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
        params={"packet_analysis": "analysis-sample-1"},
        json={
            "session_id": "cs_test_paid_packet_analysis",
            "analysis": {k: v for k, v in SAMPLE_ANALYSIS.items() if k != "analysis_id"},
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
