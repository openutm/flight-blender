from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from flight_blender.config import settings
from flight_blender.services import misc_svc
from flight_blender.utils.paths import SRC_FLIGHT_BLENDER_PATH

router = APIRouter()

templates = Jinja2Templates(directory=SRC_FLIGHT_BLENDER_PATH / "templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="homebase/home.html",
        context={"request": request, "auth_bypass_enabled": settings.BYPASS_AUTH_TOKEN_VERIFICATION},
    )


@router.get("/ping")
async def ping():
    return {"message": "pong"}


@router.get("/signing_public_key")
async def signing_public_key():
    keys = misc_svc.get_signing_public_keys()
    return JSONResponse({"keys": keys}, headers={"Access-Control-Allow-Origin": "*"})
