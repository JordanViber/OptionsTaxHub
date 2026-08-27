import os
import logging
import re
from collections import defaultdict
from pathlib import Path
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

from auth import get_current_user, enforce_ownership
from models import (
    AssetType,
    FilingStatus,
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
    build_packet_payload,
    get_payload,
    is_packet_paid,
    mark_paid,
    packet_analysis_id_from_session,
    packet_checkout_line_items,
    packet_requires_test_stripe,
    packet_session_id,
    remember_analysis,
    render_packet_pdf,
    resolve_packet_stripe_secret_key,
    session_grants_packet,
    upsert_payload,
)
from csv_parser import parse_csv, RealizedEvent
from tax_engine import get_tax_brackets_summary
from harvesting import (
    compute_lot_metrics,
    aggregate_positions,
    generate_suggestions,
    build_portfolio_summary,
    suppress_fractional_residual_positions,
)
from wash_sale import detect_wash_sales, adjust_lots_for_wash_sales
from price_service import fetch_current_prices, fetch_option_prices
from ai_advisor import get_ai_suggestions, prepare_positions_for_ai
from pdf_1099_parser import parse_robinhood_1099_pdf
from db import (
    save_analysis_history,
    get_analysis_history,
    get_analysis_by_id,
    delete_analyses_without_result,
    delete_analysis_by_id,
    save_tax_profile as db_save_tax_profile,
    get_tax_profile as db_get_tax_profile,
    get_supabase,
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
    # Build a set of allowed origins that covers both the bare domain and the
    # www. subdomain prefix, so a www-redirect in production doesn't break CORS.
    _allowed_origins: list[str] = [FRONTEND_URL]
    try:
        from urllib.parse import urlparse as _urlparse
        _parsed = _urlparse(FRONTEND_URL)
        _host = _parsed.hostname or ""
        if _host.startswith("www."):
            # e.g. https://www.optionstaxhub.com -> also allow https://optionstaxhub.com
            _bare = f"{_parsed.scheme}://{_host[4:]}"
            if _parsed.port:
                _bare += f":{_parsed.port}"
            _allowed_origins.append(_bare)
        else:
            # e.g. https://optionstaxhub.com -> also allow https://www.optionstaxhub.com
            _www = f"{_parsed.scheme}://www.{_host}"
            if _parsed.port:
                _www += f":{_parsed.port}"
            _allowed_origins.append(_www)
    except Exception:
        pass  # If URL parsing fails, fall back to the single origin

    app.add_middleware(  # NOSONAR python:S8414
        CORSMiddleware,
        allow_origins=_allowed_origins,
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
    expected_previous_year: int,
) -> tuple[Supplemental1099Summary | None, list[str]]:
    """Parse the optional prior-year 1099 PDF and return any user-facing warnings."""
    if supplemental_1099 is None:
        return None, []

    if not _is_pdf_upload(supplemental_1099):
        return None, ["Supplemental 1099 must be a PDF file (received unsupported content type)."]

    try:
        supplemental_bytes = await supplemental_1099.read(_MAX_SUPPLEMENTAL_PDF_BYTES + 1)
        if len(supplemental_bytes) > _MAX_SUPPLEMENTAL_PDF_BYTES:
            return None, ["Supplemental 1099 PDF exceeds the 20 MB size limit and was ignored."]
        summary = _parse_supplemental_1099_summary(
            supplemental_bytes,
            supplemental_1099.filename or "prior-year-1099.pdf",
            current_symbols,
            expected_previous_year,
        )
    except Exception as exc:
        logger.warning("Failed to parse supplemental 1099 PDF: %s", exc, exc_info=True)
        return None, ["Supplemental 1099 PDF could not be parsed and was ignored for this analysis."]

    if _is_empty_supplemental_summary(summary):
        return None, ["Supplemental 1099 PDF could not be parsed and was ignored for this analysis."]

    warnings: list[str] = []
    if summary.tax_year is not None and summary.tax_year != expected_previous_year:
        warnings.append(
            "The supplemental 1099 PDF was parsed successfully, but its tax year does not match the expected prior year for this analysis."
        )

    return summary, warnings


def _apply_live_prices_to_tax_lots(tax_lots: list, all_warnings: list[str]) -> list:
    """Populate stock and option lots with live prices when available."""
    symbols = list({lot.symbol for lot in tax_lots if lot.asset_type == AssetType.STOCK})
    fallback_prices = {
        lot.symbol: lot.current_price
        for lot in tax_lots
        if lot.asset_type == AssetType.STOCK and lot.current_price is not None
    }
    live_prices, price_warnings = fetch_current_prices(symbols, fallback_prices)
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
    option_prices, option_price_warnings = fetch_option_prices(
        option_labels,
        option_fallback_prices,
    )
    all_warnings.extend(option_price_warnings)

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


@app.post(
    "/api/portfolio/analyze",
    response_model=PortfolioAnalysis,
    responses={400: {"description": "Invalid user ID format or unparseable CSV"}},
)
async def analyze_portfolio(
    file: Annotated[UploadFile, File()],
    supplemental_1099: Annotated[Optional[UploadFile], File()] = None,
    filing_status: Annotated[Optional[str], Query()] = "single",
    estimated_income: Annotated[Optional[str], Query()] = None,
    tax_year: Annotated[Optional[str], Query()] = None,
    user_id: Annotated[str, Depends(get_current_user)] = "",
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

    contents = await file.read()
    try:
        return await _run_portfolio_analysis(
            contents,
            filename=file.filename or "upload.csv",
            supplemental_1099=supplemental_1099,
            filing_status=filing_status,
            estimated_income=estimated_income,
            tax_year=tax_year,
            user_id=user_id,
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

    tax_profile = _tax_profile_from_query(filing_status, estimated_income, tax_year)

    all_warnings = list(parse_errors)
    supplemental_1099_summary, supplemental_1099_warnings = await _maybe_parse_supplemental_1099(
        supplemental_1099,
        {lot.symbol for lot in tax_lots},
        (tax_profile.tax_year or 2026) - 1,
    )
    all_warnings.extend(supplemental_1099_warnings)

    tax_lots = _apply_live_prices_to_tax_lots(tax_lots, all_warnings)

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

    # Get AI-powered suggestions
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

    analysis_id = str(uuid.uuid4())
    result = PortfolioAnalysis(
        positions=positions,
        tax_lots=tax_lots,
        suggestions=suggestions,
        wash_sale_flags=wash_sale_flags,
        summary=summary,
        tax_profile=tax_profile,
        supplemental_1099=supplemental_1099_summary,
        analysis_id=analysis_id,
        warnings=_summarize_warnings(all_warnings),
    )
    remember_analysis(
        analysis_id,
        user_id,
        result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result),
    )

    # Save analysis to history for authenticated user
    _save_history_best_effort(user_id, filename, summary, result)

    return result


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
    deleted = delete_analysis_by_id(analysis_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Analysis not found")
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


def _configure_packet_stripe() -> str:
    """Set stripe.api_key for the year-close packet. Staging is TEST-only."""
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
    stripe.api_key = key
    return key


def _grant_packet_from_session(session, analysis_id: str, user_id: str = "") -> bool:
    if not session_grants_packet(session, analysis_id):
        return False
    mark_paid(analysis_id, packet_session_id(session), user_id=user_id)
    return True


def _payload_for_download(analysis_id: str, user_id: str, analysis: Optional[dict]):
    rec = upsert_payload(analysis_id, user_id, analysis)
    payload = rec.get("payload") if rec else None
    if payload:
        return payload
    if analysis:
        return build_packet_payload(analysis, analysis_id=analysis_id)
    stored = get_payload(analysis_id)
    if stored:
        return stored
    raise HTTPException(
        status_code=404,
        detail="No year-close packet snapshot found for this analysis.",
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
    analysis_id = (body.analysis_id or "").strip()
    if not analysis_id:
        raise HTTPException(status_code=400, detail="analysis_id is required")

    upsert_payload(analysis_id, user_id, body.analysis)
    _configure_packet_stripe()

    success_url = (
        f"{FRONTEND_URL}/dashboard?packet_session={{CHECKOUT_SESSION_ID}}"
        f"&packet_analysis={analysis_id}"
    )
    cancel_url = f"{FRONTEND_URL}/dashboard?packet_canceled=1"

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=packet_checkout_line_items(),
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "product": PACKET_METADATA_PRODUCT,
                "analysis_id": analysis_id,
                "user_id": user_id,
            },
        )
        return {
            "checkout_url": session.url,
            "session_id": session.id,
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

    _configure_packet_stripe()

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except stripe.StripeError as e:
        logger.error(f"Year-close packet session retrieve error: {e}")
        raise HTTPException(status_code=502, detail="Failed to verify checkout session")

    # Session metadata is canonical. Client analysis_id is optional and may be
    # local-analysis after reload; success URL packet_analysis is a fallback.
    analysis_id = packet_analysis_id_from_session(
        session,
        body.packet_analysis or "",
        body.analysis_id or "",
    )
    if not analysis_id:
        raise HTTPException(status_code=400, detail="analysis_id is required")

    upsert_payload(analysis_id, user_id, body.analysis)

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
    """Mark packet paid on checkout.session.completed. Tips never match product metadata."""
    payload = await request.body()
    event_type = ""
    session_obj = None
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET") or ""
    sig = request.headers.get("stripe-signature")

    if webhook_secret and sig:
        try:
            event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
            event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", "")
            data = event.get("data") if isinstance(event, dict) else getattr(event, "data", None)
            session_obj = (data or {}).get("object") if isinstance(data, dict) else getattr(data, "object", None)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature")
    else:
        try:
            event = json.loads(payload.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid webhook payload")
        event_type = event.get("type") or ""
        session_obj = (event.get("data") or {}).get("object")

    if event_type != "checkout.session.completed":
        return {"received": True, "granted": False}

    analysis_id = packet_analysis_id_from_session(session_obj)
    if not analysis_id:
        return {"received": True, "granted": False}

    granted = session_grants_packet(session_obj, analysis_id)
    if granted:
        mark_paid(analysis_id, packet_session_id(session_obj))
    return {"received": True, "granted": granted}


def _authorized_packet_download(analysis_id: str, session_id: Optional[str], user_id: str) -> None:
    if is_packet_paid(analysis_id):
        return
    if not session_id:
        raise HTTPException(
            status_code=403,
            detail="Year-close packet download requires payment.",
        )
    _configure_packet_stripe()
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except stripe.StripeError:
        raise HTTPException(status_code=403, detail="Year-close packet download requires payment.")
    if not _grant_packet_from_session(session, analysis_id, user_id=user_id):
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
    _authorized_packet_download(analysis_id, session_id, user_id)
    payload = get_payload(analysis_id)
    if not payload:
        raise HTTPException(
            status_code=404,
            detail="No year-close packet snapshot found for this analysis.",
        )
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
    _authorized_packet_download(analysis_id, body.session_id, user_id)
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
    # Local default is 8011. Render injects $PORT — do not hardcode 8011 in production.
    port = int(os.environ.get("PORT", 8011))
    host = os.environ.get("HOST", "0.0.0.0")  # Bind to all interfaces for Render and other platforms
    # Only enable auto-reload in local development; never in production (breaks container envs)
    is_dev = os.environ.get("ENVIRONMENT", "production").lower() == "development"
    import uvicorn
    uvicorn.run("main:app", host=host, port=port, reload=is_dev)

if __name__ == "__main__":
    run()
