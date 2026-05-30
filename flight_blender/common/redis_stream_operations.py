"""
Redis stream operations for air traffic data (migrated from common/redis_stream_operations.py).
"""

from flight_blender.common.redis_client import get_redis

FLIGHT_OBSERVATION_KEY = "flight_blender_air_traffic"
MAX_STREAM_LEN = 500


def add_air_traffic_data(observation: dict) -> str | None:
    """Append one observation dict to the Redis stream. Returns the entry ID."""
    r = get_redis()
    return r.xadd(FLIGHT_OBSERVATION_KEY, observation, maxlen=MAX_STREAM_LEN, approximate=True)


def read_all_observations(session_id: str | None = None, count: int = 500) -> list[dict]:
    """Read up to *count* entries from the stream, optionally filtered by session_id."""
    r = get_redis()
    entries = r.xrevrange(FLIGHT_OBSERVATION_KEY, count=count)
    result = []
    for _entry_id, fields in entries:
        if session_id and fields.get("session_id") != session_id:
            continue
        result.append(fields)
    return result


def read_latest_observation(session_id: str | None = None) -> dict | None:
    """Return the most-recent observation, optionally filtered by session_id."""
    observations = read_all_observations(session_id=session_id, count=500)
    return observations[0] if observations else None
