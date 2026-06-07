from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/versioning")

SUPPORTED_SYSTEMS = {
    "astm.f3548.v21",
    "astm.f3411.v22a",
}


class SystemVersionResponse(BaseModel):
    system_identity: str
    system_version: str


@router.get("/versions/{system_id}", response_model=SystemVersionResponse)
async def get_system_version(system_id: str) -> SystemVersionResponse | None:
    if system_id not in SUPPORTED_SYSTEMS:
        return None

    try:
        app_version = version("flight-blender")
    except PackageNotFoundError:
        app_version = "0.1.0"

    return SystemVersionResponse(
        system_identity=system_id,
        system_version=f"openutm/flight-blender/{app_version}",
    )
