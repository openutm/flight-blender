import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flight_blender.db.session import Base
from flight_blender.models.surveillance_orm import SurveillanceHeartbeatEventORM, SurveillanceSessionORM, SurveillanceTrackEventORM
from flight_blender.repositories.surveillance_repo import SQLAlchemySurveillanceRepository


@pytest.mark.asyncio
async def test_cleanup_old_events_deletes_only_events_before_cutoff():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    cutoff = datetime(2026, 1, 15, tzinfo=timezone.utc)
    session_id = uuid.uuid4()

    async with session_factory() as db:
        db.add(SurveillanceSessionORM(id=session_id, valid_until=cutoff + timedelta(days=1)))
        db.add_all(
            [
                SurveillanceHeartbeatEventORM(
                    session_id=session_id,
                    expected_at=cutoff - timedelta(days=2),
                    dispatched_at=cutoff - timedelta(days=2),
                    delivered_on_time=True,
                ),
                SurveillanceHeartbeatEventORM(
                    session_id=session_id,
                    expected_at=cutoff,
                    dispatched_at=cutoff,
                    delivered_on_time=True,
                ),
                SurveillanceTrackEventORM(
                    session_id=session_id,
                    expected_at=cutoff - timedelta(days=1),
                    dispatched_at=cutoff - timedelta(days=1),
                    had_active_tracks=True,
                ),
                SurveillanceTrackEventORM(
                    session_id=session_id,
                    expected_at=cutoff + timedelta(seconds=1),
                    dispatched_at=cutoff + timedelta(seconds=1),
                    had_active_tracks=True,
                ),
            ]
        )
        await db.flush()

        deleted = await SQLAlchemySurveillanceRepository(db).cleanup_old_events(cutoff=cutoff)

        heartbeat_count = await db.scalar(select(func.count()).select_from(SurveillanceHeartbeatEventORM))
        track_count = await db.scalar(select(func.count()).select_from(SurveillanceTrackEventORM))

    await engine.dispose()

    assert deleted == (1, 1)
    assert heartbeat_count == 1
    assert track_count == 1
