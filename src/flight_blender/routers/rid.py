"""
FastAPI router for Remote ID (RID) operations.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
from flight_blender.database import get_db
from flight_blender.models.rid import ISASubscription
from flight_blender.models.notification import OperatorRIDNotification
from flight_blender.schemas.rid import (
    CreateDSSSubscriptionRequest,
    CreateTestRequest,
    ISASubscriptionResponse,
    RIDCapabilitiesResponse,
    RIDDisplayDataResponse,
    RIDFlightDetailsResponse,
    RIDTestResponse,
    RIDUserNotificationsResponse,
)
from flight_blender.tasks.rid import submit_dss_subscription

router = APIRouter()


@router.post("/create_dss_subscription", response_model=ISASubscriptionResponse, status_code=status.HTTP_201_CREATED, dependencies=[WriteDep])
async def create_dss_subscription(payload: CreateDSSSubscriptionRequest, db: AsyncSession = Depends(get_db)):
    """Create a RID subscription on the DSS asynchronously."""
    import hashlib

    view_hash = hashlib.sha256(payload.view.encode()).hexdigest()[:64]
    sub = ISASubscription(
        subscription_id="",  # Will be populated by Celery task
        view=payload.view,
        flight_details="{}",
        end_datetime=payload.end_datetime,
        view_hash=view_hash,
    )
    db.add(sub)
    await db.flush()
    await db.refresh(sub)
    submit_dss_subscription.delay(str(sub.id), payload.view, payload.end_datetime.isoformat())
    return sub


@router.get("/uss/identification_service_areas/{isa_id}", dependencies=[ReadDep])
async def dss_isa_callback(isa_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    """DSS callback endpoint for ISA notifications."""
    return {"subscriptions": []}


@router.get("/get_rid_data/{subscription_id}", dependencies=[ReadDep])
async def get_rid_data(subscription_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    """Return flight RID data for a subscription."""
    sub = await db.get(ISASubscription, subscription_id)
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    import json

    details = json.loads(sub.flight_details or "{}")
    return {"subscription_id": str(subscription_id), "flights": details.get("flights", [])}


@router.get("/display_data", response_model=RIDDisplayDataResponse, dependencies=[ReadDep])
async def get_rid_display_data(
    view: str = Query(..., description="Bounding box: 'lat_lo,lng_lo,lat_hi,lng_hi'"),
):
    """Return all RID flights visible within the given view box."""
    return RIDDisplayDataResponse(flights=[])


@router.get("/display_data/{flight_id}", response_model=RIDFlightDetailsResponse, dependencies=[ReadDep])
async def get_rid_flight_detail(flight_id: str = Path(...)):
    """Return detailed RID data for a specific flight."""
    return RIDFlightDetailsResponse(id=flight_id)


# ── USS Qualifier test harness ─────────────────────────────────────────────────


@router.post("/tests/{test_id}", response_model=RIDTestResponse, status_code=status.HTTP_201_CREATED, dependencies=[WriteDep])
async def create_rid_test(payload: CreateTestRequest, test_id: uuid.UUID = Path(...)):
    logger.info("RID test created: %s", test_id)
    return RIDTestResponse(version=1)


@router.delete("/tests/{test_id}/{version}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[WriteDep])
async def delete_rid_test(test_id: uuid.UUID = Path(...), version: int = Path(...)):
    logger.info("RID test deleted: %s v%s", test_id, version)


# ── Notifications & capabilities ───────────────────────────────────────────────


@router.get("/user_notifications", response_model=RIDUserNotificationsResponse, dependencies=[ReadDep])
async def get_user_notifications(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OperatorRIDNotification).where(OperatorRIDNotification.is_active == True).limit(50))  # noqa: E712
    notifications = [{"id": str(n.id), "message": n.message, "session_id": n.session_id} for n in result.scalars().all()]
    return RIDUserNotificationsResponse(notifications=notifications)


@router.get("/capabilities", response_model=RIDCapabilitiesResponse, dependencies=[ReadDep])
async def get_rid_capabilities():
    return RIDCapabilitiesResponse(capabilities=["ASTM_F3411_22a", "ASTM_F3411_19"])
