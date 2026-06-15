import uuid
from datetime import datetime, timezone
from typing import Optional, cast

from loguru import logger
from sqlalchemy import CursorResult, and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.models.surveillance_orm import (
    SurveillanceHeartbeatEventORM,
    SurveillanceSensorFailureNotificationORM,
    SurveillanceSensorHealthORM,
    SurveillanceSensorHealthTrackingORM,
    SurveillanceSensorORM,
    SurveillanceSessionORM,
    SurveillanceTrackEventORM,
)


class SQLAlchemySurveillanceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_surveillance_sensors(self) -> list[SurveillanceSensorORM]:
        result = await self.db.execute(select(SurveillanceSensorORM).where(SurveillanceSensorORM.is_active == True))  # noqa: E712
        return list(result.scalars().all())

    async def get_sensor_by_id(self, sensor_id: uuid.UUID) -> Optional[SurveillanceSensorORM]:
        return await self.db.get(SurveillanceSensorORM, sensor_id)

    async def get_session_by_id(self, session_id: uuid.UUID) -> Optional[SurveillanceSessionORM]:
        return await self.db.get(SurveillanceSessionORM, session_id)

    async def create_session(self, session_id: uuid.UUID, valid_until: datetime) -> bool:
        existing = await self.get_session_by_id(session_id)
        if existing is not None:
            return False
        session = SurveillanceSessionORM(id=session_id, valid_until=valid_until)
        self.db.add(session)
        try:
            await self.db.flush()
        except Exception:
            logger.exception("Failed to flush new surveillance session %s", session_id)
            await self.db.rollback()
            return False
        return True

    async def delete_session(self, session_id: uuid.UUID) -> None:
        session = await self.get_session_by_id(session_id)
        if session:
            await self.db.delete(session)
            await self.db.flush()

    async def get_all_active_sessions(self) -> list[SurveillanceSessionORM]:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(select(SurveillanceSessionORM).where(SurveillanceSessionORM.valid_until >= now))
        return list(result.scalars().all())

    async def get_sessions_with_events_in_window(self, start_time: datetime, end_time: datetime) -> list[SurveillanceSessionORM]:
        hb_stmt = (
            select(SurveillanceSessionORM)
            .join(SurveillanceHeartbeatEventORM, SurveillanceHeartbeatEventORM.session_id == SurveillanceSessionORM.id)
            .where(
                and_(
                    SurveillanceHeartbeatEventORM.dispatched_at >= start_time,
                    SurveillanceHeartbeatEventORM.dispatched_at <= end_time,
                )
            )
        )
        tr_stmt = (
            select(SurveillanceSessionORM)
            .join(SurveillanceTrackEventORM, SurveillanceTrackEventORM.session_id == SurveillanceSessionORM.id)
            .where(
                and_(
                    SurveillanceTrackEventORM.dispatched_at >= start_time,
                    SurveillanceTrackEventORM.dispatched_at <= end_time,
                )
            )
        )
        hb_ids = {r.id for r in (await self.db.execute(hb_stmt)).scalars().all()}
        tr_ids = {r.id for r in (await self.db.execute(tr_stmt)).scalars().all()}
        all_ids = hb_ids | tr_ids
        if not all_ids:
            return []
        result = await self.db.execute(select(SurveillanceSessionORM).where(SurveillanceSessionORM.id.in_(all_ids)))
        return list(result.scalars().all())

    async def get_sensor_health_record(self, sensor_id: uuid.UUID) -> Optional[SurveillanceSensorHealthORM]:
        result = await self.db.execute(select(SurveillanceSensorHealthORM).where(SurveillanceSensorHealthORM.sensor_id == sensor_id))
        return result.scalars().first()

    async def get_health_tracking_records_for_sensor(
        self, sensor_id: uuid.UUID, start_time: datetime, end_time: datetime
    ) -> list[SurveillanceSensorHealthTrackingORM]:
        result = await self.db.execute(
            select(SurveillanceSensorHealthTrackingORM)
            .where(
                and_(
                    SurveillanceSensorHealthTrackingORM.sensor_id == sensor_id,
                    SurveillanceSensorHealthTrackingORM.recorded_at >= start_time,
                    SurveillanceSensorHealthTrackingORM.recorded_at <= end_time,
                )
            )
            .order_by(SurveillanceSensorHealthTrackingORM.recorded_at)
        )
        return list(result.scalars().all())

    async def get_sensor_status_before_time(self, sensor_id: uuid.UUID, before_time: datetime) -> Optional[str]:
        result = await self.db.execute(
            select(SurveillanceSensorHealthTrackingORM)
            .where(
                and_(
                    SurveillanceSensorHealthTrackingORM.sensor_id == sensor_id,
                    SurveillanceSensorHealthTrackingORM.recorded_at < before_time,
                )
            )
            .order_by(SurveillanceSensorHealthTrackingORM.recorded_at.desc())
            .limit(1)
        )
        record = result.scalars().first()
        return record.status if record else None

    async def get_heartbeat_events_for_session(
        self, session_id: uuid.UUID, start_time: datetime, end_time: datetime
    ) -> list[SurveillanceHeartbeatEventORM]:
        result = await self.db.execute(
            select(SurveillanceHeartbeatEventORM)
            .where(
                and_(
                    SurveillanceHeartbeatEventORM.session_id == session_id,
                    SurveillanceHeartbeatEventORM.dispatched_at >= start_time,
                    SurveillanceHeartbeatEventORM.dispatched_at <= end_time,
                )
            )
            .order_by(SurveillanceHeartbeatEventORM.dispatched_at)
        )
        return list(result.scalars().all())

    async def get_track_events_for_session(self, session_id: uuid.UUID, start_time: datetime, end_time: datetime) -> list[SurveillanceTrackEventORM]:
        result = await self.db.execute(
            select(SurveillanceTrackEventORM)
            .where(
                and_(
                    SurveillanceTrackEventORM.session_id == session_id,
                    SurveillanceTrackEventORM.dispatched_at >= start_time,
                    SurveillanceTrackEventORM.dispatched_at <= end_time,
                )
            )
            .order_by(SurveillanceTrackEventORM.dispatched_at)
        )
        return list(result.scalars().all())

    async def get_failure_notifications_for_sensor(
        self, sensor_id: uuid.UUID, start_time: datetime, end_time: datetime
    ) -> list[SurveillanceSensorFailureNotificationORM]:
        result = await self.db.execute(
            select(SurveillanceSensorFailureNotificationORM)
            .where(
                and_(
                    SurveillanceSensorFailureNotificationORM.sensor_id == sensor_id,
                    SurveillanceSensorFailureNotificationORM.created_at >= start_time,
                    SurveillanceSensorFailureNotificationORM.created_at <= end_time,
                )
            )
            .order_by(SurveillanceSensorFailureNotificationORM.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_all_failure_notifications(self, start_time: datetime, end_time: datetime) -> list[SurveillanceSensorFailureNotificationORM]:
        result = await self.db.execute(
            select(SurveillanceSensorFailureNotificationORM)
            .where(
                and_(
                    SurveillanceSensorFailureNotificationORM.created_at >= start_time,
                    SurveillanceSensorFailureNotificationORM.created_at <= end_time,
                )
            )
            .order_by(SurveillanceSensorFailureNotificationORM.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_sensor_health_status(self, sensor_id: uuid.UUID, new_status: str, recovery_type: Optional[str] = None) -> bool:
        sensor = await self.get_sensor_by_id(sensor_id)
        if sensor is None:
            logger.error(f"update_sensor_health_status: sensor {sensor_id} not found")
            return False

        health = await self.get_sensor_health_record(sensor_id)
        if health is None:
            health = SurveillanceSensorHealthORM(sensor_id=sensor_id, status=new_status)
            self.db.add(health)
            previous_status = new_status
        else:
            previous_status = health.status
            if previous_status == new_status:
                return True
            health.status = new_status
            health.updated_at = datetime.now(timezone.utc)

        tracking = SurveillanceSensorHealthTrackingORM(
            sensor_id=sensor_id,
            status=new_status,
            recovery_type=recovery_type,
        )
        self.db.add(tracking)

        if new_status in ("degraded", "outage"):
            message = f"Sensor '{sensor.sensor_identifier}' entered {new_status} state (was {previous_status})"
        else:
            recovery_label = f" [{recovery_type} recovery]" if recovery_type else ""
            message = f"Sensor '{sensor.sensor_identifier}' recovered to {new_status} (was {previous_status}){recovery_label}"
        notification = SurveillanceSensorFailureNotificationORM(
            sensor_id=sensor_id,
            previous_status=previous_status,
            new_status=new_status,
            recovery_type=recovery_type,
            message=message,
        )
        self.db.add(notification)
        await self.db.flush()
        return True

    async def record_heartbeat_event(self, session_id: uuid.UUID, expected_at: datetime, delivered_on_time: bool) -> bool:
        session = await self.get_session_by_id(session_id)
        if session is None:
            logger.error(f"record_heartbeat_event: session {session_id} not found")
            return False
        event = SurveillanceHeartbeatEventORM(
            session_id=session_id,
            expected_at=expected_at,
            delivered_on_time=delivered_on_time,
        )
        self.db.add(event)
        await self.db.flush()
        return True

    async def record_track_event(self, session_id: uuid.UUID, expected_at: datetime, had_active_tracks: bool) -> bool:
        session = await self.get_session_by_id(session_id)
        if session is None:
            logger.error(f"record_track_event: session {session_id} not found")
            return False
        event = SurveillanceTrackEventORM(
            session_id=session_id,
            expected_at=expected_at,
            had_active_tracks=had_active_tracks,
        )
        self.db.add(event)
        await self.db.flush()
        return True

    async def cleanup_old_events(self, cutoff: datetime) -> tuple[int, int]:
        heartbeat_result = cast(CursorResult, await self.db.execute(delete(SurveillanceHeartbeatEventORM).where(SurveillanceHeartbeatEventORM.dispatched_at < cutoff)))
        track_result = cast(CursorResult, await self.db.execute(delete(SurveillanceTrackEventORM).where(SurveillanceTrackEventORM.dispatched_at < cutoff)))
        await self.db.flush()
        return heartbeat_result.rowcount or 0, track_result.rowcount or 0
