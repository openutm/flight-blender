"""
JWT Bearer token verification and FastAPI security dependencies.
"""

from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from flight_blender.config import get_settings

settings = get_settings()

_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _get_jwks_client(jwks_uri: str) -> jwt.PyJWKClient:
    """Return a cached ``PyJWKClient`` for *jwks_uri*.

    The client is created once per distinct URI and reused for all subsequent
    requests, avoiding an HTTP round-trip to the JWKS endpoint on every
    authenticated call.  Tests can clear the cache via
    ``_get_jwks_client.cache_clear()``.
    """
    return jwt.PyJWKClient(jwks_uri)


def verify_bearer_token(token: str | None) -> dict:
    """Verify a raw bearer token string and return its claims.

    Shared by the HTTP dependency (:func:`_get_token_payload`) and the WebSocket
    auth gate. Raises ``HTTPException`` (401) on any failure. If
    BYPASS_AUTH_TOKEN_VERIFICATION is set the token is not verified
    (development / test mode only).
    """
    if settings.bypass_auth_token_verification:
        # Return a minimal payload with all scopes (dev / test mode), including
        # the RID peer-USS interop scopes so the USS-to-USS endpoints are
        # reachable without a real token.
        scopes = [
            settings.flightblender_read_scope,
            settings.flightblender_write_scope,
            getattr(settings, "rid_display_provider_scope", "rid.display_provider"),
            getattr(settings, "rid_service_provider_scope", "rid.service_provider"),
            getattr(settings, "geo_awareness_test_scope", "geo-awareness.test"),
        ]
        return {"scope": " ".join(scopes)}

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authentication token")

    # Fail CLOSED: without a JWKS source we cannot verify the signature, so we
    # refuse to authenticate rather than trusting an unverified token. (Django's
    # default posture was fail-closed; an unverified decode here would let any
    # forged token through, including its ``scope`` claim.)
    if not settings.auth_server_jwks_uri:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token verification is not configured (no JWKS URI); refusing to authenticate",
        )

    # Always require a signed, non-expiring-disallowed, issued token. Match the
    # Django original which enforced exp/iss/aud presence and RS256 signatures.
    required_claims = ["exp", "iss"]
    decode_kwargs: dict = {"algorithms": ["RS256"]}
    if settings.auth_audience:
        decode_kwargs["audience"] = settings.auth_audience
        required_claims.append("aud")
    decode_kwargs["options"] = {"require": required_claims}

    try:
        jwks_client = _get_jwks_client(settings.auth_server_jwks_uri)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(token, signing_key.key, **decode_kwargs)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    return payload


async def _get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> dict:
    """FastAPI dependency: validate the Bearer JWT and return its payload."""
    token = credentials.credentials if credentials else None
    return verify_bearer_token(token)


def require_scope(*required_scopes: str):
    """Return a FastAPI dependency that checks the token contains all *required_scopes*."""

    async def _dependency(payload: Annotated[dict, Depends(_get_token_payload)]) -> dict:
        token_scopes: set[str] = set((payload.get("scope") or "").split())
        missing = set(required_scopes) - token_scopes
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scope(s): {', '.join(sorted(missing))}",
            )
        return payload

    return _dependency


# Convenient aliases
ReadDep = Depends(require_scope(settings.flightblender_read_scope))
WriteDep = Depends(require_scope(settings.flightblender_write_scope))

# Remote-ID peer-USS interop scopes (ASTM F3411). The USS-to-USS RID data
# exchange endpoints must require these RID-specific scopes rather than the
# generic blender read/write scopes, matching the Django original.
RIDDisplayProviderDep = Depends(require_scope(settings.rid_display_provider_scope))
RIDServiceProviderDep = Depends(require_scope(settings.rid_service_provider_scope))

# InterUSS geo-awareness test-harness scope (ED-269). The geo-awareness status,
# geospatial data source lifecycle and map-query endpoints must require this
# dedicated scope rather than the generic blender read/write scopes, matching
# the Django original which guarded them with ``geo-awareness.test``.
GeoAwarenessTestDep = Depends(require_scope(settings.geo_awareness_test_scope))
