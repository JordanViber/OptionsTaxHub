"""
LEAP value ranking vs owning the underlying.

Primary score is implied CAGR to breakeven: lower is better (less annualized
move required vs owning the stock). Not a forecast, not advice, not Greeks.

DISCLAIMER: Educational/simulation only — not financial or tax advice.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

CONTRACT_MULTIPLIER = 100
YEAR_BASIS = 365
MIN_PREMIUM = 0.10
MAX_RELATIVE_SPREAD = 0.35
MIN_LEVERAGE = 1.15
MIN_DTE = 365
CALL_MONEYNESS = (0.60, 1.10)
PUT_MONEYNESS = (0.90, 1.40)
MAX_RANKS = 3

FAIL_NO_QUOTE = "no_quote"
FAIL_NO_CHAIN = "no_chain"
FAIL_NO_CANDIDATES = "no_candidates"


def round_cents(value: float) -> float:
    return round(value * 100) / 100


def _positive(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number != number:  # noqa: PLR0124 — NaN check
        return None
    return number


def extract_rank_premium(
    bid: Optional[float],
    ask: Optional[float],
    last: Optional[float],
) -> tuple[Optional[float], Optional[str]]:
    """Mid when a two-sided market exists; else last, ask, bid. Never invent."""
    bid_p = _positive(bid)
    ask_p = _positive(ask)
    last_p = _positive(last)
    if bid_p is not None and ask_p is not None:
        return round_cents((bid_p + ask_p) / 2), "mid"
    if last_p is not None:
        return round_cents(last_p), "last"
    if ask_p is not None:
        return round_cents(ask_p), "ask"
    if bid_p is not None:
        return round_cents(bid_p), "bid"
    return None, None


def relative_spread(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    bid_p = _positive(bid)
    ask_p = _positive(ask)
    if bid_p is None or ask_p is None:
        return None
    mid = (bid_p + ask_p) / 2
    if mid <= 0:
        return None
    return (ask_p - bid_p) / mid


def years_to_expiry(dte: int) -> float:
    return max(int(dte), 1) / YEAR_BASIS


def implied_cagr_to_breakeven(
    *,
    right: str,
    spot: float,
    strike: float,
    premium: float,
    years: float,
) -> Optional[float]:
    """Primary score: (BE / S) ** (1 / T) - 1. Lower is better (less move to BE)."""
    if spot <= 0 or years <= 0 or premium < 0:
        return None
    if right == "call":
        breakeven = strike + premium
    elif right == "put":
        breakeven = strike - premium
    else:
        return None
    if breakeven <= 0:
        return None
    return (breakeven / spot) ** (1 / years) - 1


def format_usd(value: float) -> str:
    return f"${value:,.2f}"


def format_pct(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def format_leverage(leverage: float) -> str:
    return f"{leverage:.1f}×"


def format_contract_label(
    symbol: str,
    expiration: str,
    right: str,
    strike: float,
) -> str:
    parsed = datetime.strptime(expiration, "%Y-%m-%d").date()
    kind = "Call" if right == "call" else "Put"
    return (
        f"{symbol} {parsed.month}/{parsed.day}/{parsed.year} {kind} "
        f"${strike:.2f}"
    )


def _moneyness_band(right: str) -> tuple[float, float]:
    if right == "put":
        return PUT_MONEYNESS
    return CALL_MONEYNESS


@dataclass(frozen=True)
class ScoredContract:
    symbol: str
    right: str
    strike: float
    expiration: str
    premium: float
    premium_source: str
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    dte: int
    years: float
    breakeven: float
    implied_cagr: float
    leverage: float
    intrinsic: float
    extrinsic: float
    extrinsic_yield: float
    spread: Optional[float]
    open_interest: Optional[int]
    volume: Optional[int]
    contract_label: str


def score_contract(
    *,
    symbol: str,
    right: str,
    spot: float,
    as_of: date,
    expiration: str,
    strike: float,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    last: Optional[float] = None,
    open_interest: Optional[int] = None,
    volume: Optional[int] = None,
    expiry_from: Optional[date] = None,
    expiry_to: Optional[date] = None,
) -> Optional[ScoredContract]:
    """Return a scored long LEAP, or None when the row is ineligible."""
    if right not in ("call", "put") or spot <= 0 or strike <= 0:
        return None

    try:
        expiry = datetime.strptime(expiration, "%Y-%m-%d").date()
    except ValueError:
        return None

    if expiry_from is not None and expiry < expiry_from:
        return None
    if expiry_to is not None and expiry > expiry_to:
        return None
    if expiry < as_of:
        return None

    dte = (expiry - as_of).days
    if dte < MIN_DTE:
        return None

    spread = relative_spread(bid, ask)
    if spread is not None and spread > MAX_RELATIVE_SPREAD:
        return None

    premium, source = extract_rank_premium(bid, ask, last)
    if premium is None or source is None or premium < MIN_PREMIUM:
        return None

    if open_interest == 0:
        return None

    moneyness = strike / spot
    lo, hi = _moneyness_band(right)
    if moneyness < lo or moneyness > hi:
        return None

    leverage = spot / premium
    if leverage < MIN_LEVERAGE:
        return None

    years = years_to_expiry(dte)
    cagr = implied_cagr_to_breakeven(
        right=right,
        spot=spot,
        strike=strike,
        premium=premium,
        years=years,
    )
    if cagr is None:
        return None

    if right == "call":
        breakeven = round_cents(strike + premium)
        intrinsic = round_cents(max(spot - strike, 0))
    else:
        breakeven = round_cents(strike - premium)
        if breakeven <= 0:
            return None
        intrinsic = round_cents(max(strike - spot, 0))

    extrinsic = round_cents(max(premium - intrinsic, 0))
    extrinsic_yield = (extrinsic / spot) / years if years > 0 else 0.0

    return ScoredContract(
        symbol=symbol.upper(),
        right=right,
        strike=float(strike),
        expiration=expiration,
        premium=premium,
        premium_source=source,
        bid=round_cents(bid) if _positive(bid) is not None else None,
        ask=round_cents(ask) if _positive(ask) is not None else None,
        last=round_cents(last) if _positive(last) is not None else None,
        dte=dte,
        years=years,
        breakeven=breakeven,
        implied_cagr=cagr,
        leverage=leverage,
        intrinsic=intrinsic,
        extrinsic=extrinsic,
        extrinsic_yield=extrinsic_yield,
        spread=spread,
        open_interest=open_interest,
        volume=volume,
        contract_label=format_contract_label(symbol.upper(), expiration, right, strike),
    )


def _sort_key(row: ScoredContract) -> tuple[float, float, float, int, int]:
    # Rank by less |annualized move| so #1 is never “higher CAGR”.
    spread = row.spread if row.spread is not None else float("inf")
    oi = row.open_interest if row.open_interest is not None else -1
    volume = row.volume if row.volume is not None else -1
    return (abs(row.implied_cagr), row.extrinsic_yield, spread, -oi, -volume)


def _richer_reason(winner: ScoredContract, richer: ScoredContract) -> str:
    if winner.right == "call" and winner.strike < richer.strike:
        return "a lower strike"
    if winner.right == "put" and winner.strike > richer.strike:
        return "a higher strike"
    if winner.extrinsic_yield < richer.extrinsic_yield:
        return "less time value per year"
    if winner.dte > richer.dte:
        return "more days to expiry"
    return "a smaller annualized move to break even"


def why_vs_stock(row: ScoredContract, spot: float) -> str:
    debit = format_usd(round_cents(row.premium * CONTRACT_MULTIPLIER))
    stock_notional = format_usd(round_cents(spot * CONTRACT_MULTIPLIER))
    lev = format_leverage(row.leverage)
    extrinsic = format_usd(row.extrinsic)
    ey = format_pct(row.extrinsic_yield)
    spot_txt = format_usd(round_cents(spot))
    move = format_pct(abs(row.implied_cagr))
    if row.right == "put":
        lead = (
            f"Needs a {move} annualized decline in "
            f"{row.symbol} to break even vs spot {spot_txt}."
        )
    elif row.implied_cagr <= 0:
        lead = (
            f"Breaks even at or below today's {row.symbol} price vs owning shares "
            f"at {spot_txt}."
        )
    else:
        lead = (
            f"Needs a {move} annualized move in "
            f"{row.symbol} to break even vs owning shares at {spot_txt}."
        )
    if row.right == "put":
        cost_line = (
            f"This put costs {debit} vs {stock_notional} for 100 shares "
            f"({lev} less capital)."
        )
    else:
        cost_line = (
            f"This LEAP costs {debit} vs {stock_notional} for 100 shares "
            f"({lev} less capital)."
        )
    return f"{lead} {cost_line} Time value is {extrinsic} ({ey} per year)."


def why_vs_richer(row: ScoredContract, richer: Optional[ScoredContract]) -> Optional[str]:
    if richer is None:
        return None
    reason = _richer_reason(row, richer)
    return (
        f"Less annualized move to break even than the {richer.contract_label} "
        f"({format_pct(abs(row.implied_cagr))} vs {format_pct(abs(richer.implied_cagr))}) "
        f"because {reason}."
    )


def scored_to_rank_dict(
    row: ScoredContract,
    rank: int,
    spot: float,
    richer: Optional[ScoredContract],
) -> dict[str, Any]:
    return {
        "rank": rank,
        "contract_label": row.contract_label,
        "symbol": row.symbol,
        "right": row.right,
        "strike": row.strike,
        "expiration": row.expiration,
        "premium": row.premium,
        "premium_source": row.premium_source,
        "bid": row.bid,
        "ask": row.ask,
        "last": row.last,
        "dte": row.dte,
        "implied_cagr": round(row.implied_cagr, 6),
        "leverage": round(row.leverage, 4),
        "intrinsic": row.intrinsic,
        "extrinsic": row.extrinsic,
        "extrinsic_yield": round(row.extrinsic_yield, 6),
        "breakeven": row.breakeven,
        "why_vs_stock": why_vs_stock(row, spot),
        "why_vs_richer": why_vs_richer(row, richer),
    }


def rank_contracts(
    *,
    symbol: str,
    right: str,
    spot: float,
    as_of: date,
    rows: list[dict[str, Any]],
    expiry_from: Optional[date] = None,
    expiry_to: Optional[date] = None,
    top_n: int = MAX_RANKS,
) -> list[ScoredContract]:
    scored: list[ScoredContract] = []
    for raw in rows:
        item = score_contract(
            symbol=symbol,
            right=right,
            spot=spot,
            as_of=as_of,
            expiration=str(raw.get("expiration") or ""),
            strike=float(raw.get("strike") or 0),
            bid=raw.get("bid"),
            ask=raw.get("ask"),
            last=raw.get("last"),
            open_interest=raw.get("open_interest"),
            volume=raw.get("volume"),
            expiry_from=expiry_from,
            expiry_to=expiry_to,
        )
        if item is not None:
            scored.append(item)
    scored.sort(key=_sort_key)
    return scored[:top_n]


def fail_payload(reason: str, symbol: str, right: str) -> dict[str, Any]:
    symbol = symbol.upper()
    if reason == FAIL_NO_QUOTE:
        message = (
            f"Could not fetch a live {symbol} quote. Rankings are hidden so we "
            "do not invent prices."
        )
    elif reason == FAIL_NO_CHAIN:
        message = (
            f"Could not load a live {symbol} option chain for this window. "
            "Rankings are hidden so we do not invent prices."
        )
    else:
        message = (
            f"No quoted long {right} LEAPs in this window met the value filters."
        )
    return {
        "ok": False,
        "reason": reason,
        "message": message,
        "ranks": [],
    }


def rank_from_chain(
    *,
    symbol: str,
    right: str,
    spot: Optional[float],
    as_of: date,
    expiry_from: date,
    expiry_to: date,
    rows: list[dict[str, Any]],
    expirations_used: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    """Build the leap-rank API payload. Fail closed when quotes/chains are missing."""
    symbol = symbol.upper()
    if spot is None or spot <= 0:
        return fail_payload(FAIL_NO_QUOTE, symbol, right)
    if not expirations_used:
        return fail_payload(FAIL_NO_CHAIN, symbol, right)

    top = rank_contracts(
        symbol=symbol,
        right=right,
        spot=spot,
        as_of=as_of,
        rows=rows,
        expiry_from=expiry_from,
        expiry_to=expiry_to,
    )
    if not top:
        return fail_payload(FAIL_NO_CANDIDATES, symbol, right)

    ranks = []
    for index, row in enumerate(top):
        richer = top[index + 1] if index + 1 < len(top) else None
        ranks.append(scored_to_rank_dict(row, index + 1, spot, richer))

    return {
        "ok": True,
        "symbol": symbol,
        "right": right,
        "spot": round_cents(spot),
        "as_of": as_of.isoformat(),
        "expiry_from": expiry_from.isoformat(),
        "expiry_to": expiry_to.isoformat(),
        "expirations_used": list(expirations_used),
        "candidates_considered": len(rows),
        "ranks": ranks,
        "warnings": list(warnings),
    }
