"""
Supabase database client for OptionsTaxHub.

Provides a singleton Supabase client and helper functions for
portfolio analysis history and tax profile storage.

NOTE: Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env.local.
"""

import os
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

_SERVER_DIR = Path(__file__).resolve().parent
load_dotenv(_SERVER_DIR / ".env.local")
load_dotenv(_SERVER_DIR / ".env")

logger = logging.getLogger(__name__)

# Lazy-initialized Supabase client
_supabase_client = None

UNPAID_PACKET_SNAPSHOT_TTL = timedelta(hours=24)


def _cleanup_expired_packet_snapshots(client) -> None:
    try:
        client.rpc("delete_expired_year_close_packet_snapshots").execute()
    except Exception:
        # Expiration filters remain authoritative if best-effort cleanup fails.
        logger.debug("Expired packet snapshot cleanup was unavailable", exc_info=True)


def get_supabase():
    """Get or create Supabase client (singleton)."""
    global _supabase_client  # noqa: PLW0603

    if _supabase_client is not None:
        return _supabase_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        logger.warning(
            "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set — "
            "history features will be disabled."
        )
        return None

    try:
        from supabase import create_client

        _supabase_client = create_client(url, key)
        logger.info("Supabase client initialized")
        return _supabase_client
    except Exception as e:
        logger.error(f"Failed to create Supabase client: {e}")
        return None


def get_supabase_with_token(access_token: str):
    """
    Create a Supabase client that enforces RLS using the user's JWT token.

    Uses the anon key for the API gateway (required by Supabase/Kong) and then
    overrides the PostgREST Authorization header with the user's JWT so that
    ``auth.uid()`` resolves correctly in RLS policies.

    Passing the JWT as the ``key`` parameter (as the service-role key) to
    ``create_client`` breaks the API gateway — only anon/service-role keys are
    valid there. This function does it correctly.

    Args:
        access_token: User's JWT access token from Supabase Auth

    Returns:
        Authenticated Supabase client or None if initialization fails
    """
    url = os.environ.get("SUPABASE_URL")
    anon_key = os.environ.get("SUPABASE_ANON_KEY")
    if not url:
        logger.warning("SUPABASE_URL not set — authentication features disabled")
        return None
    if not anon_key:
        logger.warning("SUPABASE_ANON_KEY not set — authenticated client unavailable")
        return None

    try:
        from supabase import create_client

        # Create client with anon key (passes Kong API gateway validation).
        # Then set the user's JWT on the PostgREST sub-client so that
        # auth.uid() resolves correctly and RLS is enforced.
        client = create_client(url, anon_key)
        client.postgrest.auth(access_token)
        return client
    except Exception as e:
        logger.error(f"Failed to create authenticated Supabase client: {e}")
        return None


# ---------- Portfolio History ----------


def save_analysis_history(
    user_id: str,
    filename: str,
    summary: dict,
    result_data: Optional[dict] = None,
) -> Optional[dict]:
    """
    Save a portfolio analysis summary to the portfolio_analyses table.

    Also persists the full analysis result (positions, suggestions, etc.)
    so that past reports can be re-loaded from the history sidebar.

    Returns the inserted row or None if Supabase is unavailable.
    """
    client = get_supabase()
    if client is None:
        return None

    try:
        row: dict = {
            "user_id": user_id,
            "filename": filename,
            "summary": summary,
            "positions_count": summary.get("positions_count", 0),
            "total_market_value": summary.get("total_market_value", 0),
        }
        if result_data is not None:
            row["result"] = result_data
        result = client.table("portfolio_analyses").insert(row).select("id").execute()
        if result.data:
            return dict(result.data[0])
        # Checkout depends on the returned persisted row ID. An empty
        # representation is not proof that the insert committed.
        logger.error("Analysis history insert returned no persisted row")
        return None
    except Exception as e:
        logger.error(f"Failed to save analysis history: {e}")
        return None


def get_analysis_history(
    user_id: str,
    limit: int = 20,
    client=None,
) -> list[dict]:
    """
    Retrieve past portfolio analyses for a user, newest first.

    Returns lightweight list (no full result) for the history sidebar.

    Args:
        user_id: User ID to fetch history for
        limit: Maximum number of records to return
        client: Optional authenticated Supabase client (for RLS enforcement)
                If not provided, uses service role (bypasses RLS)
    """
    if client is None:
        client = get_supabase()
    if client is None:
        return []

    try:
        result = (
            client.table("portfolio_analyses")
            .select("id, user_id, filename, uploaded_at, summary, positions_count, total_market_value")
            .eq("user_id", user_id)
            .order("uploaded_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [dict(row) for row in (result.data or [])]
    except Exception as e:
        logger.error(f"Failed to fetch analysis history: {e}")
        return []


def get_analysis_by_id(
    analysis_id: str,
    user_id: str,
    client=None,
) -> Optional[dict]:
    """
    Retrieve a single portfolio analysis by ID, including the full result.

    Filters by user_id to enforce ownership.

    Args:
        analysis_id: ID of the analysis to retrieve
        user_id: User ID (for ownership verification)
        client: Optional authenticated Supabase client (for RLS enforcement)
                If not provided, uses service role (bypasses RLS)
    """
    if client is None:
        client = get_supabase()
    if client is None:
        return None

    try:
        result = (
            client.table("portfolio_analyses")
            .select("id, user_id, filename, uploaded_at, summary, positions_count, total_market_value, result")
            .eq("id", analysis_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if result.data:
            return dict(result.data[0])
        return None
    except Exception as e:
        logger.error(f"Failed to fetch analysis by id: {e}")
        return None


def get_analysis_by_result_analysis_id(
    analysis_id: str,
    user_id: str,
    client=None,
) -> Optional[dict]:
    """Find a history row by the analysis ID embedded in its result JSONB."""
    if not analysis_id or not user_id:
        return None
    if client is None:
        client = get_supabase()
    if client is None:
        return None
    try:
        result = (
            client.table("portfolio_analyses")
            .select("id, user_id, filename, uploaded_at, summary, positions_count, total_market_value, result")
            .eq("user_id", user_id)
            .contains("result", {"analysis_id": analysis_id})
            .order("uploaded_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return dict(result.data[0])
        return None
    except Exception as e:
        logger.error("Failed to fetch analysis by embedded id: %s", e)
        return None


def _lookup_analysis_for_entitlement(
    analysis_id: str,
    user_id: str,
    client,
) -> tuple[Optional[dict], bool]:
    """Find the owner-scoped row; the bool distinguishes a miss from DB errors."""
    try:
        uuid.UUID(analysis_id)
    except (TypeError, ValueError, AttributeError):
        # The primary key is a UUID; guest and legacy IDs may be arbitrary text.
        pass
    else:
        try:
            result = (
                client.table("portfolio_analyses")
                .select("id, user_id, result")
                .eq("id", analysis_id)
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
        except Exception as e:
            logger.error("Failed to find analysis row %s: %s", analysis_id, e)
            return None, False
        if result.data:
            return dict(result.data[0]), True
    try:
        result = (
            client.table("portfolio_analyses")
            .select("id, user_id, result")
            .eq("user_id", user_id)
            .contains("result", {"analysis_id": analysis_id})
            .order("uploaded_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error("Failed to find analysis result %s: %s", analysis_id, e)
        return None, False
    if result.data:
        return dict(result.data[0]), True
    return None, True


def lookup_analysis_for_entitlement(
    analysis_id: str,
    user_id: str,
) -> tuple[Optional[dict], bool]:
    """Resolve a caller-owned history row and distinguish a miss from an outage."""
    if not analysis_id or not user_id:
        return None, False
    client = get_supabase()
    if client is None:
        return None, False
    return _lookup_analysis_for_entitlement(analysis_id, user_id, client)


def ensure_analysis_history(
    analysis_id: str,
    user_id: str,
    analysis: Optional[dict],
) -> Optional[dict]:
    """Ensure Checkout can only charge for an analysis with a durable row."""
    if not analysis_id or not user_id:
        return None
    client = get_supabase()
    if client is None:
        return None
    record, lookup_succeeded = _lookup_analysis_for_entitlement(
        analysis_id,
        user_id,
        client,
    )
    if not lookup_succeeded:
        return None
    if record:
        return record
    if not isinstance(analysis, dict) or analysis.get("analysis_id") != analysis_id:
        return None
    summary = analysis.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    filename = str(analysis.get("filename") or "year-close-packet.csv")[:255]
    # Checkout receives this value from the browser. Grant fields are written
    # only after a settled Stripe session is verified server-side.
    safe_analysis = dict(analysis)
    safe_analysis.pop("packet_unlocked", None)
    safe_analysis.pop("packet_session_id", None)
    return save_analysis_history(
        user_id,
        filename,
        summary,
        result_data=safe_analysis,
    )


def delete_analyses_without_result(user_id: str) -> int:
    """
    Delete portfolio analyses that have no stored result data.

    These are legacy entries created before we started persisting
    the full analysis result alongside the summary.

    Returns the number of rows deleted.
    """
    client = get_supabase()
    if client is None:
        return 0

    try:
        result = (
            client.table("portfolio_analyses")
            .delete()
            .eq("user_id", user_id)
            .is_("result", "null")
            .execute()
        )
        deleted = len(result.data) if result.data else 0
        if deleted:
            logger.info(
                f"Cleaned up {deleted} orphan analysis rows for user {user_id}"
            )
        return deleted
    except Exception as e:
        logger.error(f"Failed to delete orphan analyses: {e}")
        return 0


def delete_analysis_by_id(analysis_id: str, user_id: str) -> bool:
    """
    Delete a single portfolio analysis by ID.

    Filters by user_id to enforce ownership so that users can only
    delete their own records. Returns True if a row was deleted.
    """
    client = get_supabase()
    if client is None:
        return False

    try:
        result = (
            client.table("portfolio_analyses")
            .delete()
            .eq("id", analysis_id)
            .eq("user_id", user_id)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        logger.error(f"Failed to delete analysis {analysis_id}: {e}")
        return False


def get_latest_activity_book(user_id: str, client=None) -> Optional[dict]:
    """
    Newest saved analysis that includes a parsed trade book.

    Returns analysis_id, filename, raw transactions, and packet grant fields
    so a later CSV can merge instead of replacing the whole history.
    """
    if not user_id:
        return None
    if client is None:
        client = get_supabase()
    if client is None:
        return None

    try:
        result = (
            client.table("portfolio_analyses")
            .select("id, filename, uploaded_at, result")
            .eq("user_id", user_id)
            .order("uploaded_at", desc=True)
            .limit(10)
            .execute()
        )
    except Exception as e:
        logger.error(f"Failed to fetch latest activity book: {e}")
        return None

    for row in result.data or []:
        payload = row.get("result") if isinstance(row, dict) else None
        if not isinstance(payload, dict):
            continue
        book = payload.get("activity_book") or {}
        transactions = book.get("transactions") if isinstance(book, dict) else None
        if not transactions:
            transactions = payload.get("transactions")
        if not transactions:
            continue
        tax_profile = payload.get("tax_profile") or {}
        return {
            "analysis_id": row.get("id"),
            "filename": row.get("filename") or "",
            "transactions": transactions,
            "packet_unlocked": bool(payload.get("packet_unlocked")),
            "packet_session_id": payload.get("packet_session_id") or "",
            "tax_year": tax_profile.get("tax_year") if isinstance(tax_profile, dict) else None,
        }
    return None


def lookup_packet_grant_for_tax_year(
    user_id: str,
    tax_year: int,
    client=None,
) -> tuple[Optional[str], bool]:
    """Return a server-owned, paid snapshot session for the requested year."""
    if not user_id or tax_year is None:
        return None, False
    if client is None:
        client = get_supabase()
    if client is None:
        return None, False
    _cleanup_expired_packet_snapshots(client)

    try:
        snapshots = (
            client.table("year_close_packet_snapshots")
            .select("analysis_id, packet_session_id, paid_at")
            .eq("user_id", user_id)
            .eq("tax_year", int(tax_year))
            .order("created_at", desc=True)
            .execute()
        )
        for row in snapshots.data or []:
            session_id = row.get("packet_session_id") if isinstance(row, dict) else None
            if (
                isinstance(row, dict)
                and row.get("paid_at")
                and isinstance(session_id, str)
                and session_id.startswith("cs_")
            ):
                return session_id, True
    except Exception as e:
        logger.error(f"Failed to fetch packet grant: {e}")
        return None, False
    return None, True


def get_packet_grant_for_tax_year(
    user_id: str,
    tax_year: int,
    client=None,
) -> Optional[str]:
    """Return a Stripe session id if this user already paid for this tax year."""
    session_id, lookup_succeeded = lookup_packet_grant_for_tax_year(
        user_id,
        tax_year,
        client=client,
    )
    return session_id if lookup_succeeded else None


def save_packet_snapshot(
    analysis_id: str,
    user_id: str,
    tax_year: int,
    packet_payload: Optional[dict],
    *,
    session_id: Optional[str] = None,
    paid: bool = False,
    client=None,
) -> Optional[dict]:
    """Persist a private packet snapshot separately from user history."""
    if not analysis_id or not user_id or tax_year is None:
        return None
    if client is None:
        client = get_supabase()
    if client is None:
        return None
    _cleanup_expired_packet_snapshots(client)
    now = datetime.now(timezone.utc)
    paid_at = now.isoformat() if paid and session_id else None
    effective_session_id = session_id
    expires_at = None if paid_at else now + UNPAID_PACKET_SNAPSHOT_TTL
    if not paid:
        try:
            existing = (
                client.table("year_close_packet_snapshots")
                .select("packet_session_id, paid_at")
                .eq("analysis_id", analysis_id)
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            if existing.data and existing.data[0].get("paid_at"):
                paid_at = existing.data[0]["paid_at"]
                effective_session_id = existing.data[0].get("packet_session_id")
                expires_at = None
        except Exception as e:
            logger.error("Failed to check prior packet entitlement for %s: %s", analysis_id, e)
            return None
    row = {
        "analysis_id": analysis_id,
        "user_id": user_id,
        "tax_year": int(tax_year),
        "packet_payload": packet_payload,
        "packet_session_id": effective_session_id,
        "paid_at": paid_at,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "updated_at": now.isoformat(),
    }
    try:
        result = (
            client.table("year_close_packet_snapshots")
            .upsert(row, on_conflict="user_id,analysis_id")
            .select("analysis_id, user_id, tax_year")
            .execute()
        )
        return dict(result.data[0]) if result.data else None
    except Exception as e:
        logger.error("Failed to save packet snapshot for %s: %s", analysis_id, e)
        return None


def get_packet_snapshot(
    analysis_id: str,
    user_id: str,
    client=None,
) -> tuple[Optional[dict], bool]:
    """Load a caller-owned packet snapshot and distinguish outages."""
    if not analysis_id or not user_id:
        return None, False
    if client is None:
        client = get_supabase()
    if client is None:
        return None, False
    _cleanup_expired_packet_snapshots(client)
    try:
        result = (
            client.table("year_close_packet_snapshots")
            .select("analysis_id, user_id, tax_year, packet_payload, packet_session_id, paid_at, expires_at")
            .eq("analysis_id", analysis_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not result.data:
            return None, True
        snapshot = dict(result.data[0])
        if not snapshot.get("paid_at"):
            expires_at = snapshot.get("expires_at")
            try:
                if not expires_at:
                    return None, True
                expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                if expiry <= datetime.now(timezone.utc):
                    return None, True
            except (TypeError, ValueError):
                return None, True
        return snapshot, True
    except Exception as e:
        logger.error("Failed to load packet snapshot for %s: %s", analysis_id, e)
        return None, False


def mark_packet_snapshot_paid(
    analysis_id: str,
    user_id: str,
    tax_year: int,
    session_id: str,
    client=None,
) -> Optional[bool]:
    """Mark an existing owner-scoped snapshot as paid for a settled session."""
    if not analysis_id or not user_id or tax_year is None or not session_id:
        return False
    if client is None:
        client = get_supabase()
    if client is None:
        return None
    _cleanup_expired_packet_snapshots(client)
    now = datetime.now(timezone.utc)
    try:
        result = (
            client.table("year_close_packet_snapshots")
            .update({
                "packet_session_id": session_id,
                "paid_at": now.isoformat(),
                "expires_at": None,
                "updated_at": now.isoformat(),
            })
            .eq("analysis_id", analysis_id)
            .eq("user_id", user_id)
            .eq("tax_year", int(tax_year))
            .select("analysis_id")
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        logger.error("Failed to mark packet snapshot paid for %s: %s", analysis_id, e)
        return None


def patch_analysis_result(
    analysis_id: str,
    user_id: str,
    patch: dict,
) -> Optional[bool]:
    """Shallow-merge keys into a stored analysis result JSONB.

    Portfolio history row IDs predate the analysis IDs embedded in result JSON,
    so locate legacy rows by either identity while always filtering by owner.
    """
    if not analysis_id or not user_id or not patch:
        return False
    client = get_supabase()
    if client is None:
        return None
    record, lookup_succeeded = _lookup_analysis_for_entitlement(
        analysis_id,
        user_id,
        client,
    )
    if not lookup_succeeded:
        return None
    if not record or not record.get("id"):
        return False
    result = record.get("result")
    if not isinstance(result, dict):
        result = {}
    merged = {**result, **patch}
    try:
        updated = (
            client.table("portfolio_analyses")
            .update({"result": merged})
            .eq("id", record["id"])
            .eq("user_id", user_id)
            .select("id")
            .execute()
        )
        return bool(updated.data)
    except Exception as e:
        logger.error(f"Failed to patch analysis {analysis_id}: {e}")
        return None


# ---------- Tax Profiles ----------


def save_tax_profile(
    user_id: str,
    filing_status: str,
    estimated_annual_income: float,
    state: str,
    tax_year: int,
) -> Optional[dict]:
    """
    Upsert user's tax profile to the tax_profiles table.

    Uses ON CONFLICT (user_id) to update if a profile already exists.
    Returns the saved row or None if Supabase is unavailable.
    """
    client = get_supabase()
    if client is None:
        return None

    try:
        row = {
            "user_id": user_id,
            "filing_status": filing_status,
            "estimated_annual_income": estimated_annual_income,
            "state": state,
            "tax_year": tax_year,
            "updated_at": "now()",
        }
        result = (
            client.table("tax_profiles")
            .upsert(row, on_conflict="user_id")
            .execute()
        )
        if result.data:
            return dict(result.data[0])
        return None
    except Exception as e:
        logger.error(f"Failed to save tax profile: {e}")
        return None


def get_tax_profile(user_id: str) -> Optional[dict]:
    """
    Retrieve a user's saved tax profile.

    Returns the profile dict or None if not found / Supabase unavailable.
    """
    client = get_supabase()
    if client is None:
        return None

    try:
        result = (
            client.table("tax_profiles")
            .select("user_id, filing_status, estimated_annual_income, state, tax_year, created_at, updated_at")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if result.data:
            return dict(result.data[0])
        return None
    except Exception as e:
        logger.error(f"Failed to fetch tax profile: {e}")
        return None
