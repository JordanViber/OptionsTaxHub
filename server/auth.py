"""
JWT Authentication for OptionsTaxHub backend.

Uses Supabase Auth tokens to:
1. Verify JWT signatures via JWKS (works with ECC P-256 and legacy HS256)
2. Extract authenticated user_id
3. Enforce ownership of user data
"""

import os
import logging
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer
from fastapi.security.http import HTTPAuthorizationCredentials
from jwt import PyJWKClient, decode, PyJWTError

logger = logging.getLogger(__name__)

# Security scheme
security = HTTPBearer(auto_error=False)

# Cached JWKS client — fetched once, keys are cached automatically by PyJWKClient
_jwks_client: Optional[PyJWKClient] = None


def get_jwks_client() -> PyJWKClient:
    """
    Return a cached PyJWKClient pointed at Supabase's JWKS endpoint.

    Supabase exposes public signing keys at:
      {SUPABASE_URL}/auth/v1/.well-known/jwks.json

    PyJWKClient fetches and caches these keys, so it works transparently
    with both the current ECC (P-256) key and the legacy HS256 shared secret.
    """
    global _jwks_client
    if _jwks_client is not None:
        return _jwks_client

    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not supabase_url:
        raise ValueError(
            "SUPABASE_URL not configured. "
            "Set it to your Supabase project URL (e.g. https://xyz.supabase.co)."
        )

    jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
    logger.info(f"Initialising JWKS client from {jwks_url}")
    _jwks_client = PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


def verify_jwt_token(token: str) -> dict:
    """
    Verify a Supabase JWT token and extract its claims.

    Uses JWKS to automatically select the correct signing key, so this works
    with both the current ECC (P-256) key and any previously-used HS256 keys
    that are still kept by Supabase for in-flight token verification.

    Args:
        token: JWT token from Authorization header

    Returns:
        Decoded JWT claims including user_id (sub)

    Raises:
        HTTPException 401: If the token is invalid, expired, or unverifiable
    """
    try:
        client = get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256", "HS256"],
            options={"verify_aud": False},
        )
        return payload
    except PyJWTError as e:
        logger.error(f"JWT verification failed: {e}")
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired authentication token",
        )
    except Exception as e:
        logger.error(f"Auth error: {e}")
        raise HTTPException(
            status_code=401,
            detail="Authentication failed",
        )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> str:
    """
    Extract and verify user_id from JWT token in Authorization header.

    Args:
        credentials: HTTP Bearer token from Authorization header

    Returns:
        Authenticated user_id (UUID from Supabase Auth)

    Raises:
        HTTPException: If no token provided or token is invalid
    """
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    # Verify token and extract user_id (sub claim)
    payload = verify_jwt_token(credentials.credentials)

    # Supabase stores user_id in the 'sub' (subject) claim
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Invalid token: user_id not found",
        )

    return user_id


def get_current_user_with_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> tuple[str, str]:
    """
    Extract and verify user_id and JWT token from Authorization header.

    Used when an endpoint needs both the user_id and the raw token
    (e.g., to create an authenticated Supabase client for RLS enforcement).

    Args:
        credentials: HTTP Bearer token from Authorization header

    Returns:
        Tuple of (user_id, token) for creating authenticated clients

    Raises:
        HTTPException: If no token provided or token is invalid
    """
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    # Verify token and extract user_id
    payload = verify_jwt_token(credentials.credentials)

    # Supabase stores user_id in the 'sub' (subject) claim
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Invalid token: user_id not found",
        )

    return user_id, credentials.credentials


def enforce_ownership(user_id: str, resource_owner_id: str) -> None:
    """
    Enforce that authenticated user owns the resource.

    Args:
        user_id: Authenticated user's ID from JWT
        resource_owner_id: ID of the resource's owner

    Raises:
        HTTPException: If user does not own the resource
    """
    if user_id != resource_owner_id:
        logger.warning(
            f"Unauthorized access attempt: user {user_id} tried to access "
            f"resource owned by {resource_owner_id}"
        )
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this resource",
        )
