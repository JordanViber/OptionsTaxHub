"""Landing preview must match analyze of the in-app 2026 sample.

DeskPreview.tsx is a static snapshot of
client/public/sample-robinhood-transactions.csv at guest defaults
(single, $75k, tax year 2026) using the quotes below. Live quotes can
move the dollar amount; wash-sale count and AMD $300 must stay true.
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
DESK_PREVIEW_PATH = REPO_ROOT / "client" / "app" / "components" / "DeskPreview.tsx"

# Snapshot quotes used to paint DeskPreview.tsx (late-Aug 2026 market).
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


def _advertised_preview() -> tuple[int, int]:
    """Parse hero dollars and wash-sale count from the landing snapshot."""
    text = DESK_PREVIEW_PATH.read_text(encoding="utf-8")
    hero = re.search(
        r"\$([0-9,]+)\s*</Typography>\s*<Typography[^>]*>\s*Federal harvest still on the table",
        text,
    )
    washes = re.search(r'"(\d+) wash sales"', text)
    assert hero, "DeskPreview.tsx is missing a hero dollar amount"
    assert washes, "DeskPreview.tsx is missing a wash-sale count"
    return int(hero.group(1).replace(",", "")), int(washes.group(1))


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
            )
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    summary = data["summary"]
    flags = data["wash_sale_flags"]
    advertised_savings, advertised_washes = _advertised_preview()

    assert data["tax_profile"]["tax_year"] == 2026
    assert round(summary["estimated_tax_savings"]) == advertised_savings
    assert summary["wash_sale_flags_count"] == advertised_washes
    assert advertised_washes == 3
    assert summary["total_harvestable_losses"] > 0
    assert data["suggestions"]

    symbols = {flag["symbol"] for flag in flags}
    assert symbols == {"AMD", "NVDA", "TSLA"}

    amd_flags = [flag for flag in flags if flag["symbol"] == "AMD"]
    assert len(amd_flags) == 1
    assert amd_flags[0]["disallowed_loss"] == pytest.approx(300.0)
    assert amd_flags[0]["sale_date"] == "2026-07-15"
    assert amd_flags[0]["repurchase_date"] == "2026-07-24"

    nvda = next(position for position in data["positions"] if position["symbol"] == "NVDA")
    assert (nvda["unrealized_pnl"] or 0) < 0
    assert any(suggestion["symbol"] == "NVDA" for suggestion in data["suggestions"])
