import asyncio
import json
import os
import uuid
from dataclasses import asdict
from typing import Optional

import arrow
import dacite.exceptions
import shapely
from shapely.geometry import Point

from flight_blender.api.schemas.flight_feed import ObservationRequest
from flight_blender.auth.common import get_redis
from flight_blender.flight_feed.data_definitions import FlightObservationSchema, FlightObservationsProcessingResponse, SingleAirtrafficObservation
from flight_blender.flight_feed.rid_telemetry_helper import FlightBlenderTelemetryValidator, NestedDict
from flight_blender.flight_feed.tasks import bulk_write_incoming_air_traffic_data, start_opensky_network_stream
from flight_blender.infrastructure.database.repositories.sa_flight_feed import SQLAlchemyFlightFeedRepository
from flight_blender.rid import view_port_ops
from flight_blender.rid.data_definitions import SignedUnSignedTelemetryObservations
from flight_blender.rid.tasks import stream_rid_telemetry_data


def _dispatch_observations(all_parsed: list[dict]) -> None:
    for i in range(0, len(all_parsed), 250):
        bulk_write_incoming_air_traffic_data.delay(json.dumps(all_parsed[i : i + 250]))


class FlightFeedOperations:
    def __init__(self, repo: SQLAlchemyFlightFeedRepository):
        self.repo = repo

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
        asyncio.create_task(asyncio.to_thread(_dispatch_observations, all_parsed))
        op = FlightObservationsProcessingResponse(message="OK", status=201)
        return asdict(op), 201

    async def bulk_set_air_traffic(self, session_id: uuid.UUID, body: ObservationRequest) -> tuple[dict, int]:
        all_parsed = [asdict(so) for so in self._to_observations(session_id, body)]
        asyncio.create_task(asyncio.to_thread(_dispatch_observations, all_parsed))
        op = FlightObservationsProcessingResponse(message="OK", status=201)
        return asdict(op), 201

    async def get_air_traffic(self, session_id: uuid.UUID, view: Optional[str]) -> tuple[dict, int]:
        if not view:
            return {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}, 400
        try:
            view_port = list(map(float, view.split(",")))
        except ValueError:
            return {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}, 400

        if not view_port_ops.check_view_port(view_port_coords=view_port):
            return {"message": "A incorrect view port bbox was provided"}, 400

        view_port_box = view_port_ops.build_view_port_box(view_port_coords=view_port)
        r = get_redis()
        key = f"last_reading_for_{session_id}"
        if r.exists(key):
            last_reading_time = r.get(key)
            after_datetime = arrow.get(last_reading_time)
        else:
            after_datetime = arrow.now().shift(seconds=-20)

        r.set(key, arrow.now().isoformat())
        r.expire(key, 300)
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

        if not view_port_ops.check_view_port(view_port_coords=view_port):
            return {"message": "An incorrect view port bbox was provided"}, 400

        session_id = uuid.uuid4()

        start_opensky_network_stream.delay(view_port=json.dumps(view_port), session_id=str(session_id))
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
        validator = FlightBlenderTelemetryValidator()

        if not validator.validate_observation_key_exists(raw_request_data=raw_data):
            return {"message": "A flight observation object with current state and flight details is necessary"}, 400

        rid_observations = raw_data["observations"]
        unsigned_telemetry_observations = []
        allowed_states = [2, 3, 4]
        if not int(os.environ.get("USSP_NETWORK_ENABLED", 0)):
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
                stream_rid_telemetry_data.delay(rid_telemetry_observations=json.dumps(serialized))
            else:
                return {
                    "message": f"The operation ID: {operation_id} is not one of Activated, Contingent or Non-conforming states in Flight Blender, telemetry submission will be ignored, please change the state first."
                }, 400

        return {"message": "Telemetry data successfully submitted"}, 201

    async def submit_signed_telemetry(self, raw_data: dict) -> tuple[dict, int]:
        validator = FlightBlenderTelemetryValidator()

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
                stream_rid_telemetry_data.delay(rid_telemetry_observations=json.dumps(unsigned_telemetry_observations))
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
