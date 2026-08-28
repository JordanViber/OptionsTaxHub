"""Browser origins allowed to call the API with credentialed CORS.

Render historically set FRONTEND_URL to the *.onrender.com client hostname.
The public site is www.optionstaxhub.com, so that origin must stay on the
allow-list even when FRONTEND_URL still points at Render — otherwise the
dashboard /health and /analyze fetches fail in the browser with a generic
"could not reach the analysis service" toast.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Always allow the public custom domain and the Render client hostnames so a
# FRONTEND_URL mismatch cannot take the dashboard offline again.
KNOWN_BROWSER_ORIGINS: tuple[str, ...] = (
    "https://www.optionstaxhub.com",
    "https://optionstaxhub.com",
    "https://options-tax-hub-client-prod.onrender.com",
    "https://options-tax-hub-client-staging.onrender.com",
)


def origin_from_url(url: str) -> str | None:
    """Return scheme://host[:port] or None when the value is not an http(s) URL."""
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname
    if scheme not in ("http", "https") or not host:
        return None
    origin = f"{scheme}://{host}"
    if parsed.port:
        origin += f":{parsed.port}"
    return origin


def www_twin(origin: str) -> str | None:
    """www <-> apex counterpart so a redirect does not break CORS."""
    parsed = urlparse(origin)
    host = parsed.hostname or ""
    if not host:
        return None
    twin_host = host[4:] if host.startswith("www.") else f"www.{host}"
    twin = f"{parsed.scheme}://{twin_host}"
    if parsed.port:
        twin += f":{parsed.port}"
    return twin


def cors_allowed_origins(
    frontend_url: str,
    extra_origins: str = "",
    known_origins: tuple[str, ...] = KNOWN_BROWSER_ORIGINS,
) -> list[str]:
    """Union of FRONTEND_URL, known hosts, CORS_ORIGINS, and www/apex twins."""
    seeds = [frontend_url, *known_origins, *extra_origins.split(",")]
    allowed: set[str] = set()
    for seed in seeds:
        origin = origin_from_url(seed)
        if not origin:
            continue
        allowed.add(origin)
        twin = www_twin(origin)
        if twin:
            allowed.add(twin)
    return sorted(allowed)
