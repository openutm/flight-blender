"""Unit tests for signed-RID-telemetry verification.

The peer-RID / signed-telemetry path verifies an IETF HTTP Message Signature
(RFC 9421, RSA-PSS-SHA512) against the public keys registered as
``SignedTelemetryPublicKey`` rows — ported from Django
``flight_feed_operations/pki_helper.py`` (``MessageVerifier``).

These tests exercise the pure verifier (no network, no DB) by signing a request
with an in-test RSA keypair and checking:

* a valid signature over an intact body is accepted,
* a tampered body is rejected (the ``Content-Digest`` must bind the body),
* an absent/garbage signature is rejected,
* an unknown key (signature made with a different key) is rejected,
* no configured keys → rejected.
"""

import hashlib
import json

import http_sfv
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from http_message_signatures import HTTPMessageSigner, HTTPSignatureKeyResolver, algorithms
from jwt.algorithms import RSAAlgorithm

from flight_blender.services.telemetry_verifier import verify_signed_request

METHOD = "POST"
URL = "http://test/flight_stream/set_signed_telemetry"


def _keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _jwk(public_key, kid: str) -> dict:
    data = json.loads(RSAAlgorithm.to_jwk(public_key))
    data["kid"] = kid
    return data


def _content_digest(body: bytes) -> str:
    return str(http_sfv.Dictionary({"sha-256": hashlib.sha256(body).digest()}))


class _Req:
    def __init__(self, method, url, headers, body):
        self.method = method
        self.url = url
        self.headers = headers
        self.body = body


def _sign(private_key, kid: str, body: bytes) -> dict:
    """Sign a request the way Django's ResponseSigningOperations.sign_http_message does."""

    class _KR(HTTPSignatureKeyResolver):
        def resolve_private_key(self, key_id):
            return private_key

        def resolve_public_key(self, key_id=None):
            return private_key.public_key()

    req = _Req(METHOD, URL, {"Content-Digest": _content_digest(body), "Content-Type": "application/json"}, body)
    signer = HTTPMessageSigner(signature_algorithm=algorithms.RSA_PSS_SHA512, key_resolver=_KR())
    signer.sign(
        req,
        key_id=kid,
        covered_component_ids=("@method", "@authority", "@target-uri", "content-digest"),
        label="sig1",
    )
    return dict(req.headers)


pytestmark = pytest.mark.anyio


async def test_valid_signature_accepted():
    private_key, public_key = _keypair()
    body = json.dumps({"observations": [{"id": "A"}]}).encode()
    headers = _sign(private_key, "key-1", body)
    assert await verify_signed_request(METHOD, URL, headers, body, [_jwk(public_key, "key-1")]) is True


async def test_tampered_body_rejected():
    private_key, public_key = _keypair()
    body = json.dumps({"observations": [{"id": "A"}]}).encode()
    headers = _sign(private_key, "key-1", body)
    tampered = json.dumps({"observations": [{"id": "EVIL"}]}).encode()
    assert await verify_signed_request(METHOD, URL, headers, tampered, [_jwk(public_key, "key-1")]) is False


async def test_missing_signature_rejected():
    _private_key, public_key = _keypair()
    body = b"{}"
    # No Signature/Signature-Input headers at all.
    headers = {"Content-Digest": _content_digest(body), "Content-Type": "application/json"}
    assert await verify_signed_request(METHOD, URL, headers, body, [_jwk(public_key, "key-1")]) is False


async def test_unknown_key_rejected():
    signing_key, _ = _keypair()
    _, other_public = _keypair()
    body = json.dumps({"observations": []}).encode()
    headers = _sign(signing_key, "key-1", body)
    # Verify against a *different* public key.
    assert await verify_signed_request(METHOD, URL, headers, body, [_jwk(other_public, "key-1")]) is False


async def test_no_keys_rejected():
    private_key, _ = _keypair()
    body = b"{}"
    headers = _sign(private_key, "key-1", body)
    assert await verify_signed_request(METHOD, URL, headers, body, []) is False
