"""Year-close packet: $49 one-time reconciliation download.

Builds a PDF from analysis JSON already returned by /api/portfolio/analyze.
Does not re-parse 1099 PDFs or rebuild wash-sale lots.

This is a reconciliation packet, not a filed Form 8949 and not a rebuild of lots.
"""

from __future__ import annotations

import os
from io import BytesIO
from typing import Any, Optional

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


def reset_packet_store() -> None:
    """Clear entitlements (tests only)."""
    PACKET_STORE.clear()


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
    PACKET_STORE[analysis_id] = {
        "user_id": user_id,
        "payload": build_packet_payload(analysis, analysis_id=analysis_id),
        "paid": bool(existing.get("paid")),
        "session_ids": set(existing.get("session_ids") or []),
    }


def upsert_payload(analysis_id: str, user_id: str, analysis: dict[str, Any] | None) -> dict[str, Any]:
    rec = PACKET_STORE.get(analysis_id)
    if rec and rec.get("payload"):
        if user_id and rec.get("user_id") in ("", None):
            rec["user_id"] = user_id
        return rec
    if analysis:
        remember_analysis(analysis_id, user_id, analysis)
        return PACKET_STORE[analysis_id]
    if rec:
        return rec
    PACKET_STORE[analysis_id] = {
        "user_id": user_id,
        "payload": None,
        "paid": False,
        "session_ids": set(),
    }
    return PACKET_STORE[analysis_id]


def mark_paid(analysis_id: str, session_id: str, user_id: str = "") -> None:
    rec = PACKET_STORE.get(analysis_id)
    if rec is None:
        PACKET_STORE[analysis_id] = {
            "user_id": user_id,
            "payload": None,
            "paid": True,
            "session_ids": {session_id} if session_id else set(),
        }
        return
    rec["paid"] = True
    if session_id:
        rec.setdefault("session_ids", set()).add(session_id)
    if user_id and not rec.get("user_id"):
        rec["user_id"] = user_id


def is_packet_paid(analysis_id: str) -> bool:
    rec = PACKET_STORE.get(analysis_id)
    return bool(rec and rec.get("paid"))


def get_payload(analysis_id: str) -> dict[str, Any] | None:
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
    if "client-prod" in frontend or "server-prod" in render_service:
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


def session_grants_packet(session: Any, analysis_id: str) -> bool:
    """True only for a paid Year-close packet session for this analysis.

    TipJar sessions (Coffee/Lunch/Generous, $3/$10/$25) never grant this.
    """
    if session is None:
        return False
    metadata = _session_metadata(session)
    product = str(metadata.get("product") or "")
    session_analysis = str(metadata.get("analysis_id") or "")
    if product != PACKET_METADATA_PRODUCT:
        return False
    if analysis_id and session_analysis and session_analysis != analysis_id:
        return False
    payment_status = str(getattr(session, "payment_status", "") or "").lower()
    if payment_status not in ("paid", "no_payment_required"):
        if isinstance(session, dict):
            payment_status = str(session.get("payment_status") or "").lower()
        if payment_status not in ("paid", "no_payment_required"):
            return False
    amount = getattr(session, "amount_total", None)
    if amount is None and isinstance(session, dict):
        amount = session.get("amount_total")
    if amount is not None and int(amount) != PACKET_AMOUNT_CENTS:
        return False
    return True


def _session_metadata(session: Any) -> dict[str, str]:
    metadata = getattr(session, "metadata", None)
    if metadata is None and isinstance(session, dict):
        metadata = session.get("metadata")
    if not metadata:
        return {}
    if isinstance(metadata, dict):
        return {str(k): str(v) for k, v in metadata.items()}
    try:
        return {str(k): str(v) for k, v in dict(metadata).items()}
    except Exception:
        return {}


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
