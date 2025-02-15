import json
from functools import wraps
from os import environ as env

import jwt
import requests
from django.contrib.auth import authenticate
from django.http import JsonResponse
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())


def jwt_get_username_from_payload_handler(payload):
    username = payload.get("sub").replace("|", ".")
    authenticate(remote_user=username)
    return username


def requires_scopes(required_scopes):
    """Determines if the required scope is present in the access token
    Args:
        required_scopes (list): The scopes required to access the resource
    """

    s = requests.Session()

    def require_scope(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            # Set the audience of the instance
            API_IDENTIFIER = env.get("PASSPORT_AUDIENCE", "testflight.flightblender.com")
            # Check if the setting is Debug
            BYPASS_AUTH_TOKEN_VERIFICATION = int(env.get("BYPASS_AUTH_TOKEN_VERIFICATION", 0))
            # Use the OAUTH 2.0 standard endpoint
            PASSPORT_JWKS_URL = "{}/.well-known/jwks.json".format(env.get("PASSPORT_URL", "http://local.test:9000"))

            # Get the authorization Metadata
            request = args[0]
            auth = request.META.get("HTTP_AUTHORIZATION", None)
            # If no authorization data provided, then reject the request
            if auth and len(parts := auth.split()) > 1:
                token = parts[1]
            else:
                response = JsonResponse({"detail": "Authentication credentials were not provided"})
                response.status_code = 401
                return response
            # If the token is not decoded properly, the token was provided but is incorrect, return a 401
            try:
                unverified_token_headers = jwt.get_unverified_header(token)
            except jwt.DecodeError:
                response = JsonResponse({"detail": "Bearer token could not be decoded properly"})
                response.status_code = 401
                return response

            if BYPASS_AUTH_TOKEN_VERIFICATION:
                # Debug mode, no need to verify signatures
                try:
                    unverified_token_details = jwt.decode(token, algorithms=["RS256"], options={"verify_signature": False})

                except jwt.DecodeError:
                    response = JsonResponse({"detail": "Invalid token provided"})
                    response.status_code = 401
                    return response

                try:
                    assert "aud" in unverified_token_details
                    assert unverified_token_details["aud"] != ""
                except AssertionError:
                    response = JsonResponse({"detail": "Incomplete token provided, audience claim must be present and should and not empty"})
                    response.status_code = 401
                    return response

                return f(*args, **kwargs)

            # Get the Public key JWKS by making a request
            try:
                jwks_data = s.get(PASSPORT_JWKS_URL).json()
            except requests.exceptions.RequestException:
                response = JsonResponse({"detail": "Public Key Server necessary to validate the token could not be reached"})
                response.status_code = 400
                return response
            # This assumes JWKS (key set) / multiple keys, perhaps have a way to parse JWK only (single key)
            jwks = jwks_data
            public_keys = {}
            for jwk in jwks["keys"]:
                kid = jwk["kid"]
                public_keys[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))

            # Check the token has a key id
            try:
                kid = unverified_token_headers["kid"]
            except (KeyError, ValueError):
                response = JsonResponse({"detail": "There is no kid provided in the token headers / token cannot be verified"})
                response.status_code = 401
                return response
            # Check the public key has the same kid
            try:
                assert kid in public_keys
            except AssertionError:
                response = JsonResponse({"detail": "Error in parsing public keys, the signing key id {kid} is not present in JWKS".format(kid=kid)})
                response.status_code = 401
                return response
            else:
                public_key = public_keys[kid]

            # Public key and unverified token headers processed, decode the token with verification
            try:
                decoded = jwt.decode(
                    token,
                    public_key,
                    audience=API_IDENTIFIER,
                    algorithms=["RS256"],
                    options={"require": ["exp", "iss", "aud"]},
                )
            except jwt.ImmatureSignatureError:
                response = JsonResponse({"detail": "Token Signature has is not valid"})
                response.status_code = 401
                return response
            except jwt.ExpiredSignatureError:
                response = JsonResponse({"detail": "Token Signature has expired"})
                response.status_code = 401
                return response
            except jwt.InvalidAudienceError:
                response = JsonResponse({"detail": "Invalid audience in token"})
                response.status_code = 401
                return response
            except jwt.InvalidIssuerError:
                response = JsonResponse({"detail": "Invalid issuer for token"})
                response.status_code = 401
                return response
            except jwt.InvalidSignatureError:
                response = JsonResponse({"detail": "Invalid signature in token"})
                response.status_code = 401
                return response
            except jwt.DecodeError:
                response = JsonResponse({"detail": "Token cannot be decoded"})
                response.status_code = 401
                return response
            except Exception:
                response = JsonResponse({"detail": "Invalid token"})
                response.status_code = 401
                return response

            if decoded.get("scope"):
                token_scopes = decoded["scope"].split()
                token_scopes_set = set(token_scopes)
                if set(required_scopes).issubset(token_scopes_set):
                    return f(*args, **kwargs)
            response = JsonResponse({"message": "You don't have access to this resource"})
            response.status_code = 403
            return response

        return decorated

    return require_scope


class BearerAuth(requests.auth.AuthBase):
    def __init__(self, token):
        self.token = token

    def __call__(self, r):
        r.headers["authorization"] = "Bearer " + self.token
        return r
