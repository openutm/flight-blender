"""Weather subsystem parity tests (Django -> FastAPI migration).

Covers the weather service param construction (real param-building with a
mocked HTTP layer) and the weather router (scope enforcement, missing-param
contract, success/response shape, upstream-failure handling).
"""

from __future__ import annotations

import time

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from flight_blender.config import get_settings
from flight_blender.services.weather_service import WEATHER_TOPICS, WeatherService

pytestmark = pytest.mark.anyio

WEATHER_URL = "/weather_monitoring_ops/weather/"

_UPSTREAM_PAYLOAD = {
    "latitude": 51.5,
    "longitude": -0.1,
    "generationtime_ms": 0.123,
    "utc_offset_seconds": 0,
    "timezone": "UTC",
    "timezone_abbreviation": "GMT",
    "elevation": 25.0,
    "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
    "hourly": {"time": ["2026-05-30T00:00"], "temperature_2m": [12.3]},
}


# --------------------------------------------------------------------------- #
# Service: real param-building with a mocked httpx layer
# --------------------------------------------------------------------------- #
class _FakeAsyncClient:
    """Records the URL/params/timeout used and returns a canned response."""

    last_instance: "_FakeAsyncClient | None" = None

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs
        self.captured_url = None
        self.captured_params = None
        self.captured_timeout = None
        self.response = httpx.Response(200, json=_UPSTREAM_PAYLOAD)
        type(self).last_instance = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, timeout=None):
        self.captured_url = url
        self.captured_params = params
        self.captured_timeout = timeout
        return self.response


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr(
        "flight_blender.services.weather_service.httpx.AsyncClient",
        _FakeAsyncClient,
    )
    return _FakeAsyncClient


async def test_service_forwards_time_and_topics(fake_http):
    svc = WeatherService(base_url="https://api.example.com/weather")
    data = await svc.get_weather(longitude=-0.1, latitude=51.5, time=1700000000, timezone="UTC")

    params = fake_http.last_instance.captured_params
    assert params["longitude"] == -0.1
    assert params["latitude"] == 51.5
    assert params["time"] == 1700000000
    assert params["timezone"] == "UTC"
    assert params["forecast_days"] == "1"
    assert params["hourly"] == ",".join(WEATHER_TOPICS)
    # The full upstream object is returned unchanged (Django parity).
    assert data == _UPSTREAM_PAYLOAD


async def test_service_defaults_time_to_now(fake_http):
    svc = WeatherService(base_url="https://api.example.com/weather")
    await svc.get_weather(longitude=-0.1, latitude=51.5, timezone="UTC")
    params = fake_http.last_instance.captured_params
    assert "time" in params and params["time"] is not None


async def test_service_uses_timeout(fake_http):
    svc = WeatherService(base_url="https://api.example.com/weather")
    await svc.get_weather(longitude=-0.1, latitude=51.5, time=1700000000, timezone="UTC")
    assert fake_http.last_instance.captured_timeout == 30


async def test_service_topics_match_django(fake_http):
    assert WEATHER_TOPICS == [
        "temperature_2m",
        "showers",
        "windspeed_10m",
        "winddirection_10m",
        "windgusts_10m",
    ]


async def test_service_raises_on_non_200(monkeypatch):
    class _ErrClient(_FakeAsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.response = httpx.Response(500, text="upstream boom")

    monkeypatch.setattr(
        "flight_blender.services.weather_service.httpx.AsyncClient",
        _ErrClient,
    )
    svc = WeatherService(base_url="https://api.example.com/weather")
    with pytest.raises(ValueError, match="Error fetching weather data"):
        await svc.get_weather(longitude=-0.1, latitude=51.5, time=1, timezone="UTC")


# --------------------------------------------------------------------------- #
# Router: success / response-shape parity (patched service via conftest)
# --------------------------------------------------------------------------- #
async def test_router_success_returns_django_shape(client):
    resp = await client.get(WEATHER_URL, params={"latitude": 51.5, "longitude": -0.1})
    assert resp.status_code == 200
    body = resp.json()
    # Full upstream object is forwarded; declared Django fields present.
    for key in (
        "latitude",
        "longitude",
        "generationtime_ms",
        "utc_offset_seconds",
        "timezone",
        "timezone_abbreviation",
        "elevation",
        "hourly_units",
        "hourly",
    ):
        assert key in body
    assert body["generationtime_ms"] == 0.123
    assert body["elevation"] == 25.0
    # Django parity: no synthetic current_weather field.
    assert "current_weather" not in body


async def test_router_forwards_time(client, monkeypatch):
    captured = {}

    async def _fake(self, **kwargs):
        captured.update(kwargs)
        return _UPSTREAM_PAYLOAD

    monkeypatch.setattr(WeatherService, "get_weather", _fake)
    resp = await client.get(WEATHER_URL, params={"latitude": 51.5, "longitude": -0.1, "time": 1700000000})
    assert resp.status_code == 200
    assert captured["time"] == 1700000000


# --------------------------------------------------------------------------- #
# Router: validation / error-contract parity (Django: 400 + {"error": ...})
# --------------------------------------------------------------------------- #
async def test_router_missing_longitude_returns_400(client):
    resp = await client.get(WEATHER_URL, params={"latitude": 51.5})
    assert resp.status_code == 400
    assert resp.json() == {"error": "Longitude parameter is required"}


async def test_router_missing_latitude_returns_400(client):
    resp = await client.get(WEATHER_URL, params={"longitude": -0.1})
    assert resp.status_code == 400
    assert resp.json() == {"error": "Latitude parameter is required"}


async def test_router_out_of_range_is_forwarded(client, monkeypatch):
    # Django did presence-only validation; out-of-range values go upstream.
    captured = {}

    async def _fake(self, **kwargs):
        captured.update(kwargs)
        return _UPSTREAM_PAYLOAD

    monkeypatch.setattr(WeatherService, "get_weather", _fake)
    resp = await client.get(WEATHER_URL, params={"latitude": 51.5, "longitude": 999.0})
    assert resp.status_code == 200
    assert captured["longitude"] == 999.0


# --------------------------------------------------------------------------- #
# Router: upstream-failure handling -> 502 without leaking the upstream body
# --------------------------------------------------------------------------- #
async def test_router_upstream_failure_502_no_body_leak(client, monkeypatch):
    secret = "SENSITIVE-UPSTREAM-DETAIL"

    async def _boom(self, **kwargs):
        raise ValueError(f"Error fetching weather data: {secret}")

    monkeypatch.setattr(WeatherService, "get_weather", _boom)
    resp = await client.get(WEATHER_URL, params={"latitude": 51.5, "longitude": -0.1})
    assert resp.status_code == 502
    assert secret not in resp.text


async def test_router_upstream_httpx_error_502(client, monkeypatch):
    async def _boom(self, **kwargs):
        request = httpx.Request("GET", "https://api.example.com/weather")
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(WeatherService, "get_weather", _boom)
    resp = await client.get(WEATHER_URL, params={"latitude": 51.5, "longitude": -0.1})
    assert resp.status_code == 502


# --------------------------------------------------------------------------- #
# Router: scope enforcement via the real (non-bypassed) verification path
# --------------------------------------------------------------------------- #
@pytest.fixture
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _make_token(private_key, *, scope, kid="test-key", **overrides):
    now = int(time.time())
    payload = {
        "iss": "https://issuer.example.com",
        "aud": "testflight",
        "sub": "test-user",
        "scope": scope,
        "iat": now,
        "exp": now + 3600,
    }
    payload.update(overrides)
    return pyjwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


@pytest.fixture
def patch_jwks(monkeypatch, rsa_keypair):
    _, public_key = rsa_keypair
    pem = public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    def _fake_get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(pem)

    monkeypatch.setattr(
        "flight_blender.auth.jwt_bearer.jwt.PyJWKClient.get_signing_key_from_jwt",
        _fake_get_signing_key_from_jwt,
    )
    yield


@pytest.fixture
def auth_env(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "bypass_auth_token_verification", False)
    monkeypatch.setattr(settings, "auth_server_jwks_uri", "https://issuer.example.com/jwks")
    monkeypatch.setattr(settings, "auth_audience", "testflight")
    yield


async def test_weather_requires_write_scope(client, patch_jwks, auth_env, rsa_keypair):
    """Read scope must be rejected: the weather endpoint requires write (Django parity)."""
    private_key, _ = rsa_keypair
    token = _make_token(private_key, scope="blender.read")
    resp = await client.get(
        WEATHER_URL,
        params={"latitude": 51.5, "longitude": -0.1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_weather_accepts_write_scope(client, patch_jwks, auth_env, rsa_keypair):
    private_key, _ = rsa_keypair
    token = _make_token(private_key, scope="blender.write")
    resp = await client.get(
        WEATHER_URL,
        params={"latitude": 51.5, "longitude": -0.1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code not in (401, 403)


async def test_weather_missing_token_rejected(client, patch_jwks, auth_env):
    resp = await client.get(WEATHER_URL, params={"latitude": 51.5, "longitude": -0.1}, headers={})
    assert resp.status_code == 401
