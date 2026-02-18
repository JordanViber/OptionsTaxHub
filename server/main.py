import os
import logging
import re
from typing import Annotated, Optional
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, Query, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import io
import json
from typing import List, Dict, Any
from pywebpush import webpush, WebPushException
from pydantic import BaseModel

from auth import get_current_user, enforce_ownership
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

# Get environment variables
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
DATABASE_URL = os.environ.get("DATABASE_URL")
API_KEY_SECRET = os.environ.get("API_KEY_SECRET")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "admin@optionstaxhub.com")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for startup and shutdown events."""
    # Startup
    logger.info("Running startup validation...")

    # Warn if Stripe is not configured (it's optional for MVP)
    if not STRIPE_SECRET_KEY:
        logger.warning(
            "STRIPE_SECRET_KEY not set. Stripe tip/donation endpoints will return 503."
        )
    else:
        logger.info("Stripe API key configured successfully.")

    yield

    # Shutdown (if needed in future)
    logger.info("Shutting down...")

app = FastAPI(lifespan=lifespan)

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

def validate_user_id(user_id: Optional[str]) -> None:
    """
    Validate user_id format to prevent injection attacks.

    Accepts UUID format (with or without hyphens) or alphanumeric strings up to 64 chars.
    Raises HTTPException if invalid.
    """
    if user_id is None:
        return

    # Allow UUID format (8-4-4-4-12 hex digits with optional hyphens)
    uuid_pattern = r'^[a-f0-9]{8}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{12}$'
    # Allow alphanumeric with underscores/hyphens, max 64 chars
    safe_pattern = r'^[a-zA-Z0-9_-]{1,64}$'

    if not (re.match(uuid_pattern, user_id, re.IGNORECASE) or re.match(safe_pattern, user_id)):
        raise HTTPException(
            status_code=400,
            detail="Invalid user_id format. Must be UUID or alphanumeric string (max 64 chars)."
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


def _get_user_ai_preferences(user_id: str) -> bool:
    """
    Check user's saved profile to determine if AI suggestions are enabled.

    Returns True if user has opted in to AI suggestions, False otherwise.
    """
    if not user_id:
        return False

    try:
        saved_profile = db_get_tax_profile(user_id)
        return saved_profile.get("ai_suggestions_enabled", False) if saved_profile else False
    except Exception:
        return False


def _process_ai_suggestions(
    ai_enabled: bool,
    tax_lots: list,
    all_warnings: list,
) -> tuple[Optional[list], list]:
    """
    Get AI-powered suggestions if user has opted in.

    Returns (ai_suggestions, updated_warnings) tuple.
    """
    ai_suggestions = None
    warnings = all_warnings[:]  # Copy to avoid mutation

    if ai_enabled:
        ai_suggestions = _try_get_ai_suggestions(tax_lots, warnings)
    else:
        warnings.append(
            "AI-powered suggestions are disabled. Enable them in Settings to get personalized replacement security recommendations."
        )

    return ai_suggestions, warnings


@app.post("/api/portfolio/analyze", response_model=PortfolioAnalysis)
async def analyze_portfolio(
    file: Annotated[UploadFile, File()],
    filing_status: Optional[str] = Query(default="single"),
    estimated_income: Optional[float] = Query(default=75000.0),
    tax_year: Optional[int] = Query(default=2025),
    user_id: str = Depends(get_current_user),
):
    """
    Full portfolio analysis with tax-loss harvesting suggestions.

    Accepts a CSV file (Robinhood transaction history or simplified format),
    fetches live prices, runs tax engine, detects wash sales, and generates
    AI-powered harvesting suggestions.

    **Authentication Required**: Must provide valid Supabase JWT token in Authorization header.
    User ID is automatically extracted from the token.

    DISCLAIMER: For educational/simulation purposes only — not financial or tax advice.
    """
    # Validate user_id format if provided
    validate_user_id(user_id)

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

    # Build tax profile from query params, but check user's saved profile for AI consent
    try:
        fs = FilingStatus(filing_status)
    except ValueError:
        fs = FilingStatus.SINGLE

    # Check if user has AI suggestions enabled in their saved profile
    ai_enabled = _get_user_ai_preferences(user_id)

    tax_profile = TaxProfile(
        filing_status=fs,
        estimated_annual_income=estimated_income or 75000.0,
        tax_year=tax_year or 2025,
        ai_suggestions_enabled=ai_enabled,
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

    # Get AI-powered suggestions if user has opted in
    ai_suggestions, all_warnings = _process_ai_suggestions(ai_enabled, tax_lots, all_warnings)

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

    # Save analysis to history for authenticated user
    _save_history_best_effort(user_id, file.filename or "upload.csv", summary, result)

    return result


@app.get("/api/portfolio/history")
async def get_portfolio_history(
    user_id: str = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
):
    """
    Retrieve authenticated user's past portfolio analyses, newest first.

    Returns summary metadata (filename, date, positions count, market value)
    without the full position data (which is processed in-memory only).

    **Authentication Required**: Must provide valid Supabase JWT token.
    """
    history = get_analysis_history(user_id, limit)
    return history


@app.get("/api/portfolio/analysis/{analysis_id}")
async def get_portfolio_analysis(
    analysis_id: str,
    user_id: str = Depends(get_current_user),
):
    """
    Retrieve a single past portfolio analysis by ID, including the full result.

    Used when a user clicks a history item to reload that report.

    **Authentication Required**: Must provide valid Supabase JWT token.
    **Authorization**: User can only access their own analyses.
    """
    record = get_analysis_by_id(analysis_id, user_id)
    if not record:
        raise HTTPException(status_code=404, detail="Analysis not found")
    # Enforce ownership
    enforce_ownership(user_id, record.get("user_id", ""))
    return record


@app.delete("/api/portfolio/history/cleanup")
async def cleanup_orphan_history(user_id: str = Depends(get_current_user)):
    """
    Delete portfolio analysis entries that have no stored result data.

    These are legacy rows created before the app started persisting
    the full analysis result. Returns the count of deleted rows.

    **Authentication Required**: Must provide valid Supabase JWT token.
    """
    deleted = delete_analyses_without_result(user_id)
    return {"deleted": deleted}


@app.delete("/api/portfolio/analysis/{analysis_id}")
async def delete_portfolio_analysis(
    analysis_id: str,
    user_id: str = Depends(get_current_user),
):
    """
    Delete a single portfolio analysis by ID.

    **Authentication Required**: Must provide valid Supabase JWT token.
    **Authorization**: User can only delete their own analyses.
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
async def save_tax_profile_endpoint(
    profile: TaxProfile,
    user_id: str = Depends(get_current_user),
):
    """
    Save authenticated user's tax profile settings to Supabase.

    Upserts the profile so each user has exactly one row.
    Falls back to echo-only if Supabase is unavailable.

    **Authentication Required**: Must provide valid Supabase JWT token.
    **Authorization**: User can only save their own tax profile.
    """
    # Enforce ownership: ensure authenticated user matches the profile owner
    if profile.user_id and profile.user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot save tax profile for another user"
        )

    saved = db_save_tax_profile(
        user_id=user_id,
        filing_status=profile.filing_status.value,
        estimated_annual_income=profile.estimated_annual_income,
        state=profile.state,
        tax_year=profile.tax_year,
    )

    if saved:
        return {"message": "Tax profile saved", "profile": saved}

    # Fallback: return the validated profile even if DB is unavailable
    return {"message": "Tax profile saved (not persisted)", "profile": profile.model_dump()}


@app.get("/api/tax-profile")
async def get_tax_profile_endpoint(user_id: str = Depends(get_current_user)):
    """
    Retrieve authenticated user's saved tax profile from Supabase.

    Returns default profile if no saved profile exists.

    **Authentication Required**: Must provide valid Supabase JWT token.
    """
    saved = db_get_tax_profile(user_id)
    if saved:
        return saved

    # No saved profile — return defaults
    default_profile = TaxProfile(user_id=user_id)
    return default_profile.model_dump()


# --- Stripe Tip/Donation Endpoints ---

import stripe

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
