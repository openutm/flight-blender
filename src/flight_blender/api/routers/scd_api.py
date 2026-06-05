from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from flight_blender.api.dependencies import require_scopes
from flight_blender.services import scd_svc

router = APIRouter(prefix="/scd")


@router.get("/v1/status")
async def scd_test_status(_auth: Any = Depends(require_scopes(["utm.inject_test_data"]))):
    data = scd_svc.get_scd_test_status()
    return JSONResponse(data, status_code=200)


@router.get("/v1/capabilities")
async def scd_test_capabilities(_auth: Any = Depends(require_scopes(["utm.inject_test_data"]))):
    data = scd_svc.get_scd_test_capabilities()
    return JSONResponse(data, status_code=200)


@router.get("/flight_planning/status")
@router.get("/flight_planning/u_space/status")
async def flight_planning_status(_auth: Any = Depends(require_scopes(["interuss.flight_planning.direct_automated_test"]))):
    data = scd_svc.get_flight_planning_status()
    return JSONResponse(data, status_code=200)


@router.post("/flight_planning/clear_area_requests")
@router.post("/flight_planning/u_space/clear_area_requests")
async def flight_planning_clear_area_request(
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.direct_automated_test"])),
):
    data, status_code = await scd_svc.clear_area(body)
    return JSONResponse(data, status_code=status_code)


@router.put("/flight_planning/flight_plans/{flight_plan_id}")
@router.put("/flight_planning/u_space/flight_plans/{flight_plan_id}")
async def upsert_flight_plan(
    flight_plan_id: UUID,
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.plan"])),
):
    data, status_code = await scd_svc.upsert_flight_plan(str(flight_plan_id), body)
    return JSONResponse(data, status_code=status_code)


@router.delete("/flight_planning/flight_plans/{flight_plan_id}")
@router.delete("/flight_planning/u_space/flight_plans/{flight_plan_id}")
async def delete_flight_plan(
    flight_plan_id: UUID,
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.plan"])),
):
    data, status_code = await scd_svc.delete_flight_plan(str(flight_plan_id))
    return JSONResponse(data, status_code=status_code)
