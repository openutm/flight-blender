"""
Celery tasks for flight feed / air traffic observation processing.
"""

import json

import requests
from loguru import logger

from flight_blender.common.redis_stream_operations import add_air_traffic_data
from flight_blender.tasks.celery_app import celery_app


def _parse_viewport(view_port: str) -> dict:
    """Parse a 'lat_min,lon_min,lat_max,lon_max' string into OpenSky API params."""
    try:
        coords = [float(c) for c in view_port.split(",")]
        if len(coords) == 4:
            return {"lamin": coords[0], "lomin": coords[1], "lamax": coords[2], "lomax": coords[3]}
    except ValueError:
        logger.warning("Invalid view_port format: %s", view_port)
    return {}


def _state_to_observation(state: list, session_id: str | None) -> dict | None:
    """Convert a single OpenSky state vector to an observation dict, or None if invalid."""
    if not state or len(state) < 8:
        return None
    lon, lat, altitude = state[5], state[6], state[7]
    if lat is None or lon is None:
        return None
    return {
        "lat_dd": lat,
        "lon_dd": lon,
        "altitude_mm": (altitude or 0) * 1000,
        "traffic_source": 0,
        "source_type": 0,
        "icao_address": state[0] or "",
        "metadata": json.dumps({"callsign": state[1]}),
        "session_id": session_id,
    }


@celery_app.task(name="write_incoming_air_traffic_data", bind=True, max_retries=3)
def write_incoming_air_traffic_data(self, observation: dict):
    """
    Persist a single air traffic observation to the DB and push it to the Redis stream.
    This task uses a synchronous SQLAlchemy session because Celery workers are sync.
    """
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from flight_blender.config import get_settings
        from flight_blender.models.flight_feed import FlightObservation

        settings = get_settings()
        # Convert async URL to sync
        sync_url = settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)

        with Session(engine) as session:
            obs = FlightObservation(
                latitude_dd=float(observation.get("lat_dd", observation.get("latitude_dd", 0))),
                longitude_dd=float(observation.get("lon_dd", observation.get("longitude_dd", 0))),
                altitude_mm=float(observation.get("altitude_mm", 0)),
                traffic_source=int(observation.get("traffic_source", 12)),
                source_type=int(observation.get("source_type", 0)),
                icao_address=str(observation.get("icao_address", "")),
                metadata_=json.dumps(observation.get("metadata", {}))
                if isinstance(observation.get("metadata"), dict)
                else str(observation.get("metadata", "")),
                session_id=observation.get("session_id"),
            )
            session.add(obs)
            session.commit()

        add_air_traffic_data(observation)
        logger.info("Observation written and streamed to Redis")
    except Exception as exc:
        logger.error("Error writing observation: %s", exc)
        raise self.retry(exc=exc, countdown=2)


@celery_app.task(name="bulk_write_incoming_air_traffic_data", bind=True, max_retries=3)
def bulk_write_incoming_air_traffic_data(self, observations: list[dict]):
    """
    Persist a batch of air traffic observations and push each to the Redis stream.
    """
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from flight_blender.config import get_settings
        from flight_blender.models.flight_feed import FlightObservation

        settings = get_settings()
        sync_url = settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)

        with Session(engine) as session:
            obs_objects = []
            for obs in observations:
                obs_objects.append(
                    FlightObservation(
                        latitude_dd=float(obs.get("lat_dd", obs.get("latitude_dd", 0))),
                        longitude_dd=float(obs.get("lon_dd", obs.get("longitude_dd", 0))),
                        altitude_mm=float(obs.get("altitude_mm", 0)),
                        traffic_source=int(obs.get("traffic_source", 12)),
                        source_type=int(obs.get("source_type", 0)),
                        icao_address=str(obs.get("icao_address", "")),
                        metadata_=json.dumps(obs.get("metadata", {})) if isinstance(obs.get("metadata"), dict) else str(obs.get("metadata", "")),
                        session_id=obs.get("session_id"),
                    )
                )
            session.add_all(obs_objects)
            session.commit()

        for obs in observations:
            add_air_traffic_data(obs)
        logger.info("Bulk observations written (%d)", len(observations))
    except Exception as exc:
        logger.error("Bulk write error: %s", exc)
        raise self.retry(exc=exc, countdown=2)


@celery_app.task(name="start_opensky_network_stream", bind=True, max_retries=2)
def start_opensky_network_stream(self, view_port: str | None = None, session_id: str | None = None):
    """
    Poll the OpenSky Network API and push results to the air traffic stream.
    """
    import os

    username = os.getenv("OPENSKY_USERNAME", "")
    password = os.getenv("OPENSKY_PASSWORD", "")  # nosec B105

    params = _parse_viewport(view_port) if view_port else {}
    auth = (username, password) if username else None

    try:
        resp = requests.get("https://opensky-network.org/api/states/all", params=params, auth=auth, timeout=30)
        if resp.status_code != 200:
            logger.error("OpenSky API error: %s", resp.status_code)
            return

        states = resp.json().get("states", [])
        logger.info("OpenSky returned %d states", len(states))

        for state in states:
            obs = _state_to_observation(state, session_id)
            if obs is not None:
                write_incoming_air_traffic_data.delay(obs)

    except Exception as exc:
        logger.error("OpenSky stream error: %s", exc)
        raise self.retry(exc=exc, countdown=30)
