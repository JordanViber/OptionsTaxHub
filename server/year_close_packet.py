"""Year-close packet: $49 one-time reconciliation download.

Builds a PDF from analysis JSON already returned by /api/portfolio/analyze.
Does not re-parse 1099 PDFs or rebuild wash-sale lots.

This is a reconciliation packet, not a filed Form 8949. Lot-matched 1099-B
is a worksheet for this run.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime
from io import BytesIO
from typing import Any, Optional

logger = logging.getLogger(__name__)

PACKET_AMOUNT_CENTS = 4900
PACKET_PRODUCT_NAME = "Year-close packet"
PACKET_METADATA_PRODUCT = "year_close_packet"
# Stripe Checkout left-rail copy. Name stays recognizable; description sells
# the download so the $49 page is not a blank "Year-close packet" label.
PACKET_CHECKOUT_NAME = "Year-close packet — ready for your CPA"
PACKET_CHECKOUT_DESCRIPTION = (
    "Walk into tax season with this run already organized: lot-matched 1099-B, "
    "wash-sale flags on replacement lots, harvesting opportunities with estimated "
    "tax savings, and a printable reconciliation you can send your CPA. "
    "One-time $49. Instant PDF when you return. Not a filed Form 8949 — a "
    "working packet for this analysis."
)
PACKET_CHECKOUT_SUBMIT_MESSAGE = (
    "One payment. Your year-close packet unlocks the moment you return to OptionsTaxHub."
)

PACKET_DISCLAIMER = (
    "This is a reconciliation packet, not a filed Form 8949. "
    "Lot-matched 1099-B is a worksheet for this run."
)

SETTLEMENT_DATE_FAQ = (
    "Robinhood 1099 uses settlement date, so a year-end short option "
    "(for example SPX 12/31) can show a gain on the 1099 for a trade that "
    "does not settle until January. Matched lots still show both dates so "
    "the split is visible."
)

HARVEST_TITLE = "Harvest opportunities"
HARVEST_INTRO = (
    "Open lots with unrealized losses and estimated federal tax savings "
    "for this run. Not a filed Form 8949."
)
WASH_EVENTS_TITLE = "Wash-sale events"

LOT_MATCH_TITLE = "Lot-matched 1099-B"
LOT_MATCH_INTRO = (
    "Reconciliation worksheet, not a filed Form 8949. "
    "Matched: paired lots whose 1099 sold date equals the export trade date. "
    "Gap: paired lots with a settlement vs trade-date split (matched_settlement_gap). "
    "Unmatched: 1099_only (on the 1099, not in the export) and csv_only "
    "(in the export, not on the 1099)."
)
LINES_PER_PDF_PAGE = 42
PDF_WRAP_WIDTH = 96

OPTIONS_WASH_SALE_FAQ = (
    "Options and credit-spread wash-sale treatment can differ from the "
    "broker 1099. We show the 1099 wash-sale disallowed figure as reported."
)

COMPARE_TITLE = "1099 vs your export"
COMPARE_INTRO = (
    "Two columns, totals only. Broker 1099 uses settlement date; "
    "this export uses trade date. ST/LT nets include wash-sale disallowed "
    "(1099 definition); wash is also shown separately."
)
UNKNOWN_1099_YEAR_COPY = (
    "1099 tax year could not be determined; shown as a supplement — "
    "not a previous-year mismatch and not a same-year compare."
)
COMPARE_BROKER_HEADER = "Broker 1099 (settlement date)"
COMPARE_EXPORT_HEADER = "This export (trade date)"
COMPARE_GAP_COPY = (
    "These totals often disagree. A year-end short option (for example SPX 12/31) "
    "can print a gain on the 1099 while this export still shows a loss until January "
    "settlement. That is not a software bug. The $49 packet lists matched, gap, and "
    "unmatched lots. An incomplete export also shows up here. Traders on r/options have "
    "reported the same gap -- a Robinhood 1099 showing +$2,699 while the export showed "
    "a $542 loss."
)

# In-memory entitlement + snapshot store. Sufficient for staging accept
# (pay then immediately download) and unit tests. Download can also rebuild
# from a client-supplied packet payload after Stripe session verification.
PACKET_STORE: dict[str, dict[str, Any]] = {}

# Guest analyses are stored under an empty user_id. Without TTL + a cap this
# dict grows without bound on every unauthenticated POST /analyze.
ANON_PACKET_TTL_SECONDS = 60 * 60
ANON_PAID_PACKET_TTL_SECONDS = 24 * 60 * 60
ANON_PACKET_STORE_MAX = 64


def reset_packet_store() -> None:
    """Clear entitlements (tests only)."""
    PACKET_STORE.clear()


def _packet_is_anonymous(rec: dict[str, Any]) -> bool:
    return not rec.get("user_id")


def _packet_created_at(rec: dict[str, Any]) -> float:
    created = rec.get("created_at")
    try:
        return float(created)
    except (TypeError, ValueError):
        return 0.0


def _packet_is_expired(rec: dict[str, Any], now: float) -> bool:
    ttl = (
        ANON_PAID_PACKET_TTL_SECONDS
        if rec.get("paid")
        else ANON_PACKET_TTL_SECONDS
    )
    created = rec.get("created_at")
    if created is None:
        return True
    return now - _packet_created_at(rec) > ttl


def purge_packet_store(now: float | None = None) -> None:
    """Expire and cap anonymous PACKET_STORE entries so memory cannot grow unbounded."""
    current = time.time() if now is None else now
    expired = [
        key
        for key, rec in list(PACKET_STORE.items())
        if _packet_is_anonymous(rec) and _packet_is_expired(rec, current)
    ]
    for key in expired:
        PACKET_STORE.pop(key, None)

    anon_keys = [
        key for key, rec in PACKET_STORE.items() if _packet_is_anonymous(rec)
    ]
    overflow = len(anon_keys) - ANON_PACKET_STORE_MAX
    if overflow <= 0:
        return

    def eviction_order(key: str) -> tuple[int, float]:
        rec = PACKET_STORE[key]
        paid = 1 if rec.get("paid") else 0
        return (paid, _packet_created_at(rec))

    anon_keys.sort(key=eviction_order)
    for key in anon_keys[:overflow]:
        PACKET_STORE.pop(key, None)


def _new_packet_record(
    user_id: str,
    *,
    payload: dict[str, Any] | None,
    paid: bool,
    session_ids: set[str],
    created_at: float | None = None,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "payload": payload,
        "paid": paid,
        "session_ids": session_ids,
        "created_at": created_at if created_at is not None else time.time(),
    }


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _money(value: float) -> str:
    return f"${value:,.2f}"


def combined_1099_wash_sale_disallowed(supplemental: dict[str, Any] | None) -> float:
    if not supplemental:
        return 0.0
    return round(
        _as_float(supplemental.get("short_term_wash_sale_disallowed"))
        + _as_float(supplemental.get("long_term_wash_sale_disallowed")),
        2,
    )


def is_same_year_1099_compare(
    form_1099_tax_year: Any,
    analysis_tax_year: Any,
) -> bool:
    """True only when 1099 tax year equals the dashboard/analysis tax year.

    A 2024 fixture against a 2026 sample is a previous-year supplement, not a
    same-year compare. Missing years never fake a match.
    """
    form_year = _as_int(form_1099_tax_year)
    analysis_year = _as_int(analysis_tax_year)
    return form_year is not None and analysis_year is not None and form_year == analysis_year


def csv_wash_sale_disallowed_total(analysis: dict[str, Any]) -> float:
    """Wash-sale disallowed as reported on CSV flags (trade date), not 1099 lots."""
    return round(
        sum(
            _as_float(flag.get("disallowed_loss"))
            for flag in analysis.get("wash_sale_flags") or []
        ),
        2,
    )


def export_net_matching_1099(raw_fifo_net: float, wash_disallowed: float) -> float:
    """Fold classified CSV wash into FIFO net so it matches 1099 net.

    1099 net = (proceeds − basis) + wash-sale disallowed. Export net_st from
    _compute_realized_summary is raw FIFO. Adding CSV wash_sale_flags
    disallowed_loss aligns the export column with that definition. Do not use
    1099 wash here — those lots are a different universe.
    """
    return round(_as_float(raw_fifo_net) + _as_float(wash_disallowed), 2)


# Same threshold as csv_parser / harvesting: held more than 365 days = long-term.
LONG_TERM_HOLDING_DAYS = 365


def _as_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def wash_flag_is_long_term(flag: dict[str, Any]) -> bool:
    """True when the washed sale was held more than 365 days.

    Holding term uses purchase_date vs sale_date, falling back to repurchase
    on the existing flag object. Missing dates default to short-term so we
    never invent a long-term bucket.
    """
    start = _as_date(flag.get("purchase_date"))
    end = _as_date(flag.get("sale_date")) or _as_date(flag.get("repurchase_date"))
    if start is None or end is None:
        return False
    return (end - start).days > LONG_TERM_HOLDING_DAYS


def classified_csv_wash(analysis: dict[str, Any]) -> tuple[float, float]:
    """Split CSV wash into ST/LT from each flag's underlying sale term.

    Do not allocate to ST losses first. A long-term disallowed sale stays LT
    even when short-term loss buckets have room.
    """
    short_term = 0.0
    long_term = 0.0
    for flag in analysis.get("wash_sale_flags") or []:
        amount = max(_as_float(flag.get("disallowed_loss")), 0.0)
        if amount <= 0:
            continue
        if wash_flag_is_long_term(flag):
            long_term += amount
        else:
            short_term += amount
    return round(short_term, 2), round(long_term, 2)


def _realized_summary(analysis: dict[str, Any]) -> dict[str, Any] | None:
    summary = analysis.get("summary")
    if not isinstance(summary, dict):
        return None
    realized = summary.get("realized_summary")
    if not isinstance(realized, dict):
        return None
    return realized


def _has_realized_nets(realized: dict[str, Any] | None) -> bool:
    if not realized:
        return False
    return realized.get("net_st") is not None or realized.get("net_lt") is not None


def export_realized_totals(analysis: dict[str, Any]) -> dict[str, float]:
    realized = _realized_summary(analysis)
    wash = csv_wash_sale_disallowed_total(analysis)
    if not _has_realized_nets(realized):
        # Missing/null realized_summary must stay $0, not leftover wash as +ST.
        return {
            "short_term_net": 0.0,
            "long_term_net": 0.0,
            "wash_sale_disallowed": wash,
        }
    assert realized is not None
    st_wash, lt_wash = classified_csv_wash(analysis)
    return {
        "short_term_net": export_net_matching_1099(realized.get("net_st"), st_wash),
        "long_term_net": export_net_matching_1099(realized.get("net_lt"), lt_wash),
        "wash_sale_disallowed": wash,
    }


def csv_wash_sale_lots(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """One wash-sale event per CSV flag. Do not also dump replacement tax lots."""
    lots: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for flag in analysis.get("wash_sale_flags") or []:
        amount = round(_as_float(flag.get("disallowed_loss")), 2)
        if amount <= 0:
            continue
        row = {
            "symbol": flag.get("symbol") or "",
            "sale_date": str(flag.get("sale_date") or ""),
            "repurchase_date": str(flag.get("repurchase_date") or ""),
            "disallowed_loss": amount,
            "sale_quantity": _as_float(flag.get("sale_quantity")),
            "explanation": flag.get("explanation") or "",
            "source": "csv_wash_sale_flag",
        }
        key = (
            row["symbol"],
            row["sale_date"],
            row["repurchase_date"],
            row["disallowed_loss"],
        )
        if key in seen:
            continue
        seen.add(key)
        lots.append(row)

    return lots


def harvest_opportunities(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """CPA-scannable harvest rows from existing suggestions. No engine rewrite."""
    rows: list[dict[str, Any]] = []
    for suggestion in analysis.get("suggestions") or []:
        if not isinstance(suggestion, dict):
            if hasattr(suggestion, "model_dump"):
                suggestion = suggestion.model_dump(mode="json")
            else:
                continue
        savings = round(_as_float(suggestion.get("tax_savings_estimate")), 2)
        loss = round(_as_float(suggestion.get("estimated_loss")), 2)
        if savings <= 0 and loss <= 0:
            continue
        ticker = suggestion.get("symbol") or "UNKNOWN"
        label = suggestion.get("display_label") or ticker
        suggestion_id = str(suggestion.get("suggestion_id") or "")
        rows.append(
            {
                "symbol": ticker,
                "display_label": label,
                "suggestion_id": suggestion_id,
                "lot_details": str(suggestion.get("lot_details") or ""),
                "quantity": _as_float(suggestion.get("quantity")),
                "purchase_date": _purchase_date_from_row(suggestion),
                "cost_basis_per_share": _as_float(
                    suggestion.get("cost_basis_per_share")
                ),
                "term": "LT" if suggestion.get("is_long_term") else "ST",
                "estimated_federal_savings": savings,
                "estimated_loss": loss,
                "wash_sale_risk": bool(suggestion.get("wash_sale_risk")),
                "wash_sale_explanation": str(
                    suggestion.get("wash_sale_explanation") or ""
                ),
            }
        )
    return rows


def build_packet_payload(
    analysis: dict[str, Any],
    *,
    analysis_id: str = "",
) -> dict[str, Any]:
    """Structured year-close packet from existing analysis JSON (no re-parse)."""
    supplemental = analysis.get("supplemental_1099") or None
    tax_profile = analysis.get("tax_profile") or {}
    wash_lots = csv_wash_sale_lots(analysis)
    st_proceeds = _as_float((supplemental or {}).get("short_term_proceeds"))
    lt_proceeds = _as_float((supplemental or {}).get("long_term_proceeds"))
    wash_1099 = combined_1099_wash_sale_disallowed(supplemental)
    analysis_tax_year = _as_int(tax_profile.get("tax_year"))
    form_1099_tax_year = _as_int((supplemental or {}).get("tax_year"))
    same_year = bool(supplemental) and is_same_year_1099_compare(
        form_1099_tax_year, analysis_tax_year
    )
    unknown_year = bool(supplemental) and form_1099_tax_year is None
    export_totals = export_realized_totals(analysis)
    lot_report = _lot_match_payload(analysis.get("lot_match_report"))
    harvest = harvest_opportunities(analysis)
    return {
        "analysis_id": analysis_id or analysis.get("analysis_id") or "",
        "product_name": PACKET_PRODUCT_NAME,
        "price_cents": PACKET_AMOUNT_CENTS,
        "disclaimer": PACKET_DISCLAIMER,
        "analysis_tax_year": analysis_tax_year,
        "form_1099_tax_year": form_1099_tax_year,
        "form_1099_applied": bool(supplemental),
        "same_year_compare": same_year,
        "unknown_1099_year": unknown_year,
        "broker_name": (supplemental or {}).get("broker_name") or "",
        "short_term_proceeds": st_proceeds,
        "long_term_proceeds": lt_proceeds,
        "short_term_net_gain": _as_float(
            (supplemental or {}).get("short_term_net_gain")
        ),
        "long_term_net_gain": _as_float(
            (supplemental or {}).get("long_term_net_gain")
        ),
        "short_term_wash_sale_disallowed": _as_float(
            (supplemental or {}).get("short_term_wash_sale_disallowed")
        ),
        "long_term_wash_sale_disallowed": _as_float(
            (supplemental or {}).get("long_term_wash_sale_disallowed")
        ),
        "wash_sale_disallowed_1099": wash_1099,
        "export_short_term_net": export_totals["short_term_net"],
        "export_long_term_net": export_totals["long_term_net"],
        "export_wash_sale_disallowed": export_totals["wash_sale_disallowed"],
        "csv_wash_sale_lots": wash_lots,
        "harvest_opportunities": harvest,
        "lot_match_report": lot_report,
        "settlement_date_faq": SETTLEMENT_DATE_FAQ,
        "options_wash_sale_faq": OPTIONS_WASH_SALE_FAQ,
        "compare_gap_copy": COMPARE_GAP_COPY if same_year else "",
    }


def _lot_match_payload(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if hasattr(raw, "model_dump"):
        dumped = raw.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else None
    if isinstance(raw, dict):
        return raw
    return None


def _report_rows(report: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not report:
        return []
    rows = report.get(key) or []
    return [row for row in rows if isinstance(row, dict)]


def _fmt_date(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text[:10] if text else "—"


def _wash_event_line(lot: dict[str, Any]) -> str:
    symbol = lot.get("symbol") or "UNKNOWN"
    return (
        f"{symbol} {_money(lot.get('disallowed_loss') or 0)} disallowed  "
        f"sale {_fmt_date(lot.get('sale_date'))}  "
        f"repurchase {_fmt_date(lot.get('repurchase_date'))}"
    )


def _iso_date_token(value: Any) -> str:
    token = str(value or "").strip()[:10]
    if len(token) != 10:
        return ""
    try:
        date.fromisoformat(token)
        return token
    except ValueError:
        return ""


def _purchase_date_from_row(row: dict[str, Any]) -> str:
    """ISO purchase date from compact JSON or suggestion_id. No engine rewrite."""
    explicit = _iso_date_token(row.get("purchase_date"))
    if explicit:
        return explicit
    sid = str(row.get("suggestion_id") or "").strip()
    parts = [part for part in sid.split("::") if part]
    if len(parts) >= 4:
        return _iso_date_token(parts[3])
    return ""


def _harvest_lot_key(row: dict[str, Any]) -> str:
    """lot_details when present, else suggestion_id — one unique lot token."""
    details = str(row.get("lot_details") or "").strip()
    if details:
        return details
    return str(row.get("suggestion_id") or "").strip()


def _harvest_symbol_term_key(row: dict[str, Any]) -> tuple[str, str]:
    label = str(row.get("display_label") or row.get("symbol") or "UNKNOWN")
    term = str(row.get("term") or "ST")
    return (label, term)


def _harvest_collision_keys(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        key = _harvest_symbol_term_key(row)
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count > 1}


def _harvest_lines(row: dict[str, Any], *, colliding: bool = False) -> list[str]:
    """Each harvest row keeps qty + purchase date + lot_details/suggestion_id.

    Purchase date stays on the first line so two AMD ST qty-10 lots cannot
    wrap into identical symbol/term/qty lines. Explanation is never appended
    onto the savings/risk phrase.
    """
    label = row.get("display_label") or row.get("symbol") or "UNKNOWN"
    term = row.get("term") or "ST"
    qty = _as_float(row.get("quantity"))
    identity = f"{label}  {term}  qty {qty:g}"
    opened = _purchase_date_from_row(row)
    if opened:
        identity = f"{identity}  {opened}"
    elif colliding:
        basis = _as_float(row.get("cost_basis_per_share"))
        if basis:
            identity = f"{identity}  basis {_money(basis)}"
    lot_key = _harvest_lot_key(row)
    lot_key_on_identity = False
    if lot_key and len(f"{identity}  {lot_key}") <= PDF_WRAP_WIDTH:
        identity = f"{identity}  {lot_key}"
        lot_key_on_identity = True
    savings = _money(row.get("estimated_federal_savings") or 0)
    money = (
        f"wash-sale risk - not clean federal savings {savings}"
        if row.get("wash_sale_risk")
        else f"estimated federal savings {savings}"
    )
    combined = f"{identity}  {money}"
    lines = (
        [combined]
        if len(combined) <= PDF_WRAP_WIDTH
        else [identity, money]
    )
    if lot_key and not lot_key_on_identity:
        lines.insert(1, lot_key)
    if row.get("wash_sale_risk"):
        explanation = str(row.get("wash_sale_explanation") or "").strip()
        if explanation:
            lines.append(explanation)
    return lines


def harvest_plain_text(payload: dict[str, Any]) -> str:
    rows = payload.get("harvest_opportunities") or []
    lines = [HARVEST_TITLE, HARVEST_INTRO, ""]
    if rows:
        collisions = _harvest_collision_keys(rows)
        for row in rows:
            colliding = _harvest_symbol_term_key(row) in collisions
            lines.extend(_harvest_lines(row, colliding=colliding))
    else:
        lines.append("None this run.")
    return "\n".join(lines)


def _lot_line(row: dict[str, Any]) -> str:
    symbol = row.get("symbol") or row.get("description") or "UNKNOWN"
    qty = _as_float(row.get("quantity"))
    status = row.get("status") or ""
    return (
        f"{status} {symbol} qty {qty:g}  "
        f"1099 {_fmt_date(row.get('date_sold_1099'))} {_money(row.get('proceeds_1099') or 0)}  "
        f"export {_fmt_date(row.get('export_trade_date'))} {_money(row.get('proceeds_export') or 0)}"
    )


def lot_match_plain_text(payload: dict[str, Any]) -> str:
    """Paid packet page: matched / gap / unmatched 1099-B lots."""
    report = payload.get("lot_match_report") or {}
    matched = _report_rows(report, "matched")
    gap = _report_rows(report, "gap")
    unmatched = _report_rows(report, "unmatched")
    lines = [
        LOT_MATCH_TITLE,
        LOT_MATCH_INTRO,
        "",
        f"Matched ({len(matched)})",
    ]
    if matched:
        lines.extend(_lot_line(row) for row in matched)
    else:
        lines.append("None.")
    lines.extend(["", f"Gap ({len(gap)})"])
    if gap:
        lines.extend(_lot_line(row) for row in gap)
    else:
        lines.append("None.")
    lines.extend(["", f"Unmatched ({len(unmatched)})"])
    if unmatched:
        lines.extend(_lot_line(row) for row in unmatched)
    else:
        lines.append("None.")
    lines.extend(["", "FAQ", SETTLEMENT_DATE_FAQ])
    return "\n".join(lines)


def packet_plain_text(payload: dict[str, Any]) -> str:
    """Plain-text rendering used in the PDF and in content assertions."""
    lines = [
        PACKET_PRODUCT_NAME,
        PACKET_DISCLAIMER,
    ]
    if payload.get("form_1099_applied"):
        year = payload.get("form_1099_tax_year")
        lines.append(f"1099 tax year: {year if year is not None else 'unknown'}")
        lines.append(f"Short-term proceeds: {_money(payload.get('short_term_proceeds') or 0)}")
        lines.append(f"Long-term proceeds: {_money(payload.get('long_term_proceeds') or 0)}")
        lines.append(
            "Wash-sale disallowed (1099): "
            f"{_money(payload.get('wash_sale_disallowed_1099') or 0)}"
        )
    else:
        lines.append("No supplemental 1099 was applied to this analysis.")

    analysis_year = payload.get("analysis_tax_year")
    if analysis_year is not None:
        lines.append(f"CSV analysis tax year: {analysis_year}")

    if payload.get("same_year_compare"):
        lines.append("1099 vs your export is included as a dedicated page.")
    elif payload.get("unknown_1099_year") or (
        payload.get("form_1099_applied") and payload.get("form_1099_tax_year") is None
    ):
        lines.append(UNKNOWN_1099_YEAR_COPY)
    elif payload.get("form_1099_applied"):
        lines.append(
            "1099 tax year does not match this export; shown as a previous-year supplement."
        )

    lines.append(WASH_EVENTS_TITLE + ":")
    lots = payload.get("csv_wash_sale_lots") or []
    if not lots:
        lines.append("None flagged.")
    else:
        for lot in lots:
            lines.append(_wash_event_line(lot))

    report = payload.get("lot_match_report")
    if report:
        lines.append(
            f"{LOT_MATCH_TITLE}: "
            f"{_as_int(report.get('matched_count')) or len(_report_rows(report, 'matched'))} matched, "
            f"{_as_int(report.get('gap_count')) or len(_report_rows(report, 'gap'))} gap, "
            f"{_as_int(report.get('unmatched_count')) or len(_report_rows(report, 'unmatched'))} unmatched."
        )

    lines.append("FAQ")
    lines.append(SETTLEMENT_DATE_FAQ)
    lines.append(OPTIONS_WASH_SALE_FAQ)
    return "\n".join(lines)


def _escape_pdf(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_pdf_line(line: str, width: int = PDF_WRAP_WIDTH) -> list[str]:
    if len(line) <= width:
        return [line]
    words = line.split(" ")
    rows: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if len(trial) <= width:
            current = trial
            continue
        if current:
            rows.append(current)
        current = word
    if current:
        rows.append(current)
    return rows or [""]


def _two_col_row(label: str, left_value: str, right_value: str) -> str:
    return f"{label:<24}{left_value:>16}{right_value:>22}"


def same_year_compare_plain_text(payload: dict[str, Any]) -> str:
    """Dedicated two-column page body for same-year 1099 vs export."""
    return "\n".join(
        [
            COMPARE_TITLE,
            COMPARE_INTRO,
            "",
            f"{COMPARE_BROKER_HEADER:40}{COMPARE_EXPORT_HEADER}",
            _two_col_row(
                "Short-term",
                _money(payload.get("short_term_net_gain") or 0),
                _money(payload.get("export_short_term_net") or 0),
            ),
            _two_col_row(
                "Long-term",
                _money(payload.get("long_term_net_gain") or 0),
                _money(payload.get("export_long_term_net") or 0),
            ),
            _two_col_row(
                "Wash-sale disallowed",
                _money(payload.get("wash_sale_disallowed_1099") or 0),
                _money(payload.get("export_wash_sale_disallowed") or 0),
            ),
            "",
            COMPARE_GAP_COPY,
        ]
    )


def _wrapped_pdf_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.split("\n"):
        lines.extend(_wrap_pdf_line(raw))
    return lines or [""]


def _page_content_stream(text: str) -> bytes:
    lines = _wrapped_pdf_lines(text)
    y = 720
    commands = ["BT", "/F1 11 Tf"]
    first = True
    for line in lines:
        escaped = _escape_pdf(line)
        if first:
            commands.append(f"50 {y} Td ({escaped}) Tj")
            first = False
        else:
            commands.append(f"0 -16 Td ({escaped}) Tj")
        y -= 16
        if y < 48:
            break
    commands.append("ET")
    return "\n".join(commands).encode("latin-1", errors="replace")


def _page_streams_from_text(text: str) -> list[bytes]:
    lines = _wrapped_pdf_lines(text)
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if len(current) >= LINES_PER_PDF_PAGE:
            chunks.append(current)
            current = []
        current.append(line)
    if current:
        chunks.append(current)
    return [_page_content_stream("\n".join(chunk)) for chunk in chunks] or [
        _page_content_stream("")
    ]


def _assemble_pdf(page_streams: list[bytes]) -> bytes:
    if not page_streams:
        page_streams = [_page_content_stream("")]
    n = len(page_streams)
    page_ids = [3 + i * 2 for i in range(n)]
    content_ids = [4 + i * 2 for i in range(n)]
    font_id = 3 + n * 2
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode("ascii"),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, content_id, stream in zip(page_ids, content_ids, page_streams):
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        ).encode("ascii")
        objects[content_id] = (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )

    ordered = [objects[i] for i in range(1, font_id + 1)]
    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(ordered, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode("ascii"))
        out.write(obj)
        out.write(b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(ordered) + 1}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.write(
        (
            f"trailer\n<< /Size {len(ordered) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    return out.getvalue()


def render_packet_pdf(payload: dict[str, Any]) -> bytes:
    """Server-side PDF from the structured payload (no extra PDF library)."""
    pages = [packet_plain_text(payload)]
    if payload.get("harvest_opportunities"):
        pages.append(harvest_plain_text(payload))
    if payload.get("same_year_compare"):
        pages.append(same_year_compare_plain_text(payload))
    if payload.get("lot_match_report"):
        pages.append(lot_match_plain_text(payload))
    streams: list[bytes] = []
    for page in pages:
        streams.extend(_page_streams_from_text(page))
    return _assemble_pdf(streams)


def _lot_report_has_rows(report: Any) -> bool:
    if not isinstance(report, dict):
        return False
    return bool(report.get("matched") or report.get("gap") or report.get("unmatched"))


def _analysis_preserving_lot_rows(
    existing_payload: dict[str, Any] | None,
    analysis: dict[str, Any],
) -> dict[str, Any]:
    """Do not let a counts-only client payload wipe server-side lot rows."""
    incoming = analysis.get("lot_match_report")
    if _lot_report_has_rows(incoming):
        return analysis
    stored = (existing_payload or {}).get("lot_match_report")
    if not _lot_report_has_rows(stored):
        return analysis
    merged = dict(analysis)
    merged["lot_match_report"] = stored
    return merged


def _payload_preserving_harvest(
    existing_payload: dict[str, Any] | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Do not let compact client JSON wipe harvest rows rebuilt from suggestions."""
    if payload.get("harvest_opportunities"):
        return payload
    stored = (existing_payload or {}).get("harvest_opportunities")
    if not stored:
        return payload
    merged = dict(payload)
    merged["harvest_opportunities"] = stored
    return merged


def remember_analysis(analysis_id: str, user_id: str, analysis: dict[str, Any]) -> None:
    existing = PACKET_STORE.get(analysis_id) or {}
    existing_payload = existing.get("payload")
    if not isinstance(existing_payload, dict):
        existing_payload = None
    preserved = _analysis_preserving_lot_rows(existing_payload, analysis)
    payload = _payload_preserving_harvest(
        existing_payload,
        build_packet_payload(preserved, analysis_id=analysis_id),
    )
    PACKET_STORE[analysis_id] = _new_packet_record(
        user_id,
        payload=payload,
        paid=bool(existing.get("paid")),
        session_ids=set(existing.get("session_ids") or []),
        created_at=existing.get("created_at"),
    )
    purge_packet_store()


def upsert_payload(analysis_id: str, user_id: str, analysis: dict[str, Any] | None) -> dict[str, Any]:
    rec = PACKET_STORE.get(analysis_id)
    if rec and rec.get("payload"):
        if user_id and rec.get("user_id") in ("", None):
            rec["user_id"] = user_id
        purge_packet_store()
        return PACKET_STORE.get(analysis_id) or rec
    if analysis:
        remember_analysis(analysis_id, user_id, analysis)
        return PACKET_STORE[analysis_id]
    if rec:
        purge_packet_store()
        return PACKET_STORE.get(analysis_id) or rec
    PACKET_STORE[analysis_id] = _new_packet_record(
        user_id,
        payload=None,
        paid=False,
        session_ids=set(),
    )
    purge_packet_store()
    return PACKET_STORE[analysis_id]


def mark_paid(analysis_id: str, session_id: str, user_id: str = "") -> None:
    rec = PACKET_STORE.get(analysis_id)
    if rec is None:
        PACKET_STORE[analysis_id] = _new_packet_record(
            user_id,
            payload=None,
            paid=True,
            session_ids={session_id} if session_id else set(),
        )
        purge_packet_store()
        return
    rec["paid"] = True
    if session_id:
        rec.setdefault("session_ids", set()).add(session_id)
    if user_id and not rec.get("user_id"):
        rec["user_id"] = user_id
    purge_packet_store()


def is_packet_paid(analysis_id: str) -> bool:
    purge_packet_store()
    rec = PACKET_STORE.get(analysis_id)
    return bool(rec and rec.get("paid"))


def paid_session_for_user_year(user_id: str, tax_year: int | None = None) -> str | None:
    """Reuse a paid Stripe session for later analyses in the same tax year."""
    if not user_id:
        return None
    purge_packet_store()
    for rec in PACKET_STORE.values():
        if rec.get("user_id") != user_id or not rec.get("paid"):
            continue
        payload = rec.get("payload") or {}
        year = None
        if isinstance(payload, dict):
            year = payload.get("analysis_tax_year")
            profile = payload.get("tax_profile")
            if year is None and isinstance(profile, dict):
                year = profile.get("tax_year")
        if tax_year is not None and year is not None and int(year) != int(tax_year):
            continue
        session_ids = rec.get("session_ids") or set()
        for session_id in session_ids:
            if isinstance(session_id, str) and session_id.startswith("cs_"):
                return session_id
    return None


def get_payload(analysis_id: str) -> dict[str, Any] | None:
    purge_packet_store()
    rec = PACKET_STORE.get(analysis_id)
    if not rec:
        return None
    payload = rec.get("payload")
    return payload if isinstance(payload, dict) else None


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def packet_requires_test_stripe() -> bool:
    """True when live Stripe keys must not be used for this accept path.

    Staging, local, and anything that is not the production Render service
    must run Checkout in TEST mode so Mira can pay with a test card.
    """
    if _env_flag("STRIPE_FORCE_TEST_MODE"):
        return True
    frontend = (os.environ.get("FRONTEND_URL") or "").lower()
    render_service = (os.environ.get("RENDER_SERVICE_NAME") or "").lower()
    environment = (os.environ.get("ENVIRONMENT") or "").lower()
    if "staging" in frontend or "staging" in render_service:
        return True
    if "localhost" in frontend or "127.0.0.1" in frontend:
        return True
    if environment in ("development", "test", "staging"):
        return True
    if (
        "client-prod" in frontend
        or "server-prod" in render_service
        or "optionstaxhub.com" in frontend
    ):
        return False
    # Ambiguous environment: never live-charge the packet by accident.
    return True


def resolve_packet_stripe_secret_key(primary_key: str | None) -> tuple[str | None, str]:
    """Pick the Stripe secret key for year-close packet Checkout.

    Returns (key, reason). Key is None when checkout must be refused rather
    than risk a live charge on staging/local.
    """
    test_key = os.environ.get("STRIPE_SECRET_KEY_TEST") or ""
    primary = primary_key or os.environ.get("STRIPE_SECRET_KEY") or ""
    if packet_requires_test_stripe():
        candidate = test_key or primary
        if not candidate:
            return None, "missing_test_key"
        if candidate.startswith("sk_live_"):
            return None, "refused_live_key"
        return candidate, "test"
    if not primary:
        return None, "missing_key"
    return primary, "live"


def _plain_mapping(value: Any) -> dict[str, Any] | None:
    """Best-effort dict from dicts, StripeObject.to_dict(), or .items()."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            converted = to_dict()
            if isinstance(converted, dict):
                return converted
        except Exception:
            pass
    items = getattr(value, "items", None)
    if callable(items):
        try:
            return dict(items())
        except Exception:
            pass
    return None


def _session_attr(session: Any, name: str) -> Any:
    """Read a field off dict, SimpleNamespace, or StripeObject-like retrieve."""
    if session is None:
        return None
    if isinstance(session, dict):
        return session.get(name)
    value = getattr(session, name, None)
    if value is not None:
        return value
    mapping = _plain_mapping(session)
    if mapping is not None:
        return mapping.get(name)
    return None


def _session_metadata(session: Any) -> dict[str, str]:
    """Metadata from a real Stripe retrieve object, not only dict/SimpleNamespace.

    stripe>=13 StripeObject is not a dict, has no .items()/.get(), and dict()
    raises. Empty metadata here made product look unset and 403'd paid TEST
    Checkout sessions.
    """
    raw = _session_attr(session, "metadata")
    mapping = _plain_mapping(raw)
    if mapping is None and raw is not None:
        out: dict[str, str] = {}
        for key in ("product", "analysis_id", "packet_analysis", "user_id"):
            val = getattr(raw, key, None)
            if val is not None:
                out[key] = str(val)
        return out
    if not mapping:
        return {}
    return {str(k): str(v) for k, v in mapping.items() if v is not None}


def packet_session_id(session: Any) -> str:
    return str(_session_attr(session, "id") or "")


def packet_analysis_id_from_session(session: Any, *fallbacks: str) -> str:
    """Canonical analysis id: session metadata first, then caller fallbacks."""
    metadata = _session_metadata(session)
    candidates = [
        metadata.get("analysis_id") or "",
        metadata.get("packet_analysis") or "",
        *fallbacks,
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _coerce_amount_cents(amount: Any) -> Optional[int]:
    if amount is None or amount == "":
        return None
    try:
        return int(amount)
    except (TypeError, ValueError):
        return None


def session_grants_packet(session: Any, analysis_id: str = "") -> bool:
    """True only for a paid Year-close packet session.

    TipJar sessions (Coffee/Lunch/Generous, $3/$10/$25) never grant this.

    Session metadata.analysis_id (or packet_analysis) is canonical; a client
    analysis_id of local-analysis / missing must not 403 a paid TEST session.
    Sandbox Checkout may be status=complete while payment_status is not
    exactly paid.
    """
    if session is None:
        return False
    metadata = _session_metadata(session)
    product = str(metadata.get("product") or "")
    session_analysis = packet_analysis_id_from_session(session, analysis_id)
    payment_status = str(_session_attr(session, "payment_status") or "").lower()
    status = str(_session_attr(session, "status") or "").lower()
    amount = _session_attr(session, "amount_total")
    amount_cents = _coerce_amount_cents(amount)

    paid_ok = payment_status in ("paid", "no_payment_required")
    amount_is_packet = amount_cents == PACKET_AMOUNT_CENTS
    complete_ok = status == "complete" and (
        amount_cents is None or amount_is_packet
    )
    amount_ok = amount_cents is None or amount_is_packet
    granted = (
        product == PACKET_METADATA_PRODUCT
        and amount_ok
        and (paid_ok or complete_ok)
    )
    if not granted:
        logger.info(
            "year-close packet session rejected product=%s payment_status=%s "
            "status=%s amount=%s analysis_id=%s",
            product or "-",
            payment_status or "-",
            status or "-",
            amount_cents if amount_cents is not None else "-",
            session_analysis or "-",
        )
    return granted


def packet_checkout_line_items() -> list[dict[str, Any]]:
    """Inline price_data so TEST and LIVE do not share TipJar price IDs."""
    return [
        {
            "price_data": {
                "currency": "usd",
                "unit_amount": PACKET_AMOUNT_CENTS,
                "product_data": {
                    "name": PACKET_CHECKOUT_NAME,
                    "description": PACKET_CHECKOUT_DESCRIPTION,
                },
            },
            "quantity": 1,
        }
    ]


def packet_checkout_custom_text() -> dict[str, Any]:
    """Copy shown near the Stripe Pay button (right rail)."""
    return {"submit": {"message": PACKET_CHECKOUT_SUBMIT_MESSAGE}}
