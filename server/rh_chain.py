"""Per-user Robinhood option-chain spike.

Resolves only the authenticated OTH user's RH credential, normalizes quotes,
and ranks via existing leap_rank.score. No MCP, no shared trader tokens,
no browser-supplied RH token.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, Protocol

from leap_rank import MIN_DTE, rank_contracts, rank_from_chain, round_cents

logger = logging.getLogger(__name__)

RH_CONNECTION_REQUIRED = "RH_CONNECTION_REQUIRED"
RH_CONNECTION_REQUIRED_COPY = (
    "Connect Robinhood after sign-in for live top-3"
)
RH_TIMEOUT = "RH_TIMEOUT"
RH_RATE_LIMITED = "RH_RATE_LIMITED"
RH_EMPTY_CHAIN = "RH_EMPTY_CHAIN"
RH_NO_EXPIRY_IN_WINDOW = "RH_NO_EXPIRY_IN_WINDOW"
RH_SAAS_WALL = "RH_SAAS_WALL"
RH_MALFORMED = "RH_MALFORMED"
RH_REVOKED = "RH_REVOKED"
RH_OK = "ok"

PROVIDER = "robinhood"
MAX_NORMALIZED_ROWS = 500
CACHE_TTL_SECONDS = 30.0
CHAIN_TIMEOUT_SECONDS = 8.0


class RhChainError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RhCredential:
    user_id: str
    token: str
    expires_at: Optional[datetime] = None
    revoked: bool = False


class OthRhCredentialStore(Protocol):
    def get_for_user(self, oth_user_id: str) -> Optional[RhCredential]:
        """Return that user's RH credential, or None. Never a shared fallback."""


class EnvOthRhCredentialStore:
    """Spike stub: only the configured test user has a credential.

    OTH_RH_TEST_USER_ID / OTH_RH_TEST_TOKEN are process secrets and must
    never be logged or returned to the client.
    """

    def get_for_user(self, oth_user_id: str) -> Optional[RhCredential]:
        if not oth_user_id:
            return None
        test_id = os.environ.get("OTH_RH_TEST_USER_ID", "").strip()
        test_token = os.environ.get("OTH_RH_TEST_TOKEN", "").strip()
        if not test_id or not test_token:
            return None
        if oth_user_id != test_id:
            return None
        return RhCredential(user_id=oth_user_id, token=test_token)


class InMemoryOthRhCredentialStore:
    """Test store keyed only by OTH user id."""

    def __init__(self, credentials: Optional[dict[str, RhCredential]] = None):
        self._credentials = dict(credentials or {})
        self.lookups: list[str] = []

    def put(self, credential: RhCredential) -> None:
        self._credentials[credential.user_id] = credential

    def get_for_user(self, oth_user_id: str) -> Optional[RhCredential]:
        self.lookups.append(oth_user_id)
        if not oth_user_id:
            return None
        cred = self._credentials.get(oth_user_id)
        if cred is None:
            return None
        if cred.revoked:
            raise RhChainError(RH_REVOKED, "Robinhood connection was revoked.")
        if cred.expires_at is not None and cred.expires_at <= datetime.now(
            timezone.utc
        ):
            raise RhChainError(RH_REVOKED, "Robinhood connection expired.")
        return cred


@dataclass
class RawRhChain:
    symbol: str
    underlying_price: float
    quote_timestamp: datetime
    options: list[dict[str, Any]]


class RhChainClient(Protocol):
    def fetch_chain(
        self,
        credential: RhCredential,
        symbol: str,
        side: str,
        min_expiry: date,
        max_expiry: date,
        as_of: Optional[date] = None,
    ) -> RawRhChain:
        """Return raw chain data. Must not expose provider SDK objects."""


def _iso_ts(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # noqa: PLR0124
        return None
    return number


def _as_int(value: Any) -> Optional[int]:
    number = _as_float(value)
    if number is None:
        return None
    return int(number)


def _parse_expiry(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_timestamp(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return fallback


def normalize_rh_option(
    raw: dict[str, Any],
    *,
    symbol: str,
    side: str,
    underlying_price: float,
    as_of: date,
    quote_timestamp: datetime,
) -> Optional[dict[str, Any]]:
    """Normalize one RH option. Reject unusable quotes. Prefer bid/ask mid."""
    if not isinstance(raw, dict):
        return None
    strike = _as_float(raw.get("strike"))
    expiry = _parse_expiry(raw.get("expiry") or raw.get("expiration"))
    if strike is None or strike <= 0 or expiry is None:
        return None
    bid = _as_float(raw.get("bid"))
    ask = _as_float(raw.get("ask"))
    last = _as_float(raw.get("last"))
    if bid is not None and bid <= 0:
        bid = None
    if ask is not None and ask <= 0:
        ask = None
    if last is not None and last <= 0:
        last = None
    if bid is not None and ask is not None and ask < bid:
        return None
    if bid is not None and ask is not None:
        source = "mid"
    elif last is not None:
        source = "last"
    else:
        return None
    dte = (expiry - as_of).days
    ts = _parse_timestamp(raw.get("quote_timestamp") or raw.get("updated_at"), quote_timestamp)
    return {
        "symbol": symbol.upper(),
        "underlying_price": round_cents(underlying_price),
        "side": side,
        "strike": float(strike),
        "expiration": expiry.isoformat(),
        "dte": dte,
        "bid": round_cents(bid) if bid is not None else None,
        "ask": round_cents(ask) if ask is not None else None,
        "last": round_cents(last) if last is not None else None,
        "volume": _as_int(raw.get("volume")),
        "open_interest": _as_int(
            raw.get("open_interest") or raw.get("openInterest")
        ),
        "quote_timestamp": _iso_ts(ts),
        "price_source": source,
        "provider": PROVIDER,
    }


def normalize_rh_chain(
    raw: RawRhChain,
    *,
    side: str,
    min_expiry: date,
    max_expiry: date,
    as_of: date,
) -> tuple[list[dict[str, Any]], list[str]]:
    if raw.underlying_price is None or raw.underlying_price <= 0:
        raise RhChainError(RH_MALFORMED, "Underlying price was unusable.")
    rows: list[dict[str, Any]] = []
    expiries: set[str] = set()
    for item in raw.options or []:
        normalized = normalize_rh_option(
            item,
            symbol=raw.symbol,
            side=side,
            underlying_price=raw.underlying_price,
            as_of=as_of,
            quote_timestamp=raw.quote_timestamp,
        )
        if normalized is None:
            continue
        expiry = date.fromisoformat(normalized["expiration"])
        if expiry < min_expiry or expiry > max_expiry:
            continue
        rows.append(normalized)
        expiries.add(normalized["expiration"])
    return rows, sorted(expiries)


def stub_expiry_in_window(
    min_expiry: date,
    max_expiry: date,
    *,
    as_of: Optional[date] = None,
) -> date:
    """Pick a stub expiry inside [min_expiry, max_expiry].

    min_expiry alone can yield DTE=364 when a Chicago 12–24m window is scored
    against a UTC as_of one calendar day later (MIN_DTE is 365). Use
    as_of+MIN_DTE when as_of is known, else the window midpoint, then clamp.
    Never invent a date outside the window.
    """
    lo, hi = (min_expiry, max_expiry) if min_expiry <= max_expiry else (
        max_expiry,
        min_expiry,
    )
    if as_of is not None:
        target = as_of + timedelta(days=MIN_DTE)
    else:
        target = lo + timedelta(days=(hi - lo).days // 2)
    if target < lo:
        return lo
    if target > hi:
        return hi
    return target


def default_stub_options(
    *,
    side: str,
    min_expiry: date,
    max_expiry: date,
    as_of: Optional[date] = None,
) -> list[dict[str, Any]]:
    """Deterministic in-window chain for the spike stub (not live RH)."""
    in_window = stub_expiry_in_window(min_expiry, max_expiry, as_of=as_of)
    outside = date(in_window.year - 1, in_window.month, min(in_window.day, 28))
    ts = datetime(2026, 9, 7, 14, 30, tzinfo=timezone.utc).isoformat()
    rows = [
        {
            "strike": 90.0,
            "expiry": in_window.isoformat(),
            "bid": 11.9,
            "ask": 12.1,
            "last": 12.0,
            "volume": 40,
            "open_interest": 500,
            "quote_timestamp": ts,
        },
        {
            "strike": 95.0,
            "expiry": in_window.isoformat(),
            "bid": 9.9,
            "ask": 10.1,
            "last": 10.0,
            "volume": 30,
            "open_interest": 400,
            "quote_timestamp": ts,
        },
        {
            "strike": 100.0,
            "expiry": in_window.isoformat(),
            "bid": 7.9,
            "ask": 8.1,
            "last": 8.0,
            "volume": 20,
            "open_interest": 300,
            "quote_timestamp": ts,
        },
        {
            "strike": 105.0,
            "expiry": in_window.isoformat(),
            "bid": 5.9,
            "ask": 6.1,
            "last": 6.0,
            "volume": 10,
            "open_interest": 200,
            "quote_timestamp": ts,
        },
        {
            "strike": 90.0,
            "expiry": outside.isoformat(),
            "bid": 1.0,
            "ask": 1.2,
            "last": 1.1,
            "volume": 1,
            "open_interest": 10,
            "quote_timestamp": ts,
        },
    ]
    for row in rows:
        row["side"] = side
    return rows


class StubRhChainClient:
    """Test/spike client. Never talks to Robinhood or MCP."""

    def __init__(
        self,
        *,
        options: Optional[list[dict[str, Any]]] = None,
        underlying_price: float = 100.0,
        quote_timestamp: Optional[datetime] = None,
        error: Optional[RhChainError] = None,
        as_of: Optional[date] = None,
    ):
        self.options = options
        self.underlying_price = underlying_price
        self.quote_timestamp = quote_timestamp or datetime(
            2026, 9, 7, 14, 30, tzinfo=timezone.utc
        )
        self.error = error
        self.as_of = as_of
        self.calls: list[tuple[str, str, str, date, date]] = []

    def fetch_chain(
        self,
        credential: RhCredential,
        symbol: str,
        side: str,
        min_expiry: date,
        max_expiry: date,
        as_of: Optional[date] = None,
    ) -> RawRhChain:
        # Credential token is intentionally unused beyond identity.
        self.calls.append(
            (credential.user_id, symbol.upper(), side, min_expiry, max_expiry)
        )
        if self.error is not None:
            raise self.error
        options = self.options
        if options is None:
            options = default_stub_options(
                side=side,
                min_expiry=min_expiry,
                max_expiry=max_expiry,
                as_of=as_of if as_of is not None else self.as_of,
            )
        return RawRhChain(
            symbol=symbol.upper(),
            underlying_price=self.underlying_price,
            quote_timestamp=self.quote_timestamp,
            options=list(options),
        )


class WallRhChainClient:
    """Typed wall when RH SaaS is blocked / unavailable under ToS."""

    def fetch_chain(
        self,
        credential: RhCredential,
        symbol: str,
        side: str,
        min_expiry: date,
        max_expiry: date,
        as_of: Optional[date] = None,
    ) -> RawRhChain:
        del credential, symbol, side, min_expiry, max_expiry, as_of
        raise RhChainError(
            RH_SAAS_WALL,
            "Robinhood market-data SaaS is not available for this spike.",
        )


def build_rh_chain_client() -> RhChainClient:
    mode = os.environ.get("OTH_RH_CHAIN_MODE", "stub").strip().lower()
    if mode == "wall" or os.environ.get("OTH_RH_SAAS_WALL", "").strip() == "1":
        return WallRhChainClient()
    return StubRhChainClient()


class ChainCache:
    """Short-lived market-data cache. Never stores RH credentials."""

    def __init__(self, ttl_seconds: float = CACHE_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._data: dict[tuple[str, ...], tuple[float, Any]] = {}
        self._inflight: dict[tuple[str, ...], threading.Event] = {}
        self._lock = threading.Lock()

    @staticmethod
    def make_key(
        user_id: str,
        symbol: str,
        side: str,
        min_expiry: date,
        max_expiry: date,
    ) -> tuple[str, ...]:
        return (
            user_id,
            symbol.upper(),
            side,
            min_expiry.isoformat(),
            max_expiry.isoformat(),
        )

    def get(self, key: tuple[str, ...]) -> Any:
        with self._lock:
            hit = self._data.get(key)
            if not hit:
                return None
            expires, value = hit
            if expires <= time.monotonic():
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: tuple[str, ...], value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + self.ttl_seconds, value)

    def begin(self, key: tuple[str, ...]) -> Optional[threading.Event]:
        """Return an existing in-flight event, or None if this caller should fetch."""
        with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                return existing
            event = threading.Event()
            self._inflight[key] = event
            return None

    def end(self, key: tuple[str, ...]) -> None:
        with self._lock:
            event = self._inflight.pop(key, None)
        if event is not None:
            event.set()

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._inflight.clear()


def fail_rh(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "code": code,
        "reason": code.lower(),
        "message": message,
        "ranks": [],
        "provider": PROVIDER,
    }


def connection_required_payload() -> dict[str, Any]:
    return fail_rh(RH_CONNECTION_REQUIRED, RH_CONNECTION_REQUIRED_COPY)


def _rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for row in rows:
        ranked.append(
            {
                "expiration": row["expiration"],
                "strike": row["strike"],
                "bid": row["bid"],
                "ask": row["ask"],
                "last": row["last"],
                "open_interest": row["open_interest"],
                "volume": row["volume"],
            }
        )
    return ranked


def _prefer_best_rows_before_cap(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    side: str,
    spot: float,
    as_of: date,
    min_expiry: date,
    max_expiry: date,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Bound memory after scoring so provider order cannot drop a better top-3."""
    if len(rows) <= MAX_NORMALIZED_ROWS:
        return rows, []
    top = rank_contracts(
        symbol=symbol,
        right=side,
        spot=spot,
        as_of=as_of,
        rows=_rank_rows(rows),
        expiry_from=min_expiry,
        expiry_to=max_expiry,
        top_n=MAX_NORMALIZED_ROWS,
    )
    kept = [
        {
            "expiration": row.expiration,
            "strike": row.strike,
            "bid": row.bid,
            "ask": row.ask,
            "last": row.last,
            "open_interest": row.open_interest,
            "volume": row.volume,
        }
        for row in top
    ]
    warning = (
        f"Ranked the best-scoring {len(kept)} of {len(rows)} quotes in this window "
        "so top-3 is not cut by provider order."
    )
    return kept, [warning]


def rank_normalized_chain(
    *,
    symbol: str,
    side: str,
    min_expiry: date,
    max_expiry: date,
    as_of: date,
    raw: RawRhChain,
) -> dict[str, Any]:
    rows, expirations = normalize_rh_chain(
        raw,
        side=side,
        min_expiry=min_expiry,
        max_expiry=max_expiry,
        as_of=as_of,
    )
    if not expirations:
        return fail_rh(
            RH_NO_EXPIRY_IN_WINDOW,
            f"No {symbol} expirations fell inside this LEAP window.",
        )
    if not rows:
        return fail_rh(
            RH_EMPTY_CHAIN,
            f"No usable {symbol} {side} quotes in this window.",
        )
    rows, cap_warnings = _prefer_best_rows_before_cap(
        rows,
        symbol=symbol,
        side=side,
        spot=raw.underlying_price,
        as_of=as_of,
        min_expiry=min_expiry,
        max_expiry=max_expiry,
    )
    payload = rank_from_chain(
        symbol=symbol,
        right=side,
        spot=raw.underlying_price,
        as_of=as_of,
        expiry_from=min_expiry,
        expiry_to=max_expiry,
        rows=_rank_rows(rows),
        expirations_used=expirations,
        warnings=cap_warnings,
    )
    payload["provider"] = PROVIDER
    payload["quote_timestamp"] = _iso_ts(raw.quote_timestamp)
    payload["code"] = RH_OK if payload.get("ok") else payload.get("reason") or RH_EMPTY_CHAIN
    if payload.get("ok"):
        payload["code"] = RH_OK
    elif payload.get("reason") == "no_chain":
        return fail_rh(
            RH_NO_EXPIRY_IN_WINDOW,
            payload.get("message") or f"No {symbol} expirations in this window.",
        )
    elif payload.get("reason") == "no_candidates":
        return fail_rh(
            RH_EMPTY_CHAIN,
            payload.get("message")
            or f"No usable {symbol} {side} LEAPs in this window.",
        )
    elif payload.get("reason") == "no_quote":
        return fail_rh(
            RH_MALFORMED,
            payload.get("message") or "Underlying price was unusable.",
        )
    return payload


def fetch_and_rank_chain(
    *,
    user_id: str,
    symbol: str,
    side: str,
    min_expiry: date,
    max_expiry: date,
    store: OthRhCredentialStore,
    client: RhChainClient,
    cache: ChainCache,
    as_of: Optional[date] = None,
) -> dict[str, Any]:
    if not user_id:
        return connection_required_payload()
    try:
        credential = store.get_for_user(user_id)
    except RhChainError as exc:
        return fail_rh(exc.code, exc.message)
    if credential is None:
        logger.info("rh chain: no credential for user lookup (id redacted)")
        return connection_required_payload()

    as_of = as_of or datetime.now(timezone.utc).date()
    key = cache.make_key(user_id, symbol, side, min_expiry, max_expiry)
    cached = cache.get(key)
    if cached is not None:
        return cached

    waiter = cache.begin(key)
    if waiter is not None:
        waiter.wait(timeout=CHAIN_TIMEOUT_SECONDS)
        cached = cache.get(key)
        if cached is not None:
            return cached

    try:
        logger.info("rh chain fetch symbol=%s side=%s", symbol, side)
        raw = client.fetch_chain(
            credential, symbol, side, min_expiry, max_expiry, as_of
        )
        if not raw.options:
            payload = fail_rh(
                RH_EMPTY_CHAIN,
                f"Robinhood returned no {symbol} {side} contracts.",
            )
        else:
            payload = rank_normalized_chain(
                symbol=symbol,
                side=side,
                min_expiry=min_expiry,
                max_expiry=max_expiry,
                as_of=as_of,
                raw=raw,
            )
        cache.set(key, payload)
        return payload
    except RhChainError as exc:
        payload = fail_rh(exc.code, exc.message)
        if exc.code in {RH_SAAS_WALL, RH_RATE_LIMITED, RH_TIMEOUT}:
            cache.set(key, payload)
        return payload
    finally:
        cache.end(key)
