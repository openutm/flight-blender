import json

import jwcrypto.jwk as jwk
from loguru import logger

from flight_blender.config import settings


def get_signing_public_keys() -> list[dict]:
    keys: list[dict] = []
    if not settings.SECRET_KEY:
        return keys
    try:
        for pem in [settings.SECRET_KEY]:
            key = jwk.JWK.from_pem(pem.encode("utf8"))
            data = {"alg": "RS256", "use": "sig", "kid": key.thumbprint()}
            data.update(json.loads(key.export_public()))
            keys.append(data)
    except Exception:
        logger.exception("Error during signing public key.")
    return keys
