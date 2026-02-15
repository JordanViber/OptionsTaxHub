"""
Price service for OptionsTaxHub.

Fetches current stock prices using yfinance (Yahoo Finance).
Implements in-memory caching with configurable TTL to avoid rate limiting.
Provides graceful fallback when yfinance is unavailable.

DISCLAIMER: Price data is for educational/simulation purposes only.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Cache configuration
CACHE_TTL_SECONDS = 300  # 5-minute cache TTL

# In-memory price cache: {symbol: (price, timestamp)}
_price_cache: dict[str, tuple[float, float]] = {}


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


def clear_cache() -> None:
    """Clear the entire price cache."""
    _price_cache.clear()


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

        if data.empty:
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
