"""
Price service for OptionsTaxHub.

Fetches current stock and option prices using yfinance (Yahoo Finance).
Implements in-memory caching with configurable TTL to avoid rate limiting.
Provides graceful fallback when live quotes are unavailable.

DISCLAIMER: Price data is for educational/simulation purposes only.
"""

from __future__ import annotations

import logging
import math
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Cache configuration
CACHE_TTL_SECONDS = 300  # 5-minute cache TTL

# In-memory price cache: {symbol: (price, timestamp)}
_price_cache: dict[str, tuple[float, float]] = {}
_option_price_cache: dict[str, tuple[float, float]] = {}
# Chain snapshots: {(symbol, expiration, right): (rows, timestamp)}
_chain_cache: dict[tuple[str, str, str], tuple[list[dict[str, Any]], float]] = {}

OPTION_LABEL_PATTERN = re.compile(
    r"^(?P<symbol>[A-Z]+)\s+(?P<expiry>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<kind>Call|Put)\s+\$(?P<strike>\d+(?:\.\d+)?)$"
)
MAX_EXPIRATION_FALLBACK_DAYS = 7


def _get_cached_price(symbol: str) -> Optional[float]:
    """Get a cached price if it's still fresh."""
    if symbol in _price_cache:
        price, cached_at = _price_cache[symbol]
        if time.time() - cached_at < CACHE_TTL_SECONDS:
            return price
    return None


def _set_cached_price(symbol: str, price: float) -> None:
    """Store a price in the cache."""
    _price_cache[symbol] = (price, time.time())


def _get_cached_option_price(contract_label: str) -> Optional[float]:
    """Get a cached option price if it's still fresh."""
    if contract_label in _option_price_cache:
        price, cached_at = _option_price_cache[contract_label]
        if time.time() - cached_at < CACHE_TTL_SECONDS:
            return price
    return None


def _set_cached_option_price(contract_label: str, price: float) -> None:
    """Store an option price in the cache."""
    _option_price_cache[contract_label] = (price, time.time())


def clear_cache() -> None:
    """Clear the entire price cache."""
    _price_cache.clear()
    _option_price_cache.clear()
    _chain_cache.clear()


def _coerce_price(value) -> Optional[float]:
    """Convert a candidate quote field to a rounded float when possible."""
    if value is None:
        return None

    try:
        price = float(value)
    except (TypeError, ValueError):
        return None

    if price <= 0:
        return None
    return round(price, 2)


def _parse_option_contract_label(contract_label: str) -> Optional[dict[str, str | float]]:
    """Parse an option display label like 'TSLA 3/16/2026 Put $375.00'."""
    match = OPTION_LABEL_PATTERN.match(contract_label.strip())
    if not match:
        return None

    expiry = datetime.strptime(match.group("expiry"), "%m/%d/%Y").date().isoformat()
    return {
        "symbol": match.group("symbol"),
        "expiration": expiry,
        "kind": match.group("kind").lower(),
        "strike": float(match.group("strike")),
        "label": contract_label,
    }


def _parse_iso_expiration(value: str) -> Optional[date]:
    """Parse an ISO option expiration string into a date."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _nearest_available_expiration(
    requested_expiration: str,
    available_expirations: list[str],
) -> Optional[str]:
    """Return the closest listed expiration when the requested one is unavailable."""
    requested_date = _parse_iso_expiration(requested_expiration)
    if requested_date is None:
        return None

    dated_expirations: list[tuple[int, date, str]] = []
    for expiration in available_expirations:
        expiration_date = _parse_iso_expiration(expiration)
        if expiration_date is None:
            continue

        delta_days = abs((expiration_date - requested_date).days)
        if delta_days <= MAX_EXPIRATION_FALLBACK_DAYS:
            dated_expirations.append((delta_days, expiration_date, expiration))

    if not dated_expirations:
        return None

    dated_expirations.sort(key=lambda item: (item[0], item[1]))
    return dated_expirations[0][2]


def _resolve_option_chain_expiration(
    ticker,
    symbol: str,
    requested_expiration: str,
) -> tuple[str, Optional[str]]:
    """Resolve a requested expiration to an available listed expiration when possible."""
    available_expirations = list(getattr(ticker, "options", ()) or [])
    if not available_expirations or requested_expiration in available_expirations:
        return requested_expiration, None

    resolved_expiration = _nearest_available_expiration(
        requested_expiration,
        available_expirations,
    )
    if resolved_expiration is None:
        return requested_expiration, None

    return (
        resolved_expiration,
        f"Used listed option expiration {resolved_expiration} for {symbol} instead of unavailable {requested_expiration}.",
    )


def _resolve_cached_option_prices(
    contract_labels: list[str],
) -> tuple[dict[str, float], list[str]]:
    """Separate option labels into cached hits and cache misses."""
    prices: dict[str, float] = {}
    to_fetch: list[str] = []
    for label in contract_labels:
        cached = _get_cached_option_price(label)
        if cached is not None:
            prices[label] = cached
        else:
            to_fetch.append(label)
    return prices, to_fetch


def _extract_option_contract_price(option_table, strike: float) -> Optional[float]:
    """Extract a quoted premium for a single strike from an option chain table."""
    if option_table is None or getattr(option_table, "empty", True):
        return None
    if "strike" not in option_table.columns:
        return None

    strike_rows = option_table[option_table["strike"].astype(float).sub(strike).abs() < 0.001]
    if strike_rows.empty:
        return None

    row = strike_rows.iloc[0]
    for field in ("lastPrice", "ask", "bid"):
        price = _coerce_price(row.get(field))
        if price is not None:
            return price

    bid = _coerce_price(row.get("bid"))
    ask = _coerce_price(row.get("ask"))
    if bid is not None and ask is not None:
        return round((bid + ask) / 2, 2)

    return None


def _group_parsed_option_contracts(
    labels_to_fetch: list[str],
) -> tuple[dict[tuple[str, str], list[dict[str, str | float]]], list[str]]:
    """Parse and group option labels by underlying symbol and expiration."""
    grouped: dict[tuple[str, str], list[dict[str, str | float]]] = {}
    warnings: list[str] = []

    for label in labels_to_fetch:
        parsed = _parse_option_contract_label(label)
        if parsed is None:
            warnings.append(f"Could not parse option contract label for live pricing: {label}")
            continue

        key = (str(parsed["symbol"]), str(parsed["expiration"]))
        grouped.setdefault(key, []).append(parsed)

    return grouped, warnings


def _fetch_grouped_option_prices(
    grouped_contracts: dict[tuple[str, str], list[dict[str, str | float]]],
) -> tuple[dict[str, float], list[str]]:
    """Fetch grouped option prices from yfinance."""
    if not grouped_contracts:
        return {}, []

    prices: dict[str, float] = {}
    warnings: list[str] = []

    try:
        import yfinance as yf
    except ImportError:
        return {}, [
            "yfinance is not installed. Run: pip install yfinance. Using CSV-provided option premiums."
        ]

    for (symbol, expiration), contracts in grouped_contracts.items():
        try:
            ticker = yf.Ticker(symbol)
            resolved_expiration, resolution_warning = _resolve_option_chain_expiration(
                ticker,
                symbol,
                expiration,
            )
            if resolution_warning is not None:
                warnings.append(resolution_warning)

            chain = ticker.option_chain(resolved_expiration)
        except Exception as exc:
            warnings.append(
                f"Could not fetch live option prices for {symbol} {expiration}: {str(exc)}"
            )
            continue

        for contract in contracts:
            table = chain.calls if contract["kind"] == "call" else chain.puts
            price = _extract_option_contract_price(table, float(contract["strike"]))
            if price is None:
                continue

            label = str(contract["label"])
            prices[label] = price
            _set_cached_option_price(label, price)

    return prices, warnings


def _apply_option_fallback_prices(
    contract_labels: list[str],
    prices: dict[str, float],
    fallback_prices: dict[str, float] | None,
) -> list[str]:
    """Fill missing option prices from fallbacks and return warnings."""
    warnings: list[str] = []
    for label in contract_labels:
        if label in prices:
            continue
        if fallback_prices and label in fallback_prices:
            prices[label] = fallback_prices[label]
            warnings.append(f"Using CSV-provided option premium for {label} (live quote unavailable)")

    still_missing = [label for label in contract_labels if label not in prices]
    if still_missing:
        warnings.append(
            "No live option prices available for: " + ", ".join(still_missing)
        )

    return warnings


def fetch_option_prices(
    contract_labels: list[str],
    fallback_prices: dict[str, float] | None = None,
) -> tuple[dict[str, float], list[str]]:
    """Fetch current option prices for parsed contract labels via yfinance."""
    if not contract_labels:
        return {}, []

    unique_labels = list(dict.fromkeys(contract_labels))
    prices, labels_to_fetch = _resolve_cached_option_prices(unique_labels)
    grouped_contracts, warnings = _group_parsed_option_contracts(labels_to_fetch)
    fetched_prices, fetch_warnings = _fetch_grouped_option_prices(grouped_contracts)
    prices.update(fetched_prices)
    warnings.extend(fetch_warnings)
    warnings.extend(_apply_option_fallback_prices(unique_labels, prices, fallback_prices))

    return prices, warnings


def _resolve_cached_prices(
    symbols: list[str],
) -> tuple[dict[str, float], list[str]]:
    """Separate symbols into cached hits and cache misses."""
    prices: dict[str, float] = {}
    to_fetch: list[str] = []
    for symbol in symbols:
        cached = _get_cached_price(symbol.upper())
        if cached is not None:
            prices[symbol.upper()] = cached
        else:
            to_fetch.append(symbol.upper())
    return prices, to_fetch


def _extract_single_ticker_price(data, _symbol: str) -> Optional[float]:
    """Extract closing price from yfinance data for a single-ticker download."""
    if "Close" not in data.columns:
        return None
    close_series = data["Close"]
    if close_series.empty:
        return None
    return round(float(close_series.iloc[-1]), 2)


def _extract_multi_ticker_prices(
    data, symbols: list[str],
) -> dict[str, float]:
    """Extract closing prices from yfinance data for a multi-ticker download."""
    prices: dict[str, float] = {}
    if "Close" not in data.columns.get_level_values(0):
        return prices
    close_data = data["Close"]
    for symbol in symbols:
        if symbol not in close_data.columns:
            continue
        series = close_data[symbol].dropna()
        if series.empty:
            continue
        prices[symbol] = round(float(series.iloc[-1]), 2)
    return prices


def _download_yfinance_prices(
    symbols_to_fetch: list[str],
) -> tuple[dict[str, float], list[str]]:
    """Download prices via yfinance with error handling. Returns (prices, warnings)."""
    prices: dict[str, float] = {}
    warnings: list[str] = []

    try:
        import yfinance as yf

        data = yf.download(
            tickers=symbols_to_fetch,
            period="1d",
            progress=False,
            threads=True,
        )

        if data is None or data.empty:
            warnings.append(
                "yfinance returned no data. Using CSV-provided prices as fallback."
            )
            return prices, warnings

        if len(symbols_to_fetch) == 1:
            price = _extract_single_ticker_price(data, symbols_to_fetch[0])
            if price is not None:
                prices[symbols_to_fetch[0]] = price
                _set_cached_price(symbols_to_fetch[0], price)
        else:
            fetched = _extract_multi_ticker_prices(data, symbols_to_fetch)
            for sym, price in fetched.items():
                prices[sym] = price
                _set_cached_price(sym, price)

    except ImportError:
        warnings.append(
            "yfinance is not installed. Run: pip install yfinance. "
            "Using CSV-provided prices."
        )
    except Exception as e:
        logger.error(f"yfinance error: {e}")
        warnings.append(
            f"Could not fetch live prices from Yahoo Finance: {str(e)}. "
            f"Using CSV-provided prices as fallback."
        )

    return prices, warnings


def _apply_fallback_prices(
    symbols: list[str],
    prices: dict[str, float],
    fallback_prices: dict[str, float] | None,
) -> list[str]:
    """Fill missing prices from fallback dict. Returns list of warnings."""
    warnings: list[str] = []
    if fallback_prices:
        for symbol in symbols:
            upper = symbol.upper()
            if upper not in prices and upper in fallback_prices:
                prices[upper] = fallback_prices[upper]
                warnings.append(
                    f"Using CSV-provided price for {upper} "
                    f"(live price unavailable)"
                )

    missing = [s.upper() for s in symbols if s.upper() not in prices]
    if missing:
        warnings.append(f"No prices available for: {', '.join(missing)}")
    return warnings


def fetch_current_prices(
    symbols: list[str],
    fallback_prices: dict[str, float] | None = None,
) -> tuple[dict[str, float], list[str]]:
    """
    Fetch current prices for a list of stock symbols using yfinance.

    Uses batch downloading for efficiency and caches results for 5 minutes.
    Falls back to provided prices if yfinance is unavailable.

    Args:
        symbols: List of ticker symbols (e.g., ["AAPL", "MSFT"]).
        fallback_prices: Optional dict of symbol->price to use if yfinance fails.

    Returns:
        Tuple of (dict mapping symbol to current price, list of warnings).
    """
    if not symbols:
        return {}, []

    prices, symbols_to_fetch = _resolve_cached_prices(symbols)
    if not symbols_to_fetch:
        return prices, []

    fetched, warnings = _download_yfinance_prices(symbols_to_fetch)
    prices.update(fetched)

    fallback_warnings = _apply_fallback_prices(symbols, prices, fallback_prices)
    warnings.extend(fallback_warnings)

    return prices, warnings


def fetch_single_price(symbol: str) -> Optional[float]:
    """
    Fetch the current price for a single symbol.

    Args:
        symbol: Ticker symbol (e.g., "AAPL").

    Returns:
        Current price or None if unavailable.
    """
    prices, _ = fetch_current_prices([symbol])
    return prices.get(symbol.upper())


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _coerce_open_interest(value) -> Optional[int]:
    if value is None:
        return None
    try:
        if value != value:  # NaN
            return None
    except (TypeError, ValueError):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number)


def listed_expirations_in_window(
    available: list[str],
    expiry_from: str,
    expiry_to: str,
    as_of: str,
) -> list[str]:
    """Filter ISO listed expirations to [from, to] and not before as_of.

    Every listed date in the window is kept so ranking is the true top 3,
    not a sampled subset. No harvest 7-day snap.
    """
    as_of_date = _parse_iso_expiration(as_of)
    start = _parse_iso_expiration(expiry_from)
    end = _parse_iso_expiration(expiry_to)
    if as_of_date is None or start is None or end is None:
        return []

    in_window: list[str] = []
    for expiration in available:
        expiration_date = _parse_iso_expiration(expiration)
        if expiration_date is None:
            continue
        if expiration_date < as_of_date:
            continue
        if expiration_date < start or expiration_date > end:
            continue
        in_window.append(expiration)
    in_window.sort()
    return in_window


def _get_cached_chain(
    symbol: str,
    expiration: str,
    right: str,
) -> Optional[list[dict[str, Any]]]:
    key = (symbol.upper(), expiration, right)
    if key not in _chain_cache:
        return None
    rows, cached_at = _chain_cache[key]
    if time.time() - cached_at >= CACHE_TTL_SECONDS:
        return None
    return [dict(row) for row in rows]


def _set_cached_chain(
    symbol: str,
    expiration: str,
    right: str,
    rows: list[dict[str, Any]],
) -> None:
    _chain_cache[(symbol.upper(), expiration, right)] = (
        [dict(row) for row in rows],
        time.time(),
    )


def _rows_from_option_table(table, expiration: str) -> list[dict[str, Any]]:
    if table is None or getattr(table, "empty", True):
        return []
    if "strike" not in table.columns:
        return []

    has_oi = "openInterest" in table.columns
    has_volume = "volume" in table.columns
    rows: list[dict[str, Any]] = []
    for _, row in table.iterrows():
        strike = _coerce_price(row.get("strike"))
        if strike is None:
            continue
        item: dict[str, Any] = {
            "strike": strike,
            "expiration": expiration,
            "bid": _coerce_price(row.get("bid")) if "bid" in table.columns else None,
            "ask": _coerce_price(row.get("ask")) if "ask" in table.columns else None,
            "last": (
                _coerce_price(row.get("lastPrice"))
                if "lastPrice" in table.columns
                else None
            ),
            "open_interest": (
                _coerce_open_interest(row.get("openInterest")) if has_oi else None
            ),
            "volume": (
                _coerce_open_interest(row.get("volume")) if has_volume else None
            ),
        }
        rows.append(item)
    return rows


def fetch_option_chain_window(
    symbol: str,
    right: str,
    expiry_from: str,
    expiry_to: str,
    as_of: Optional[str] = None,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """
    Fetch listed option contracts in an expiry window.

    Scores every listed expiration in the window (no silent sample/cap).
    Does not snap to a nearest expiration (harvest-only behavior).
    Failed expirations are skipped; the rest are still ranked.
    """
    symbol = symbol.upper().strip()
    right = right.lower().strip()
    if right not in ("call", "put"):
        return [], [], [f"Invalid option right: {right}"]

    as_of_iso = as_of or _utc_today().isoformat()
    warnings: list[str] = []

    try:
        import yfinance as yf
    except ImportError:
        return [], [], [
            "yfinance is not installed. Run: pip install yfinance."
        ]

    try:
        ticker = yf.Ticker(symbol)
        available = list(getattr(ticker, "options", ()) or [])
    except Exception as exc:
        return [], [], [f"Could not load a live {symbol} option chain: {str(exc)}"]

    selected = listed_expirations_in_window(
        available, expiry_from, expiry_to, as_of_iso,
    )
    if not selected:
        return [], [], [f"No listed expirations in this window for {symbol}."]

    rows: list[dict[str, Any]] = []
    expirations_used: list[str] = []

    for expiration in selected:
        cached = _get_cached_chain(symbol, expiration, right)
        if cached is not None:
            rows.extend(cached)
            expirations_used.append(expiration)
            continue
        try:
            chain = ticker.option_chain(expiration)
        except Exception as exc:
            warnings.append(
                f"Could not fetch live option chain for {symbol} {expiration}: {str(exc)}"
            )
            continue

        table = chain.calls if right == "call" else chain.puts
        fetched = _rows_from_option_table(table, expiration)
        _set_cached_chain(symbol, expiration, right, fetched)
        rows.extend(fetched)
        expirations_used.append(expiration)

    if not expirations_used:
        if not warnings:
            warnings.append(
                f"Could not load a live {symbol} option chain for this window."
            )
        return [], [], warnings

    return rows, expirations_used, warnings

