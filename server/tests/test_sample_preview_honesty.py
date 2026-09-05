"""Landing preview must match analyze of the in-app 2026 sample.

DeskPreview.tsx is a static snapshot of
client/public/sample-robinhood-transactions.csv plus
client/public/sample-robinhood-1099-2026.pdf at guest defaults
(single, $75k, tax year 2026). Advertised broker ST / export ST / wash
must match the same-year 1099 vs export compare. Live quotes do not
move these totals.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_CSV_PATH = (
    REPO_ROOT / "client" / "public" / "sample-robinhood-transactions.csv"
)
SAMPLE_1099_PATH = (
    REPO_ROOT / "client" / "public" / "sample-robinhood-1099-2026.pdf"
)
DESK_PREVIEW_PATH = REPO_ROOT / "client" / "app" / "components" / "DeskPreview.tsx"

# Snapshot quotes keep analyze stable; advertised landing dollars are
# 1099 vs export totals, not harvest estimates.
SAMPLE_PREVIEW_PRICES = {
    "AAPL": 315.0,
    "AMD": 477.0,
    "META": 571.0,
    "MSFT": 505.0,
    "NVDA": 228.0,
    "SPY": 771.0,
    "TSLA": 355.0,
}

client = TestClient(main.app)


def _parse_preview_dollars(name: str) -> int:
    text = DESK_PREVIEW_PATH.read_text(encoding="utf-8")
    match = re.search(rf'{name} = "\$([0-9,]+)"', text)
    assert match, f"DeskPreview.tsx is missing {name}"
    return int(match.group(1).replace(",", ""))


def _advertised_compare() -> tuple[int, int, int]:
    """Parse broker ST, export ST, and wash from the landing snapshot."""
    return (
        _parse_preview_dollars("SAMPLE_BROKER_ST"),
        _parse_preview_dollars("SAMPLE_EXPORT_ST"),
        _parse_preview_dollars("SAMPLE_WASH"),
    )


def test_public_sample_analyze_matches_landing_preview(monkeypatch):
    """Guest analyze of the 2026 sample must match the home preview numbers."""
    monkeypatch.setattr(
        "main.fetch_current_prices",
        lambda symbols, fb=None: (
            {symbol.upper(): SAMPLE_PREVIEW_PRICES[symbol.upper()] for symbol in symbols},
            [],
        ),
    )
    monkeypatch.setattr("main.fetch_option_prices", lambda labels, fb=None: ({}, []))
    monkeypatch.setattr("main.prepare_positions_for_ai", lambda lots: [])
    monkeypatch.setattr("main._save_history_best_effort", lambda *args, **kwargs: None)

    response = client.post(
        "/api/portfolio/analyze?filing_status=single&estimated_income=75000&tax_year=2026",
        files={
            "file": (
                "sample-robinhood-transactions.csv",
                SAMPLE_CSV_PATH.read_bytes(),
                "text/csv",
            ),
            "supplemental_1099": (
                "sample-robinhood-1099-2026.pdf",
                SAMPLE_1099_PATH.read_bytes(),
                "application/pdf",
            ),
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    form_1099 = data["supplemental_1099"]
    flags = data["wash_sale_flags"]
    advertised_broker_st, advertised_export_st, advertised_wash = (
        _advertised_compare()
    )

    assert data["tax_profile"]["tax_year"] == 2026
    assert form_1099["tax_year"] == 2026

    broker_st = round(form_1099["short_term_net_gain"])
    broker_wash = round(
        form_1099["short_term_wash_sale_disallowed"]
        + form_1099["long_term_wash_sale_disallowed"]
    )
    csv_wash = round(sum(float(flag["disallowed_loss"]) for flag in flags))
    export_st = round(data["summary"]["realized_summary"]["net_st"] + csv_wash)

    assert broker_st == advertised_broker_st == 2699
    assert export_st == advertised_export_st == 0
    assert broker_wash == advertised_wash == 924
    assert csv_wash == advertised_wash

    symbols = {flag["symbol"] for flag in flags}
    assert symbols == {"AMD", "NVDA", "TSLA"}

    amd_flags = [flag for flag in flags if flag["symbol"] == "AMD"]
    assert len(amd_flags) == 1
    assert amd_flags[0]["disallowed_loss"] == pytest.approx(300.0)
    assert amd_flags[0]["sale_date"] == "2026-07-15"
    assert amd_flags[0]["repurchase_date"] == "2026-07-24"
