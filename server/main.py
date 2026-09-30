import hashlib
import hmac
import os
import logging
import re
import time
import ipaddress
from datetime import date, datetime, timezone
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote
import uuid
from typing import Annotated, Optional
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Load environment variables BEFORE importing local modules that read os.environ
# at module-import time (e.g. auth.py, db.py).  Order matters: .env.local wins.
SERVER_DIR = Path(__file__).resolve().parent
load_dotenv(SERVER_DIR / ".env.local")
load_dotenv(SERVER_DIR / ".env")

from fastapi import FastAPI, File, UploadFile, Query, HTTPException, Depends, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import io
import json
from typing import List, Dict, Any
from pywebpush import webpush, WebPushException
from pydantic import BaseModel, ValidationError

from cors_origins import cors_allowed_origins
from auth import get_current_user, get_optional_user, enforce_ownership
from models import (
    AssetType,
    FilingStatus,
    ActivityBookSummary,
    LotMatchReport,
    PortfolioAnalysis,
    RealizedSummary,
    Supplemental1099Summary,
    TaxProfile,
    TransCode,
)
from year_close_packet import (
    PACKET_AMOUNT_CENTS,
    PACKET_METADATA_PRODUCT,
    PACKET_PRODUCT_NAME,
    adopt_guest_packet,
    build_packet_payload,
    claimable_guest_packet_payload,
    copy_packet_payload_to_id,
    forget_packet_payload,
    get_payload,
    is_packet_paid,
    mark_paid,
    packet_analysis_id_from_session,
    packet_checkout_custom_text,
    packet_checkout_line_items,
    packet_requires_test_stripe,
    packet_user_id_from_session,
    packet_session_id,
    packet_store_owner,
    _session_metadata,
    packet_store_belongs_to_user,
    paid_session_for_user_year,
    remember_analysis,
    render_packet_pdf,
    resolve_packet_stripe_secret_key,
    session_grants_packet,
    session_is_settled_packet,
    upsert_packet_payload,
)
from csv_parser import parse_csv, RealizedEvent, transactions_to_tax_lots
from lot_matcher import match_1099b_lots
from ledger import (
    SAMPLE_FIXTURE_PRICES,
    is_sample_csv_filename,
    is_trusted_in_app_sample,
    merge_transaction_books,
    merge_warning,
    strip_book_transactions_dict,
    transactions_from_stored,
)
from tax_engine import get_tax_brackets_summary
from harvesting import (
    compute_lot_metrics,
    aggregate_positions,
    generate_suggestions,
    build_portfolio_summary,
    suppress_fractional_residual_positions,
)
from wash_sale import detect_wash_sales, adjust_lots_for_wash_sales
from price_service import (
    fetch_current_prices,
    fetch_option_chain_window,
    fetch_option_prices,
)
from leap_rank import rank_from_chain
from rh_chain import (
    RH_CONNECTION_REQUIRED_COPY,
    ChainCache,
    EnvOthRhCredentialStore,
    RhChainError,
    build_rh_chain_client,
    connection_required_payload,
    fetch_and_rank_chain,
)
from ai_advisor import get_ai_suggestions, prepare_positions_for_ai
from pdf_1099_parser import parse_robinhood_1099_pdf
from db import (
    save_analysis_history,
    get_analysis_history,
    get_analysis_by_id,
    get_analysis_by_result_analysis_id,
    lookup_analysis_for_entitlement,
    delete_analyses_without_result,
    delete_analysis_by_id,
    HistoryInsertConflict,
    save_tax_profile as db_save_tax_profile,
    get_tax_profile as db_get_tax_profile,
    get_supabase,
    get_latest_activity_book,
    lookup_packet_grant_for_tax_year,
    lookup_packet_entitlement_for_tax_year,
    save_packet_entitlement,
    save_packet_snapshot,
    get_packet_snapshot,
    mark_packet_snapshot_paid,
    patch_analysis_result,
    save_push_subscription as db_save_push_subscription,
    list_push_subscriptions as db_list_push_subscriptions,
    get_push_subscription_for_user as db_get_push_subscription_for_user,
    delete_push_subscription as db_delete_push_subscription,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
_MAX_SUPPLEMENTAL_PDF_BYTES = 20 * 1024 * 1024  # 20 MB: max size for supplemental 1099 PDF uploads
_MAX_GUEST_CSV_BYTES = 5 * 1024 * 1024  # 5 MB: unauthenticated analyze must not file.read() unbounded
_GUEST_ANALYZE_WINDOW_SECONDS = 60 * 60
_GUEST_ANALYZE_MAX_PER_WINDOW = 10
_GUEST_ANALYZE_MAX_BUCKETS = 4096
_guest_analyze_hits: dict[str, list[float]] = defaultdict(list)
_GUEST_LEAP_RANK_WINDOW_SECONDS = 60 * 60
_GUEST_LEAP_RANK_MAX_PER_WINDOW = 20
_AUTH_LEAP_RANK_MAX_PER_WINDOW = 60
_LEAP_RANK_MAX_BUCKETS = 4096
_guest_leap_rank_hits: dict[str, list[float]] = defaultdict(list)
_auth_leap_rank_hits: dict[str, list[float]] = defaultdict(list)
_LEAP_RANK_SYMBOL_RE = re.compile(r"^[A-Z]{1,10}$")
_LEAP_RANK_MAX_WINDOW_DAYS = 800
rh_credential_store = EnvOthRhCredentialStore()
rh_chain_client = build_rh_chain_client()
rh_chain_cache = ChainCache()
_rh_chain_hits: dict[str, list[float]] = defaultdict(list)

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


class PushTestRequest(BaseModel):
    endpoint: str


class PersistGuestAnalysisBody(BaseModel):
    filename: str = "guest-run.csv"
    analysis: Dict[str, Any]

# Enable CORS for frontend
# In development allow any localhost port so dev servers on 3000/3001/etc work.
# CORSMiddleware is the only middleware — it is therefore implicitly last.
if FRONTEND_URL.startswith("http://localhost"):
    app.add_middleware(  # NOSONAR python:S8414
        CORSMiddleware,
        allow_origin_regex=r"^http://localhost(:[0-9]+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # FRONTEND_URL plus www/apex twins, CORS_ORIGINS extras, and known public
    # hosts (www.optionstaxhub.com + Render client URLs). Do not derive the
    # allow-list from FRONTEND_URL alone — prod used the onrender hostname
    # while users load the custom domain, which omitted ACAO and broke analyze.
    _allowed_origins = cors_allowed_origins(
        FRONTEND_URL,
        extra_origins=os.environ.get("CORS_ORIGINS", ""),
    )
    logger.info("CORS allow_origins=%s", _allowed_origins)
    app.add_middleware(  # NOSONAR python:S8414
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

def reset_guest_analyze_quota() -> None:
    """Clear guest analyze rate-limit buckets (tests only)."""
    _guest_analyze_hits.clear()


def reset_guest_leap_rank_quota() -> None:
    """Clear LEAP-rank rate-limit buckets (tests only)."""
    _guest_leap_rank_hits.clear()
    _auth_leap_rank_hits.clear()
    _rh_chain_hits.clear()
    rh_chain_cache.clear()


def _is_trusted_proxy_peer(host: str) -> bool:
    """True when the TCP peer is a local/private hop that may set forwarding headers."""
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def _client_ip(request: Request) -> str:
    """
    Identify the guest for analyze quota.

    The socket address is always the source of truth for untrusted peers.
    X-Forwarded-For is only read when the immediate TCP peer is a private,
    loopback, or link-local proxy (Render/nginx). The last hop is used
    because clients can prepend spoofed values; a trusted proxy appends.
    """
    socket_ip = request.client.host if request.client and request.client.host else ""
    if _is_trusted_proxy_peer(socket_ip):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
            if hops:
                return hops[-1]
    return socket_ip or "unknown"


def _prune_ip_hits(
    store: dict[str, list[float]],
    now: float,
    window_seconds: float,
    max_buckets: int,
) -> None:
    """Drop stale quota buckets and cap the map so unique IPs cannot grow memory unbounded."""
    stale_keys = [
        key
        for key, stamps in list(store.items())
        if not stamps or now - max(stamps) >= window_seconds
    ]
    for key in stale_keys:
        store.pop(key, None)

    overflow = len(store) - max_buckets
    if overflow <= 0:
        return
    oldest = sorted(
        store.items(),
        key=lambda item: max(item[1]) if item[1] else 0.0,
    )[:overflow]
    for key, _stamps in oldest:
        store.pop(key, None)


def _prune_guest_analyze_hits(now: float) -> None:
    _prune_ip_hits(
        _guest_analyze_hits,
        now,
        _GUEST_ANALYZE_WINDOW_SECONDS,
        _GUEST_ANALYZE_MAX_BUCKETS,
    )


def _enforce_ip_quota(
    request: Request,
    store: dict[str, list[float]],
    *,
    max_per_window: int,
    window_seconds: float,
    max_buckets: int,
    detail: str,
) -> None:
    now = time.time()
    _prune_ip_hits(store, now, window_seconds, max_buckets)
    key = _client_ip(request)
    hits = store[key]
    hits[:] = [stamp for stamp in hits if now - stamp < window_seconds]
    if len(hits) >= max_per_window:
        raise HTTPException(status_code=429, detail=detail)
    hits.append(now)


def _enforce_guest_analyze_quota(request: Request) -> None:
    """Simple per-IP quota so anonymous analyze cannot be used as a free compute pump."""
    _enforce_ip_quota(
        request,
        _guest_analyze_hits,
        max_per_window=_GUEST_ANALYZE_MAX_PER_WINDOW,
        window_seconds=_GUEST_ANALYZE_WINDOW_SECONDS,
        max_buckets=_GUEST_ANALYZE_MAX_BUCKETS,
        detail="Too many guest analyses from this network. Sign in or try again later.",
    )


def _enforce_leap_rank_quota(request: Request, user_id: str) -> None:
    """Per-IP quota so anonymous LEAP chain lookups cannot pump yfinance."""
    if user_id:
        _enforce_ip_quota(
            request,
            _auth_leap_rank_hits,
            max_per_window=_AUTH_LEAP_RANK_MAX_PER_WINDOW,
            window_seconds=_GUEST_LEAP_RANK_WINDOW_SECONDS,
            max_buckets=_LEAP_RANK_MAX_BUCKETS,
            detail="Too many LEAP lookups from this network. Sign in or try again later.",
        )
        return
    _enforce_ip_quota(
        request,
        _guest_leap_rank_hits,
        max_per_window=_GUEST_LEAP_RANK_MAX_PER_WINDOW,
        window_seconds=_GUEST_LEAP_RANK_WINDOW_SECONDS,
        max_buckets=_LEAP_RANK_MAX_BUCKETS,
        detail="Too many LEAP lookups from this network. Sign in or try again later.",
    )


async def _read_csv_upload(file: UploadFile, *, guest: bool) -> bytes:
    """Read the CSV. Guest uploads are capped so file.read() is never unbounded."""
    if not guest:
        return await file.read()

    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > _MAX_GUEST_CSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail="CSV is too large for a guest analysis. Maximum size is 5 MB.",
        )

    contents = await file.read(_MAX_GUEST_CSV_BYTES + 1)
    if len(contents) > _MAX_GUEST_CSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail="CSV is too large for a guest analysis. Maximum size is 5 MB.",
        )
    return contents


def validate_user_id(user_id: Optional[str]) -> None:
    """
    Validate user_id format to prevent injection attacks.

    Accepts UUID format (with or without hyphens) or alphanumeric strings up to 64 chars.
    Raises HTTPException if invalid.
    """
    if not user_id:
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


def _decode_csv_upload(contents: bytes) -> str:
    """Decode uploaded CSV bytes (UTF-8 with BOM, UTF-8, or Windows-1252)."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return contents.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(
        status_code=400,
        detail={
            "message": "Could not read the CSV file. Export a UTF-8 CSV from Robinhood and try again.",
            "errors": ["Unable to decode the uploaded file"],
        },
    )


_INVALID_QUERY_TOKENS = frozenset({"", "undefined", "null", "nan", "none"})


def _query_token(value: object) -> Optional[str]:
    """Normalize a query value, dropping JS/JSON empty tokens."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        return str(int(value)) if value.is_integer() else str(value)
    text = str(value).strip()
    if not text or text.lower() in _INVALID_QUERY_TOKENS:
        return None
    return text


def _coerce_int_query(value: object, default: int) -> int:
    token = _query_token(value)
    if token is None:
        return default
    try:
        return int(token)
    except ValueError:
        try:
            return int(float(token))
        except ValueError:
            return default


def _coerce_float_query(value: object, default: float) -> float:
    token = _query_token(value)
    if token is None:
        return default
    try:
        parsed = float(token)
    except ValueError:
        return default
    if parsed != parsed:  # NaN
        return default
    return parsed


def _tax_profile_from_query(
    filing_status: Optional[str],
    estimated_income: object,
    tax_year: object,
) -> TaxProfile:
    """Build a TaxProfile, falling back to defaults when query values are invalid.

    Invalid filing status, tax year, or income must not 422/500 the analyze
    endpoint (the in-app sample CSV uses the user's saved profile params).
    """
    status_token = _query_token(filing_status)
    try:
        fs = FilingStatus(status_token) if status_token else FilingStatus.SINGLE
    except ValueError:
        fs = FilingStatus.SINGLE

    year = _coerce_int_query(tax_year, 2026)
    if year < 2024 or year > 2026:
        year = 2026

    income = _coerce_float_query(estimated_income, 75000.0)
    if income < 0:
        income = 75000.0

    try:
        return TaxProfile(
            filing_status=fs,
            estimated_annual_income=income,
            tax_year=year,
        )
    except ValidationError:
        return TaxProfile()

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


def _compute_realized_summary(realized: list[RealizedEvent], tax_year: int) -> RealizedSummary:
    """
    Aggregate realized gain/loss events for the specified tax year.

    Filters realized events by sale_date.year == tax_year, then sums short-term
    and long-term gains/losses separately.
    """
    year_events = [e for e in realized if e.sale_date.year == tax_year]

    st_gains = sum(e.pnl for e in year_events if not e.is_long_term and e.pnl > 0)
    st_losses = sum(e.pnl for e in year_events if not e.is_long_term and e.pnl < 0)
    lt_gains = sum(e.pnl for e in year_events if e.is_long_term and e.pnl > 0)
    lt_losses = sum(e.pnl for e in year_events if e.is_long_term and e.pnl < 0)
    net_st = st_gains + st_losses
    net_lt = lt_gains + lt_losses

    return RealizedSummary(
        tax_year=tax_year,
        st_gains=round(st_gains, 2),
        st_losses=round(st_losses, 2),
        lt_gains=round(lt_gains, 2),
        lt_losses=round(lt_losses, 2),
        net_st=round(net_st, 2),
        net_lt=round(net_lt, 2),
        total_net=round(net_st + net_lt, 2),
        transactions_count=len(year_events),
    )


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
) -> bool:
    """Save analysis to history (best-effort, non-blocking)."""
    if not user_id:
        return False
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
            return True
        else:
            logger.warning("save_analysis_history returned None — check Supabase connection")
    except HistoryInsertConflict:
        # A concurrent insert already stored this run. The row exists.
        return True
    except Exception as e:
        logger.warning(f"Failed to save analysis history: {e}", exc_info=True)
    return False





def _process_ai_suggestions(
    tax_lots: List[Any],
    all_warnings: List[str],
) -> tuple[Dict[str, Any] | None, List[str]]:
    """
    Get AI-powered suggestions for tax-loss harvesting.

    Returns (ai_suggestions, updated_warnings) tuple.
    """
    ai_suggestions: Dict[str, Any] | None = None
    warnings: List[str] = all_warnings[:]  # Copy to avoid mutation

    ai_suggestions = _try_get_ai_suggestions(tax_lots, warnings)

    return ai_suggestions, warnings


def _classify_warning(
    warning: str,
    row_errors: list[str],
    option_assignments: dict[str, list[str]],
    corporate_actions: dict[str, int],
    stock_splits: dict[str, int],
    fallback_prices: list[str],
    passthrough: list[str],
) -> None:
    """Place a raw warning into the appropriate summary bucket."""
    if warning.startswith("Row "):
        row_errors.append(warning)
        return

    assignment_match = re.match(
        r"^Option assignment \(OASGN\) detected for (?P<symbol>\S+) on (?P<date>\d{2}/\d{2}/\d{4})",
        warning,
    )
    if assignment_match:
        option_assignments[assignment_match.group("symbol")].append(
            assignment_match.group("date")
        )
        return

    exercise_match = re.match(
        r"^Option exercise \(OEXCS\) detected for (?P<symbol>\S+) on (?P<date>\d{2}/\d{2}/\d{4})",
        warning,
    )
    if exercise_match:
        option_assignments[exercise_match.group("symbol")].append(
            exercise_match.group("date")
        )
        return

    corporate_match = re.match(
        r"^Corporate action \(OCA\) detected for (?P<symbol>\S+)",
        warning,
    )
    if corporate_match:
        corporate_actions[corporate_match.group("symbol")] += 1
        return

    split_match = re.match(r"^Stock split detected for (?P<symbol>\S+)", warning)
    if split_match:
        stock_splits[split_match.group("symbol")] += 1
        return

    price_match = re.match(r"^Using CSV-provided price for (?P<symbol>\S+) ", warning)
    if price_match:
        fallback_prices.append(price_match.group("symbol"))
        return

    passthrough.append(warning)


def _build_summarized_warning_messages(
    row_errors: list[str],
    option_assignments: dict[str, list[str]],
    corporate_actions: dict[str, int],
    stock_splits: dict[str, int],
    fallback_prices: list[str],
) -> list[str]:
    """Convert warning buckets into short plain-English messages."""
    summarized: list[str] = []

    if row_errors:
        if len(row_errors) == 1:
            summarized.append(row_errors[0])
        else:
            summarized.append(
                f"{len(row_errors)} CSV row(s) could not be parsed. First issue: {row_errors[0]}"
            )

    for symbol in sorted(option_assignments.keys()):
        dates = sorted(option_assignments[symbol])
        if len(dates) == 1:
            summarized.append(
                f"Option assignment affected {symbol} on {dates[0]}. We recorded the option result, but the resulting share position may need manual verification."
            )
        else:
            summarized.append(
                f"Option assignments affected {symbol} {len(dates)} times ({dates[0]} to {dates[-1]}). We recorded the option results, but the resulting share position may need manual verification."
            )

    for symbol in sorted(corporate_actions.keys()):
        count = corporate_actions[symbol]
        summarized.append(
            f"Corporate action activity may have changed the reported share count for {symbol} ({count} event{'s' if count != 1 else ''}). Position totals for {symbol} may be inaccurate until the brokerage CSV fully reflects the change."
        )

    for symbol in sorted(stock_splits.keys()):
        count = stock_splits[symbol]
        summarized.append(
            f"A stock split may have changed the reported share count for {symbol} ({count} event{'s' if count != 1 else ''}). Position totals for {symbol} may be inaccurate until the brokerage CSV fully reflects the split."
        )

    if fallback_prices:
        symbols = ", ".join(sorted(set(fallback_prices)))
        summarized.append(
            f"Live prices were unavailable for {symbols}, so the analysis used the CSV-provided price instead."
        )

    return summarized


def _dedupe_preserving_order(warnings: list[str]) -> list[str]:
    """Return unique warning strings without changing their order."""
    ordered: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        if warning not in seen:
            seen.add(warning)
            ordered.append(warning)
    return ordered


def _summarize_warnings(warnings: List[str]) -> List[str]:
    """Collapse repetitive technical warnings into shorter plain-English notes."""
    if not warnings:
        return []

    row_errors: list[str] = []
    option_assignments: dict[str, list[str]] = defaultdict(list)
    corporate_actions: dict[str, int] = defaultdict(int)
    stock_splits: dict[str, int] = defaultdict(int)
    fallback_prices: list[str] = []
    passthrough: list[str] = []

    for warning in warnings:
        _classify_warning(
            warning,
            row_errors,
            option_assignments,
            corporate_actions,
            stock_splits,
            fallback_prices,
            passthrough,
        )

    summarized = _build_summarized_warning_messages(
        row_errors,
        option_assignments,
        corporate_actions,
        stock_splits,
        fallback_prices,
    )

    return _dedupe_preserving_order([*summarized, *passthrough])


def _build_manual_review_notes_by_symbol(transactions: list) -> dict[str, str]:
    """Build per-symbol manual-review notes for unsupported position-changing events."""
    if not transactions:
        return {}

    events_by_symbol: dict[str, set[str]] = defaultdict(set)
    for txn in transactions:
        symbol = getattr(txn, "instrument", "")
        if not symbol:
            continue

        if txn.trans_code == TransCode.SPR:
            events_by_symbol[symbol].add("stock split activity")
        elif txn.trans_code == TransCode.OCA:
            events_by_symbol[symbol].add("corporate-action adjustments")
        elif txn.trans_code == TransCode.OASGN:
            events_by_symbol[symbol].add("option assignment activity")
        elif txn.trans_code == TransCode.OEXCS:
            events_by_symbol[symbol].add("option exercise activity")

    notes: dict[str, str] = {}
    for symbol, event_labels in events_by_symbol.items():
        labels = sorted(event_labels)
        if len(labels) == 1:
            events_text = labels[0]
        elif len(labels) == 2:
            events_text = f"{labels[0]} and {labels[1]}"
        else:
            events_text = f"{', '.join(labels[:-1])}, and {labels[-1]}"

        notes[symbol] = (
            f"Recent {events_text} affected {symbol}. Verify reported quantities, "
            f"adjusted contracts, and cost basis manually before acting."
        )

    return notes


def _apply_manual_review_flags(
    positions: list,
    suggestions: list,
    manual_review_notes: dict[str, str],
) -> None:
    """Attach structured manual-review metadata to affected positions and suggestions."""
    if not manual_review_notes:
        return

    for position in positions:
        reason = manual_review_notes.get(position.symbol)
        if not reason:
            continue
        position.manual_review_required = True
        position.manual_review_reason = reason

    for suggestion in suggestions:
        reason = manual_review_notes.get(suggestion.symbol)
        if not reason:
            continue
        suggestion.manual_review_required = True
        suggestion.manual_review_reason = reason


def _parse_supplemental_1099_summary(
    pdf_bytes: bytes,
    filename: str,
    current_symbols: set[str],
    expected_previous_year: int,
) -> Supplemental1099Summary:
    """Parse an optional Robinhood 1099 PDF into reconciliation context."""
    return parse_robinhood_1099_pdf(
        pdf_bytes,
        current_symbols=current_symbols,
        filename=filename,
        expected_previous_year=expected_previous_year,
    )



def _is_pdf_upload(supplemental_1099: UploadFile) -> bool:
    """True when the optional 1099 looks like a PDF by content type or filename."""
    content_type = (supplemental_1099.content_type or "").lower()
    filename = (supplemental_1099.filename or "").lower()
    return "pdf" in content_type or filename.endswith(".pdf")


def _is_empty_supplemental_summary(summary: Supplemental1099Summary) -> bool:
    """True when the parser returned no usable 1099 totals or tax year."""
    return (
        summary.tax_year is None
        and summary.short_term_proceeds == 0
        and summary.long_term_proceeds == 0
        and summary.short_term_cost_basis == 0
        and summary.long_term_cost_basis == 0
        and summary.short_term_wash_sale_disallowed == 0
        and summary.long_term_wash_sale_disallowed == 0
        and not summary.referenced_symbols
    )


async def _maybe_parse_supplemental_1099(
    supplemental_1099: UploadFile | None,
    current_symbols: set[str],
    dashboard_tax_year: int,
) -> tuple[Supplemental1099Summary | None, list[str], bytes]:
    """Parse the optional 1099 PDF and return any user-facing warnings.

    Same-year (1099 tax year == dashboard tax year) is a first-class compare.
    Immediately prior year is a previous-year supplement. Any other year is
    still shown as a previous-year supplement, with an honest mismatch warning.
    """
    if supplemental_1099 is None:
        return None, [], b""

    if not _is_pdf_upload(supplemental_1099):
        return None, ["Supplemental 1099 must be a PDF file (received unsupported content type)."], b""

    expected_previous_year = dashboard_tax_year - 1
    supplemental_bytes = b""
    try:
        supplemental_bytes = await supplemental_1099.read(_MAX_SUPPLEMENTAL_PDF_BYTES + 1)
        if len(supplemental_bytes) > _MAX_SUPPLEMENTAL_PDF_BYTES:
            return None, ["Supplemental 1099 PDF exceeds the 20 MB size limit and was ignored."], b""
        summary = _parse_supplemental_1099_summary(
            supplemental_bytes,
            supplemental_1099.filename or "1099.pdf",
            current_symbols,
            expected_previous_year,
        )
    except Exception as exc:
        logger.warning("Failed to parse supplemental 1099 PDF: %s", exc, exc_info=True)
        return None, ["Supplemental 1099 PDF could not be parsed and was ignored for this analysis."], supplemental_bytes

    if _is_empty_supplemental_summary(summary):
        return None, ["Supplemental 1099 PDF could not be parsed and was ignored for this analysis."], supplemental_bytes

    warnings: list[str] = []
    if summary.tax_year is not None and summary.tax_year not in (
        dashboard_tax_year,
        expected_previous_year,
    ):
        warnings.append(
            "The supplemental 1099 PDF was parsed successfully, but its tax year does not match the expected prior year for this analysis."
        )

    return summary, warnings, supplemental_bytes


def _apply_live_prices_to_tax_lots(
    tax_lots: list,
    all_warnings: list[str],
    *,
    allow_network: bool = True,
    fixture_prices: dict[str, float] | None = None,
) -> list:
    """Populate stock and option lots with live prices when available."""
    symbols = list({lot.symbol for lot in tax_lots if lot.asset_type == AssetType.STOCK})
    fallback_prices = {
        lot.symbol: lot.current_price
        for lot in tax_lots
        if lot.asset_type == AssetType.STOCK and lot.current_price is not None
    }
    if fixture_prices:
        # Fixtures win over lot fill prices.
        fallback_prices = {
            **fallback_prices,
            **{symbol.upper(): price for symbol, price in fixture_prices.items()},
        }
    if allow_network:
        live_prices, price_warnings = fetch_current_prices(symbols, fallback_prices)
    else:
        live_prices, price_warnings = fetch_current_prices(
            symbols,
            fallback_prices,
            allow_network=False,
        )
    all_warnings.extend(price_warnings)

    option_labels = list(
        {
            lot.contract_label
            for lot in tax_lots
            if lot.asset_type == AssetType.OPTION and lot.contract_label
        }
    )
    option_fallback_prices = {
        lot.contract_label: lot.current_price
        for lot in tax_lots
        if lot.asset_type == AssetType.OPTION
        and lot.contract_label
        and lot.current_price is not None
    }
    if allow_network:
        option_prices, option_price_warnings = fetch_option_prices(
            option_labels,
            option_fallback_prices,
        )
    else:
        option_prices, option_price_warnings = fetch_option_prices(
            option_labels,
            option_fallback_prices,
            allow_network=False,
        )
    all_warnings.extend(option_price_warnings)

    if fixture_prices:
        for symbol, price in fixture_prices.items():
            live_prices[symbol.upper()] = price

    for lot in tax_lots:
        if lot.asset_type == AssetType.STOCK and lot.symbol in live_prices:
            lot.current_price = live_prices[lot.symbol]
            continue
        if lot.asset_type == AssetType.OPTION and lot.contract_label in option_prices:
            lot.current_price = option_prices[lot.contract_label]

    return tax_lots


def _filter_suggestion_tax_lots(
    tax_lots: list,
    transactions: list,
) -> tuple[list, list[str]]:
    """Exclude stock lots with split/corporate-action drift from harvesting suggestions."""
    if not tax_lots or not transactions:
        return tax_lots, []

    affected_symbols = {
        txn.instrument
        for txn in transactions
        if txn.asset_type == AssetType.STOCK
        and txn.trans_code in (TransCode.SPR, TransCode.OCA)
    }
    if not affected_symbols:
        return tax_lots, []

    filtered_lots = []
    skipped_symbols: set[str] = set()
    for lot in tax_lots:
        if lot.asset_type == AssetType.STOCK and lot.symbol in affected_symbols:
            skipped_symbols.add(lot.symbol)
            continue
        filtered_lots.append(lot)

    warnings = [
        (
            f"Skipped automated harvesting suggestions for {symbol} stock lots because "
            f"a stock split or corporate action changed the share count. Verify {symbol} "
            f"manually before acting on any loss estimate."
        )
        for symbol in sorted(skipped_symbols)
    ]
    return filtered_lots, warnings


def _normalize_merge_mode(value: object) -> str:
    raw = str(value or "auto").strip().lower()
    return "replace" if raw == "replace" else "auto"


def _counts_only_lot_match_report(report: LotMatchReport) -> LotMatchReport:
    """Keep teaser counts; drop lot rows until the $49 packet is unlocked."""
    return report.model_copy(
        update={"matched": [], "gap": [], "unmatched": []}
    )


def _public_analysis(
    result: PortfolioAnalysis,
    *,
    keep_lot_rows: bool = False,
) -> PortfolioAnalysis:
    """Hide raw trades and unpaid lot-level 1099-B rows from the browser payload.

    Unpaid/guest analyze JSON (and anything persisted from it) must not include
    lot dates, amounts, or statuses. Counts stay so the $49 teaser still works.
    The in-app 2026 sample keeps lot rows so the desk can show the table;
    packet_unlocked stays false so download remains $49 gated.
    """
    updates: dict = {}
    book = result.activity_book
    if book and book.transactions:
        updates["activity_book"] = book.model_copy(update={"transactions": []})
    if not result.packet_unlocked and not keep_lot_rows:
        if result.lot_match_report is not None:
            updates["lot_match_report"] = _counts_only_lot_match_report(
                result.lot_match_report
            )
        supplemental = result.supplemental_1099
        if supplemental is not None and supplemental.lots:
            updates["supplemental_1099"] = supplemental.model_copy(
                update={"lots": []}
            )
    if not updates:
        return result
    return result.model_copy(update=updates)


def _apply_packet_year_grant(result: PortfolioAnalysis, user_id: str) -> PortfolioAnalysis:
    """Repeat uploads in a paid tax year stay unlocked — no second $49."""
    # Never carry an authorization claim from history JSON into a fresh result.
    result = result.model_copy(
        update={"packet_unlocked": False, "packet_session_id": None}
    )
    if not user_id or not result.analysis_id:
        return result
    tax_year = result.tax_profile.tax_year if result.tax_profile else None
    if tax_year is None:
        return result
    # History JSON may have been writable under legacy client RLS policies.
    # Only service-role snapshots or verified Stripe receipts prove a grant.
    session_id, lookup_succeeded = lookup_packet_grant_for_tax_year(
        user_id,
        tax_year,
    )
    if not lookup_succeeded:
        logger.warning(
            "Could not load durable packet grant for user=%s year=%s",
            user_id,
            tax_year,
        )
    if not session_id:
        entitlement, entitlement_lookup_succeeded = (
            lookup_packet_entitlement_for_tax_year(user_id, tax_year)
        )
        if not entitlement_lookup_succeeded:
            logger.warning(
                "Could not load packet entitlement for user=%s year=%s",
                user_id,
                tax_year,
            )
        elif entitlement:
            try:
                packet_api_key = _configure_packet_stripe()
                prior_session = stripe.checkout.Session.retrieve(
                    entitlement["packet_session_id"],
                    api_key=packet_api_key,
                )
            except (HTTPException, stripe.StripeError) as exc:
                logger.warning("Could not verify prior packet entitlement: %s", exc)
                prior_session = None
            metadata_year = _packet_result_tax_year(
                {"analysis_tax_year": _session_metadata(prior_session).get("tax_year")}
            )
            if (
                prior_session is not None
                and packet_session_id(prior_session)
                == entitlement.get("packet_session_id")
                and packet_user_id_from_session(prior_session) == user_id
                and packet_analysis_id_from_session(prior_session)
                == entitlement.get("analysis_id")
                and metadata_year == tax_year
                and session_is_settled_packet(prior_session)
            ):
                session_id = packet_session_id(prior_session)
    if not session_id:
        session_id = paid_session_for_user_year(user_id, tax_year)
    if not session_id:
        return result
    if not packet_store_belongs_to_user(result.analysis_id, user_id):
        return result
    return result.model_copy(
        update={"packet_unlocked": True, "packet_session_id": session_id}
    )


def _resolve_activity_book(
    *,
    user_id: str,
    filename: str,
    merge_mode: str,
    transactions: list,
    parse_errors: list[str],
    tax_lots: list,
    realized_events: list,
):
    """Merge this upload with the saved book when the user is signed in."""
    merge_mode = _normalize_merge_mode(merge_mode)
    sample = is_sample_csv_filename(filename)
    prior = None
    if user_id and merge_mode != "replace" and not sample:
        prior = get_latest_activity_book(user_id)
        if prior and is_sample_csv_filename(prior.get("filename") or ""):
            prior = None
    prior_txns = transactions_from_stored((prior or {}).get("transactions"))

    if prior_txns and transactions:
        merged = merge_transaction_books(prior_txns, transactions)
        tax_lots, lot_warnings, realized_events = transactions_to_tax_lots(
            merged.transactions
        )
        dropped = (
            "no open lots at all",
            "exceeded the available open lot quantity",
            "none matched the required asset type",
        )
        kept = [
            error
            for error in parse_errors
            if not any(needle in error for needle in dropped)
        ]
        kept.extend(lot_warnings)
        warning = merge_warning(merged, (prior or {}).get("filename") or "")
        if warning:
            kept.append(warning)
        parse_errors[:] = kept
        book = ActivityBookSummary(
            transaction_count=len(merged.transactions),
            first_activity_date=merged.first_activity_date,
            last_activity_date=merged.last_activity_date,
            added_from_this_upload=merged.added,
            already_in_book=merged.already_in_book,
            merged_from_analysis_id=(prior or {}).get("analysis_id"),
            merged_from_filename=(prior or {}).get("filename") or "",
            gap_days=merged.gap_days,
            replaced=False,
            transactions=merged.transactions,
        )
        return tax_lots, merged.transactions, realized_events, book

    stored_txns = list(transactions)
    if not stored_txns and prior_txns:
        stored_txns = prior_txns
        note = (
            "This file is a position snapshot, not a transaction export. "
            "Your saved trade book was kept. Upload a Robinhood activity CSV "
            "to add new trades."
        )
        parse_errors.append(note)
    first = min((txn.activity_date for txn in stored_txns), default=None)
    last = max((txn.activity_date for txn in stored_txns), default=None)
    kept_prior = bool(not transactions and prior_txns and prior)
    book = ActivityBookSummary(
        transaction_count=len(stored_txns),
        first_activity_date=first,
        last_activity_date=last,
        added_from_this_upload=len(transactions),
        already_in_book=0,
        merged_from_analysis_id=(prior or {}).get("analysis_id") if kept_prior else None,
        merged_from_filename=(prior or {}).get("filename") or "" if kept_prior else "",
        gap_days=0,
        replaced=merge_mode == "replace" or sample or not prior_txns,
        transactions=stored_txns,
    )
    return tax_lots, transactions, realized_events, book


@app.post(
    "/api/portfolio/analyze",
    response_model=PortfolioAnalysis,
    responses={
        400: {"description": "Invalid user ID format or unparseable CSV"},
        413: {"description": "Guest CSV exceeds size limit"},
        429: {"description": "Guest analyze quota exceeded"},
    },
)
async def analyze_portfolio(
    request: Request,
    file: Annotated[UploadFile, File()],
    supplemental_1099: Annotated[Optional[UploadFile], File()] = None,
    filing_status: Annotated[Optional[str], Query()] = "single",
    estimated_income: Annotated[Optional[str], Query()] = None,
    tax_year: Annotated[Optional[str], Query()] = None,
    merge_mode: Annotated[Optional[str], Query()] = "auto",
    user_id: Annotated[str, Depends(get_optional_user)] = "",
):
    """
    Full portfolio analysis with tax-loss harvesting suggestions.

    Accepts a CSV file (Robinhood transaction history or simplified format),
    fetches live prices, runs tax engine, detects wash sales, and generates
    AI-powered harvesting suggestions.

    Authentication is optional. With a valid Supabase JWT the analysis is saved
    to the user's history. Guests still get a full analysis; it is not persisted.
    Guest uploads are size-capped and rate-limited.

    DISCLAIMER: For educational/simulation purposes only — not financial or tax advice.
    """
    # Validate user_id format if provided
    validate_user_id(user_id)

    guest = not user_id
    if guest:
        _enforce_guest_analyze_quota(request)

    contents = await _read_csv_upload(file, guest=guest)
    try:
        return await _run_portfolio_analysis(
            contents,
            filename=file.filename or "upload.csv",
            supplemental_1099=supplemental_1099,
            filing_status=filing_status,
            estimated_income=estimated_income,
            tax_year=tax_year,
            user_id=user_id,
            merge_mode=merge_mode,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Portfolio analysis failed")
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Analysis failed while processing the CSV. Please try again.",
                "errors": [str(exc)],
            },
        ) from exc


async def _run_portfolio_analysis(
    contents: bytes,
    *,
    filename: str,
    supplemental_1099: Optional[UploadFile],
    filing_status: Optional[str],
    estimated_income: object,
    tax_year: object,
    user_id: str,
    merge_mode: object = "auto",
) -> PortfolioAnalysis:
    """Parse the CSV and run tax, wash-sale, and harvesting analysis."""
    csv_text = _decode_csv_upload(contents)
    tax_lots, transactions, parse_errors, realized_events = parse_csv(csv_text)

    if not tax_lots and not transactions:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Could not parse any positions from the CSV file.",
                "errors": parse_errors,
            },
        )

    tax_lots, transactions, realized_events, activity_book = _resolve_activity_book(
        user_id=user_id,
        filename=filename,
        merge_mode=_normalize_merge_mode(merge_mode),
        transactions=transactions,
        parse_errors=parse_errors,
        tax_lots=tax_lots,
        realized_events=realized_events,
    )

    tax_profile = _tax_profile_from_query(filing_status, estimated_income, tax_year)

    all_warnings = list(parse_errors)
    (
        supplemental_1099_summary,
        supplemental_1099_warnings,
        supplemental_bytes,
    ) = await _maybe_parse_supplemental_1099(
        supplemental_1099,
        {lot.symbol for lot in tax_lots},
        tax_profile.tax_year or 2026,
    )
    all_warnings.extend(supplemental_1099_warnings)

    trusted_sample = is_trusted_in_app_sample(contents, supplemental_bytes)
    tax_lots = _apply_live_prices_to_tax_lots(
        tax_lots,
        all_warnings,
        allow_network=not trusted_sample,
        fixture_prices=SAMPLE_FIXTURE_PRICES if trusted_sample else None,
    )

    tax_lots = compute_lot_metrics(tax_lots)

    # Detect wash sales from transaction history
    wash_sale_flags = (
        detect_wash_sales(transactions, tax_year=tax_profile.tax_year)
        if transactions
        else []
    )
    if wash_sale_flags:
        tax_lots = adjust_lots_for_wash_sales(tax_lots, wash_sale_flags)

    tax_lots, residual_warnings = suppress_fractional_residual_positions(
        tax_lots,
        transactions,
    )
    all_warnings.extend(residual_warnings)

    suggestion_tax_lots, suggestion_filter_warnings = _filter_suggestion_tax_lots(
        tax_lots,
        transactions,
    )
    all_warnings.extend(suggestion_filter_warnings)

    if trusted_sample:
        ai_suggestions = None
    else:
        ai_suggestions, all_warnings = _process_ai_suggestions(
            suggestion_tax_lots,
            all_warnings,
        )

    suggestions = generate_suggestions(
        tax_lots=suggestion_tax_lots,
        transactions=transactions,
        tax_profile=tax_profile,
        ai_suggestions=ai_suggestions,
    )

    positions = aggregate_positions(tax_lots)
    manual_review_notes = _build_manual_review_notes_by_symbol(transactions)
    _apply_manual_review_flags(positions, suggestions, manual_review_notes)
    summary = build_portfolio_summary(positions, suggestions, wash_sale_flags)

    # Compute realized gain/loss breakdown for the requested tax year
    summary.realized_summary = _compute_realized_summary(
        realized_events, tax_profile.tax_year
    )
    if activity_book:
        summary.activity_first_date = activity_book.first_activity_date
        summary.activity_last_date = activity_book.last_activity_date
        summary.activity_transaction_count = activity_book.transaction_count

    lot_match_report = None
    if supplemental_1099_summary is not None:
        lot_match_report = match_1099b_lots(
            supplemental_1099_summary.lots,
            realized_events,
            form_1099_tax_year=supplemental_1099_summary.tax_year,
            analysis_tax_year=tax_profile.tax_year,
            short_term_proceeds=supplemental_1099_summary.short_term_proceeds,
            long_term_proceeds=supplemental_1099_summary.long_term_proceeds,
            short_term_cost_basis=supplemental_1099_summary.short_term_cost_basis,
            long_term_cost_basis=supplemental_1099_summary.long_term_cost_basis,
            short_term_wash=supplemental_1099_summary.short_term_wash_sale_disallowed,
            long_term_wash=supplemental_1099_summary.long_term_wash_sale_disallowed,
        )
        if lot_match_report is not None and not lot_match_report.totals_ok:
            all_warnings.append(
                "Parsed 1099-B lots do not sum to the broker ST/LT totals. "
                "Summary totals are unchanged; lot rows are still listed."
            )

    analysis_id = str(uuid.uuid4())
    result = PortfolioAnalysis(
        positions=positions,
        tax_lots=tax_lots,
        suggestions=suggestions,
        wash_sale_flags=wash_sale_flags,
        summary=summary,
        tax_profile=tax_profile,
        supplemental_1099=supplemental_1099_summary,
        lot_match_report=lot_match_report,
        analysis_id=analysis_id,
        activity_book=activity_book,
        warnings=_summarize_warnings(all_warnings),
    )
    result = _apply_packet_year_grant(result, user_id)
    keep_lot_rows = trusted_sample
    if keep_lot_rows:
        result = result.model_copy(update={"sample_run": True})
    full_dump = (
        result.model_dump(mode="json")
        if hasattr(result, "model_dump")
        else dict(result)
    )
    remember_analysis(analysis_id, user_id, full_dump)
    public_result = _public_analysis(result, keep_lot_rows=keep_lot_rows)

    # History is always redacted. Lot rows stay in PACKET_STORE and the paid
    # snapshot. Grant flags are patched on only after that snapshot commits,
    # so a failed snapshot cannot leave private lots in the history JSON.
    history_result = _public_analysis(
        result.model_copy(update={"packet_unlocked": False, "packet_session_id": None}),
        keep_lot_rows=keep_lot_rows,
    )
    history_saved = _save_history_best_effort(
        user_id, filename, summary, history_result
    )
    if result.packet_unlocked and result.packet_session_id and not history_saved:
        # Paid documents must remain attached to a deletable history row.
        forget_packet_payload(result.analysis_id, user_id)
        result = result.model_copy(
            update={"packet_unlocked": False, "packet_session_id": None}
        )
        public_result = _public_analysis(result, keep_lot_rows=keep_lot_rows)
        return public_result
    if user_id and not (result.packet_unlocked and result.packet_session_id):
        packet_payload = get_payload(result.analysis_id)
        tax_year = _packet_result_tax_year(packet_payload)
        if packet_payload and tax_year is not None:
            if not save_packet_snapshot(
                result.analysis_id,
                user_id,
                tax_year,
                packet_payload,
            ):
                logger.warning(
                    "Could not persist unpaid packet snapshot for analysis %s",
                    result.analysis_id,
                )
    if result.packet_unlocked and result.packet_session_id:
        packet_payload = get_payload(result.analysis_id)
        tax_year = _packet_result_tax_year(packet_payload)
        snapshot_saved = bool(
            packet_payload
            and tax_year is not None
            and save_packet_snapshot(
                result.analysis_id,
                user_id,
                tax_year,
                packet_payload,
                session_id=result.packet_session_id,
                paid=True,
            )
        )
        if snapshot_saved:
            mark_paid(
                result.analysis_id,
                result.packet_session_id,
                user_id=user_id,
            )
            if history_saved:
                patched = patch_analysis_result(
                    result.analysis_id,
                    user_id,
                    {
                        "packet_unlocked": True,
                        "packet_session_id": result.packet_session_id,
                    },
                )
                if patched is not True:
                    logger.warning(
                        "Paid packet snapshot for %s has no history flag update",
                        result.analysis_id,
                    )
            return public_result
        if history_saved:
            patch_analysis_result(
                result.analysis_id,
                user_id,
                {"packet_unlocked": False, "packet_session_id": None},
            )
        result = result.model_copy(
            update={"packet_unlocked": False, "packet_session_id": None}
        )
        public_result = _public_analysis(result, keep_lot_rows=keep_lot_rows)

    return public_result


@app.post(
    "/api/portfolio/history",
    responses={
        401: {"description": "Authentication required"},
        503: {"description": "Could not save analysis history"},
    },
)
async def persist_portfolio_history(
    body: PersistGuestAnalysisBody,
    user_id: Annotated[str, Depends(get_current_user)],
):
    """Save a guest (or restored) analysis into the signed-in user's history."""
    validate_user_id(user_id)
    analysis = dict(body.analysis or {})
    # This endpoint stores browser-restored guest results. Never trust payment
    # state supplied by the client; only verified server paths may set it.
    analysis.pop("packet_unlocked", None)
    analysis.pop("packet_session_id", None)
    analysis_id = str(analysis.get("analysis_id") or "").strip()
    guest_packet_payload = claimable_guest_packet_payload(
        analysis_id,
        user_id,
        analysis,
    )
    summary = analysis.get("summary") if isinstance(analysis.get("summary"), dict) else {}
    filename = Path(body.filename or "guest-run.csv").name.strip() or "guest-run.csv"
    if analysis_id:
        existing_history, lookup_succeeded = lookup_analysis_for_entitlement(
            analysis_id,
            user_id,
        )
        if not lookup_succeeded:
            raise HTTPException(
                status_code=503,
                detail="Could not check whether this analysis is already saved. Please retry.",
            )
    else:
        existing_history = None
    if existing_history and not guest_packet_payload:
        durable_snapshot, snapshot_lookup_succeeded = get_packet_snapshot(
            analysis_id,
            user_id,
        )
        if not snapshot_lookup_succeeded:
            raise HTTPException(
                status_code=503,
                detail="Could not restore this analysis snapshot. Please retry.",
            )
        if durable_snapshot and isinstance(durable_snapshot.get("packet_payload"), dict):
            return existing_history
        raise HTTPException(
            status_code=503,
            detail="This saved analysis is missing its private packet data. Please re-run the analysis.",
        )
    newly_inserted = False
    if existing_history:
        saved = existing_history
    else:
        try:
            saved = save_analysis_history(
                user_id=user_id,
                filename=filename[:255],
                summary=summary,
                result_data=analysis,
            )
        except HistoryInsertConflict:
            saved = None
            if analysis_id:
                raced, race_ok = lookup_analysis_for_entitlement(analysis_id, user_id)
                if not race_ok:
                    raise HTTPException(
                        status_code=503,
                        detail="Could not check whether this analysis is already saved. Please retry.",
                    )
                saved = raced
        else:
            newly_inserted = bool(saved)
    if not saved:
        raise HTTPException(
            status_code=503,
            detail="Could not save analysis history",
        )
    if guest_packet_payload:
        tax_year = _packet_result_tax_year(guest_packet_payload)
        if tax_year is None or not save_packet_snapshot(
            analysis_id,
            user_id,
            tax_year,
            guest_packet_payload,
        ):
            if newly_inserted:
                _rollback_inserted_history(saved, user_id)
            raise HTTPException(
                status_code=503,
                detail="Could not securely save this analysis. Please retry.",
            )
        # Claim the already-validated server snapshot only after its durable,
        # owner-scoped copy has been saved successfully. Ownership moves only
        # because claimable_guest_packet_payload matched exactly.
        if not adopt_guest_packet(analysis_id, user_id, guest_packet_payload):
            raise HTTPException(
                status_code=503,
                detail="Could not securely save this analysis. Please retry.",
            )
        upsert_packet_payload(analysis_id, user_id, guest_packet_payload)
    return saved


@app.get(
    "/api/portfolio/history",
    responses={500: {"description": "Database connection failed"}},
)
async def get_portfolio_history(
    user_id: Annotated[str, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """
    Retrieve authenticated user's past portfolio analyses, newest first.

    Returns summary metadata (filename, date, positions count, market value)
    without the full position data (which is processed in-memory only).

    **Authentication Required**: Must provide valid Supabase JWT token.
    **Security**: user_id is extracted from the verified JWT; the query filters
    by user_id so users can only access their own analyses.
    """
    # Use service role client — security is enforced at the app level:
    # user_id comes from the verified JWT, and the query filters by user_id.
    db_client = get_supabase()

    if not db_client:
        raise HTTPException(
            status_code=500,
            detail="Database connection failed"
        )

    history = get_analysis_history(user_id, limit, client=db_client)
    return history


@app.get(
    "/api/portfolio/analysis/{analysis_id}",
    responses={
        404: {"description": "Analysis not found"},
        500: {"description": "Database connection failed"},
    },
)
async def get_portfolio_analysis(
    analysis_id: str,
    user_id: Annotated[str, Depends(get_current_user)],
):
    """
    Retrieve a single past portfolio analysis by ID, including the full result.

    Used when a user clicks a history item to reload that report.

    **Authentication Required**: Must provide valid Supabase JWT token.
    **Security**: user_id is extracted from the verified JWT. The query filters
    by both analysis_id and user_id so users can only access their own analyses.
    """
    # Use service role client — security enforced at app level via user_id filter.
    db_client = get_supabase()

    if not db_client:
        raise HTTPException(
            status_code=500,
            detail="Database connection failed"
        )

    record = get_analysis_by_id(analysis_id, user_id, client=db_client)
    if not record:
        raise HTTPException(status_code=404, detail="Analysis not found")
    # Enforce ownership (redundant with RLS, but good defense-in-depth)
    enforce_ownership(user_id, record.get("user_id", ""))
    if isinstance(record.get("result"), dict):
        record = {
            **record,
            "result": strip_book_transactions_dict(record["result"]),
        }
    return record


@app.delete("/api/portfolio/history/cleanup")
async def cleanup_orphan_history(
    user_id: Annotated[str, Depends(get_current_user)],
):
    """
    Delete portfolio analysis entries that have no stored result data.

    These are legacy rows created before the app started persisting
    the full analysis result. Returns the count of deleted rows.

    **Authentication Required**: Must provide valid Supabase JWT token.
    """
    deleted = delete_analyses_without_result(user_id)
    return {"deleted": deleted}


@app.delete(
    "/api/portfolio/analysis/{analysis_id}",
    responses={404: {"description": "Analysis not found"}},
)
async def delete_portfolio_analysis(
    analysis_id: str,
    user_id: Annotated[str, Depends(get_current_user)],
):
    """
    Delete a single portfolio analysis by ID.

    **Authentication Required**: Must provide valid Supabase JWT token.
    **Authorization**: User can only delete their own analyses.
    """
    record = get_analysis_by_id(analysis_id, user_id)
    if not record:
        raise HTTPException(status_code=404, detail="Analysis not found")
    result = record.get("result") if isinstance(record, dict) else None
    packet_analysis_id = (
        result.get("analysis_id") if isinstance(result, dict) else None
    )
    deleted = delete_analysis_by_id(analysis_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if isinstance(packet_analysis_id, str) and packet_analysis_id:
        forget_packet_payload(packet_analysis_id, user_id)
    forget_packet_payload(analysis_id, user_id)
    return {"deleted": True}


@app.get(
    "/api/prices",
    responses={400: {"description": "No symbols provided"}},
)
async def get_prices(
    symbols: Annotated[str, Query(description="Comma-separated ticker symbols")],
):
    """Fetch current prices for given symbols via yfinance."""
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="No symbols provided")

    prices, warnings = fetch_current_prices(symbol_list)
    return {"prices": prices, "warnings": warnings}


def _parse_iso_date_query(value: str, field: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be an ISO date (YYYY-MM-DD).",
        ) from exc


@app.get(
    "/api/options/leap-rank",
    responses={
        400: {"description": "Invalid query"},
        429: {"description": "Too many LEAP lookups"},
    },
)
async def get_leap_rank(
    request: Request,
    symbol: Annotated[str, Query(description="Underlying ticker")],
    right: Annotated[str, Query(description="call or put")],
    expiry_from: Annotated[str, Query(description="Window start YYYY-MM-DD")],
    expiry_to: Annotated[str, Query(description="Window end YYYY-MM-DD")],
    user_id: Annotated[str, Depends(get_optional_user)] = "",
):
    """
    Rank long LEAPs vs owning the stock. Free. Guests allowed.

    Lower implied CAGR (less annualized move to break even) ranks first.
    Missing quotes or chains return 200 with ok=false and no ranks.
    """
    symbol_clean = symbol.strip().upper()
    right_clean = right.strip().lower()
    if not _LEAP_RANK_SYMBOL_RE.fullmatch(symbol_clean):
        raise HTTPException(
            status_code=400,
            detail="Underlying ticker must be 1–10 letters.",
        )
    if right_clean not in ("call", "put"):
        raise HTTPException(
            status_code=400,
            detail="right must be call or put.",
        )

    start = _parse_iso_date_query(expiry_from, "expiry_from")
    end = _parse_iso_date_query(expiry_to, "expiry_to")
    if start > end:
        raise HTTPException(
            status_code=400,
            detail="expiry_from must be on or before expiry_to.",
        )
    if (end - start).days > _LEAP_RANK_MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=400,
            detail="Expiry window is too long.",
        )

    as_of = datetime.now(timezone.utc).date()
    if end < as_of:
        raise HTTPException(
            status_code=400,
            detail="Expiry window is in the past.",
        )

    _enforce_leap_rank_quota(request, user_id)

    prices, _price_warnings = fetch_current_prices([symbol_clean])
    spot = prices.get(symbol_clean)
    if not spot:
        return rank_from_chain(
            symbol=symbol_clean,
            right=right_clean,
            spot=None,
            as_of=as_of,
            expiry_from=start,
            expiry_to=end,
            rows=[],
            expirations_used=[],
            warnings=[],
        )

    rows, expirations_used, chain_warnings = fetch_option_chain_window(
        symbol_clean,
        right_clean,
        start.isoformat(),
        end.isoformat(),
        as_of=as_of.isoformat(),
    )
    return rank_from_chain(
        symbol=symbol_clean,
        right=right_clean,
        spot=spot,
        as_of=as_of,
        expiry_from=start,
        expiry_to=end,
        rows=rows,
        expirations_used=expirations_used,
        warnings=chain_warnings,
    )


def _enforce_rh_chain_quota(request: Request, user_id: str) -> None:
    _enforce_ip_quota(
        request,
        _rh_chain_hits,
        max_per_window=_AUTH_LEAP_RANK_MAX_PER_WINDOW if user_id else 20,
        window_seconds=_GUEST_LEAP_RANK_WINDOW_SECONDS,
        max_buckets=_LEAP_RANK_MAX_BUCKETS,
        detail="Too many Robinhood chain lookups. Try again later.",
    )


@app.get("/api/oth/options/rh-status")
async def get_oth_rh_status(
    user_id: Annotated[str, Depends(get_optional_user)] = "",
):
    """Whether this OTH user has a per-user RH credential. Never returns tokens."""
    if not user_id:
        return {
            "connected": False,
            "code": "RH_CONNECTION_REQUIRED",
            "message": RH_CONNECTION_REQUIRED_COPY,
        }
    try:
        cred = rh_credential_store.get_for_user(user_id)
    except RhChainError:
        return {"connected": False, "code": "RH_REVOKED"}
    return {"connected": cred is not None}


@app.get(
    "/api/oth/options/chain",
    responses={
        400: {"description": "Invalid query"},
        429: {"description": "Too many RH chain lookups"},
    },
)
async def get_oth_options_chain(
    request: Request,
    symbol: Annotated[str, Query(description="Underlying ticker")],
    min_expiry: Annotated[str, Query(description="Window start YYYY-MM-DD")],
    max_expiry: Annotated[str, Query(description="Window end YYYY-MM-DD")],
    side: Annotated[str, Query(description="call or put")] = "call",
    user_id: Annotated[str, Depends(get_optional_user)] = "",
):
    """RH-backed LEAP rank for the authenticated OTH user only.

    Guests and users without a per-user RH credential get an honest empty
    ranking. Never accepts a browser RH token. Never uses MCP or a shared
    trader session.
    """
    if request.query_params.get("token") or request.query_params.get("rh_token"):
        raise HTTPException(
            status_code=400,
            detail="Robinhood tokens are not accepted as query parameters.",
        )
    symbol_clean = symbol.strip().upper()
    side_clean = side.strip().lower()
    if not _LEAP_RANK_SYMBOL_RE.fullmatch(symbol_clean):
        raise HTTPException(
            status_code=400,
            detail="Underlying ticker must be 1–10 letters.",
        )
    if side_clean not in ("call", "put"):
        raise HTTPException(
            status_code=400,
            detail="side must be call or put.",
        )
    start = _parse_iso_date_query(min_expiry, "min_expiry")
    end = _parse_iso_date_query(max_expiry, "max_expiry")
    if start > end:
        raise HTTPException(
            status_code=400,
            detail="min_expiry must be on or before max_expiry.",
        )
    if (end - start).days > _LEAP_RANK_MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=400,
            detail="Expiry window is too long.",
        )
    as_of = datetime.now(timezone.utc).date()
    if end < as_of:
        raise HTTPException(
            status_code=400,
            detail="Expiry window is in the past.",
        )

    _enforce_rh_chain_quota(request, user_id)
    if not user_id:
        return connection_required_payload()

    return fetch_and_rank_chain(
        user_id=user_id,
        symbol=symbol_clean,
        side=side_clean,
        min_expiry=start,
        max_expiry=end,
        store=rh_credential_store,
        client=rh_chain_client,
        cache=rh_chain_cache,
        as_of=as_of,
    )


@app.get("/api/tax-brackets")
async def get_tax_brackets(
    year: Annotated[int, Query(ge=2024, le=2026)] = 2026,
    filing_status: Annotated[str, Query()] = "single",
    income: Annotated[float, Query(ge=0)] = 75000.0,
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


@app.post(
    "/api/tax-profile",
    responses={403: {"description": "Cannot save tax profile for another user"}},
)
async def save_tax_profile_endpoint(
    profile: TaxProfile,
    user_id: Annotated[str, Depends(get_current_user)],
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
        normalized_profile = TaxProfile.model_validate(saved).model_dump(mode="json")
        return {"message": "Tax profile saved", "profile": normalized_profile}

    # Fallback: return the validated profile even if DB is unavailable
    return {"message": "Tax profile saved (not persisted)", "profile": profile.model_dump()}


@app.get("/api/tax-profile")
async def get_tax_profile_endpoint(
    user_id: Annotated[str, Depends(get_current_user)],
):
    """
    Retrieve authenticated user's saved tax profile from Supabase.

    Returns default profile if no saved profile exists.

    **Authentication Required**: Must provide valid Supabase JWT token.
    """
    saved = db_get_tax_profile(user_id)
    if saved:
        return TaxProfile.model_validate(saved).model_dump(mode="json")

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


@app.post(
    "/api/tips/checkout",
    responses={
        400: {"description": "Invalid tip tier"},
        502: {"description": "Stripe checkout session creation failed"},
        503: {"description": "Stripe is not configured"},
    },
)
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

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": tier["price_id"], "quantity": 1}],
            success_url=f"{FRONTEND_URL}/tips/success",
            cancel_url=f"{FRONTEND_URL}/tips/cancel",
            api_key=STRIPE_SECRET_KEY,
        )
        return {"checkout_url": session.url}
    except stripe.StripeError as e:
        logger.error(f"Stripe checkout error: {e}")
        raise HTTPException(status_code=502, detail="Failed to create checkout session")


# --- Year-close packet ($49 one-time, not a tip, not a subscription) ---


class PacketCheckoutRequest(BaseModel):
    analysis_id: str
    analysis: Optional[dict] = None


class PacketConfirmRequest(BaseModel):
    session_id: str
    analysis_id: Optional[str] = None
    packet_analysis: Optional[str] = None
    analysis: Optional[dict] = None


class PacketDownloadRequest(BaseModel):
    analysis_id: str
    session_id: Optional[str] = None
    analysis: Optional[dict] = None


def _rollback_inserted_history(saved: dict, user_id: str) -> None:
    """Drop a history row this request just inserted after a failed snapshot."""
    row_id = str((saved or {}).get("id") or "").strip()
    if not row_id:
        return
    try:
        delete_analysis_by_id(row_id, user_id)
    except Exception:
        logger.warning(
            "Could not roll back history row %s after packet snapshot failure",
            row_id,
            exc_info=True,
        )


def _packet_checkout_idempotency_key(
    user_id: str,
    analysis_id: str,
    tax_year: int,
) -> str:
    """Stable Stripe idempotency key. Hashed when the raw key would exceed 255."""
    raw = f"year-close-packet:{user_id}:{analysis_id}:{tax_year}"
    if len(raw) <= 255:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"year-close-packet:{digest}"


def _configure_packet_stripe() -> str:
    """Return the request-scoped packet key. Staging is TEST-only."""
    key, reason = resolve_packet_stripe_secret_key(STRIPE_SECRET_KEY)
    if not key:
        if reason == "refused_live_key":
            raise HTTPException(
                status_code=503,
                detail=(
                    "Year-close packet checkout on staging/local requires Stripe TEST "
                    "keys. Set STRIPE_SECRET_KEY_TEST to an sk_test_ key "
                    "(do not use live keys for this accept path)."
                ),
            )
        raise HTTPException(status_code=503, detail="Stripe is not configured")
    return key


PACKET_GRANT_MISSING_SOURCE = "missing_source"
PACKET_MISSING_SOURCE_DETAIL = (
    "Payment was received, but this analysis has no source document. "
    "Run a new analysis for this tax year."
)


def _persist_packet_grant(session, analysis_id: str, user_id: str) -> Optional[bool | str]:
    if not session_grants_packet(session, analysis_id):
        return False
    session_analysis_id = packet_analysis_id_from_session(session)
    session_user_id = packet_user_id_from_session(session)
    if not session_user_id or not user_id or session_user_id != user_id:
        logger.warning("Rejecting packet grant with missing or mismatched checkout owner")
        return False
    cache_belongs_to_user = packet_store_belongs_to_user(session_analysis_id, user_id)
    session_id = packet_session_id(session)
    snapshot, snapshot_lookup_succeeded = get_packet_snapshot(
        session_analysis_id,
        session_user_id,
    )
    if not snapshot_lookup_succeeded:
        return None
    if snapshot:
        tax_year = snapshot.get("tax_year")
        packet_payload = snapshot.get("packet_payload")
        if not isinstance(packet_payload, dict) and cache_belongs_to_user:
            cached_payload = get_payload(session_analysis_id)
            if isinstance(cached_payload, dict):
                packet_payload = cached_payload
    else:
        # Compatibility for sessions created before private snapshots existed.
        record, record_lookup_succeeded = lookup_analysis_for_entitlement(
            session_analysis_id,
            session_user_id,
        )
        if not record_lookup_succeeded:
            return None
        result = record.get("result") if isinstance(record, dict) else None
        tax_year = _packet_result_tax_year(result)
        metadata = _session_metadata(session)
        try:
            tax_year = int(metadata.get("tax_year") or tax_year)
        except (TypeError, ValueError):
            pass
        packet_payload = (
            get_payload(session_analysis_id) if cache_belongs_to_user else None
        )
        if tax_year is None:
            logger.warning("Paid packet has no recoverable tax year: %s", session_analysis_id)
            return False
        # A deleted history row must not be replaced by a private snapshot
        # rebuilt from process memory.
        if not record:
            packet_payload = None
        elif isinstance(packet_payload, dict) and not save_packet_snapshot(
            session_analysis_id,
            session_user_id,
            tax_year,
            packet_payload,
        ):
            return None
    try:
        tax_year = int(tax_year)
    except (TypeError, ValueError):
        return False
    metadata_year = _packet_result_tax_year(
        {"analysis_tax_year": _session_metadata(session).get("tax_year")}
    )
    if metadata_year is not None and metadata_year != tax_year:
        logger.warning(
            "Rejecting packet session for mismatched tax year %s (snapshot %s)",
            metadata_year,
            tax_year,
        )
        return False
    if (
        snapshot
        and not isinstance(snapshot.get("packet_payload"), dict)
        and isinstance(packet_payload, dict)
    ):
        history_row, history_ok = lookup_analysis_for_entitlement(
            session_analysis_id,
            session_user_id,
        )
        if not history_ok:
            return None
        if not history_row:
            packet_payload = None
        elif not save_packet_snapshot(
            session_analysis_id,
            session_user_id,
            tax_year,
            packet_payload,
            session_id=session_id,
            paid=True,
        ):
            return None
    if not isinstance(packet_payload, dict):
        # Keep the settled session as a same-year entitlement, but do not tell
        # confirm/webhook that a downloadable packet exists without its private
        # source document. A fresh analysis can reuse this receipt.
        saved_entitlement = save_packet_entitlement(
            session_analysis_id,
            session_user_id,
            tax_year,
            session_id,
        )
        if saved_entitlement is None:
            return None
        logger.error(
            "Persisted paid packet entitlement without its source document: %s",
            session_analysis_id,
        )
        return PACKET_GRANT_MISSING_SOURCE
    history_ready = _ensure_packet_history_row(
        session_analysis_id,
        session_user_id,
        tax_year,
    )
    if history_ready is None:
        return None
    if not history_ready:
        saved_entitlement = save_packet_entitlement(
            session_analysis_id,
            session_user_id,
            tax_year,
            session_id,
        )
        if saved_entitlement is None:
            return None
        return PACKET_GRANT_MISSING_SOURCE
    entitlement_saved = mark_packet_snapshot_paid(
        session_analysis_id,
        session_user_id,
        tax_year,
        session_id,
    )
    if entitlement_saved is None:
        return None
    if not entitlement_saved:
        # The snapshot row disappeared (history delete, expiry). Do not INSERT
        # a replacement private document; keep the purchase as a receipt.
        latest_snapshot, latest_ok = get_packet_snapshot(
            session_analysis_id,
            session_user_id,
        )
        if not latest_ok:
            return None
        history_row, history_ok = lookup_analysis_for_entitlement(
            session_analysis_id,
            session_user_id,
        )
        if not history_ok:
            return None
        if not latest_snapshot or not history_row:
            saved_entitlement = save_packet_entitlement(
                session_analysis_id,
                session_user_id,
                tax_year,
                session_id,
            )
            if saved_entitlement is None:
                return None
            return PACKET_GRANT_MISSING_SOURCE
        repaired = save_packet_snapshot(
            session_analysis_id,
            session_user_id,
            tax_year,
            packet_payload,
            session_id=session_id,
            paid=True,
        )
        if not repaired:
            return None
    if not save_packet_entitlement(
        session_analysis_id,
        session_user_id,
        tax_year,
        session_id,
    ):
        return None
    # The independent entitlement/snapshot row is authoritative. History flags
    # help legacy clients but a deleted row or history outage cannot revoke a
    # settled purchase.
    history_patched = patch_analysis_result(
        session_analysis_id,
        session_user_id,
        {"packet_unlocked": True, "packet_session_id": session_id},
    )
    if history_patched is not True:
        logger.info("Packet entitlement saved without a history row: %s", session_analysis_id)
    mark_paid(
        session_analysis_id,
        session_id,
        user_id=session_user_id,
    )
    return True


def _grant_packet_from_session(session, analysis_id: str, user_id: str = "") -> bool:
    granted = _persist_packet_grant(session, analysis_id, user_id)
    if granted is None:
        raise HTTPException(
            status_code=503,
            detail="Payment was verified but could not be saved. Please retry.",
        )
    if granted == PACKET_GRANT_MISSING_SOURCE:
        raise HTTPException(status_code=409, detail=PACKET_MISSING_SOURCE_DETAIL)
    return granted


def _analysis_with_history_suggestions(
    analysis_id: str,
    user_id: str,
    analysis: Optional[dict],
) -> Optional[dict]:
    """Fill harvest suggestions from saved analysis when compact JSON omits them."""
    if analysis and analysis.get("suggestions"):
        return analysis
    if not analysis_id or not user_id:
        return analysis
    record = get_analysis_by_id(analysis_id, user_id)
    if not record:
        record = get_analysis_by_result_analysis_id(analysis_id, user_id)
    result = record.get("result") if isinstance(record, dict) else None
    if not isinstance(result, dict) or not result.get("suggestions"):
        return analysis
    merged = dict(analysis or {})
    merged["suggestions"] = result["suggestions"]
    return merged


def _packet_result_tax_year(result: Optional[dict]) -> Optional[int]:
    if not isinstance(result, dict):
        return None
    year = result.get("analysis_tax_year")
    profile = result.get("tax_profile")
    if year is None and isinstance(profile, dict):
        year = profile.get("tax_year")
    try:
        return int(year) if year is not None else None
    except (TypeError, ValueError):
        return None


def _ensure_packet_history_row(
    analysis_id: str,
    user_id: str,
    tax_year: int,
) -> Optional[bool]:
    """Create a deletable, redacted history stub without trusting checkout JSON.

    True when a row is present, False when it cannot be created, None on outage.
    """
    record, lookup_succeeded = lookup_analysis_for_entitlement(analysis_id, user_id)
    if not lookup_succeeded:
        return None
    if record:
        return True
    safe_result = {
        "analysis_id": analysis_id,
        "analysis_tax_year": int(tax_year),
        "tax_profile": {"tax_year": int(tax_year)},
        "packet_unlocked": False,
        "packet_session_id": None,
    }
    try:
        saved = save_analysis_history(
            user_id,
            "year-close-packet.csv",
            {},
            result_data=safe_result,
        )
    except HistoryInsertConflict:
        raced, race_ok = lookup_analysis_for_entitlement(analysis_id, user_id)
        if not race_ok:
            return None
        return bool(raced)
    return True if saved else False


def _is_persisted_same_year_packet_grant(
    analysis_id: str,
    session_id: str,
    user_id: str,
    session,
) -> Optional[bool]:
    """Accept a reused Checkout only when owner-scoped rows prove same-year entitlement."""
    source_analysis_id = packet_analysis_id_from_session(session)
    if (
        not session_id
        or not user_id
        or packet_session_id(session) != session_id
        or packet_user_id_from_session(session) != user_id
        or not source_analysis_id
        or source_analysis_id == "local-analysis"
        or not session_is_settled_packet(session)
    ):
        return False

    metadata = _session_metadata(session)
    session_year = _packet_result_tax_year(
        {"analysis_tax_year": metadata.get("tax_year")}
    )
    if session_year is None:
        source_snapshot, source_snapshot_lookup_succeeded = get_packet_snapshot(
            source_analysis_id,
            user_id,
        )
        if not source_snapshot_lookup_succeeded:
            return None
        if isinstance(source_snapshot, dict):
            session_year = _packet_result_tax_year(
                {"analysis_tax_year": source_snapshot.get("tax_year")}
            )
        if session_year is None:
            source, source_lookup_succeeded = lookup_analysis_for_entitlement(
                source_analysis_id,
                user_id,
            )
            if not source_lookup_succeeded:
                return None
            source_result = source.get("result") if isinstance(source, dict) else None
            session_year = _packet_result_tax_year(source_result)
    if session_year is None:
        return False

    packet_snapshot, snapshot_lookup_succeeded = get_packet_snapshot(
        analysis_id,
        user_id,
    )
    if not snapshot_lookup_succeeded:
        return None
    if isinstance(packet_snapshot, dict):
        target_year = packet_snapshot.get("tax_year")
        try:
            target_year = int(target_year)
        except (TypeError, ValueError):
            target_year = None
        if (
            packet_snapshot.get("paid_at")
            and packet_snapshot.get("packet_session_id") == session_id
            and target_year == session_year
            and isinstance(packet_snapshot.get("packet_payload"), dict)
        ):
            return True
        return False

    # History JSON is user-writable in older installations. Only the private,
    # owner-scoped packet snapshot can prove a reused same-year grant.
    return False


def _payload_for_download(analysis_id: str, user_id: str, analysis: Optional[dict]):
    if isinstance(analysis, dict):
        supplied_id = str(analysis.get("analysis_id") or "").strip()
        if supplied_id and supplied_id != "local-analysis" and supplied_id != analysis_id:
            raise HTTPException(
                status_code=400,
                detail="The analysis ID does not match the paid packet.",
            )
    snapshot, lookup_succeeded = get_packet_snapshot(analysis_id, user_id)
    if not lookup_succeeded:
        raise HTTPException(
            status_code=503,
            detail="Could not load the saved packet snapshot. Please retry.",
        )
    if isinstance(snapshot, dict):
        if isinstance(snapshot.get("packet_payload"), dict):
            return snapshot["packet_payload"]
        raise HTTPException(
            status_code=503,
            detail=(
                "The private packet snapshot is unavailable. Re-run this analysis "
                "to restore it, then download again."
            ),
        )
    if packet_store_belongs_to_user(analysis_id, user_id):
        stored = get_payload(analysis_id)
        if stored:
            return stored
    raise HTTPException(
        status_code=503,
        detail=(
            "The private packet snapshot is unavailable. Re-run the analysis "
            "to restore it, then download again."
        ),
    )


@app.post(
    "/api/year-close-packet/checkout",
    responses={
        400: {"description": "Missing analysis_id"},
        502: {"description": "Stripe checkout session creation failed"},
        503: {"description": "Stripe TEST keys required or Stripe is not configured"},
    },
)
async def create_year_close_packet_checkout(
    body: PacketCheckoutRequest,
    user_id: Annotated[str, Depends(get_current_user)],
):
    """Create a NEW Stripe Checkout Session for the $49 Year-close packet.

    Does not reuse /api/tips/checkout or TipJar price IDs.
    Staging / local always uses Stripe TEST keys (never live).
    """
    requested_analysis_id = (body.analysis_id or "").strip()
    if not requested_analysis_id:
        raise HTTPException(status_code=400, detail="analysis_id is required")

    analysis = body.analysis
    guest_packet_payload = None
    if requested_analysis_id == "local-analysis":
        if not isinstance(analysis, dict):
            raise HTTPException(
                status_code=400,
                detail="This analysis has no stable ID. Reload it before starting checkout.",
            )
        analysis_id = str(uuid.uuid4())
        if not copy_packet_payload_to_id(
            requested_analysis_id,
            analysis_id,
            user_id,
            analysis,
        ):
            raise HTTPException(
                status_code=409,
                detail="This analysis has no owner-scoped private snapshot. Re-run it before checkout.",
            )
        analysis = {**analysis, "analysis_id": analysis_id}
    else:
        record, lookup_succeeded = lookup_analysis_for_entitlement(
            requested_analysis_id,
            user_id,
        )
        if not lookup_succeeded:
            raise HTTPException(
                status_code=503,
                detail="Could not verify this analysis before checkout. Please retry.",
            )
        analysis_id = requested_analysis_id
        if record:
            result = record.get("result")
            if isinstance(result, dict):
                stored_analysis_id = str(result.get("analysis_id") or "").strip()
                if stored_analysis_id and stored_analysis_id != analysis_id:
                    raise HTTPException(
                        status_code=400,
                        detail="The analysis ID does not match the saved analysis.",
                    )
                if analysis is None:
                    analysis = result
        elif not isinstance(analysis, dict) or analysis.get("analysis_id") != analysis_id:
            raise HTTPException(
                status_code=400,
                detail="This analysis has no matching stable ID. Reload it before checkout.",
            )
        if not isinstance(analysis, dict):
            analysis = {"analysis_id": analysis_id}
        elif not analysis.get("analysis_id"):
            analysis = {**analysis, "analysis_id": analysis_id}
        elif analysis.get("analysis_id") != analysis_id:
            raise HTTPException(
                status_code=400,
                detail="The analysis ID does not match the checkout request.",
            )
        owner = packet_store_owner(analysis_id)
        if owner == "":
            # Blank guest rows are not owned by this caller. Adopt only after
            # an exact payload match, and only once checkout commits below.
            guest_packet_payload = claimable_guest_packet_payload(
                analysis_id,
                user_id,
                analysis,
            )
            if guest_packet_payload is None:
                raise HTTPException(
                    status_code=409,
                    detail="This guest snapshot must be restored from its matching analysis before checkout.",
                )
        elif not packet_store_belongs_to_user(analysis_id, user_id):
            raise HTTPException(
                status_code=409,
                detail="This analysis snapshot belongs to another user.",
            )

    analysis = _analysis_with_history_suggestions(analysis_id, user_id, analysis)
    packet_api_key = _configure_packet_stripe()
    durable_snapshot, snapshot_lookup_succeeded = get_packet_snapshot(
        analysis_id,
        user_id,
    )
    if not snapshot_lookup_succeeded:
        raise HTTPException(
            status_code=503,
            detail="Could not load the saved packet snapshot. Please retry.",
        )
    if (
        isinstance(durable_snapshot, dict)
        and durable_snapshot.get("paid_at")
        and not isinstance(durable_snapshot.get("packet_payload"), dict)
    ):
        # A cleared paid row is a deleted source. Do not rebuild it from
        # PACKET_STORE or the guest body and report already_paid.
        raise HTTPException(status_code=409, detail=PACKET_MISSING_SOURCE_DETAIL)
    packet_snapshot = (
        durable_snapshot.get("packet_payload")
        if isinstance(durable_snapshot, dict)
        else None
    )
    if not isinstance(packet_snapshot, dict) and guest_packet_payload is not None:
        packet_snapshot = guest_packet_payload
    if not isinstance(packet_snapshot, dict) and packet_store_belongs_to_user(
        analysis_id, user_id
    ):
        packet_snapshot = get_payload(analysis_id)
    tax_year = (
        _packet_result_tax_year(packet_snapshot)
        if isinstance(packet_snapshot, dict)
        else None
    )
    if tax_year is None and isinstance(durable_snapshot, dict):
        try:
            tax_year = int(durable_snapshot.get("tax_year"))
        except (TypeError, ValueError):
            tax_year = None
    if tax_year is None:
        raise HTTPException(
            status_code=409,
            detail="The private packet snapshot has expired. Re-run this analysis before checkout.",
        )
    if not _ensure_packet_history_row(
        analysis_id,
        user_id,
        tax_year,
    ):
        raise HTTPException(
            status_code=503,
            detail="Could not save this analysis before checkout. Please retry.",
        )
    existing_session_id, grant_lookup_succeeded = lookup_packet_grant_for_tax_year(
        user_id,
        tax_year,
    )
    if not grant_lookup_succeeded:
        raise HTTPException(
            status_code=503,
            detail="Could not verify existing packet access. Please retry.",
        )
    if not existing_session_id:
        entitlement, entitlement_lookup_succeeded = lookup_packet_entitlement_for_tax_year(
            user_id,
            tax_year,
        )
        if not entitlement_lookup_succeeded:
            raise HTTPException(
                status_code=503,
                detail="Could not verify existing packet access. Please retry.",
            )
        if entitlement:
            try:
                prior_session = stripe.checkout.Session.retrieve(
                    entitlement["packet_session_id"],
                    api_key=packet_api_key,
                )
            except stripe.StripeError as e:
                logger.error("Year-close packet entitlement retrieve error: %s", e)
                raise HTTPException(
                    status_code=503,
                    detail="Could not verify the prior packet payment. Please retry.",
                )
            prior_metadata = _session_metadata(prior_session)
            try:
                prior_tax_year = int(prior_metadata.get("tax_year"))
            except (TypeError, ValueError):
                prior_tax_year = None
            if (
                not session_is_settled_packet(prior_session)
                or packet_session_id(prior_session) != entitlement["packet_session_id"]
                or packet_user_id_from_session(prior_session) != user_id
                or prior_tax_year != tax_year
                or packet_analysis_id_from_session(prior_session)
                != entitlement.get("analysis_id")
            ):
                raise HTTPException(
                    status_code=503,
                    detail="The prior packet payment could not be verified for this tax year.",
                )
            existing_session_id = entitlement["packet_session_id"]
    if existing_session_id:
        if not isinstance(packet_snapshot, dict):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Payment was received, but this analysis has no source document. "
                    "Run a new analysis for this tax year."
                ),
            )
        saved = save_packet_snapshot(
            analysis_id,
            user_id,
            tax_year,
            packet_snapshot,
            session_id=existing_session_id,
            paid=True,
        )
        if not saved:
            raise HTTPException(
                status_code=503,
                detail="Could not save the packet snapshot. Please retry.",
            )
        if not save_packet_entitlement(
            analysis_id,
            user_id,
            tax_year,
            existing_session_id,
        ):
            raise HTTPException(
                status_code=503,
                detail="Could not save the packet entitlement. Please retry.",
            )
        if guest_packet_payload is not None and not adopt_guest_packet(
            analysis_id, user_id, guest_packet_payload
        ):
            raise HTTPException(
                status_code=503,
                detail="Could not save the packet snapshot. Please retry.",
            )
        upsert_packet_payload(analysis_id, user_id, packet_snapshot)
        mark_paid(analysis_id, existing_session_id, user_id=user_id)
        try:
            patch_analysis_result(
                analysis_id,
                user_id,
                {"packet_unlocked": True, "packet_session_id": existing_session_id},
            )
        except Exception as exc:
            logger.warning("Could not update packet-unlocked history flags: %s", exc)
        return {
            "already_paid": True,
            "session_id": existing_session_id,
            "analysis_id": analysis_id,
            "product": PACKET_PRODUCT_NAME,
            "amount": PACKET_AMOUNT_CENTS,
        }
    if not isinstance(packet_snapshot, dict):
        raise HTTPException(
            status_code=409,
            detail="The private packet snapshot has expired. Re-run this analysis before checkout.",
        )
    if not save_packet_snapshot(
        analysis_id,
        user_id,
        tax_year,
        packet_snapshot,
    ):
        raise HTTPException(
            status_code=503,
            detail="Could not securely save the packet before checkout. Please retry.",
        )

    success_url = (
        f"{FRONTEND_URL}/dashboard?packet_session={{CHECKOUT_SESSION_ID}}"
        f"&packet_analysis={quote(str(analysis_id), safe='')}"
    )
    cancel_url = f"{FRONTEND_URL}/dashboard?packet_canceled=1"
    # Idempotency key is the primary double-charge guard. Stripe returns the
    # original Checkout Session when this user, analysis, and tax year are
    # replayed. Packet calls pass api_key= and never assign stripe.api_key.
    idempotency_key = _packet_checkout_idempotency_key(
        user_id,
        analysis_id,
        int(tax_year),
    )

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=packet_checkout_line_items(),
            custom_text=packet_checkout_custom_text(),
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "product": PACKET_METADATA_PRODUCT,
                "analysis_id": analysis_id,
                "user_id": user_id,
                "tax_year": str(tax_year),
            },
            idempotency_key=idempotency_key,
            api_key=packet_api_key,
        )
        if guest_packet_payload is not None and not adopt_guest_packet(
            analysis_id, user_id, guest_packet_payload
        ):
            raise HTTPException(
                status_code=503,
                detail="Could not save the packet snapshot. Please retry.",
            )
        upsert_packet_payload(analysis_id, user_id, packet_snapshot)
        return {
            "checkout_url": session.url,
            "session_id": session.id,
            "analysis_id": analysis_id,
            "product": PACKET_PRODUCT_NAME,
            "amount": PACKET_AMOUNT_CENTS,
            "stripe_mode": "test" if packet_requires_test_stripe() else "live",
        }
    except stripe.StripeError as e:
        logger.error(f"Year-close packet checkout error: {e}")
        raise HTTPException(status_code=502, detail="Failed to create checkout session")


@app.post("/api/year-close-packet/confirm")
async def confirm_year_close_packet(
    body: PacketConfirmRequest,
    user_id: Annotated[str, Depends(get_current_user)],
):
    """Unlock download after Stripe Checkout returns to staging (session_id)."""
    session_id = (body.session_id or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    packet_api_key = _configure_packet_stripe()

    try:
        session = stripe.checkout.Session.retrieve(session_id, api_key=packet_api_key)
    except stripe.StripeError as e:
        logger.error(f"Year-close packet session retrieve error: {e}")
        raise HTTPException(status_code=502, detail="Failed to verify checkout session")

    metadata = _session_metadata(session)
    if metadata.get("product") != PACKET_METADATA_PRODUCT:
        raise HTTPException(status_code=403, detail="Checkout session is not a year-close packet.")

    # Checkout metadata is canonical. Client IDs may confirm that they are
    # returning to the same run, but can never supply a missing server ID.
    analysis_id = packet_analysis_id_from_session(session)
    if not analysis_id:
        raise HTTPException(status_code=400, detail="analysis_id is required")
    if analysis_id == "local-analysis" or packet_user_id_from_session(session) != user_id:
        raise HTTPException(
            status_code=403,
            detail="Checkout session does not belong to this analysis.",
        )
    for supplied_id in (body.packet_analysis, body.analysis_id):
        if supplied_id and supplied_id != "local-analysis" and supplied_id != analysis_id:
            raise HTTPException(
                status_code=400,
                detail="The checkout session does not match this analysis.",
            )

    status_value = (
        session.get("status")
        if isinstance(session, dict)
        else getattr(session, "status", None)
    )
    payment_status_value = (
        session.get("payment_status")
        if isinstance(session, dict)
        else getattr(session, "payment_status", None)
    )
    status = str(status_value or "").lower()
    payment_status = str(payment_status_value or "").lower()
    if status == "expired":
        raise HTTPException(
            status_code=410,
            detail="This checkout session expired. Start a new checkout.",
        )
    if status == "open" or (status == "complete" and payment_status != "paid"):
        raise HTTPException(
            status_code=409,
            detail="This checkout session is still open. Resume it to finish payment.",
        )

    if isinstance(body.analysis, dict):
        supplied_id = str(body.analysis.get("analysis_id") or "").strip()
        if supplied_id and supplied_id != analysis_id:
            raise HTTPException(
                status_code=400,
                detail="The analysis ID does not match the paid packet.",
            )
        if packet_store_owner(analysis_id) == "":
            guest_payload = claimable_guest_packet_payload(
                analysis_id,
                user_id,
                body.analysis,
            )
            if guest_payload is not None:
                adopt_guest_packet(analysis_id, user_id, guest_payload)

    if not _grant_packet_from_session(session, analysis_id, user_id=user_id):
        logger.info(
            "year-close packet confirm 403 session_present=1 analysis_id=%s",
            analysis_id,
        )
        raise HTTPException(
            status_code=403,
            detail="Checkout session does not unlock the year-close packet.",
        )
    return {
        "paid": True,
        "product": PACKET_PRODUCT_NAME,
        "analysis_id": analysis_id,
    }


@app.post("/api/year-close-packet/webhook")
async def year_close_packet_webhook(request: Request):
    """Grant packet access only from a verified, settled Stripe Checkout event."""
    payload = await request.body()
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET") or ""
    sig = request.headers.get("stripe-signature")

    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET is not configured")
        raise HTTPException(status_code=503, detail="Stripe webhook is not configured")
    if not sig:
        raise HTTPException(status_code=400, detail="Stripe-Signature header is required")
    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", "")
    data = event.get("data") if isinstance(event, dict) else getattr(event, "data", None)
    session_obj = (data or {}).get("object") if isinstance(data, dict) else getattr(data, "object", None)

    if event_type not in (
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    ):
        return {"received": True, "granted": False}

    analysis_id = packet_analysis_id_from_session(session_obj)
    if not analysis_id:
        return {"received": True, "granted": False}

    granted = _persist_packet_grant(session_obj, analysis_id, packet_user_id_from_session(session_obj))
    if granted is None:
        raise HTTPException(
            status_code=500,
            detail="Unable to persist packet payment; Stripe may retry this event.",
        )
    return {"received": True, "granted": granted is True}


def _authorized_packet_download(
    analysis_id: str,
    session_id: Optional[str],
    user_id: str,
) -> str:
    if analysis_id == "local-analysis":
        raise HTTPException(
            status_code=400,
            detail="Re-run this analysis to download its packet.",
        )
    snapshot, lookup_succeeded = get_packet_snapshot(analysis_id, user_id)
    if not lookup_succeeded:
        raise HTTPException(
            status_code=503,
            detail="Could not verify the saved packet entitlement. Please retry.",
        )
    if isinstance(snapshot, dict):
        if not isinstance(snapshot.get("packet_payload"), dict):
            if snapshot.get("paid_at"):
                raise HTTPException(status_code=409, detail=PACKET_MISSING_SOURCE_DETAIL)
        stored_session_id = snapshot.get("packet_session_id")
        if snapshot.get("paid_at") and stored_session_id:
            if session_id and session_id != stored_session_id:
                raise HTTPException(
                    status_code=403,
                    detail="Year-close packet download requires payment.",
                )
            if (
                not session_id
                and isinstance(snapshot.get("packet_payload"), dict)
            ):
                # No conflicting session was offered. The owner-scoped paid
                # snapshot is enough during Stripe downtime.
                return analysis_id
            try:
                packet_api_key = _configure_packet_stripe()
                settled_session = stripe.checkout.Session.retrieve(
                    stored_session_id,
                    api_key=packet_api_key,
                )
            except (stripe.StripeError, HTTPException):
                # The owner-scoped durable grant is enough during Stripe
                # downtime when the caller echoed the stored session.
                if isinstance(snapshot.get("packet_payload"), dict):
                    return analysis_id
                raise HTTPException(
                    status_code=403,
                    detail="Year-close packet download requires payment.",
                )
            metadata = _session_metadata(settled_session)
            try:
                settled_tax_year = int(metadata.get("tax_year"))
            except (TypeError, ValueError):
                settled_tax_year = None
            if (
                packet_session_id(settled_session) != stored_session_id
                or not session_is_settled_packet(settled_session)
                or packet_user_id_from_session(settled_session) != user_id
                or metadata.get("product") != PACKET_METADATA_PRODUCT
                or (
                    settled_tax_year is not None
                    and settled_tax_year != int(snapshot.get("tax_year"))
                )
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Year-close packet download requires payment.",
                )
            return analysis_id
    elif is_packet_paid(analysis_id, user_id=user_id):
        # Legacy in-memory grants are usable only when no durable row exists.
        # A durable row with a cleared payload above always wins over cache.
        return analysis_id
    if not session_id:
        raise HTTPException(
            status_code=403,
            detail="Year-close packet download requires payment.",
        )
    packet_api_key = _configure_packet_stripe()
    try:
        session = stripe.checkout.Session.retrieve(session_id, api_key=packet_api_key)
    except stripe.StripeError:
        if (
            isinstance(snapshot, dict)
            and snapshot.get("paid_at")
            and isinstance(snapshot.get("packet_payload"), dict)
        ):
            raise HTTPException(
                status_code=503,
                detail="Could not verify the supplied checkout session. Please retry.",
            )
        raise HTTPException(status_code=403, detail="Year-close packet download requires payment.")
    if _grant_packet_from_session(session, analysis_id, user_id=user_id):
        return packet_analysis_id_from_session(session)
    if not packet_store_belongs_to_user(analysis_id, user_id):
        raise HTTPException(
            status_code=403,
            detail="Year-close packet download requires payment.",
        )
    same_year_grant = _is_persisted_same_year_packet_grant(
        analysis_id,
        session_id,
        user_id,
        session,
    )
    if same_year_grant is None:
        raise HTTPException(
            status_code=503,
            detail="Could not verify the saved packet entitlement. Please retry.",
        )
    if same_year_grant and packet_store_belongs_to_user(analysis_id, user_id):
        if mark_paid(analysis_id, session_id, user_id=user_id):
            return analysis_id
    raise HTTPException(
        status_code=403,
        detail="Year-close packet download requires payment.",
    )


@app.get("/api/year-close-packet/download")
async def download_year_close_packet_get(
    analysis_id: Annotated[str, Query()],
    user_id: Annotated[str, Depends(get_current_user)],
    session_id: Annotated[Optional[str], Query()] = None,
):
    """Download the paid packet PDF. Unpaid requests are 403."""
    analysis_id = (analysis_id or "").strip()
    if not analysis_id:
        raise HTTPException(status_code=400, detail="analysis_id is required")
    analysis_id = _authorized_packet_download(analysis_id, session_id, user_id)
    payload = _payload_for_download(analysis_id, user_id, None)
    pdf_bytes = render_packet_pdf(payload)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="year-close-packet.pdf"'
        },
    )


@app.post("/api/year-close-packet/download")
async def download_year_close_packet_post(
    body: PacketDownloadRequest,
    user_id: Annotated[str, Depends(get_current_user)],
):
    """Download the paid packet PDF, rebuilding from analysis JSON if needed."""
    analysis_id = (body.analysis_id or "").strip()
    if not analysis_id:
        raise HTTPException(status_code=400, detail="analysis_id is required")
    analysis_id = _authorized_packet_download(analysis_id, body.session_id, user_id)
    payload = _payload_for_download(analysis_id, user_id, body.analysis)
    pdf_bytes = render_packet_pdf(payload)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="year-close-packet.pdf"'
        },
    )


@app.post("/push/subscribe")
async def subscribe_to_push(
    subscription: PushSubscription,
    user_id: Annotated[str, Depends(get_current_user)],
):
    """Store a push endpoint for the authenticated user only."""
    result = db_save_push_subscription(user_id, subscription.model_dump())
    if result is None:
        raise HTTPException(status_code=503, detail="Could not save push subscription.")
    if result is False:
        raise HTTPException(
            status_code=409,
            detail="This push endpoint is already registered to another account.",
        )
    return {"message": "Subscription stored"}

@app.post("/push/unsubscribe")
async def unsubscribe_from_push(
    subscription: PushSubscription,
    user_id: Annotated[str, Depends(get_current_user)],
):
    """Remove only the authenticated user's matching push endpoint."""
    row, lookup_succeeded = db_get_push_subscription_for_user(
        user_id,
        subscription.endpoint,
    )
    if not lookup_succeeded:
        raise HTTPException(status_code=503, detail="Could not check push subscription.")
    if row is None:
        return {"message": "Subscription not found"}
    deleted = db_delete_push_subscription(row.get("id"), user_id=user_id)
    if deleted is None:
        raise HTTPException(status_code=503, detail="Could not remove push subscription.")
    return {"message": "Subscription removed" if deleted else "Subscription not found"}

@app.get("/push/subscriptions")
async def get_subscriptions(user_id: Annotated[str, Depends(get_current_user)]):
    """Return owner-scoped subscription metadata without exposing endpoints or keys."""
    subscriptions = db_list_push_subscriptions(user_id)
    if subscriptions is None:
        raise HTTPException(status_code=503, detail="Could not load push subscriptions.")
    return {
        "count": len(subscriptions),
        "subscriptions": [
            {
                "id": row.get("id"),
                "created_at": row.get("created_at"),
                "expiration_time_ms": row.get("expiration_time_ms"),
            }
            for row in subscriptions
        ],
    }


def _deliver_push_notification(
    notification: PushNotification,
    subscriptions: list[dict],
) -> dict:
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=503, detail="VAPID keys are not configured.")

    notification_data = {
        "title": notification.title,
        "body": notification.body,
        "icon": notification.icon,
        "badge": notification.badge,
        "tag": notification.tag,
        "data": notification.data,
    }
    sent_count = 0
    failed_count = 0
    for row in subscriptions:
        subscription_info = {
            "endpoint": row.get("endpoint"),
            "keys": row.get("keys"),
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=json.dumps(notification_data),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{VAPID_CLAIM_EMAIL}"},
            )
            sent_count += 1
        except Exception as e:
            failed_count += 1
            logger.error("Push notification failed: %s", e)
            response = getattr(e, "response", None)
            if (
                isinstance(e, WebPushException)
                and response
                and response.status_code == 410
                and row.get("id")
            ):
                db_delete_push_subscription(row["id"], user_id=row.get("user_id"))

    return {
        "message": f"Notification sent to {sent_count} subscribers",
        "sent": sent_count,
        "failed": failed_count,
        "total_subscriptions": len(subscriptions),
    }

@app.post("/push/send")
async def send_push_notification(notification: PushNotification, request: Request):
    """Broadcast only with an explicitly configured server-side admin key."""
    admin_key = os.environ.get("PUSH_ADMIN_KEY", "")
    provided_key = request.headers.get("X-Push-Admin-Key", "")
    if not admin_key:
        raise HTTPException(status_code=503, detail="Push broadcast is not configured.")
    if not provided_key or not hmac.compare_digest(provided_key, admin_key):
        raise HTTPException(status_code=403, detail="Push broadcast is not authorized.")
    subscriptions = db_list_push_subscriptions()
    if subscriptions is None:
        raise HTTPException(status_code=503, detail="Could not load push subscriptions.")
    return _deliver_push_notification(notification, subscriptions)

@app.post("/push/test")
async def test_push_notification(
    body: PushTestRequest,
    user_id: Annotated[str, Depends(get_current_user)],
):
    """Send a test notification to one endpoint owned by the requesting user."""
    subscription, lookup_succeeded = db_get_push_subscription_for_user(
        user_id,
        body.endpoint,
    )
    if not lookup_succeeded:
        raise HTTPException(status_code=503, detail="Could not verify push subscription.")
    if subscription is None:
        raise HTTPException(status_code=404, detail="Push subscription not found.")
    notification = PushNotification(
        title="Test Notification",
        body="This is a test notification from OptionsTaxHub!",
        tag="test"
    )
    return _deliver_push_notification(notification, [subscription])

def run():
    # Local default is 8011. Render injects $PORT — do not hardcode 8011 in production.
    port = int(os.environ.get("PORT", 8011))
    host = os.environ.get("HOST", "0.0.0.0")  # Bind to all interfaces for Render and other platforms
    # Only enable auto-reload in local development; never in production (breaks container envs)
    is_dev = os.environ.get("ENVIRONMENT", "production").lower() == "development"
    import uvicorn
    uvicorn.run("main:app", host=host, port=port, reload=is_dev)

if __name__ == "__main__":
    run()
