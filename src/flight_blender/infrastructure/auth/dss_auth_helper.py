import json
from datetime import datetime, timedelta

import requests
from loguru import logger

from flight_blender.config import settings
from flight_blender.infrastructure.auth.redis_helpers import get_redis


class AuthorityCredentialsGetter:
    """
    A class to handle the retrieval and caching of authority credentials.
    Methods
    -------
    __init__():
        Initializes the AuthorityCredentialsGetter with a Redis connection and the current datetime.
    get_cached_credentials(audience: str, token_type: str):
        Retrieves cached credentials if available and valid, otherwise fetches new credentials and caches them.
    _get_credentials(audience: str, token_type: str):
        Determines the type of credentials to fetch based on the token type.
    _cache_credentials(cache_key: str, credentials: dict):
        Caches the credentials in Redis with a specified expiration time.
    _get_rid_credentials(audience: str):
        Fetches RID (Remote ID) credentials for the given audience.
    _get_scd_credentials(audience: str):
        Fetches SCD (Strategic Coordination) credentials for the given audience.
    _get_cmsa_credentials(audience: str):
        Fetches CMSA (Conformance Monitoring Service Area) credentials for the given audience.
    _request_credentials(audience: str, scope: str):
        Makes a request to the authentication service to retrieve credentials for the given audience and scope.
    """

    def __init__(self):
        self.redis = get_redis()
        self.now = datetime.now()

    def get_cached_credentials(self, audience: str, token_type: str):
        if token_type == "rid":  # nosec B105
            token_suffix = "_auth_rid_token"  # nosec B105
        elif token_type == "scd":  # nosec B105
            token_suffix = "_auth_scd_token"  # nosec B105
        elif token_type == "constraints":  # nosec B105
            token_suffix = "_auth_constraints_token"  # nosec B105

        cache_key = audience + token_suffix
        token_details = self.redis.get(cache_key)
        logger.info("Retrieved cached token details..")

        if token_details:
            token_details = json.loads(token_details)
            created_at = token_details["created_at"]
            set_date = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%f")
            if self.now < (set_date + timedelta(minutes=58)):
                return token_details["credentials"]

        credentials = self._get_credentials(audience, token_type)
        self._cache_credentials(cache_key, credentials)
        return credentials

    def _get_credentials(self, audience: str, token_type: str):
        if token_type == "rid":  # nosec B105
            return self._get_rid_credentials(audience)
        elif token_type == "scd":  # nosec B105
            return self._get_scd_credentials(audience)
        elif token_type == "constraints":  # nosec B105
            return self._get_constraints_credentials(audience)
        else:
            raise ValueError("Invalid token type")

    def _cache_credentials(self, cache_key: str, credentials: dict):
        self.redis.set(
            cache_key,
            json.dumps({"credentials": credentials, "created_at": self.now.isoformat()}),
        )
        self.redis.expire(cache_key, timedelta(minutes=58))

    def _get_rid_credentials(self, audience: str):
        return self._request_credentials(audience, ["rid.service_provider", "rid.display_provider"])

    def _get_scd_credentials(self, audience: str):
        return self._request_credentials(audience, ["utm.strategic_coordination", "utm.conformance_monitoring_sa"])

    def _get_constraints_credentials(self, audience: str):
        return self._request_credentials(audience, ["utm.constraint_processing"])

    def _request_credentials(self, audience: str, scopes: list[str]):
        issuer = audience if audience == "localhost" else None
        scopes_str = " ".join(scopes)

        auth_server_url = settings.DSS_AUTH_URL + settings.DSS_AUTH_TOKEN_ENDPOINT

        if auth_server_url.startswith("http://local_"):
            payload = {
                "grant_type": "client_credentials",
                "intended_audience": settings.DSS_SELF_AUDIENCE,
                "scope": scopes_str,
                "issuer": issuer,
            }

            token_data = requests.get(auth_server_url, params=payload, timeout=30)
            if token_data.status_code != 200:
                logger.error(f"Failed to get token for audience {audience} with scopes {scopes_str} and URL {auth_server_url}")
                logger.error(f"Payload: {payload}")
                logger.error(f"Failed to get token: {token_data.status_code} - {token_data.text}")
            return token_data.json()
        else:
            payload = {
                "grant_type": "client_credentials",
                "client_id": settings.AUTH_DSS_CLIENT_ID,
                "client_secret": settings.AUTH_DSS_CLIENT_SECRET,
                "audience": audience,
                "scope": scopes_str,
            }

            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            token_data = requests.post(auth_server_url, data=payload, headers=headers, timeout=30)
            if token_data.status_code != 200:
                logger.error(f"Failed to get token for audience {audience} with scopes {scopes_str} and URL {auth_server_url}")
                logger.error(f"Payload: {payload}")
                logger.error(f"Failed to get token: {token_data.status_code} - {token_data.text}")
            return token_data.json()
