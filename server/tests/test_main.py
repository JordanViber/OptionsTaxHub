from fastapi.testclient import TestClient
from pathlib import Path
from types import SimpleNamespace

import pytest

import db
import main
from auth import get_current_user, get_current_user_with_token, get_optional_user


# Mock authentication: return test user ID for all authenticated endpoints
def mock_get_current_user() -> str:
    return "test-user-123"


# Mock authentication with token: return both user ID and a dummy token
def mock_get_current_user_with_token() -> tuple[str, str]:
    return "test-user-123", "test-token-123"


# Override the authentication dependencies
main.app.dependency_overrides[get_current_user] = mock_get_current_user
main.app.dependency_overrides[get_optional_user] = mock_get_current_user
main.app.dependency_overrides[get_current_user_with_token] = mock_get_current_user_with_token

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _no_live_supabase(monkeypatch):
    """Keep analyze tests off the hosted Supabase project in server/.env.local."""
    monkeypatch.setattr(db, "get_supabase", lambda: None)
    monkeypatch.setattr(main, "get_supabase", lambda: None)


def setup_function():
    main.push_subscriptions.clear()
    main.reset_guest_analyze_quota()
    main.reset_guest_leap_rank_quota()
    from year_close_packet import reset_packet_store

    reset_packet_store()


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_csv_returns_first_five_rows():
    csv_content = """symbol,qty,price
AAPL,10,150
MSFT,5,310
TSLA,2,220
AMZN,1,130
GOOGL,3,140
NVDA,4,450
"""
    files = {"file": ("test.csv", csv_content, "text/csv")}
    response = client.post("/upload-csv", files=files)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 5
    assert data[0] == {"symbol": "AAPL", "qty": 10, "price": 150}
    assert data[-1] == {"symbol": "GOOGL", "qty": 3, "price": 140}


def test_push_subscribe_and_list():
    subscription = {
        "endpoint": "https://example.com/endpoint",
        "keys": {"p256dh": "key", "auth": "auth"},
    }

    response = client.post("/push/subscribe", json=subscription)
    assert response.status_code == 200
    assert response.json() == {"message": "Subscription stored", "count": 1}

    response = client.post("/push/subscribe", json=subscription)
    assert response.status_code == 200
    assert response.json() == {"message": "Subscription already exists", "count": 1}

    response = client.get("/push/subscriptions")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["subscriptions"][0]["endpoint"] == "https://example.com/endpoint"


def test_push_unsubscribe_flow():
    subscription = {
        "endpoint": "https://example.com/endpoint",
        "keys": {"p256dh": "key", "auth": "auth"},
    }

    client.post("/push/subscribe", json=subscription)

    response = client.post("/push/unsubscribe", json=subscription)
    assert response.status_code == 200
    assert response.json() == {"message": "Subscription removed", "count": 0}

    response = client.post("/push/unsubscribe", json=subscription)
    assert response.status_code == 200
    assert response.json() == {"message": "Subscription not found", "count": 0}


def test_send_push_notification_missing_vapid_keys():
    original_private = main.VAPID_PRIVATE_KEY
    original_public = main.VAPID_PUBLIC_KEY
    main.VAPID_PRIVATE_KEY = None
    main.VAPID_PUBLIC_KEY = None

    response = client.post(
        "/push/send",
        json={"title": "Test", "body": "Body"},
    )

    assert response.status_code == 200
    assert response.json()["error"] == "VAPID keys not configured"

    main.VAPID_PRIVATE_KEY = original_private
    main.VAPID_PUBLIC_KEY = original_public


def test_send_push_notification_success_and_expired_cleanup():
    class DummyResponse:
        status_code = 410

    class DummyWebPushException(Exception):
        def __init__(self):
            self.response = DummyResponse()

    original_private = main.VAPID_PRIVATE_KEY
    original_public = main.VAPID_PUBLIC_KEY
    original_webpush = main.webpush
    original_exception = main.WebPushException

    main.VAPID_PRIVATE_KEY = "private"
    main.VAPID_PUBLIC_KEY = "public"

    def fake_webpush(subscription_info, **_kwargs):
        if subscription_info["endpoint"] == "https://example.com/gone":
            raise DummyWebPushException()

    main.webpush = fake_webpush
    main.WebPushException = DummyWebPushException

    main.push_subscriptions.extend(
        [
            {"endpoint": "https://example.com/ok", "keys": {"p256dh": "k", "auth": "a"}},
            {"endpoint": "https://example.com/gone", "keys": {"p256dh": "k", "auth": "a"}},
        ]
    )

    response = client.post(
        "/push/send",
        json={"title": "Test", "body": "Body"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sent"] == 1
    assert payload["failed"] == 1
    assert payload["total_subscriptions"] == 1

    main.VAPID_PRIVATE_KEY = original_private
    main.VAPID_PUBLIC_KEY = original_public
    main.webpush = original_webpush
    main.WebPushException = original_exception


def test_push_test_endpoint():
    original_private = main.VAPID_PRIVATE_KEY
    original_public = main.VAPID_PUBLIC_KEY
    original_webpush = main.webpush

    main.VAPID_PRIVATE_KEY = "private"
    main.VAPID_PUBLIC_KEY = "public"

    def fake_webpush(**_kwargs):
        return None

    main.webpush = fake_webpush

    main.push_subscriptions.append(
        {"endpoint": "https://example.com/ok", "keys": {"p256dh": "k", "auth": "a"}}
    )

    response = client.post("/push/test")
    assert response.status_code == 200
    payload = response.json()
    assert payload["sent"] == 1

    main.VAPID_PRIVATE_KEY = original_private
    main.VAPID_PUBLIC_KEY = original_public
    main.webpush = original_webpush


def test_run_invokes_uvicorn(monkeypatch):
    import uvicorn

    captured = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("PORT", "9090")
    monkeypatch.setenv("ENVIRONMENT", "development")  # enable reload in dev mode

    main.run()

    assert captured["args"] == ("main:app",)
    assert captured["kwargs"]["host"] == "0.0.0.0"
    assert captured["kwargs"]["port"] == 9090
    assert captured["kwargs"]["reload"] is True


def test_run_invokes_uvicorn_no_reload_in_production(monkeypatch):
    import uvicorn

    captured = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("PORT", "9090")
    monkeypatch.setenv("ENVIRONMENT", "production")

    main.run()

    assert captured["kwargs"]["reload"] is False


def test_main_entrypoint(monkeypatch):
    import runpy
    import uvicorn

    called = {"value": False}

    def fake_run(*_args, **_kwargs):
        called["value"] = True

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("PORT", "9091")

    runpy.run_module("main", run_name="__main__")

    assert called["value"] is True


# ---------- Tax Profile Endpoints ----------


def test_save_tax_profile_requires_matching_user():
    """POST /api/tax-profile with mismatched user_id returns 403."""
    # Authenticated user is "test-user-123" from our mock
    # Try to save profile for a different user
    profile = {
        "user_id": "different-user",  # This doesn't match authenticated user
        "filing_status": "single",
        "estimated_annual_income": 100000,
        "state": "CA",
        "tax_year": 2025,
    }
    response = client.post("/api/tax-profile", json=profile)
    assert response.status_code == 403
    assert "Cannot save tax profile for another user" in response.json()["detail"]


def test_save_tax_profile_returns_profile(monkeypatch):
    """POST /api/tax-profile saves and returns profile data."""
    # Mock db_save_tax_profile to avoid real Supabase call
    def fake_save(**_kwargs):
        return None  # Simulate Supabase unavailable — fallback path

    monkeypatch.setattr(main, "db_save_tax_profile", fake_save)

    profile = {
        "filing_status": "married_filing_jointly",
        "estimated_annual_income": 150000,
        "state": "NY",
        "tax_year": 2025,
    }
    response = client.post("/api/tax-profile", json=profile)
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Tax profile saved (not persisted)"
    assert data["profile"]["estimated_annual_income"] == 150000


def test_save_tax_profile_persists_to_db(monkeypatch):
    """POST /api/tax-profile returns persisted data when DB is available."""
    saved_row = {
        "user_id": "test-user-123",
        "filing_status": "single",
        "estimated_annual_income": 120000,
        "state": "CA",
        "tax_year": 2025,
    }

    def fake_save(**_kwargs):
        return saved_row

    monkeypatch.setattr(main, "db_save_tax_profile", fake_save)

    profile = {
        "filing_status": "single",
        "estimated_annual_income": 120000,
        "state": "CA",
        "tax_year": 2025,
    }
    response = client.post("/api/tax-profile", json=profile)
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Tax profile saved"
    assert data["profile"]["estimated_annual_income"] == 120000


def test_get_tax_profile_returns_saved(monkeypatch):
    """GET /api/tax-profile returns authenticated user's saved profile."""
    saved_row = {
        "user_id": "test-user-123",
        "filing_status": "married_filing_jointly",
        "estimated_annual_income": 200000,
        "state": "TX",
        "tax_year": 2025,
    }

    def fake_get(_user_id):
        return saved_row

    monkeypatch.setattr(main, "db_get_tax_profile", fake_get)

    response = client.get("/api/tax-profile")
    assert response.status_code == 200
    data = response.json()
    assert data["estimated_annual_income"] == 200000
    assert data["filing_status"] == "married_filing_jointly"


def test_get_tax_profile_returns_default_when_not_found(monkeypatch):
    """GET /api/tax-profile returns defaults if no saved profile."""
    def fake_get(_user_id):
        return None

    monkeypatch.setattr(main, "db_get_tax_profile", fake_get)

    response = client.get("/api/tax-profile")
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "test-user-123"  # From mock JWT
    assert data["estimated_annual_income"] == 75000
    assert data["filing_status"] == "single"
    assert data["tax_year"] == 2026


# ---------- Tip / Donation Endpoints ----------


def test_get_tip_tiers():
    """GET /api/tips/tiers returns all available tip tiers."""
    response = client.get("/api/tips/tiers")
    assert response.status_code == 200
    tiers = response.json()
    assert len(tiers) == 3
    ids = [t["id"] for t in tiers]
    assert "coffee" in ids
    assert "lunch" in ids
    assert "generous" in ids
    # Verify amounts in cents
    coffee = next(t for t in tiers if t["id"] == "coffee")
    assert coffee["amount"] == 300
    assert coffee["label"] == "Coffee"


def test_tip_checkout_invalid_tier():
    """POST /api/tips/checkout with invalid tier returns 400."""
    response = client.post("/api/tips/checkout", json={"tier": "diamond"})
    assert response.status_code == 400
    assert "Invalid tier" in response.json()["detail"]


def test_tip_checkout_no_stripe_key(monkeypatch):
    """POST /api/tips/checkout returns 503 when Stripe is not configured."""
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", None)
    response = client.post("/api/tips/checkout", json={"tier": "coffee"})
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_tip_checkout_creates_session(monkeypatch):
    """POST /api/tips/checkout creates Stripe session and returns URL."""
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "sk_test_fake")

    class FakeSession:
        url = "https://checkout.stripe.com/test_session"

    import stripe as stripe_mod

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeSession()

    monkeypatch.setattr(stripe_mod.checkout.Session, "create", fake_create)

    response = client.post("/api/tips/checkout", json={"tier": "coffee"})
    assert response.status_code == 200
    data = response.json()
    assert data["checkout_url"] == "https://checkout.stripe.com/test_session"
    assert captured["api_key"] == "sk_test_fake"


def test_tip_checkout_stripe_error(monkeypatch):
    """POST /api/tips/checkout returns 502 on Stripe errors."""
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "sk_test_fake")

    import stripe as stripe_mod

    def fake_create(**_kwargs):
        raise stripe_mod.StripeError("Test error")

    monkeypatch.setattr(stripe_mod.checkout.Session, "create", fake_create)

    response = client.post("/api/tips/checkout", json={"tier": "lunch"})
    assert response.status_code == 502
    assert "checkout session" in response.json()["detail"].lower()


# ---------- Portfolio Analysis Endpoint ----------


SAMPLE_CSV_PATH = (
    Path(__file__).resolve().parents[2]
    / "client"
    / "public"
    / "sample-robinhood-transactions.csv"
)
ROBINHOOD_CLEAN_PATH = Path(__file__).parent / "fixtures" / "robinhood_clean.csv"


def _make_csv(content: str | None = None):
    """Helper to create a CSV upload payload."""
    if content is None:
        content = (
            "symbol,quantity,cost_basis_per_share,total_cost_basis,purchase_date,current_price\n"
            "AAPL,10,150.00,1500.00,2024-01-15,145.00\n"
            "MSFT,5,300.00,1500.00,2024-06-01,310.00\n"
        )
    return {"file": ("test.csv", content, "text/csv")}


def _stub_analyze_network(monkeypatch):
    """Stub live prices, AI, and history so analyze can run without network I/O."""
    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None, allow_network=True: (
            {s.upper(): 100.0 for s in symbols},
            [],
        ),
    )
    monkeypatch.setattr(
        "main.fetch_option_prices",
        lambda labels, fb=None, allow_network=True: ({}, []),
    )
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda lots: [])
    monkeypatch.setattr("main._save_history_best_effort", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "main.save_packet_snapshot",
        lambda analysis_id, *_args, **_kwargs: {"analysis_id": analysis_id},
    )
    monkeypatch.setattr("main.get_latest_activity_book", lambda uid, client=None: None)
    monkeypatch.setattr(
        "main.load_activity_book_for_merge",
        lambda user_id, client=None: db.ActivityBookLookup(),
    )
    # None means the private ledger write failed. Happy-path doubles must succeed.
    monkeypatch.setattr(
        "main.upsert_activity_book",
        lambda *args, **kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        "main.lookup_packet_grant_for_tax_year",
        lambda *args, **kwargs: (None, True),
    )


def _make_supplemental_1099_upload() -> tuple[str, bytes, str]:
    pdf_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "c15f7458-e9d5-4dfb-a985-351df5a36cde.pdf"
    )
    return (pdf_path.name, pdf_path.read_bytes(), "application/pdf")


def test_analyze_portfolio_success(monkeypatch):
    """POST /api/portfolio/analyze returns full analysis for a valid CSV."""
    from datetime import date
    from models import TaxLot, Transaction, Position, PortfolioSummary, HarvestingSuggestion
    import pytest

    lots = [
        TaxLot(
            symbol="AAPL", quantity=10, cost_basis_per_share=150.0,
            total_cost_basis=1500.0, purchase_date=date(2024, 1, 15),
            current_price=145.0,
        ),
    ]
    positions = [
        Position(
            position_id="AAPL:stock", symbol="AAPL", quantity=10, avg_cost_basis=150.0,
            total_cost_basis=1500.0, current_price=145.0, market_value=1450.0,
            unrealized_pnl=-50.0, unrealized_pnl_pct=-3.33,
        ),
    ]
    summary = PortfolioSummary(
        total_market_value=1450.0, total_cost_basis=1500.0,
        total_unrealized_pnl=-50.0, total_unrealized_pnl_pct=-3.33,
        positions_count=1, lots_with_losses=1,
    )

    monkeypatch.setattr("main.parse_csv", lambda _: (lots, [], [], []))
    monkeypatch.setattr("main.fetch_current_prices", lambda s, fb=None: ({"AAPL": 145.0}, []))
    monkeypatch.setattr("main.compute_lot_metrics", lambda l: l)
    monkeypatch.setattr("main.detect_wash_sales", lambda t: [])
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda l: [])
    monkeypatch.setattr("main.generate_suggestions", lambda **kw: [])
    monkeypatch.setattr("main.aggregate_positions", lambda l: positions)
    monkeypatch.setattr("main.build_portfolio_summary", lambda p, s, w: summary)

    response = client.post(
        "/api/portfolio/analyze?filing_status=single&estimated_income=80000&tax_year=2025",
        files=_make_csv(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["total_market_value"] == pytest.approx(1450.0)
    assert data["summary"]["positions_count"] == 1
    assert len(data["positions"]) == 1
    assert data["positions"][0]["symbol"] == "AAPL"
    assert "disclaimer" in data


def test_analyze_portfolio_empty_csv(monkeypatch):
    """POST /api/portfolio/analyze returns 400 if CSV has no parseable data."""
    monkeypatch.setattr("main.parse_csv", lambda _: ([], [], ["No valid rows"], []))

    response = client.post("/api/portfolio/analyze", files=_make_csv("bad,csv\n"))
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Could not parse" in detail["message"]
    assert "No valid rows" in detail["errors"]


def test_analyze_public_sample_csv_succeeds(monkeypatch):
    """The in-app sample CSV must analyze without mocking parse_csv."""
    _stub_analyze_network(monkeypatch)
    csv_bytes = SAMPLE_CSV_PATH.read_bytes()

    response = client.post(
        "/api/portfolio/analyze?filing_status=single&estimated_income=75000&tax_year=2026",
        files={"file": ("sample-robinhood-transactions.csv", csv_bytes, "text/csv")},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["tax_profile"]["tax_year"] == 2026
    symbols = {position["symbol"] for position in data["positions"]}
    assert "AAPL" in symbols
    assert "MSFT" in symbols
    amd_flags = [flag for flag in data["wash_sale_flags"] if flag["symbol"] == "AMD"]
    assert len(amd_flags) == 1
    assert amd_flags[0]["disallowed_loss"] == 300.0
    assert amd_flags[0]["sale_date"] == "2026-07-15"
    assert amd_flags[0]["repurchase_date"] == "2026-07-24"
    flag_symbols = {flag["symbol"] for flag in data["wash_sale_flags"]}
    assert flag_symbols == {"AMD", "NVDA", "TSLA"}
    assert data["summary"]["wash_sale_flags_count"] == 3
    assert data["summary"]["total_harvestable_losses"] > 0
    assert data["suggestions"]
    amd_lots = [
        lot
        for position in data["positions"]
        if position["symbol"] == "AMD"
        for lot in position["tax_lots"]
        if lot["wash_sale_disallowed"] > 0
    ]
    assert len(amd_lots) == 1
    assert amd_lots[0]["wash_sale_disallowed"] == 300.0


def test_analyze_robinhood_style_csv_succeeds(monkeypatch):
    """A Robinhood-format transaction CSV analyzes into open positions."""
    _stub_analyze_network(monkeypatch)
    csv_bytes = ROBINHOOD_CLEAN_PATH.read_bytes()

    response = client.post(
        "/api/portfolio/analyze",
        files={"file": ("robinhood_clean.csv", csv_bytes, "text/csv")},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["positions"]
    symbols = {position["symbol"] for position in data["positions"]}
    assert "AAPL" in symbols
    assert "MSFT" in symbols


_RH_HEADER = (
    "Activity Date,Process Date,Settle Date,Instrument,Description,"
    "Trans Code,Quantity,Price,Amount\n"
)


def _rh_csv(*rows: str) -> bytes:
    return (_RH_HEADER + "".join(rows)).encode("utf-8")


def test_analyze_merges_new_activity_with_saved_book(monkeypatch):
    """Signed-in users can upload only new trades; overlap is not double-counted."""
    from csv_parser import parse_csv

    _stub_analyze_network(monkeypatch)
    prior_csv = _rh_csv(
        "06/01/2023,06/01/2023,06/03/2023,AAPL,Apple,Buy,10,100.00,-1000.00\n",
        "01/01/2026,01/01/2026,01/03/2026,AAPL,Apple,Buy,2,180.00,-360.00\n",
    )
    _prior_lots, prior_txns, _errs, _real = parse_csv(prior_csv.decode())
    monkeypatch.setattr(
        "main.load_activity_book_for_merge",
        lambda uid, client=None: db.ActivityBookLookup(
            book={
                "analysis_id": "book-2023",
                "filename": "full-history.csv",
                "transactions": [t.model_dump(mode="json") for t in prior_txns],
                "tax_year": 2026,
            },
        ),
    )
    monkeypatch.setattr(
        "main.lookup_packet_grant_for_tax_year",
        lambda uid, year, client=None: ("cs_test_yeargrant", True),
    )

    new_csv = _rh_csv(
        "01/01/2026,01/01/2026,01/03/2026,AAPL,Apple,Buy,2,180.00,-360.00\n",
        "03/01/2026,03/01/2026,03/03/2026,AAPL,Apple,Buy,5,150.00,-750.00\n",
    )
    response = client.post(
        "/api/portfolio/analyze?tax_year=2026",
        files={"file": ("ytd-2026.csv", new_csv, "text/csv")},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    aapl = next(p for p in data["positions"] if p["symbol"] == "AAPL")
    assert aapl["quantity"] == 17
    book = data["activity_book"]
    assert book["added_from_this_upload"] == 1
    assert book["already_in_book"] == 1
    assert book["transaction_count"] == 3
    assert book["transactions"] == []
    assert book["merged_from_filename"] == "full-history.csv"
    assert data["packet_unlocked"] is True
    assert data["packet_session_id"] == "cs_test_yeargrant"
    assert data["summary"]["activity_transaction_count"] == 3
    assert any("Added 1 new trade" in w for w in data["warnings"])


def test_inherited_packet_grant_is_not_returned_when_history_save_fails(monkeypatch):
    from year_close_packet import PACKET_STORE

    _stub_analyze_network(monkeypatch)
    monkeypatch.setattr(
        "main.lookup_packet_grant_for_tax_year",
        lambda *_args, **_kwargs: ("cs_test_yeargrant", True),
    )
    monkeypatch.setattr("main._save_history_best_effort", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("main.save_packet_snapshot", lambda *_args, **_kwargs: None)
    csv_data = _rh_csv(
        "01/01/2026,01/01/2026,01/03/2026,AAPL,Apple,Buy,2,180.00,-360.00\n",
    )

    response = client.post(
        "/api/portfolio/analyze?tax_year=2026",
        files={"file": ("update.csv", csv_data, "text/csv")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["packet_unlocked"] is False
    assert body["packet_session_id"] is None
    assert body["analysis_id"] not in PACKET_STORE


def test_analyze_recovers_deleted_source_entitlement_after_stripe_verification(monkeypatch):
    from year_close_packet import PACKET_AMOUNT_CENTS, PACKET_METADATA_PRODUCT

    result = main.PortfolioAnalysis.model_validate(
        {
            "analysis_id": "followup-analysis",
            "tax_profile": {"tax_year": 2026, "filing_status": "single"},
        }
    )
    main.remember_analysis(
        result.analysis_id,
        "test-user-123",
        {"analysis_id": result.analysis_id, "tax_profile": {"tax_year": 2026}},
    )
    session = {
        "id": "cs_paid_for_deleted_analysis",
        "status": "complete",
        "payment_status": "paid",
        "amount_total": PACKET_AMOUNT_CENTS,
        "currency": "usd",
        "mode": "payment",
        "livemode": False,
        "metadata": {
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "original-analysis",
            "user_id": "test-user-123",
            "tax_year": "2026",
        },
    }
    monkeypatch.setattr(
        main,
        "lookup_packet_grant_for_tax_year",
        lambda *_args, **_kwargs: (None, True),
    )
    monkeypatch.setattr(
        main,
        "lookup_packet_entitlement_for_tax_year",
        lambda *_args, **_kwargs: (
            {
                "analysis_id": "original-analysis",
                "packet_session_id": "cs_paid_for_deleted_analysis",
            },
            True,
        ),
    )
    monkeypatch.setattr(main, "_configure_packet_stripe", lambda: "sk_test")
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )

    granted = main._apply_packet_year_grant(result, "test-user-123")

    assert granted.packet_unlocked is True
    assert granted.packet_session_id == "cs_paid_for_deleted_analysis"


def test_analyze_does_not_reuse_deleted_source_entitlement_for_wrong_year(monkeypatch):
    from year_close_packet import PACKET_AMOUNT_CENTS, PACKET_METADATA_PRODUCT

    result = main.PortfolioAnalysis.model_validate(
        {
            "analysis_id": "wrong-year-analysis",
            "tax_profile": {"tax_year": 2025, "filing_status": "single"},
        }
    )
    main.remember_analysis(
        result.analysis_id,
        "test-user-123",
        {"analysis_id": result.analysis_id, "tax_profile": {"tax_year": 2025}},
    )
    session = {
        "id": "cs_paid_for_2026",
        "status": "complete",
        "payment_status": "paid",
        "amount_total": PACKET_AMOUNT_CENTS,
        "currency": "usd",
        "mode": "payment",
        "livemode": False,
        "metadata": {
            "product": PACKET_METADATA_PRODUCT,
            "analysis_id": "original-analysis",
            "user_id": "test-user-123",
            "tax_year": "2026",
        },
    }
    monkeypatch.setattr(
        main,
        "lookup_packet_grant_for_tax_year",
        lambda *_args, **_kwargs: (None, True),
    )
    monkeypatch.setattr(
        main,
        "lookup_packet_entitlement_for_tax_year",
        lambda *_args, **_kwargs: (
            {
                "analysis_id": "original-analysis",
                "packet_session_id": "cs_paid_for_2026",
            },
            True,
        ),
    )
    monkeypatch.setattr(main, "_configure_packet_stripe", lambda: "sk_test")
    monkeypatch.setattr(
        main.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )

    granted = main._apply_packet_year_grant(result, "test-user-123")

    assert granted.packet_unlocked is False
    assert granted.packet_session_id is None


def test_analyze_replace_mode_ignores_saved_book(monkeypatch):
    """Start a new book uses only the file that was just uploaded."""
    from csv_parser import parse_csv

    _stub_analyze_network(monkeypatch)
    prior_csv = _rh_csv(
        "06/01/2023,06/01/2023,06/03/2023,AAPL,Apple,Buy,10,100.00,-1000.00\n",
    )
    _lots, prior_txns, _e, _r = parse_csv(prior_csv.decode())
    monkeypatch.setattr(
        "main.load_activity_book_for_merge",
        lambda uid, client=None: db.ActivityBookLookup(
            book={
                "analysis_id": "book-2023",
                "filename": "full-history.csv",
                "transactions": [t.model_dump(mode="json") for t in prior_txns],
            },
        ),
    )
    new_csv = _rh_csv(
        "03/01/2026,03/01/2026,03/03/2026,AAPL,Apple,Buy,5,150.00,-750.00\n",
    )
    response = client.post(
        "/api/portfolio/analyze?merge_mode=replace",
        files={"file": ("fresh.csv", new_csv, "text/csv")},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    aapl = next(p for p in data["positions"] if p["symbol"] == "AAPL")
    assert aapl["quantity"] == 5
    assert data["activity_book"]["replaced"] is True
    assert data["activity_book"]["transaction_count"] == 1


def test_analyze_invalid_tax_year_does_not_500(monkeypatch):
    """Out-of-range tax_year must not fail analysis (sample CSV uses saved profile)."""
    _stub_analyze_network(monkeypatch)
    csv_bytes = ROBINHOOD_CLEAN_PATH.read_bytes()

    response = client.post(
        "/api/portfolio/analyze?tax_year=2023",
        files={"file": ("robinhood_clean.csv", csv_bytes, "text/csv")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["tax_profile"]["tax_year"] == 2026


def test_analyze_unparseable_profile_query_params_do_not_422(monkeypatch):
    """JS undefined tokens in tax profile query params must not 422 analyze."""
    _stub_analyze_network(monkeypatch)
    csv_bytes = SAMPLE_CSV_PATH.read_bytes()

    response = client.post(
        "/api/portfolio/analyze?filing_status=undefined&estimated_income=undefined&tax_year=undefined",
        files={"file": ("sample-robinhood-transactions.csv", csv_bytes, "text/csv")},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["tax_profile"]["tax_year"] == 2026
    assert data["tax_profile"]["filing_status"] == "single"
    assert data["positions"]


def test_analyze_unexpected_error_returns_string_message(monkeypatch):
    """Unhandled analyze failures return a string message, never a bare object."""
    def _boom(_content):
        raise RuntimeError("lot matcher exploded")

    monkeypatch.setattr("main.parse_csv", _boom)

    response = client.post("/api/portfolio/analyze", files=_make_csv())
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert "Analysis failed" in detail["message"]
    assert "lot matcher exploded" in detail["errors"]
    assert "[object Object]" not in str(detail)


def test_analyze_portfolio_invalid_filing_status(monkeypatch):
    """POST /api/portfolio/analyze falls back to SINGLE for invalid filing status."""
    from datetime import date
    from models import TaxLot, Position, PortfolioSummary

    lots = [
        TaxLot(
            symbol="TSLA", quantity=2, cost_basis_per_share=200.0,
            total_cost_basis=400.0, purchase_date=date(2024, 3, 1),
            current_price=210.0,
        ),
    ]
    positions = [
        Position(
            position_id="TSLA:stock", symbol="TSLA", quantity=2, avg_cost_basis=200.0,
            total_cost_basis=400.0, current_price=210.0, market_value=420.0,
        ),
    ]
    summary = PortfolioSummary(positions_count=1)

    monkeypatch.setattr("main.parse_csv", lambda _: (lots, [], [], []))
    monkeypatch.setattr("main.fetch_current_prices", lambda s, fb=None: ({}, []))
    monkeypatch.setattr("main.compute_lot_metrics", lambda l: l)
    monkeypatch.setattr("main.detect_wash_sales", lambda t: [])
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda l: [])
    monkeypatch.setattr("main.generate_suggestions", lambda **kw: [])
    monkeypatch.setattr("main.aggregate_positions", lambda l: positions)
    monkeypatch.setattr("main.build_portfolio_summary", lambda p, s, w: summary)

    response = client.post(
        "/api/portfolio/analyze?filing_status=INVALID_STATUS",
        files=_make_csv(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["tax_profile"]["filing_status"] == "single"


def test_analyze_portfolio_saves_history(monkeypatch):
    """POST /api/portfolio/analyze saves to history when user_id is provided."""
    from datetime import date
    from models import TaxLot, Position, PortfolioSummary

    lots = [
        TaxLot(
            symbol="NVDA", quantity=3, cost_basis_per_share=400.0,
            total_cost_basis=1200.0, purchase_date=date(2024, 5, 1),
            current_price=450.0,
        ),
    ]
    positions = [
        Position(
            position_id="NVDA:stock", symbol="NVDA", quantity=3, avg_cost_basis=400.0,
            total_cost_basis=1200.0, current_price=450.0, market_value=1350.0,
        ),
    ]
    summary = PortfolioSummary(positions_count=1)

    save_called = {"value": False}

    def fake_save(**_kw):
        save_called["value"] = True

    monkeypatch.setattr("main.parse_csv", lambda _: (lots, [], [], []))
    monkeypatch.setattr("main.fetch_current_prices", lambda s, fb=None: ({}, []))
    monkeypatch.setattr("main.compute_lot_metrics", lambda l: l)
    monkeypatch.setattr("main.detect_wash_sales", lambda t: [])
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda l: [])
    monkeypatch.setattr("main.db_get_tax_profile", lambda uid: None)  # No profile exists
    monkeypatch.setattr("main.generate_suggestions", lambda **kw: [])
    monkeypatch.setattr("main.aggregate_positions", lambda l: positions)
    monkeypatch.setattr("main.build_portfolio_summary", lambda p, s, w: summary)
    monkeypatch.setattr("main.save_analysis_history", fake_save)

    response = client.post(
        "/api/portfolio/analyze?user_id=test-user-123",
        files=_make_csv(),
    )

    assert response.status_code == 200
    assert save_called["value"] is True


def test_analyze_portfolio_allows_unauthenticated(monkeypatch):
    """Guests can analyze a CSV; the run is not written to history."""
    _stub_analyze_network(monkeypatch)
    captured_user_ids: list[str] = []

    def capture_save(user_id, filename, summary, result):
        captured_user_ids.append(user_id)

    monkeypatch.setattr("main._save_history_best_effort", capture_save)
    sample_path = (
        Path(__file__).resolve().parents[2]
        / "client"
        / "public"
        / "sample-robinhood-transactions.csv"
    )
    main.app.dependency_overrides.pop(get_optional_user, None)
    try:
        response = client.post(
            "/api/portfolio/analyze",
            files={"file": (sample_path.name, sample_path.read_bytes(), "text/csv")},
        )
        assert response.status_code == 200
        assert "positions" in response.json()
        assert captured_user_ids == [""]
    finally:
        main.app.dependency_overrides[get_optional_user] = mock_get_current_user


def test_save_history_best_effort_skips_anonymous():
    """Anonymous analyses are not persisted."""
    main._save_history_best_effort("", "guest.csv", None, None)


def test_guest_analyze_rejects_oversize_csv(monkeypatch):
    """Unauthenticated analyze must 413 before unbounded file.read()."""
    monkeypatch.setattr(main, "_MAX_GUEST_CSV_BYTES", 64)
    parse_called = {"value": False}

    def _should_not_parse(_content):
        parse_called["value"] = True
        raise AssertionError("parse_csv must not run for an oversize guest CSV")

    monkeypatch.setattr("main.parse_csv", _should_not_parse)
    main.app.dependency_overrides.pop(get_optional_user, None)
    try:
        response = client.post(
            "/api/portfolio/analyze",
            files={"file": ("huge.csv", b"a" * 65, "text/csv")},
        )
    finally:
        main.app.dependency_overrides[get_optional_user] = mock_get_current_user

    assert response.status_code == 413
    assert "too large" in response.json()["detail"].lower()
    assert parse_called["value"] is False


def test_guest_analyze_quota_returns_429(monkeypatch):
    """Unauthenticated analyze is rate-limited per client IP."""
    _stub_analyze_network(monkeypatch)
    monkeypatch.setattr(main, "_GUEST_ANALYZE_MAX_PER_WINDOW", 2)
    sample_path = (
        Path(__file__).resolve().parents[2]
        / "client"
        / "public"
        / "sample-robinhood-transactions.csv"
    )
    csv_bytes = sample_path.read_bytes()
    main.app.dependency_overrides.pop(get_optional_user, None)
    try:
        first = client.post(
            "/api/portfolio/analyze",
            files={"file": (sample_path.name, csv_bytes, "text/csv")},
        )
        second = client.post(
            "/api/portfolio/analyze",
            files={"file": (sample_path.name, csv_bytes, "text/csv")},
        )
        third = client.post(
            "/api/portfolio/analyze",
            files={"file": (sample_path.name, csv_bytes, "text/csv")},
        )
    finally:
        main.app.dependency_overrides[get_optional_user] = mock_get_current_user

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert "too many guest analyses" in third.json()["detail"].lower()


def test_client_ip_ignores_spoofed_forwarded_for_from_public_peer():
    """Public TCP peers cannot rotate X-Forwarded-For to mint a new quota bucket."""
    request = SimpleNamespace(
        client=SimpleNamespace(host="8.8.8.8"),
        headers={"x-forwarded-for": "1.2.3.4"},
    )
    assert main._client_ip(request) == "8.8.8.8"


def test_client_ip_uses_last_forwarded_hop_from_private_proxy():
    """A trusted proxy's appended hop is the client; prepended spoofed values are ignored."""
    request = SimpleNamespace(
        client=SimpleNamespace(host="10.8.0.3"),
        headers={"x-forwarded-for": "8.8.8.8, 1.1.1.1"},
    )
    assert main._client_ip(request) == "1.1.1.1"


def test_guest_analyze_quota_ignores_rotating_forwarded_for(monkeypatch):
    """TestClient is not a trusted proxy, so rotating X-Forwarded-For still 429s."""
    _stub_analyze_network(monkeypatch)
    monkeypatch.setattr(main, "_GUEST_ANALYZE_MAX_PER_WINDOW", 2)
    sample_path = (
        Path(__file__).resolve().parents[2]
        / "client"
        / "public"
        / "sample-robinhood-transactions.csv"
    )
    csv_bytes = sample_path.read_bytes()
    main.app.dependency_overrides.pop(get_optional_user, None)
    try:
        statuses = []
        for spoofed in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
            response = client.post(
                "/api/portfolio/analyze",
                files={"file": (sample_path.name, csv_bytes, "text/csv")},
                headers={"X-Forwarded-For": spoofed},
            )
            statuses.append(response.status_code)
    finally:
        main.app.dependency_overrides[get_optional_user] = mock_get_current_user

    assert statuses == [200, 200, 429]


def test_guest_analyze_quota_prunes_stale_buckets():
    now = 1_000_000.0
    main._guest_analyze_hits["old"] = [now - 60 * 60 - 1]
    main._guest_analyze_hits["fresh"] = [now - 10]
    main._prune_guest_analyze_hits(now)
    assert "old" not in main._guest_analyze_hits
    assert "fresh" in main._guest_analyze_hits


def test_guest_analyze_quota_caps_bucket_count(monkeypatch):
    monkeypatch.setattr(main, "_GUEST_ANALYZE_MAX_BUCKETS", 2)
    main._guest_analyze_hits["a"] = [1.0]
    main._guest_analyze_hits["b"] = [2.0]
    main._guest_analyze_hits["c"] = [3.0]
    main._prune_guest_analyze_hits(10.0)
    assert len(main._guest_analyze_hits) <= 2
    assert "a" not in main._guest_analyze_hits
    assert "c" in main._guest_analyze_hits


def test_persist_guest_analysis_saves_history(monkeypatch):
    """Signed-in POST /api/portfolio/history stores a guest snapshot."""
    saved = {}

    def fake_save(**kwargs):
        saved.update(kwargs)
        return {"id": "hist-guest-1", "filename": kwargs["filename"]}

    monkeypatch.setattr("main.save_analysis_history", fake_save)
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: (None, True),
    )
    response = client.post(
        "/api/portfolio/history",
        json={
            "filename": "sample-robinhood-transactions.csv",
            "analysis": {
                "analysis_id": "guest-abc",
                "summary": {"positions_count": 3, "total_market_value": 1000},
                "packet_unlocked": True,
                "packet_session_id": "cs_test_forged",
            },
        },
    )
    assert response.status_code == 200
    assert saved["user_id"] == "test-user-123"
    assert saved["filename"] == "sample-robinhood-transactions.csv"
    assert saved["summary"]["positions_count"] == 3
    assert saved["result_data"]["analysis_id"] == "guest-abc"
    assert "packet_unlocked" not in saved["result_data"]
    assert "packet_session_id" not in saved["result_data"]


def test_persist_guest_analysis_claims_matching_private_packet_snapshot(monkeypatch):
    from year_close_packet import PACKET_STORE

    guest_analysis = {
        "analysis_id": "guest-claim-1",
        "tax_profile": {"tax_year": 2025, "filing_status": "single"},
        "tax_lots": [],
        "supplemental_1099": None,
        "wash_sale_flags": [],
        "suggestions": [],
        "lot_match_report": {
            "matched": [{"symbol": "AAPL", "quantity": 10}],
            "gap": [{"symbol": "NVDA", "quantity": 2}],
            "unmatched": [],
            "matched_count": 1,
            "gap_count": 1,
            "unmatched_count": 0,
        },
    }
    main.remember_analysis("guest-claim-1", "", guest_analysis)
    public_guest_analysis = {
        **guest_analysis,
        "lot_match_report": {
            **guest_analysis["lot_match_report"],
            "matched": [],
            "gap": [],
            "unmatched": [],
        },
    }
    saved_snapshots = []
    monkeypatch.setattr(
        main,
        "save_analysis_history",
        lambda **kwargs: {
            "id": "history-claim-1",
            **kwargs,
            "result": {
                **kwargs["result_data"],
                "lot_match_report": {
                    **kwargs["result_data"]["lot_match_report"],
                    "matched": [],
                    "gap": [],
                    "unmatched": [],
                },
            },
        },
    )
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    monkeypatch.setattr(
        main,
        "save_packet_snapshot",
        lambda analysis_id, user_id, tax_year, payload, **kwargs: (
            saved_snapshots.append((analysis_id, user_id, tax_year, payload))
            or {"analysis_id": analysis_id}
        ),
    )
    claimed_payloads = []
    original_upsert_packet_payload = main.upsert_packet_payload

    def upsert_packet_payload_spy(analysis_id, user_id, payload):
        claimed_payloads.append(payload)
        return original_upsert_packet_payload(analysis_id, user_id, payload)

    monkeypatch.setattr(
        main,
        "upsert_packet_payload",
        upsert_packet_payload_spy,
    )

    response = client.post(
        "/api/portfolio/history",
        json={"filename": "guest.csv", "analysis": public_guest_analysis},
    )

    assert response.status_code == 200, response.text
    assert saved_snapshots[0][0:3] == ("guest-claim-1", "test-user-123", 2025)
    assert saved_snapshots[0][3] == PACKET_STORE["guest-claim-1"]["payload"]
    assert saved_snapshots[0][3]["lot_match_report"]["matched"] == [
        {"symbol": "AAPL", "quantity": 10}
    ]
    assert saved_snapshots[0][3]["lot_match_report"]["gap"] == [
        {"symbol": "NVDA", "quantity": 2}
    ]
    assert claimed_payloads == [saved_snapshots[0][3]]
    assert PACKET_STORE["guest-claim-1"]["user_id"] == "test-user-123"


def test_apply_packet_year_grant_ignores_history_packet_flags(monkeypatch):
    result = main.PortfolioAnalysis.model_validate(
        {
            "analysis_id": "forged-history-analysis",
            "tax_profile": {"tax_year": 2025, "filing_status": "single"},
            "packet_unlocked": True,
            "packet_session_id": "cs_forged_history_value",
        }
    )
    monkeypatch.setattr(main, "lookup_packet_grant_for_tax_year", lambda *_args: (None, True))
    monkeypatch.setattr(main, "lookup_packet_entitlement_for_tax_year", lambda *_args: (None, True))
    monkeypatch.setattr(main, "paid_session_for_user_year", lambda *_args: None)

    result = main._apply_packet_year_grant(result, "test-user-123")

    assert result.packet_unlocked is False
    assert result.packet_session_id is None


def test_guest_snapshot_retry_reuses_history_row_after_snapshot_write_failure(monkeypatch):
    guest_analysis = {
        "analysis_id": "guest-retry-1",
        "tax_profile": {"tax_year": 2025, "filing_status": "single"},
        "tax_lots": [],
        "supplemental_1099": None,
        "wash_sale_flags": [],
        "suggestions": [],
    }
    main.remember_analysis("guest-retry-1", "", guest_analysis)
    history_saves = []
    history_row = {
        "id": "history-retry-1",
        "user_id": "test-user-123",
        "result": {**guest_analysis},
    }
    lookups = iter([(None, True), (history_row, True)])
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda *_args: next(lookups),
    )
    monkeypatch.setattr(
        main,
        "save_analysis_history",
        lambda **kwargs: history_saves.append(kwargs) or history_row,
    )
    snapshot_writes = []
    monkeypatch.setattr(
        main,
        "save_packet_snapshot",
        lambda analysis_id, user_id, tax_year, payload, **_kwargs: (
            snapshot_writes.append((analysis_id, user_id, tax_year, payload))
            or (None if len(snapshot_writes) == 1 else {"analysis_id": analysis_id})
        ),
    )
    request_body = {"filename": "guest.csv", "analysis": guest_analysis}

    failed = client.post("/api/portfolio/history", json=request_body)
    retried = client.post("/api/portfolio/history", json=request_body)

    assert failed.status_code == 503
    assert retried.status_code == 200
    assert len(history_saves) == 1
    assert len(snapshot_writes) == 2


def test_guest_history_retry_after_cache_loss_does_not_insert_duplicate(monkeypatch):
    analysis = {"analysis_id": "guest-cache-loss", "summary": {}}
    existing = {"id": "history-cache-loss", "result": analysis}
    lookups = []
    inserts = []
    monkeypatch.setattr(
        main,
        "lookup_analysis_for_entitlement",
        lambda analysis_id, user_id: (lookups.append((analysis_id, user_id)) or (existing, True)),
    )
    monkeypatch.setattr(
        main,
        "get_packet_snapshot",
        lambda *_args: (None, True),
    )
    monkeypatch.setattr(
        main,
        "save_analysis_history",
        lambda **kwargs: inserts.append(kwargs),
    )

    response = client.post(
        "/api/portfolio/history",
        json={"filename": "guest.csv", "analysis": analysis},
    )

    assert response.status_code == 503
    assert lookups == [("guest-cache-loss", "test-user-123")]
    assert inserts == []


def test_persist_guest_analysis_requires_auth():
    """Guest history persist must not work without a signed-in user."""
    main.app.dependency_overrides.pop(get_current_user, None)
    try:
        response = client.post(
            "/api/portfolio/history",
            json={"filename": "guest-run.csv", "analysis": {"summary": {}}},
        )
    finally:
        main.app.dependency_overrides[get_current_user] = mock_get_current_user

    assert response.status_code == 401


def test_analyze_portfolio_ai_failure_adds_warning(monkeypatch):
    """AI failure should add a warning but not break the analysis."""
    from datetime import date
    from models import TaxLot, Position, PortfolioSummary

    lots = [
        TaxLot(
            symbol="AMD", quantity=5, cost_basis_per_share=100.0,
            total_cost_basis=500.0, purchase_date=date(2024, 2, 1),
            current_price=95.0,
        ),
    ]
    positions = [
        Position(
            position_id="AMD:stock", symbol="AMD", quantity=5, avg_cost_basis=100.0,
            total_cost_basis=500.0, current_price=95.0, market_value=475.0,
        ),
    ]
    summary = PortfolioSummary(positions_count=1)

    monkeypatch.setattr("main.parse_csv", lambda _: (lots, [], [], []))
    monkeypatch.setattr("main.fetch_current_prices", lambda s, fb=None: ({}, []))
    monkeypatch.setattr("main.compute_lot_metrics", lambda l: l)
    monkeypatch.setattr("main.detect_wash_sales", lambda t: [])
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda l: [{"symbol": "AMD"}])

    def fake_ai_fail(_positions):
        raise RuntimeError("AI service unavailable")

    monkeypatch.setattr("main.get_ai_suggestions", fake_ai_fail)
    monkeypatch.setattr("main.generate_suggestions", lambda **kw: [])
    monkeypatch.setattr("main.aggregate_positions", lambda l: positions)
    monkeypatch.setattr("main.build_portfolio_summary", lambda p, s, w: summary)

    response = client.post(
        "/api/portfolio/analyze?user_id=test-user-123",
        files=_make_csv()
    )

    assert response.status_code == 200
    data = response.json()
    assert any("AI-powered suggestions unavailable" in w for w in data["warnings"])


def test_analyze_portfolio_with_wash_sales(monkeypatch):
    """POST /api/portfolio/analyze detects and adjusts for wash sales."""
    from datetime import date
    from models import TaxLot, Transaction, TransCode, Position, PortfolioSummary, WashSaleFlag

    lots = [
        TaxLot(
            symbol="AAPL", quantity=10, cost_basis_per_share=150.0,
            total_cost_basis=1500.0, purchase_date=date(2024, 1, 15),
            current_price=145.0,
        ),
    ]
    transactions = [
        Transaction(
            activity_date=date(2024, 6, 1), instrument="AAPL",
            trans_code=TransCode.SELL, quantity=10, price=140.0, amount=-1400.0,
        ),
        Transaction(
            activity_date=date(2024, 6, 15), instrument="AAPL",
            trans_code=TransCode.BUY, quantity=10, price=145.0, amount=1450.0,
        ),
    ]
    wash_flags = [
        WashSaleFlag(
            symbol="AAPL", sale_date=date(2024, 6, 1), sale_quantity=10,
            sale_loss=100.0, repurchase_date=date(2024, 6, 15),
            repurchase_quantity=10, disallowed_loss=100.0,
            adjusted_cost_basis=155.0, explanation="Repurchased within 30 days",
        ),
    ]
    positions = [
        Position(
            position_id="AAPL:stock", symbol="AAPL", quantity=10, avg_cost_basis=155.0,
            total_cost_basis=1550.0, current_price=145.0, market_value=1450.0,
            wash_sale_risk=True,
        ),
    ]
    summary = PortfolioSummary(positions_count=1, wash_sale_flags_count=1)

    monkeypatch.setattr("main.parse_csv", lambda _: (lots, transactions, [], []))
    monkeypatch.setattr("main.fetch_current_prices", lambda s, fb=None: ({"AAPL": 145.0}, []))
    monkeypatch.setattr("main.compute_lot_metrics", lambda l: l)
    monkeypatch.setattr("main.detect_wash_sales", lambda t, tax_year=None: wash_flags)
    monkeypatch.setattr("main.adjust_lots_for_wash_sales", lambda l, w: l)
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda l: [])
    monkeypatch.setattr("main.generate_suggestions", lambda **kw: [])
    monkeypatch.setattr("main.aggregate_positions", lambda l: positions)
    monkeypatch.setattr("main.build_portfolio_summary", lambda p, s, w: summary)

    response = client.post("/api/portfolio/analyze", files=_make_csv())

    assert response.status_code == 200
    data = response.json()
    assert len(data["wash_sale_flags"]) == 1
    assert data["wash_sale_flags"][0]["symbol"] == "AAPL"
    assert data["summary"]["wash_sale_flags_count"] == 1


def test_analyze_portfolio_passes_selected_tax_year_to_wash_sale_detector(monkeypatch):
    """POST /api/portfolio/analyze scopes wash-sale output to the selected tax year."""
    from datetime import date
    from models import TaxLot, Position, PortfolioSummary, Transaction, TransCode

    lots = [
        TaxLot(
            symbol="AAPL", quantity=10, cost_basis_per_share=150.0,
            total_cost_basis=1500.0, purchase_date=date(2024, 1, 15),
            current_price=145.0,
        ),
    ]
    positions = [
        Position(
            position_id="AAPL:stock", symbol="AAPL", quantity=10, avg_cost_basis=150.0,
            total_cost_basis=1500.0, current_price=145.0, market_value=1450.0,
        ),
    ]
    summary = PortfolioSummary(positions_count=1, wash_sale_flags_count=0)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "main.parse_csv",
        lambda _: (
            lots,
            [
                Transaction(
                    activity_date=date(2025, 6, 1), instrument="AAPL",
                    trans_code=TransCode.SELL, quantity=10, price=140.0, amount=-1400.0,
                ),
            ],
            [],
            [],
        ),
    )
    monkeypatch.setattr("main.fetch_current_prices", lambda s, fb=None: ({"AAPL": 145.0}, []))
    monkeypatch.setattr("main.compute_lot_metrics", lambda l: l)
    monkeypatch.setattr(
        "main.detect_wash_sales",
        lambda t, tax_year=None: captured.update({"tax_year": tax_year}) or [],
    )
    monkeypatch.setattr("main.adjust_lots_for_wash_sales", lambda l, w: l)
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda l: [])
    monkeypatch.setattr("main.generate_suggestions", lambda **kw: [])
    monkeypatch.setattr("main.aggregate_positions", lambda l: positions)
    monkeypatch.setattr("main.build_portfolio_summary", lambda p, s, w: summary)

    response = client.post("/api/portfolio/analyze?tax_year=2026", files=_make_csv())

    assert response.status_code == 200
    assert captured["tax_year"] == 2026


def test_summarize_warnings_consolidates_repetitive_broker_messages():
    """Repeated broker-specific warnings should be grouped into plain English summaries."""
    from main import _summarize_warnings

    warnings = [
        "Option assignment (OASGN) detected for TSLL on 09/22/2025 — the option P&L has been recorded, but the resulting stock position change from assignment/exercise may require manual verification.",
        "Option assignment (OASGN) detected for TSLL on 09/26/2025 — the option P&L has been recorded, but the resulting stock position change from assignment/exercise may require manual verification.",
        "Corporate action (OCA) detected for ASST — lot quantities are NOT automatically adjusted. Reported positions for ASST may be inaccurate. Verify against your brokerage account and re-run after the CSV reflects any post-action quantities.",
        "Corporate action (OCA) detected for ASST — lot quantities are NOT automatically adjusted. Reported positions for ASST may be inaccurate. Verify against your brokerage account and re-run after the CSV reflects any post-action quantities.",
        "Using CSV-provided price for CEP (live price unavailable)",
    ]

    summarized = _summarize_warnings(warnings)

    assert any("Option assignments affected TSLL 2 times" in w for w in summarized)
    assert any("Corporate action activity may have changed" in w for w in summarized)
    assert any("Live prices were unavailable for CEP" in w for w in summarized)
    assert len(summarized) == 3


def test_filter_suggestion_tax_lots_skips_split_affected_stock_lots():
    from datetime import date
    from main import _filter_suggestion_tax_lots
    from models import TaxLot, Transaction, AssetType, TransCode

    stock_lot = TaxLot(
        symbol="ASST",
        quantity=5,
        cost_basis_per_share=1.0,
        total_cost_basis=5.0,
        purchase_date=date(2026, 1, 15),
        asset_type=AssetType.STOCK,
    )
    option_lot = TaxLot(
        symbol="ASST",
        quantity=1,
        cost_basis_per_share=2.5,
        total_cost_basis=250.0,
        purchase_date=date(2026, 1, 15),
        asset_type=AssetType.OPTION,
        contract_label="ASST 4/17/2026 Call $1.00",
    )
    transactions = [
        Transaction(
            activity_date=date(2026, 2, 6),
            instrument="ASST",
            description="Stock Split",
            trans_code=TransCode.SPR,
            quantity=400,
            price=0.0,
            amount=0.0,
            asset_type=AssetType.STOCK,
        )
    ]

    filtered_lots, warnings = _filter_suggestion_tax_lots(
        [stock_lot, option_lot],
        transactions,
    )

    assert filtered_lots == [option_lot]
    assert any("Skipped automated harvesting suggestions for ASST" in w for w in warnings)


def test_build_manual_review_notes_by_symbol_summarizes_unsupported_events():
    from datetime import date
    from main import _build_manual_review_notes_by_symbol
    from models import AssetType, Transaction, TransCode

    transactions = [
        Transaction(
            activity_date=date(2026, 2, 6),
            instrument="ASST",
            description="Stock Split",
            trans_code=TransCode.SPR,
            quantity=400,
            price=0.0,
            amount=0.0,
            asset_type=AssetType.STOCK,
        ),
        Transaction(
            activity_date=date(2026, 2, 7),
            instrument="ASST",
            description="Corporate Action",
            trans_code=TransCode.OCA,
            quantity=1,
            price=0.0,
            amount=0.0,
            asset_type=AssetType.OPTION,
        ),
        Transaction(
            activity_date=date(2026, 2, 8),
            instrument="TSLL",
            description="Option Assignment",
            trans_code=TransCode.OASGN,
            quantity=1,
            price=0.0,
            amount=0.0,
            asset_type=AssetType.OPTION,
        ),
    ]

    notes = _build_manual_review_notes_by_symbol(transactions)

    assert "ASST" in notes
    assert "stock split activity" in notes["ASST"]
    assert "corporate-action adjustments" in notes["ASST"]
    assert "TSLL" in notes
    assert "option assignment activity" in notes["TSLL"]


def test_apply_manual_review_flags_marks_positions_and_suggestions():
    from main import _apply_manual_review_flags
    from models import AssetType, HarvestingSuggestion, Position

    reason = (
        "Recent stock split activity affected ASST. Verify reported quantities, "
        "adjusted contracts, and cost basis manually before acting."
    )
    positions = [
        Position(
            position_id="ASST:stock",
            symbol="ASST",
            quantity=5,
            avg_cost_basis=1.0,
            total_cost_basis=5.0,
            asset_type=AssetType.STOCK,
            tax_lots=[],
        )
    ]
    suggestions = [
        HarvestingSuggestion(
            symbol="ASST",
            suggestion_id="ASST-stock-2026-01-15",
            display_label="ASST",
            lot_details="Tax lot opened Jan 15, 2026 at $1.00/share",
            quantity=5,
            cost_basis_per_share=1.0,
            estimated_loss=1.5,
            tax_savings_estimate=0.3,
            holding_period_days=10,
            is_long_term=False,
        )
    ]

    _apply_manual_review_flags(positions, suggestions, {"ASST": reason})

    assert positions[0].manual_review_required is True
    assert positions[0].manual_review_reason == reason
    assert suggestions[0].manual_review_required is True
    assert suggestions[0].manual_review_reason == reason





# ---------- Portfolio History Endpoints ----------


def test_get_portfolio_history(monkeypatch):
    """GET /api/portfolio/history returns authenticated user's history."""
    mock_history = [
        {"id": "h1", "filename": "test1.csv", "uploaded_at": "2025-01-01T00:00:00"},
        {"id": "h2", "filename": "test2.csv", "uploaded_at": "2025-01-02T00:00:00"},
    ]

    # Mock get_supabase to return a dummy client object (service role path)
    mock_client = object()
    monkeypatch.setattr("main.get_supabase", lambda: mock_client)
    monkeypatch.setattr("main.get_analysis_history", lambda uid, limit, client=None: mock_history)

    response = client.get("/api/portfolio/history")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["id"] == "h1"


def test_get_portfolio_history_empty(monkeypatch):
    """GET /api/portfolio/history returns empty list for new user."""
    mock_client = object()
    monkeypatch.setattr("main.get_supabase", lambda: mock_client)
    monkeypatch.setattr("main.get_analysis_history", lambda uid, limit, client=None: [])

    response = client.get("/api/portfolio/history")
    assert response.status_code == 200
    assert response.json() == []


def test_get_portfolio_history_custom_limit(monkeypatch):
    """GET /api/portfolio/history?limit=5 passes limit to DB."""
    captured = {}

    def fake_history(uid, limit, client=None):
        captured["limit"] = limit
        return []

    mock_client = object()
    monkeypatch.setattr("main.get_supabase", lambda: mock_client)
    monkeypatch.setattr("main.get_analysis_history", fake_history)

    response = client.get("/api/portfolio/history?limit=5")
    assert response.status_code == 200
    assert captured["limit"] == 5


def test_get_portfolio_history_invalid_limit():
    """GET /api/portfolio/history with invalid limit returns 422."""
    response = client.get("/api/portfolio/history?limit=0")
    assert response.status_code == 422


# ---------- Single Analysis Retrieval ----------


def test_get_portfolio_analysis_found(monkeypatch):
    """GET /api/portfolio/analysis/{id} returns the full analysis."""
    mock_record = {
        "id": "abc-123",
        "user_id": "test-user-123",  # Must match authenticated user
        "filename": "portfolio.csv",
        "result": {"positions": [], "summary": {}},
    }

    mock_client = object()
    monkeypatch.setattr("main.get_supabase", lambda: mock_client)
    monkeypatch.setattr("main.get_analysis_by_id", lambda aid, uid, client=None: mock_record)

    response = client.get("/api/portfolio/analysis/abc-123")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "abc-123"
    assert "result" in data


def test_get_portfolio_analysis_not_found(monkeypatch):
    """GET /api/portfolio/analysis/{id} returns 404 when not found."""
    mock_client = object()
    monkeypatch.setattr("main.get_supabase", lambda: mock_client)
    monkeypatch.setattr("main.get_analysis_by_id", lambda aid, uid, client=None: None)

    response = client.get("/api/portfolio/analysis/nonexistent")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()





# ---------- Delete Analysis ----------


def test_delete_analysis_success(monkeypatch):
    """DELETE /api/portfolio/analysis/{id} returns success on deletion."""
    monkeypatch.setattr(
        "main.get_analysis_by_id",
        lambda aid, uid: {"id": aid, "user_id": uid, "result": {"analysis_id": "packet-id"}},
    )
    monkeypatch.setattr("main.delete_analysis_by_id", lambda aid, uid: True)
    forgotten = []
    monkeypatch.setattr(
        "main.forget_packet_payload",
        lambda aid, uid: forgotten.append((aid, uid)) or True,
    )

    response = client.delete("/api/portfolio/analysis/abc-123")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert forgotten == [
        ("packet-id", "test-user-123"),
        ("abc-123", "test-user-123"),
    ]


def test_delete_analysis_clears_packet_by_history_id_when_result_has_no_analysis_id(monkeypatch):
    monkeypatch.setattr(
        "main.get_analysis_by_id",
        lambda aid, uid: {"id": aid, "user_id": uid, "result": None},
    )
    monkeypatch.setattr("main.delete_analysis_by_id", lambda aid, uid: True)
    forgotten = []
    monkeypatch.setattr(
        "main.forget_packet_payload",
        lambda aid, uid: forgotten.append((aid, uid)) or True,
    )

    response = client.delete("/api/portfolio/analysis/abc-123")

    assert response.status_code == 200
    assert forgotten == [("abc-123", "test-user-123")]


def test_delete_analysis_not_found(monkeypatch):
    """DELETE /api/portfolio/analysis/{id} returns 404 when not found."""
    monkeypatch.setattr("main.get_analysis_by_id", lambda *_args: None)
    monkeypatch.setattr("main.delete_analysis_by_id", lambda aid, uid: False)

    response = client.delete("/api/portfolio/analysis/nonexistent")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()





# ---------- Cleanup Orphan History ----------


def test_cleanup_orphan_history(monkeypatch):
    """DELETE /api/portfolio/history/cleanup deletes orphans."""
    monkeypatch.setattr("main.delete_analyses_without_result", lambda uid: 3)

    response = client.delete("/api/portfolio/history/cleanup")
    assert response.status_code == 200
    assert response.json()["deleted"] == 3


def test_cleanup_orphan_history_none(monkeypatch):
    """DELETE /api/portfolio/history/cleanup returns 0 when none found."""
    monkeypatch.setattr("main.delete_analyses_without_result", lambda uid: 0)

    response = client.delete("/api/portfolio/history/cleanup")
    assert response.status_code == 200
    assert response.json()["deleted"] == 0


# ---------- Prices Endpoint ----------


def test_get_prices_success(monkeypatch):
    """GET /api/prices returns prices for given symbols."""
    import pytest

    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None: ({"AAPL": 150.0, "MSFT": 300.0}, []),
    )

    response = client.get("/api/prices?symbols=AAPL,MSFT")
    assert response.status_code == 200
    data = response.json()
    assert data["prices"]["AAPL"] == pytest.approx(150.0)
    assert data["prices"]["MSFT"] == pytest.approx(300.0)
    assert data["warnings"] == []


def test_get_prices_with_warnings(monkeypatch):
    """GET /api/prices returns warnings for missing symbols."""
    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None: ({"AAPL": 150.0}, ["FAKE: no data found"]),
    )

    response = client.get("/api/prices?symbols=AAPL,FAKE")
    assert response.status_code == 200
    data = response.json()
    assert len(data["warnings"]) == 1


def _future_leap_window():
    from datetime import datetime, timedelta, timezone

    as_of = datetime.now(timezone.utc).date()
    start = as_of + timedelta(days=365)
    end = as_of + timedelta(days=730)
    return start.isoformat(), end.isoformat()


def test_leap_rank_success_unauthenticated(monkeypatch):
    """Guests can rank LEAPs with no packet / auth gate."""
    start, end = _future_leap_window()

    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None: ({"NVDA": 100.0}, []),
    )
    monkeypatch.setattr(
        "main.fetch_option_chain_window",
        lambda symbol, right, expiry_from, expiry_to, as_of=None: (
            [
                {
                    "strike": 90.0,
                    "expiration": start,
                    "bid": 11.9,
                    "ask": 12.1,
                    "last": 12.0,
                    "open_interest": 40,
                },
                {
                    "strike": 95.0,
                    "expiration": start,
                    "bid": 9.9,
                    "ask": 10.1,
                    "last": 10.0,
                    "open_interest": 40,
                },
                {
                    "strike": 100.0,
                    "expiration": start,
                    "bid": 7.9,
                    "ask": 8.1,
                    "last": 8.0,
                    "open_interest": 40,
                },
            ],
            [start],
            [],
        ),
    )

    main.app.dependency_overrides.pop(get_optional_user, None)
    try:
        response = client.get(
            f"/api/options/leap-rank?symbol=nvda&right=call&expiry_from={start}&expiry_to={end}"
        )
    finally:
        main.app.dependency_overrides[get_optional_user] = mock_get_current_user

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["symbol"] == "NVDA"
    assert len(data["ranks"]) == 3
    assert data["ranks"][0]["implied_cagr"] <= data["ranks"][1]["implied_cagr"]
    assert "annualized move" in data["ranks"][0]["why_vs_stock"]
    assert "higher CAGR" not in data["ranks"][0]["why_vs_stock"]
    assert "packet" not in response.text.lower()
    assert "$49" not in response.text


def test_leap_rank_no_quote_is_honest_empty(monkeypatch):
    start, end = _future_leap_window()
    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None: ({}, ["No prices available for: NVDA"]),
    )
    chain_called = {"value": False}

    def _should_not_chain(*args, **kwargs):
        chain_called["value"] = True
        return [], [], ["should not run"]

    monkeypatch.setattr("main.fetch_option_chain_window", _should_not_chain)
    response = client.get(
        f"/api/options/leap-rank?symbol=NVDA&right=call&expiry_from={start}&expiry_to={end}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["reason"] == "no_quote"
    assert data["ranks"] == []
    assert "do not invent prices" in data["message"]
    assert chain_called["value"] is False


def test_leap_rank_no_chain_is_honest_empty(monkeypatch):
    start, end = _future_leap_window()
    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None: ({"NVDA": 100.0}, []),
    )
    monkeypatch.setattr(
        "main.fetch_option_chain_window",
        lambda *args, **kwargs: ([], [], ["yahoo down"]),
    )
    response = client.get(
        f"/api/options/leap-rank?symbol=NVDA&right=call&expiry_from={start}&expiry_to={end}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["reason"] == "no_chain"
    assert data["ranks"] == []


def test_leap_rank_returns_fewer_than_three(monkeypatch):
    start, end = _future_leap_window()
    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None: ({"NVDA": 100.0}, []),
    )
    monkeypatch.setattr(
        "main.fetch_option_chain_window",
        lambda *args, **kwargs: (
            [
                {
                    "strike": 90.0,
                    "expiration": start,
                    "bid": 11.9,
                    "ask": 12.1,
                    "last": 12.0,
                    "open_interest": 40,
                }
            ],
            [start],
            [],
        ),
    )
    response = client.get(
        f"/api/options/leap-rank?symbol=NVDA&right=call&expiry_from={start}&expiry_to={end}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["ranks"]) == 1
    assert data["ranks"][0]["rank"] == 1


def test_leap_rank_rejects_bad_input():
    start, end = _future_leap_window()
    bad_symbol = client.get(
        f"/api/options/leap-rank?symbol=NVDA%20INC&right=call&expiry_from={start}&expiry_to={end}"
    )
    assert bad_symbol.status_code == 400

    bad_right = client.get(
        f"/api/options/leap-rank?symbol=NVDA&right=straddle&expiry_from={start}&expiry_to={end}"
    )
    assert bad_right.status_code == 400

    inverted = client.get(
        f"/api/options/leap-rank?symbol=NVDA&right=call&expiry_from={end}&expiry_to={start}"
    )
    assert inverted.status_code == 400


def test_guest_leap_rank_quota_returns_429(monkeypatch):
    start, end = _future_leap_window()
    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None: ({"NVDA": 100.0}, []),
    )
    monkeypatch.setattr(
        "main.fetch_option_chain_window",
        lambda *args, **kwargs: ([], [], ["none"]),
    )
    monkeypatch.setattr(main, "_GUEST_LEAP_RANK_MAX_PER_WINDOW", 2)
    main.app.dependency_overrides.pop(get_optional_user, None)
    try:
        first = client.get(
            f"/api/options/leap-rank?symbol=NVDA&right=call&expiry_from={start}&expiry_to={end}"
        )
        second = client.get(
            f"/api/options/leap-rank?symbol=NVDA&right=call&expiry_from={start}&expiry_to={end}"
        )
        third = client.get(
            f"/api/options/leap-rank?symbol=NVDA&right=call&expiry_from={start}&expiry_to={end}"
        )
    finally:
        main.app.dependency_overrides[get_optional_user] = mock_get_current_user

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert "too many leap lookups" in third.json()["detail"].lower()


def test_analyze_portfolio_applies_live_option_prices(monkeypatch):
    from datetime import date
    import pytest
    from models import TaxLot, Transaction, TransCode, AssetType, Position, PortfolioSummary

    option_lot = TaxLot(
        symbol="TSLA",
        description="TSLA 3/16/2026 Put $375.00",
        quantity=1,
        cost_basis_per_share=4.92,
        total_cost_basis=492.0,
        purchase_date=date(2026, 3, 2),
        current_price=4.92,
        asset_type=AssetType.OPTION,
        contract_label="TSLA 3/16/2026 Put $375.00",
    )
    transactions = [
        Transaction(
            activity_date=date(2026, 3, 2),
            instrument="TSLA",
            description="TSLA 3/16/2026 Put $375.00",
            trans_code=TransCode.BTO,
            quantity=1,
            price=4.92,
            amount=-492.0,
            asset_type=AssetType.OPTION,
        )
    ]

    monkeypatch.setattr("main.parse_csv", lambda _: ([option_lot], transactions, [], []))
    monkeypatch.setattr("main.fetch_current_prices", lambda s, fb=None: ({}, []))
    monkeypatch.setattr(
        "main.fetch_option_prices",
        lambda labels, fb=None: ({"TSLA 3/16/2026 Put $375.00": 6.25}, []),
    )
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda l: [])
    monkeypatch.setattr("main.generate_suggestions", lambda **kw: [])
    monkeypatch.setattr("main.detect_wash_sales", lambda *args, **kwargs: [])
    monkeypatch.setattr("main._save_history_best_effort", lambda *args, **kwargs: True)

    response = client.post("/api/portfolio/analyze", files=_make_csv())
    assert response.status_code == 200

    data = response.json()
    assert data["positions"][0]["current_price"] == pytest.approx(6.25)
    assert data["positions"][0]["market_value"] == pytest.approx(625.0)
    assert data["positions"][0]["unrealized_pnl"] == pytest.approx(133.0)


def test_analyze_portfolio_parses_supplemental_1099_pdf(monkeypatch):
    from datetime import date
    import pytest
    from models import AssetType, PortfolioSummary, Position, TaxLot, Transaction, TransCode

    lot = TaxLot(
        symbol="CLSK",
        quantity=1,
        cost_basis_per_share=10.0,
        total_cost_basis=10.0,
        purchase_date=date(2025, 1, 10),
        current_price=8.0,
        asset_type=AssetType.STOCK,
        unrealized_pnl=-2.0,
        unrealized_pnl_pct=-20.0,
        holding_period_days=30,
        is_long_term=False,
    )
    position = Position(
        position_id="CLSK:stock",
        symbol="CLSK",
        quantity=1,
        avg_cost_basis=10.0,
        total_cost_basis=10.0,
        current_price=8.0,
        market_value=8.0,
        unrealized_pnl=-2.0,
        unrealized_pnl_pct=-20.0,
        earliest_purchase_date=date(2025, 1, 10),
        holding_period_days=30,
        is_long_term=False,
        asset_type=AssetType.STOCK,
        tax_lots=[lot],
    )
    summary = PortfolioSummary(
        total_market_value=8.0,
        total_cost_basis=10.0,
        total_unrealized_pnl=-2.0,
        total_unrealized_pnl_pct=-20.0,
        total_harvestable_losses=2.0,
        estimated_tax_savings=0.5,
        positions_count=1,
        lots_with_losses=1,
        lots_with_gains=0,
        wash_sale_flags_count=0,
    )

    monkeypatch.setattr(
        "main.parse_csv",
        lambda _content: (
            [lot],
            [
                Transaction(
                    activity_date=date(2025, 1, 10),
                    instrument="CLSK",
                    trans_code=TransCode.BUY,
                    quantity=1,
                    price=10.0,
                    amount=-10.0,
                    asset_type=AssetType.STOCK,
                )
            ],
            [],
            [],
        ),
    )
    monkeypatch.setattr("main.fetch_current_prices", lambda s, fb=None: ({"CLSK": 8.0}, []))
    monkeypatch.setattr("main.fetch_option_prices", lambda labels, fb=None: ({}, []))
    monkeypatch.setattr("main.compute_lot_metrics", lambda lots: lots)
    monkeypatch.setattr("main.detect_wash_sales", lambda *args, **kwargs: [])
    monkeypatch.setattr("main.adjust_lots_for_wash_sales", lambda lots, flags: lots)
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda lots: [])
    monkeypatch.setattr("main.generate_suggestions", lambda **kwargs: [])
    monkeypatch.setattr("main.aggregate_positions", lambda lots: [position])
    monkeypatch.setattr("main.build_portfolio_summary", lambda positions, suggestions, flags: summary)
    monkeypatch.setattr("main._save_history_best_effort", lambda *args, **kwargs: None)

    response = client.post(
        "/api/portfolio/analyze?tax_year=2025",
        files={
            **_make_csv(),
            "supplemental_1099": _make_supplemental_1099_upload(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supplemental_1099"]["tax_year"] == 2024
    assert payload["supplemental_1099"]["broker_name"] == "Robinhood"
    assert payload["supplemental_1099"]["short_term_proceeds"] == pytest.approx(281823.83)
    assert payload["supplemental_1099"]["short_term_wash_sale_disallowed"] == pytest.approx(17409.64)
    assert payload["supplemental_1099"]["long_term_wash_sale_disallowed"] == pytest.approx(33.16)
    assert "CLSK" in payload["supplemental_1099"]["matched_symbols"]


def test_parse_supplemental_1099_summary_delegates_to_parser(monkeypatch):
    from main import _parse_supplemental_1099_summary
    from models import Supplemental1099Summary

    captured: dict[str, object] = {}

    def fake_parser(pdf_bytes, *, current_symbols, filename, expected_previous_year):
        captured["pdf_bytes"] = pdf_bytes
        captured["current_symbols"] = current_symbols
        captured["filename"] = filename
        captured["expected_previous_year"] = expected_previous_year
        return Supplemental1099Summary(source_filename=filename, tax_year=expected_previous_year)

    monkeypatch.setattr("main.parse_robinhood_1099_pdf", fake_parser)

    summary = _parse_supplemental_1099_summary(
        b"pdf-bytes",
        "prior.pdf",
        {"CLSK", "TSLL"},
        2024,
    )

    assert summary.source_filename == "prior.pdf"
    assert captured == {
        "pdf_bytes": b"pdf-bytes",
        "current_symbols": {"CLSK", "TSLL"},
        "filename": "prior.pdf",
        "expected_previous_year": 2024,
    }


def test_maybe_parse_supplemental_1099_returns_warning_for_year_mismatch(monkeypatch):
    import asyncio

    from main import _maybe_parse_supplemental_1099
    from models import Supplemental1099Summary

    class DummyUpload:
        filename = "prior.pdf"
        content_type = "application/pdf"

        async def read(self, size=-1):
            await asyncio.sleep(0)
            return b"pdf"

    monkeypatch.setattr(
        "main._parse_supplemental_1099_summary",
        lambda *_args, **_kwargs: Supplemental1099Summary(
            source_filename="prior.pdf",
            broker_name="Robinhood",
            tax_year=2022,
        ),
    )

    summary, warnings, _pdf = asyncio.run(
        _maybe_parse_supplemental_1099(DummyUpload(), {"CLSK"}, 2024)
    )

    assert summary is not None
    assert summary.tax_year == 2022
    assert warnings == [
        "The supplemental 1099 PDF was parsed successfully, but its tax year does not match the expected prior year for this analysis."
    ]


def test_maybe_parse_supplemental_1099_ignores_unparseable_pdf(monkeypatch):
    import asyncio

    from main import _maybe_parse_supplemental_1099

    class DummyUpload:
        filename = "broken.pdf"
        content_type = "application/pdf"

        async def read(self, size=-1):
            await asyncio.sleep(0)
            return b"broken"

    def fail_parse(*_args, **_kwargs):
        raise ValueError("bad pdf")

    monkeypatch.setattr("main._parse_supplemental_1099_summary", fail_parse)

    summary, warnings, _pdf = asyncio.run(
        _maybe_parse_supplemental_1099(DummyUpload(), {"CLSK"}, 2024)
    )

    assert summary is None
    assert warnings == [
        "Supplemental 1099 PDF could not be parsed and was ignored for this analysis."
    ]


def test_maybe_parse_supplemental_1099_rejects_non_pdf_content_type():
    import asyncio

    from main import _maybe_parse_supplemental_1099

    class DummyUpload:
        filename = "document.xlsx"
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        async def read(self, size=-1):
            await asyncio.sleep(0)
            return b"not-a-pdf"

    summary, warnings, _pdf = asyncio.run(
        _maybe_parse_supplemental_1099(DummyUpload(), {"CLSK"}, 2024)
    )

    assert summary is None
    assert len(warnings) == 1
    assert "PDF" in warnings[0]


def test_maybe_parse_supplemental_1099_rejects_oversized_pdf(monkeypatch):
    import asyncio

    from main import _maybe_parse_supplemental_1099, _MAX_SUPPLEMENTAL_PDF_BYTES

    class DummyUpload:
        filename = "huge.pdf"
        content_type = "application/pdf"

        async def read(self, size=-1):
            await asyncio.sleep(0)
            # Return more than the allowed maximum to trigger the size guard
            return b"x" * (_MAX_SUPPLEMENTAL_PDF_BYTES + 1)

    summary, warnings, _pdf = asyncio.run(
        _maybe_parse_supplemental_1099(DummyUpload(), {"CLSK"}, 2024)
    )

    assert summary is None
    assert len(warnings) == 1
    assert "20 MB" in warnings[0]


def test_maybe_parse_supplemental_1099_ignores_empty_pdf(monkeypatch):
    import asyncio

    from main import _maybe_parse_supplemental_1099
    from models import Supplemental1099Summary

    class DummyUpload:
        filename = "empty.pdf"
        content_type = "application/pdf"

        async def read(self, size=-1):
            await asyncio.sleep(0)
            return b"%PDF-1.4 empty"

    monkeypatch.setattr(
        "main._parse_supplemental_1099_summary",
        lambda *_args, **_kwargs: Supplemental1099Summary(source_filename="empty.pdf"),
    )

    summary, warnings, _pdf = asyncio.run(
        _maybe_parse_supplemental_1099(DummyUpload(), {"CLSK"}, 2024)
    )

    assert summary is None
    assert warnings == [
        "Supplemental 1099 PDF could not be parsed and was ignored for this analysis."
    ]


def test_maybe_parse_supplemental_1099_accepts_pdf_filename_without_content_type(monkeypatch):
    import asyncio

    from main import _maybe_parse_supplemental_1099
    from models import Supplemental1099Summary

    class DummyUpload:
        filename = "prior-year.pdf"
        content_type = ""

        async def read(self, size=-1):
            await asyncio.sleep(0)
            return b"%PDF-1.4"

    monkeypatch.setattr(
        "main._parse_supplemental_1099_summary",
        lambda *_args, **_kwargs: Supplemental1099Summary(
            source_filename="prior-year.pdf",
            broker_name="Robinhood",
            tax_year=2024,
            short_term_proceeds=100.0,
        ),
    )

    summary, warnings, _pdf = asyncio.run(
        _maybe_parse_supplemental_1099(DummyUpload(), {"CLSK"}, 2024)
    )

    assert summary is not None
    assert summary.tax_year == 2024
    assert warnings == []


def test_maybe_parse_same_year_1099_does_not_warn(monkeypatch):
    import asyncio

    from main import _maybe_parse_supplemental_1099
    from models import Supplemental1099Summary

    class DummyUpload:
        filename = "2024-1099.pdf"
        content_type = "application/pdf"

        async def read(self, size=-1):
            await asyncio.sleep(0)
            return b"%PDF-1.4"

    monkeypatch.setattr(
        "main._parse_supplemental_1099_summary",
        lambda *_args, **_kwargs: Supplemental1099Summary(
            source_filename="2024-1099.pdf",
            broker_name="Robinhood",
            tax_year=2024,
            short_term_proceeds=100.0,
        ),
    )

    summary, warnings, _pdf = asyncio.run(
        _maybe_parse_supplemental_1099(DummyUpload(), {"AMD"}, 2024)
    )

    assert summary is not None
    assert summary.tax_year == 2024
    assert warnings == []


def test_maybe_parse_unknown_1099_year_is_not_mismatch(monkeypatch):
    import asyncio

    from main import _maybe_parse_supplemental_1099
    from models import Supplemental1099Summary

    class DummyUpload:
        filename = "unknown.pdf"
        content_type = "application/pdf"

        async def read(self, size=-1):
            await asyncio.sleep(0)
            return b"%PDF-1.4"

    monkeypatch.setattr(
        "main._parse_supplemental_1099_summary",
        lambda *_args, **_kwargs: Supplemental1099Summary(
            source_filename="unknown.pdf",
            broker_name="Robinhood",
            tax_year=None,
            short_term_proceeds=1200.0,
            short_term_cost_basis=1500.0,
            short_term_wash_sale_disallowed=300.0,
            short_term_net_gain=0.0,
        ),
    )

    summary, warnings, _pdf = asyncio.run(
        _maybe_parse_supplemental_1099(DummyUpload(), {"AMD"}, 2024)
    )

    assert summary is not None
    assert summary.tax_year is None
    assert warnings == []
    assert not any("does not match" in warning for warning in warnings)


def test_analyze_same_year_1099_compare_vs_2026_sample_mismatch(monkeypatch):
    """2024 CSV + 2024 fixture is a same-year compare; 2026 sample is not."""
    from year_close_packet import (
        COMPARE_TITLE,
        HARVEST_TITLE,
        build_packet_payload,
        render_packet_pdf,
    )
    from pypdf import PdfReader
    from io import BytesIO

    _stub_analyze_network(monkeypatch)

    repo = Path(__file__).resolve().parents[2]
    csv_2024 = (Path(__file__).resolve().parent / "fixtures" / "year_close_2024.csv").read_bytes()
    sample_2026 = (repo / "client" / "public" / "sample-robinhood-transactions.csv").read_bytes()
    pdf_upload = _make_supplemental_1099_upload()

    same_year = client.post(
        "/api/portfolio/analyze?tax_year=2024",
        files={
            "file": ("year_close_2024.csv", csv_2024, "text/csv"),
            "supplemental_1099": pdf_upload,
        },
    )
    assert same_year.status_code == 200, same_year.text
    same_body = same_year.json()
    assert same_body["tax_profile"]["tax_year"] == 2024
    assert same_body["supplemental_1099"]["tax_year"] == 2024
    public_report = same_body["lot_match_report"]
    assert public_report is not None
    assert public_report["matched"] == []
    assert public_report["gap"] == []
    assert public_report["unmatched"] == []
    assert (
        public_report["matched_count"]
        + public_report["gap_count"]
        + public_report["unmatched_count"]
        >= 1
    )

    from year_close_packet import get_payload

    stored = get_payload(same_body["analysis_id"])
    same_payload = stored or build_packet_payload(same_body)
    assert same_payload["same_year_compare"] is True
    assert same_payload["form_1099_tax_year"] == 2024
    assert same_payload["analysis_tax_year"] == 2024
    # year_close_2024.csv is a $300 ST loss + $300 wash; fold into 1099-style net.
    assert same_payload["export_short_term_net"] == 0.0
    assert same_payload["export_wash_sale_disallowed"] == 300.0
    stored_report = same_payload.get("lot_match_report") or {}
    assert any(
        row["status"] in ("matched", "matched_settlement_gap", "1099_only", "csv_only")
        for section in ("matched", "gap", "unmatched")
        for row in stored_report.get(section) or []
    )
    same_pdf = render_packet_pdf(same_payload)
    same_reader = PdfReader(BytesIO(same_pdf))
    assert len(same_reader.pages) >= 2
    same_text = "\n".join((page.extract_text() or "") for page in same_reader.pages)
    assert COMPARE_TITLE in same_text
    assert HARVEST_TITLE in same_text
    assert same_payload.get("harvest_opportunities")
    assert "Broker 1099 (settlement date)" in same_text
    assert "This export (trade date)" in same_text
    assert "$-300.00" not in same_text
    assert "Lot-matched 1099-B" in same_text

    mismatch = client.post(
        "/api/portfolio/analyze?tax_year=2026",
        files={
            "file": ("sample-robinhood-transactions.csv", sample_2026, "text/csv"),
            "supplemental_1099": _make_supplemental_1099_upload(),
        },
    )
    assert mismatch.status_code == 200, mismatch.text
    mismatch_body = mismatch.json()
    assert mismatch_body["tax_profile"]["tax_year"] == 2026
    assert mismatch_body["supplemental_1099"]["tax_year"] == 2024
    mismatch_payload = build_packet_payload(mismatch_body)
    assert mismatch_payload["same_year_compare"] is False
    assert mismatch_payload.get("harvest_opportunities")
    mismatch_pdf = render_packet_pdf(mismatch_payload)
    mismatch_reader = PdfReader(BytesIO(mismatch_pdf))
    assert len(mismatch_reader.pages) == 2
    mismatch_text = "\n".join(
        (page.extract_text() or "") for page in mismatch_reader.pages
    )
    assert HARVEST_TITLE in mismatch_text
    assert COMPARE_TITLE not in mismatch_text
    assert "previous-year supplement" in mismatch_text


def test_analyze_2026_sample_csv_and_1099_is_same_year_compare(monkeypatch):
    """Open-the-sample pair: 2026 CSV + 2026 1099 is a same-year compare."""
    import pytest
    from year_close_packet import (
        COMPARE_TITLE,
        HARVEST_TITLE,
        build_packet_payload,
        render_packet_pdf,
    )
    from pypdf import PdfReader
    from io import BytesIO

    _stub_analyze_network(monkeypatch)

    repo = Path(__file__).resolve().parents[2]
    sample_csv = (repo / "client" / "public" / "sample-robinhood-transactions.csv").read_bytes()
    sample_1099 = (repo / "client" / "public" / "sample-robinhood-1099-2026.pdf").read_bytes()
    fixture_2024 = _make_supplemental_1099_upload()

    same_year = client.post(
        "/api/portfolio/analyze?tax_year=2026",
        files={
            "file": ("sample-robinhood-transactions.csv", sample_csv, "text/csv"),
            "supplemental_1099": (
                "sample-robinhood-1099-2026.pdf",
                sample_1099,
                "application/pdf",
            ),
        },
    )
    assert same_year.status_code == 200, same_year.text
    same_body = same_year.json()
    assert same_body["tax_profile"]["tax_year"] == 2026
    summary = same_body["supplemental_1099"]
    assert summary["tax_year"] == 2026
    assert summary["broker_name"] == "Robinhood"
    assert summary["short_term_proceeds"] == pytest.approx(8315.00)
    assert summary["short_term_cost_basis"] == pytest.approx(6540.00)
    assert summary["short_term_wash_sale_disallowed"] == pytest.approx(924.00)
    assert summary["short_term_net_gain"] == pytest.approx(2699.00)
    assert summary["long_term_net_gain"] == pytest.approx(0.00)
    assert summary["matched_symbols"] == ["AMD", "NVDA", "TSLA"]
    assert "SPX" in summary["referenced_symbols"]
    public_report = same_body["lot_match_report"]
    assert public_report is not None
    assert {row["symbol"] for row in public_report["gap"]} >= {"NVDA", "TSLA", "AMD"}
    assert all(row["status"] == "matched_settlement_gap" for row in public_report["gap"])
    assert any(
        row["symbol"] == "SPX" and row["status"] == "1099_only"
        for row in public_report["unmatched"]
    )
    assert public_report["gap_count"] >= 3
    assert public_report["unmatched_count"] >= 1
    assert same_body["packet_unlocked"] is False
    assert same_body["sample_run"] is True

    from year_close_packet import get_payload

    same_payload = get_payload(same_body["analysis_id"]) or build_packet_payload(same_body)
    assert same_payload["same_year_compare"] is True
    assert same_payload["form_1099_tax_year"] == 2026
    assert same_payload["analysis_tax_year"] == 2026
    # Sample realized is -$924; folding CSV wash ($924) yields 1099-style ST net $0.
    assert same_body["summary"]["realized_summary"]["net_st"] == pytest.approx(-924.00)
    assert same_payload["export_short_term_net"] == pytest.approx(0.00)
    assert same_payload["export_wash_sale_disallowed"] == pytest.approx(924.00)
    assert same_payload["short_term_net_gain"] == pytest.approx(2699.00)
    report = same_payload.get("lot_match_report") or {}
    assert {row["symbol"] for row in report.get("gap") or []} >= {"NVDA", "TSLA", "AMD"}
    assert all(row["status"] == "matched_settlement_gap" for row in report.get("gap") or [])
    assert any(
        row["symbol"] == "SPX" and row["status"] == "1099_only"
        for row in report.get("unmatched") or []
    )
    same_pdf = render_packet_pdf(same_payload)
    same_text = "\n".join(
        (page.extract_text() or "")
        for page in PdfReader(BytesIO(same_pdf)).pages
    )
    assert COMPARE_TITLE in same_text
    assert HARVEST_TITLE in same_text
    assert same_payload.get("harvest_opportunities")
    assert "Broker 1099 (settlement date)" in same_text
    assert "$2,699.00" in same_text
    assert "Lot-matched 1099-B" in same_text
    assert "Matched (" in same_text
    assert "Gap (" in same_text
    assert "Unmatched (" in same_text
    assert "matched_settlement_gap" in same_text
    assert "1099_only" in same_text
    assert "NVDA" in same_text

    mismatch = client.post(
        "/api/portfolio/analyze?tax_year=2026",
        files={
            "file": ("sample-robinhood-transactions.csv", sample_csv, "text/csv"),
            "supplemental_1099": fixture_2024,
        },
    )
    assert mismatch.status_code == 200, mismatch.text
    mismatch_body = mismatch.json()
    assert mismatch_body["supplemental_1099"]["tax_year"] == 2024
    mismatch_payload = build_packet_payload(mismatch_body)
    assert mismatch_payload["same_year_compare"] is False
    assert mismatch_payload.get("harvest_opportunities")
    mismatch_pdf = render_packet_pdf(mismatch_payload)
    mismatch_reader = PdfReader(BytesIO(mismatch_pdf))
    assert len(mismatch_reader.pages) == 2
    mismatch_text = "\n".join(
        (page.extract_text() or "") for page in mismatch_reader.pages
    )
    assert HARVEST_TITLE in mismatch_text
    assert COMPARE_TITLE not in mismatch_text
    assert "previous-year supplement" in mismatch_text


def test_unpaid_analyze_lot_match_report_is_counts_only(monkeypatch):
    """Unpaid non-sample analyze keeps teaser counts and redacts lot rows."""
    from year_close_packet import get_payload

    _stub_analyze_network(monkeypatch)
    csv_2024 = (
        Path(__file__).resolve().parent / "fixtures" / "year_close_2024.csv"
    ).read_bytes()
    response = client.post(
        "/api/portfolio/analyze?tax_year=2024",
        files={
            "file": ("year_close_2024.csv", csv_2024, "text/csv"),
            "supplemental_1099": _make_supplemental_1099_upload(),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["packet_unlocked"] is False
    assert body.get("sample_run") is False
    report = body["lot_match_report"]
    assert report["matched"] == []
    assert report["gap"] == []
    assert report["unmatched"] == []
    assert (
        report["matched_count"] + report["gap_count"] + report["unmatched_count"]
        >= 1
    )
    public_json = response.text
    assert "matched_settlement_gap" not in public_json
    assert "1099_only" not in public_json
    assert "date_sold_1099" not in public_json
    lots = (body.get("supplemental_1099") or {}).get("lots") or []
    assert lots == []
    stored = get_payload(body["analysis_id"])
    assert stored is not None
    stored_report = stored["lot_match_report"]
    assert any(
        stored_report.get(section)
        for section in ("matched", "gap", "unmatched")
    )


def test_guest_non_sample_analyze_lot_match_report_is_counts_only(monkeypatch):
    """Unauthenticated non-sample analyze must not leak lot rows."""
    _stub_analyze_network(monkeypatch)
    csv_2024 = (
        Path(__file__).resolve().parent / "fixtures" / "year_close_2024.csv"
    ).read_bytes()
    main.app.dependency_overrides.pop(get_optional_user, None)
    try:
        response = client.post(
            "/api/portfolio/analyze?tax_year=2024",
            files={
                "file": ("year_close_2024.csv", csv_2024, "text/csv"),
                "supplemental_1099": _make_supplemental_1099_upload(),
            },
        )
    finally:
        main.app.dependency_overrides[get_optional_user] = mock_get_current_user
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["packet_unlocked"] is False
    assert body.get("sample_run") is False
    report = body["lot_match_report"]
    assert report["matched"] == []
    assert report["gap"] == []
    assert report["unmatched"] == []
    assert (
        report["matched_count"] + report["gap_count"] + report["unmatched_count"]
        >= 1
    )
    assert "matched_settlement_gap" not in response.text
    assert "date_sold_1099" not in response.text
    assert (body.get("supplemental_1099") or {}).get("lots") == []


def test_paid_year_analyze_includes_lot_match_rows(monkeypatch):
    """A paid tax year keeps full lot rows on later analyze responses."""
    from year_close_packet import mark_paid

    _stub_analyze_network(monkeypatch)
    repo = Path(__file__).resolve().parents[2]
    sample_csv = (repo / "client" / "public" / "sample-robinhood-transactions.csv").read_bytes()
    sample_1099 = (repo / "client" / "public" / "sample-robinhood-1099-2026.pdf").read_bytes()
    files = {
        "file": ("sample-robinhood-transactions.csv", sample_csv, "text/csv"),
        "supplemental_1099": (
            "sample-robinhood-1099-2026.pdf",
            sample_1099,
            "application/pdf",
        ),
    }
    first = client.post("/api/portfolio/analyze?tax_year=2026", files=files)
    assert first.status_code == 200, first.text
    first_id = first.json()["analysis_id"]
    mark_paid(first_id, "cs_test_year_grant", user_id="test-user-123")

    second = client.post("/api/portfolio/analyze?tax_year=2026", files=files)
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["packet_unlocked"] is True
    report = body["lot_match_report"]
    assert report["gap"]
    assert {row["symbol"] for row in report["gap"]} >= {"NVDA", "TSLA", "AMD"}
    assert any(row["symbol"] == "SPX" for row in report["unmatched"])
    assert body["supplemental_1099"]["lots"]


def test_trusted_in_app_sample_skips_yahoo_and_still_returns_positions(monkeypatch):
    """Open sample must finish even when Yahoo is blocked."""
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda lots: [])
    monkeypatch.setattr("main._save_history_best_effort", lambda *args, **kwargs: None)
    monkeypatch.setattr("main.get_latest_activity_book", lambda uid, client=None: None)
    monkeypatch.setattr(
        "main.load_activity_book_for_merge",
        lambda user_id, client=None: db.ActivityBookLookup(),
    )
    monkeypatch.setattr("main.upsert_activity_book", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "main.lookup_packet_grant_for_tax_year",
        lambda *args, **kwargs: (None, True),
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("yfinance should not run for the in-app sample")

    monkeypatch.setattr("price_service._download_yfinance_prices", boom)
    monkeypatch.setattr("price_service._fetch_grouped_option_prices", boom)
    monkeypatch.setattr("main._process_ai_suggestions", boom)
    monkeypatch.setattr("main._try_get_ai_suggestions", boom)

    repo = Path(__file__).resolve().parents[2]
    sample_csv = (repo / "client" / "public" / "sample-robinhood-transactions.csv").read_bytes()
    sample_1099 = (repo / "client" / "public" / "sample-robinhood-1099-2026.pdf").read_bytes()
    response = client.post(
        "/api/portfolio/analyze?filing_status=single&estimated_income=75000&tax_year=2026",
        files={
            "file": ("sample-robinhood-transactions.csv", sample_csv, "text/csv"),
            "supplemental_1099": (
                "sample-robinhood-1099-2026.pdf",
                sample_1099,
                "application/pdf",
            ),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sample_run"] is True
    assert body["positions"]
    assert body["tax_profile"]["tax_year"] == 2026


def test_trusted_sample_fixture_prices_win_over_cache_and_lot_fills():
    from datetime import date

    import pytest
    from ledger import SAMPLE_FIXTURE_PRICES
    from models import AssetType, TaxLot
    from price_service import _set_cached_price, clear_cache

    clear_cache()
    _set_cached_price("AAPL", 1.0)
    lot = TaxLot(
        symbol="AAPL",
        quantity=10,
        cost_basis_per_share=100.0,
        total_cost_basis=1000.0,
        purchase_date=date(2025, 1, 2),
        current_price=2.0,
        asset_type=AssetType.STOCK,
    )
    lots = main._apply_live_prices_to_tax_lots(
        [lot],
        [],
        allow_network=False,
        fixture_prices=SAMPLE_FIXTURE_PRICES,
    )
    assert lots[0].current_price == pytest.approx(SAMPLE_FIXTURE_PRICES["AAPL"])
    clear_cache()


def test_guest_sample_analyze_includes_lot_match_rows_without_unlocking(monkeypatch):
    """In-app sample keeps lot rows so the desk table can render; download stays locked."""
    _stub_analyze_network(monkeypatch)
    repo = Path(__file__).resolve().parents[2]
    sample_csv = (repo / "client" / "public" / "sample-robinhood-transactions.csv").read_bytes()
    sample_1099 = (repo / "client" / "public" / "sample-robinhood-1099-2026.pdf").read_bytes()
    main.app.dependency_overrides.pop(get_optional_user, None)
    try:
        response = client.post(
            "/api/portfolio/analyze?tax_year=2026",
            files={
                "file": ("sample-robinhood-transactions.csv", sample_csv, "text/csv"),
                "supplemental_1099": (
                    "sample-robinhood-1099-2026.pdf",
                    sample_1099,
                    "application/pdf",
                ),
            },
        )
    finally:
        main.app.dependency_overrides[get_optional_user] = mock_get_current_user
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["packet_unlocked"] is False
    assert body["sample_run"] is True
    report = body["lot_match_report"]
    assert {row["symbol"] for row in report["gap"]} >= {"NVDA", "TSLA", "AMD"}
    assert any(row["symbol"] == "SPX" for row in report["unmatched"])
    assert report["gap_count"] >= 3


def test_trusted_sample_bytes_unlock_even_with_attacker_filenames(monkeypatch):
    """Real in-app fixture bytes unlock lot rows; client filenames are ignored."""
    _stub_analyze_network(monkeypatch)
    repo = Path(__file__).resolve().parents[2]
    sample_csv = (repo / "client" / "public" / "sample-robinhood-transactions.csv").read_bytes()
    sample_1099 = (repo / "client" / "public" / "sample-robinhood-1099-2026.pdf").read_bytes()
    response = client.post(
        "/api/portfolio/analyze?tax_year=2026",
        files={
            "file": ("stolen.csv", sample_csv, "text/csv"),
            "supplemental_1099": ("stolen.pdf", sample_1099, "application/pdf"),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["packet_unlocked"] is False
    assert body["sample_run"] is True
    report = body["lot_match_report"]
    assert {row["symbol"] for row in report["gap"]} >= {"NVDA", "TSLA", "AMD"}
    assert any(
        row["symbol"] == "SPX" and row["status"] == "1099_only"
        for row in report["unmatched"]
    )


def test_sample_filenames_with_non_fixture_bytes_stay_counts_only(monkeypatch):
    """Sample filenames with different bytes must not leak lot rows."""
    _stub_analyze_network(monkeypatch)
    csv_2024 = (
        Path(__file__).resolve().parent / "fixtures" / "year_close_2024.csv"
    ).read_bytes()
    response = client.post(
        "/api/portfolio/analyze?tax_year=2024",
        files={
            "file": ("sample-robinhood-transactions.csv", csv_2024, "text/csv"),
            "supplemental_1099": (
                "sample-robinhood-1099-2026.pdf",
                _make_supplemental_1099_upload()[1],
                "application/pdf",
            ),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["packet_unlocked"] is False
    assert body.get("sample_run") is False
    report = body["lot_match_report"]
    assert report["matched"] == []
    assert report["gap"] == []
    assert report["unmatched"] == []
    assert "date_sold_1099" not in response.text
    assert "matched_settlement_gap" not in response.text


def test_renamed_real_csv_does_not_unlock_lot_rows(monkeypatch):
    """A real user CSV renamed as the sample file cannot unlock unless hashes match."""
    _stub_analyze_network(monkeypatch)
    csv_2024 = (
        Path(__file__).resolve().parent / "fixtures" / "year_close_2024.csv"
    ).read_bytes()
    repo = Path(__file__).resolve().parents[2]
    sample_1099 = (repo / "client" / "public" / "sample-robinhood-1099-2026.pdf").read_bytes()
    response = client.post(
        "/api/portfolio/analyze?tax_year=2026",
        files={
            "file": ("sample-robinhood-transactions.csv", csv_2024, "text/csv"),
            "supplemental_1099": (
                "sample-robinhood-1099-2026.pdf",
                sample_1099,
                "application/pdf",
            ),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["packet_unlocked"] is False
    assert body.get("sample_run") is False
    report = body.get("lot_match_report")
    if report is not None:
        assert report["matched"] == []
        assert report["gap"] == []
        assert report["unmatched"] == []
    assert "date_sold_1099" not in response.text


def test_unpaid_analyze_history_save_redacts_lot_rows(monkeypatch):
    """History persist uses the public payload so restore cannot leak lot rows."""
    saved = {}

    def capture_save(_user_id, _filename, _summary, result):
        saved["result"] = result

    _stub_analyze_network(monkeypatch)
    monkeypatch.setattr("main._save_history_best_effort", capture_save)
    csv_2024 = (
        Path(__file__).resolve().parent / "fixtures" / "year_close_2024.csv"
    ).read_bytes()
    response = client.post(
        "/api/portfolio/analyze?tax_year=2024",
        files={
            "file": ("year_close_2024.csv", csv_2024, "text/csv"),
            "supplemental_1099": _make_supplemental_1099_upload(),
        },
    )
    assert response.status_code == 200, response.text
    public = saved["result"]
    report = public.lot_match_report
    assert report is not None
    assert report.matched == []
    assert report.gap == []
    assert report.unmatched == []
    dump = public.model_dump(mode="json")
    assert "date_sold_1099" not in str(dump.get("lot_match_report"))
    assert dump.get("supplemental_1099", {}).get("lots") == []


def test_analyze_unknown_1099_year_is_not_mismatch_or_same_year_compare(monkeypatch):
    from io import BytesIO

    from pypdf import PdfReader

    from models import Supplemental1099Summary
    from year_close_packet import (
        COMPARE_TITLE,
        HARVEST_TITLE,
        UNKNOWN_1099_YEAR_COPY,
        build_packet_payload,
        packet_plain_text,
        render_packet_pdf,
    )

    _stub_analyze_network(monkeypatch)
    monkeypatch.setattr(
        "main._parse_supplemental_1099_summary",
        lambda *_args, **_kwargs: Supplemental1099Summary(
            source_filename="unknown.pdf",
            broker_name="Robinhood",
            tax_year=None,
            short_term_proceeds=1200.0,
            short_term_cost_basis=1500.0,
            short_term_wash_sale_disallowed=300.0,
            short_term_net_gain=0.0,
        ),
    )
    csv_2024 = (Path(__file__).resolve().parent / "fixtures" / "year_close_2024.csv").read_bytes()
    response = client.post(
        "/api/portfolio/analyze?tax_year=2024",
        files={
            "file": ("year_close_2024.csv", csv_2024, "text/csv"),
            "supplemental_1099": ("unknown.pdf", b"%PDF-1.4 unknown", "application/pdf"),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["supplemental_1099"]["tax_year"] is None
    assert not any("does not match" in warning for warning in body.get("warnings") or [])
    payload = build_packet_payload(body)
    assert payload["same_year_compare"] is False
    assert payload["unknown_1099_year"] is True
    text = packet_plain_text(payload)
    assert UNKNOWN_1099_YEAR_COPY in text
    assert "previous-year supplement" not in text
    assert payload.get("harvest_opportunities")
    pdf_text = "\n".join(
        (page.extract_text() or "")
        for page in PdfReader(BytesIO(render_packet_pdf(payload))).pages
    )
    assert HARVEST_TITLE in pdf_text
    assert COMPARE_TITLE not in pdf_text
    assert "could not be determined" in pdf_text
    reader = PdfReader(BytesIO(render_packet_pdf(payload)))
    assert len(reader.pages) == 2


def test_get_prices_empty_symbols():
    """GET /api/prices with empty symbols returns 400."""
    response = client.get("/api/prices?symbols=")
    assert response.status_code == 400
    assert "No symbols" in response.json()["detail"]


def test_get_prices_missing_param():
    """GET /api/prices without symbols param returns 422."""
    response = client.get("/api/prices")
    assert response.status_code == 422


# ---------- Tax Brackets Endpoint ----------


def test_get_tax_brackets_defaults():
    """GET /api/tax-brackets returns brackets with default params."""
    response = client.get("/api/tax-brackets")
    assert response.status_code == 200
    data = response.json()
    # Should return bracket data (structure depends on get_tax_brackets_summary)
    assert data is not None


def test_get_tax_brackets_custom_params():
    """GET /api/tax-brackets with custom params returns brackets."""
    response = client.get(
        "/api/tax-brackets?year=2025&filing_status=married_filing_jointly&income=200000"
    )
    assert response.status_code == 200
    data = response.json()
    assert data is not None


def test_get_tax_brackets_invalid_filing_status():
    """GET /api/tax-brackets with invalid filing status falls back to single."""
    response = client.get("/api/tax-brackets?filing_status=INVALID")
    assert response.status_code == 200


def test_get_tax_brackets_invalid_year():
    """GET /api/tax-brackets with year out of range returns 422."""
    response = client.get("/api/tax-brackets?year=2020")
    assert response.status_code == 422


def test_save_history_best_effort_dict_fallback():
    """_save_history_best_effort falls back to dict() when model_dump is missing."""
    from unittest.mock import patch, MagicMock

    # Create mock objects without model_dump attribute
    result_obj = MagicMock()
    delattr(result_obj, "model_dump")
    summary_obj = MagicMock()
    delattr(summary_obj, "model_dump")

    with patch("main.save_analysis_history", return_value={"id": "test"}):
        # Should not raise
        main._save_history_best_effort(
            user_id="user1",
            filename="test.csv",
            result=result_obj,
            summary=summary_obj,
        )


def test_save_history_best_effort_exception():
    """_save_history_best_effort handles exceptions gracefully."""
    from unittest.mock import patch, MagicMock

    result_obj = MagicMock()
    result_obj.model_dump = MagicMock(return_value={"test": "data"})
    summary_obj = MagicMock()
    summary_obj.model_dump = MagicMock(return_value={"test": "data"})

    with patch("main.save_analysis_history", side_effect=Exception("db error")):
        # Should not raise
        main._save_history_best_effort(
            user_id="user1",
            filename="test.csv",
            result=result_obj,
            summary=summary_obj,
        )


def test_save_history_best_effort_reraises_missing_schema():
    """A missing result column is not swallowed as a soft history failure."""
    from unittest.mock import patch, MagicMock
    import pytest

    result_obj = MagicMock()
    result_obj.model_dump = MagicMock(return_value={"analysis_id": "analysis-1"})
    summary_obj = MagicMock()
    summary_obj.model_dump = MagicMock(return_value={"positions_count": 1})

    with patch(
        "main.save_analysis_history",
        side_effect=main.AnalysisSchemaError(
            "missing result column; apply server/migrations"
        ),
    ):
        with pytest.raises(main.AnalysisSchemaError, match="server/migrations"):
            main._save_history_best_effort(
                user_id="user1",
                filename="test.csv",
                result=result_obj,
                summary=summary_obj,
            )


def test_analyze_reports_missing_schema(monkeypatch):
    """Signed-in analyze fails with the schema message instead of a generic 500."""

    async def missing_schema(*_args, **_kwargs):
        raise main.AnalysisSchemaError(
            "Cannot save analysis history because portfolio_analyses is missing "
            "the result column. Apply server/migrations in filename order."
        )

    monkeypatch.setattr(main, "_run_portfolio_analysis", missing_schema)
    response = client.post(
        "/api/portfolio/analyze",
        files={"file": ("t.csv", "symbol,qty\nAAPL,1\n", "text/csv")},
    )
    assert response.status_code == 503
    assert "server/migrations" in response.json()["detail"]
    assert "result" in response.json()["detail"]


def test_persist_guest_analysis_reports_missing_schema(monkeypatch):
    """POST /api/portfolio/history surfaces a missing result column."""

    def missing_schema(**_kwargs):
        raise main.AnalysisSchemaError(
            "Cannot save analysis history because portfolio_analyses is missing "
            "the result column. Apply server/migrations in filename order."
        )

    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    monkeypatch.setattr(main, "save_analysis_history", missing_schema)
    response = client.post(
        "/api/portfolio/history",
        json={
            "filename": "guest.csv",
            "analysis": {"analysis_id": "guest-schema", "summary": {}},
        },
    )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "server/migrations" in detail
    assert "result" in detail


def test_validate_user_id_invalid_format():
    """validate_user_id raises HTTPException 400 for unsafe input (line 141)."""
    import pytest
    from fastapi import HTTPException as FastAPIHTTPException

    with pytest.raises(FastAPIHTTPException) as exc_info:
        main.validate_user_id("!!invalid user_id!!")
    assert exc_info.value.status_code == 400
    assert "Invalid user_id format" in exc_info.value.detail


def test_get_portfolio_history_db_connection_failed(monkeypatch):
    """Returns 500 when get_supabase returns None (DB unavailable)."""
    monkeypatch.setattr("main.get_supabase", lambda: None)
    response = client.get("/api/portfolio/history")
    assert response.status_code == 500
    assert response.json()["detail"] == "Database connection failed"


def test_get_portfolio_analysis_db_connection_failed(monkeypatch):
    """Returns 500 when get_supabase returns None (DB unavailable)."""
    monkeypatch.setattr("main.get_supabase", lambda: None)
    response = client.get("/api/portfolio/analysis/some-analysis-id")
    assert response.status_code == 500
    assert response.json()["detail"] == "Database connection failed"


def test_lifespan_startup_no_stripe(monkeypatch):
    """Lifespan startup logs warning when STRIPE_SECRET_KEY is absent (lines 70-83)."""
    monkeypatch.setattr("main.STRIPE_SECRET_KEY", None)
    with TestClient(main.app) as temp_client:
        resp = temp_client.get("/health")
        assert resp.status_code == 200


def test_lifespan_startup_with_stripe(monkeypatch):
    """Lifespan startup logs success when STRIPE_SECRET_KEY is set (else branch, lines 70-83)."""
    monkeypatch.setattr("main.STRIPE_SECRET_KEY", "sk_test_fake_key")
    with TestClient(main.app) as temp_client:
        resp = temp_client.get("/health")
        assert resp.status_code == 200


class _ApiBookQuery:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.op = "select"
        self.filters = []
        self.order_col = None
        self.order_desc = False
        self.range_bounds = None
        self.limit_n = None
        self.payload = None
        self.on_conflict = None

    def select(self, *_args, **_kwargs):
        return self

    def insert(self, row):
        self.op = "insert"
        self.payload = row
        return self

    def upsert(self, row, on_conflict=None, **_kwargs):
        self.op = "upsert"
        self.payload = row
        self.on_conflict = on_conflict
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, col, desc=False, **_kwargs):
        self.order_col = col
        self.order_desc = bool(desc)
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def range(self, start, end):
        self.range_bounds = (start, end)
        return self

    def execute(self):
        if (
            self.op == "select"
            and self.table == "portfolio_activity_books"
            and self.client.select_error is not None
        ):
            raise self.client.select_error
        self.client.queries.append(
            {"table": self.table, "op": self.op, "filters": list(self.filters)}
        )
        rows = self.client.tables.setdefault(self.table, [])
        if self.op in {"upsert", "insert"}:
            stored = dict(self.payload or {})
            if self.op == "upsert":
                key = self.on_conflict or "user_id"
                match = stored.get(key)
                replaced = False
                for index, row in enumerate(rows):
                    if row.get(key) == match:
                        rows[index] = stored
                        replaced = True
                        break
                if not replaced:
                    rows.append(stored)
                self.client.upserts.append(stored)
            else:
                rows.append(stored)
            return SimpleNamespace(data=[dict(stored)])
        filtered = list(rows)
        if self.filters:
            for col, val in self.filters:
                filtered = [row for row in filtered if row.get(col) == val]
        if self.order_col:
            filtered.sort(
                key=lambda row: row.get(self.order_col) or "",
                reverse=self.order_desc,
            )
        if self.range_bounds is not None:
            start, end = self.range_bounds
            filtered = filtered[start:end + 1]
        elif self.limit_n is not None:
            filtered = filtered[: self.limit_n]
        return SimpleNamespace(data=filtered)


class _ApiBookClient:
    def __init__(self):
        self.tables = {
            "portfolio_activity_books": [],
            "portfolio_analyses": [],
        }
        self.queries = []
        self.upserts = []
        self.select_error = None

    def table(self, name):
        return _ApiBookQuery(self, name)


def _use_memory_book(monkeypatch, memory=None):
    memory = memory or _ApiBookClient()
    monkeypatch.setattr(db, "get_supabase", lambda: memory)
    monkeypatch.setattr(main, "get_supabase", lambda: memory)
    monkeypatch.setattr(main, "load_activity_book_for_merge", db.load_activity_book_for_merge)
    monkeypatch.setattr(main, "upsert_activity_book", db.upsert_activity_book)
    return memory


def _forget_process_memory():
    from year_close_packet import PACKET_STORE

    PACKET_STORE.clear()


def _post_book(filename, content, *, merge_mode=None):
    url = "/api/portfolio/analyze?tax_year=2026"
    if merge_mode:
        url += f"&merge_mode={merge_mode}"
    return client.post(url, files={"file": (filename, content, "text/csv")})


def _stored_books(memory, user_id="test-user-123"):
    return [
        row
        for row in memory.tables["portfolio_activity_books"]
        if row.get("user_id") == user_id
    ]


def _aapl_buy_bytes():
    return _rh_csv(
        "01/15/2024,01/15/2024,01/17/2024,AAPL,Apple,Buy,10,100.00,-1000.00\n"
    )


def _aapl_sell_bytes():
    return _rh_csv(
        "06/01/2026,06/01/2026,06/03/2026,AAPL,Apple,Sell,4,150.00,600.00\n"
    )


def test_buy_then_sell_merges_from_the_private_book(monkeypatch):
    """A later sell rebuilds the lot from the private book after memory is cleared."""
    _stub_analyze_network(monkeypatch)
    memory = _use_memory_book(monkeypatch)
    captured = []

    def capture(user_id, filename, summary, result):
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
        captured.append(payload)
        return True

    monkeypatch.setattr(main, "_save_history_best_effort", capture)

    first = _post_book("buy.csv", _aapl_buy_bytes())
    assert first.status_code == 200, first.text
    assert first.json()["activity_book"]["transactions"] == []
    assert "trans_code" not in first.text
    assert len(_stored_books(memory)[0]["transactions"]) == 1
    _forget_process_memory()

    second = _post_book("sell.csv", _aapl_sell_bytes())
    assert second.status_code == 200, second.text
    body = second.json()
    assert "trans_code" not in second.text
    assert body["activity_book"]["transactions"] == []
    aapl = next(position for position in body["positions"] if position["symbol"] == "AAPL")
    assert aapl["quantity"] == 6
    realized = body["summary"]["realized_summary"]
    assert realized["tax_year"] == 2026
    assert realized["lt_gains"] == 200
    assert realized["st_gains"] == 0
    assert realized["total_net"] == 200
    assert realized["transactions_count"] == 1
    stored = _stored_books(memory)[0]["transactions"]
    assert len(stored) == 2
    assert captured
    for payload in captured:
        assert payload["activity_book"]["transactions"] == []
        assert "transactions" not in payload or payload.get("transactions") in (None, [])
        assert "trans_code" not in str(payload["activity_book"]["transactions"])


def test_failed_activity_book_upsert_keeps_the_previous_book(monkeypatch):
    """A None upsert must not claim the new trades landed, and the next auto merge uses the old row."""
    import copy

    from ledger import ACTIVITY_BOOK_SAVE_FAILED_WARNING

    _stub_analyze_network(monkeypatch)
    memory = _use_memory_book(monkeypatch)
    real = db.upsert_activity_book
    results = []

    def flaky(user_id, analysis_id, filename, transactions, client=None):
        if len(results) == 1:
            results.append(None)
            return None
        saved = real(user_id, analysis_id, filename, transactions, client=client)
        results.append(saved)
        return saved

    monkeypatch.setattr(main, "upsert_activity_book", flaky)

    first = _post_book("buy.csv", _aapl_buy_bytes())
    assert first.status_code == 200, first.text
    assert len(results) == 1
    assert results[0] is not None
    previous = copy.deepcopy(_stored_books(memory)[0])
    assert len(previous["transactions"]) == 1

    _forget_process_memory()
    extra = _rh_csv(
        "01/15/2024,01/15/2024,01/17/2024,AAPL,Apple,Buy,5,150.00,-750.00\n"
    )
    failed = _post_book("more.csv", extra)
    assert failed.status_code == 200, failed.text
    body = failed.json()
    assert results[1] is None
    assert _stored_books(memory) == [previous]
    assert ACTIVITY_BOOK_SAVE_FAILED_WARNING in body["warnings"]
    assert body["activity_book"]["added_from_this_upload"] == 0
    assert body["activity_book"]["replaced"] is False
    assert "Added 1 new trade" not in failed.text
    assert "to your book" not in failed.text

    _forget_process_memory()
    sold = _post_book("sell.csv", _aapl_sell_bytes())
    assert sold.status_code == 200, sold.text
    assert results[2] is not None
    sold_body = sold.json()
    aapl = next(position for position in sold_body["positions"] if position["symbol"] == "AAPL")
    assert aapl["quantity"] == 6
    realized = sold_body["summary"]["realized_summary"]
    assert realized["lt_gains"] == 200
    assert realized["st_gains"] == 0
    assert realized["total_net"] == 200
    stored = _stored_books(memory)[0]["transactions"]
    assert len(stored) == 2
    buys = [txn for txn in stored if txn.get("trans_code") == "Buy"]
    assert len(buys) == 1
    assert buys[0]["quantity"] == 10
    assert buys[0]["price"] == 100
    assert not any(txn.get("quantity") == 5 for txn in stored)


def test_overlapping_upload_is_idempotent(monkeypatch):
    _stub_analyze_network(monkeypatch)
    memory = _use_memory_book(monkeypatch)
    buy = _aapl_buy_bytes()
    assert _post_book("buy.csv", buy).status_code == 200
    _forget_process_memory()
    second = _post_book("buy-again.csv", buy)
    assert second.status_code == 200, second.text
    body = second.json()
    book = body["activity_book"]
    assert book["added_from_this_upload"] == 0
    assert book["already_in_book"] == 1
    assert book["transaction_count"] == 1
    assert book["replaced"] is False
    assert book["transactions"] == []
    aapl = next(position for position in body["positions"] if position["symbol"] == "AAPL")
    assert aapl["quantity"] == 10
    stored = _stored_books(memory)[0]["transactions"]
    assert len(stored) == 1
    assert stored[0]["instrument"] == "AAPL"
    assert stored[0]["quantity"] == 10


def test_replace_mode_replaces_the_private_book(monkeypatch):
    _stub_analyze_network(monkeypatch)
    memory = _use_memory_book(monkeypatch)
    assert _post_book("buy.csv", _aapl_buy_bytes()).status_code == 200
    _forget_process_memory()
    replacement = _rh_csv(
        "03/01/2026,03/01/2026,03/03/2026,AAPL,Apple,Buy,5,150.00,-750.00\n"
    )
    replaced = _post_book("fresh.csv", replacement, merge_mode="replace")
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["activity_book"]["replaced"] is True
    assert replaced.json()["activity_book"]["transaction_count"] == 1
    stored = _stored_books(memory)[0]["transactions"]
    assert len(stored) == 1
    assert stored[0]["quantity"] == 5
    assert stored[0]["price"] == 150
    _forget_process_memory()

    sell = _rh_csv(
        "06/01/2026,06/01/2026,06/03/2026,AAPL,Apple,Sell,5,180.00,900.00\n"
    )
    closed = _post_book("sell.csv", sell)
    assert closed.status_code == 200, closed.text
    body = closed.json()
    realized = body["summary"]["realized_summary"]
    assert realized["st_gains"] == 150
    assert realized["lt_gains"] == 0
    assert realized["total_net"] == 150
    aapl = [position for position in body["positions"] if position["symbol"] == "AAPL"]
    assert not aapl or aapl[0]["quantity"] == 0


def test_sample_upload_does_not_replace_the_private_book(monkeypatch):
    _stub_analyze_network(monkeypatch)
    memory = _use_memory_book(monkeypatch)
    assert _post_book("aapl-buy.csv", _aapl_buy_bytes()).status_code == 200
    sample_bytes = SAMPLE_CSV_PATH.read_bytes()

    _forget_process_memory()
    official = _post_book("sample-robinhood-transactions.csv", sample_bytes)
    assert official.status_code == 200, official.text
    _assert_original_buy(memory)

    _forget_process_memory()
    replaced = _post_book(
        "sample-robinhood-transactions.csv",
        sample_bytes,
        merge_mode="replace",
    )
    assert replaced.status_code == 200, replaced.text
    _assert_original_buy(memory)

    _forget_process_memory()
    renamed = _post_book("renamed-sample.csv", sample_bytes, merge_mode="auto")
    assert renamed.status_code == 200, renamed.text
    _assert_original_buy(memory)

    _forget_process_memory()
    sold = _post_book("sell.csv", _aapl_sell_bytes())
    assert sold.status_code == 200, sold.text
    body = sold.json()
    aapl = next(position for position in body["positions"] if position["symbol"] == "AAPL")
    assert aapl["quantity"] == 6
    assert body["summary"]["realized_summary"]["lt_gains"] == 200
    assert body["summary"]["realized_summary"]["total_net"] == 200


def _assert_original_buy(memory):
    stored = _stored_books(memory)[0]["transactions"]
    assert len(stored) == 1
    assert stored[0]["instrument"] == "AAPL"
    assert stored[0]["quantity"] == 10
    assert stored[0]["trans_code"] == "Buy"


def test_history_persist_strips_client_transactions(monkeypatch):
    saved = {}

    def fake_save(**kwargs):
        saved.update(kwargs)
        return {
            "id": "hist-1",
            "user_id": kwargs["user_id"],
            "filename": kwargs["filename"],
            "result": kwargs["result_data"],
        }

    monkeypatch.setattr("main.save_analysis_history", fake_save)
    monkeypatch.setattr(main, "lookup_analysis_for_entitlement", lambda *_args: (None, True))
    upserts = []
    monkeypatch.setattr(
        main,
        "upsert_activity_book",
        lambda *args, **kwargs: upserts.append((args, kwargs)),
    )
    response = client.post(
        "/api/portfolio/history",
        json={
            "filename": "book.csv",
            "analysis": {
                "analysis_id": "guest-trades",
                "summary": {"positions_count": 1},
                "transactions": [{"instrument": "AAPL", "trans_code": "Buy"}],
                "activity_book": {
                    "transaction_count": 1,
                    "transactions": [{"instrument": "AAPL", "trans_code": "Buy"}],
                },
            },
        },
    )
    assert response.status_code == 200, response.text
    assert saved["result_data"]["activity_book"]["transactions"] == []
    assert saved["result_data"]["activity_book"]["transaction_count"] == 1
    assert "transactions" not in saved["result_data"]
    assert upserts == []

    monkeypatch.setattr(main, "get_supabase", lambda: object())
    monkeypatch.setattr(
        main,
        "get_analysis_by_id",
        lambda analysis_id, user_id, client=None: {
            "id": analysis_id,
            "user_id": user_id,
            "result": saved["result_data"],
        },
    )
    loaded = client.get("/api/portfolio/analysis/hist-1")
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["result"]["activity_book"]["transactions"] == []
    assert "trans_code" not in loaded.text


def test_stripped_marker_rejects_an_older_subset_and_keeps_a_prior_list(monkeypatch):
    """A newer stripped row blocks an older trade list. A list before that marker stays the book."""
    from ledger import HISTORICAL_BOOK_UNRECOVERABLE_WARNING

    _stub_analyze_network(monkeypatch)
    memory = _use_memory_book(monkeypatch)
    older_subset = {
        "activity_date": "2023-06-01",
        "instrument": "MSFT",
        "description": "Microsoft",
        "trans_code": "Buy",
        "quantity": 3,
        "price": 200,
        "amount": -600,
    }
    memory.tables["portfolio_analyses"].extend(
        [
            {
                "id": "stripped",
                "user_id": "test-user-123",
                "filename": "recent.csv",
                "uploaded_at": "2026-08-01T00:00:00+00:00",
                "summary": {"activity_transaction_count": 4},
                "result": {
                    "activity_book": {"transactions": [], "transaction_count": 4},
                    "summary": {"activity_transaction_count": 4},
                },
            },
            {
                "id": "older-subset",
                "user_id": "test-user-123",
                "filename": "old.csv",
                "uploaded_at": "2024-01-01T00:00:00+00:00",
                "summary": {"activity_transaction_count": 1},
                "result": {
                    "activity_book": {
                        "transactions": [older_subset],
                        "transaction_count": 1,
                    }
                },
            },
        ]
    )
    first = _post_book("buy.csv", _aapl_buy_bytes())
    assert first.status_code == 200, first.text
    body = first.json()
    assert HISTORICAL_BOOK_UNRECOVERABLE_WARNING in body["warnings"]
    assert body["activity_book"]["transaction_count"] == 1
    assert body["activity_book"]["merged_from_analysis_id"] is None
    symbols = {position["symbol"] for position in body["positions"]}
    assert "MSFT" not in symbols
    stored = _stored_books(memory)[0]["transactions"]
    assert len(stored) == 1
    assert stored[0]["instrument"] == "AAPL"
    assert stored[0]["quantity"] == 10
    assert all(txn.get("instrument") != "MSFT" for txn in stored)
    assert all(
        txn.get("instrument") != "MSFT"
        for row in memory.upserts
        for txn in row["transactions"]
    )

    kept = _use_memory_book(monkeypatch)
    kept.tables["portfolio_analyses"].extend(
        [
            {
                "id": "newer-book",
                "user_id": "test-user-123",
                "filename": "newer.csv",
                "uploaded_at": "2026-07-01T00:00:00+00:00",
                "summary": {"activity_transaction_count": 1},
                "result": {
                    "activity_book": {
                        "transactions": [
                            {
                                "activity_date": "2024-01-15",
                                "instrument": "AAPL",
                                "description": "Apple",
                                "trans_code": "Buy",
                                "quantity": 10,
                                "price": 100,
                                "amount": -1000,
                            }
                        ],
                        "transaction_count": 1,
                    },
                    "tax_profile": {"tax_year": 2024},
                },
            },
            {
                "id": "stripped-older",
                "user_id": "test-user-123",
                "filename": "gap.csv",
                "uploaded_at": "2024-01-01T00:00:00+00:00",
                "summary": {"activity_transaction_count": 4},
                "result": {"activity_book": {"transactions": [], "transaction_count": 4}},
            },
        ]
    )
    _forget_process_memory()
    sold = _post_book("sell.csv", _aapl_sell_bytes())
    assert sold.status_code == 200, sold.text
    sold_body = sold.json()
    assert HISTORICAL_BOOK_UNRECOVERABLE_WARNING not in sold_body["warnings"]
    assert sold_body["activity_book"]["merged_from_analysis_id"] == "newer-book"
    assert sold_body["activity_book"]["transaction_count"] == 2
    aapl = next(position for position in sold_body["positions"] if position["symbol"] == "AAPL")
    assert aapl["quantity"] == 6
    assert sold_body["summary"]["realized_summary"]["lt_gains"] == 200
    kept_rows = _stored_books(kept)[0]["transactions"]
    assert len(kept_rows) == 2
    assert {txn["trans_code"] for txn in kept_rows} == {"Buy", "Sell"}


def test_stripped_history_warns_and_starts_a_new_book(monkeypatch):
    from ledger import HISTORICAL_BOOK_UNRECOVERABLE_WARNING

    _stub_analyze_network(monkeypatch)
    memory = _use_memory_book(monkeypatch)
    memory.tables["portfolio_analyses"].append(
        {
            "id": "stripped",
            "user_id": "test-user-123",
            "filename": "recent.csv",
            "uploaded_at": "2026-08-01T00:00:00+00:00",
            "summary": {"activity_transaction_count": 4},
            "result": {
                "activity_book": {"transactions": [], "transaction_count": 4},
                "summary": {"activity_transaction_count": 4},
            },
        }
    )
    first = _post_book("buy.csv", _aapl_buy_bytes())
    assert first.status_code == 200, first.text
    assert HISTORICAL_BOOK_UNRECOVERABLE_WARNING in first.json()["warnings"]
    assert first.json()["activity_book"]["transactions"] == []
    stored = _stored_books(memory)[0]["transactions"]
    assert len(stored) == 1
    assert stored[0]["quantity"] == 10

    _forget_process_memory()
    second = _post_book(
        "more.csv",
        _rh_csv("03/01/2026,03/01/2026,03/03/2026,AAPL,Apple,Buy,5,150.00,-750.00\n"),
    )
    assert second.status_code == 200, second.text
    assert HISTORICAL_BOOK_UNRECOVERABLE_WARNING not in second.json()["warnings"]
    assert second.json()["activity_book"]["transaction_count"] == 2
    assert len(_stored_books(memory)[0]["transactions"]) == 2

    replace_memory = _use_memory_book(monkeypatch)
    replace_memory.tables["portfolio_analyses"].append(
        {
            "id": "stripped",
            "user_id": "test-user-123",
            "filename": "recent.csv",
            "uploaded_at": "2026-08-01T00:00:00+00:00",
            "summary": {"activity_transaction_count": 4},
            "result": {"activity_book": {"transactions": [], "transaction_count": 4}},
        }
    )
    _forget_process_memory()
    replaced = _post_book("fresh.csv", _aapl_buy_bytes(), merge_mode="replace")
    assert replaced.status_code == 200, replaced.text
    assert HISTORICAL_BOOK_UNRECOVERABLE_WARNING not in replaced.json()["warnings"]


def test_empty_private_row_wins_over_history(monkeypatch):
    _stub_analyze_network(monkeypatch)
    memory = _use_memory_book(monkeypatch)
    memory.tables["portfolio_activity_books"].append(
        {
            "user_id": "test-user-123",
            "analysis_id": "empty-book",
            "filename": "cleared.csv",
            "transactions": [],
        }
    )
    memory.tables["portfolio_analyses"].append(
        {
            "id": "older-buy",
            "user_id": "test-user-123",
            "filename": "full.csv",
            "uploaded_at": "2024-01-20T00:00:00+00:00",
            "summary": {},
            "result": {
                "activity_book": {
                    "transactions": [
                        {
                            "activity_date": "2024-01-15",
                            "instrument": "AAPL",
                            "description": "Apple",
                            "trans_code": "Buy",
                            "quantity": 10,
                            "price": 100,
                            "amount": -1000,
                        }
                    ],
                    "transaction_count": 1,
                }
            },
        }
    )
    response = _post_book("sell.csv", _aapl_sell_bytes())
    assert response.status_code == 200, response.text
    realized = response.json()["summary"]["realized_summary"]
    assert realized["lt_gains"] == 0
    assert realized["transactions_count"] == 0
    assert not any(query["table"] == "portfolio_analyses" for query in memory.queries)
    stored = _stored_books(memory)[0]["transactions"]
    assert len(stored) == 1
    assert stored[0]["trans_code"] == "Sell"


def test_incomplete_history_scan_does_not_warn_or_upsert(monkeypatch):
    from ledger import (
        ACTIVITY_BOOK_LOAD_FAILED_WARNING,
        HISTORICAL_BOOK_UNRECOVERABLE_WARNING,
    )

    _stub_analyze_network(monkeypatch)
    memory = _use_memory_book(monkeypatch)
    monkeypatch.setattr(db, "ACTIVITY_BOOK_HISTORY_PAGE", 1)
    monkeypatch.setattr(db, "ACTIVITY_BOOK_HISTORY_MAX_PAGES", 1)
    memory.tables["portfolio_analyses"].extend(
        [
            {
                "id": "stripped-new",
                "user_id": "test-user-123",
                "filename": "recent.csv",
                "uploaded_at": "2026-08-01T00:00:00+00:00",
                "summary": {"activity_transaction_count": 4},
                "result": {"activity_book": {"transactions": [], "transaction_count": 4}},
            },
            {
                "id": "older-full",
                "user_id": "test-user-123",
                "filename": "old.csv",
                "uploaded_at": "2024-01-01T00:00:00+00:00",
                "summary": {},
                "result": {
                    "activity_book": {
                        "transactions": [
                            {
                                "activity_date": "2024-01-15",
                                "instrument": "AAPL",
                                "description": "Apple",
                                "trans_code": "Buy",
                                "quantity": 10,
                                "price": 100,
                                "amount": -1000,
                            }
                        ],
                        "transaction_count": 1,
                    }
                },
            },
        ]
    )
    response = _post_book("sell.csv", _aapl_sell_bytes())
    assert response.status_code == 200, response.text
    warnings = response.json()["warnings"]
    assert HISTORICAL_BOOK_UNRECOVERABLE_WARNING not in warnings
    assert ACTIVITY_BOOK_LOAD_FAILED_WARNING not in warnings
    assert response.json()["summary"]["realized_summary"]["lt_gains"] == 0
    assert memory.tables["portfolio_activity_books"] == []
    assert memory.upserts == []


def test_snapshot_with_no_new_trades_does_not_blank_the_book(monkeypatch):
    _stub_analyze_network(monkeypatch)
    memory = _use_memory_book(monkeypatch)
    assert _post_book("buy.csv", _aapl_buy_bytes()).status_code == 200
    _forget_process_memory()
    snapshot = (
        "symbol,quantity,purchase_price,current_price\n"
        "AAPL,3,50,60\n"
    ).encode()
    response = _post_book("positions.csv", snapshot)
    assert response.status_code == 200, response.text
    body = response.json()
    assert any("position snapshot" in warning for warning in body["warnings"])
    assert body["activity_book"]["transactions"] == []
    assert body["activity_book"]["transaction_count"] == 1
    aapl = next(position for position in body["positions"] if position["symbol"] == "AAPL")
    assert aapl["quantity"] == 3
    stored = _stored_books(memory)[0]["transactions"]
    assert len(stored) == 1
    assert stored[0]["quantity"] == 10
    assert stored[0]["trans_code"] == "Buy"
    assert all(row["transactions"] for row in memory.upserts)


def test_activity_book_read_error_does_not_overwrite(monkeypatch):
    from ledger import (
        ACTIVITY_BOOK_LOAD_FAILED_WARNING,
        HISTORICAL_BOOK_UNRECOVERABLE_WARNING,
    )

    _stub_analyze_network(monkeypatch)
    memory = _use_memory_book(monkeypatch)
    original = [{"instrument": "AAPL", "trans_code": "Buy", "quantity": 10}]
    memory.tables["portfolio_activity_books"].append(
        {
            "user_id": "test-user-123",
            "analysis_id": "kept",
            "filename": "kept.csv",
            "transactions": list(original),
        }
    )
    memory.select_error = RuntimeError("connection reset")
    failed = _post_book("sell.csv", _aapl_sell_bytes())
    assert failed.status_code == 200, failed.text
    assert ACTIVITY_BOOK_LOAD_FAILED_WARNING in failed.json()["warnings"]
    assert memory.upserts == []
    assert memory.tables["portfolio_activity_books"][0]["transactions"] == original

    missing = _ApiBookClient()
    _use_memory_book(monkeypatch, missing)
    missing.tables["portfolio_activity_books"].append(
        {
            "user_id": "test-user-123",
            "analysis_id": "kept",
            "filename": "kept.csv",
            "transactions": list(original),
        }
    )

    class _MissingTable(Exception):
        code = "42P01"

        def __str__(self):
            return 'relation "public.portfolio_activity_books" does not exist'

    missing.select_error = _MissingTable()
    _forget_process_memory()
    response = _post_book("sell.csv", _aapl_sell_bytes())
    assert response.status_code == 200, response.text
    warnings = response.json()["warnings"]
    assert ACTIVITY_BOOK_LOAD_FAILED_WARNING not in warnings
    assert HISTORICAL_BOOK_UNRECOVERABLE_WARNING not in warnings
    assert missing.upserts == []
    assert missing.tables["portfolio_activity_books"][0]["transactions"] == original


def test_activity_book_is_not_visible_across_accounts(monkeypatch):
    _stub_analyze_network(monkeypatch)
    memory = _use_memory_book(monkeypatch)
    main.app.dependency_overrides[get_optional_user] = lambda: "user-a"
    try:
        first = _post_book("a.csv", _aapl_buy_bytes())
        assert first.status_code == 200, first.text
        _forget_process_memory()
        main.app.dependency_overrides[get_optional_user] = lambda: "user-b"
        second = _post_book("b.csv", _aapl_sell_bytes())
        assert second.status_code == 200, second.text
        realized = second.json()["summary"]["realized_summary"]
        assert realized["lt_gains"] == 0
        assert realized["transactions_count"] == 0
        books = {
            row["user_id"]: row for row in memory.tables["portfolio_activity_books"]
        }
        assert len(books["user-a"]["transactions"]) == 1
        assert books["user-a"]["transactions"][0]["trans_code"] == "Buy"
        assert len(books["user-b"]["transactions"]) == 1
        assert books["user-b"]["transactions"][0]["trans_code"] == "Sell"
        private_selects = [
            query
            for query in memory.queries
            if query["table"] == "portfolio_activity_books" and query["op"] == "select"
        ]
        assert any(query["filters"] == [("user_id", "user-b")] for query in private_selects)
    finally:
        main.app.dependency_overrides[get_optional_user] = mock_get_current_user
