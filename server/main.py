import os
import logging
from typing import Annotated
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import io
import json
from typing import List, Dict, Any
from pywebpush import webpush, WebPushException
from pydantic import BaseModel

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
    # MVP: Read uploaded CSV in-memory, parse with pandas, return first 5 rows as JSON
    # This supports the portfolio rebalancer MVP by allowing users to upload CSV exports (e.g., from Robinhood)
    # for initial parsing and tax-loss harvesting suggestions (to be added later)
    contents = await file.read()
    df = pd.read_csv(io.StringIO(contents.decode("utf-8")))
    # Return first 5 rows as dict for simplicity (no persistent storage yet)
    return df.head(5).to_dict(orient="records")

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
    host = os.environ.get("HOST", "127.0.0.1")  # Use 127.0.0.1 by default for security
    import uvicorn
    uvicorn.run("main:app", host=host, port=port, reload=True)

if __name__ == "__main__":
    run()
