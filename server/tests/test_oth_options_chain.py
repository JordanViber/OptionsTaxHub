"""API tests for GET /api/oth/options/chain."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
from auth import get_optional_user
from rh_chain import (
    RH_CONNECTION_REQUIRED,
    RH_CONNECTION_REQUIRED_COPY,
    RH_RATE_LIMITED,
    RH_SAAS_WALL,
    ChainCache,
    InMemoryOthRhCredentialStore,
    RhChainError,
    RhCredential,
    StubRhChainClient,
    WallRhChainClient,
)

client = TestClient(main.app)


def _window():
    start = datetime.now(timezone.utc).date() + timedelta(days=365)
    end = start + timedelta(days=30)
    return start.isoformat(), end.isoformat()


@pytest.fixture(autouse=True)
def _reset_rh(monkeypatch):
    store = InMemoryOthRhCredentialStore()
    chain = StubRhChainClient()
    cache = ChainCache()
    monkeypatch.setattr(main, "rh_credential_store", store)
    monkeypatch.setattr(main, "rh_chain_client", chain)
    monkeypatch.setattr(main, "rh_chain_cache", cache)
    main.reset_guest_leap_rank_quota()
    yield store, chain, cache
    main.app.dependency_overrides[get_optional_user] = lambda: "test-user-123"


def test_guest_honest_empty():
    main.app.dependency_overrides[get_optional_user] = lambda: ""
    start, end = _window()
    response = client.get(
        f"/api/oth/options/chain?symbol=NVDA&side=call&min_expiry={start}&max_expiry={end}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["code"] == RH_CONNECTION_REQUIRED
    assert data["message"] == RH_CONNECTION_REQUIRED_COPY
    assert data["ranks"] == []
    assert "token" not in response.text.lower() or "secret" not in response.text.lower()


def test_authenticated_without_rh_is_honest_empty():
    start, end = _window()
    response = client.get(
        f"/api/oth/options/chain?symbol=NVDA&side=call&min_expiry={start}&max_expiry={end}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == RH_CONNECTION_REQUIRED
    assert data["ranks"] == []


def test_authenticated_connected_user_ranks(monkeypatch):
    store = InMemoryOthRhCredentialStore()
    store.put(RhCredential(user_id="test-user-123", token="secret-a"))
    chain = StubRhChainClient()
    monkeypatch.setattr(main, "rh_credential_store", store)
    monkeypatch.setattr(main, "rh_chain_client", chain)
    start, end = _window()
    response = client.get(
        f"/api/oth/options/chain?symbol=NVDA&side=call&min_expiry={start}&max_expiry={end}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["provider"] == "robinhood"
    assert 1 <= len(data["ranks"]) <= 3
    assert data["expirations_used"]
    assert data["ranks"][0]["why_vs_stock"]
    assert "secret-a" not in response.text
    assert chain.calls[0][0] == "test-user-123"
    assert store.lookups == ["test-user-123"]


def test_rh_status_never_returns_token(monkeypatch):
    store = InMemoryOthRhCredentialStore()
    store.put(RhCredential(user_id="test-user-123", token="secret-a"))
    monkeypatch.setattr(main, "rh_credential_store", store)
    response = client.get("/api/oth/options/rh-status")
    assert response.status_code == 200
    assert response.json() == {"connected": True}
    assert "secret" not in response.text

    main.app.dependency_overrides[get_optional_user] = lambda: ""
    guest = client.get("/api/oth/options/rh-status")
    assert guest.json()["connected"] is False
    assert guest.json()["code"] == RH_CONNECTION_REQUIRED


def test_rejects_browser_token_query_param():
    start, end = _window()
    response = client.get(
        f"/api/oth/options/chain?symbol=NVDA&side=call&min_expiry={start}&max_expiry={end}&token=abc"
    )
    assert response.status_code == 400
    assert "query" in response.json()["detail"].lower()


def test_bounds_and_malformed_query():
    start, end = _window()
    assert client.get(
        f"/api/oth/options/chain?symbol=NVDA%20INC&side=call&min_expiry={start}&max_expiry={end}"
    ).status_code == 400
    assert client.get(
        f"/api/oth/options/chain?symbol=NVDA&side=straddle&min_expiry={start}&max_expiry={end}"
    ).status_code == 400
    assert client.get(
        f"/api/oth/options/chain?symbol=NVDA&side=call&min_expiry={end}&max_expiry={start}"
    ).status_code == 400


def test_timeout_rate_limit_empty_and_wall(monkeypatch):
    store = InMemoryOthRhCredentialStore()
    store.put(RhCredential(user_id="test-user-123", token="secret-a"))
    monkeypatch.setattr(main, "rh_credential_store", store)
    start, end = _window()

    monkeypatch.setattr(
        main,
        "rh_chain_client",
        StubRhChainClient(error=RhChainError("RH_TIMEOUT", "Robinhood timed out.")),
    )
    timed = client.get(
        f"/api/oth/options/chain?symbol=NVDA&side=call&min_expiry={start}&max_expiry={end}"
    )
    assert timed.json()["code"] == "RH_TIMEOUT"

    monkeypatch.setattr(main, "rh_chain_cache", ChainCache())
    monkeypatch.setattr(
        main,
        "rh_chain_client",
        StubRhChainClient(
            error=RhChainError(RH_RATE_LIMITED, "Robinhood rate limited this request.")
        ),
    )
    limited = client.get(
        f"/api/oth/options/chain?symbol=NVDA&side=call&min_expiry={start}&max_expiry={end}"
    )
    assert limited.json()["code"] == RH_RATE_LIMITED

    monkeypatch.setattr(main, "rh_chain_cache", ChainCache())
    monkeypatch.setattr(main, "rh_chain_client", StubRhChainClient(options=[]))
    empty = client.get(
        f"/api/oth/options/chain?symbol=NVDA&side=call&min_expiry={start}&max_expiry={end}"
    )
    assert empty.json()["code"] == "RH_EMPTY_CHAIN"

    monkeypatch.setattr(main, "rh_chain_cache", ChainCache())
    monkeypatch.setattr(main, "rh_chain_client", WallRhChainClient())
    wall = client.get(
        f"/api/oth/options/chain?symbol=NVDA&side=call&min_expiry={start}&max_expiry={end}"
    )
    assert wall.json()["code"] == RH_SAAS_WALL
    assert wall.json()["ok"] is False
