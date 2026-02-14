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


async def fetch_current_prices(
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

    warnings: list[str] = []
    prices: dict[str, float] = {}
    symbols_to_fetch: list[str] = []

    # Check cache first
    for symbol in symbols:
        cached = _get_cached_price(symbol.upper())
        if cached is not None:
            prices[symbol.upper()] = cached
        else:
            symbols_to_fetch.append(symbol.upper())

    if not symbols_to_fetch:
        return prices, warnings

    # Fetch prices via yfinance
    try:
        import yfinance as yf

        # Use yf.download() for batch efficiency
        # Period "1d" gets the most recent trading day's data
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
        else:
            # Extract closing prices
            if len(symbols_to_fetch) == 1:
                # Single ticker — data has simple columns
                symbol = symbols_to_fetch[0]
                if "Close" in data.columns:
                    close_series = data["Close"]
                    if not close_series.empty:
                        price = float(close_series.iloc[-1])
                        prices[symbol] = round(price, 2)
                        _set_cached_price(symbol, prices[symbol])
            else:
                # Multiple tickers — data has MultiIndex columns
                if "Close" in data.columns.get_level_values(0):
                    close_data = data["Close"]
                    for symbol in symbols_to_fetch:
                        if symbol in close_data.columns:
                            series = close_data[symbol].dropna()
                            if not series.empty:
                                price = float(series.iloc[-1])
                                prices[symbol] = round(price, 2)
                                _set_cached_price(symbol, prices[symbol])

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

    # Fill in any missing prices with fallback values
    if fallback_prices:
        for symbol in symbols:
            symbol_upper = symbol.upper()
            if symbol_upper not in prices and symbol_upper in fallback_prices:
                prices[symbol_upper] = fallback_prices[symbol_upper]
                warnings.append(
                    f"Using CSV-provided price for {symbol_upper} "
                    f"(live price unavailable)"
                )

    # Report symbols we couldn't get prices for
    missing = [s.upper() for s in symbols if s.upper() not in prices]
    if missing:
        warnings.append(f"No prices available for: {', '.join(missing)}")

    return prices, warnings


async def fetch_single_price(symbol: str) -> Optional[float]:
    """
    Fetch the current price for a single symbol.

    Args:
        symbol: Ticker symbol (e.g., "AAPL").

    Returns:
        Current price or None if unavailable.
    """
    prices, _ = await fetch_current_prices([symbol])
    return prices.get(symbol.upper())
