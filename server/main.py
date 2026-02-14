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
from db import save_analysis_history, get_analysis_history

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


@app.post("/api/portfolio/analyze", response_model=PortfolioAnalysis)
async def analyze_portfolio(
    file: Annotated[UploadFile, File()],
    filing_status: Optional[str] = Query(default="single"),
    estimated_income: Optional[float] = Query(default=75000.0),
    tax_year: Optional[int] = Query(default=2025),
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
    file_content = contents.decode("utf-8")

    tax_lots, transactions, parse_errors = parse_csv(file_content)

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
        estimated_annual_income=estimated_income or 75000.0,
        tax_year=tax_year or 2025,
    )

    # Collect warnings from all stages
    all_warnings = list(parse_errors)

    # Fetch live prices for all symbols
    symbols = list({lot.symbol for lot in tax_lots})
    fallback_prices = {
        lot.symbol: lot.current_price
        for lot in tax_lots
        if lot.current_price is not None
    }

    live_prices, price_warnings = await fetch_current_prices(
        symbols=symbols,
        fallback_prices=fallback_prices,
    )
    all_warnings.extend(price_warnings)

    # Update lots with live prices
    for lot in tax_lots:
        if lot.symbol in live_prices:
            lot.current_price = live_prices[lot.symbol]

    # Compute P&L, holding periods, and long-term status
    tax_lots = compute_lot_metrics(tax_lots)

    # Detect wash sales from transaction history
    wash_sale_flags = []
    if transactions:
        wash_sale_flags = detect_wash_sales(transactions)
        if wash_sale_flags:
            tax_lots = adjust_lots_for_wash_sales(tax_lots, wash_sale_flags)

    # Get AI-powered suggestions
    ai_positions = prepare_positions_for_ai(tax_lots)
    ai_suggestions = None
    if ai_positions:
        try:
            ai_suggestions = await get_ai_suggestions(ai_positions)
        except Exception as e:
            logger.error(f"AI advisor failed: {e}")
            all_warnings.append(
                "AI-powered suggestions unavailable. Using default replacement mappings."
            )

    # Generate harvesting suggestions
    suggestions = generate_suggestions(
        tax_lots=tax_lots,
        transactions=transactions,
        tax_profile=tax_profile,
        ai_suggestions=ai_suggestions,
    )

    # Aggregate into positions
    positions = aggregate_positions(tax_lots)

    # Build summary
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

    # Save analysis summary to history (non-blocking, best-effort)
    if user_id:
        try:
            await save_analysis_history(
                user_id=user_id,
                filename=file.filename or "upload.csv",
                summary=summary.model_dump() if hasattr(summary, "model_dump") else dict(summary),
            )
        except Exception as e:
            logger.warning(f"Failed to save analysis history: {e}")

    return result


@app.get("/api/portfolio/history/{user_id}")
async def get_portfolio_history(user_id: str, limit: int = Query(default=20, ge=1, le=100)):
    """
    Retrieve a user's past portfolio analyses, newest first.

    Returns summary metadata (filename, date, positions count, market value)
    without the full position data (which is processed in-memory only).
    """
    history = await get_analysis_history(user_id, limit)
    return history


@app.get("/api/prices")
async def get_prices(symbols: str = Query(..., description="Comma-separated ticker symbols")):
    """Fetch current prices for given symbols via yfinance."""
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="No symbols provided")

    prices, warnings = await fetch_current_prices(symbol_list)
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
async def save_tax_profile(profile: TaxProfile):
    """
    Save user's tax profile settings.

    TODO: Persist to Supabase when database tables are created.
    Currently returns the profile as confirmation.
    """
    # For now, return the validated profile
    # Supabase persistence will be added when the tax_profiles table is created
    return {
        "message": "Tax profile saved",
        "profile": profile.model_dump(),
    }


@app.get("/api/tax-profile/{user_id}")
async def get_tax_profile(user_id: str):
    """
    Retrieve a user's saved tax profile.

    TODO: Fetch from Supabase when database tables are created.
    Currently returns default profile.
    """
    # Return default profile until Supabase table is created
    default_profile = TaxProfile(user_id=user_id)
    return default_profile.model_dump()

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
