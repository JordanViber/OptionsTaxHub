"""Helpers for extracting reconciliation context from Robinhood 1099 PDFs.

The goal of this module is not to fully rebuild lot-level history from the PDF.
Instead, it extracts the high-signal broker-reported metadata that can improve
portfolio analysis for edge cases such as wash-sale carryovers, assignments,
splits, and renamed tickers.
"""

from __future__ import annotations

import re
from io import BytesIO

from pypdf import PdfReader

from models import Supplemental1099Summary

_NUMBER = r"[0-9]{1,3}(?:,[0-9]{3})*\.\d{2}"
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


def _parse_money(raw_value: str | None) -> float:
    if not raw_value:
        return 0.0
    return float(raw_value.replace(",", ""))


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
    )
