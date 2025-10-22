import json
import logging
from functools import wraps
from os import environ as env
from urllib.parse import urlparse

import jwt
import requests
from django.contrib.auth import authenticate
from django.http import JsonResponse
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())
logger = logging.getLogger("django")


def jwt_get_username_from_payload_handler(payload):
    username = payload.get("sub").replace("|", ".")
    authenticate(remote_user=username)
    return username


def requires_scopes(required_scopes, allow_any: bool = False):
    """
    Decorator to enforce required scopes for accessing a view.

    Args:
        required_scopes (list): A list of scopes required to access the decorated view.

    Returns:
        function: The decorated function which checks for the required scopes.

    The decorator performs the following steps:
    1. Extracts the authorization token from the request headers.
    2. Verifies the token using the public keys from the JWKS endpoint.
    3. Decodes the token and checks if it contains the required scopes.
    4. If the token is valid and contains the required scopes, the original function is executed.
    5. If the token is invalid or does not contain the required scopes, an appropriate JSON response is returned.

    Raises:
        JsonResponse: If the authorization token is missing, invalid, or does not contain the required scopes.
    """

    s = requests.Session()

    def require_scope(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            API_IDENTIFIER = env.get(
                "PASSPORT_AUDIENCE", "testflight.flightblender.com"
            )
            BYPASS_AUTH_TOKEN_VERIFICATION = int(
                env.get("BYPASS_AUTH_TOKEN_VERIFICATION", 0)
            )
            PASSPORT_URL = env.get("PASSPORT_URL", "http://local.test:9000")
            # remove the trailing slash if present
            if PASSPORT_URL.endswith("/"):
                PASSPORT_URL = PASSPORT_URL[:-1]
            PASSPORT_JWKS_URL = f"{PASSPORT_URL}/.well-known/jwks.json"

            DSS_AUTH_JWKS_ENDPOINT = f"{env.get('DSS_AUTH_JWKS_ENDPOINT', 'http://local.test:9000')}/.well-known/jwks.json"

            request = args[0]
            auth = request.META.get("HTTP_AUTHORIZATION", None)
            if not auth or len(parts := auth.split()) <= 1:
                return JsonResponse(
                    {"detail": "Authentication credentials were not provided"},
                    status=401,
                )

            token = parts[1]
            try:
                unverified_token_headers = jwt.get_unverified_header(token)
            except jwt.DecodeError:
                return JsonResponse(
                    {"detail": "Bearer token could not be decoded properly"}, status=401
                )

            if BYPASS_AUTH_TOKEN_VERIFICATION:
                return handle_bypass_verification(
                    token, required_scopes, f, *args, **kwargs
                )

            try:

                passport_jwks_data_response = s.get(PASSPORT_JWKS_URL)

                passport_jwks_data = passport_jwks_data_response.json()

            except requests.exceptions.RequestException as e:
                passport_jwks_data = {}
                logger.error(f"Error fetching Passport JWKS: {e}")
                return JsonResponse(
                    {
                        "detail": "Public Key Server necessary to validate the token could not be reached",
                        "error": str(e),
                        "passport_url": PASSPORT_JWKS_URL,
                        "url_response_status": (
                            passport_jwks_data_response.status_code
                            if "passport_jwks_data_response" in locals()
                            else "N/A"
                        ),
                        "url_response_content": (
                            passport_jwks_data_response.text
                            if "passport_jwks_data_response" in locals()
                            else "N/A"
                        ),
                    },
                    status=400,
                )
            try:
                dss_jwks_data = s.get(DSS_AUTH_JWKS_ENDPOINT).json()
            except requests.exceptions.RequestException as e:
                logger.error(f"Error fetching DSS JWKS: {e}")
                dss_jwks_data = {}
                logger.info(
                    "DSS Public Key Server necessary to validate the token could not be reached, tokens for DSS operations will not be validated"
                )
            # Combine keys from both JWKS sources
            jwks_keys = passport_jwks_data.get("keys", []) + dss_jwks_data.get(
                "keys", []
            )
            jwks_data = {"keys": jwks_keys}

            public_keys = {
                jwk["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
                for jwk in jwks_data["keys"]
            }

            kid = unverified_token_headers.get("kid")
            if not kid or kid not in public_keys:
                return JsonResponse(
                    {
                        "detail": f"Error in parsing public keys, the signing key id {kid} is not present in JWKS"
                    },
                    status=401,
                )

            public_key = public_keys[kid]
            try:
                decoded = jwt.decode(
                    token,
                    public_key,
                    audience=API_IDENTIFIER,
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
            ) as token_error:
                logger.error(f"Token verification failed: {token_error}")
                return JsonResponse(
                    {"detail": "Invalid token", "error details": f"{token_error}"},
                    status=401,
                )
            decoded_scopes_set = set(decoded.get("scope", "").split())
            if (allow_any and decoded_scopes_set & set(required_scopes)) or set(
                required_scopes
            ).issubset(decoded_scopes_set):
                return f(*args, **kwargs)

            return JsonResponse(
                {"message": "You don't have access to this resource"}, status=403
            )

        return decorated

    return require_scope


def handle_bypass_verification(token, required_scopes, f, *args, **kwargs):
    try:
        unverified_token_details = jwt.decode(
            token, algorithms=["RS256"], options={"verify_signature": False}
        )
    except jwt.DecodeError:
        return JsonResponse({"detail": "Invalid token provided"}, status=401)
    decoded_scopes_set = set(unverified_token_details.get("scope", "").split())
    if not set(required_scopes).issubset(decoded_scopes_set):
        return JsonResponse(
            {"message": "You don't have access to this resource"}, status=403
        )

    iss = unverified_token_details.get("iss")
    if not iss:
        return JsonResponse(
            {
                "detail": "Incomplete token provided, issuer (iss) claim must be present and should not be empty"
            },
            status=401,
        )
    if iss != "dummy":
        parsed_iss = urlparse(iss)
        if not (parsed_iss.scheme in ("http", "https") and parsed_iss.netloc):
            return JsonResponse(
                {"detail": "Issuer (iss) claim is not a valid URL"}, status=401
            )

    if not unverified_token_details.get("aud"):
        return JsonResponse(
            {
                "detail": "Incomplete token provided, audience claim must be present and should not be empty"
            },
            status=401,
        )

    return f(*args, **kwargs)


class BearerAuth(requests.auth.AuthBase):
    def __init__(self, token):
        self.token = token

    def __call__(self, r):
        r.headers["authorization"] = "Bearer " + self.token
        return r
