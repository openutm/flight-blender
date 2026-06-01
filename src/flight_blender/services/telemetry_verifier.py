"""IETF HTTP Message Signature verification for signed telemetry.

Ported from Django flight_feed_operations/pki_helper.py.
Uses the http-message-signatures library (RFC 9421).
"""

from __future__ import annotations
from jwt.algorithms import RSAAlgorithm
from http_message_signatures.resolvers import HTTPSignatureKeyResolver
from flight_blender.models import SignedTelemetryPublicKey
from typing import Iterable

import base64
import hashlib

import httpx
from loguru import logger

try:
    from http_message_signatures import (
        HTTPMessageVerifier,
        algorithms,
    )

    _SIGNING_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SIGNING_AVAILABLE = False
    logger.warning("http-message-signatures not installed — telemetry signature verification disabled")


def _content_digest_matches(headers: dict[str, str], body: bytes) -> bool:
    """Validate that the ``Content-Digest`` header binds *body* (RFC 9530).

    The HTTP message signature covers the ``content-digest`` *header*, not the
    raw body, so a signature can be replayed with a swapped body unless the
    digest is independently checked against the body. We require a ``sha-256``
    digest to be present and to match ``base64(sha256(body))``.
    """
    header = next((v for k, v in headers.items() if k.lower() == "content-digest"), None)
    if not header:
        # No digest to bind the body — refuse rather than trust an unbound signature.
        return False

    expected = base64.b64encode(hashlib.sha256(body).digest()).decode()
    # Header is a Structured Field Dictionary, e.g. ``sha-256=:<b64>:``. Be lenient
    # about ordering / additional algorithms and just look for our sha-256 value.
    for member in header.split(","):
        if "sha-256=" not in member.lower():
            continue
        value = member.split("=", 1)[1].strip().strip(":")
        if value == expected:
            return True
    return False


class _JWKKeyResolver(HTTPSignatureKeyResolver):
    """Resolve public keys from a JWK dict for HTTP message signature verification."""

    def __init__(self, jwk_data: dict) -> None:
        self._jwk = jwk_data

    def resolve_public_key(self, key_id: str | None = None):  # type: ignore[override]
        return RSAAlgorithm.from_jwk(self._jwk)

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

    # The signature covers the ``content-digest`` header rather than the raw body,
    # so verify the digest independently — otherwise a captured signature could be
    # replayed against a tampered body.
    if not _content_digest_matches(headers, body):
        logger.debug("Signature verification failed: Content-Digest does not bind the body")
        return False

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


async def fetch_public_keys_from_db_rows(rows: Iterable[SignedTelemetryPublicKey]) -> dict[str, dict]:
    """Fetch and cache JWK data for a list of SignedTelemetryPublicKey model rows.

    Returns a mapping of key_id → JWK dict.
    Uses an async ``httpx.AsyncClient`` so it can be called from async handlers
    without blocking the event loop.
    """
    result: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=5) as client:
        for row in rows:
            kid = row.key_id
            try:
                resp = await client.get(row.url)
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
