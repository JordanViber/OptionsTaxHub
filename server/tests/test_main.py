from fastapi.testclient import TestClient
from pathlib import Path
from types import SimpleNamespace

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


def setup_function():
    main.push_subscriptions.clear()
    main.reset_guest_analyze_quota()


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

    def fake_create(**_kwargs):
        return FakeSession()

    monkeypatch.setattr(stripe_mod.checkout.Session, "create", fake_create)

    response = client.post("/api/tips/checkout", json={"tier": "coffee"})
    assert response.status_code == 200
    data = response.json()
    assert data["checkout_url"] == "https://checkout.stripe.com/test_session"


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
        lambda symbols, fb=None: ({s.upper(): 100.0 for s in symbols}, []),
    )
    monkeypatch.setattr("main.fetch_option_prices", lambda labels, fb=None: ({}, []))
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda lots: [])
    monkeypatch.setattr("main._save_history_best_effort", lambda *args, **kwargs: None)
    monkeypatch.setattr("main.get_latest_activity_book", lambda uid, client=None: None)
    monkeypatch.setattr("main.get_packet_grant_for_tax_year", lambda *args, **kwargs: None)


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
        "main.get_latest_activity_book",
        lambda uid, client=None: {
            "analysis_id": "book-2023",
            "filename": "full-history.csv",
            "transactions": [t.model_dump(mode="json") for t in prior_txns],
            "packet_unlocked": True,
            "packet_session_id": "cs_test_yeargrant",
            "tax_year": 2026,
        },
    )
    monkeypatch.setattr(
        "main.get_packet_grant_for_tax_year",
        lambda uid, year, client=None: "cs_test_yeargrant",
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


def test_analyze_replace_mode_ignores_saved_book(monkeypatch):
    """Start a new book uses only the file that was just uploaded."""
    from csv_parser import parse_csv

    _stub_analyze_network(monkeypatch)
    prior_csv = _rh_csv(
        "06/01/2023,06/01/2023,06/03/2023,AAPL,Apple,Buy,10,100.00,-1000.00\n",
    )
    _lots, prior_txns, _e, _r = parse_csv(prior_csv.decode())
    monkeypatch.setattr(
        "main.get_latest_activity_book",
        lambda uid, client=None: {
            "analysis_id": "book-2023",
            "filename": "full-history.csv",
            "transactions": [t.model_dump(mode="json") for t in prior_txns],
        },
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
    response = client.post(
        "/api/portfolio/history",
        json={
            "filename": "sample-robinhood-transactions.csv",
            "analysis": {
                "analysis_id": "guest-abc",
                "summary": {"positions_count": 3, "total_market_value": 1000},
            },
        },
    )
    assert response.status_code == 200
    assert saved["user_id"] == "test-user-123"
    assert saved["filename"] == "sample-robinhood-transactions.csv"
    assert saved["summary"]["positions_count"] == 3
    assert saved["result_data"]["analysis_id"] == "guest-abc"


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
    monkeypatch.setattr("main.delete_analysis_by_id", lambda aid, uid: True)

    response = client.delete("/api/portfolio/analysis/abc-123")
    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_delete_analysis_not_found(monkeypatch):
    """DELETE /api/portfolio/analysis/{id} returns 404 when not found."""
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
    monkeypatch.setattr("main._save_history_best_effort", lambda *args, **kwargs: None)

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

    summary, warnings = asyncio.run(
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

    summary, warnings = asyncio.run(
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

    summary, warnings = asyncio.run(
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

    summary, warnings = asyncio.run(
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

    summary, warnings = asyncio.run(
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

    summary, warnings = asyncio.run(
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

    summary, warnings = asyncio.run(
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

    summary, warnings = asyncio.run(
        _maybe_parse_supplemental_1099(DummyUpload(), {"AMD"}, 2024)
    )

    assert summary is not None
    assert summary.tax_year is None
    assert warnings == []
    assert not any("does not match" in warning for warning in warnings)


def test_analyze_same_year_1099_compare_vs_2026_sample_mismatch(monkeypatch):
    """2024 CSV + 2024 fixture is a same-year compare; 2026 sample is not."""
    from year_close_packet import COMPARE_TITLE, build_packet_payload, render_packet_pdf
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
    same_payload = build_packet_payload(same_body)
    assert same_payload["same_year_compare"] is True
    assert same_payload["form_1099_tax_year"] == 2024
    assert same_payload["analysis_tax_year"] == 2024
    # year_close_2024.csv is a $300 ST loss + $300 wash; fold into 1099-style net.
    assert same_payload["export_short_term_net"] == 0.0
    assert same_payload["export_wash_sale_disallowed"] == 300.0
    same_pdf = render_packet_pdf(same_payload)
    same_reader = PdfReader(BytesIO(same_pdf))
    assert len(same_reader.pages) == 2
    same_text = "\n".join((page.extract_text() or "") for page in same_reader.pages)
    assert COMPARE_TITLE in same_text
    assert "Broker 1099 (settlement date)" in same_text
    assert "This export (trade date)" in same_text
    assert "$-300.00" not in same_text

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
    mismatch_pdf = render_packet_pdf(mismatch_payload)
    mismatch_reader = PdfReader(BytesIO(mismatch_pdf))
    assert len(mismatch_reader.pages) == 1
    mismatch_text = "\n".join(
        (page.extract_text() or "") for page in mismatch_reader.pages
    )
    assert COMPARE_TITLE not in mismatch_text
    assert "previous-year supplement" in mismatch_text


def test_analyze_2026_sample_csv_and_1099_is_same_year_compare(monkeypatch):
    """Open-the-sample pair: 2026 CSV + 2026 1099 is a same-year compare."""
    import pytest
    from year_close_packet import COMPARE_TITLE, build_packet_payload, render_packet_pdf
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
    same_payload = build_packet_payload(same_body)
    assert same_payload["same_year_compare"] is True
    assert same_payload["form_1099_tax_year"] == 2026
    assert same_payload["analysis_tax_year"] == 2026
    # Sample realized is -$924; folding CSV wash ($924) yields 1099-style ST net $0.
    assert same_body["summary"]["realized_summary"]["net_st"] == pytest.approx(-924.00)
    assert same_payload["export_short_term_net"] == pytest.approx(0.00)
    assert same_payload["export_wash_sale_disallowed"] == pytest.approx(924.00)
    assert same_payload["short_term_net_gain"] == pytest.approx(2699.00)
    same_pdf = render_packet_pdf(same_payload)
    same_text = "\n".join(
        (page.extract_text() or "")
        for page in PdfReader(BytesIO(same_pdf)).pages
    )
    assert COMPARE_TITLE in same_text
    assert "Broker 1099 (settlement date)" in same_text
    assert "$2,699.00" in same_text

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
    mismatch_text = "\n".join(
        (page.extract_text() or "")
        for page in PdfReader(BytesIO(render_packet_pdf(mismatch_payload))).pages
    )
    assert COMPARE_TITLE not in mismatch_text
    assert "previous-year supplement" in mismatch_text


def test_analyze_unknown_1099_year_is_not_mismatch_or_same_year_compare(monkeypatch):
    from io import BytesIO

    from pypdf import PdfReader

    from models import Supplemental1099Summary
    from year_close_packet import (
        COMPARE_TITLE,
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
    pdf_text = "\n".join(
        (page.extract_text() or "")
        for page in PdfReader(BytesIO(render_packet_pdf(payload))).pages
    )
    assert COMPARE_TITLE not in pdf_text
    assert "could not be determined" in pdf_text
    reader = PdfReader(BytesIO(render_packet_pdf(payload)))
    assert len(reader.pages) == 1


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
