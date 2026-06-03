import json
from urllib.parse import urlparse

import httpx
import jwt
from fastapi import HTTPException, status
from loguru import logger

from flight_blender.config import settings


async def _fetch_jwks(url: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Error fetching JWKS from {url}: {e}")
            return {}


def _check_scopes(decoded: dict, required_scopes: list[str], allow_any: bool = False) -> bool:
    granted = set(decoded.get("scope", "").split())
    if allow_any:
        return bool(granted & set(required_scopes))
    return set(required_scopes).issubset(granted)


def _validate_bypass_token(token: str, required_scopes: list[str], allow_any: bool = False) -> dict:
    """Decode without signature verification — only for BYPASS_AUTH_TOKEN_VERIFICATION=True."""
    try:
        decoded = jwt.decode(token, algorithms=["RS256", "HS256"], options={"verify_signature": False})
    except jwt.DecodeError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token provided")

    iss = decoded.get("iss")
    if not iss:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token missing iss claim")
    if iss != "dummy":
        parsed = urlparse(iss)
        if not (parsed.scheme in ("http", "https") and parsed.netloc):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Issuer claim is not a valid URL")

    if not decoded.get("aud"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token missing aud claim")

    if not _check_scopes(decoded, required_scopes, allow_any):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient scope")

    return decoded


async def validate_token(token: str, required_scopes: list[str], allow_any: bool = False) -> dict:
    """Validate JWT and check scopes. Returns decoded payload."""
    try:
        jwt.get_unverified_header(token)
    except jwt.DecodeError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Bearer token could not be decoded")

    if settings.BYPASS_AUTH_TOKEN_VERIFICATION:
        return _validate_bypass_token(token, required_scopes, allow_any)

    passport_jwks = await _fetch_jwks(settings.PASSPORT_JWKS_URL)
    dss_jwks = await _fetch_jwks(settings.DSS_AUTH_JWKS_ENDPOINT)

    all_keys = passport_jwks.get("keys", []) + dss_jwks.get("keys", [])
    if not all_keys:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Could not fetch public keys for token validation")

    public_keys = {}
    for jwk in all_keys:
        try:
            public_keys[jwk["kid"]] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        except Exception as exc:
            logger.warning("Skipping malformed JWK kid={}: {}", jwk.get("kid"), exc)

    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")
    if not kid or kid not in public_keys:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"Signing key id {kid} not found in JWKS")

    try:
        decoded = jwt.decode(
            token,
            public_keys[kid],
            audience=settings.PASSPORT_AUDIENCE,
            algorithms=["RS256"],
            options={"require": ["exp", "iss", "aud"]},
        )
    except (
        jwt.ImmatureSignatureError,
        jwt.ExpiredSignatureError,
        jwt.InvalidAudienceError,
        jwt.InvalidIssuerError,
        jwt.InvalidSignatureError,
        jwt.DecodeError,
        jwt.exceptions.MissingRequiredClaimError,
    ) as e:
        logger.error(f"Token verification failed: {e}")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")

    if not _check_scopes(decoded, required_scopes, allow_any):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient scope")

    return decoded
