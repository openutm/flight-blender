"""
DSS OAuth2 client-credentials helper.
"""

import json
from datetime import datetime, timedelta

import requests
from loguru import logger

from flight_blender.common.redis_client import get_redis
from flight_blender.config import get_settings

settings = get_settings()

_TOKEN_CACHE_MINUTES = 58


class AuthorityCredentialsGetter:
    """Retrieves and caches DSS authority credentials in Redis."""

    def __init__(self) -> None:
        self.redis = get_redis()
        self.now = datetime.now()

    # ── Public API ─────────────────────────────────────────────────────────
    def get_cached_credentials(self, audience: str, token_type: str) -> dict:
        token_suffix = self._token_suffix(token_type)
        cache_key = audience + token_suffix
        raw = self.redis.get(cache_key)
        if raw:
            cached = json.loads(raw)
            created_at = datetime.strptime(cached["created_at"], "%Y-%m-%dT%H:%M:%S.%f")
            if self.now < (created_at + timedelta(minutes=_TOKEN_CACHE_MINUTES)):
                return cached["credentials"]

        credentials = self._get_credentials(audience, token_type)
        self._cache_credentials(cache_key, credentials)
        return credentials

    # ── Private helpers ────────────────────────────────────────────────────
    @staticmethod
    def _token_suffix(token_type: str) -> str:
        suffixes = {
            "rid": "_auth_rid_token",  # nosec B105
            "scd": "_auth_scd_token",  # nosec B105
            "constraints": "_auth_constraints_token",  # nosec B105
        }
        try:
            return suffixes[token_type]
        except KeyError as exc:
            raise ValueError(f"Invalid token type: {token_type!r}") from exc

    def _get_credentials(self, audience: str, token_type: str) -> dict:
        dispatch = {
            "rid": lambda: self._request_credentials(audience, ["rid.service_provider", "rid.display_provider"]),
            "scd": lambda: self._request_credentials(audience, ["utm.strategic_coordination", "utm.conformance_monitoring_sa"]),
            "constraints": lambda: self._request_credentials(audience, ["utm.constraint_processing"]),
        }
        try:
            return dispatch[token_type]()
        except KeyError as exc:
            raise ValueError(f"Invalid token type: {token_type!r}") from exc

    def _cache_credentials(self, cache_key: str, credentials: dict) -> None:
        self.redis.set(
            cache_key,
            json.dumps({"credentials": credentials, "created_at": self.now.isoformat()}),
        )
        self.redis.expire(cache_key, timedelta(minutes=_TOKEN_CACHE_MINUTES))

    def _request_credentials(self, audience: str, scopes: list[str]) -> dict:
        scopes_str = " ".join(scopes)
        auth_url = settings.dss_auth_url + settings.dss_auth_token_endpoint

        if auth_url.startswith("http://local_"):
            payload = {
                "grant_type": "client_credentials",
                "intended_audience": settings.dss_self_audience,
                "scope": scopes_str,
                "issuer": audience if audience == "localhost" else None,
            }
            resp = requests.get(auth_url, params=payload, timeout=30)
        else:
            payload = {
                "grant_type": "client_credentials",
                "client_id": settings.auth_dss_client_id,
                "client_secret": settings.auth_dss_client_secret,
                "audience": audience,
                "scope": scopes_str,
            }
            resp = requests.post(auth_url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)

        if resp.status_code != 200:
            logger.error("DSS token request failed: %s %s", resp.status_code, resp.text)
        return resp.json()


def get_dss_auth_header(audience: str, token_type: str = "scd") -> dict[str, str]:
    """Build an Authorization header for DSS / peer-USS requests.

    Parameters
    ----------
    audience:
        The JWT ``aud`` claim (typically ``settings.dss_auth_audience`` or
        ``settings.dss_self_audience``).
    token_type:
        Credential scope identifier forwarded to
        :class:`AuthorityCredentialsGetter` (default ``"scd"``).
    """
    getter = AuthorityCredentialsGetter()
    credentials = getter.get_cached_credentials(audience=audience, token_type=token_type)
    access_token = credentials.get("access_token", "") if isinstance(credentials, dict) else ""
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
