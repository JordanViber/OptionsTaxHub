"""
Supabase database client for OptionsTaxHub.

Provides a singleton Supabase client and helper functions for
portfolio analysis history and tax profile storage.

NOTE: Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env.local.
"""

import os
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

from ledger import is_sample_csv_filename, strip_book_transactions_dict

_SERVER_DIR = Path(__file__).resolve().parent
load_dotenv(_SERVER_DIR / ".env.local")
load_dotenv(_SERVER_DIR / ".env")

logger = logging.getLogger(__name__)


class HistoryInsertConflict(Exception):
    """Another request already inserted this user's analysis row."""


class PacketSnapshotYearConflict(Exception):
    """A paid snapshot for this spelling already belongs to another tax year.

    The stored analysis_id is left unchanged. Callers map this to 409.
    """

    def __init__(self, stored_analysis_id, stored_tax_year, requested_tax_year):
        self.stored_analysis_id = stored_analysis_id
        self.stored_tax_year = stored_tax_year
        self.requested_tax_year = requested_tax_year
        super().__init__(
            "Paid packet %s is tax year %s, not %s"
            % (stored_analysis_id, stored_tax_year, requested_tax_year)
        )


class AnalysisSchemaError(Exception):
    """portfolio_analyses is missing the result column or the table itself.

    save_analysis_history raises this so a fresh deploy with an incomplete
    schema fails visibly. A missing Supabase client still returns None.
    """


_ANALYSIS_SCHEMA_ERROR = (
    "Cannot save analysis history because portfolio_analyses is missing "
    "the result column or the table. Apply the versioned SQL files in "
    "server/migrations/ in filename order. Those files add nullable result "
    "JSONB and the unique index on (user_id, result->>'analysis_id'). "
    "No other manual schema SQL is required."
)


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


# 012 rewrites only this 36-character hyphenated form. Braces, URN, and
# 32-hex text are not UUIDs here, so the app and the migration agree.
_HYPHENATED_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _canonical_analysis_uuid(value) -> Optional[str]:
    """Return the lowercase UUID string, or None when value is not a UUID.

    Only the 36-character hyphenated form counts. ``uuid.UUID`` also accepts
    braces, ``urn:uuid:``, and 32 hex digits; those stay non-UUID text.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _HYPHENATED_UUID_RE.fullmatch(text):
        return None
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError):
        return None


def _canonical_analysis_id(value) -> Optional[str]:
    """Canonical analysis id for writes and for exact reads.

    A 36-character hyphenated UUID becomes ``str(uuid.UUID(text))``:
    lowercase and hyphenated. Braces, URN, and 32-hex text are stripped and
    not casefolded. Any other non-empty string is returned stripped and is
    not casefolded, so ``local-analysis`` and distinct test ids stay
    distinct. Non-strings and blank strings are None.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    parsed = _canonical_analysis_uuid(text)
    if parsed is not None:
        return parsed
    return text


class _SnapshotReadOutage(Exception):
    """A snapshot or entitlement read failed. Callers must not treat it as a miss."""


def _history_result_for_insert(
    result_data: Optional[dict],
) -> tuple[Optional[dict], Optional[str]]:
    """Copy result JSON and choose the row id. Does not mutate result_data.

    A UUID analysis_id is stored in lowercase canonical form on both the
    primary key and the result JSON. A missing id is generated once and
    written into both fields. Any other embedded id is stripped, not
    casefolded, and stays in JSON only, because portfolio_analyses.id is a
    UUID column.
    """
    if not isinstance(result_data, dict):
        return result_data, None
    copied = dict(result_data)
    raw = copied.get("analysis_id")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        canonical = str(uuid.uuid4())
        copied["analysis_id"] = canonical
        return copied, canonical
    canonical = _canonical_analysis_id(raw) if isinstance(raw, str) else None
    if canonical is None:
        return copied, None
    copied["analysis_id"] = canonical
    if _canonical_analysis_uuid(canonical) is None:
        return copied, None
    return copied, canonical


def _inserted_history_row(client, row: dict) -> Optional[dict]:
    result = client.table("portfolio_analyses").insert(row).select("id").execute()
    if result.data:
        return dict(result.data[0])
    # Checkout depends on the returned persisted row ID. An empty
    # representation is not proof that the insert committed.
    logger.error("Analysis history insert returned no persisted row")
    return None


def _user_already_has_analysis(client, user_id: str, embedded_id) -> Optional[bool]:
    """Whether this user already stored the embedded id.

    None means the lookup failed, so the caller must not treat the insert as
    either a duplicate or a success.
    """
    if not isinstance(embedded_id, str) or not embedded_id.strip():
        return False
    record, lookup_succeeded = _lookup_analysis_for_entitlement(
        embedded_id.strip(),
        user_id,
        client,
    )
    if not lookup_succeeded:
        return None
    return record is not None


def _stored_row_matches_uuid(row, user_id: str, canonical: str) -> bool:
    """True when a stored history row is this user's copy of the UUID."""
    if not isinstance(row, dict):
        return False
    owner = row.get("user_id")
    result = row.get("result") if isinstance(row.get("result"), dict) else None
    # Insert representations echo only an id. They are not a stored analysis.
    if owner is None and result is None:
        return False
    if owner is not None and owner != user_id:
        return False
    row_id = _canonical_analysis_uuid(str(row.get("id") or ""))
    embedded = None
    if result is not None and isinstance(result.get("analysis_id"), str):
        embedded = _canonical_analysis_uuid(result.get("analysis_id"))
    return row_id == canonical or embedded == canonical


def _query_embedded_analysis_id(
    client,
    user_id: str,
    operator: str,
    canonical: str,
    *,
    exclude_id: Optional[str] = None,
):
    """One embedded-id read. ``exclude_id`` drops that row before ``limit(1)``.

    The history re-case check passes the row being patched. Without that
    exclusion, ``ilike`` can return the current row and hide the sibling.
    """
    query = (
        client.table("portfolio_analyses")
        .select("id, user_id, result")
        .eq("user_id", user_id)
        .filter("result->>analysis_id", operator, canonical)
    )
    if exclude_id is not None:
        query = query.neq("id", exclude_id)
    return query.limit(1).execute()


def _user_has_case_insensitive_embedded_id(
    client,
    user_id: str,
    canonical: str,
) -> Optional[bool]:
    """Whether this user already stored this UUID.

    Exact match first. A case-insensitive read runs when that misses or the
    exact row does not match, so an unmigrated uppercase JSON id still blocks
    a second insert. A payload that is not a list is an outage (None), not a
    miss. None means the lookup failed, including a failed fallback. The
    unique index is case-sensitive, so a different spelling would otherwise
    insert a second row.
    """
    try:
        result = _query_embedded_analysis_id(client, user_id, "eq", canonical)
    except Exception as exc:
        logger.error(
            "Could not compare analysis id case for user %s: %s",
            user_id,
            exc,
        )
        return None
    data = getattr(result, "data", None)
    if not isinstance(data, list):
        return None
    if data and _stored_row_matches_uuid(data[0], user_id, canonical):
        return True
    if _canonical_analysis_uuid(canonical) is None:
        return False
    # TODO(remove after 012 applied): legacy mixed-case embedded analysis_id.
    try:
        result = _query_embedded_analysis_id(client, user_id, "ilike", canonical)
    except Exception as exc:
        logger.error(
            "Could not compare legacy analysis id case for user %s: %s",
            user_id,
            exc,
        )
        return None
    data = getattr(result, "data", None)
    if not isinstance(data, list):
        return None
    if not data:
        return False
    return _stored_row_matches_uuid(data[0], user_id, canonical)


def _other_history_row_embeds_uuid(data, row_id: str) -> Optional[bool]:
    """True when a different row is in this payload. None when the payload is unusable."""
    if not isinstance(data, list):
        return None
    for row in data:
        if isinstance(row, dict) and str(row.get("id") or "") != str(row_id or ""):
            return True
    return False


def canonical_history_embedded_on_other_row(
    user_id: str,
    canonical: str,
    row_id: str,
) -> Optional[bool]:
    """True when a different history row already embeds this canonical UUID.

    None means the lookup failed, including a non-list payload. Callers must
    not re-case on None: that can create the second history UUID migration
    012 aborts on. An empty list is a real miss.
    """
    if not user_id or not canonical or _canonical_analysis_uuid(canonical) is None:
        return False
    client = get_supabase()
    if client is None:
        return None
    try:
        result = _query_embedded_analysis_id(
            client, user_id, "eq", canonical, exclude_id=row_id
        )
    except Exception as exc:
        logger.error(
            "Could not check canonical history id for user %s: %s",
            user_id,
            exc,
        )
        return None
    data = getattr(result, "data", None)
    if not isinstance(data, list):
        return None
    if data:
        return _other_history_row_embeds_uuid(data, row_id)
    # TODO(remove after 012 applied): legacy mixed-case embedded analysis_id.
    try:
        result = _query_embedded_analysis_id(
            client, user_id, "ilike", canonical, exclude_id=row_id
        )
    except Exception as exc:
        logger.error(
            "Could not check legacy canonical history id for user %s: %s",
            user_id,
            exc,
        )
        return None
    data = getattr(result, "data", None)
    if not isinstance(data, list):
        return None
    if not data:
        return False
    return _other_history_row_embeds_uuid(data, row_id)


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

    When the result carries a UUID analysis_id, the canonical value is the row
    id and the result JSON. The same UUID in another letter case is the same
    analysis for this user. Non-UUID ids are stripped, not casefolded. The
    primary key is global,
    while the embedded id is unique per user. If another user already owns
    that primary key, the insert is retried without an id so the database
    assigns one. Summary-only inserts leave the database default in place.

    Returns the inserted row, or None when Supabase is not configured.
    Raises HistoryInsertConflict when this user already has the analysis.
    Raises AnalysisSchemaError when the result column or table is missing.
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
            stored_result, row_id = _history_result_for_insert(result_data)
            row["result"] = stored_result
            if row_id:
                row["id"] = row_id
        embedded_id = None
        if isinstance(row.get("result"), dict):
            embedded_id = row["result"].get("analysis_id")
        if row.get("id"):
            # The per-user unique index compares embedded text as written.
            # A pre-existing row can spell the same UUID differently and use
            # a different primary key, so check before inserting.
            already_saved = _user_has_case_insensitive_embedded_id(
                client,
                user_id,
                row["id"],
            )
            if already_saved is None:
                logger.error(
                    "Could not tell whether user %s already saved analysis %s",
                    user_id,
                    embedded_id,
                )
                return None
            if already_saved:
                raise HistoryInsertConflict(
                    "portfolio analysis history already exists for this analysis"
                )
        try:
            return _inserted_history_row(client, row)
        except Exception as insert_error:
            if not _is_unique_violation(insert_error) or not row.get("id"):
                raise
            already_saved = _user_already_has_analysis(client, user_id, embedded_id)
            if already_saved is False:
                already_saved = _user_has_case_insensitive_embedded_id(
                    client,
                    user_id,
                    row["id"],
                )
            if already_saved is None:
                logger.error(
                    "Could not tell whether user %s already saved analysis %s",
                    user_id,
                    embedded_id,
                )
                return None
            if already_saved:
                raise HistoryInsertConflict(
                    "portfolio analysis history already exists for this analysis"
                ) from insert_error
            # Another account owns this primary key. Keep the embedded id and
            # let the database assign a row id for this user.
            # The concurrent cross-user retry race is accepted. New ids are
            # lowercase uuid4, and a lower() unique index would require
            # collapsing duplicates inside migration 012, which is riskier
            # than the race.
            retry = dict(row)
            retry.pop("id", None)
            try:
                return _inserted_history_row(client, retry)
            except Exception as retry_error:
                if not _is_unique_violation(retry_error):
                    raise
                already_saved = _user_already_has_analysis(
                    client, user_id, embedded_id
                )
                if already_saved:
                    raise HistoryInsertConflict(
                        "portfolio analysis history already exists for this analysis"
                    ) from retry_error
                logger.error(
                    "Analysis history insert conflict was not this user's row: %s",
                    retry_error,
                )
                return None
    except (HistoryInsertConflict, AnalysisSchemaError):
        raise
    except Exception as e:
        if _is_unique_violation(e):
            raise HistoryInsertConflict(
                "portfolio analysis history already exists for this analysis"
            ) from e
        if _is_missing_analysis_schema(e):
            logger.error("Analysis history schema prerequisite is missing: %s", e)
            raise AnalysisSchemaError(
                f"{_ANALYSIS_SCHEMA_ERROR} Database reported: {e}"
            ) from e
        logger.error(f"Failed to save analysis history: {e}")
        return None


def clear_unconfirmed_history_counts(
    summary: dict,
    result_data: Optional[dict],
) -> tuple[dict, Optional[dict]]:
    """Zero trade counts on a history copy that did not land in the private book.

    A redacted row with a positive count is a stripped marker. The next auto
    upload would then refuse an older transaction list. Callers pass copies;
    this does not mutate those inputs.
    """
    summary_out = dict(summary) if isinstance(summary, dict) else {}
    if not isinstance(result_data, dict):
        if "activity_transaction_count" in summary_out:
            summary_out["activity_transaction_count"] = 0
        return summary_out, result_data
    result_out = dict(result_data)
    book = result_out.get("activity_book")
    has_book = isinstance(book, dict)
    if has_book:
        result_out["activity_book"] = {**book, "transaction_count": 0}
    nested = result_out.get("summary")
    if isinstance(nested, dict) and (
        has_book or "activity_transaction_count" in nested
    ):
        nested_out = dict(nested)
        nested_out["activity_transaction_count"] = 0
        result_out["summary"] = nested_out
    if has_book or "activity_transaction_count" in summary_out:
        summary_out["activity_transaction_count"] = 0
    return summary_out, result_out


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


_ANALYSIS_ROW_COLUMNS = (
    "id, user_id, filename, uploaded_at, summary, positions_count, "
    "total_market_value, result"
)
_ANALYSIS_LOOKUP_COLUMNS = "id, user_id, result"


def _analysis_row(result) -> Optional[dict]:
    data = getattr(result, "data", None) or []
    if not data:
        return None
    return dict(data[0])


def _is_invalid_uuid_query(exc: Exception) -> bool:
    """True when Postgres rejected a non-UUID primary-key comparison."""
    code = _exception_code(exc).upper()
    if code == "22P02":
        return True
    text = _exception_text(exc)
    return "invalid input syntax for type uuid" in text or "invalid uuid" in text


def _fetch_analysis_by_primary_key(client, analysis_id: str, user_id: str, columns: str):
    canonical = _canonical_analysis_uuid(analysis_id)
    query_id = canonical if canonical is not None else analysis_id
    return (
        client.table("portfolio_analyses")
        .select(columns)
        .eq("id", query_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )


def _fetch_analysis_by_embedded_id(client, analysis_id: str, user_id: str, columns: str):
    """Find a row by result.analysis_id.

    UUID ids use an exact match on the canonical text. A case-insensitive
    read runs only when that misses, so an unmigrated uppercase JSON id is
    still found. A failed fallback is an outage, not a miss. Non-UUID ids
    stay an exact contains match and are not casefolded.
    """
    canonical = _canonical_analysis_id(analysis_id) or analysis_id

    def _run(match):
        query = (
            client.table("portfolio_analyses")
            .select(columns)
            .eq("user_id", user_id)
        )
        query = match(query)
        return query.order("uploaded_at", desc=True).limit(1).execute()

    if _canonical_analysis_uuid(canonical) is None:
        return _run(lambda query: query.contains("result", {"analysis_id": canonical}))
    exact = _run(
        lambda query: query.filter("result->>analysis_id", "eq", canonical)
    )
    if getattr(exact, "data", None):
        return exact
    # TODO(remove after 012 applied): legacy mixed-case embedded analysis_id.
    return _run(
        lambda query: query.filter("result->>analysis_id", "ilike", canonical)
    )


def _fetch_analysis_alias(client, analysis_id: str, user_id: str):
    return (
        client.table("portfolio_analysis_id_aliases")
        .select("canonical_analysis_id")
        .eq("user_id", user_id)
        .eq("legacy_row_id", analysis_id)
        .limit(1)
        .execute()
    )


def _resolve_owned_analysis(
    analysis_id: str,
    user_id: str,
    client,
    *,
    full: bool,
    strict_outage: bool,
) -> tuple[Optional[dict], bool]:
    """Find one owner row by id, then embedded analysis_id, then alias.

    ``strict_outage`` is for payment lookups: a database error is ``(None, False)``.
    Reads used by history restore treat those errors as a miss. Only a missing
    portfolio_analysis_id_aliases table is a miss. Any other alias error on the
    payment path stays an outage, so an install that has not applied migration
    012 does not fail the request and a schema or network error does not look
    like a missing analysis.
    """
    canonical_id = _canonical_analysis_id(analysis_id)
    if not canonical_id or not user_id or client is None:
        return None, False
    analysis_id = canonical_id
    columns = _ANALYSIS_ROW_COLUMNS if full else _ANALYSIS_LOOKUP_COLUMNS

    def failed(exc: Exception, label: str) -> tuple[Optional[dict], bool]:
        logger.error("Failed to find analysis %s by %s: %s", analysis_id, label, exc)
        if strict_outage:
            return None, False
        return None, True

    id_is_uuid = _canonical_analysis_uuid(analysis_id) is not None
    # Payment lookups skip the UUID primary key for ids such as "analysis-1".
    # Postgres would reject the cast, and a scripted miss is the embedded query.
    # History reads still try id first and fall through on an invalid-uuid error.
    row = None
    if id_is_uuid or not strict_outage:
        try:
            row = _analysis_row(
                _fetch_analysis_by_primary_key(client, analysis_id, user_id, columns)
            )
        except Exception as exc:
            if not _is_invalid_uuid_query(exc):
                return failed(exc, "id")
            row = None
        if row:
            return row, True

    try:
        row = _analysis_row(
            _fetch_analysis_by_embedded_id(client, analysis_id, user_id, columns)
        )
    except Exception as exc:
        return failed(exc, "embedded id")
    if row:
        return row, True

    if not id_is_uuid:
        return None, True
    try:
        alias = _analysis_row(_fetch_analysis_alias(client, analysis_id, user_id))
    except Exception as exc:
        if _is_missing_alias_table(exc):
            logger.info(
                "portfolio_analysis_id_aliases is missing; analysis lookup "
                "continues without legacy ids"
            )
            return None, True
        return failed(exc, "alias")
    if not alias:
        return None, True
    canonical = str(alias.get("canonical_analysis_id") or "").strip()
    if not canonical or canonical == analysis_id:
        return None, True
    try:
        row = _analysis_row(
            _fetch_analysis_by_primary_key(client, canonical, user_id, columns)
        )
    except Exception as exc:
        return failed(exc, "alias target")
    if row:
        return row, True
    try:
        row = _analysis_row(
            _fetch_analysis_by_embedded_id(client, canonical, user_id, columns)
        )
    except Exception as exc:
        return failed(exc, "alias target embedded id")
    return (row, True) if row else (None, True)


def get_analysis_by_id(
    analysis_id: str,
    user_id: str,
    client=None,
) -> Optional[dict]:
    """
    Retrieve a single portfolio analysis by ID, including the full result.

    Resolves portfolio_analyses.id, then result.analysis_id, then
    portfolio_analysis_id_aliases. Filters by user_id on every query.
    A missing alias table is a miss, not an error.

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
        record, _lookup_succeeded = _resolve_owned_analysis(
            analysis_id,
            user_id,
            client,
            full=True,
            strict_outage=False,
        )
    except Exception as e:
        logger.error(f"Failed to fetch analysis by id: {e}")
        return None
    return record


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
        result = _fetch_analysis_by_embedded_id(
            client,
            analysis_id,
            user_id,
            "id, user_id, filename, uploaded_at, summary, positions_count, "
            "total_market_value, result",
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
    """Find the owner-scoped row; the bool distinguishes a miss from DB errors.

    Order is portfolio_analyses.id, then result.analysis_id, then
    portfolio_analysis_id_aliases. Only a missing alias table is a miss.
    """
    return _resolve_owned_analysis(
        analysis_id,
        user_id,
        client,
        full=False,
        strict_outage=True,
    )


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
    # Canonicalize the argument and the body id before comparing, so an UPPER
    # body still matches the caller's original spelling (and the reverse).
    # The caller's dict is not rewritten; the insert stores the lowercase UUID.
    canonical_id = _canonical_analysis_id(analysis_id) or ""
    if not canonical_id or not user_id:
        return None
    client = get_supabase()
    if client is None:
        return None
    record, lookup_succeeded = _lookup_analysis_for_entitlement(
        canonical_id,
        user_id,
        client,
    )
    if not lookup_succeeded:
        return None
    if record:
        return record
    if not isinstance(analysis, dict):
        return None
    body_raw = analysis.get("analysis_id")
    body_id = _canonical_analysis_id(body_raw) if isinstance(body_raw, str) else None
    if body_id != canonical_id:
        return None
    summary = analysis.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    filename = str(analysis.get("filename") or "year-close-packet.csv")[:255]
    # Checkout receives this value from the browser. Grant fields are written
    # only after a settled Stripe session is verified server-side.
    safe_analysis = strip_book_transactions_dict(dict(analysis))
    safe_analysis.pop("packet_unlocked", None)
    safe_analysis.pop("packet_session_id", None)
    safe_analysis.pop("transactions", None)
    summary, safe_analysis = clear_unconfirmed_history_counts(summary, safe_analysis)
    try:
        return save_analysis_history(
            user_id,
            filename,
            summary,
            result_data=safe_analysis,
        )
    except HistoryInsertConflict:
        raced, race_ok = _lookup_analysis_for_entitlement(
            canonical_id,
            user_id,
            client,
        )
        if not race_ok:
            return None
        return raced


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


def list_legacy_alias_ids(
    canonical_analysis_id: str,
    user_id: str,
) -> Optional[list]:
    """Legacy row ids aliased to this user and canonical analysis.

    An empty list means there are no aliases, or migration 012 is not applied.
    None means the lookup failed. Callers must not treat that as no aliases:
    a PostgREST schema-cache miss still has the rows, and forgetting nothing
    would leave the rewritten analysis reachable by its old id.
    """
    canonical_analysis_id = _canonical_analysis_id(canonical_analysis_id) or ""
    if not canonical_analysis_id or not user_id:
        return []
    client = get_supabase()
    if client is None:
        return []
    canonical = _canonical_analysis_uuid(canonical_analysis_id)
    query_id = canonical if canonical is not None else canonical_analysis_id
    try:
        result = (
            client.table("portfolio_analysis_id_aliases")
            .select("legacy_row_id")
            .eq("user_id", user_id)
            .eq("canonical_analysis_id", query_id)
            .execute()
        )
    except Exception as exc:
        if _is_missing_alias_table(exc):
            logger.info(
                "portfolio_analysis_id_aliases is missing; delete continues "
                "without legacy packet keys"
            )
            return []
        logger.error(
            "Could not list analysis id aliases for %s: %s",
            canonical_analysis_id,
            exc,
        )
        return None
    data = getattr(result, "data", None)
    if not isinstance(data, list):
        return None
    legacy_ids = []
    for row in data:
        if not isinstance(row, dict):
            continue
        legacy_id = str(row.get("legacy_row_id") or "").strip()
        if legacy_id and legacy_id not in legacy_ids:
            legacy_ids.append(legacy_id)
    return legacy_ids


def delete_analysis_by_id(analysis_id: str, user_id: str) -> bool:
    """
    Delete a single portfolio analysis by ID.

    Filters by user_id to enforce ownership so that users can only
    delete their own records. Returns True if a row was deleted.
    """
    analysis_id = _canonical_analysis_id(analysis_id) or ""
    client = get_supabase()
    if client is None or not analysis_id:
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


# History pages are inclusive PostgREST ranges. A short page means the rows
# ran out. Stopping at ACTIVITY_BOOK_HISTORY_MAX_PAGES is an unfinished scan.
ACTIVITY_BOOK_HISTORY_PAGE = 100
ACTIVITY_BOOK_HISTORY_MAX_PAGES = 10000
_PRIVATE_ACTIVITY_BOOKS = "portfolio_activity_books"


@dataclass
class ActivityBookLookup:
    """Result of reading the account's trade book.

    ``ok`` is false only when the private-table read failed. An empty
    ``transactions`` list on ``book`` is still that account's book. An
    unfinished history scan sets ``scan_incomplete`` and is not unrecoverable.
    """

    book: Optional[dict] = None
    ok: bool = True
    missing_schema: bool = False
    unrecoverable: bool = False
    scan_incomplete: bool = False


def _positive_count(value) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        try:
            return float(value) > 0
        except ValueError:
            return False
    return False


def _book_from_private_row(row: dict) -> Optional[dict]:
    """The private book, or None when transactions is not a list.

    None, an object, or a string is a failed read. An empty list is a real book.
    """
    raw = row.get("transactions")
    if not isinstance(raw, list):
        return None
    return {
        "analysis_id": row.get("analysis_id"),
        "filename": row.get("filename") or "",
        "transactions": raw,
        "tax_year": None,
    }


def _history_payload(row: dict) -> dict:
    payload = row.get("result")
    return payload if isinstance(payload, dict) else {}


def _nonempty_transaction_list(row: dict) -> Optional[list]:
    payload = _history_payload(row)
    book = payload.get("activity_book")
    book = book if isinstance(book, dict) else {}
    for candidate in (book.get("transactions"), payload.get("transactions")):
        if isinstance(candidate, list) and candidate:
            return candidate
    return None


def _history_summary(row: dict, payload: dict) -> dict:
    summary = row.get("summary")
    if isinstance(summary, dict):
        return summary
    nested = payload.get("summary")
    return nested if isinstance(nested, dict) else {}


def _history_row_is_stripped(row: dict) -> bool:
    """True when a non-sample row once had trades and no longer stores them."""
    payload = _history_payload(row)
    book = payload.get("activity_book")
    if not isinstance(book, dict):
        return False
    if _nonempty_transaction_list(row) is not None:
        return False
    summary = _history_summary(row, payload)
    return _positive_count(book.get("transaction_count")) or _positive_count(
        summary.get("activity_transaction_count")
    )


def _book_from_history_row(row: dict, transactions: list) -> dict:
    payload = _history_payload(row)
    tax_profile = payload.get("tax_profile")
    tax_profile = tax_profile if isinstance(tax_profile, dict) else {}
    return {
        "analysis_id": row.get("id"),
        "filename": row.get("filename") or "",
        "transactions": transactions,
        "tax_year": tax_profile.get("tax_year"),
    }


def _query_rows(result) -> Optional[list]:
    rows = getattr(result, "data", None) or []
    if not isinstance(rows, list):
        return None
    return rows


def _scan_history_for_activity_book(client, user_id: str) -> ActivityBookLookup:
    """Page this user's history, newest first, for a usable trade list.

    A non-empty list found before any stripped non-sample row is the book.
    A stripped row means a newer analysis had trades that are gone, so an
    older list is not restored. Rows that share uploaded_at are ordered by
    id descending so a page boundary cannot swap them. Read failures and the
    page cap stay an unfinished scan.
    """
    page_size = max(1, int(ACTIVITY_BOOK_HISTORY_PAGE))
    max_pages = max(0, int(ACTIVITY_BOOK_HISTORY_MAX_PAGES))
    saw_marker = False
    offset = 0
    exhausted = False
    for _page in range(max_pages):
        try:
            result = (
                client.table("portfolio_analyses")
                .select("id, filename, uploaded_at, summary, result")
                .eq("user_id", user_id)
                .order("uploaded_at", desc=True)
                .order("id", desc=True)
                .range(offset, offset + page_size - 1)
                .execute()
            )
        except Exception as exc:
            logger.error("Failed to scan history for a trade book: %s", exc)
            return ActivityBookLookup(scan_incomplete=True)
        rows = _query_rows(result)
        if rows is None:
            logger.error("History scan returned a non-list")
            return ActivityBookLookup(scan_incomplete=True)
        for row in rows:
            if not isinstance(row, dict):
                continue
            if is_sample_csv_filename(row.get("filename") or ""):
                continue
            transactions = _nonempty_transaction_list(row)
            if transactions is not None:
                if saw_marker:
                    return ActivityBookLookup(unrecoverable=True)
                return ActivityBookLookup(
                    book=_book_from_history_row(row, transactions)
                )
            if _history_row_is_stripped(row):
                saw_marker = True
        if len(rows) < page_size:
            exhausted = True
            break
        offset += page_size
    if not exhausted:
        logger.warning(
            "Stopped scanning portfolio history for user %s before the rows ran out",
            user_id,
        )
        return ActivityBookLookup(scan_incomplete=True)
    return ActivityBookLookup(unrecoverable=saw_marker)


def load_activity_book_for_merge(user_id: str, client=None) -> ActivityBookLookup:
    """Read the account book. A private row wins, even when it has no trades.

    A private transactions value that is not a list is a failed read: history
    is not scanned and the caller must not upsert. No private row pages this
    user's portfolio_analyses, newest first, until a non-empty
    activity_book.transactions or top-level transactions list that appears
    before any stripped non-sample row, or until the rows run out. An older
    list after a stripped row is unrecoverable and is not the book. Sample
    filenames are skipped. A cap hit before the rows run out is an unfinished
    scan, not an unrecoverable book.
    """
    if not user_id:
        return ActivityBookLookup()
    if client is None:
        client = get_supabase()
    if client is None:
        return ActivityBookLookup()

    try:
        result = (
            client.table(_PRIVATE_ACTIVITY_BOOKS)
            .select("user_id, analysis_id, filename, transactions, updated_at")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        if _is_missing_analysis_schema(exc):
            logger.error("Private activity book table is unavailable: %s", exc)
            return ActivityBookLookup(ok=False, missing_schema=True)
        logger.error("Failed to load private activity book: %s", exc)
        return ActivityBookLookup(ok=False)

    rows = _query_rows(result)
    if rows is None:
        logger.error("Private activity book query returned a non-list")
        return ActivityBookLookup(ok=False)
    if rows:
        row = rows[0] if isinstance(rows[0], dict) else {}
        book = _book_from_private_row(row)
        if book is None:
            logger.error("Private activity book transactions were not a list")
            return ActivityBookLookup(ok=False)
        return ActivityBookLookup(book=book)
    return _scan_history_for_activity_book(client, user_id)


def get_latest_activity_book(user_id: str, client=None) -> Optional[dict]:
    """
    The account's trade book, including an empty transactions list.

    Returns analysis_id, filename, and raw transactions so a later CSV can
    merge instead of replacing the whole history. Packet grants are resolved
    from the private owner-scoped entitlement tables, never history JSON.
    A failed private read and an unfinished history scan return None.
    """
    if not user_id:
        return None
    lookup = load_activity_book_for_merge(user_id, client=client)
    if not lookup.ok or lookup.scan_incomplete:
        return None
    return lookup.book


def upsert_activity_book(
    user_id: str,
    analysis_id: str,
    filename: str,
    transactions: list,
    client=None,
) -> Optional[dict]:
    """Store the full in-memory trade list for this account. Best-effort."""
    analysis_id = _canonical_analysis_id(analysis_id) or ""
    if not user_id or not analysis_id:
        return None
    if client is None:
        client = get_supabase()
    if client is None:
        return None
    if not isinstance(transactions, list):
        transactions = []
    row = {
        "user_id": user_id,
        "analysis_id": analysis_id,
        "filename": (filename or "")[:255],
        "transactions": transactions,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        result = (
            client.table(_PRIVATE_ACTIVITY_BOOKS)
            .upsert(row, on_conflict="user_id")
            .execute()
        )
    except Exception as exc:
        logger.error("Failed to save private activity book: %s", exc)
        return None
    # An empty representation is not proof the upsert committed.
    data = getattr(result, "data", None)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return dict(data[0])
    logger.error("Private activity book upsert returned no persisted row")
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
            .select("analysis_id, packet_session_id, packet_payload, paid_at")
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


def lookup_packet_entitlement_for_tax_year(
    user_id: str,
    tax_year: int,
    client=None,
) -> tuple[Optional[dict], bool]:
    """Find a verified year entitlement even when its source document was deleted."""
    if not user_id or tax_year is None:
        return None, False
    if client is None:
        client = get_supabase()
    if client is None:
        return None, False
    try:
        result = (
            client.table("year_close_packet_entitlements")
            .select("analysis_id, packet_session_id, tax_year")
            .eq("user_id", user_id)
            .eq("tax_year", int(tax_year))
            .order("created_at", desc=True)
            .execute()
        )
        for row in result.data or []:
            if not isinstance(row, dict):
                continue
            # A conflict receipt records a charge that must not grant this year
            # or shadow a real cs_ receipt ordered after it.
            if _is_conflict_analysis_id(row.get("analysis_id")):
                continue
            if (
                isinstance(row.get("packet_session_id"), str)
                and row["packet_session_id"].startswith("cs_")
            ):
                return dict(row), True
    except Exception as e:
        logger.error("Failed to fetch packet entitlement: %s", e)
        return None, False
    return None, True


def lookup_packet_session_entitlement(
    user_id: str,
    tax_year: int,
    session_id: str,
    client=None,
) -> tuple[Optional[dict], bool]:
    """Read the entitlement primary key, including a conflict: receipt.

    The second value is False on outage. (None, True) is a real miss.
    """
    if not user_id or tax_year is None or not session_id:
        return None, False
    if client is None:
        client = get_supabase()
    if client is None:
        return None, False
    try:
        return _read_packet_entitlement_pk(client, user_id, int(tax_year), session_id), True
    except Exception as e:
        logger.error("Failed to read packet entitlement for session %s: %s", session_id, e)
        return None, False


def _exception_code(exc: Exception) -> str:
    for attr in ("code", "sqlstate", "pgcode"):
        value = getattr(exc, attr, None)
        if value:
            return str(value).strip()
    return ""


def _exception_text(exc: Exception) -> str:
    parts = [str(exc)]
    for attr in ("message", "details", "hint"):
        value = getattr(exc, attr, None)
        if value:
            parts.append(str(value))
    if exc.args and isinstance(exc.args[0], dict):
        parts.append(str(exc.args[0]))
    return " ".join(parts).lower()


def _is_missing_alias_table(exc: Exception) -> bool:
    """True only when portfolio_analysis_id_aliases itself is absent.

    Postgres 42P01 (undefined_table) naming this relation, or text that says
    the relation or table does not exist, means migration 012 is not applied.
    The legacy id is still the primary key, so a lookup may fall through.
    PostgREST schema-cache errors such as PGRST205 are not a miss: the table
    exists and a rewritten id is no longer a primary key. Those stay outages.
    A column miss is not this case.
    """
    text = _exception_text(exc)
    if "portfolio_analysis_id_aliases" not in text:
        return False
    if "column" in text:
        return False
    code = _exception_code(exc).upper()
    if code == "42P01":
        return True
    if code:
        return False
    return (
        "does not exist" in text
        and any(token in text for token in ("relation", "table"))
    )


class _StickyConflictReceipt:
    """An existing conflict: receipt for this checkout session.

    Callers must test this sentinel by identity. It is not a saved grant and
    not an outage (None). Truthiness is refused so a stray ``if saved`` cannot
    treat the receipt as success.
    """

    def __repr__(self) -> str:
        return "STICKY_CONFLICT_RECEIPT"

    def __bool__(self) -> bool:
        raise TypeError(
            "test STICKY_CONFLICT_RECEIPT by identity, not truthiness"
        )


STICKY_CONFLICT_RECEIPT = _StickyConflictReceipt()
PACKET_GRANT_SAME_YEAR_DUPLICATE = "PACKET_GRANT_SAME_YEAR_DUPLICATE"


class _AlreadyGrantedEntitlement:
    """The charged session already has a real entitlement, not a conflict receipt.

    Callers must test this sentinel by identity. It is not a receipt row and
    not an outage (None). Truthiness is refused so a stray ``if saved`` cannot
    treat it as a new refund row.
    """

    def __repr__(self) -> str:
        return "ALREADY_GRANTED_ENTITLEMENT"

    def __bool__(self) -> bool:
        raise TypeError(
            "test ALREADY_GRANTED_ENTITLEMENT by identity, not truthiness"
        )


ALREADY_GRANTED_ENTITLEMENT = _AlreadyGrantedEntitlement()


class _InsertedPacketEntitlement(dict):
    """Entitlement row inserted by this call.

    A unique-violation reread is a plain dict, so the receipt helper can log
    only the insert and stay quiet when the writer hands back a stored row.
    """


def _is_conflict_analysis_id(value) -> bool:
    """True for a refund receipt stored in the entitlement analysis_id column."""
    return isinstance(value, str) and value.startswith("conflict:")


def conflict_receipt_analysis_id(analysis_id: str) -> str:
    """Entitlement id for a charged session that must not grant a year.

    ``conflict:`` plus the canonical analysis id fails 012's UUID regex, so
    the migration does not rewrite the row or treat it as a year collision.
    """
    canonical = _canonical_analysis_id(analysis_id) or ""
    if not canonical:
        return ""
    if _is_conflict_analysis_id(canonical):
        return canonical
    return f"conflict:{canonical}"


def _is_unique_violation(exc: Exception) -> bool:
    code = _exception_code(exc)
    text = _exception_text(exc)
    return (
        code == "23505"
        or "duplicate key" in text
        or "unique constraint" in text
    )


# Undefined column/table from Postgres, and PostgREST schema-cache misses.
_MISSING_ANALYSIS_SCHEMA_CODES = {"42703", "42P01", "PGRST204", "PGRST205"}


def _is_missing_analysis_schema(exc: Exception) -> bool:
    """True when portfolio_analyses or its result column is not in the database."""
    code = _exception_code(exc).upper()
    if code in _MISSING_ANALYSIS_SCHEMA_CODES:
        return True
    text = _exception_text(exc)
    if any(token in text for token in ("42703", "42p01", "pgrst204", "pgrst205")):
        return True
    if "schema cache" in text and any(
        token in text for token in ("column", "table", "relation")
    ):
        return True
    if any(
        phrase in text
        for phrase in ("undefined column", "undefined table", "undefined relation")
    ):
        return True
    return (
        "does not exist" in text
        and any(token in text for token in ("column", "relation", "table"))
    )


def _read_packet_entitlement_pk(client, user_id: str, tax_year: int, session_id: str):
    existing = (
        client.table("year_close_packet_entitlements")
        .select("analysis_id, user_id, tax_year, packet_session_id")
        .eq("user_id", user_id)
        .eq("tax_year", int(tax_year))
        .eq("packet_session_id", session_id)
        .limit(1)
        .execute()
    )
    data = getattr(existing, "data", None)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return dict(data[0])
    return None


def _entitlement_row_or_sticky(current, *, conflict_receipt: bool):
    """Return a stored row, the sticky sentinel, or None when the PK is empty."""
    if not current:
        return None
    if _is_conflict_analysis_id(current.get("analysis_id")) and not conflict_receipt:
        return STICKY_CONFLICT_RECEIPT
    return current


def save_packet_entitlement(
    analysis_id: str,
    user_id: str,
    tax_year: int,
    session_id: str,
    client=None,
    *,
    conflict_receipt: bool = False,
):
    """Persist a verified Stripe purchase separately from its private document.

    The receipt is immutable for (user, tax year, checkout session). A later
    analysis in that paid year gets its own snapshot and must not rewrite the
    original analysis id, or Stripe metadata checks 503.

    ``conflict_receipt=True`` is only for ``save_packet_conflict_receipt``.
    A normal grant that finds a ``conflict:`` row returns
    ``STICKY_CONFLICT_RECEIPT`` instead of that row. A ``conflict:`` id passed
    without the flag returns None and does not insert.
    """
    analysis_id = _canonical_analysis_id(analysis_id) or ""
    if _is_conflict_analysis_id(analysis_id) and not conflict_receipt:
        return None
    if not analysis_id or not user_id or tax_year is None or not isinstance(session_id, str) or not session_id.startswith("cs_"):
        return None
    if client is None:
        client = get_supabase()
    if client is None:
        return None

    def read_existing():
        return _entitlement_row_or_sticky(
            _read_packet_entitlement_pk(client, user_id, int(tax_year), session_id),
            conflict_receipt=conflict_receipt,
        )

    try:
        current = read_existing()
        if current is STICKY_CONFLICT_RECEIPT or isinstance(current, dict):
            return current
        row = {
            "analysis_id": analysis_id,
            "user_id": user_id,
            "tax_year": int(tax_year),
            "packet_session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            # INSERT cannot rewrite an existing receipt. Upsert with a wiped
            # ignore-duplicates preference would replace analysis_id.
            result = (
                client.table("year_close_packet_entitlements")
                .insert(row)
                .select("analysis_id, user_id, tax_year, packet_session_id")
                .execute()
            )
        except Exception as insert_error:
            if not _is_unique_violation(insert_error):
                raise
            # Stored row, not a sentinel. The receipt helper classifies it.
            return read_existing()
        current = read_existing()
        if current is STICKY_CONFLICT_RECEIPT or isinstance(current, dict):
            if conflict_receipt and isinstance(current, dict):
                return _InsertedPacketEntitlement(current)
            return current
        if result.data:
            inserted = dict(result.data[0])
            if conflict_receipt:
                return _InsertedPacketEntitlement(inserted)
            return inserted
        return None
    except Exception as e:
        logger.error("Failed to save packet entitlement for %s: %s", analysis_id, e)
        return None


def save_packet_conflict_receipt(
    analysis_id: str,
    user_id: str,
    tax_year: int,
    session_id: str,
    *,
    paid_tax_year: Optional[int] = None,
    client=None,
    log_event: str = "PACKET_GRANT_YEAR_CONFLICT",
    entitlement_writer=None,
) -> dict | _AlreadyGrantedEntitlement | None:
    """Store one refund receipt for a charged session that must not grant.

    This is the only writer. It passes ``conflict_receipt=True``. A replay
    rereads the primary key and does not log again. The log is the human
    refund trail: charged session, user, analysis, paid year, and charged year.
    """
    receipt_id = conflict_receipt_analysis_id(analysis_id)
    if not receipt_id or not user_id or tax_year is None or not isinstance(session_id, str):
        return None
    if not session_id.startswith("cs_"):
        return None
    try:
        charged_year = int(tax_year)
    except (TypeError, ValueError):
        return None
    stored_year = charged_year
    if paid_tax_year is not None:
        try:
            stored_year = int(paid_tax_year)
        except (TypeError, ValueError):
            stored_year = charged_year
    writer = entitlement_writer or save_packet_entitlement
    read_client = client if client is not None else get_supabase()
    already = None
    if read_client is not None:
        try:
            already = _read_packet_entitlement_pk(
                read_client, user_id, charged_year, session_id
            )
        except Exception as exc:
            logger.error(
                "Failed to read packet conflict receipt for %s: %s",
                session_id,
                exc,
            )
            return None
    if isinstance(already, dict):
        # A real cs_ entitlement already granted this session. Do not rewrite
        # it into a conflict: receipt and do not log a refund.
        if not _is_conflict_analysis_id(already.get("analysis_id")):
            return ALREADY_GRANTED_ENTITLEMENT
        return already
    saved = writer(
        receipt_id,
        user_id,
        charged_year,
        session_id,
        client=client,
        conflict_receipt=True,
    )
    if saved is None or saved is STICKY_CONFLICT_RECEIPT or not isinstance(saved, dict):
        return None
    # A unique-violation reread is a plain dict. This call's insert is marked.
    if not isinstance(saved, _InsertedPacketEntitlement):
        if not _is_conflict_analysis_id(saved.get("analysis_id")):
            return ALREADY_GRANTED_ENTITLEMENT
        return saved
    logger.error(
        "%s session_id=%s user_id=%s analysis_id=%s "
        "stored_tax_year=%s requested_tax_year=%s",
        log_event,
        session_id,
        user_id,
        analysis_id,
        stored_year,
        charged_year,
    )
    return saved


def get_packet_grant_for_tax_year(
    user_id: str,
    tax_year: int,
    client=None,
) -> tuple[Optional[str], bool]:
    """Return a Stripe session id and whether its database lookup succeeded."""
    return lookup_packet_grant_for_tax_year(
        user_id,
        tax_year,
        client=client,
    )


def _load_analysis_id_rows(
    client,
    table: str,
    analysis_id: str,
    user_id: str,
    columns: str,
    *,
    refine=None,
    key_fields: tuple[str, ...] = ("analysis_id",),
) -> tuple[list[dict], bool]:
    """Exact ``analysis_id`` read plus a temporary UUID case fallback.

    UUID ids always run both queries, even when ``.eq`` already returned rows.
    Results are merged and deduped on ``key_fields``, keeping the exact-match
    copy. Snapshots key on ``analysis_id``. Entitlements key on
    ``(analysis_id, tax_year, packet_session_id)``. A failed ``ilike`` is an
    outage even if ``.eq`` hit.
    Callers sort and limit after the merge so one query cannot drop the other
    spelling. Non-UUID ids stay exact ``.eq`` only. The second value is False
    on outage. An empty list with True is a real miss.

    # TODO(remove after 012 applied): legacy mixed-case UUID analysis_id.
    """
    canonical = _canonical_analysis_id(analysis_id)
    if not canonical or not user_id or client is None:
        return [], False

    def _execute(match):
        query = client.table(table).select(columns)
        query = match(query)
        query = query.eq("user_id", user_id)
        if refine is not None:
            query = refine(query)
        return query.execute()

    def _dicts(result):
        return [
            dict(row)
            for row in (getattr(result, "data", None) or [])
            if isinstance(row, dict)
        ]

    try:
        exact = _execute(lambda query: query.eq("analysis_id", canonical))
    except Exception as exc:
        logger.error("Failed to read %s for %s: %s", table, canonical, exc)
        return [], False
    exact_rows = _dicts(exact)
    if _canonical_analysis_uuid(canonical) is None:
        return exact_rows, True
    try:
        legacy = _execute(
            lambda query: query.filter("analysis_id", "ilike", canonical)
        )
    except Exception as exc:
        logger.error(
            "Failed legacy case read of %s for %s: %s",
            table,
            canonical,
            exc,
        )
        return [], False
    return _dedupe_stored_analysis_rows(
        exact_rows, _dicts(legacy), key_fields=key_fields
    ), True


def _dedupe_stored_analysis_rows(
    exact_rows: list[dict],
    legacy_rows: list[dict],
    *,
    key_fields: tuple[str, ...] = ("analysis_id",),
) -> list[dict]:
    """Keep the first copy of each key. Exact rows come first."""
    merged = []
    seen = set()
    for row in list(exact_rows) + list(legacy_rows):
        key = tuple(str(row.get(field) or "") for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def _snapshot_time_rank(value, *, nulls_last: bool = True):
    """Sort key for a timestamp column. Later values sort first. Nulls last."""
    if not value:
        return (1, 0) if nulls_last else (0, 0)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (0, -parsed.timestamp())
    except (TypeError, ValueError):
        return (1, 0) if nulls_last else (0, 0)


def _sort_snapshot_rows(rows: list[dict]) -> list[dict]:
    """Paid rows first, then latest expiry (nulls last), then analysis_id.

    Matches ``_order_snapshot_candidates``. Python owns the order after the
    exact and case-insensitive reads are merged, so a SQL ``limit`` cannot
    drop the paid spelling before the merge.
    """

    def sort_key(row):
        paid_at = row.get("paid_at")
        if paid_at:
            paid_rank = (0, _snapshot_time_rank(paid_at)[1])
        else:
            paid_rank = (1, 0)
        return (
            paid_rank,
            _snapshot_time_rank(row.get("expires_at")),
            str(row.get("analysis_id") or ""),
        )

    return sorted(rows, key=sort_key)


def _order_snapshot_candidates(query):
    """Pick one snapshot when two spellings of the same UUID both exist.

    DESC in PostgREST is NULLS FIRST unless nullsfirst is false, which would
    let an expired unpaid row hide a paid row. Paid rows come first, then the
    latest expiry, then the stored key.
    """
    return (
        query.order("paid_at", desc=True, nullsfirst=False)
        .order("expires_at", desc=True, nullsfirst=False)
        .order("analysis_id")
    )


def _packet_snapshot_is_current(snapshot: dict) -> bool:
    """Paid rows stay. Unpaid rows stay only while expires_at is in the future."""
    if snapshot.get("paid_at"):
        return True
    expires_at = snapshot.get("expires_at")
    try:
        if not expires_at:
            return False
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        return expiry > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


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
    analysis_id = _canonical_analysis_id(analysis_id) or ""
    if not analysis_id or not user_id or tax_year is None:
        return None
    if client is None:
        client = get_supabase()
    if client is None:
        return None
    _cleanup_expired_packet_snapshots(client)
    now = datetime.now(timezone.utc)
    requested_paid_at = now.isoformat() if paid and session_id else None
    expires_at = None if requested_paid_at else now + UNPAID_PACKET_SNAPSHOT_TTL

    snapshot_columns = (
        "analysis_id, user_id, tax_year, packet_payload, packet_session_id, paid_at, expires_at"
    )

    def _snapshot_rows(*, tax_year_filter=None, limit=None):
        def refine(query):
            if tax_year_filter is not None:
                query = query.eq("tax_year", int(tax_year_filter))
            # No SQL limit. Both spellings are merged first, then sorted.
            return _order_snapshot_candidates(query)

        rows, ok = _load_analysis_id_rows(
            client,
            "year_close_packet_snapshots",
            analysis_id,
            user_id,
            snapshot_columns,
            refine=refine,
        )
        if not ok:
            raise _SnapshotReadOutage()
        rows = _sort_snapshot_rows(rows)
        if limit is not None:
            return rows[:limit]
        return rows

    def read_exact_key():
        # The primary key is (user_id, canonical analysis_id). This row can
        # sit in another tax year. Exact eq only; the legacy spelling scan
        # is separate and has no limit.
        result = (
            client.table("year_close_packet_snapshots")
            .select(snapshot_columns)
            .eq("analysis_id", analysis_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        return dict(result.data[0]) if result.data else None

    def _raise_paid_other_year(rows):
        """Any paid spelling in a different year blocks the write.

        # TODO(remove after 012 applied): the scan exists so a legacy spelling
        # cannot hide behind the canonical row. Same-year unpaid re-year of a
        # single row stays allowed.
        """
        for row in rows:
            if not row.get("paid_at"):
                continue
            try:
                stored_year = int(row.get("tax_year"))
            except (TypeError, ValueError):
                continue
            if stored_year != int(tax_year):
                year_conflict(row)

    def read_existing():
        # One unscoped load, no limit, every spelling. The conflict check runs
        # before any UPDATE or INSERT.
        rows = _snapshot_rows()
        _raise_paid_other_year(rows)
        for row in rows:
            try:
                if int(row.get("tax_year")) == int(tax_year):
                    return row
            except (TypeError, ValueError):
                continue
        for row in rows:
            if str(row.get("analysis_id") or "") == analysis_id:
                return row
        for row in rows:
            if str(row.get("analysis_id") or "") != analysis_id:
                return row
        return None

    def year_conflict(row):
        raise PacketSnapshotYearConflict(
            row.get("analysis_id"),
            row.get("tax_year"),
            tax_year,
        )

    def result_row(result, fallback):
        data = getattr(result, "data", None)
        if data:
            return dict(data[0])
        return {
            key: fallback.get(key)
            for key in ("analysis_id", "user_id", "tax_year")
        }

    try:
        existing_row = read_existing()
        if existing_row and existing_row.get("paid_at"):
            if int(existing_row.get("tax_year")) != int(tax_year):
                logger.warning(
                    "Refusing to move paid packet %s from tax year %s to %s",
                    analysis_id,
                    existing_row.get("tax_year"),
                    tax_year,
                )
                year_conflict(existing_row)
            paid_at = existing_row["paid_at"]
            effective_session_id = existing_row.get("packet_session_id")
            stored_payload = existing_row.get("packet_payload")
            if isinstance(stored_payload, dict) or not isinstance(packet_payload, dict):
                return {
                    key: existing_row.get(key)
                    for key in ("analysis_id", "user_id", "tax_year")
                }
            tax_year = existing_row["tax_year"]
            row = {
                "user_id": user_id,
                "tax_year": int(tax_year),
                "packet_payload": packet_payload,
                "packet_session_id": effective_session_id,
                "paid_at": paid_at,
                "expires_at": None,
                "updated_at": now.isoformat(),
            }
            update_row = row
            # Repair only the null payload we just read. A concurrent delete or
            # a payload written after this read must not be upserted over.
            # Pin the stored key: an ilike match would rewrite every case variant.
            repair = (
                client.table("year_close_packet_snapshots")
                .update(row)
                .eq("analysis_id", existing_row["analysis_id"])
                .eq("user_id", user_id)
                .filter("paid_at", "not.is", "null")
            )
            if stored_payload is None:
                repair = repair.is_("packet_payload", "null")
            else:
                repair = repair.eq("packet_payload", stored_payload)
            result = repair.select("analysis_id, user_id, tax_year").execute()
            if result.data:
                return dict(result.data[0])
            latest_row = read_existing()
            if not latest_row:
                return None
            if latest_row.get("paid_at") and int(latest_row.get("tax_year")) != int(tax_year):
                year_conflict(latest_row)
            latest_payload = latest_row.get("packet_payload")
            if latest_row.get("paid_at") and (
                isinstance(latest_payload, dict) or not isinstance(packet_payload, dict)
            ):
                return {
                    key: latest_row.get(key)
                    for key in ("analysis_id", "user_id", "tax_year")
                }
            return None

        row = {
            "analysis_id": analysis_id,
            "user_id": user_id,
            "tax_year": int(tax_year),
            "packet_payload": packet_payload,
            "packet_session_id": session_id,
            "paid_at": requested_paid_at,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "updated_at": now.isoformat(),
        }
        # Leave the stored primary-key spelling alone. The ilike match can see
        # another case of the same UUID, and writing analysis_id would rename it.
        update_row = {key: value for key, value in row.items() if key != "analysis_id"}

        if existing_row:
            # The predicate makes this transition atomic with respect to the
            # webhook's paid_at update. If payment wins the race, the UPDATE
            # affects no row and the paid row is read back without downgrading.
            result = (
                client.table("year_close_packet_snapshots")
                .update(update_row)
                .eq("analysis_id", existing_row["analysis_id"])
                .eq("user_id", user_id)
                .is_("paid_at", "null")
                .select("analysis_id, user_id, tax_year")
                .execute()
            )
            if result.data:
                return dict(result.data[0])
            latest_row = read_existing()
            if not latest_row:
                return None
            if latest_row.get("paid_at"):
                # Payment won at the old year. Do not write this year's payload
                # onto that paid row.
                if int(latest_row.get("tax_year")) != int(tax_year):
                    year_conflict(latest_row)
                return save_packet_snapshot(
                    analysis_id,
                    user_id,
                    int(latest_row["tax_year"]),
                    packet_payload,
                    session_id=latest_row.get("packet_session_id"),
                    paid=True,
                    client=client,
                )
            if latest_row.get("packet_payload") == packet_payload:
                return result_row(result, latest_row)
            return None

        # No canonical row and no legacy spelling. INSERT preserves any row
        # created by a concurrent webhook/checkout;
        # unlike UPSERT it can never replace a just-paid snapshot.
        result = (
            client.table("year_close_packet_snapshots")
            .insert(row)
            .select("analysis_id, user_id, tax_year")
            .execute()
        )
        if result.data:
            return dict(result.data[0])
        latest_row = read_exact_key()
        if not latest_row:
            return None
        if latest_row.get("paid_at"):
            if int(latest_row.get("tax_year")) != int(tax_year):
                year_conflict(latest_row)
            return result_row(None, latest_row)
        if int(latest_row.get("tax_year")) != int(tax_year):
            reyeared = (
                client.table("year_close_packet_snapshots")
                .update(update_row)
                .eq("analysis_id", latest_row["analysis_id"])
                .eq("user_id", user_id)
                .is_("paid_at", "null")
                .select("analysis_id, user_id, tax_year")
                .execute()
            )
            if reyeared.data:
                return dict(reyeared.data[0])
            return None
        return result_row(None, latest_row)
    except PacketSnapshotYearConflict:
        raise
    except _SnapshotReadOutage:
        return None
    except Exception as e:
        logger.error("Failed to save packet snapshot for %s: %s", analysis_id, e)
        # A uniqueness conflict means another request created the row. Treat
        # that as idempotent only after reading the exact primary key back.
        # A year-scoped reread would miss the other year and look like an outage.
        error_text = str(e).lower()
        error_code = getattr(e, "code", None)
        if error_code != "23505" and "duplicate key" not in error_text and "unique constraint" not in error_text:
            return None
        try:
            latest_row = read_exact_key()
            if not latest_row:
                return None
            if int(latest_row.get("tax_year")) != int(tax_year):
                if latest_row.get("paid_at"):
                    year_conflict(latest_row)
                reyeared = (
                    client.table("year_close_packet_snapshots")
                    .update(update_row)
                    .eq("analysis_id", latest_row["analysis_id"])
                    .eq("user_id", user_id)
                    .is_("paid_at", "null")
                    .select("analysis_id, user_id, tax_year")
                    .execute()
                )
                if reyeared.data:
                    return dict(reyeared.data[0])
                raced = read_exact_key()
                if raced and raced.get("paid_at"):
                    if int(raced.get("tax_year")) != int(tax_year):
                        year_conflict(raced)
                    return result_row(None, raced)
                return None
            if latest_row.get("paid_at") or not requested_paid_at:
                return result_row(None, latest_row)
            # A paid insert can race an unpaid insert for the same unique
            # key. Promote only while the database still says unpaid.
            promoted = (
                client.table("year_close_packet_snapshots")
                .update(update_row)
                .eq("analysis_id", latest_row["analysis_id"])
                .eq("user_id", user_id)
                .is_("paid_at", "null")
                .select("analysis_id, user_id, tax_year")
                .execute()
            )
            if promoted.data:
                return dict(promoted.data[0])
            latest_row = read_exact_key()
            if latest_row and latest_row.get("paid_at"):
                if int(latest_row.get("tax_year")) != int(tax_year):
                    year_conflict(latest_row)
                return result_row(None, latest_row)
        except PacketSnapshotYearConflict:
            raise
        except Exception:
            pass
        return None


def get_packet_snapshot(
    analysis_id: str,
    user_id: str,
    client=None,
    *,
    tax_year: Optional[int] = None,
) -> tuple[Optional[dict], bool]:
    """Load a caller-owned packet snapshot and distinguish outages.

    tax_year is keyword-only so a positional client argument stays put. When
    the caller knows the year, the read stays inside that year and still
    prefers a paid row, then the latest expiry.
    """
    analysis_id = _canonical_analysis_id(analysis_id) or ""
    if not analysis_id or not user_id:
        return None, False
    if client is None:
        client = get_supabase()
    if client is None:
        return None, False
    _cleanup_expired_packet_snapshots(client)

    def refine(query):
        if tax_year is not None:
            query = query.eq("tax_year", int(tax_year))
        return _order_snapshot_candidates(query)

    rows, ok = _load_analysis_id_rows(
        client,
        "year_close_packet_snapshots",
        analysis_id,
        user_id,
        "analysis_id, user_id, tax_year, packet_payload, packet_session_id, paid_at, expires_at",
        refine=refine,
    )
    if not ok:
        return None, False
    rows = _sort_snapshot_rows(rows)
    if not rows:
        return None, True
    snapshot = rows[0]
    if not _packet_snapshot_is_current(snapshot):
        return None, True
    return snapshot, True


def list_packet_snapshots_for_identity(
    analysis_id: str,
    user_id: str,
    client=None,
) -> tuple[list[dict], bool]:
    """Every live snapshot for this user and analysis identity.

    Same match as get_packet_snapshot, with no tax_year and no limit. Expired
    unpaid rows are dropped. The second value is False on outage, including
    when no client is available. An empty list with True is a real miss.
    """
    analysis_id = _canonical_analysis_id(analysis_id) or ""
    if not analysis_id or not user_id:
        return [], False
    if client is None:
        client = get_supabase()
    if client is None:
        return [], False
    _cleanup_expired_packet_snapshots(client)
    rows, ok = _load_analysis_id_rows(
        client,
        "year_close_packet_snapshots",
        analysis_id,
        user_id,
        "analysis_id, user_id, tax_year, packet_payload, packet_session_id, paid_at, expires_at",
        refine=_order_snapshot_candidates,
    )
    if not ok:
        return [], False
    rows = _sort_snapshot_rows(rows)
    return [row for row in rows if _packet_snapshot_is_current(row)], True


def lookup_packet_entitlements_for_analysis(
    user_id: str,
    analysis_id: str,
    client=None,
) -> tuple[list[dict], bool]:
    """Every cs_ entitlement for this user and analysis identity.

    Does not choose a tax year. The second value is False on outage.
    """
    analysis_id = _canonical_analysis_id(analysis_id) or ""
    if not user_id or not analysis_id:
        return [], False
    if client is None:
        client = get_supabase()
    if client is None:
        return [], False
    rows, ok = _load_analysis_id_rows(
        client,
        "year_close_packet_entitlements",
        analysis_id,
        user_id,
        "analysis_id, packet_session_id, tax_year",
        key_fields=("analysis_id", "tax_year", "packet_session_id"),
    )
    if not ok:
        return [], False
    kept = []
    for row in rows:
        if _is_conflict_analysis_id(row.get("analysis_id")):
            continue
        session_id = row.get("packet_session_id")
        if isinstance(session_id, str) and session_id.startswith("cs_"):
            kept.append(row)
    return kept, True


def mark_packet_snapshot_paid(
    analysis_id: str,
    user_id: str,
    tax_year: int,
    session_id: str,
    client=None,
) -> bool | str | None:
    """Mark an existing owner-scoped snapshot as paid for a settled session."""
    analysis_id = _canonical_analysis_id(analysis_id) or ""
    if not analysis_id or not user_id or tax_year is None or not session_id:
        return False
    if client is None:
        client = get_supabase()
    if client is None:
        return None
    _cleanup_expired_packet_snapshots(client)
    now = datetime.now(timezone.utc)
    try:
        rows, ok = _load_analysis_id_rows(
            client,
            "year_close_packet_snapshots",
            analysis_id,
            user_id,
            "analysis_id, user_id, tax_year, packet_session_id, paid_at, expires_at",
            refine=_order_snapshot_candidates,
        )
        if not ok:
            return None
        rows = _sort_snapshot_rows(rows)
        for row in rows:
            if not row.get("paid_at"):
                continue
            try:
                stored_year = int(row.get("tax_year"))
            except (TypeError, ValueError):
                continue
            if stored_year != int(tax_year):
                raise PacketSnapshotYearConflict(
                    row.get("analysis_id"),
                    row.get("tax_year"),
                    tax_year,
                )
        same_year = []
        for row in rows:
            try:
                if int(row.get("tax_year")) == int(tax_year):
                    same_year.append(row)
            except (TypeError, ValueError):
                continue
        if not same_year:
            return False
        existing_row = same_year[0]
        stored_analysis_id = existing_row.get("analysis_id")
        if not stored_analysis_id:
            return False
        if existing_row.get("paid_at"):
            # Same session is a replay. Do not overwrite paid_at or the
            # stored session, and do not stamp an unpaid sibling spelling.
            if existing_row.get("packet_session_id") == session_id:
                return True
            # A different session already paid this year. Keep the snapshot
            # and record one refund receipt. Returning True would let the grant
            # insert a normal entitlement for the new session. This is not a
            # different tax year.
            receipt = save_packet_conflict_receipt(
                analysis_id,
                user_id,
                int(tax_year),
                session_id,
                paid_tax_year=int(tax_year),
                client=client,
                log_event=PACKET_GRANT_SAME_YEAR_DUPLICATE,
            )
            if receipt is None:
                return None
            # ALREADY_GRANTED_ENTITLEMENT is not True. The snapshot stays on
            # the other session, so this session is not reported paid and
            # history is not patched. A new conflict: row takes the same path.
            return PACKET_GRANT_SAME_YEAR_DUPLICATE
        # Stamp only the row that was read, and only while it is still unpaid.
        # An ilike update would re-stamp every case variant, including one
        # that is already paid.
        result = (
            client.table("year_close_packet_snapshots")
            .update({
                "packet_session_id": session_id,
                "paid_at": now.isoformat(),
                "expires_at": None,
                "updated_at": now.isoformat(),
            })
            .eq("analysis_id", stored_analysis_id)
            .eq("user_id", user_id)
            .eq("tax_year", int(tax_year))
            .is_("paid_at", "null")
            .select("analysis_id")
            .execute()
        )
        return bool(result.data)
    except PacketSnapshotYearConflict:
        raise
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
    analysis_id = _canonical_analysis_id(analysis_id) or ""
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
