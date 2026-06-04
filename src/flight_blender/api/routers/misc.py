import json
from os import environ as env

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/ping")
async def ping():
    return {"message": "pong"}


@router.get("/signing_public_key")
async def signing_public_key():
    from jwcrypto import jwk

    keys = []
    private_key = env.get("SECRET_KEY", None)
    if private_key:
        try:
            for pem in [private_key]:
                key = jwk.JWK.from_pem(pem.encode("utf8"))
                data = {"alg": "RS256", "use": "sig", "kid": key.thumbprint()}
                data.update(json.loads(key.export_public()))
                keys.append(data)
        except Exception:
            pass
    return JSONResponse({"keys": keys}, headers={"Access-Control-Allow-Origin": "*"})
