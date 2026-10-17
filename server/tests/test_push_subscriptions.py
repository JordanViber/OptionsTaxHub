"""Ownership, secrecy, and delivery rules for web push subscriptions."""

import base64
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pywebpush import WebPushException

import main
from auth import get_current_user
from tests.push_memory import MemorySupabase


USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
ADMIN_KEY = "a" * 32
PAGE_SIZE = 500

client = TestClient(main.app)


def _keys(seed: int) -> dict:
    p256dh = bytes([0x04]) + bytes((seed + index) % 256 for index in range(64))
    auth = bytes((seed * 3 + index) % 256 for index in range(16))
    return {
        "p256dh": base64.urlsafe_b64encode(p256dh).rstrip(b"=").decode(),
        "auth": base64.urlsafe_b64encode(auth).rstrip(b"=").decode(),
    }


def _subscription(endpoint: str, seed: int, **extra) -> dict:
    body = {"endpoint": endpoint, "keys": _keys(seed), "expirationTime": None}
    body.update(extra)
    return body


def _as_user(monkeypatch, user_id: str) -> None:
    monkeypatch.setitem(
        main.app.dependency_overrides,
        get_current_user,
        lambda uid=user_id: uid,
    )


def _send(headers=None):
    return client.post(
        "/push/send",
        json={"title": "Deadline", "body": "Seven days left"},
        headers=headers or {},
    )


@pytest.fixture(autouse=True)
def push_db(monkeypatch):
    store = MemorySupabase()
    try:
        import push_subscriptions as push_mod
    except ImportError:
        push_mod = None
    if push_mod is not None:
        monkeypatch.setattr(push_mod, "get_supabase", lambda: store)
    return store


@pytest.fixture(autouse=True)
def push_transport(monkeypatch):
    calls = []

    def fake_webpush(subscription_info, **kwargs):
        calls.append((subscription_info, kwargs))

    monkeypatch.setattr(main, "webpush", fake_webpush)
    monkeypatch.setattr(main, "VAPID_PRIVATE_KEY", "test-private")
    monkeypatch.setattr(main, "VAPID_PUBLIC_KEY", "test-public")
    _as_user(monkeypatch, USER_A)
    if hasattr(main, "reset_push_rate_limit"):
        main.reset_push_rate_limit()
    return calls


def setup_function():
    if hasattr(main, "reset_push_rate_limit"):
        main.reset_push_rate_limit()
    subscriptions = getattr(main, "push_subscriptions", None)
    if subscriptions is not None:
        subscriptions.clear()


def _seed(store, user_id, endpoint, seed, **overrides):
    keys = _keys(seed)
    row = {
        "id": overrides.get("id", f"{seed:08d}-0000-4000-8000-000000000000"),
        "user_id": user_id,
        "endpoint": endpoint,
        "p256dh": keys["p256dh"],
        "auth": keys["auth"],
        "expiration_time_ms": None,
        "created_at": overrides.get("created_at", f"2026-01-01T00:00:{seed:02d}+00:00"),
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    store.rows.append(row)
    return row


def test_unauthenticated_push_access_is_rejected(monkeypatch, push_transport):
    monkeypatch.delitem(main.app.dependency_overrides, get_current_user, raising=False)
    monkeypatch.delenv("PUSH_ADMIN_KEY", raising=False)
    subscription = _subscription("https://fcm.googleapis.com/fcm/send/device", 1)

    assert client.post("/push/subscribe", json=subscription).status_code == 401
    assert client.post("/push/unsubscribe", json=subscription).status_code == 401
    assert client.get("/push/subscriptions").status_code == 401
    assert client.post("/push/test", json={}).status_code == 401
    assert client.post(
        "/push/test",
        json={"endpoint": subscription["endpoint"]},
    ).status_code == 401

    response = _send()
    assert response.status_code == 503
    assert push_transport == []
    assert subscription["endpoint"] not in response.text
    assert subscription["keys"]["auth"] not in response.text
    assert subscription["keys"]["p256dh"] not in response.text


def test_two_users_with_two_devices_stay_isolated(monkeypatch, push_db, push_transport):
    user_a = [
        _subscription("https://fcm.googleapis.com/fcm/send/a1", 1),
        _subscription("https://fcm.googleapis.com/fcm/send/a2", 2),
    ]
    user_b = [
        _subscription("https://updates.push.services.mozilla.com/wpush/v2/b1", 3),
        _subscription("https://web.push.apple.com/b2", 4),
    ]
    for subscription in user_a:
        assert client.post("/push/subscribe", json=subscription).status_code == 200
    _as_user(monkeypatch, USER_B)
    for subscription in user_b:
        assert client.post("/push/subscribe", json=subscription).status_code == 200

    listed_b = client.get("/push/subscriptions")
    assert listed_b.status_code == 200
    assert listed_b.json()["count"] == 2
    assert "endpoint" not in listed_b.text
    assert "p256dh" not in listed_b.text
    assert "keys" not in listed_b.text
    for subscription in user_a + user_b:
        assert subscription["endpoint"] not in listed_b.text
        assert subscription["keys"]["auth"] not in listed_b.text
        assert subscription["keys"]["p256dh"] not in listed_b.text

    _as_user(monkeypatch, USER_A)
    listed_a = client.get("/push/subscriptions")
    assert listed_a.json()["count"] == 2
    ids_a = {row["id"] for row in listed_a.json()["subscriptions"]}
    ids_b = {row["id"] for row in listed_b.json()["subscriptions"]}
    assert ids_a.isdisjoint(ids_b)
    assert len(ids_a) == 2

    push_transport.clear()
    response = client.post("/push/test", json={})
    assert response.status_code == 200
    assert response.json()["sent"] == 2
    sent = {call[0]["endpoint"] for call in push_transport}
    assert sent == {user_a[0]["endpoint"], user_a[1]["endpoint"]}
    for subscription in user_b:
        assert subscription["endpoint"] not in response.text

    push_transport.clear()
    rejected = client.post("/push/test", json={"endpoint": user_b[0]["endpoint"]})
    assert rejected.status_code == 404
    assert push_transport == []
    assert user_b[0]["endpoint"] not in rejected.text

    blocked = client.post("/push/unsubscribe", json=user_b[0])
    assert blocked.status_code == 200
    assert blocked.json()["message"] == "Subscription not found"
    _as_user(monkeypatch, USER_B)
    assert client.get("/push/subscriptions").json()["count"] == 2

    removed = client.post("/push/unsubscribe", json=user_b[0])
    assert removed.status_code == 200
    assert removed.json()["message"] == "Subscription removed"
    assert client.get("/push/subscriptions").json()["count"] == 1
    assert user_b[0]["endpoint"] not in removed.text


def test_broadcast_requires_server_admin_key_and_hides_subscriptions(
    monkeypatch, push_db, push_transport
):
    _subscription_rows = [
        _subscription("https://fcm.googleapis.com/fcm/send/a1", 1),
        _subscription("https://web.push.apple.com/b1", 2),
    ]
    assert client.post("/push/subscribe", json=_subscription_rows[0]).status_code == 200
    monkeypatch.delenv("PUSH_ADMIN_KEY", raising=False)
    assert _send().status_code == 503
    monkeypatch.setenv("PUSH_ADMIN_KEY", "1")
    assert _send().status_code == 503
    monkeypatch.setenv("PUSH_ADMIN_KEY", ADMIN_KEY)
    assert _send().status_code == 403
    assert _send(headers={"X-Push-Admin-Key": "wrong-key-wrong-key-wrong-key-xx"}).status_code == 403
    non_ascii = SimpleNamespace(headers={"x-push-admin-key": "server-only\u00e9"})
    with pytest.raises(main.HTTPException) as caught:
        main._push_admin_or_error(non_ascii)
    assert caught.value.status_code == 403
    assert push_transport == []

    response = _send(headers={"X-Push-Admin-Key": ADMIN_KEY})
    assert response.status_code == 200
    assert response.json()["sent"] == 1
    assert _subscription_rows[0]["endpoint"] not in response.text
    assert _subscription_rows[0]["keys"]["p256dh"] not in response.text
    delivered = push_transport[0][1]
    assert delivered["ttl"] == 86400
    assert delivered["timeout"] == 10
    assert delivered["requests_session"] is not None


def test_push_http_session_disables_redirects(monkeypatch):
    import requests

    seen = {}

    def fake_post(self, url, *args, **kwargs):
        seen["allow_redirects"] = kwargs.get("allow_redirects", "MISSING")
        return SimpleNamespace(status_code=201, text="", reason="Created", headers={})

    monkeypatch.setattr(requests.Session, "post", fake_post)
    import push_subscriptions

    session = push_subscriptions.push_http_session()
    session.post("https://fcm.googleapis.com/fcm/send/probe")
    assert seen["allow_redirects"] is False


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1/",
        "https://127.0.0.1/fcm/send/abc",
        "https://[::1]/",
        "https://169.254.169.254/latest/meta-data",
        "https://evil.example/push",
        "https://user:pass@fcm.googleapis.com/fcm/send/abc",
        "https://fcm.googleapis.com.evil.com/fcm/send/abc",
        "http://fcm.googleapis.com/fcm/send/abc",
        "https://fcm.googleapis.com/" + ("a" * 2100),
    ],
)
def test_subscribe_rejects_non_push_endpoints(push_db, endpoint):
    response = client.post(
        "/push/subscribe",
        json={"endpoint": endpoint, "keys": _keys(7)},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Push subscription is invalid."
    assert "another" not in response.text.lower()
    assert endpoint not in response.text
    assert push_db.rows == []


def test_subscribe_rejects_malformed_keys_before_storage(push_db):
    response = client.post(
        "/push/subscribe",
        json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/device",
            "keys": {"p256dh": "!!!", "auth": "!!!", "extra": "x" * 5000},
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Push subscription is invalid."
    assert push_db.rows == []
    assert "!!!" not in response.text


def test_allowed_push_hosts_store_owner_and_only_the_two_keys(push_db):
    endpoint = "https://wns2-pn1p.notify.windows.com/w/?token=abc"
    body = _subscription(endpoint, 9)
    body["keys"] = {**body["keys"], "extra": "do-not-store"}
    response = client.post("/push/subscribe", json=body)
    assert response.status_code == 200
    assert response.json() == {"message": "Subscription stored"}
    assert endpoint not in response.text
    assert len(push_db.rows) == 1
    stored = push_db.rows[0]
    assert stored["user_id"] == USER_A
    assert stored["endpoint"] == endpoint
    assert stored["p256dh"] == body["keys"]["p256dh"]
    assert stored["auth"] == body["keys"]["auth"]
    assert "extra" not in stored
    assert "keys" not in stored


def test_same_browser_reassigns_only_when_keys_match(monkeypatch, push_db):
    endpoint = "https://fcm.googleapis.com/fcm/send/shared"
    original = _subscription(endpoint, 4)
    assert client.post("/push/subscribe", json=original).status_code == 200

    _as_user(monkeypatch, USER_B)
    conflict = client.post("/push/subscribe", json=_subscription(endpoint, 5))
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "Push subscription could not be saved."
    assert "another" not in conflict.text.lower()
    assert "account" not in conflict.text.lower()
    assert endpoint not in conflict.text
    assert push_db.rows[0]["user_id"] == USER_A

    transferred = client.post("/push/subscribe", json=original)
    assert transferred.status_code == 200
    assert push_db.rows[0]["user_id"] == USER_B
    _as_user(monkeypatch, USER_A)
    assert client.get("/push/subscriptions").json()["count"] == 0
    _as_user(monkeypatch, USER_B)
    assert client.get("/push/subscriptions").json()["count"] == 1


def test_device_cap_rejects_a_new_device_and_still_refreshes_an_existing_one(push_db):
    for index in range(10):
        _seed(
            push_db,
            USER_A,
            f"https://fcm.googleapis.com/fcm/send/cap-{index}",
            index + 1,
        )
    existing = _subscription("https://fcm.googleapis.com/fcm/send/cap-0", 1)
    assert client.post("/push/subscribe", json=existing).status_code == 200
    fresh = client.post(
        "/push/subscribe",
        json=_subscription("https://fcm.googleapis.com/fcm/send/cap-new", 40),
    )
    assert fresh.status_code == 400
    assert fresh.json()["detail"] == "Too many push devices are registered for this account."
    assert len(push_db.rows) == 10


def test_empty_insert_representation_is_unavailable_not_a_conflict(push_db):
    push_db.insert_returns_empty = True
    response = client.post(
        "/push/subscribe",
        json=_subscription("https://fcm.googleapis.com/fcm/send/empty", 6),
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Could not save push subscription."
    assert "another" not in response.text.lower()


def test_subscribe_and_test_are_rate_limited(push_transport):
    subscription = _subscription("https://fcm.googleapis.com/fcm/send/rate", 8)
    for _ in range(10):
        assert client.post("/push/subscribe", json=subscription).status_code == 200
    blocked = client.post("/push/subscribe", json=subscription)
    assert blocked.status_code == 429
    assert push_transport == []

    main.reset_push_rate_limit()
    for _ in range(10):
        assert client.post("/push/test", json={}).status_code == 200
    assert client.post("/push/test", json={}).status_code == 429


def test_expired_subscriptions_are_pruned_and_other_failures_are_not(
    monkeypatch, push_db, push_transport, caplog
):
    endpoints = {
        "ok": "https://fcm.googleapis.com/fcm/send/ok",
        "missing": "https://fcm.googleapis.com/fcm/send/missing",
        "gone": "https://fcm.googleapis.com/fcm/send/gone",
        "boom": "https://fcm.googleapis.com/fcm/send/boom",
        "bad": "https://fcm.googleapis.com/fcm/send/bad",
    }
    for index, endpoint in enumerate(endpoints.values(), start=1):
        assert client.post(
            "/push/subscribe",
            json=_subscription(endpoint, index),
        ).status_code == 200

    def fake_webpush(subscription_info, **kwargs):
        push_transport.append((subscription_info, kwargs))
        endpoint = subscription_info["endpoint"]
        if endpoint.endswith("/missing"):
            raise WebPushException(
                f"missing {endpoint}",
                response=SimpleNamespace(status_code=404, text=endpoint, headers={}),
            )
        if endpoint.endswith("/gone"):
            raise WebPushException(
                f"gone {endpoint}",
                response=SimpleNamespace(status_code=410, text=endpoint, headers={}),
            )
        if endpoint.endswith("/boom"):
            raise WebPushException(
                f"boom {endpoint}",
                response=SimpleNamespace(status_code=500, text=endpoint, headers={}),
            )
        if endpoint.endswith("/bad"):
            raise ValueError(f"bad {endpoint}")

    monkeypatch.setattr(main, "webpush", fake_webpush)
    monkeypatch.setenv("PUSH_ADMIN_KEY", ADMIN_KEY)
    with caplog.at_level(logging.ERROR):
        response = _send(headers={"X-Push-Admin-Key": ADMIN_KEY})

    assert response.status_code == 200
    assert response.json()["sent"] == 1
    assert response.json()["failed"] == 4
    remaining = {row["endpoint"] for row in push_db.rows}
    assert remaining == {endpoints["ok"], endpoints["boom"], endpoints["bad"]}
    sent = {call[0]["endpoint"] for call in push_transport}
    assert endpoints["ok"] in sent
    assert endpoints["bad"] in sent
    log_text = caplog.text
    for endpoint in endpoints.values():
        assert endpoint not in log_text
    assert "missing" not in log_text
    pruned_ids = [
        row["id"]
        for row in push_db.rows
        if row["endpoint"] in {endpoints["missing"], endpoints["gone"]}
    ]
    assert pruned_ids == []
    kept = [row for row in push_db.rows if row["endpoint"] == endpoints["boom"]]
    assert kept[0]["id"] in log_text


def test_delivery_skips_a_stored_endpoint_outside_the_allowlist(
    monkeypatch, push_db, push_transport
):
    good = _subscription("https://fcm.googleapis.com/fcm/send/good", 12)
    assert client.post("/push/subscribe", json=good).status_code == 200
    evil = "https://evil.example/push"
    rejected = client.post(
        "/push/subscribe",
        json={"endpoint": evil, "keys": _keys(13)},
    )
    assert rejected.status_code == 400
    _seed(push_db, USER_A, evil, 13)
    monkeypatch.setenv("PUSH_ADMIN_KEY", ADMIN_KEY)
    response = _send(headers={"X-Push-Admin-Key": ADMIN_KEY})
    assert response.status_code == 200
    sent = {call[0]["endpoint"] for call in push_transport}
    assert sent == {good["endpoint"]}
    assert any(row["endpoint"] == evil for row in push_db.rows)
    assert evil not in response.text


def test_keyset_pages_keep_every_subscription_when_a_row_is_inserted(push_db):
    import push_subscriptions

    boundary = "2026-06-01T00:00:00+00:00"
    originals = []
    for index in range(PAGE_SIZE - 1):
        created_at = (
            datetime(2026, 6, 2, tzinfo=timezone.utc) + timedelta(seconds=index)
        ).isoformat()
        row = _seed(
            push_db,
            USER_A,
            f"https://fcm.googleapis.com/fcm/send/page-{index}",
            index + 1,
            created_at=created_at,
            id=f"{index + 1:08d}-0000-4000-8000-000000000000",
        )
        originals.append(row["id"])
    high = _seed(
        push_db,
        USER_A,
        "https://fcm.googleapis.com/fcm/send/tie-high",
        PAGE_SIZE,
        created_at=boundary,
        id="ffffffff-ffff-4fff-8fff-ffffffffffff",
    )
    low = _seed(
        push_db,
        USER_A,
        "https://fcm.googleapis.com/fcm/send/tie-low",
        PAGE_SIZE + 1,
        created_at=boundary,
        id="00000000-0000-4000-8000-000000000000",
    )
    originals.extend([high["id"], low["id"]])

    def insert_newer(_store):
        _seed(
            push_db,
            USER_B,
            "https://fcm.googleapis.com/fcm/send/racer",
            90,
            created_at="2026-12-01T00:00:00+00:00",
            id="99999999-9999-4999-8999-999999999999",
        )

    push_db.after_first_page = insert_newer
    listed = push_subscriptions.list_push_subscriptions()
    assert listed is not None
    found = [row["id"] for row in listed]
    assert sorted(found) == sorted(originals)
    assert len(found) == len(set(found))
    assert "99999999-9999-4999-8999-999999999999" not in found
    assert push_db.limits
    assert set(push_db.limits) == {PAGE_SIZE}
    assert push_db.or_filters
    assert low["id"] in push_db.or_filters[0] or high["id"] in push_db.or_filters[0]
    assert boundary in push_db.or_filters[0]


def test_unique_violation_with_different_keys_is_a_conflict_not_a_transfer():
    import push_subscriptions

    other = {
        "id": "33333333-3333-4333-8333-333333333333",
        "user_id": USER_B,
        "endpoint": "https://fcm.googleapis.com/fcm/send/race",
        "p256dh": _keys(2)["p256dh"],
        "auth": _keys(2)["auth"],
        "expiration_time_ms": None,
        "created_at": "2026-01-01T00:00:00+00:00",
    }

    class Scripted:
        def __init__(self):
            self.selects = 0
            self.updates = []
            self.pending = None

        def table(self, _name):
            return self

        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def order(self, *_args, **_kwargs):
            return self

        def or_(self, *_args, **_kwargs):
            return self

        def insert(self, row):
            self.pending = "insert"
            self.updates.append(("insert", row))
            return self

        def update(self, row):
            self.pending = "update"
            self.updates.append(("update", row))
            return self

        def execute(self):
            pending = self.pending
            self.pending = None
            if pending == "insert":
                raise Exception("duplicate key value violates unique constraint")
            self.selects += 1
            if self.selects < 3:
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=[dict(other)])

    scripted = Scripted()
    result = push_subscriptions.save_push_subscription(
        USER_A,
        _subscription(other["endpoint"], 1),
        client=scripted,
    )
    assert result == "conflict"
    assert all(name != "update" for name, _row in scripted.updates)


def test_migration_protects_push_subscriptions_with_rls_and_owner_cascade():
    path = Path(__file__).resolve().parents[1] / "migrations" / "013_push_subscriptions.sql"
    assert path.is_file()
    sql = path.read_text().lower()
    assert "enable row level security" in sql
    assert "revoke all" in sql
    assert "anon" in sql
    assert "authenticated" in sql
    assert "service_role" in sql
    assert "references auth.users" in sql
    assert "on delete cascade" in sql
    assert "like 'https://%'" not in sql
    assert "vgrlucxqncajjdoaoctq" in sql
    assert "not applied" in sql
