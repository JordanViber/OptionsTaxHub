"""RH chain normalizer, credential isolation, and leap-rank adapter."""

from datetime import date, datetime, timedelta, timezone

import pytest

from leap_rank import rank_contracts, rank_from_chain
from rh_chain import (
    RH_CONNECTION_REQUIRED,
    RH_CONNECTION_REQUIRED_COPY,
    RH_EMPTY_CHAIN,
    RH_NO_EXPIRY_IN_WINDOW,
    RH_SAAS_WALL,
    ChainCache,
    EnvOthRhCredentialStore,
    InMemoryOthRhCredentialStore,
    RhChainError,
    RhCredential,
    StubRhChainClient,
    WallRhChainClient,
    fetch_and_rank_chain,
    normalize_rh_option,
    rank_normalized_chain,
    RawRhChain,
)

AS_OF = date(2026, 9, 7)
IN_WINDOW = date(2027, 9, 7)
TS = datetime(2026, 9, 7, 14, 30, tzinfo=timezone.utc)


def _raw_option(**overrides):
    base = {
        "strike": 90.0,
        "expiry": IN_WINDOW.isoformat(),
        "bid": 11.9,
        "ask": 12.1,
        "last": 12.0,
        "volume": 40,
        "open_interest": 500,
        "quote_timestamp": TS.isoformat(),
    }
    base.update(overrides)
    return base


def test_normalize_prefers_bid_ask_mid():
    row = normalize_rh_option(
        _raw_option(),
        symbol="nvda",
        side="call",
        underlying_price=100.0,
        as_of=AS_OF,
        quote_timestamp=TS,
    )
    assert row is not None
    assert row["price_source"] == "mid"
    assert row["bid"] == pytest.approx(11.9)
    assert row["ask"] == pytest.approx(12.1)
    assert row["dte"] == (IN_WINDOW - AS_OF).days
    assert row["expiration"] == IN_WINDOW.isoformat()
    assert row["quote_timestamp"].startswith("2026-09-07")
    assert row["provider"] == "robinhood"


def test_normalize_falls_back_to_last():
    row = normalize_rh_option(
        _raw_option(bid=None, ask=None, last=4.2),
        symbol="NVDA",
        side="call",
        underlying_price=100.0,
        as_of=AS_OF,
        quote_timestamp=TS,
    )
    assert row is not None
    assert row["price_source"] == "last"
    assert row["last"] == pytest.approx(4.2)


def test_normalize_rejects_crossed_and_invalid():
    kwargs = dict(
        symbol="NVDA",
        side="call",
        underlying_price=100.0,
        as_of=AS_OF,
        quote_timestamp=TS,
    )
    assert normalize_rh_option(_raw_option(bid=12.0, ask=11.0), **kwargs) is None
    assert normalize_rh_option(_raw_option(strike=0), **kwargs) is None
    assert normalize_rh_option(_raw_option(expiry="not-a-date"), **kwargs) is None
    assert normalize_rh_option(_raw_option(bid=None, ask=None, last=None), **kwargs) is None
    assert normalize_rh_option(_raw_option(bid=-1, ask=-2, last=-3), **kwargs) is None


def test_env_store_is_per_user_never_a_shared_fallback(monkeypatch):
    monkeypatch.setenv("OTH_RH_TEST_USER_ID", "oth-test-user")
    monkeypatch.setenv("OTH_RH_TEST_TOKEN", "secret-env-token")
    store = EnvOthRhCredentialStore()
    mine = store.get_for_user("oth-test-user")
    assert mine is not None
    assert mine.user_id == "oth-test-user"
    assert store.get_for_user("jordan-or-trader") is None
    assert store.get_for_user("oth-user-b") is None
    assert store.get_for_user("") is None
    other_rank = fetch_and_rank_chain(
        user_id="jordan-or-trader",
        symbol="NVDA",
        side="call",
        min_expiry=IN_WINDOW,
        max_expiry=IN_WINDOW,
        store=store,
        client=StubRhChainClient(),
        cache=ChainCache(),
        as_of=AS_OF,
    )
    assert other_rank["ok"] is False
    assert other_rank["code"] == RH_CONNECTION_REQUIRED


def test_store_looks_up_only_selected_oth_user():
    store = InMemoryOthRhCredentialStore()
    mine = RhCredential(user_id="oth-user-a", token="secret-a")
    other = RhCredential(user_id="oth-user-b", token="secret-b")
    store.put(mine)
    store.put(other)
    found = store.get_for_user("oth-user-a")
    assert found is not None
    assert found.user_id == "oth-user-a"
    assert store.lookups == ["oth-user-a"]
    assert store.get_for_user("oth-user-z") is None
    assert "secret-a" not in str(store.lookups)


def test_fetch_and_rank_uses_only_that_users_credential():
    store = InMemoryOthRhCredentialStore()
    store.put(RhCredential(user_id="oth-user-a", token="secret-a"))
    client = StubRhChainClient()
    cache = ChainCache()
    payload = fetch_and_rank_chain(
        user_id="oth-user-a",
        symbol="NVDA",
        side="call",
        min_expiry=IN_WINDOW,
        max_expiry=IN_WINDOW + timedelta(days=30),
        store=store,
        client=client,
        cache=cache,
        as_of=AS_OF,
    )
    assert payload["ok"] is True
    assert client.calls == [
        ("oth-user-a", "NVDA", "call", IN_WINDOW, IN_WINDOW + timedelta(days=30))
    ]
    assert store.lookups == ["oth-user-a"]
    assert payload["provider"] == "robinhood"
    assert 1 <= len(payload["ranks"]) <= 3
    assert payload["expirations_used"]


def test_auth_pipeline_is_client_then_normalize_then_leap_rank(monkeypatch):
    """Auth store → RhChainClient.fetch_chain → normalize → leap_rank.rank_from_chain."""
    store = InMemoryOthRhCredentialStore()
    store.put(RhCredential(user_id="oth-user-a", token="secret-a"))
    client = StubRhChainClient(
        options=[
            _raw_option(strike=90.0),
            _raw_option(strike=95.0, bid=12.0, ask=11.0),
            _raw_option(strike=100.0),
        ]
    )
    seen: dict = {}
    real = rank_from_chain

    def spy_rank_from_chain(**kwargs):
        seen["rows"] = list(kwargs["rows"])
        seen["right"] = kwargs["right"]
        seen["symbol"] = kwargs["symbol"]
        return real(**kwargs)

    monkeypatch.setattr("rh_chain.rank_from_chain", spy_rank_from_chain)
    payload = fetch_and_rank_chain(
        user_id="oth-user-a",
        symbol="NVDA",
        side="call",
        min_expiry=IN_WINDOW,
        max_expiry=IN_WINDOW,
        store=store,
        client=client,
        cache=ChainCache(),
        as_of=AS_OF,
    )
    assert client.calls == [
        ("oth-user-a", "NVDA", "call", IN_WINDOW, IN_WINDOW)
    ]
    assert seen["symbol"] == "NVDA"
    assert seen["right"] == "call"
    strikes = [row["strike"] for row in seen["rows"]]
    assert 90.0 in strikes
    assert 100.0 in strikes
    assert 95.0 not in strikes
    assert payload["ok"] is True
    assert 1 <= len(payload["ranks"]) <= 3
    assert payload["ranks"][0]["rank"] == 1


def test_guest_and_unknown_user_are_honest_empty():
    store = InMemoryOthRhCredentialStore()
    client = StubRhChainClient()
    cache = ChainCache()
    guest = fetch_and_rank_chain(
        user_id="",
        symbol="NVDA",
        side="call",
        min_expiry=IN_WINDOW,
        max_expiry=IN_WINDOW,
        store=store,
        client=client,
        cache=cache,
        as_of=AS_OF,
    )
    assert guest["ok"] is False
    assert guest["code"] == RH_CONNECTION_REQUIRED
    assert guest["message"] == RH_CONNECTION_REQUIRED_COPY
    assert guest["ranks"] == []
    assert client.calls == []

    missing = fetch_and_rank_chain(
        user_id="nobody",
        symbol="NVDA",
        side="call",
        min_expiry=IN_WINDOW,
        max_expiry=IN_WINDOW,
        store=store,
        client=client,
        cache=cache,
        as_of=AS_OF,
    )
    assert missing["code"] == RH_CONNECTION_REQUIRED
    assert client.calls == []


def test_ranking_reuses_leap_rank_order_and_caps_at_three():
    raw = RawRhChain(
        symbol="NVDA",
        underlying_price=100.0,
        quote_timestamp=TS,
        options=[
            _raw_option(strike=90.0, bid=11.9, ask=12.1, last=12.0),
            _raw_option(strike=95.0, bid=9.9, ask=10.1, last=10.0),
            _raw_option(strike=100.0, bid=7.9, ask=8.1, last=8.0),
            _raw_option(strike=85.0, bid=15.9, ask=16.1, last=16.0),
        ],
    )
    payload = rank_normalized_chain(
        symbol="NVDA",
        side="call",
        min_expiry=IN_WINDOW,
        max_expiry=IN_WINDOW,
        as_of=AS_OF,
        raw=raw,
    )
    assert payload["ok"] is True
    assert len(payload["ranks"]) == 3
    cagrs = [row["implied_cagr"] for row in payload["ranks"]]
    assert cagrs == sorted(cagrs)
    direct = rank_contracts(
        symbol="NVDA",
        right="call",
        spot=100.0,
        as_of=AS_OF,
        rows=[
            {
                "expiration": IN_WINDOW.isoformat(),
                "strike": 90.0,
                "bid": 11.9,
                "ask": 12.1,
                "last": 12.0,
                "open_interest": 500,
                "volume": 40,
            },
            {
                "expiration": IN_WINDOW.isoformat(),
                "strike": 95.0,
                "bid": 9.9,
                "ask": 10.1,
                "last": 10.0,
                "open_interest": 500,
                "volume": 40,
            },
            {
                "expiration": IN_WINDOW.isoformat(),
                "strike": 100.0,
                "bid": 7.9,
                "ask": 8.1,
                "last": 8.0,
                "open_interest": 500,
                "volume": 40,
            },
            {
                "expiration": IN_WINDOW.isoformat(),
                "strike": 85.0,
                "bid": 15.9,
                "ask": 16.1,
                "last": 16.0,
                "open_interest": 500,
                "volume": 40,
            },
        ],
        expiry_from=IN_WINDOW,
        expiry_to=IN_WINDOW,
    )
    assert [row.strike for row in direct] == [
        payload["ranks"][0]["strike"],
        payload["ranks"][1]["strike"],
        payload["ranks"][2]["strike"],
    ]


def test_fewer_than_three_and_no_expiry_in_window():
    raw = RawRhChain(
        symbol="NVDA",
        underlying_price=100.0,
        quote_timestamp=TS,
        options=[_raw_option(strike=90.0)],
    )
    payload = rank_normalized_chain(
        symbol="NVDA",
        side="call",
        min_expiry=IN_WINDOW,
        max_expiry=IN_WINDOW,
        as_of=AS_OF,
        raw=raw,
    )
    assert payload["ok"] is True
    assert len(payload["ranks"]) == 1

    empty_window = rank_normalized_chain(
        symbol="NVDA",
        side="call",
        min_expiry=date(2028, 1, 1),
        max_expiry=date(2028, 6, 1),
        as_of=AS_OF,
        raw=raw,
    )
    assert empty_window["ok"] is False
    assert empty_window["code"] == RH_NO_EXPIRY_IN_WINDOW


def test_cache_dedupes_and_does_not_store_token():
    store = InMemoryOthRhCredentialStore()
    store.put(RhCredential(user_id="oth-user-a", token="secret-a"))
    client = StubRhChainClient()
    cache = ChainCache(ttl_seconds=60)
    kwargs = dict(
        user_id="oth-user-a",
        symbol="NVDA",
        side="call",
        min_expiry=IN_WINDOW,
        max_expiry=IN_WINDOW,
        store=store,
        client=client,
        cache=cache,
        as_of=AS_OF,
    )
    first = fetch_and_rank_chain(**kwargs)
    second = fetch_and_rank_chain(**kwargs)
    assert first["ok"] is True
    assert second["ok"] is True
    assert len(client.calls) == 1
    cached = cache.get(cache.make_key("oth-user-a", "NVDA", "call", IN_WINDOW, IN_WINDOW))
    assert cached is not None
    assert "secret-a" not in str(cached)
    assert "token" not in str(cached)


def test_saas_wall_and_timeout_are_typed():
    store = InMemoryOthRhCredentialStore()
    store.put(RhCredential(user_id="oth-user-a", token="secret-a"))
    wall = fetch_and_rank_chain(
        user_id="oth-user-a",
        symbol="NVDA",
        side="call",
        min_expiry=IN_WINDOW,
        max_expiry=IN_WINDOW,
        store=store,
        client=WallRhChainClient(),
        cache=ChainCache(),
        as_of=AS_OF,
    )
    assert wall["ok"] is False
    assert wall["code"] == RH_SAAS_WALL
    assert wall["ranks"] == []

    timed = fetch_and_rank_chain(
        user_id="oth-user-a",
        symbol="NVDA",
        side="call",
        min_expiry=IN_WINDOW,
        max_expiry=IN_WINDOW,
        store=store,
        client=StubRhChainClient(error=RhChainError("RH_TIMEOUT", "Robinhood timed out.")),
        cache=ChainCache(),
        as_of=AS_OF,
    )
    assert timed["code"] == "RH_TIMEOUT"

    empty = fetch_and_rank_chain(
        user_id="oth-user-a",
        symbol="NVDA",
        side="call",
        min_expiry=IN_WINDOW,
        max_expiry=IN_WINDOW,
        store=store,
        client=StubRhChainClient(options=[]),
        cache=ChainCache(),
        as_of=AS_OF,
    )
    assert empty["code"] == RH_EMPTY_CHAIN
