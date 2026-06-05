import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from jwcrypto import jwk
from loguru import logger

from flight_blender.config import settings

router = APIRouter()


@router.get("/ping")
async def ping():
    return {"message": "pong"}


@router.get("/signing_public_key")
async def signing_public_key():
    keys = []
    if settings.SECRET_KEY:
        try:
            for pem in [settings.SECRET_KEY]:
                key = jwk.JWK.from_pem(pem.encode("utf8"))
                data = {"alg": "RS256", "use": "sig", "kid": key.thumbprint()}
                data.update(json.loads(key.export_public()))
                keys.append(data)
        except Exception:
            logger.exception("Error during signing public key.")
    return JSONResponse({"keys": keys}, headers={"Access-Control-Allow-Origin": "*"})
