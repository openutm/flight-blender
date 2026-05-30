"""
JWT Bearer token verification and FastAPI security dependencies.
"""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from flight_blender.config import get_settings

settings = get_settings()

_bearer = HTTPBearer(auto_error=False)


async def _get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> dict:
    """Validate Bearer JWT and return its payload.

    If BYPASS_AUTH_TOKEN_VERIFICATION is set the token is not verified
    (development / test mode only).
    """
    if settings.bypass_auth_token_verification:
        # Return a minimal payload with all scopes
        return {"scope": f"{settings.flightblender_read_scope} {settings.flightblender_write_scope}"}

    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authentication token")

    token = credentials.credentials
    try:
        if settings.auth_server_jwks_uri:
            jwks_client = jwt.PyJWKClient(settings.auth_server_jwks_uri)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(token, signing_key.key, algorithms=["RS256"])
        else:
            # Decode without verification (insecure – only for fully trusted local environments)
            payload = jwt.decode(token, options={"verify_signature": False})
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    return payload


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
