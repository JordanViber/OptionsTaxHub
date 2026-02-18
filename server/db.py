"""
Supabase database client for OptionsTaxHub.

Provides a singleton Supabase client and helper functions for
portfolio analysis history and tax profile storage.

NOTE: Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env.local.
"""

import os
import logging
from typing import Optional
from dotenv import load_dotenv

load_dotenv(".env.local")
load_dotenv(".env")

logger = logging.getLogger(__name__)

# Lazy-initialized Supabase client
_supabase_client = None


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
    Create a Supabase client with user's JWT token for RLS enforcement.

    This client respects Row Level Security policies because it's initialized
    with the user's authentication token, not the service role key.

    Args:
        access_token: User's JWT access token from Supabase Auth

    Returns:
        Authenticated Supabase client or None if initialization fails
    """
    url = os.environ.get("SUPABASE_URL")
    if not url:
        logger.warning("SUPABASE_URL not set — authentication features disabled")
        return None

    try:
        from supabase import create_client

        # Create client with user's token (respects RLS)
        client = create_client(url, access_token)
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
        result = client.table("portfolio_analyses").insert(row).execute()
        if result.data:
            return dict(result.data[0])
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


# ---------- Tax Profiles ----------


def save_tax_profile(
    user_id: str,
    filing_status: str,
    estimated_annual_income: float,
    state: str,
    tax_year: int,
    ai_suggestions_enabled: bool = True,
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
            "ai_suggestions_enabled": ai_suggestions_enabled,
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
            .select("user_id, filing_status, estimated_annual_income, state, tax_year, ai_suggestions_enabled, created_at, updated_at")
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
