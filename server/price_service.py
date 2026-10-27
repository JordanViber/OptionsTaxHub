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
import os
import re
import time

import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import partial
from typing import Any, Callable, Optional, Protocol, TypeVar

logger = logging.getLogger(__name__)

# Cache configuration
CACHE_TTL_SECONDS = 300  # 5-minute cache TTL
YFINANCE_TIMEOUT_SECONDS = 5.0
# One budget for every provider and both stock and option quotes.
QUOTE_FETCH_BUDGET_SECONDS = 5.0

# Keyed quote vendors stay inactive unless the operator sets these.
# This build does not call them; the stubs exist so the order is pluggable.
_KEYED_PROVIDER_ENV: dict[str, tuple[str, ...]] = {
    "alpaca": ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"),
    "tradier": ("TRADIER_ACCESS_TOKEN",),
    "marketdata": ("MARKETDATA_APP_TOKEN",),
    "polygon": ("POLYGON_API_KEY",),
}

_T = TypeVar("_T")


def _call_with_timeout(fn: Callable[[], _T], timeout_seconds: float) -> _T:
    """Run a blocking provider call with a hard timeout.

    Do not ``shutdown(wait=True)``: a never-returning Yahoo thread would
    block the caller until that worker finished. Cancel and return.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as exc:
        future.cancel()
        raise TimeoutError(
            f"Live price fetch timed out after {timeout_seconds:.0f}s"
        ) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Quote:
    """One price with the source that produced it.

    ``live`` carries the provider name and the quote timestamp.
    ``csv`` carries the CSV date in ``as_of`` and no provider.
    """

    symbol: str
    price: float
    source: str
    provider: str | None
    as_of: str


@dataclass(frozen=True)
class ProviderStatus:
    provider: str
    reason: str | None


@dataclass
class PriceFetchResult:
    """Iterable as ``(prices, warnings)`` so existing callers keep working."""

    prices: dict[str, float]
    warnings: list[str]
    quotes: list[Quote]
    provider_statuses: list[ProviderStatus]

    def __iter__(self):
        yield self.prices
        yield self.warnings


# The Robinhood fallback number is the purchase fill, not a market price.
PURCHASE_PRICE_LABEL = "Purchase price from your CSV, no live quote"


def csv_price_label(csv_as_of: str | None) -> str:
    """Desk copy when a CSV states its own price.

    The date is included only when the file has an activity timestamp.
    A blank date must not hide the numbers or invent one.
    """
    if not csv_as_of:
        return "Prices as of your CSV"
    return f"Prices as of your CSV ({csv_as_of})"


def price_basis_from_quotes(
    quotes: list[Quote],
    csv_as_of: str | None,
    fill_fallback: bool = False,
) -> dict[str, Any]:
    """Portfolio provenance. ``live`` is true only when every quote succeeded.

    An unquoted purchase-fill lot is not in ``quotes``. ``fill_fallback``
    keeps that book off the live label.
    """
    live = bool(quotes) and not fill_fallback and all(
        quote.source == "live" and quote.provider and quote.as_of for quote in quotes
    )
    provider: str | None = None
    as_of: str | None = None
    label = ""
    if live:
        providers = {quote.provider for quote in quotes}
        provider = next(iter(providers)) if len(providers) == 1 else None
        as_of = max(quote.as_of for quote in quotes)
    elif fill_fallback:
        label = PURCHASE_PRICE_LABEL
        as_of = None
    else:
        label = csv_price_label(csv_as_of)
        as_of = csv_as_of
    return {
        "quotes": [
            {
                "symbol": quote.symbol,
                "price": quote.price,
                "source": quote.source,
                "provider": quote.provider,
                "as_of": quote.as_of,
            }
            for quote in quotes
        ],
        "live": live,
        "provider": provider,
        "as_of": as_of,
        "csv_as_of": None if live else csv_as_of,
        "label": label,
    }


class QuoteProvider(Protocol):
    name: str

    def enabled(self) -> bool: ...

    def fetch(
        self,
        symbols: list[str],
        timeout_seconds: float,
    ) -> tuple[dict[str, float], list[str], str | None]: ...


def _reason_from_price_warnings(warnings: list[str], got_any: bool) -> str | None:
    if got_any:
        return None
    text = " ".join(warnings).lower()
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "no data" in text or "no_quote" in text:
        return "no_quote"
    if warnings:
        return "error"
    return "no_quote"


class YahooQuoteProvider:
    """Keyless Yahoo Finance quotes via yfinance."""

    name = "yahoo"

    def enabled(self) -> bool:
        return True

    def fetch(
        self,
        symbols: list[str],
        timeout_seconds: float,
    ) -> tuple[dict[str, float], list[str], str | None]:
        if timeout_seconds <= 0:
            return {}, [
                "Quote fetch timed out. Using CSV-provided prices as fallback."
            ], "timeout"
        prices, warnings = _download_yfinance_prices(
            symbols,
            timeout_seconds=timeout_seconds,
        )
        return prices, warnings, _reason_from_price_warnings(warnings, bool(prices))


class _KeyedQuoteProvider:
    """Registered but inactive unless its env vars are set.

    Even when enabled, this build does not call the vendor. No secrets are
    read into logs and no HTTP client is constructed.
    """

    def __init__(self, name: str, env_vars: tuple[str, ...]):
        self.name = name
        self._env_vars = env_vars

    def enabled(self) -> bool:
        return all(os.environ.get(var, "").strip() for var in self._env_vars)

    def fetch(
        self,
        symbols: list[str],
        timeout_seconds: float,
    ) -> tuple[dict[str, float], list[str], str | None]:
        del symbols, timeout_seconds
        return {}, [], "no_quote"


def quote_provider_registry() -> dict[str, QuoteProvider]:
    return {
        "yahoo": YahooQuoteProvider(),
        "alpaca": _KeyedQuoteProvider("alpaca", _KEYED_PROVIDER_ENV["alpaca"]),
        "tradier": _KeyedQuoteProvider("tradier", _KEYED_PROVIDER_ENV["tradier"]),
        "marketdata": _KeyedQuoteProvider(
            "marketdata", _KEYED_PROVIDER_ENV["marketdata"]
        ),
        "polygon": _KeyedQuoteProvider("polygon", _KEYED_PROVIDER_ENV["polygon"]),
    }


def provider_by_name(name: str) -> QuoteProvider | None:
    return quote_provider_registry().get(name.strip().lower())


def active_providers() -> list[QuoteProvider]:
    """Provider order from QUOTE_PROVIDER_ORDER. Keyed names stay off by default."""
    raw = os.environ.get("QUOTE_PROVIDER_ORDER", "yahoo")
    names = [part.strip().lower() for part in raw.split(",") if part.strip()]
    registry = quote_provider_registry()
    selected: list[QuoteProvider] = []
    for name in names:
        provider = registry.get(name)
        if provider is not None and provider.enabled():
            selected.append(provider)
    return selected


def _quote_deadline(
    deadline: float | None,
    budget_seconds: float,
) -> float:
    budget = min(float(budget_seconds), QUOTE_FETCH_BUDGET_SECONDS)
    cap = time.monotonic() + budget
    if deadline is None:
        return cap
    return min(deadline, cap)


# In-memory price cache: {symbol: (price, cached_at, provider, as_of)}
_price_cache: dict[str, tuple] = {}
_option_price_cache: dict[str, tuple] = {}
# Chain snapshots: {(symbol, expiration, right): (rows, timestamp)}
_chain_cache: dict[tuple[str, str, str], tuple[list[dict[str, Any]], float]] = {}

OPTION_LABEL_PATTERN = re.compile(
    r"^(?P<symbol>[A-Z]+)\s+(?P<expiry>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<kind>Call|Put)\s+\$(?P<strike>\d+(?:\.\d+)?)$"
)
MAX_EXPIRATION_FALLBACK_DAYS = 7


def _fresh_cache_entry(cache: dict[str, tuple], key: str) -> Optional[tuple]:
    raw = cache.get(key)
    if not raw:
        return None
    cached_at = raw[1]
    if time.time() - cached_at >= CACHE_TTL_SECONDS:
        return None
    return raw


def _get_cached_price(symbol: str) -> Optional[float]:
    """Get a cached price if it's still fresh."""
    raw = _fresh_cache_entry(_price_cache, symbol)
    if raw is None:
        return None
    return float(raw[0])


def _cached_price_quote(symbol: str) -> Optional[Quote]:
    raw = _fresh_cache_entry(_price_cache, symbol)
    if raw is None:
        return None
    provider = raw[2] if len(raw) > 2 else "yahoo"
    as_of = raw[3] if len(raw) > 3 else ""
    return Quote(symbol, float(raw[0]), "live", str(provider), str(as_of))


def _set_cached_price(
    symbol: str,
    price: float,
    provider: str = "yahoo",
    as_of: str | None = None,
) -> None:
    """Store a live price in the cache."""
    _price_cache[symbol] = (price, time.time(), provider, as_of or _utc_now_iso())


def _get_cached_option_price(contract_label: str) -> Optional[float]:
    """Get a cached option price if it's still fresh."""
    raw = _fresh_cache_entry(_option_price_cache, contract_label)
    if raw is None:
        return None
    return float(raw[0])


def _cached_option_quote(contract_label: str) -> Optional[Quote]:
    raw = _fresh_cache_entry(_option_price_cache, contract_label)
    if raw is None:
        return None
    provider = raw[2] if len(raw) > 2 else "yahoo"
    as_of = raw[3] if len(raw) > 3 else ""
    return Quote(contract_label, float(raw[0]), "live", str(provider), str(as_of))


def _set_cached_option_price(
    contract_label: str,
    price: float,
    provider: str = "yahoo",
    as_of: str | None = None,
) -> None:
    """Store a live option price in the cache."""
    _option_price_cache[contract_label] = (
        price,
        time.time(),
        provider,
        as_of or _utc_now_iso(),
    )


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
            warnings.append(f"Could not parse option contract label: {label}")
            continue

        key = (str(parsed["symbol"]), str(parsed["expiration"]))
        grouped.setdefault(key, []).append(parsed)

    return grouped, warnings


def _load_option_chain(yf: Any, symbol: str, expiration: str):
    """Load one yfinance option chain for a symbol/expiration pair."""
    ticker = yf.Ticker(symbol)
    resolved_expiration, resolution_warning = _resolve_option_chain_expiration(
        ticker,
        symbol,
        expiration,
    )
    chain = ticker.option_chain(resolved_expiration)
    return chain, resolution_warning


def _fetch_grouped_option_prices(
    grouped_contracts: dict[tuple[str, str], list[dict[str, str | float]]],
    deadline: float | None = None,
) -> tuple[dict[str, float], list[str]]:
    """Fetch grouped option prices from yfinance."""
    if not grouped_contracts:
        return {}, []

    if deadline is None:
        deadline = time.monotonic() + QUOTE_FETCH_BUDGET_SECONDS

    prices: dict[str, float] = {}
    warnings: list[str] = []

    try:
        import yfinance as yf
    except ImportError:
        return {}, [
            "yfinance is not installed. Run: pip install yfinance. Using CSV-provided option premiums."
        ]

    timed_out = False
    for (symbol, expiration), contracts in grouped_contracts.items():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            chain, resolution_warning = _call_with_timeout(
                partial(_load_option_chain, yf, symbol, expiration),
                min(remaining, YFINANCE_TIMEOUT_SECONDS),
            )
            if resolution_warning is not None:
                warnings.append(resolution_warning)
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                timed_out = True
                break
            warnings.append(
                f"Could not fetch option prices for {symbol} {expiration}: {str(exc)}"
            )
            continue

        as_of = _utc_now_iso()
        for contract in contracts:
            table = chain.calls if contract["kind"] == "call" else chain.puts
            price = _extract_option_contract_price(table, float(contract["strike"]))
            if price is None:
                continue

            label = str(contract["label"])
            prices[label] = price
            _set_cached_option_price(label, price, provider="yahoo", as_of=as_of)

    if timed_out:
        warnings.append(
            "Quote fetch timed out. Using CSV-provided prices as fallback."
        )

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
            warnings.append(
                f"Using CSV-provided option premium for {label} (quote unavailable)"
            )

    still_missing = [label for label in contract_labels if label not in prices]
    if still_missing:
        warnings.append(
            "No option prices available for: " + ", ".join(still_missing)
        )

    return warnings


def fetch_option_prices(
    contract_labels: list[str],
    fallback_prices: dict[str, float] | None = None,
    allow_network: bool = True,
    csv_as_of: str | None = None,
    budget_seconds: float = QUOTE_FETCH_BUDGET_SECONDS,
    deadline: float | None = None,
) -> PriceFetchResult:
    """Fetch option premiums. Failures fall back to CSV premiums and do not raise."""
    if not contract_labels:
        return _empty_price_result()

    fetch_deadline = _quote_deadline(deadline, budget_seconds)
    try:
        unique_labels = list(dict.fromkeys(contract_labels))
        prices, labels_to_fetch = _resolve_cached_option_prices(unique_labels)
        quotes: list[Quote] = []
        cached_symbols = set(prices)
        for label in prices:
            cached = _cached_option_quote(label)
            if cached is not None:
                quotes.append(cached)
        warnings: list[str] = []
        if allow_network and labels_to_fetch:
            grouped_contracts, parse_warnings = _group_parsed_option_contracts(
                labels_to_fetch
            )
            warnings.extend(parse_warnings)
            fetched_prices, fetch_warnings = _fetch_grouped_option_prices(
                grouped_contracts,
                deadline=fetch_deadline,
            )
            as_of = _utc_now_iso()
            for label, price in fetched_prices.items():
                prices[label] = price
                meta = _cached_option_quote(label)
                quotes.append(
                    meta
                    if meta is not None
                    else Quote(label, price, "live", "yahoo", as_of)
                )
            warnings.extend(fetch_warnings)
        live_symbols = {
            quote.symbol for quote in quotes if quote.symbol not in cached_symbols
        }
        quoted = {quote.symbol for quote in quotes}
        warnings.extend(
            _apply_option_fallback_prices(unique_labels, prices, fallback_prices)
        )
        quotes.extend(_csv_quotes_for_prices(prices, csv_as_of, quoted))
        _log_quote_outcome(
            "option",
            unique_labels,
            prices,
            live_symbols=live_symbols,
            cached_symbols=cached_symbols,
        )
        return PriceFetchResult(prices, warnings, quotes, [])
    except Exception as exc:
        logger.error("option quote fetch failed: %s", exc)
        prices = {}
        if fallback_prices:
            for label in contract_labels:
                if label in fallback_prices:
                    prices[label] = fallback_prices[label]
        quotes = _csv_quotes_for_prices(prices, csv_as_of, set())
        _log_quote_outcome(
            "option",
            list(dict.fromkeys(contract_labels)),
            prices,
            live_symbols=set(),
            cached_symbols=set(),
        )
        return PriceFetchResult(
            prices,
            ["Quote fetch failed. Using CSV-provided prices as fallback."],
            quotes,
            [ProviderStatus("yahoo", "error")],
        )


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


def _close_column_present(data) -> bool:
    columns = getattr(data, "columns", None)
    if columns is None:
        return False
    if isinstance(columns, pd.MultiIndex):
        return "Close" in columns.get_level_values(0)
    return "Close" in columns


def _extract_single_ticker_price(data, symbol: str) -> Optional[float]:
    """Extract the close for one ticker.

    Current yfinance returns a MultiIndex even for a single symbol, so
    ``data["Close"].iloc[-1]`` is a Series. Select that ticker instead of
    calling float() on the row.
    """
    if not _close_column_present(data):
        return None
    close = data["Close"]
    if isinstance(close, pd.DataFrame):
        if symbol in close.columns:
            series = close[symbol]
        elif close.shape[1] == 1:
            series = close.iloc[:, 0]
        else:
            return None
    else:
        series = close
    if isinstance(series, pd.DataFrame):
        series = series.squeeze(axis=1)
    series = series.dropna()
    if getattr(series, "empty", True):
        return None
    value = series.iloc[-1]
    if isinstance(value, pd.Series):
        if symbol in value.index:
            value = value[symbol]
        elif len(value) == 1:
            value = value.iloc[0]
        else:
            return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _log_quote_outcome(
    request: str,
    symbols: list[str],
    prices: dict[str, float],
    *,
    live_symbols: set[str],
    cached_symbols: set[str],
) -> None:
    """One line per quote request: live, cached, fallback, and empty counts."""
    priced = {str(symbol).upper() for symbol in prices}
    live = {symbol.upper() for symbol in live_symbols} & priced
    cached = {symbol.upper() for symbol in cached_symbols} & priced
    fallback = priced - live - cached
    requested = {str(symbol).upper() for symbol in symbols if symbol}
    empty = requested - priced
    logger.info(
        "quote outcome request=%s symbols=%d live=%d cached=%d fallback=%d empty=%d",
        request,
        len(requested),
        len(live),
        len(cached),
        len(fallback),
        len(empty),
    )


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
    timeout_seconds: float = YFINANCE_TIMEOUT_SECONDS,
) -> tuple[dict[str, float], list[str]]:
    """Download prices via yfinance with error handling. Returns (prices, warnings)."""
    prices: dict[str, float] = {}
    warnings: list[str] = []
    timeout_seconds = min(float(timeout_seconds), YFINANCE_TIMEOUT_SECONDS)
    if timeout_seconds <= 0:
        warnings.append(
            "Quote fetch timed out. Using CSV-provided prices as fallback."
        )
        return prices, warnings

    try:
        import yfinance as yf

        data = _call_with_timeout(
            lambda: yf.download(
                tickers=symbols_to_fetch,
                period="1d",
                progress=False,
                threads=True,
            ),
            timeout_seconds,
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
    except TimeoutError as e:
        logger.error(f"yfinance timeout: {e}")
        warnings.append(
            "Quote fetch timed out. Using CSV-provided prices as fallback."
        )
    except Exception as e:
        logger.error(f"yfinance error: {e}")
        warnings.append(
            f"Could not fetch prices from Yahoo Finance: {str(e)}. "
            f"Using CSV-provided prices as fallback."
        )

    return prices, warnings


def _apply_fallback_prices(
    symbols: list[str],
    prices: dict[str, float],
    fallback_prices: dict[str, float] | None,
    csv_as_of: str | None = None,
) -> list[str]:
    """Fill missing prices from fallback dict. Returns list of warnings."""
    warnings: list[str] = []
    if fallback_prices:
        for symbol in symbols:
            upper = symbol.upper()
            if upper not in prices and upper in fallback_prices:
                prices[upper] = fallback_prices[upper]
                if csv_as_of:
                    warnings.append(
                        f"Using CSV-provided price for {upper} "
                        f"as-of {csv_as_of} (quote unavailable)"
                    )
                else:
                    warnings.append(
                        f"Using CSV-provided price for {upper} (quote unavailable)"
                    )

    missing = [s.upper() for s in symbols if s.upper() not in prices]
    if missing:
        warnings.append(f"No prices available for: {', '.join(missing)}")
    return warnings


def _empty_price_result() -> PriceFetchResult:
    return PriceFetchResult({}, [], [], [])


def _csv_quotes_for_prices(
    prices: dict[str, float],
    csv_as_of: str | None,
    already: set[str],
) -> list[Quote]:
    quotes: list[Quote] = []
    for symbol, price in prices.items():
        if symbol in already:
            continue
        quotes.append(Quote(symbol, price, "csv", None, csv_as_of or ""))
    return quotes


def _fetch_current_prices_impl(
    symbols: list[str],
    fallback_prices: dict[str, float] | None,
    *,
    allow_network: bool,
    csv_as_of: str | None,
    deadline: float,
    providers: list[QuoteProvider] | None,
) -> PriceFetchResult:
    unique = list(dict.fromkeys(symbol.upper() for symbol in symbols if symbol))
    prices: dict[str, float] = {}
    quotes: list[Quote] = []
    warnings: list[str] = []
    statuses: list[ProviderStatus] = []

    cached_symbols: set[str] = set()
    live_symbols: set[str] = set()
    for symbol in unique:
        cached = _cached_price_quote(symbol)
        if cached is None:
            continue
        prices[symbol] = cached.price
        quotes.append(cached)
        cached_symbols.add(symbol)

    to_fetch = [symbol for symbol in unique if symbol not in prices]
    if allow_network and to_fetch:
        chain = active_providers() if providers is None else providers
        for provider in chain:
            if not to_fetch:
                break
            if not provider.enabled():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                statuses.append(ProviderStatus(provider.name, "timeout"))
                warnings.append(
                    "Quote fetch timed out. Using CSV-provided prices as fallback."
                )
                break
            try:
                got, provider_warnings, reason = provider.fetch(to_fetch, remaining)
            except Exception as exc:
                logger.error("%s quote provider failed: %s", provider.name, exc)
                statuses.append(ProviderStatus(provider.name, "error"))
                warnings.append(
                    f"Could not fetch prices from {provider.name}. "
                    "Using CSV-provided prices as fallback."
                )
                continue
            warnings.extend(provider_warnings)
            as_of = _utc_now_iso()
            for symbol, price in got.items():
                upper = symbol.upper()
                prices[upper] = price
                _set_cached_price(upper, price, provider=provider.name, as_of=as_of)
                quotes.append(Quote(upper, price, "live", provider.name, as_of))
                live_symbols.add(upper)
            statuses.append(ProviderStatus(provider.name, reason))
            to_fetch = [symbol for symbol in to_fetch if symbol not in prices]
            if reason == "timeout":
                break

    quoted = {quote.symbol for quote in quotes}
    warnings.extend(
        _apply_fallback_prices(unique, prices, fallback_prices, csv_as_of=csv_as_of)
    )
    quotes.extend(_csv_quotes_for_prices(prices, csv_as_of, quoted))
    _log_quote_outcome(
        "stock",
        unique,
        prices,
        live_symbols=live_symbols,
        cached_symbols=cached_symbols,
    )
    return PriceFetchResult(prices, warnings, quotes, statuses)


def fetch_current_prices(
    symbols: list[str],
    fallback_prices: dict[str, float] | None = None,
    allow_network: bool = True,
    csv_as_of: str | None = None,
    budget_seconds: float = QUOTE_FETCH_BUDGET_SECONDS,
    deadline: float | None = None,
    providers: list[QuoteProvider] | None = None,
) -> PriceFetchResult:
    """
    Fetch prices for stock symbols.

    Tries enabled providers in order, then CSV fallbacks. The whole call stays
    inside a 5 second budget. A provider failure returns CSV prices instead of
    raising.

    Args:
        symbols: List of ticker symbols (e.g., ["AAPL", "MSFT"]).
        fallback_prices: Optional dict of symbol->price to use when a quote misses.
        allow_network: When False, skip providers and use cache plus fallback only.
        csv_as_of: ISO date of the CSV those fallback prices came from.
        budget_seconds: Total time allowed for this call, capped at 5 seconds.
        deadline: Shared monotonic deadline so stock and option quotes share one budget.
        providers: Override the configured provider order. Used by tests.

    Returns:
        PriceFetchResult, also iterable as (prices, warnings).
    """
    if not symbols:
        return _empty_price_result()

    fetch_deadline = _quote_deadline(deadline, budget_seconds)
    try:
        return _fetch_current_prices_impl(
            symbols,
            fallback_prices,
            allow_network=allow_network,
            csv_as_of=csv_as_of,
            deadline=fetch_deadline,
            providers=providers,
        )
    except Exception as exc:
        logger.error("quote fetch failed: %s", exc)
        prices: dict[str, float] = {}
        warnings = ["Quote fetch failed. Using CSV-provided prices as fallback."]
        if fallback_prices:
            for symbol in symbols:
                upper = symbol.upper()
                if upper in fallback_prices:
                    prices[upper] = fallback_prices[upper]
        quotes = _csv_quotes_for_prices(prices, csv_as_of, set())
        missing = [symbol.upper() for symbol in symbols if symbol.upper() not in prices]
        if missing:
            warnings.append(f"No prices available for: {', '.join(missing)}")
        _log_quote_outcome(
            "stock",
            list(symbols),
            prices,
            live_symbols=set(),
            cached_symbols=set(),
        )
        return PriceFetchResult(
            prices,
            warnings,
            quotes,
            [ProviderStatus("yahoo", "error")],
        )


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

