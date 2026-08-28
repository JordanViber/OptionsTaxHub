"""Year-close packet: $49 one-time reconciliation download.

Builds a PDF from analysis JSON already returned by /api/portfolio/analyze.
Does not re-parse 1099 PDFs or rebuild wash-sale lots.

This is a reconciliation packet, not a filed Form 8949 and not a rebuild of lots.
"""

from __future__ import annotations

import logging
import os
import time
from io import BytesIO
from typing import Any, Optional

logger = logging.getLogger(__name__)

PACKET_AMOUNT_CENTS = 4900
PACKET_PRODUCT_NAME = "Year-close packet"
PACKET_METADATA_PRODUCT = "year_close_packet"

PACKET_DISCLAIMER = (
    "This is a reconciliation packet, not a filed Form 8949 and not a rebuild of lots."
)

SETTLEMENT_DATE_FAQ = (
    "Robinhood 1099 uses settlement date, so a year-end short option "
    "(for example SPX 12/31) can show a gain on the 1099 for a trade that "
    "does not settle until January. Totals only -- we do not parse "
    "settlement-date lots from the PDF."
)

OPTIONS_WASH_SALE_FAQ = (
    "Options and credit-spread wash-sale treatment can differ from the "
    "broker 1099. We show the 1099 wash-sale disallowed figure as reported."
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


def csv_wash_sale_lots(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Wash-sale lots already computed on the analysis (CSV engine), not 1099 lots."""
    lots: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for flag in analysis.get("wash_sale_flags") or []:
        row = {
            "symbol": flag.get("symbol") or "",
            "sale_date": str(flag.get("sale_date") or ""),
            "repurchase_date": str(flag.get("repurchase_date") or ""),
            "disallowed_loss": round(_as_float(flag.get("disallowed_loss")), 2),
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

    for lot in analysis.get("tax_lots") or []:
        disallowed = round(_as_float(lot.get("wash_sale_disallowed")), 2)
        if disallowed <= 0:
            continue
        row = {
            "symbol": lot.get("symbol") or "",
            "sale_date": "",
            "repurchase_date": str(lot.get("purchase_date") or ""),
            "disallowed_loss": disallowed,
            "sale_quantity": _as_float(lot.get("quantity")),
            "explanation": "Replacement lot with wash-sale disallowed loss from CSV analysis.",
            "source": "csv_tax_lot",
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
    return {
        "analysis_id": analysis_id or analysis.get("analysis_id") or "",
        "product_name": PACKET_PRODUCT_NAME,
        "price_cents": PACKET_AMOUNT_CENTS,
        "disclaimer": PACKET_DISCLAIMER,
        "analysis_tax_year": _as_int(tax_profile.get("tax_year")),
        "form_1099_tax_year": _as_int((supplemental or {}).get("tax_year")),
        "form_1099_applied": bool(supplemental),
        "broker_name": (supplemental or {}).get("broker_name") or "",
        "short_term_proceeds": st_proceeds,
        "long_term_proceeds": lt_proceeds,
        "short_term_wash_sale_disallowed": _as_float(
            (supplemental or {}).get("short_term_wash_sale_disallowed")
        ),
        "long_term_wash_sale_disallowed": _as_float(
            (supplemental or {}).get("long_term_wash_sale_disallowed")
        ),
        "wash_sale_disallowed_1099": wash_1099,
        "csv_wash_sale_lots": wash_lots,
        "settlement_date_faq": SETTLEMENT_DATE_FAQ,
        "options_wash_sale_faq": OPTIONS_WASH_SALE_FAQ,
    }


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

    lines.append("Wash-sale lots from the CSV:")
    lots = payload.get("csv_wash_sale_lots") or []
    if not lots:
        lines.append("None flagged.")
    else:
        for lot in lots:
            symbol = lot.get("symbol") or "UNKNOWN"
            disallowed = _as_float(lot.get("disallowed_loss"))
            lines.append(f"{symbol} {_money(disallowed)} disallowed")
            extra = []
            if lot.get("sale_date"):
                extra.append(f"sale {lot['sale_date']}")
            if lot.get("repurchase_date"):
                extra.append(f"repurchase {lot['repurchase_date']}")
            if extra:
                lines.append("  " + ", ".join(extra))

    lines.append("FAQ")
    lines.append(SETTLEMENT_DATE_FAQ)
    lines.append(OPTIONS_WASH_SALE_FAQ)
    return "\n".join(lines)


def _escape_pdf(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_pdf_line(line: str, width: int = 96) -> list[str]:
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


def render_packet_pdf(payload: dict[str, Any]) -> bytes:
    """Server-side PDF from the structured payload (no extra PDF library)."""
    lines: list[str] = []
    for raw in packet_plain_text(payload).split("\n"):
        lines.extend(_wrap_pdf_line(raw))

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
    stream = "\n".join(commands).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode("ascii"))
        out.write(obj)
        out.write(b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    return out.getvalue()


def remember_analysis(analysis_id: str, user_id: str, analysis: dict[str, Any]) -> None:
    existing = PACKET_STORE.get(analysis_id) or {}
    PACKET_STORE[analysis_id] = _new_packet_record(
        user_id,
        payload=build_packet_payload(analysis, analysis_id=analysis_id),
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
                "product_data": {"name": PACKET_PRODUCT_NAME},
            },
            "quantity": 1,
        }
    ]
