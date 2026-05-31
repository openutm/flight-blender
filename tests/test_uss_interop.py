"""Peer-USS RID interop parity tests.

Django served the USS-to-USS Remote-ID exchange endpoints under ``/uss/...``
guarded by the RID-specific scope ``rid.display_provider`` (see Django
``uss_operations/urls.py`` + ``uss_operations/views.py``):

* ``GET /uss/flights``                       → ``rid.display_provider``
* ``GET /uss/flights/<flight_id>/details``   → ``rid.display_provider``

The FastAPI migration moved these to ``/uss_ops/...`` and guarded them with the
generic blender read scope, breaking interop with other USSs. These tests pin:

1. the Django-compatible path responds (``/uss/flights*``), while the
   ``/uss_ops`` alias is also kept for back-compat, and
2. the endpoints enforce the *real* ``rid.display_provider`` scope — proven with
   the non-bypassed JWT path (bypass switched OFF + a fake JWKS), mirroring
   ``tests/test_auth_verification.py``.
"""

import time
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import flight_blender.auth.jwt_bearer as jb

# ── Scope-enforcement helpers (mirror test_auth_verification.py) ────────────────


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
        auth_server_jwks_uri="https://jwks.test",
        auth_audience="",
        flightblender_read_scope="blender.read",
        flightblender_write_scope="blender.write",
        rid_display_provider_scope="rid.display_provider",
        rid_service_provider_scope="rid.service_provider",
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


def _valid_claims(scope: str, **overrides):
    claims = {
        "exp": int(time.time()) + 3600,
        "iss": "https://issuer.example.test",
        "scope": scope,
    }
    claims.update(overrides)
    return claims


# ── 1. Django-compatible path responds (and /uss_ops alias kept) ────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("base", ["/uss", "/uss_ops"])
async def test_get_uss_flights_path(client, base):
    """``GET <base>/flights`` responds (200) at the Django path and the alias."""
    resp = await client.get(f"{base}/flights?view=-1.0,51.0,1.0,52.0")
    assert resp.status_code == 200
    assert "flights" in resp.json()


@pytest.mark.anyio
@pytest.mark.parametrize("base", ["/uss", "/uss_ops"])
async def test_get_uss_flight_details_path(client, base):
    """``GET <base>/flights/<id>/details`` returns 404 for an unknown flight."""
    resp = await client.get(f"{base}/flights/UNKNOWN-FLIGHT/details")
    assert resp.status_code == 404


# ── 2. The RID-specific scope is actually enforced (real JWT path) ──────────────


def _rid_display_dep():
    """Resolve the require_scope dependency callable for rid.display_provider."""
    return jb.require_scope("rid.display_provider")


@pytest.mark.anyio
async def test_uss_flights_requires_rid_display_provider_scope(monkeypatch, use_jwks):
    """A token without ``rid.display_provider`` is rejected (403); with it, accepted."""
    private_key, public_key = _keypair()
    use_jwks(public_key)
    monkeypatch.setattr(jb, "settings", _settings())

    dependency = _rid_display_dep()

    # Wrong scope (only the generic blender scopes) → 403.
    bad_token = _sign(private_key, **_valid_claims(scope="blender.read blender.write"))
    bad_payload = await jb._get_token_payload(_creds(bad_token))
    with pytest.raises(HTTPException) as exc:
        await dependency(bad_payload)
    assert exc.value.status_code == 403

    # Correct RID scope → accepted.
    good_token = _sign(private_key, **_valid_claims(scope="rid.display_provider"))
    good_payload = await jb._get_token_payload(_creds(good_token))
    result = await dependency(good_payload)
    assert "rid.display_provider" in result["scope"]


@pytest.mark.anyio
async def test_uss_rid_routes_declare_display_provider_scope():
    """The mounted /uss/flights* routes must require rid.display_provider, not blender.read.

    This guards against the migration regression where these peer-USS RID
    endpoints were guarded with the generic blender read scope.
    """
    from flight_blender.config import get_settings
    from flight_blender.main import create_app

    settings = get_settings()
    app = create_app()
    rid_scope = settings.rid_display_provider_scope

    targets = {"/uss/flights", "/uss/flights/{flight_id}/details"}
    seen = set()
    for route in app.routes:
        if getattr(route, "path", None) in targets and "GET" in (getattr(route, "methods", set()) or set()):
            seen.add(route.path)
            # Collect every scope referenced by this route's dependencies.
            scopes: set[str] = set()
            for dep in route.dependant.dependencies:
                closure = getattr(dep.call, "__closure__", None) or ()
                for cell in closure:
                    val = cell.cell_contents
                    if isinstance(val, tuple):
                        scopes.update(s for s in val if isinstance(s, str))
            assert rid_scope in scopes, f"{route.path} must require {rid_scope}; saw {scopes}"
            assert settings.flightblender_read_scope not in scopes, f"{route.path} must not require the generic read scope"

    assert seen == targets, f"missing Django-compatible RID routes: {targets - seen}"
