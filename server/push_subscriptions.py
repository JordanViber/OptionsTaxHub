"""Owner-scoped Web Push subscriptions.

Endpoint URLs and key material stay in the database. Callers never receive
them. Broadcast delivery re-checks the push-service allowlist and refuses
redirects before any request leaves the server.
"""

import base64
import binascii
import hmac
import ipaddress
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

import requests

from db import _is_unique_violation, get_supabase


logger = logging.getLogger(__name__)

PUSH_PAGE_SIZE = 500
MAX_PUSH_DEVICES_PER_USER = 10
PUSH_TTL_SECONDS = 86400
PUSH_TIMEOUT_SECONDS = 10
_MAX_ENDPOINT_LENGTH = 2048
_MAX_PAGES = 10000
_ALLOWED_HOSTS = frozenset(
    {
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "web.push.apple.com",
    }
)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_CURSOR_TIME_RE = re.compile(r"^[0-9T:+\-.Z]+$")

SAVED = "saved"
INVALID = "invalid"
CONFLICT = "conflict"
UNAVAILABLE = "unavailable"
LIMIT = "limit"


def push_http_session() -> requests.Session:
    """Session that cannot follow a push service onto another host."""
    session = requests.Session()
    original = session.post

    def post(url, *args, **kwargs):
        kwargs["allow_redirects"] = False
        return original(url, *args, **kwargs)

    session.post = post
    return session


def endpoint_is_allowed(endpoint: object) -> bool:
    """True for https URLs on the push services this app actually uses."""
    if not isinstance(endpoint, str) or not endpoint:
        return False
    if len(endpoint) > _MAX_ENDPOINT_LENGTH:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in endpoint):
        return False
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or parts.username is not None or parts.password is not None:
        return False
    host = parts.hostname
    if not host or parts.port not in (None, 443):
        return False
    host = host.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    if host in _ALLOWED_HOSTS:
        return True
    return _windows_push_host(host)


def save_push_subscription(user_id: str, subscription: dict, client=None) -> str:
    """Store an endpoint for its owner, or transfer it when the keys match."""
    parsed = _parse_subscription(subscription)
    if parsed is None or not _valid_user_id(user_id):
        return INVALID
    if client is None:
        client = get_supabase()
    if client is None:
        return UNAVAILABLE
    try:
        return _save_parsed(client, user_id, parsed, allow_insert=True)
    except Exception as exc:
        logger.error("Failed to save push subscription (%s)", type(exc).__name__)
        return UNAVAILABLE


def list_push_subscriptions(user_id: Optional[str] = None, client=None) -> Optional[list]:
    """Return subscriptions for one owner, or every row for an admin broadcast.

    Pages are keyset-ordered by created_at and id so an insert during the scan
    cannot shift later rows out of the result.
    """
    if user_id is not None and not _valid_user_id(user_id):
        return None
    if client is None:
        client = get_supabase()
    if client is None:
        return None
    rows = []
    cursor = None
    try:
        for _page in range(_MAX_PAGES):
            query = client.table("push_subscriptions").select(
                "id,user_id,endpoint,p256dh,auth,expiration_time_ms,created_at"
            )
            if user_id is not None:
                query = query.eq("user_id", user_id)
            query = (
                query.order("created_at", desc=True)
                .order("id", desc=True)
                .limit(PUSH_PAGE_SIZE)
            )
            if cursor is not None:
                created_at, row_id = cursor
                if not _safe_cursor(created_at, row_id):
                    logger.error("Push subscription page cursor was not safe")
                    return None
                query = query.or_(
                    f'created_at.lt."{created_at}",and(created_at.eq."{created_at}",id.lt."{row_id}")'
                )
            result = query.execute()
            page = [dict(row) for row in (result.data or []) if isinstance(row, dict)]
            if not page:
                return rows
            rows.extend(page)
            if len(page) < PUSH_PAGE_SIZE:
                return rows
            last = page[-1]
            created_at = last.get("created_at")
            row_id = last.get("id")
            if not created_at or not row_id:
                logger.error("Push subscription page was missing a cursor")
                return None
            nxt = (str(created_at), str(row_id))
            if nxt == cursor:
                logger.error("Push subscription page cursor did not advance")
                return None
            cursor = nxt
        logger.error("Push subscription pagination stopped at the safety limit")
        return None
    except Exception as exc:
        logger.error("Failed to list push subscriptions (%s)", type(exc).__name__)
        return None


def get_push_subscription_for_user(
    user_id: str,
    endpoint: str,
    client=None,
) -> tuple:
    """Return (row, True) for this owner, (None, True) when absent, or (None, False)."""
    if not _valid_user_id(user_id) or not isinstance(endpoint, str) or not endpoint:
        return None, False
    if client is None:
        client = get_supabase()
    if client is None:
        return None, False
    try:
        result = (
            client.table("push_subscriptions")
            .select("id,user_id,endpoint,p256dh,auth,expiration_time_ms,created_at")
            .eq("user_id", user_id)
            .eq("endpoint", endpoint)
            .limit(1)
            .execute()
        )
        if not result.data:
            return None, True
        return dict(result.data[0]), True
    except Exception as exc:
        logger.error("Failed to load push subscription (%s)", type(exc).__name__)
        return None, False


def delete_push_subscription(
    subscription_id: str,
    user_id: Optional[str] = None,
    client=None,
) -> Optional[bool]:
    """Delete one row. A user_id restricts the delete to that owner."""
    if not subscription_id:
        return False
    if user_id is not None and not _valid_user_id(user_id):
        return False
    if client is None:
        client = get_supabase()
    if client is None:
        return None
    try:
        query = client.table("push_subscriptions").delete().eq("id", subscription_id)
        if user_id is not None:
            query = query.eq("user_id", user_id)
        result = query.select("id").execute()
        return bool(result.data)
    except Exception as exc:
        logger.error("Failed to delete push subscription (%s)", type(exc).__name__)
        return None


def deliver_push(
    rows: list,
    notification: dict,
    *,
    webpush,
    vapid_private_key: str,
    vapid_claims_sub: str,
    delete_subscription,
) -> dict:
    """Deliver one payload. Expired rows are removed. One failure does not stop the rest."""
    session = push_http_session()
    sent = 0
    failed = 0
    for row in rows:
        if not isinstance(row, dict):
            failed += 1
            continue
        endpoint = row.get("endpoint")
        keys = _stored_keys(row)
        if not endpoint_is_allowed(endpoint) or keys is None:
            failed += 1
            logger.error(
                "Push delivery rejected subscription_id=%s",
                row.get("id"),
            )
            continue
        try:
            webpush(
                subscription_info={"endpoint": endpoint, "keys": keys},
                data=_notification_json(notification),
                vapid_private_key=vapid_private_key,
                vapid_claims={"sub": vapid_claims_sub},
                ttl=PUSH_TTL_SECONDS,
                timeout=PUSH_TIMEOUT_SECONDS,
                requests_session=session,
            )
            sent += 1
        except Exception as exc:
            failed += 1
            status = _status_code(exc)
            logger.error(
                "Push delivery failed subscription_id=%s status=%s",
                row.get("id"),
                status,
            )
            if status in (404, 410) and row.get("id"):
                _prune(delete_subscription, row)
    return {
        "message": f"Notification sent to {sent} subscribers",
        "sent": sent,
        "failed": failed,
        "total_subscriptions": len(rows),
    }


def _save_parsed(client, user_id: str, parsed: dict, allow_insert: bool) -> str:
    existing = _read_by_endpoint(client, parsed["endpoint"])
    if existing:
        return _update_existing(client, user_id, parsed, existing)
    if not allow_insert:
        return UNAVAILABLE
    if _device_count(client, user_id) >= MAX_PUSH_DEVICES_PER_USER:
        return LIMIT
    try:
        if not _insert(client, user_id, parsed):
            return UNAVAILABLE
        return SAVED
    except Exception as exc:
        if not _is_unique_violation(exc):
            raise
        return _save_parsed(client, user_id, parsed, allow_insert=False)


def _update_existing(client, user_id: str, parsed: dict, existing: dict) -> str:
    same_owner = str(existing.get("user_id")) == str(user_id)
    if not same_owner and not _keys_match(existing, parsed):
        return CONFLICT
    if not same_owner and _device_count(client, user_id) >= MAX_PUSH_DEVICES_PER_USER:
        return LIMIT
    values = {
        "p256dh": parsed["p256dh"],
        "auth": parsed["auth"],
        "expiration_time_ms": parsed["expiration_time_ms"],
        "updated_at": _now(),
    }
    if same_owner:
        query = (
            client.table("push_subscriptions")
            .update(values)
            .eq("id", existing.get("id"))
            .eq("user_id", user_id)
        )
    else:
        values = {**values, "user_id": user_id}
        query = (
            client.table("push_subscriptions")
            .update(values)
            .eq("id", existing.get("id"))
            .eq("user_id", existing.get("user_id"))
            .eq("p256dh", existing.get("p256dh"))
            .eq("auth", existing.get("auth"))
        )
    result = query.select("id").execute()
    if not result.data:
        return UNAVAILABLE
    return SAVED


def _insert(client, user_id: str, parsed: dict) -> bool:
    now = _now()
    result = (
        client.table("push_subscriptions")
        .insert(
            {
                "user_id": user_id,
                "endpoint": parsed["endpoint"],
                "p256dh": parsed["p256dh"],
                "auth": parsed["auth"],
                "expiration_time_ms": parsed["expiration_time_ms"],
                "created_at": now,
                "updated_at": now,
            }
        )
        .select("id")
        .execute()
    )
    return bool(result.data)


def _read_by_endpoint(client, endpoint: str):
    result = (
        client.table("push_subscriptions")
        .select("id,user_id,endpoint,p256dh,auth,expiration_time_ms,created_at")
        .eq("endpoint", endpoint)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return dict(result.data[0])


def _device_count(client, user_id: str) -> int:
    result = (
        client.table("push_subscriptions")
        .select("id")
        .eq("user_id", user_id)
        .limit(MAX_PUSH_DEVICES_PER_USER + 1)
        .execute()
    )
    return len(result.data or [])


def _parse_subscription(subscription: object) -> Optional[dict]:
    if not isinstance(subscription, dict):
        return None
    endpoint = subscription.get("endpoint")
    if not endpoint_is_allowed(endpoint):
        return None
    pair = _key_pair(subscription.get("keys"))
    if pair is None:
        return None
    expiration = subscription.get("expirationTime")
    if expiration is not None and (
        isinstance(expiration, bool) or not isinstance(expiration, (int, float))
    ):
        return None
    return {
        "endpoint": endpoint,
        "p256dh": pair[0],
        "auth": pair[1],
        "expiration_time_ms": expiration,
    }


def _key_pair(keys: object) -> Optional[tuple]:
    if not isinstance(keys, dict):
        return None
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    raw_p256dh = _decode_b64url(p256dh)
    raw_auth = _decode_b64url(auth)
    if raw_p256dh is None or raw_auth is None:
        return None
    if len(raw_p256dh) != 65 or not raw_p256dh.startswith(b"\x04") or len(raw_auth) != 16:
        return None
    return p256dh, auth


def _stored_keys(row: dict) -> Optional[dict]:
    pair = _key_pair({"p256dh": row.get("p256dh"), "auth": row.get("auth")})
    if pair is None:
        return None
    return {"p256dh": pair[0], "auth": pair[1]}


def _keys_match(existing: dict, parsed: dict) -> bool:
    left_p256dh = _decode_b64url(existing.get("p256dh"))
    left_auth = _decode_b64url(existing.get("auth"))
    right_p256dh = _decode_b64url(parsed["p256dh"])
    right_auth = _decode_b64url(parsed["auth"])
    if None in (left_p256dh, left_auth, right_p256dh, right_auth):
        return False
    if len(left_p256dh) != len(right_p256dh) or len(left_auth) != len(right_auth):
        return False
    return hmac.compare_digest(left_p256dh, right_p256dh) and hmac.compare_digest(
        left_auth, right_auth
    )


def _decode_b64url(value: object) -> Optional[bytes]:
    if not isinstance(value, str) or not _B64URL_RE.fullmatch(value):
        return None
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return None


def _windows_push_host(host: str) -> bool:
    suffix = ".notify.windows.com"
    if not host.endswith(suffix):
        return False
    labels = host[: -len(suffix)].split(".")
    return bool(labels) and all(_dns_label(label) for label in labels)


def _dns_label(label: str) -> bool:
    return (
        1 <= len(label) <= 63
        and label[0].isalnum()
        and label[-1].isalnum()
        and all(char.isalnum() or char == "-" for char in label)
    )


def _valid_user_id(user_id: object) -> bool:
    return isinstance(user_id, str) and bool(_UUID_RE.fullmatch(user_id))


def _safe_cursor(created_at: str, row_id: str) -> bool:
    return bool(
        _CURSOR_TIME_RE.fullmatch(created_at) and _UUID_RE.fullmatch(row_id)
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _notification_json(notification: dict) -> str:
    import json

    return json.dumps(
        {
            "title": notification.get("title"),
            "body": notification.get("body"),
            "icon": notification.get("icon"),
            "badge": notification.get("badge"),
            "tag": notification.get("tag"),
            "data": notification.get("data") or {},
        }
    )


def _status_code(exc: Exception):
    response = getattr(exc, "response", None)
    if response is None:
        return None
    return getattr(response, "status_code", None)


def _prune(delete_subscription, row: dict) -> None:
    try:
        delete_subscription(row)
    except Exception as exc:
        logger.error(
            "Push subscription prune failed subscription_id=%s (%s)",
            row.get("id"),
            type(exc).__name__,
        )
