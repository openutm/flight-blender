"""
Auth-layer verification tests for the real (non-bypassed) JWT path.

The rest of the suite runs with BYPASS_AUTH_TOKEN_VERIFICATION=1, so the
genuine signature/audience/required-claim checks are never exercised there.
These tests patch ``jwt_bearer.settings`` with the bypass switched OFF and
drive ``_get_token_payload`` directly, signing real RS256 tokens with an
in-test RSA keypair (only the JWKS *fetch* is faked — ``jwt.decode`` runs for
real).

They pin the hardened posture that matches the Django original:
  * fail CLOSED when no JWKS URI is configured (never decode unverified),
  * verify ``audience`` when an expected audience is configured,
  * require ``exp`` (no never-expiring tokens) and ``iss`` to be present.
"""

import time
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import flight_blender.auth.jwt_bearer as jb


def _keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _sign(private_key, **claims) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256")


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _settings(**overrides):
    base = dict(
        bypass_auth_token_verification=False,
        auth_server_jwks_uri="",
        auth_audience="",
        flightblender_read_scope="blender.read",
        flightblender_write_scope="blender.write",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture
def use_jwks(monkeypatch):
    """Install a fake PyJWKClient whose signing key is the given public key."""

    def _install(public_key):
        class _FakeJWKClient:
            def __init__(self, *_a, **_kw):
                pass

            def get_signing_key_from_jwt(self, _token):
                return types.SimpleNamespace(key=public_key)

        monkeypatch.setattr(jb.jwt, "PyJWKClient", _FakeJWKClient)

    return _install


def _valid_claims(**overrides):
    claims = {
        "exp": int(time.time()) + 3600,
        "iss": "https://issuer.example.test",
        "aud": "flightblender.test",
        "scope": "blender.read blender.write",
    }
    claims.update(overrides)
    return claims


# ── Fail-closed when JWKS is not configured ─────────────────────────────────


@pytest.mark.anyio
async def test_fail_closed_when_jwks_unset(monkeypatch):
    """No JWKS URI + bypass off must reject, not decode-without-verification."""
    monkeypatch.setattr(jb, "settings", _settings(auth_server_jwks_uri=""))
    with pytest.raises(HTTPException) as exc:
        await jb._get_token_payload(_creds("any.unverifiable.token"))
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_missing_credentials_rejected(monkeypatch):
    monkeypatch.setattr(jb, "settings", _settings(auth_server_jwks_uri="https://jwks.test"))
    with pytest.raises(HTTPException) as exc:
        await jb._get_token_payload(None)
    assert exc.value.status_code == 401


# ── Audience verification ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_wrong_audience_rejected(monkeypatch, use_jwks):
    private_key, public_key = _keypair()
    use_jwks(public_key)
    monkeypatch.setattr(
        jb,
        "settings",
        _settings(auth_server_jwks_uri="https://jwks.test", auth_audience="flightblender.test"),
    )
    token = _sign(private_key, **_valid_claims(aud="some-other-service"))
    with pytest.raises(HTTPException) as exc:
        await jb._get_token_payload(_creds(token))
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_correct_audience_accepted(monkeypatch, use_jwks):
    private_key, public_key = _keypair()
    use_jwks(public_key)
    monkeypatch.setattr(
        jb,
        "settings",
        _settings(auth_server_jwks_uri="https://jwks.test", auth_audience="flightblender.test"),
    )
    token = _sign(private_key, **_valid_claims(aud="flightblender.test"))
    payload = await jb._get_token_payload(_creds(token))
    assert payload["scope"] == "blender.read blender.write"


# ── Required claims ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_missing_exp_rejected(monkeypatch, use_jwks):
    private_key, public_key = _keypair()
    use_jwks(public_key)
    monkeypatch.setattr(
        jb,
        "settings",
        _settings(auth_server_jwks_uri="https://jwks.test", auth_audience="flightblender.test"),
    )
    claims = _valid_claims()
    claims.pop("exp")
    token = _sign(private_key, **claims)
    with pytest.raises(HTTPException) as exc:
        await jb._get_token_payload(_creds(token))
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_missing_iss_rejected(monkeypatch, use_jwks):
    private_key, public_key = _keypair()
    use_jwks(public_key)
    monkeypatch.setattr(
        jb,
        "settings",
        _settings(auth_server_jwks_uri="https://jwks.test", auth_audience="flightblender.test"),
    )
    claims = _valid_claims()
    claims.pop("iss")
    token = _sign(private_key, **claims)
    with pytest.raises(HTTPException) as exc:
        await jb._get_token_payload(_creds(token))
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_expired_token_rejected(monkeypatch, use_jwks):
    private_key, public_key = _keypair()
    use_jwks(public_key)
    monkeypatch.setattr(
        jb,
        "settings",
        _settings(auth_server_jwks_uri="https://jwks.test", auth_audience="flightblender.test"),
    )
    token = _sign(private_key, **_valid_claims(exp=int(time.time()) - 10))
    with pytest.raises(HTTPException) as exc:
        await jb._get_token_payload(_creds(token))
    assert exc.value.status_code == 401


# ── Bypass still works (dev/test convenience) ───────────────────────────────


@pytest.mark.anyio
async def test_bypass_returns_all_scopes(monkeypatch):
    monkeypatch.setattr(jb, "settings", _settings(bypass_auth_token_verification=True))
    payload = await jb._get_token_payload(None)
    assert "blender.read" in payload["scope"]
    assert "blender.write" in payload["scope"]
