"""CORS allow-list for the public custom domain.

Regression: production FRONTEND_URL was the Render client hostname, so
requests from https://www.optionstaxhub.com got no Access-Control-Allow-Origin
and the dashboard treated a healthy API as unreachable.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from cors_origins import cors_allowed_origins, origin_from_url, www_twin

PROD_RENDER = "https://options-tax-hub-client-prod.onrender.com"
CUSTOM_WWW = "https://www.optionstaxhub.com"
CUSTOM_APEX = "https://optionstaxhub.com"


def test_origin_from_url_strips_path_and_rejects_junk():
    assert origin_from_url("https://www.optionstaxhub.com/dashboard") == CUSTOM_WWW
    assert origin_from_url("  https://optionstaxhub.com  ") == CUSTOM_APEX
    assert origin_from_url("ftp://www.optionstaxhub.com") is None
    assert origin_from_url("not-a-url") is None
    assert origin_from_url("") is None


def test_www_twin_round_trips():
    assert www_twin(CUSTOM_WWW) == CUSTOM_APEX
    assert www_twin(CUSTOM_APEX) == CUSTOM_WWW


def test_onrender_frontend_url_still_allows_custom_domain():
    origins = cors_allowed_origins(PROD_RENDER)
    assert CUSTOM_WWW in origins
    assert CUSTOM_APEX in origins
    assert PROD_RENDER in origins


def test_custom_domain_frontend_url_still_allows_onrender_client():
    origins = cors_allowed_origins(CUSTOM_WWW)
    assert PROD_RENDER in origins
    assert CUSTOM_APEX in origins
    assert "https://options-tax-hub-client-staging.onrender.com" in origins


def test_cors_origins_env_adds_preview_hosts():
    origins = cors_allowed_origins(
        CUSTOM_WWW,
        extra_origins="https://preview.example.com/path, https://other.example.com",
    )
    assert "https://preview.example.com" in origins
    assert "https://www.preview.example.com" in origins
    assert "https://other.example.com" in origins


def test_unknown_origin_not_included():
    origins = cors_allowed_origins(PROD_RENDER)
    assert "https://evil.example" not in origins


def _cors_app(frontend_url: str) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allowed_origins(frontend_url),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return TestClient(app)


def test_health_from_custom_domain_gets_acao_when_frontend_url_is_onrender():
    client = _cors_app(PROD_RENDER)
    response = client.get("/health", headers={"Origin": CUSTOM_WWW})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["access-control-allow-origin"] == CUSTOM_WWW
    assert response.headers["access-control-allow-credentials"] == "true"


def test_preflight_from_custom_domain_is_allowed():
    client = _cors_app(PROD_RENDER)
    response = client.options(
        "/health",
        headers={
            "Origin": CUSTOM_WWW,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code in (200, 204)
    assert response.headers["access-control-allow-origin"] == CUSTOM_WWW


def test_disallowed_origin_does_not_get_acao():
    client = _cors_app(PROD_RENDER)
    response = client.get("/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
