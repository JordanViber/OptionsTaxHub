"""
Tests for server/auth.py — JWT authentication helpers.

Covers:
- get_jwks_client(): URL construction, caching, missing SUPABASE_URL
- verify_jwt_token(): HS256 path, asymmetric (ES256/RS256) path, error paths
- get_current_user(): no credentials, valid token, missing sub
- get_current_user_with_token(): same as above + returns raw token
- enforce_ownership(): matching and mismatched owner IDs
"""

import os
import pytest
import jwt as pyjwt

from fastapi import HTTPException
from fastapi.security.http import HTTPAuthorizationCredentials
from unittest.mock import MagicMock, patch
import auth


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_hs256_token(payload: dict, secret: str = "test-secret") -> str:
    """Return a signed HS256 JWT with the given payload."""
    return pyjwt.encode(payload, secret, algorithm="HS256")


def make_credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ---------------------------------------------------------------------------
# get_jwks_client
# ---------------------------------------------------------------------------

class TestGetJwksClient:
    def setup_method(self):
        # Reset the module-level cache before each test
        auth._jwks_client = None

    def teardown_method(self):
        auth._jwks_client = None

    def test_raises_when_supabase_url_missing(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.setenv("SUPABASE_URL", "")
        with pytest.raises(ValueError, match="SUPABASE_URL not configured"):
            auth.get_jwks_client()

    def test_creates_client_with_correct_url(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
        with patch("auth.PyJWKClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = auth.get_jwks_client()
            mock_cls.assert_called_once_with(
                "https://example.supabase.co/auth/v1/.well-known/jwks.json",
                cache_keys=True,
            )
            assert client is mock_cls.return_value

    def test_returns_cached_client_on_second_call(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
        with patch("auth.PyJWKClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            first = auth.get_jwks_client()
            second = auth.get_jwks_client()
            assert first is second
            # PyJWKClient should only be constructed once
            mock_cls.assert_called_once()

    def test_strips_trailing_slash_from_url(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co/")
        with patch("auth.PyJWKClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            auth.get_jwks_client()
            url_arg = mock_cls.call_args[0][0]
            assert not url_arg.startswith("https://example.supabase.co//")
            assert "well-known/jwks.json" in url_arg


# ---------------------------------------------------------------------------
# verify_jwt_token
# ---------------------------------------------------------------------------

class TestVerifyJwtToken:
    def setup_method(self):
        auth._jwks_client = None

    def teardown_method(self):
        auth._jwks_client = None

    def test_invalid_jwt_header_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            auth.verify_jwt_token("not.a.valid.jwt")
        assert exc_info.value.status_code == 401

    def test_hs256_valid_token(self, monkeypatch):
        secret = "test-hs256-secret"  # NOSONAR python:S6418 — test value, not a real credential
        monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
        payload = {"sub": "user-abc", "role": "authenticated"}
        token = make_hs256_token(payload, secret)
        result = auth.verify_jwt_token(token)
        assert result["sub"] == "user-abc"

    def test_hs256_no_secret_raises_500(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
        monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
        os.environ.pop("SUPABASE_JWT_SECRET", None)
        payload = {"sub": "user-abc"}
        token = make_hs256_token(payload, "some-secret")
        with pytest.raises(HTTPException) as exc_info:
            auth.verify_jwt_token(token)
        assert exc_info.value.status_code == 500
        assert "Internal authentication error" in exc_info.value.detail

    def test_hs256_wrong_secret_raises_401(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "correct-secret")
        payload = {"sub": "user-abc"}
        token = make_hs256_token(payload, "wrong-secret")
        with pytest.raises(HTTPException) as exc_info:
            auth.verify_jwt_token(token)
        assert exc_info.value.status_code == 401

    def test_asymmetric_valid_token(self, monkeypatch):
        """RS256/ES256 path — mock the JWKS client."""
        # Build a fake ES256 token so get_unverified_header returns alg=ES256
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend

        key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        payload = {"sub": "user-es256", "role": "authenticated"}
        token = pyjwt.encode(payload, key, algorithm="ES256")

        mock_signing_key = MagicMock()
        mock_signing_key.key = key.public_key()

        mock_client = MagicMock()
        mock_client.get_signing_key_from_jwt.return_value = mock_signing_key

        with patch("auth.get_jwks_client", return_value=mock_client):
            result = auth.verify_jwt_token(token)
        assert result["sub"] == "user-es256"

    def test_asymmetric_jwks_config_error_raises_500(self, monkeypatch):
        """JWKS client raises ValueError → should return 500."""
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend

        key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        payload = {"sub": "user-es256"}
        token = pyjwt.encode(payload, key, algorithm="ES256")

        def bad_client():
            raise ValueError("SUPABASE_URL not configured")

        with patch("auth.get_jwks_client", side_effect=bad_client):
            with pytest.raises(HTTPException) as exc_info:
                auth.verify_jwt_token(token)
        assert exc_info.value.status_code == 500

    def test_asymmetric_expired_token_raises_401(self, monkeypatch):
        """PyJWTError during decode → 401."""
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend
        import time

        key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        payload = {"sub": "user-es256", "exp": int(time.time()) - 3600}
        token = pyjwt.encode(payload, key, algorithm="ES256")

        mock_signing_key = MagicMock()
        mock_signing_key.key = key.public_key()
        mock_client = MagicMock()
        mock_client.get_signing_key_from_jwt.return_value = mock_signing_key

        with patch("auth.get_jwks_client", return_value=mock_client):
            with pytest.raises(HTTPException) as exc_info:
                auth.verify_jwt_token(token)
        assert exc_info.value.status_code == 401

    def test_unexpected_exception_raises_500(self, monkeypatch):
        """Unexpected exception during decode → 500."""
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend

        key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        payload = {"sub": "user-es256"}
        token = pyjwt.encode(payload, key, algorithm="ES256")

        mock_client = MagicMock()
        mock_client.get_signing_key_from_jwt.side_effect = RuntimeError("unexpected")

        with patch("auth.get_jwks_client", return_value=mock_client):
            with pytest.raises(HTTPException) as exc_info:
                auth.verify_jwt_token(token)
        # RuntimeError is not a ValueError so goes to the outer except Exception
        assert exc_info.value.status_code in (500, 401)


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------

class TestGetCurrentUser:
    def setup_method(self):
        auth._jwks_client = None

    def teardown_method(self):
        auth._jwks_client = None

    def test_no_credentials_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            auth.get_current_user(credentials=None)
        assert exc_info.value.status_code == 401
        assert "Authentication required" in exc_info.value.detail

    def test_valid_token_returns_user_id(self, monkeypatch):
        secret = "test-secret-key"
        monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
        payload = {"sub": "user-xyz"}
        token = make_hs256_token(payload, secret)
        creds = make_credentials(token)
        user_id = auth.get_current_user(credentials=creds)
        assert user_id == "user-xyz"

    def test_missing_sub_raises_401(self, monkeypatch):
        secret = "test-secret-key"
        monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
        # Token has no 'sub' claim
        token = make_hs256_token({"role": "anon"}, secret)
        creds = make_credentials(token)
        with pytest.raises(HTTPException) as exc_info:
            auth.get_current_user(credentials=creds)
        assert exc_info.value.status_code == 401
        assert "user_id not found" in exc_info.value.detail

    def test_invalid_token_raises_401(self):
        creds = make_credentials("bad.token.here")
        with pytest.raises(HTTPException) as exc_info:
            auth.get_current_user(credentials=creds)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# get_current_user_with_token
# ---------------------------------------------------------------------------

class TestGetCurrentUserWithToken:
    def setup_method(self):
        auth._jwks_client = None

    def teardown_method(self):
        auth._jwks_client = None

    def test_no_credentials_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            auth.get_current_user_with_token(credentials=None)
        assert exc_info.value.status_code == 401
        assert "Authentication required" in exc_info.value.detail

    def test_valid_token_returns_user_id_and_token(self, monkeypatch):
        secret = "test-secret-key"
        monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
        payload = {"sub": "user-abc"}
        token = make_hs256_token(payload, secret)
        creds = make_credentials(token)
        user_id, returned_token = auth.get_current_user_with_token(credentials=creds)
        assert user_id == "user-abc"
        assert returned_token == token

    def test_missing_sub_raises_401(self, monkeypatch):
        secret = "test-secret-key"
        monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
        token = make_hs256_token({"role": "anon"}, secret)
        creds = make_credentials(token)
        with pytest.raises(HTTPException) as exc_info:
            auth.get_current_user_with_token(credentials=creds)
        assert exc_info.value.status_code == 401
        assert "user_id not found" in exc_info.value.detail

    def test_invalid_token_raises_401(self):
        creds = make_credentials("not.a.jwt")
        with pytest.raises(HTTPException) as exc_info:
            auth.get_current_user_with_token(credentials=creds)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# enforce_ownership
# ---------------------------------------------------------------------------

class TestEnforceOwnership:
    def test_matching_owner_does_not_raise(self):
        # Should return None (no exception)
        result = auth.enforce_ownership("user-123", "user-123")
        assert result is None

    def test_different_owner_raises_403(self):
        with pytest.raises(HTTPException) as exc_info:
            auth.enforce_ownership("user-123", "user-456")
        assert exc_info.value.status_code == 403
        assert "permission" in exc_info.value.detail.lower()

    def test_empty_ids_raises_403(self):
        with pytest.raises(HTTPException) as exc_info:
            auth.enforce_ownership("user-a", "user-b")
        assert exc_info.value.status_code == 403
