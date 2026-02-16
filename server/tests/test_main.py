from fastapi.testclient import TestClient

import main
from auth import get_current_user


# Mock authentication: return test user ID for all authenticated endpoints
def mock_get_current_user() -> str:
    return "test-user-123"


# Override the authentication dependency
main.app.dependency_overrides[get_current_user] = mock_get_current_user

client = TestClient(main.app)


def setup_function():
    main.push_subscriptions.clear()


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

    main.run()

    assert captured["args"] == ("main:app",)
    assert captured["kwargs"]["host"] == "0.0.0.0"
    assert captured["kwargs"]["port"] == 9090
    assert captured["kwargs"]["reload"] is True


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


def _make_csv(content: str | None = None):
    """Helper to create a CSV upload payload."""
    if content is None:
        content = (
            "symbol,quantity,cost_basis_per_share,total_cost_basis,purchase_date,current_price\n"
            "AAPL,10,150.00,1500.00,2024-01-15,145.00\n"
            "MSFT,5,300.00,1500.00,2024-06-01,310.00\n"
        )
    return {"file": ("test.csv", content, "text/csv")}


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
            symbol="AAPL", quantity=10, avg_cost_basis=150.0,
            total_cost_basis=1500.0, current_price=145.0, market_value=1450.0,
            unrealized_pnl=-50.0, unrealized_pnl_pct=-3.33,
        ),
    ]
    summary = PortfolioSummary(
        total_market_value=1450.0, total_cost_basis=1500.0,
        total_unrealized_pnl=-50.0, total_unrealized_pnl_pct=-3.33,
        positions_count=1, lots_with_losses=1,
    )

    monkeypatch.setattr("main.parse_csv", lambda _: (lots, [], []))
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
    monkeypatch.setattr("main.parse_csv", lambda _: ([], [], ["No valid rows"]))

    response = client.post("/api/portfolio/analyze", files=_make_csv("bad,csv\n"))
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Could not parse" in detail["message"]
    assert "No valid rows" in detail["errors"]


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
            symbol="TSLA", quantity=2, avg_cost_basis=200.0,
            total_cost_basis=400.0, current_price=210.0, market_value=420.0,
        ),
    ]
    summary = PortfolioSummary(positions_count=1)

    monkeypatch.setattr("main.parse_csv", lambda _: (lots, [], []))
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
            symbol="NVDA", quantity=3, avg_cost_basis=400.0,
            total_cost_basis=1200.0, current_price=450.0, market_value=1350.0,
        ),
    ]
    summary = PortfolioSummary(positions_count=1)

    save_called = {"value": False}

    def fake_save(**_kw):
        save_called["value"] = True

    monkeypatch.setattr("main.parse_csv", lambda _: (lots, [], []))
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
    # Verify AI is disabled by default when no profile exists
    data = response.json()
    assert data["tax_profile"]["ai_suggestions_enabled"] is False


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
            symbol="AMD", quantity=5, avg_cost_basis=100.0,
            total_cost_basis=500.0, current_price=95.0, market_value=475.0,
        ),
    ]
    summary = PortfolioSummary(positions_count=1)

    # Mock that user has AI enabled
    def fake_get_profile(_uid):
        return {"ai_suggestions_enabled": True}

    monkeypatch.setattr("main.parse_csv", lambda _: (lots, [], []))
    monkeypatch.setattr("main.fetch_current_prices", lambda s, fb=None: ({}, []))
    monkeypatch.setattr("main.compute_lot_metrics", lambda l: l)
    monkeypatch.setattr("main.detect_wash_sales", lambda t: [])
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda l: [{"symbol": "AMD"}])
    monkeypatch.setattr("main.db_get_tax_profile", fake_get_profile)

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


def test_analyze_portfolio_ai_disabled_shows_warning(monkeypatch):
    """AI disabled should show a helpful warning message."""
    from datetime import date
    from models import TaxLot, Position, PortfolioSummary

    lots = [
        TaxLot(
            symbol="TSLA", quantity=2, cost_basis_per_share=200.0,
            total_cost_basis=400.0, purchase_date=date(2024, 3, 1),
            current_price=180.0,
        ),
    ]
    positions = [
        Position(
            symbol="TSLA", quantity=2, avg_cost_basis=200.0,
            total_cost_basis=400.0, current_price=180.0, market_value=360.0,
        ),
    ]
    summary = PortfolioSummary(positions_count=1)

    # Mock that user has AI disabled (default)
    def fake_get_profile(_uid):
        return {"ai_suggestions_enabled": False}

    monkeypatch.setattr("main.parse_csv", lambda _: (lots, [], []))
    monkeypatch.setattr("main.fetch_current_prices", lambda s, fb=None: ({}, []))
    monkeypatch.setattr("main.compute_lot_metrics", lambda l: l)
    monkeypatch.setattr("main.detect_wash_sales", lambda t: [])
    monkeypatch.setattr("main.db_get_tax_profile", fake_get_profile)
    monkeypatch.setattr("main.generate_suggestions", lambda **kw: [])
    monkeypatch.setattr("main.aggregate_positions", lambda l: positions)
    monkeypatch.setattr("main.build_portfolio_summary", lambda p, s, w: summary)

    response = client.post(
        "/api/portfolio/analyze?user_id=test-user-123",
        files=_make_csv()
    )

    assert response.status_code == 200
    data = response.json()
    assert any("AI-powered suggestions are disabled" in w for w in data["warnings"])
    assert any("Enable them in Settings" in w for w in data["warnings"])


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
            symbol="AAPL", quantity=10, avg_cost_basis=155.0,
            total_cost_basis=1550.0, current_price=145.0, market_value=1450.0,
            wash_sale_risk=True,
        ),
    ]
    summary = PortfolioSummary(positions_count=1, wash_sale_flags_count=1)

    monkeypatch.setattr("main.parse_csv", lambda _: (lots, transactions, []))
    monkeypatch.setattr("main.fetch_current_prices", lambda s, fb=None: ({"AAPL": 145.0}, []))
    monkeypatch.setattr("main.compute_lot_metrics", lambda l: l)
    monkeypatch.setattr("main.detect_wash_sales", lambda t: wash_flags)
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





# ---------- Portfolio History Endpoints ----------


def test_get_portfolio_history(monkeypatch):
    """GET /api/portfolio/history returns authenticated user's history."""
    mock_history = [
        {"id": "h1", "filename": "test1.csv", "uploaded_at": "2025-01-01T00:00:00"},
        {"id": "h2", "filename": "test2.csv", "uploaded_at": "2025-01-02T00:00:00"},
    ]

    monkeypatch.setattr("main.get_analysis_history", lambda uid, limit: mock_history)

    response = client.get("/api/portfolio/history")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["id"] == "h1"


def test_get_portfolio_history_empty(monkeypatch):
    """GET /api/portfolio/history returns empty list for new user."""
    monkeypatch.setattr("main.get_analysis_history", lambda uid, limit: [])

    response = client.get("/api/portfolio/history")
    assert response.status_code == 200
    assert response.json() == []


def test_get_portfolio_history_custom_limit(monkeypatch):
    """GET /api/portfolio/history?limit=5 passes limit to DB."""
    captured = {}

    def fake_history(uid, limit):
        captured["limit"] = limit
        return []

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

    monkeypatch.setattr("main.get_analysis_by_id", lambda aid, uid: mock_record)

    response = client.get("/api/portfolio/analysis/abc-123")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "abc-123"
    assert "result" in data


def test_get_portfolio_analysis_not_found(monkeypatch):
    """GET /api/portfolio/analysis/{id} returns 404 when not found."""
    monkeypatch.setattr("main.get_analysis_by_id", lambda aid, uid: None)

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
