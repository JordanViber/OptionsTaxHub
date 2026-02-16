"""
JWT Authentication for OptionsTaxHub backend.

Uses Supabase Auth tokens to:
1. Verify JWT signatures
2. Extract authenticated user_id
3. Enforce ownership of user data
"""

import os
import logging
from typing import Optional
from functools import lru_cache

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthCredentials
from jwt import decode, PyJWTError
import requests

logger = logging.getLogger(__name__)

# Supabase JWT configuration
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")

# Security scheme
security = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def get_jwt_secret() -> str:
    """
    Get JWT secret from environment.

    In production, this should be cached and only retrieved once.
    The secret is used to verify JWT tokens signed by Supabase Auth.
    """
    if not JWT_SECRET:
        raise ValueError(
            "SUPABASE_JWT_SECRET not configured. "
            "Get it from Supabase Dashboard > Project Settings > API > JWT Secrets"
        )
    return JWT_SECRET


def verify_jwt_token(token: str) -> dict:
    """
    Verify JWT token and extract claims.

    Args:
        token: JWT token from Authorization header

    Returns:
        Decoded JWT claims including user_id (sub)

    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        # Verify and decode the JWT
        # Supabase uses HS256 algorithm by default
        payload = decode(
            token,
            get_jwt_secret(),
            algorithms=["HS256"],
        )
        return payload
    except PyJWTError as e:
        logger.error(f"JWT verification failed: {e}")
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired authentication token",
        )


def get_current_user(
    credentials: Optional[HTTPAuthCredentials] = Security(security),
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
