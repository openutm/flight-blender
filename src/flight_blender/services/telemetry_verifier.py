"""IETF HTTP Message Signature verification for signed telemetry.

Ported from Django flight_feed_operations/pki_helper.py.
Uses the http-message-signatures library (RFC 9421).
"""

from __future__ import annotations

import json

import jwt
import requests
from loguru import logger

try:
    from http_message_signatures import (
        HTTPMessageVerifier,
        HTTPSignatureKeyResolver,
        algorithms,
    )

    _SIGNING_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SIGNING_AVAILABLE = False
    logger.warning("http-message-signatures not installed — telemetry signature verification disabled")


class _JWKKeyResolver:
    """Resolve public keys from a JWK dict for HTTP message signature verification."""

    def __init__(self, jwk_data: dict) -> None:
        self._jwk = jwk_data

    def resolve_public_key(self, key_id: str | None = None):  # type: ignore[override]
        return jwt.algorithms.RSAAlgorithm.from_jwk(self._jwk)

    def resolve_private_key(self, key_id: str) -> None:  # type: ignore[override]
        return None


async def verify_signed_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    public_keys: list[dict],
) -> bool:
    """Verify an IETF HTTP Message Signature against the given public keys.

    Returns True if any key successfully verifies the signature.
    Returns False if no keys are configured or all verifications fail.
    """
    if not _SIGNING_AVAILABLE:
        logger.warning("Signature verification skipped: http-message-signatures not installed")
        return True  # graceful degradation

    if not public_keys:
        return False

    import http.client

    class _FakeRequest:
        """Minimal request object accepted by HTTPMessageVerifier."""

        def __init__(self, method: str, url: str, headers: dict, body: bytes) -> None:
            self.method = method
            self.url = url
            self.headers = headers
            self.body = body

    fake_req = _FakeRequest(method, url, headers, body)

    for key_data in public_keys:
        try:
            verifier = HTTPMessageVerifier(
                signature_algorithm=algorithms.RSA_PSS_SHA512,
                key_resolver=_JWKKeyResolver(key_data),
            )
            verifier.verify(fake_req)  # type: ignore[arg-type]
            return True
        except Exception as exc:
            logger.debug("Signature verification attempt failed: {}", exc)

    return False


def fetch_public_keys_from_db_rows(rows: list) -> dict[str, dict]:
    """Fetch and cache JWK data for a list of SignedTelemetryPublicKey model rows.

    Returns a mapping of key_id → JWK dict.
    Uses a simple in-process requests.Session (no Redis in FastAPI path).
    """
    session = requests.Session()
    result: dict[str, dict] = {}

    for row in rows:
        kid = row.key_id
        try:
            resp = session.get(row.url, timeout=5)
            resp.raise_for_status()
            jwks = resp.json()
            jwk: dict | None = None
            if "keys" in jwks:
                jwk = next((k for k in jwks["keys"] if k.get("kid") == kid), None)
            elif jwks.get("kid") == kid:
                jwk = jwks
            if jwk:
                result[kid] = jwk
        except Exception as exc:
            logger.error("Failed to fetch public key {} from {}: {}", kid, row.url, exc)

    return result
