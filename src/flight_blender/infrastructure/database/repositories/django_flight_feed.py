import json
from datetime import datetime, timezone
from typing import Optional

import arrow
from django.db.models import QuerySet
from django.db.utils import IntegrityError
from loguru import logger

from flight_blender.flight_feed.data_definitions import SingleAirtrafficObservation
from flight_blender.flight_feed.models import FlightObservation


class DjangoFlightFeedRepository:
    @staticmethod
    def _normalize_timestamp(ts) -> Optional[datetime]:
        if not ts:
            return None
        try:
            timestamp = float(ts)
        except (TypeError, ValueError):
            logger.warning("Invalid sensor timestamp {!r}; storing observation without sensor_timestamp", ts)
            return None
        if timestamp > 1e15:
            timestamp = timestamp / 1_000_000
        elif timestamp > 1e12:
            timestamp = timestamp / 1_000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            logger.warning("Out-of-range sensor timestamp {!r}; storing observation without sensor_timestamp", ts)
            return None

    def get_flight_observations(self, after_datetime: arrow.Arrow):
        return FlightObservation.objects.filter(created_at__gte=after_datetime.isoformat()).order_by("created_at").values()

    def get_closest_flight_observation_for_now(self, now: arrow.Arrow):
        one_second_before_now = now.shift(seconds=-1)
        return FlightObservation.objects.filter(
            created_at__gte=one_second_before_now.isoformat(),
            created_at__lte=now.isoformat(),
        )

    def get_flight_observation_objects(self):
        return FlightObservation.objects.all().order_by("created_at").values()

    def get_temporal_flight_observations_by_session(self, session_id: str, after_datetime: arrow.Arrow):
        return FlightObservation.objects.filter(session_id=session_id, created_at__gte=after_datetime.isoformat()).order_by("created_at").values()

    def get_flight_observations_by_session(self, session_id: str, after_datetime: arrow.Arrow):
        return (
            FlightObservation.objects.filter(session_id=session_id, created_at__gte=after_datetime.isoformat())
            .exclude(traffic_source=11)
            .order_by("created_at")
            .values()
        )

    def get_all_flight_observations_in_window(self, start_time: datetime, end_time: datetime) -> QuerySet[FlightObservation]:
        return FlightObservation.objects.filter(created_at__gte=start_time, created_at__lte=end_time)

    def get_latest_flight_observation_by_session(self, session_id: str) -> Optional[FlightObservation]:
        try:
            return FlightObservation.objects.filter(session_id=session_id).latest("created_at")
        except FlightObservation.DoesNotExist:
            return None

    def get_active_rid_observations_for_view(self, start_time: datetime, end_time: datetime):
        try:
            return FlightObservation.objects.filter(created_at__gte=start_time, created_at__lte=end_time, traffic_source=11).order_by("-created_at")
        except FlightObservation.DoesNotExist:
            return None

    def get_active_rid_observations_for_session(self, session_id: str):
        try:
            return FlightObservation.objects.filter(session_id=session_id, traffic_source=11).order_by("-created_at")
        except FlightObservation.DoesNotExist:
            return None

    def get_active_rid_observations_for_session_between_interval(self, start_time: datetime, end_time: datetime, session_id: str):
        try:
            return FlightObservation.objects.filter(
                session_id=session_id,
                created_at__gte=start_time,
                created_at__lte=end_time,
                traffic_source=11,
            )
        except FlightObservation.DoesNotExist:
            return None

    def bulk_write_flight_observations(self, observations: list[SingleAirtrafficObservation]) -> bool:
        try:
            flight_observation_objects = []
            for single_observation in observations:
                session_id = single_observation.session_id if single_observation.session_id else "00000000-0000-0000-0000-000000000000"
                flight_observation_objects.append(
                    FlightObservation(
                        session_id=session_id,
                        traffic_source=single_observation.traffic_source,
                        latitude_dd=single_observation.lat_dd,
                        longitude_dd=single_observation.lon_dd,
                        altitude_mm=single_observation.altitude_mm,
                        source_type=single_observation.source_type,
                        icao_address=single_observation.icao_address,
                        metadata=json.dumps(single_observation.metadata),
                    )
                )
            FlightObservation.objects.bulk_create(flight_observation_objects)
            return True
        except IntegrityError:
            return False

    def write_flight_observation(self, single_observation: SingleAirtrafficObservation) -> bool:
        session_id = single_observation.session_id if single_observation.session_id else "00000000-0000-0000-0000-000000000000"
        sensor_timestamp = self._normalize_timestamp(single_observation.timestamp)
        try:
            flight_observation = FlightObservation(
                session_id=session_id,
                traffic_source=single_observation.traffic_source,
                latitude_dd=single_observation.lat_dd,
                longitude_dd=single_observation.lon_dd,
                altitude_mm=single_observation.altitude_mm,
                source_type=single_observation.source_type,
                icao_address=single_observation.icao_address,
                metadata=json.dumps(single_observation.metadata),
                sensor_timestamp=sensor_timestamp,
            )
            flight_observation.save()
            return True
        except IntegrityError:
            return False

    def delete_all_flight_observations(self) -> bool:
        try:
            FlightObservation.objects.all().delete()
            return True
        except Exception as e:
            logger.error(f"Error deleting all flight observations: {e}")
            return False
