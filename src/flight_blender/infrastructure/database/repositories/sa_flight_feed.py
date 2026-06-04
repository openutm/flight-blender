import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import arrow
from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.flight_feed.data_definitions import SingleAirtrafficObservation
from flight_blender.infrastructure.database.models.flight_feed import FlightObservationORM, SignedTelmetryPublicKeyORM


def _normalize_timestamp(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        timestamp = float(ts)
    except (TypeError, ValueError):
        logger.warning("Invalid sensor timestamp {!r}; skipping", ts)
        return None
    if timestamp > 1e15:
        timestamp = timestamp / 1_000_000
    elif timestamp > 1e12:
        timestamp = timestamp / 1_000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        logger.warning("Out-of-range sensor timestamp {!r}; skipping", ts)
        return None


class SQLAlchemyFlightFeedRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def write_flight_observation(self, single_observation: SingleAirtrafficObservation) -> bool:
        session_id = single_observation.session_id or "00000000-0000-0000-0000-000000000000"
        sensor_timestamp = _normalize_timestamp(single_observation.timestamp)
        obs = FlightObservationORM(
            session_id=session_id,
            latitude_dd=single_observation.lat_dd,
            longitude_dd=single_observation.lon_dd,
            altitude_mm=single_observation.altitude_mm,
            traffic_source=single_observation.traffic_source,
            source_type=single_observation.source_type,
            icao_address=single_observation.icao_address,
            raw_metadata=json.dumps(single_observation.metadata),
            sensor_timestamp=sensor_timestamp,
        )
        self.db.add(obs)
        await self.db.flush()
        return True

    async def bulk_write_flight_observations(self, observations: list[SingleAirtrafficObservation]) -> bool:
        objs = []
        for o in observations:
            session_id = o.session_id or "00000000-0000-0000-0000-000000000000"
            objs.append(
                FlightObservationORM(
                    session_id=session_id,
                    latitude_dd=o.lat_dd,
                    longitude_dd=o.lon_dd,
                    altitude_mm=o.altitude_mm,
                    traffic_source=o.traffic_source,
                    source_type=o.source_type,
                    icao_address=o.icao_address,
                    raw_metadata=json.dumps(o.metadata),
                )
            )
        self.db.add_all(objs)
        await self.db.flush()
        return True

    async def get_flight_observations(self, after_datetime: arrow.Arrow) -> list[FlightObservationORM]:
        result = await self.db.execute(
            select(FlightObservationORM).where(FlightObservationORM.created_at >= after_datetime.datetime).order_by(FlightObservationORM.created_at)
        )
        return list(result.scalars().all())

    async def get_closest_flight_observation_for_now(self, now: arrow.Arrow) -> list[FlightObservationORM]:
        one_second_before = now.shift(seconds=-1)
        result = await self.db.execute(
            select(FlightObservationORM).where(
                and_(
                    FlightObservationORM.created_at >= one_second_before.datetime,
                    FlightObservationORM.created_at <= now.datetime,
                )
            )
        )
        return list(result.scalars().all())

    async def get_flight_observations_by_session(self, session_id: str, after_datetime: arrow.Arrow) -> list[FlightObservationORM]:
        result = await self.db.execute(
            select(FlightObservationORM)
            .where(
                and_(
                    FlightObservationORM.session_id == session_id,
                    FlightObservationORM.created_at >= after_datetime.datetime,
                    FlightObservationORM.traffic_source != 11,
                )
            )
            .order_by(FlightObservationORM.created_at)
        )
        return list(result.scalars().all())

    async def get_all_flight_observations_in_window(self, start_time: datetime, end_time: datetime) -> list[FlightObservationORM]:
        result = await self.db.execute(
            select(FlightObservationORM).where(
                and_(
                    FlightObservationORM.created_at >= start_time,
                    FlightObservationORM.created_at <= end_time,
                )
            )
        )
        return list(result.scalars().all())

    async def get_latest_flight_observation_by_session(self, session_id: str) -> Optional[FlightObservationORM]:
        result = await self.db.execute(
            select(FlightObservationORM)
            .where(FlightObservationORM.session_id == session_id)
            .order_by(FlightObservationORM.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def list_signed_telemetry_public_keys(self) -> list[SignedTelmetryPublicKeyORM]:
        result = await self.db.execute(select(SignedTelmetryPublicKeyORM))
        return list(result.scalars().all())

    async def get_signed_telemetry_public_key(self, pk: uuid.UUID) -> Optional[SignedTelmetryPublicKeyORM]:
        return await self.db.get(SignedTelmetryPublicKeyORM, pk)

    async def create_signed_telemetry_public_key(self, key_id: str, url: str, is_active: bool = True) -> SignedTelmetryPublicKeyORM:
        key = SignedTelmetryPublicKeyORM(key_id=key_id, url=url, is_active=is_active)
        self.db.add(key)
        await self.db.flush()
        await self.db.refresh(key)
        return key

    async def update_signed_telemetry_public_key(self, pk: uuid.UUID, **kwargs) -> Optional[SignedTelmetryPublicKeyORM]:
        key = await self.get_signed_telemetry_public_key(pk)
        if key is None:
            return None
        for field, value in kwargs.items():
            if hasattr(key, field):
                setattr(key, field, value)
        key.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return key

    async def delete_signed_telemetry_public_key(self, pk: uuid.UUID) -> bool:
        key = await self.get_signed_telemetry_public_key(pk)
        if key is None:
            return False
        await self.db.delete(key)
        await self.db.flush()
        return True

    async def get_active_signed_telemetry_public_keys(self) -> list[SignedTelmetryPublicKeyORM]:
        result = await self.db.execute(select(SignedTelmetryPublicKeyORM).where(SignedTelmetryPublicKeyORM.is_active == True))  # noqa: E712
        return list(result.scalars().all())


class SQLAlchemyFlightFeedSyncRepository:
    """Sync repository for Celery tasks."""

    def __init__(self, db):
        self.db = db

    def write_flight_observation(self, single_observation: SingleAirtrafficObservation) -> bool:
        session_id = single_observation.session_id or "00000000-0000-0000-0000-000000000000"
        sensor_timestamp = _normalize_timestamp(single_observation.timestamp)
        obs = FlightObservationORM(
            session_id=session_id,
            latitude_dd=single_observation.lat_dd,
            longitude_dd=single_observation.lon_dd,
            altitude_mm=single_observation.altitude_mm,
            traffic_source=single_observation.traffic_source,
            source_type=single_observation.source_type,
            icao_address=single_observation.icao_address,
            raw_metadata=json.dumps(single_observation.metadata),
            sensor_timestamp=sensor_timestamp,
        )
        self.db.add(obs)
        self.db.flush()
        return True

    def bulk_write_flight_observations(self, observations: list[SingleAirtrafficObservation]) -> bool:
        objs = []
        for o in observations:
            session_id = o.session_id or "00000000-0000-0000-0000-000000000000"
            objs.append(
                FlightObservationORM(
                    session_id=session_id,
                    latitude_dd=o.lat_dd,
                    longitude_dd=o.lon_dd,
                    altitude_mm=o.altitude_mm,
                    traffic_source=o.traffic_source,
                    source_type=o.source_type,
                    icao_address=o.icao_address,
                    raw_metadata=json.dumps(o.metadata),
                )
            )
        self.db.add_all(objs)
        self.db.flush()
        return True
