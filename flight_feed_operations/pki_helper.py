import hashlib
import json
import logging
from os import environ as env

import http_sfv
import jwt
import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from django.core.signing import Signer
from django.http import HttpRequest, HttpResponse
from dotenv import find_dotenv, load_dotenv
from http_message_signatures import (
    HTTPMessageSigner,
    HTTPMessageVerifier,
    HTTPSignatureKeyResolver,
    algorithms,
)
from jwcrypto import jwk, jws
from jwcrypto.common import json_encode

from auth_helper.common import get_redis

from .models import SignedTelmetryPublicKey

load_dotenv(find_dotenv())


logger = logging.getLogger("django")


class MyHTTPSignatureKeyResolver(HTTPSignatureKeyResolver):
    """
    A custom HTTPSignatureKeyResolver that resolves public and private keys for HTTP signatures.

    Attributes:
        jwk (str): JSON Web Key (JWK) used to resolve the public key.

    Methods:
        __init__(jwk):
            Initializes the MyHTTPSignatureKeyResolver with the provided JWK.
        resolve_public_key(key_id=None):
            Resolves and returns the public key from the provided JWK.
        resolve_private_key(key_id: str):
            Resolves and returns the private key from the environment variable 'IETF_SIGNING_KEY'.
    """

    def __init__(self, jwk):
        self.jwk = jwk

    def resolve_public_key(self, key_id=None):
        """
        Resolves and returns the public key from the JSON Web Key (JWK).
        Args:
            key_id (str, optional): The key ID to resolve. Defaults to None.
        Returns:
            public_key: The resolved public key.
        """

        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(self.jwk)
        return public_key

    def resolve_private_key(self, key_id: str):
        """
        Resolves and loads a private key from environment variables.
        This method retrieves the private key in PEM format from the environment
        variable 'IETF_SIGNING_KEY', decodes it, and loads it using the cryptography
        library.
        Args:
            key_id (str): The identifier for the key. (Currently not used in the method)
        Returns:
            private_key: The loaded private key object.
        Raises:
            ValueError: If the private key is not found in the environment variables.
            Exception: If there is an error loading the private key.
        """

        private_key_pem = env.get("IETF_SIGNING_KEY", "")
        if not private_key_pem:
            raise ValueError("Private key not found in environment variables.")
        try:
            private_key = load_pem_private_key(
            private_key_pem.encode("utf-8"),
            password=None,
            backend=default_backend(),
            )
        except Exception as e:
            logger.error(f"Failed to load private key: {e}")
            raise
        return private_key

class MessageVerifier:
    """
    A class to verify messages using public keys.
    Methods
    -------
    get_public_keys():
        Retrieves and caches public keys from a remote source or Redis.
    verify_message(request) -> bool:
        Verifies the message using stored public keys.
    """
    """
    Retrieves and caches public keys from a remote source or Redis.
    Returns
    -------
    dict
        A dictionary of public keys with key IDs as keys and key details as values.
    """
    # method implementation
    """
    Verifies the message using stored public keys.
    Parameters
    ----------
    request : Request
        The request object containing the message to be verified.
    Returns
    -------
    bool
        True if the message is successfully verified, False otherwise.
    """
    # method implementation

    def get_public_keys(self):
        """
        Retrieve public keys from the database and cache them in Redis.
        This method fetches all active public keys from the SignedTelmetryPublicKey model.
        For each key, it checks if the key is already cached in Redis. If not, it retrieves
        the key from the specified URL, caches it in Redis, and sets an expiration time.
        Returns:
            dict: A dictionary where the keys are the key IDs (kid) and the values are the
                  corresponding public key data.
        """

        r = get_redis()
        s = requests.Session()

        
        public_keys = {}
        all_public_keys = SignedTelmetryPublicKey.objects.filter(is_active=1)
        for current_public_key in all_public_keys:
            redis_jwks_key = str(current_public_key.id) + "-jwks"
            current_kid = current_public_key.key_id
            if r.exists(redis_jwks_key):
                k = r.get(redis_jwks_key)
                key = json.loads(k)
            else:
                response = s.get(current_public_key.url)
                jwks_data = response.json()
                jwk = None

                if "keys" in jwks_data:
                    jwk = next((item for item in jwks_data["keys"] if item["kid"] == current_kid), None)
                elif "kid" in jwks_data and current_kid == jwks_data["kid"]:
                    jwk = jwks_data

                key = jwk if jwk else {"000"}

                r.set(redis_jwks_key, json.dumps(key))
                r.expire(redis_jwks_key, 60000)

            public_keys[current_kid] = key

        return public_keys

    def verify_message(self, request) -> bool:
        """
        Verifies the authenticity of a given HTTP request using stored public keys.
        Args:
            request: The HTTP request object to be verified. It should contain the
                     necessary data and headers for verification.
        Returns:
            bool: True if the request is successfully verified using the stored
                  public keys, False otherwise.
        Raises:
            HTTPMessageVerifierError: If the verification process fails for any reason.
        """
        stored_public_keys = self.get_public_keys()
        if not stored_public_keys:
            return False

        r = requests.Request(
            "PUT",
            request.build_absolute_uri(),
            json=request.data,
            headers=request.headers,
        )

        for key_id, jwk_detail in stored_public_keys.items():
            verifier = HTTPMessageVerifier(
            signature_algorithm=algorithms.RSA_PSS_SHA512,
            key_resolver=MyHTTPSignatureKeyResolver(jwk=jwk_detail),
            )
            try:
                verifier.verify(r)
                return True
            except Exception as e:
                logger.error(f"Verification failed for key_id {key_id}: {e}")

        return False


class ResponseSigningOperations:
    """
    A class to handle various response signing operations.
    Attributes:
    ----------
    signing_url : str
        The URL used for signing the flight passport.
    signing_client_id : str
        The client ID used for signing the flight passport.
    signing_client_secret : str
        The client secret used for signing the flight passport.
    signing_key_id : str
        The key ID used for signing, default is "temp_id".
    signing_key_label : str
        The key label used for signing, default is "temp_label".
    Methods:
    -------
    generate_content_digest(payload):
        Generates a content digest for the given payload.
    sign_json_via_django(data_to_sign):
        Signs the given data using Django's signing mechanism.
    sign_json_via_jose(payload):
        Signs the given payload using JOSE and returns a signed JWS.
    sign_http_message(json_payload, original_request: HttpRequest) -> HttpResponse:
        Signs the HTTP response message using IETF standard and returns an HttpResponse object.
    """
    def __init__(self):
        self.signing_url = env.get("FLIGHT_PASSPORT_SIGNING_URL", None)
        self.signing_client_id = env.get("FLIGHT_PASSPORT_SIGNING_CLIENT_ID")
        self.signing_client_secret = env.get("FLIGHT_PASSPORT_SIGNING_CLIENT_SECRET")

        self.signing_key_id = env.get("IETF_SIGNING_KEY_ID", "temp_id")
        self.signing_key_label = env.get("IETF_SIGNING_KEY_LABEL", "temp_label")

    def generate_content_digest(self, payload):
        payload_str = json.dumps(payload)
        return str(http_sfv.Dictionary({"sha-256": hashlib.sha256(payload_str.encode("utf-8")).digest()}))

    def sign_json_via_django(self, data_to_sign):
        signer = Signer()
        signed_obj = signer.sign_object(data_to_sign)
        return signed_obj

    def sign_json_via_jose(self, payload):
        """
        Sign a JSON payload using the OIDC private key and return the signed JWS.

        This method uses the RS256 algorithm to sign the provided JSON payload.
        The private key is retrieved from the environment variable 'SECRET_KEY'.
        If the key is available and valid, the payload is signed and the JWS token
        is returned in a dictionary format. If the key is not available or invalid,
        an empty dictionary is returned.

        Args:
            payload (dict): The JSON payload to be signed.

        Returns:
            dict: A dictionary containing the signed JWS token with the key 'signature',
                  or an empty dictionary if signing fails.

                  
        """
        algorithm = "RS256"
        private_key_pem = env.get("SECRET_KEY", None)
        
        if not private_key_pem:
            return {}

        try:
            key = jwk.JWK.from_pem(private_key_pem.encode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to load private key: {e}")
            return {}

        payload_str = json.dumps(payload)
        jws_token = jws.JWS(payload=payload_str)

        jws_token.add_signature(
            key=key,
            alg=algorithm,
            protected=json_encode({"alg": algorithm, "kid": key.thumbprint()}),
        )

        sig = jws_token.serialize()
        signature = json.loads(sig)

        return {"signature": f"{signature['protected']}.{signature['payload']}.{signature['signature']}"}

    def sign_http_message(self, json_payload, original_request: HttpRequest) -> HttpResponse:

        """
        Sign the HTTP response message using IETF standard and return an HttpResponse object.
        This method takes a JSON payload and an original HttpRequest, creates an HttpResponse
        object, and signs it using the HTTP Message Signatures standard.
        Args:
            json_payload (dict): The JSON payload to include in the HTTP response.
            original_request (HttpRequest): The original HTTP request object.
        Returns:
            HttpResponse: The signed HTTP response object.
        References:
            - IETF HTTP Message Signatures: https://datatracker.ietf.org/doc/draft-ietf-httpbis-message-signatures/

        """
        response = HttpResponse(json.dumps(json_payload), content_type="application/json")
        response.url = original_request.build_absolute_uri()
        response.request = original_request

        content_digest = self.generate_content_digest(payload=json_payload)
        response["Content-Digest"] = content_digest

        signer = HTTPMessageSigner(
            signature_algorithm=algorithms.RSA_PSS_SHA512,
            key_resolver=MyHTTPSignatureKeyResolver(jwk=None),
        )
        signer.sign(
            response,
            key_id=self.signing_key_id,
            covered_component_ids=(
            "@method",
            "@authority",
            "@target-uri",
            "content-digest",
            ),
            label=self.signing_key_label,
        )

        return response
