from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def setup_function():
    main.push_subscriptions.clear()


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_csv_returns_first_five_rows():
    csv_content = """symbol,qty,price
AAPL,10,150
MSFT,5,310
TSLA,2,220
AMZN,1,130
GOOGL,3,140
NVDA,4,450
"""
    files = {"file": ("test.csv", csv_content, "text/csv")}
    response = client.post("/upload-csv", files=files)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 5
    assert data[0] == {"symbol": "AAPL", "qty": 10, "price": 150}
    assert data[-1] == {"symbol": "GOOGL", "qty": 3, "price": 140}


def test_push_subscribe_and_list():
    subscription = {
        "endpoint": "https://example.com/endpoint",
        "keys": {"p256dh": "key", "auth": "auth"},
    }

    response = client.post("/push/subscribe", json=subscription)
    assert response.status_code == 200
    assert response.json() == {"message": "Subscription stored", "count": 1}

    response = client.post("/push/subscribe", json=subscription)
    assert response.status_code == 200
    assert response.json() == {"message": "Subscription already exists", "count": 1}

    response = client.get("/push/subscriptions")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["subscriptions"][0]["endpoint"] == "https://example.com/endpoint"


def test_push_unsubscribe_flow():
    subscription = {
        "endpoint": "https://example.com/endpoint",
        "keys": {"p256dh": "key", "auth": "auth"},
    }

    client.post("/push/subscribe", json=subscription)

    response = client.post("/push/unsubscribe", json=subscription)
    assert response.status_code == 200
    assert response.json() == {"message": "Subscription removed", "count": 0}

    response = client.post("/push/unsubscribe", json=subscription)
    assert response.status_code == 200
    assert response.json() == {"message": "Subscription not found", "count": 0}


def test_send_push_notification_missing_vapid_keys():
    original_private = main.VAPID_PRIVATE_KEY
    original_public = main.VAPID_PUBLIC_KEY
    main.VAPID_PRIVATE_KEY = None
    main.VAPID_PUBLIC_KEY = None

    response = client.post(
        "/push/send",
        json={"title": "Test", "body": "Body"},
    )

    assert response.status_code == 200
    assert response.json()["error"] == "VAPID keys not configured"

    main.VAPID_PRIVATE_KEY = original_private
    main.VAPID_PUBLIC_KEY = original_public


def test_send_push_notification_success_and_expired_cleanup():
    class DummyResponse:
        status_code = 410

    class DummyWebPushException(Exception):
        def __init__(self):
            self.response = DummyResponse()

    original_private = main.VAPID_PRIVATE_KEY
    original_public = main.VAPID_PUBLIC_KEY
    original_webpush = main.webpush
    original_exception = main.WebPushException

    main.VAPID_PRIVATE_KEY = "private"
    main.VAPID_PUBLIC_KEY = "public"

    def fake_webpush(subscription_info, **_kwargs):
        if subscription_info["endpoint"] == "https://example.com/gone":
            raise DummyWebPushException()

    main.webpush = fake_webpush
    main.WebPushException = DummyWebPushException

    main.push_subscriptions.extend(
        [
            {"endpoint": "https://example.com/ok", "keys": {"p256dh": "k", "auth": "a"}},
            {"endpoint": "https://example.com/gone", "keys": {"p256dh": "k", "auth": "a"}},
        ]
    )

    response = client.post(
        "/push/send",
        json={"title": "Test", "body": "Body"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sent"] == 1
    assert payload["failed"] == 1
    assert payload["total_subscriptions"] == 1

    main.VAPID_PRIVATE_KEY = original_private
    main.VAPID_PUBLIC_KEY = original_public
    main.webpush = original_webpush
    main.WebPushException = original_exception


def test_push_test_endpoint():
    original_private = main.VAPID_PRIVATE_KEY
    original_public = main.VAPID_PUBLIC_KEY
    original_webpush = main.webpush

    main.VAPID_PRIVATE_KEY = "private"
    main.VAPID_PUBLIC_KEY = "public"

    def fake_webpush(**_kwargs):
        return None

    main.webpush = fake_webpush

    main.push_subscriptions.append(
        {"endpoint": "https://example.com/ok", "keys": {"p256dh": "k", "auth": "a"}}
    )

    response = client.post("/push/test")
    assert response.status_code == 200
    payload = response.json()
    assert payload["sent"] == 1

    main.VAPID_PRIVATE_KEY = original_private
    main.VAPID_PUBLIC_KEY = original_public
    main.webpush = original_webpush


def test_run_invokes_uvicorn(monkeypatch):
    import uvicorn

    captured = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("PORT", "9090")

    main.run()

    assert captured["args"] == ("main:app",)
    assert captured["kwargs"]["host"] == "0.0.0.0"
    assert captured["kwargs"]["port"] == 9090
    assert captured["kwargs"]["reload"] is True


def test_main_entrypoint(monkeypatch):
    import runpy
    import uvicorn

    called = {"value": False}

    def fake_run(*_args, **_kwargs):
        called["value"] = True

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("PORT", "9091")

    runpy.run_module("main", run_name="__main__")

    assert called["value"] is True
