import os
import logging
from typing import Annotated, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import io
import json
from typing import List, Dict, Any
from pywebpush import webpush, WebPushException
from pydantic import BaseModel

from models import (
    FilingStatus,
    PortfolioAnalysis,
    TaxProfile,
)
from csv_parser import parse_csv
from tax_engine import get_tax_brackets_summary
from harvesting import (
    compute_lot_metrics,
    aggregate_positions,
    generate_suggestions,
    build_portfolio_summary,
)
from wash_sale import detect_wash_sales, adjust_lots_for_wash_sales
from price_service import fetch_current_prices
from ai_advisor import get_ai_suggestions, prepare_positions_for_ai
from db import (
    save_analysis_history,
    get_analysis_history,
    get_analysis_by_id,
    delete_analyses_without_result,
    delete_analysis_by_id,
    save_tax_profile as db_save_tax_profile,
    get_tax_profile as db_get_tax_profile,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env.local or .env
load_dotenv(".env.local")
load_dotenv(".env")

app = FastAPI()

# Get environment variables
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
DATABASE_URL = os.environ.get("DATABASE_URL")
API_KEY_SECRET = os.environ.get("API_KEY_SECRET")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "admin@optionstaxhub.com")

# In-memory storage for push subscriptions
# NOTE: This is temporary storage for development/MVP. Production implementation
# will use database storage (see GitHub issue or backlog for migration task)
push_subscriptions: List[Dict[str, Any]] = []

# Pydantic models
class PushSubscription(BaseModel):
    endpoint: str
    keys: Dict[str, str]
    expirationTime: Any = None

class PushNotification(BaseModel):
    title: str
    body: str
    icon: str = "/icons/icon-192x192.svg"
    badge: str = "/icons/icon-192x192.svg"
    tag: str = "default"
    data: Dict[str, Any] = {}

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/upload-csv")
async def upload_csv(file: Annotated[UploadFile, File()]):
    # Legacy endpoint: Read uploaded CSV in-memory, parse with pandas, return first 5 rows
    # Use POST /api/portfolio/analyze for full tax-loss harvesting analysis
    contents = await file.read()
    df = pd.read_csv(io.StringIO(contents.decode("utf-8")))
    return df.head(5).to_dict(orient="records")


# --- Portfolio Analysis Endpoints ---


def _try_get_ai_suggestions(
    tax_lots: list,
    warnings: list[str],
) -> dict | None:
    """Attempt to get AI suggestions, appending a warning on failure."""
    ai_positions = prepare_positions_for_ai(tax_lots)
    if not ai_positions:
        return None
    try:
        return get_ai_suggestions(ai_positions)
    except Exception as e:
        logger.error(f"AI advisor failed: {e}")
        warnings.append(
            "AI-powered suggestions unavailable. Using default replacement mappings."
        )
        return None


def _save_history_best_effort(
    user_id: str,
    filename: str,
    summary,
    result: PortfolioAnalysis,
) -> None:
    """Save analysis to history (best-effort, non-blocking)."""
    try:
        # Use mode="json" to convert date/datetime objects to ISO strings
        # so the dict is JSON-serializable for the JSONB column.
        result_dict = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
        summary_dict = summary.model_dump(mode="json") if hasattr(summary, "model_dump") else dict(summary)
        saved = save_analysis_history(
            user_id=user_id,
            filename=filename,
            summary=summary_dict,
            result_data=result_dict,
        )
        if saved:
            logger.info(f"History saved successfully: id={saved.get('id')}")
        else:
            logger.warning("save_analysis_history returned None — check Supabase connection")
    except Exception as e:
        logger.warning(f"Failed to save analysis history: {e}", exc_info=True)


@app.post("/api/portfolio/analyze", response_model=PortfolioAnalysis)
async def analyze_portfolio(
    file: Annotated[UploadFile, File()],
    filing_status: Optional[str] = Query(default="single"),
    estimated_income: Optional[float] = Query(default=None, ge=0, description="Estimated annual income (must be >= 0)"),
    tax_year: Optional[int] = Query(default=None, ge=2024, le=2026, description="Tax year (2024-2026)"),
    user_id: Optional[str] = Query(default=None),
):
    """
    Full portfolio analysis with tax-loss harvesting suggestions.

    Accepts a CSV file (Robinhood transaction history or simplified format),
    fetches live prices, runs tax engine, detects wash sales, and generates
    AI-powered harvesting suggestions.

    DISCLAIMER: For educational/simulation purposes only — not financial or tax advice.
    """
    # Read and parse CSV
    contents = await file.read()
    tax_lots, transactions, parse_errors = parse_csv(contents.decode("utf-8"))

    if not tax_lots and not transactions:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Could not parse any positions from the CSV file.",
                "errors": parse_errors,
            },
        )

    # Build tax profile from query params
    try:
        fs = FilingStatus(filing_status)
    except ValueError:
        fs = FilingStatus.SINGLE

    tax_profile = TaxProfile(
        filing_status=fs,
        estimated_annual_income=estimated_income if estimated_income is not None else 75000.0,
        tax_year=tax_year if tax_year is not None else 2025,
    )

    all_warnings = list(parse_errors)

    # Fetch live prices for all symbols
    symbols = list({lot.symbol for lot in tax_lots})
    fallback_prices = {
        lot.symbol: lot.current_price
        for lot in tax_lots
        if lot.current_price is not None
    }
    live_prices, price_warnings = fetch_current_prices(symbols, fallback_prices)
    all_warnings.extend(price_warnings)

    for lot in tax_lots:
        if lot.symbol in live_prices:
            lot.current_price = live_prices[lot.symbol]

    tax_lots = compute_lot_metrics(tax_lots)

    # Detect wash sales from transaction history
    wash_sale_flags = detect_wash_sales(transactions) if transactions else []
    if wash_sale_flags:
        tax_lots = adjust_lots_for_wash_sales(tax_lots, wash_sale_flags)

    # Get AI-powered suggestions
    ai_suggestions = _try_get_ai_suggestions(tax_lots, all_warnings)

    suggestions = generate_suggestions(
        tax_lots=tax_lots,
        transactions=transactions,
        tax_profile=tax_profile,
        ai_suggestions=ai_suggestions,
    )

    positions = aggregate_positions(tax_lots)
    summary = build_portfolio_summary(positions, suggestions, wash_sale_flags)

    result = PortfolioAnalysis(
        positions=positions,
        tax_lots=tax_lots,
        suggestions=suggestions,
        wash_sale_flags=wash_sale_flags,
        summary=summary,
        tax_profile=tax_profile,
        warnings=all_warnings,
    )

    if user_id:
        _save_history_best_effort(user_id, file.filename or "upload.csv", summary, result)

    return result


@app.get("/api/portfolio/history/{user_id}")
async def get_portfolio_history(user_id: str, limit: int = Query(default=20, ge=1, le=100)):
    """
    Retrieve a user's past portfolio analyses, newest first.

    Returns summary metadata (filename, date, positions count, market value)
    without the full position data (which is processed in-memory only).
    """
    history = get_analysis_history(user_id, limit)
    return history


@app.get("/api/portfolio/analysis/{analysis_id}")
async def get_portfolio_analysis(
    analysis_id: str,
    user_id: str = Query(..., description="Owner user ID for access control"),
):
    """
    Retrieve a single past portfolio analysis by ID, including the full result.

    Used when a user clicks a history item to reload that report.
    """
    record = get_analysis_by_id(analysis_id, user_id)
    if not record:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return record


@app.delete("/api/portfolio/history/{user_id}/cleanup")
async def cleanup_orphan_history(user_id: str):
    """
    Delete portfolio analysis entries that have no stored result data.

    These are legacy rows created before the app started persisting
    the full analysis result. Returns the count of deleted rows.
    """
    deleted = delete_analyses_without_result(user_id)
    return {"deleted": deleted}


@app.delete("/api/portfolio/analysis/{analysis_id}")
async def delete_portfolio_analysis(
    analysis_id: str,
    user_id: str = Query(..., description="Owner user ID for access control"),
):
    """
    Delete a single portfolio analysis by ID.

    Enforces ownership via user_id query param.
    """
    deleted = delete_analysis_by_id(analysis_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {"deleted": True}


@app.get("/api/prices")
async def get_prices(symbols: str = Query(..., description="Comma-separated ticker symbols")):
    """Fetch current prices for given symbols via yfinance."""
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="No symbols provided")

    prices, warnings = fetch_current_prices(symbol_list)
    return {"prices": prices, "warnings": warnings}


@app.get("/api/tax-brackets")
async def get_tax_brackets(
    year: int = Query(default=2025, ge=2024, le=2026),
    filing_status: str = Query(default="single"),
    income: float = Query(default=75000.0, ge=0),
):
    """Return applicable tax brackets for the given parameters."""
    try:
        fs = FilingStatus(filing_status)
    except ValueError:
        fs = FilingStatus.SINGLE

    profile = TaxProfile(
        filing_status=fs,
        estimated_annual_income=income,
        tax_year=year,
    )

    return get_tax_brackets_summary(profile)


@app.post("/api/tax-profile")
async def save_tax_profile_endpoint(profile: TaxProfile):
    """
    Save user's tax profile settings to Supabase.

    Upserts the profile so each user has exactly one row.
    Falls back to echo-only if Supabase is unavailable.
    """
    if not profile.user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    saved = db_save_tax_profile(
        user_id=profile.user_id,
        filing_status=profile.filing_status.value,
        estimated_annual_income=profile.estimated_annual_income,
        state=profile.state,
        tax_year=profile.tax_year,
    )

    if saved:
        return {"message": "Tax profile saved", "profile": saved}

    # Fallback: return the validated profile even if DB is unavailable
    return {"message": "Tax profile saved (not persisted)", "profile": profile.model_dump()}


@app.get("/api/tax-profile/{user_id}")
async def get_tax_profile_endpoint(user_id: str):
    """
    Retrieve a user's saved tax profile from Supabase.

    Returns default profile if no saved profile exists.
    """
    saved = db_get_tax_profile(user_id)
    if saved:
        return saved

    # No saved profile — return defaults
    default_profile = TaxProfile(user_id=user_id)
    return default_profile.model_dump()


# --- Stripe Tip/Donation Endpoints ---

import stripe

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")

# Tip tiers: price_id → metadata
TIP_TIERS = {
    "coffee": {
        "price_id": "price_1T0mFVKjuEm9woaeLRWgYJBJ",
        "amount": 300,
        "label": "Coffee",
    },
    "lunch": {
        "price_id": "price_1T0mFVKjuEm9woaeTqeB2FCD",
        "amount": 1000,
        "label": "Lunch",
    },
    "generous": {
        "price_id": "price_1T0mFVKjuEm9woaemwHjU9ou",
        "amount": 2500,
        "label": "Generous",
    },
}


class TipRequest(BaseModel):
    tier: str  # "coffee", "lunch", or "generous"


@app.get("/api/tips/tiers")
async def get_tip_tiers():
    """Return available tip tiers for the frontend."""
    return [
        {"id": k, "label": v["label"], "amount": v["amount"]}
        for k, v in TIP_TIERS.items()
    ]


@app.post("/api/tips/checkout")
async def create_tip_checkout(tip: TipRequest):
    """
    Create a Stripe Checkout Session for a one-time tip.

    Returns the checkout URL to redirect the user to.
    """
    tier = TIP_TIERS.get(tip.tier)
    if not tier:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier '{tip.tier}'. Choose: {', '.join(TIP_TIERS.keys())}",
        )

    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe is not configured")

    stripe.api_key = STRIPE_SECRET_KEY

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": tier["price_id"], "quantity": 1}],
            success_url=f"{FRONTEND_URL}/tips/success",
            cancel_url=f"{FRONTEND_URL}/tips/cancel",
        )
        return {"checkout_url": session.url}
    except stripe.StripeError as e:
        logger.error(f"Stripe checkout error: {e}")
        raise HTTPException(status_code=502, detail="Failed to create checkout session")

@app.post("/push/subscribe")
async def subscribe_to_push(subscription: PushSubscription):
    """Store push notification subscription"""
    subscription_dict = subscription.model_dump()

    # Check if subscription already exists
    for existing in push_subscriptions:
        if existing.get("endpoint") == subscription_dict["endpoint"]:
            return {"message": "Subscription already exists", "count": len(push_subscriptions)}

    push_subscriptions.append(subscription_dict)
    logger.info(f"New push subscription added. Total subscriptions: {len(push_subscriptions)}")
    return {"message": "Subscription stored", "count": len(push_subscriptions)}

@app.post("/push/unsubscribe")
async def unsubscribe_from_push(subscription: PushSubscription):
    """Remove push notification subscription"""
    subscription_dict = subscription.model_dump()

    # Find and remove subscription
    for i, existing in enumerate(push_subscriptions):
        if existing.get("endpoint") == subscription_dict["endpoint"]:
            push_subscriptions.pop(i)
            logger.info(f"Push subscription removed. Total subscriptions: {len(push_subscriptions)}")
            return {"message": "Subscription removed", "count": len(push_subscriptions)}

    return {"message": "Subscription not found", "count": len(push_subscriptions)}

@app.get("/push/subscriptions")
async def get_subscriptions():
    """Get count of active push subscriptions (for debugging)"""
    return {"count": len(push_subscriptions), "subscriptions": push_subscriptions}

@app.post("/push/send")
async def send_push_notification(notification: PushNotification):
    """Send push notification to all subscribed users"""

    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return {
            "error": "VAPID keys not configured",
            "message": "Please set VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY in .env",
            "sent": 0,
            "failed": 0
        }

    notification_data = {
        "title": notification.title,
        "body": notification.body,
        "icon": notification.icon,
        "badge": notification.badge,
        "tag": notification.tag,
        "data": notification.data
    }

    sent_count = 0
    failed_count = 0

    for subscription_info in push_subscriptions[:]:  # Use slice to allow removal during iteration
        try:
            webpush(
                subscription_info=subscription_info,
                data=json.dumps(notification_data),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={
                    "sub": f"mailto:{VAPID_CLAIM_EMAIL}"
                }
            )
            sent_count += 1
            logger.info(f"Push notification sent: {notification.title}")
        except WebPushException as e:
            failed_count += 1
            logger.error(f"Push notification failed: {e}")
            # If subscription is gone (410 Gone), remove it
            if e.response and e.response.status_code == 410:
                push_subscriptions.remove(subscription_info)
                logger.info("Removed expired subscription")

    return {
        "message": f"Notification sent to {sent_count} subscribers",
        "sent": sent_count,
        "failed": failed_count,
        "total_subscriptions": len(push_subscriptions)
    }

@app.post("/push/test")
async def test_push_notification():
    """Send a test push notification to all subscribers"""
    notification = PushNotification(
        title="Test Notification",
        body="This is a test notification from OptionsTaxHub!",
        tag="test"
    )
    return await send_push_notification(notification)

def run():
    port = int(os.environ.get("PORT", 8080))
    host = os.environ.get("HOST", "0.0.0.0")  # Bind to all interfaces for Render and other platforms
    import uvicorn
    uvicorn.run("main:app", host=host, port=port, reload=True)

if __name__ == "__main__":
    run()
