"""Year-close packet: unpaid block, $49 checkout (not tips), webhook unlock, PDF contents."""

from __future__ import annotations

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
    is_same_year_1099_compare,
    packet_plain_text,
    packet_requires_test_stripe,
    purge_packet_store,
    remember_analysis,
    render_packet_pdf,
    reset_packet_store,
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

def _stripe_object_session(**fields):
    """Real stripe.checkout.Session.retrieve shape (StripeObject, not dict)."""
    payload = {"object": "checkout.session", "id": "cs_test_retrieve"}
    payload.update(fields)
    return StripeObject.construct_from(payload, "sk_test")


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


def test_custom_domain_frontend_uses_live_stripe(monkeypatch):
    monkeypatch.delenv("STRIPE_FORCE_TEST_MODE", raising=False)
    monkeypatch.setenv("FRONTEND_URL", "https://www.optionstaxhub.com")
    monkeypatch.delenv("RENDER_SERVICE_NAME", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert packet_requires_test_stripe() is False


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


def test_mismatch_2026_sample_plus_2024_fixture_is_previous_year_supplement():
    payload = build_packet_payload(MISMATCH_2026_ANALYSIS, analysis_id="analysis-2026-sample")
    assert payload["same_year_compare"] is False
    assert payload["form_1099_tax_year"] == 2024
    assert payload["analysis_tax_year"] == 2026
    text = packet_plain_text(payload)
    assert "previous-year supplement" in text
    assert "included as a dedicated page" not in text
    pdf_text = _pdf_text(render_packet_pdf(payload))
    assert COMPARE_TITLE not in pdf_text
    assert "settlement date" in pdf_text.lower()
    reader = PdfReader(BytesIO(render_packet_pdf(payload)))
    assert len(reader.pages) == 1


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
    assert len(reader.pages) == 2
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
    assert len(reader.pages) == 2


def test_unknown_1099_year_is_not_previous_year_mismatch_or_same_year_compare():
    payload = build_packet_payload(
        UNKNOWN_YEAR_ANALYSIS, analysis_id="analysis-unknown-year"
    )
    assert payload["same_year_compare"] is False
    assert payload["unknown_1099_year"] is True
    assert payload["form_1099_tax_year"] is None
    assert payload["form_1099_applied"] is True
    text = packet_plain_text(payload)
    assert UNKNOWN_1099_YEAR_COPY in text
    assert "1099 tax year: unknown" in text
    assert "previous-year supplement" not in text
    assert "does not match this export" not in text
    assert "included as a dedicated page" not in text
    pdf_text = _pdf_text(render_packet_pdf(payload))
    assert COMPARE_TITLE not in pdf_text
    assert "could not be determined" in pdf_text
    assert "not a previous-year mismatch" in pdf_text
    reader = PdfReader(BytesIO(render_packet_pdf(payload)))
    assert len(reader.pages) == 1


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
