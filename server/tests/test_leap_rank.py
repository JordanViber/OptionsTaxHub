"""Unit tests for LEAP ranking vs owning the stock. No yfinance I/O."""
from datetime import date

import pytest

from leap_rank import (
    MIN_DTE,
    extract_rank_premium,
    fail_payload,
    format_contract_label,
    implied_cagr_to_breakeven,
    rank_contracts,
    rank_from_chain,
    score_contract,
    why_vs_richer,
    why_vs_stock,
    years_to_expiry,
)

AS_OF = date(2026, 9, 6)
EXPIRY = "2027-09-06"  # 365 calendar days → T = 1


def _row(**overrides):
    base = {
        "expiration": EXPIRY,
        "strike": 90.0,
        "bid": 11.9,
        "ask": 12.1,
        "last": 12.0,
        "open_interest": 500,
    }
    base.update(overrides)
    return base


def test_years_to_expiry_floor():
    assert years_to_expiry(365) == pytest.approx(1.0)
    assert years_to_expiry(0) == pytest.approx(1 / 365)


def test_implied_cagr_call_one_year():
    # S=100, K=90, C=12 → BE=102 → 2%
    assert implied_cagr_to_breakeven(
        right="call", spot=100, strike=90, premium=12, years=1,
    ) == pytest.approx(0.02)


def test_implied_cagr_put_one_year():
    # S=100, K=110, C=12 → BE=98 → 1 - 98/100 = 2% decline
    assert implied_cagr_to_breakeven(
        right="put", spot=100, strike=110, premium=12, years=1,
    ) == pytest.approx(1 - 98 / 100)


def test_implied_cagr_put_is_spot_to_breakeven_not_reciprocal():
    # $100 → $50 in one year is a 50% decline, not the 100% reciprocal growth.
    assert implied_cagr_to_breakeven(
        right="put", spot=100, strike=60, premium=10, years=1,
    ) == pytest.approx(0.5)
    assert implied_cagr_to_breakeven(
        right="put", spot=100, strike=60, premium=10, years=1,
    ) != pytest.approx(1.0)


def test_extract_rank_premium_prefers_mid():
    premium, source = extract_rank_premium(11.9, 12.1, 99.0)
    assert source == "mid"
    assert premium == pytest.approx(12.0)


def test_extract_rank_premium_falls_back_last_ask_bid():
    assert extract_rank_premium(None, None, 4.2) == (4.2, "last")
    assert extract_rank_premium(None, 3.5, None) == (3.5, "ask")
    assert extract_rank_premium(2.25, None, None) == (2.25, "bid")
    assert extract_rank_premium(None, None, None) == (None, None)


def test_call_rank_order_is_lower_implied_cagr_first():
    cheaper = score_contract(
        symbol="NVDA",
        right="call",
        spot=100,
        as_of=AS_OF,
        expiration=EXPIRY,
        strike=90,
        bid=11.9,
        ask=12.1,
        last=12.0,
        open_interest=100,
    )
    richer = score_contract(
        symbol="NVDA",
        right="call",
        spot=100,
        as_of=AS_OF,
        expiration=EXPIRY,
        strike=100,
        bid=7.9,
        ask=8.1,
        last=8.0,
        open_interest=100,
    )
    assert cheaper is not None and richer is not None
    assert cheaper.implied_cagr == pytest.approx(0.02)
    assert richer.implied_cagr == pytest.approx(0.08)
    assert cheaper.breakeven == pytest.approx(102)
    assert cheaper.implied_cagr < richer.implied_cagr

    ranked = rank_contracts(
        symbol="NVDA",
        right="call",
        spot=100,
        as_of=AS_OF,
        rows=[
            _row(strike=100, bid=7.9, ask=8.1, last=8.0),
            _row(strike=90, bid=11.9, ask=12.1, last=12.0),
            _row(strike=95, bid=9.9, ask=10.1, last=10.0),
        ],
    )
    assert [row.strike for row in ranked] == [90.0, 95.0, 100.0]
    assert ranked[0].implied_cagr < ranked[1].implied_cagr < ranked[2].implied_cagr

    why = why_vs_stock(cheaper, 100)
    assert "annualized move" in why
    assert "break even" in why
    assert "higher CAGR" not in why
    assert "highest implied CAGR" not in why.lower()

    vs_richer = why_vs_richer(cheaper, richer)
    assert vs_richer is not None
    assert vs_richer.startswith("Less annualized move to break even")
    assert "higher CAGR" not in vs_richer


def test_put_score_and_copy():
    row = score_contract(
        symbol="SPY",
        right="put",
        spot=100,
        as_of=AS_OF,
        expiration=EXPIRY,
        strike=110,
        bid=11.9,
        ask=12.1,
        last=12.0,
        open_interest=50,
    )
    assert row is not None
    assert row.breakeven == pytest.approx(98)
    decline = 1 - 98 / 100
    reciprocal = 100 / 98 - 1
    assert row.implied_cagr == pytest.approx(decline)
    assert row.implied_cagr != pytest.approx(reciprocal)
    why = why_vs_stock(row, 100)
    assert "annualized decline" in why
    assert "break even vs spot" in why
    assert "higher CAGR" not in why
    assert "2.0%" in why


def test_put_rank_order_is_less_annualized_decline_first():
    ranked = rank_contracts(
        symbol="SPY",
        right="put",
        spot=100,
        as_of=AS_OF,
        rows=[
            _row(strike=100, bid=5.9, ask=6.1, last=6.0),
            _row(strike=110, bid=11.9, ask=12.1, last=12.0),
            _row(strike=105, bid=7.9, ask=8.1, last=8.0),
        ],
    )
    assert [row.strike for row in ranked] == [110.0, 105.0, 100.0]
    assert ranked[0].implied_cagr < ranked[1].implied_cagr < ranked[2].implied_cagr
    why = why_vs_stock(ranked[0], 100)
    assert "annualized decline" in why
    assert "higher CAGR" not in why
    vs_richer = why_vs_richer(ranked[0], ranked[1])
    assert vs_richer is not None
    assert vs_richer.startswith("Less annualized move to break even")
    assert "higher CAGR" not in vs_richer


def test_tie_break_lower_extrinsic_yield_wins():
    # Same implied CAGR (2%): 1-year BE 102 vs 2-year BE 104.04 at K=90.
    # The 1-year has less annualized time value, so it ranks first.
    one_year = _row(strike=90, bid=11.9, ask=12.1, last=12.0, expiration=EXPIRY)
    two_year = _row(
        strike=90,
        bid=14.00,
        ask=14.08,
        last=14.04,
        expiration="2028-09-05",
    )
    ranked = rank_contracts(
        symbol="NVDA",
        right="call",
        spot=100,
        as_of=AS_OF,
        rows=[two_year, one_year],
    )
    assert len(ranked) == 2
    assert ranked[0].implied_cagr == pytest.approx(ranked[1].implied_cagr)
    assert ranked[0].extrinsic_yield < ranked[1].extrinsic_yield
    assert ranked[0].expiration == EXPIRY


def test_requires_leap_dte():
    assert MIN_DTE == 365
    short = score_contract(
        symbol="NVDA",
        right="call",
        spot=100,
        as_of=AS_OF,
        expiration="2027-03-06",
        strike=90,
        bid=11.9,
        ask=12.1,
        last=12.0,
        open_interest=10,
    )
    assert short is None

    # CT min_expiry 2027-09-06 vs UTC as_of 2026-09-07 is DTE=364; still ineligible.
    utc_as_of = date(2026, 9, 7)
    ct_min = score_contract(
        symbol="NVDA",
        right="call",
        spot=100,
        as_of=utc_as_of,
        expiration="2027-09-06",
        strike=90,
        bid=11.9,
        ask=12.1,
        last=12.0,
        open_interest=10,
    )
    assert (date(2027, 9, 6) - utc_as_of).days == MIN_DTE - 1
    assert ct_min is None
    utc_ok = score_contract(
        symbol="NVDA",
        right="call",
        spot=100,
        as_of=utc_as_of,
        expiration="2027-09-07",
        strike=90,
        bid=11.9,
        ask=12.1,
        last=12.0,
        open_interest=10,
    )
    assert utc_ok is not None
    assert utc_ok.dte == MIN_DTE


def test_filters_wide_spread_cheap_premium_otm_and_leverage():
    as_of = AS_OF
    wide = score_contract(
        symbol="NVDA", right="call", spot=100, as_of=as_of, expiration=EXPIRY,
        strike=90, bid=5.0, ask=12.0, last=8.0, open_interest=10,
    )
    assert wide is None  # (12-5)/8.5 > 0.35

    cheap = score_contract(
        symbol="NVDA", right="call", spot=100, as_of=as_of, expiration=EXPIRY,
        strike=90, bid=0.04, ask=0.05, last=0.04, open_interest=10,
    )
    assert cheap is None

    far_otm = score_contract(
        symbol="NVDA", right="call", spot=100, as_of=as_of, expiration=EXPIRY,
        strike=150, bid=4.9, ask=5.1, last=5.0, open_interest=10,
    )
    assert far_otm is None  # K/S = 1.5

    no_leverage = score_contract(
        symbol="NVDA", right="call", spot=100, as_of=as_of, expiration=EXPIRY,
        strike=90, bid=90.0, ask=91.0, last=90.5, open_interest=10,
    )
    assert no_leverage is None  # S/C < 1.15

    dead_oi = score_contract(
        symbol="NVDA", right="call", spot=100, as_of=as_of, expiration=EXPIRY,
        strike=90, bid=11.9, ask=12.1, last=12.0, open_interest=0,
    )
    assert dead_oi is None

    far_otm_put = score_contract(
        symbol="NVDA", right="put", spot=100, as_of=as_of, expiration=EXPIRY,
        strike=50, bid=1.9, ask=2.1, last=2.0, open_interest=10,
    )
    assert far_otm_put is None

    assert implied_cagr_to_breakeven(
        right="put", spot=100, strike=5, premium=5, years=1,
    ) is None


def test_missing_oi_column_is_allowed():
    row = score_contract(
        symbol="NVDA",
        right="call",
        spot=100,
        as_of=AS_OF,
        expiration=EXPIRY,
        strike=90,
        bid=11.9,
        ask=12.1,
        last=12.0,
        open_interest=None,
    )
    assert row is not None


def test_zero_eligible_is_no_candidates():
    payload = rank_from_chain(
        symbol="nvda",
        right="call",
        spot=100,
        as_of=AS_OF,
        expiry_from=date(2027, 6, 1),
        expiry_to=date(2028, 9, 1),
        rows=[_row(strike=250, bid=1.0, ask=1.1, last=1.05)],
        expirations_used=["2027-09-06"],
        warnings=[],
    )
    assert payload["ok"] is False
    assert payload["reason"] == "no_candidates"
    assert payload["ranks"] == []
    assert "met the value filters" in payload["message"]


def test_no_quote_and_no_chain_fail_closed():
    quote = rank_from_chain(
        symbol="NVDA",
        right="call",
        spot=None,
        as_of=AS_OF,
        expiry_from=date(2027, 6, 1),
        expiry_to=date(2028, 9, 1),
        rows=[_row()],
        expirations_used=["2027-09-06"],
        warnings=[],
    )
    assert quote["ok"] is False
    assert quote["reason"] == "no_quote"
    assert quote["ranks"] == []
    assert "do not invent prices" in quote["message"]

    chain = rank_from_chain(
        symbol="NVDA",
        right="call",
        spot=100,
        as_of=AS_OF,
        expiry_from=date(2027, 6, 1),
        expiry_to=date(2028, 9, 1),
        rows=[],
        expirations_used=[],
        warnings=["yfinance is not installed"],
    )
    assert chain["ok"] is False
    assert chain["reason"] == "no_chain"
    assert chain["ranks"] == []


def test_success_payload_top_three_and_copy_lock():
    rows = [
        _row(strike=90, bid=11.9, ask=12.1, last=12.0),
        _row(strike=95, bid=9.9, ask=10.1, last=10.0),
        _row(strike=100, bid=7.9, ask=8.1, last=8.0),
        _row(strike=92, bid=10.9, ask=11.1, last=11.0),
    ]
    payload = rank_from_chain(
        symbol="nvda",
        right="call",
        spot=100,
        as_of=AS_OF,
        expiry_from=date(2027, 9, 1),
        expiry_to=date(2027, 9, 10),
        rows=rows,
        expirations_used=["2027-09-06"],
        warnings=[],
    )
    assert payload["ok"] is True
    assert payload["symbol"] == "NVDA"
    assert len(payload["ranks"]) == 3
    cagrs = [item["implied_cagr"] for item in payload["ranks"]]
    assert cagrs == sorted(cagrs)
    assert payload["ranks"][0]["rank"] == 1
    assert payload["ranks"][0]["premium"] == pytest.approx(12.0)
    assert payload["ranks"][0]["premium_source"] == "mid"
    why = payload["ranks"][0]["why_vs_stock"]
    assert "annualized move" in why
    assert "break even vs owning shares" in why
    assert "higher CAGR" not in why
    assert "higher CAGR" not in (payload["ranks"][0]["why_vs_richer"] or "")
    assert payload["ranks"][2]["why_vs_richer"] is None


def test_format_contract_label_matches_harvest_style():
    assert format_contract_label("NVDA", "2028-01-21", "call", 110) == (
        "NVDA 1/21/2028 Call $110.00"
    )


def test_fail_payload_reasons():
    assert fail_payload("no_quote", "spy", "call")["message"].startswith(
        "Could not fetch a live SPY quote"
    )
    assert "long put LEAPs" in fail_payload("no_candidates", "SPY", "put")["message"]
