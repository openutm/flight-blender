import asyncio
import json
import uuid
from dataclasses import asdict
from enum import Enum
from itertools import zip_longest
from typing import Optional

import arrow
import dacite
import dacite.exceptions
import shapely
from dacite import from_dict
from loguru import logger
from shapely.geometry import Point
from shapely.geometry import box as shapely_box

from flight_blender.config import settings
from flight_blender.domain_types.flight_feed import (
    FlightObservationSchema,
    FlightObservationsProcessingResponse,
    ObservationRequest,
    SingleAirtrafficObservation,
)
from flight_blender.domain_types.rid import (
    NestedDict,
    RIDAircraftState,
    RIDFlightDetails,
    SignedTelemetryRequest,
    SignedUnSignedTelemetryObservations,
    SubmittedTelemetryFlightDetails,
)
from flight_blender.domain_types.protocols_flight_feed import FlightFeedRepository, FlightFeedTaskDispatcher, SyncFlightFeedReader, TelemetryValidator
from flight_blender.domain_types.redis_protocols import SyncRedisClient


def _check_view_port(view_port_coords: list[float]) -> bool:
    if len(view_port_coords) != 4:
        return False

    lat_min, lat_max = sorted(view_port_coords[::2])
    lng_min, lng_max = sorted(view_port_coords[1::2])
    return -90 <= lat_min < 90 and -90 < lat_max <= 90 and -180 <= lng_min < 360 and -180 < lng_max <= 360


def _build_view_port_box(view_port_coords: list[float]):
    return shapely_box(
        view_port_coords[0],
        view_port_coords[1],
        view_port_coords[2],
        view_port_coords[3],
    )


class FlightFeedOperations:
    def __init__(
        self,
        repo: FlightFeedRepository,
        dispatcher: FlightFeedTaskDispatcher,
        telemetry_validator: TelemetryValidator,
        redis: SyncRedisClient,
    ):
        self.repo = repo
        self.dispatcher = dispatcher
        self.telemetry_validator = telemetry_validator
        self.redis = redis

    @staticmethod
    def _to_observations(session_id: uuid.UUID, body: ObservationRequest) -> list[SingleAirtrafficObservation]:
        session_id_str = str(session_id)
        return [
            SingleAirtrafficObservation(
                session_id=session_id_str,
                lat_dd=obs.lat_dd,
                lon_dd=obs.lon_dd,
                altitude_mm=obs.altitude_mm,
                traffic_source=obs.traffic_source,
                source_type=obs.source_type,
                icao_address=obs.icao_address,
                metadata=obs.metadata,
                timestamp=obs.timestamp,
            )
            for obs in body.observations
        ]

    async def set_air_traffic(self, session_id: uuid.UUID, body: ObservationRequest) -> tuple[dict, int]:
        all_parsed = [asdict(so) for so in self._to_observations(session_id, body)]
        asyncio.create_task(asyncio.to_thread(self.dispatcher.dispatch_observations, all_parsed))
        op = FlightObservationsProcessingResponse(message="OK", status=201)
        return asdict(op), 201

    async def bulk_set_air_traffic(self, session_id: uuid.UUID, body: ObservationRequest) -> tuple[dict, int]:
        all_parsed = [asdict(so) for so in self._to_observations(session_id, body)]
        asyncio.create_task(asyncio.to_thread(self.dispatcher.dispatch_observations, all_parsed))
        op = FlightObservationsProcessingResponse(message="OK", status=201)
        return asdict(op), 201

    async def get_air_traffic(self, session_id: uuid.UUID, view: Optional[str]) -> tuple[dict, int]:
        if not view:
            return {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}, 400
        try:
            view_port = list(map(float, view.split(",")))
        except ValueError:
            return {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}, 400

        if not _check_view_port(view_port_coords=view_port):
            return {"message": "A incorrect view port bbox was provided"}, 400

        view_port_box = _build_view_port_box(view_port_coords=view_port)
        key = f"last_reading_for_{session_id}"
        if self.redis.exists(key):
            last_reading_time = self.redis.get(key)
            after_datetime = arrow.get(last_reading_time)
        else:
            after_datetime = arrow.now().shift(seconds=-20)

        self.redis.set(key, arrow.now().isoformat())
        self.redis.expire(key, 300)
        observation_rows = await self.repo.get_recent_flight_observations(after_datetime=after_datetime)
        all_observations = []
        for row in observation_rows:
            obs = FlightObservationSchema(
                id=str(row.id),
                session_id=str(row.session_id) if row.session_id else "",
                latitude_dd=row.latitude_dd,
                longitude_dd=row.longitude_dd,
                altitude_mm=row.altitude_mm,
                traffic_source=row.traffic_source,
                source_type=row.source_type,
                icao_address=row.icao_address,
                created_at=row.created_at.isoformat(),
                updated_at=row.updated_at.isoformat(),
                metadata=json.loads(row.raw_metadata),
            )
            if shapely.contains(view_port_box, Point(obs.latitude_dd, obs.longitude_dd)):
                all_observations.append(obs)

        latest_observations: dict = {}
        for obs in all_observations:
            if obs.icao_address not in latest_observations:
                latest_observations[obs.icao_address] = obs
            elif obs.created_at > latest_observations[obs.icao_address].created_at:
                latest_observations[obs.icao_address] = obs

        all_traffic = []
        for icao_address, obs in latest_observations.items():
            so = SingleAirtrafficObservation(
                lat_dd=obs.latitude_dd,
                lon_dd=obs.longitude_dd,
                altitude_mm=obs.altitude_mm,
                traffic_source=obs.traffic_source,
                source_type=obs.source_type,
                icao_address=icao_address,
                metadata=obs.metadata,
                session_id=obs.session_id,
            )
            all_traffic.append(asdict(so))

        return {"observations": all_traffic}, 200

    async def start_opensky_feed(self, view: Optional[str]) -> tuple[dict, int]:
        if not view:
            return {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}, 400
        try:
            view_port = [float(i) for i in view.split(",")]
        except ValueError:
            return {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}, 400

        if not _check_view_port(view_port_coords=view_port):
            return {"message": "An incorrect view port bbox was provided"}, 400

        session_id = uuid.uuid4()

        self.dispatcher.start_opensky_network_stream(view_port=json.dumps(view_port), session_id=str(session_id))
        return {"message": "Openskies Network stream started"}, 200

    async def list_signed_telemetry_keys(self) -> list[dict]:
        keys = await self.repo.list_signed_telemetry_public_keys()
        return [_key_to_dict(k) for k in keys]

    async def create_signed_telemetry_key(self, key_id: str, url: str, is_active: bool = True) -> dict:
        key = await self.repo.create_signed_telemetry_public_key(key_id=key_id, url=url, is_active=is_active)
        return _key_to_dict(key)

    async def get_signed_telemetry_key(self, pk: uuid.UUID) -> Optional[dict]:
        key = await self.repo.get_signed_telemetry_public_key(pk)
        if key is None:
            return None
        return _key_to_dict(key)

    async def update_signed_telemetry_key(self, pk: uuid.UUID, **kwargs) -> Optional[dict]:
        key = await self.repo.update_signed_telemetry_public_key(pk, **kwargs)
        if key is None:
            return None
        return _key_to_dict(key)

    async def delete_signed_telemetry_key(self, pk: uuid.UUID) -> bool:
        return await self.repo.delete_signed_telemetry_public_key(pk)

    async def submit_telemetry(self, raw_data: dict) -> tuple[dict, int]:
        validator = self.telemetry_validator

        if not validator.validate_observation_key_exists(raw_request_data=raw_data):
            return {"message": "A flight observation object with current state and flight details is necessary"}, 400

        rid_observations = raw_data["observations"]
        unsigned_telemetry_observations = []
        allowed_states = [2, 3, 4]
        if not settings.USSP_NETWORK_ENABLED:
            allowed_states.append(1)

        for flight in rid_observations:
            if not validator.validate_flight_details_current_states_exist(flight=flight):
                return {"message": "A flights object with current states, flight details is necessary"}, 400

            try:
                all_states = validator.parse_validate_current_states(current_states=flight["current_states"])
                f_details = validator.parse_validate_rid_details(rid_flight_details=flight["flight_details"])
            except KeyError as ke:
                return {
                    "message": f"A states object with a fully valid current states is necessary, the parsing the following key encountered errors {ke}"
                }, 400
            except (dacite.exceptions.WrongTypeError, dacite.exceptions.MissingValueError) as ve:
                return {"message": f"The parsing of telemetry object raised the following errors {ve}"}, 400

            unsigned_telemetry_observations.append(SignedUnSignedTelemetryObservations(current_states=all_states, flight_details=f_details))

            operation_id = f_details.id
            now = arrow.now().datetime
            flight_declaration_active = await self.repo.check_flight_declaration_active(flight_declaration_id=operation_id, now=now)
            if not flight_declaration_active:
                return {
                    "message": f"The operation ID: {operation_id} in the flight details object provided does not match any current operation in Flight Blender"
                }, 400

            flight_operation = await self.repo.get_flight_declaration_by_id(flight_declaration_id=operation_id)
            if flight_operation.state in allowed_states:
                serialized = [asdict(obs, dict_factory=NestedDict) for obs in unsigned_telemetry_observations]
                self.dispatcher.stream_rid_telemetry_data(rid_telemetry_observations=json.dumps(serialized))
            else:
                return {
                    "message": f"The operation ID: {operation_id} is not one of Activated, Contingent or Non-conforming states in Flight Blender, telemetry submission will be ignored, please change the state first."
                }, 400

        return {"message": "Telemetry data successfully submitted"}, 201

    async def submit_signed_telemetry(self, raw_data: dict) -> tuple[dict, int]:
        validator = self.telemetry_validator

        if not validator.validate_observation_key_exists(raw_request_data=raw_data):
            return {"message": "A flight observation object with current state and flight details is necessary"}, 400

        rid_observations = raw_data["observations"]
        unsigned_telemetry_observations = []

        for flight in rid_observations:
            if not validator.validate_flight_details_current_states_exist(flight=flight):
                return {"message": "A flights object with current states, flight details is necessary"}, 400

            try:
                all_states = validator.parse_validate_current_states(current_states=flight["current_states"])
                f_details = validator.parse_validate_rid_details(rid_flight_details=flight["flight_details"]["rid_details"])
            except KeyError as ke:
                return {
                    "message": f"A states object with a fully valid current states is necessary, the parsing the following key encountered errors {ke}"
                }, 400

            unsigned_telemetry_observations.append(
                asdict(SignedUnSignedTelemetryObservations(current_states=all_states, flight_details=f_details), dict_factory=NestedDict)
            )

            operation_id = f_details.id
            now = arrow.now().datetime
            flight_declaration_active = await self.repo.check_flight_declaration_active(flight_declaration_id=operation_id, now=now)
            if not flight_declaration_active:
                return {
                    "message": f"The operation ID: {operation_id} in the flight details object provided does not match any current operation in Flight Blender"
                }, 400

            flight_operation = await self.repo.get_flight_declaration_by_id(flight_declaration_id=operation_id)
            if flight_operation.state in [2, 3, 4]:
                self.dispatcher.stream_rid_telemetry_data(rid_telemetry_observations=json.dumps(unsigned_telemetry_observations))
            else:
                return {"message": f"The operation ID: {operation_id} is not one of Activated, Contingent or Non-conforming states."}, 400

        return {"message": "Telemetry data successfully submitted"}, 201


def _key_to_dict(key) -> dict:
    return {
        "id": str(key.id),
        "key_id": key.key_id,
        "url": key.url,
        "is_active": key.is_active,
        "created_at": key.created_at.isoformat() if key.created_at else None,
        "updated_at": key.updated_at.isoformat() if key.updated_at else None,
    }


# ── RID telemetry helpers (from flight_feed/rid_telemetry_helper.py) ──────────


def generate_rid_telemetry_objects(
    signed_telemetry_request: SignedTelemetryRequest,
) -> list[SubmittedTelemetryFlightDetails]:
    all_rid_data = []
    for current_signed_telemetry_request in signed_telemetry_request:
        s = from_dict(
            data_class=SubmittedTelemetryFlightDetails,
            data=current_signed_telemetry_request,
            config=dacite.Config(cast=[Enum]),
        )
        all_rid_data.append(s)
    return all_rid_data


def generate_unsigned_rid_telemetry_objects(
    telemetry_request: list[SignedUnSignedTelemetryObservations],
) -> list[SubmittedTelemetryFlightDetails]:
    all_rid_data = []
    for current_unsigned_telemetry_request in telemetry_request:
        s = from_dict(
            data_class=SubmittedTelemetryFlightDetails,
            data=current_unsigned_telemetry_request,
            config=dacite.Config(cast=[Enum]),
        )
        all_rid_data.append(s)
    return all_rid_data


class FlightBlenderTelemetryValidator:
    def parse_validate_current_states(self, current_states) -> list[RIDAircraftState]:
        all_states = []
        for state in current_states:
            aircraft_state = from_dict(
                data_class=RIDAircraftState,
                data=state,
                config=dacite.Config(cast=[Enum]),
            )
            all_states.append(aircraft_state)
        return all_states

    def parse_validate_rid_details(self, rid_flight_details) -> RIDFlightDetails:
        return from_dict(
            data_class=RIDFlightDetails,
            data=rid_flight_details,
            config=dacite.Config(cast=[Enum]),
        )

    def validate_flight_details_current_states_exist(self, flight) -> bool:
        return "flight_details" in flight and "current_states" in flight

    def validate_observation_key_exists(self, raw_request_data) -> bool:
        return "observations" in raw_request_data


# ── Observation read helpers (from flight_feed/flight_stream_helper.py) ────────


def batcher(iterable, n):
    args = [iter(iterable)] * n
    return zip_longest(*args)


class ObservationReadOperations:
    def __init__(self, redis: SyncRedisClient, db_reader: SyncFlightFeedReader, view_port_box=None):
        self.view_port_box: shapely_box = view_port_box
        self.redis: SyncRedisClient = redis
        self.db_reader: SyncFlightFeedReader = db_reader

    def get_flight_observations(self, session_id: str) -> list[FlightObservationSchema]:
        key = f"last_reading_for_{session_id}"
        if self.redis.exists(key):
            last_reading_time = self.redis.get(key)
            after_datetime = arrow.get(last_reading_time)
        else:
            now = arrow.now()
            after_datetime = now.shift(seconds=-20)

        self.redis.set(key, arrow.now().isoformat())
        self.redis.expire(key, 300)
        pending_messages = []
        all_flight_observations = self.db_reader.get_flight_observations(after_datetime=after_datetime)
        logger.info("Retrieved all flight observations..")
        for message in all_flight_observations:
            observation = FlightObservationSchema(
                id=message["id"],
                session_id=message["session_id"],
                latitude_dd=message["latitude_dd"],
                longitude_dd=message["longitude_dd"],
                altitude_mm=message["altitude_mm"],
                traffic_source=message["traffic_source"],
                source_type=message["source_type"],
                icao_address=message["icao_address"],
                created_at=message["created_at"],
                updated_at=message["updated_at"],
                metadata=json.loads(message["metadata"]),
            )
            if self.view_port_box:
                if shapely.contains(self.view_port_box, Point(observation.latitude_dd, observation.longitude_dd)):
                    pending_messages.append(observation)
            else:
                pending_messages.append(observation)
        return pending_messages

    def get_closest_observation_for_now(self, now: arrow.arrow.Arrow):
        all_observations = []
        closest_observations = self.db_reader.get_closest_flight_observation_for_now(now=now)
        logger.info("Retrieved closest_observations..")
        for closest_observation in closest_observations:
            single_observation = FlightObservationSchema(
                id=str(closest_observation.id),
                session_id=str(closest_observation.session_id),
                latitude_dd=closest_observation.latitude_dd,
                longitude_dd=closest_observation.longitude_dd,
                altitude_mm=closest_observation.altitude_mm,
                traffic_source=closest_observation.traffic_source,
                source_type=closest_observation.source_type,
                icao_address=closest_observation.icao_address,
                created_at=closest_observation.created_at.isoformat(),
                updated_at=closest_observation.updated_at.isoformat(),
                metadata=json.loads(closest_observation.metadata),
            )
            if self.view_port_box:
                if shapely.contains(self.view_port_box, Point(single_observation.latitude_dd, single_observation.longitude_dd)):
                    all_observations.append(single_observation)
            else:
                all_observations.append(single_observation)
        return all_observations

    def get_all_flight_observations(self) -> list[FlightObservationSchema]:
        pending_messages = []
        all_flight_observations = self.db_reader.get_flight_observation_objects()
        for message in all_flight_observations:
            observation = FlightObservationSchema(
                id=message["id"],
                session_id=message["session_id"],
                latitude_dd=message["latitude_dd"],
                longitude_dd=message["longitude_dd"],
                altitude_mm=message["altitude_mm"],
                traffic_source=message["traffic_source"],
                source_type=message["source_type"],
                icao_address=message["icao_address"],
                created_at=message["created_at"],
                updated_at=message["updated_at"],
                metadata=json.loads(message["metadata"]),
            )
            if self.view_port_box:
                if shapely.contains(self.view_port_box, Point(observation.latitude_dd, observation.longitude_dd)):
                    pending_messages.append(observation)
            else:
                pending_messages.append(observation)
        return pending_messages

    def get_latest_flight_observation_by_flight_declaration_id(self, flight_declaration_id: str) -> FlightObservationSchema | None:
        latest_observation = self.db_reader.get_latest_flight_observation_by_session(session_id=flight_declaration_id)
        if latest_observation:
            return FlightObservationSchema(
                id=str(latest_observation.id),
                session_id=str(latest_observation.session_id),
                latitude_dd=latest_observation.latitude_dd,
                longitude_dd=latest_observation.longitude_dd,
                altitude_mm=latest_observation.altitude_mm,
                traffic_source=latest_observation.traffic_source,
                source_type=latest_observation.source_type,
                icao_address=latest_observation.icao_address,
                created_at=latest_observation.created_at.isoformat(),
                updated_at=latest_observation.updated_at.isoformat(),
                metadata=json.loads(latest_observation.metadata),
            )
        return None

    def get_temporal_flight_observations_by_session(self, session_id: str) -> list[FlightObservationSchema]:
        key = f"last_reading_for_{session_id}"
        if self.redis.exists(key):
            last_reading_time = self.redis.get(key)
            after_datetime = arrow.get(last_reading_time)
        else:
            now = arrow.now()
            after_datetime = now.shift(seconds=-20)

        self.redis.set(key, arrow.now().isoformat())
        self.redis.expire(key, 300)
        pending_messages = []
        all_flight_observations = self.db_reader.get_temporal_flight_observations_by_session(session_id=session_id, after_datetime=after_datetime)
        for message in all_flight_observations:
            observation = FlightObservationSchema(
                id=message["id"],
                session_id=message["session_id"],
                latitude_dd=message["latitude_dd"],
                longitude_dd=message["longitude_dd"],
                altitude_mm=message["altitude_mm"],
                traffic_source=message["traffic_source"],
                source_type=message["source_type"],
                icao_address=message["icao_address"],
                created_at=message["created_at"],
                updated_at=message["updated_at"],
                metadata=json.loads(message["metadata"]),
            )
            if self.view_port_box:
                if shapely.contains(self.view_port_box, Point(observation.latitude_dd, observation.longitude_dd)):
                    pending_messages.append(observation)
            else:
                pending_messages.append(observation)
        return pending_messages
