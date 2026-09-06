"""Helpers for extracting reconciliation context from Robinhood 1099 PDFs.

The goal of this module is not to fully rebuild lot-level history from the PDF.
Instead, it extracts the high-signal broker-reported metadata that can improve
portfolio analysis for edge cases such as wash-sale carryovers, assignments,
splits, and renamed tickers.

1099-B lot rows are parsed as additive context (totals extraction is unchanged).
Matching those lots to CSV FIFO closes happens in lot_matcher.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from io import BytesIO

from pypdf import PdfReader

from models import Form1099BLot, Supplemental1099Summary

_NUMBER = r"(?:-?[0-9]{1,3}(?:,[0-9]{3})*\.\d{2}|\([0-9]{1,3}(?:,[0-9]{3})*\.\d{2}\))"
_TOTAL_SHORT_TERM_PATTERN = re.compile(
    rf"Total\s+Short-term\s*(?P<proceeds>{_NUMBER})\s*"
    rf"(?P<cost_basis>{_NUMBER})\s*(?P<market_discount>{_NUMBER})\s*"
    rf"(?P<wash_sale_disallowed>{_NUMBER})\s*(?P<net_gain>{_NUMBER})",
    re.IGNORECASE,
)
_TOTAL_LONG_TERM_PATTERN = re.compile(
    rf"Total\s+Long-term\s*(?P<proceeds>{_NUMBER})\s*"
    rf"(?P<cost_basis>{_NUMBER})\s*(?P<market_discount>{_NUMBER})\s*"
    rf"(?P<wash_sale_disallowed>{_NUMBER})\s*(?P<net_gain>{_NUMBER})",
    re.IGNORECASE,
)
_TAX_YEAR_PATTERN = re.compile(
    r"Enclosed is your (?P<tax_year>20\d{2}) Consolidated Tax Statement",
    re.IGNORECASE,
)
_SYMBOL_PATTERN = re.compile(r"/\s*Symbol:\s*(?P<symbol>[A-Z][A-Z0-9.\-]{0,9})\b")
_QTY = r"[0-9]{1,3}(?:,[0-9]{3})*(?:\.\d+)?|[0-9]+\.\d+|[0-9]+"
_SAMPLE_STOCK_LOT = re.compile(
    rf"(?P<symbol>[A-Z][A-Z0-9.\-]{{0,9}})\s+(?P<qty>{_QTY})\s+sh\s+sold\s+"
    rf"(?P<sold>\d{{1,2}}/\d{{1,2}}/\d{{2,4}})\s+settled\s+"
    rf"(?P<settled>\d{{1,2}}/\d{{1,2}}/\d{{2,4}})\s+proceeds\s+"
    rf"(?P<proceeds>{_NUMBER})\s+cost\s+(?P<cost>{_NUMBER})\s+wash\s+"
    rf"(?P<wash>{_NUMBER})",
    re.IGNORECASE,
)
_SAMPLE_SPX_HEAD = re.compile(
    r"(?P<symbol>[A-Z][A-Z0-9.\-]{0,9})\s+"
    r"(?P<expiry>\d{1,2}/\d{1,2}/\d{2,4})\s+index option\s+"
    r"settlement date\s+(?P<settled>\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)
_SAMPLE_SPX_AMOUNTS = re.compile(
    rf"(?P<symbol>[A-Z][A-Z0-9.\-]{{0,9}})\s+proceeds\s+(?P<proceeds>{_NUMBER})\s+"
    rf"cost\s+(?P<cost>{_NUMBER})\s+wash\s+(?P<wash>{_NUMBER})"
    rf"(?:\s+net\s+(?P<net>{_NUMBER}))?",
    re.IGNORECASE,
)
_SECURITY_HEADER = re.compile(
    r"(?P<description>[A-Z0-9][^/]{1,90}?)\s*/\s*CUSIP:\s*(?P<cusip>[A-Z0-9]*)"
    r"\s*/\s*Symbol:\s*(?P<symbol>[A-Z][A-Z0-9.\-]{0,9})?",
    re.IGNORECASE,
)
_BROKER_LOT_ROW = re.compile(
    rf"(?P<sold>\d{{2}}/\d{{2}}/\d{{2,4}})\s+"
    rf"(?P<qty>{_QTY})\s+"
    rf"(?P<proceeds>{_NUMBER})\s+"
    rf"(?P<acquired>Various|\d{{2}}/\d{{2}}/\d{{2,4}})\s+"
    rf"(?P<cost>{_NUMBER})\s+"
    rf"(?:(?:(?P<wash>{_NUMBER})\s+W\s+)|\.\.\.\s+)?"
    rf"(?P<gain>{_NUMBER})\s+"
    rf"(?P<info>Option sale|Sale|Total of \d+ transactions)",
    re.IGNORECASE,
)
_SECURITY_TOTAL = re.compile(r"Security\s*total\s*:", re.IGNORECASE)
_PROCEEDS_SECTION = re.compile(
    r"Proceeds from Broker and Barter Exchange Transactions",
    re.IGNORECASE,
)


def _parse_money(raw_value: str | None) -> float:
    if not raw_value:
        return 0.0
    stripped = raw_value.strip()
    # Parentheses notation represents a negative number, e.g. (1,234.56) → -1234.56
    if stripped.startswith("(") and stripped.endswith(")"):
        return -float(stripped[1:-1].replace(",", ""))
    return float(stripped.replace(",", ""))


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract concatenated text from all pages in a PDF."""
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_tax_year(text: str) -> int | None:
    match = _TAX_YEAR_PATTERN.search(text)
    if not match:
        return None
    return int(match.group("tax_year"))


def _extract_term_totals(text: str, pattern: re.Pattern[str]) -> dict[str, float]:
    match = pattern.search(text)
    if not match:
        return {
            "proceeds": 0.0,
            "cost_basis": 0.0,
            "wash_sale_disallowed": 0.0,
            "net_gain": 0.0,
        }

    groups = match.groupdict()
    return {
        "proceeds": _parse_money(groups.get("proceeds")),
        "cost_basis": _parse_money(groups.get("cost_basis")),
        "wash_sale_disallowed": _parse_money(groups.get("wash_sale_disallowed")),
        "net_gain": _parse_money(groups.get("net_gain")),
    }


def _extract_symbols(text: str) -> list[str]:
    return sorted({match.group("symbol") for match in _SYMBOL_PATTERN.finditer(text)})


def _parse_qty(raw_value: str | None) -> float:
    if not raw_value:
        return 0.0
    return float(raw_value.replace(",", ""))


def _parse_lot_date(raw_value: str | None) -> date | None:
    if not raw_value:
        return None
    text = raw_value.strip()
    if not text or text.lower() == "various":
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _section_meta(blob: str) -> tuple[str, bool, str]:
    upper = blob.upper()
    if "LONG TERM" in upper or "LONG-TERM" in upper:
        term = "long"
    elif "UNDETERMINED" in upper:
        term = "undetermined"
    else:
        term = "short"
    covered = "NONCOVERED" not in upper
    box = ""
    box_match = re.search(
        r"Box\s+([A-F])\s+checked",
        blob,
        re.IGNORECASE,
    )
    if box_match:
        box = box_match.group(1).upper()
    elif term == "short" and covered:
        box = "A"
    elif term == "short" and not covered:
        box = "B"
    elif term == "long" and covered:
        box = "D"
    elif term == "long" and not covered:
        box = "E"
    return term, covered, box


def _extract_sample_lots(text: str) -> list[Form1099BLot]:
    lots: list[Form1099BLot] = []
    for match in _SAMPLE_STOCK_LOT.finditer(text):
        lots.append(
            Form1099BLot(
                symbol=match.group("symbol").upper(),
                quantity=_parse_qty(match.group("qty")),
                date_sold=_parse_lot_date(match.group("settled")),
                date_acquired=_parse_lot_date(match.group("sold")),
                proceeds=_parse_money(match.group("proceeds")),
                cost_basis=_parse_money(match.group("cost")),
                wash_sale_disallowed=_parse_money(match.group("wash")),
                gain_or_loss=round(
                    _parse_money(match.group("proceeds"))
                    - _parse_money(match.group("cost"))
                    + _parse_money(match.group("wash")),
                    2,
                ),
                term="short",
                covered=True,
                form_8949_box="A",
                additional_info="Sale",
            )
        )

    heads = list(_SAMPLE_SPX_HEAD.finditer(text))
    amounts = {
        match.group("symbol").upper(): match
        for match in _SAMPLE_SPX_AMOUNTS.finditer(text)
    }
    seen_spx: set[str] = set()
    for head in heads:
        symbol = head.group("symbol").upper()
        amount = amounts.get(symbol)
        if amount is None or symbol in seen_spx:
            continue
        seen_spx.add(symbol)
        proceeds = _parse_money(amount.group("proceeds"))
        cost = _parse_money(amount.group("cost"))
        wash = _parse_money(amount.group("wash"))
        net = amount.group("net")
        lots.append(
            Form1099BLot(
                symbol=symbol,
                quantity=1.0,
                date_sold=_parse_lot_date(head.group("settled")),
                date_acquired=_parse_lot_date(head.group("expiry")),
                proceeds=proceeds,
                cost_basis=cost,
                wash_sale_disallowed=wash,
                gain_or_loss=_parse_money(net) if net else round(proceeds - cost + wash, 2),
                term="short",
                covered=True,
                form_8949_box="A",
                additional_info="index option settlement",
            )
        )
    return lots


def _extract_broker_1099b_lots(text: str) -> list[Form1099BLot]:
    start = _PROCEEDS_SECTION.search(text)
    if not start:
        return []
    body = text[start.start() :]
    collapsed = re.sub(r"[ \t]+", " ", body)
    collapsed = collapsed.replace("\n", " ")
    lots: list[Form1099BLot] = []
    headers = list(_SECURITY_HEADER.finditer(collapsed))
    if not headers:
        return []

    for index, header in enumerate(headers):
        chunk_end = (
            headers[index + 1].start()
            if index + 1 < len(headers)
            else len(collapsed)
        )
        prefix = collapsed[max(0, header.start() - 400) : header.start()]
        term, covered, box = _section_meta(prefix)
        chunk = collapsed[header.end() : chunk_end]
        total_at = _SECURITY_TOTAL.search(chunk)
        if total_at:
            chunk = chunk[: total_at.start()]
        description = " ".join(header.group("description").split())
        cusip = header.group("cusip") or ""
        symbol = (header.group("symbol") or "").upper()
        for row in _BROKER_LOT_ROW.finditer(chunk):
            info = row.group("info")
            lots.append(
                Form1099BLot(
                    symbol=symbol,
                    cusip=cusip,
                    description=description,
                    quantity=_parse_qty(row.group("qty")),
                    date_sold=_parse_lot_date(row.group("sold")),
                    date_acquired=_parse_lot_date(row.group("acquired")),
                    proceeds=_parse_money(row.group("proceeds")),
                    cost_basis=_parse_money(row.group("cost")),
                    wash_sale_disallowed=_parse_money(row.group("wash")),
                    gain_or_loss=_parse_money(row.group("gain")),
                    term=term,
                    covered=covered,
                    form_8949_box=box,
                    additional_info=info,
                    is_aggregate=info.lower().startswith("total of"),
                )
            )
    return lots


def extract_1099b_lots(text: str) -> list[Form1099BLot]:
    """Parse 1099-B lot rows from sample or live Robinhood PDF text."""
    sample = _extract_sample_lots(text)
    if sample:
        return sample
    return _extract_broker_1099b_lots(text)


def _build_insights(
    *,
    tax_year: int | None,
    expected_previous_year: int | None,
    referenced_symbols: list[str],
    matched_symbols: list[str],
    short_term_wash_sale_disallowed: float,
    long_term_wash_sale_disallowed: float,
) -> list[str]:
    insights: list[str] = []

    if tax_year is not None and expected_previous_year is not None:
        if tax_year == expected_previous_year:
            insights.append(
                f"The supplemental Robinhood 1099 matches the expected prior tax year ({tax_year})."
            )
        else:
            insights.append(
                f"The supplemental Robinhood 1099 is for tax year {tax_year}, not the expected prior year ({expected_previous_year})."
            )

    if matched_symbols:
        preview = ", ".join(matched_symbols[:6])
        suffix = "" if len(matched_symbols) <= 6 else ", …"
        insights.append(
            f"Matched prior-year 1099 activity to {len(matched_symbols)} current symbol(s): {preview}{suffix}."
        )
    elif referenced_symbols:
        insights.append(
            "No symbols from the prior-year 1099 directly matched the current CSV. The document can still help with renamed tickers, closed positions, and carryover basis checks."
        )

    total_wash_sale = short_term_wash_sale_disallowed + long_term_wash_sale_disallowed
    if total_wash_sale > 0:
        insights.append(
            f"The prior-year 1099 reported ${total_wash_sale:,.2f} of wash-sale disallowed loss that may still affect adjusted basis."
        )

    return insights


def parse_robinhood_1099_pdf(
    pdf_bytes: bytes,
    *,
    current_symbols: set[str] | None = None,
    filename: str = "",
    expected_previous_year: int | None = None,
) -> Supplemental1099Summary:
    """Parse a Robinhood 1099 PDF into a compact reconciliation summary."""
    text = extract_text_from_pdf(pdf_bytes)
    tax_year = _extract_tax_year(text)
    short_term = _extract_term_totals(text, _TOTAL_SHORT_TERM_PATTERN)
    long_term = _extract_term_totals(text, _TOTAL_LONG_TERM_PATTERN)
    referenced_symbols = _extract_symbols(text)
    current_symbols = current_symbols or set()
    matched_symbols = sorted(current_symbols.intersection(referenced_symbols))
    lots = extract_1099b_lots(text)

    return Supplemental1099Summary(
        source_filename=filename,
        broker_name="Robinhood" if "Robinhood" in text else "",
        tax_year=tax_year,
        short_term_proceeds=short_term["proceeds"],
        short_term_cost_basis=short_term["cost_basis"],
        short_term_wash_sale_disallowed=short_term["wash_sale_disallowed"],
        short_term_net_gain=short_term["net_gain"],
        long_term_proceeds=long_term["proceeds"],
        long_term_cost_basis=long_term["cost_basis"],
        long_term_wash_sale_disallowed=long_term["wash_sale_disallowed"],
        long_term_net_gain=long_term["net_gain"],
        referenced_symbols=referenced_symbols,
        matched_symbols=matched_symbols,
        insights=_build_insights(
            tax_year=tax_year,
            expected_previous_year=expected_previous_year,
            referenced_symbols=referenced_symbols,
            matched_symbols=matched_symbols,
            short_term_wash_sale_disallowed=short_term["wash_sale_disallowed"],
            long_term_wash_sale_disallowed=long_term["wash_sale_disallowed"],
        ),
        lots=lots,
    )
