from fastapi import APIRouter
from fastapi.responses import JSONResponse

from flight_blender.services import misc_svc

router = APIRouter()


@router.get("/ping")
async def ping():
    return {"message": "pong"}


@router.get("/signing_public_key")
async def signing_public_key():
    keys = misc_svc.get_signing_public_keys()
    return JSONResponse({"keys": keys}, headers={"Access-Control-Allow-Origin": "*"})
