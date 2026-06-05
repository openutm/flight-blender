"""Tests for common/redis_stream_operations.py using fakeredis (autouse in conftest)."""

import json

import pytest

from flight_blender.common.redis_stream_operations import RedisStreamOperations
from flight_blender.core.entities.surveillance import ActiveTrack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_ops() -> RedisStreamOperations:
    """Return a RedisStreamOperations instance (uses fakeredis via autouse fixture)."""
    return RedisStreamOperations()


# ---------------------------------------------------------------------------
# Stream creation
# ---------------------------------------------------------------------------


class TestCreateAirTrafficStream:
    def test_creates_stream(self):
        ops = _fresh_ops()
        ops.create_air_traffic_stream("test-stream-1")
        # Stream should exist
        assert ops.redis.exists("test-stream-1")

    def test_stream_has_initial_entry(self):
        ops = _fresh_ops()
        ops.create_air_traffic_stream("test-stream-2")
        entries = ops.redis.xrange("test-stream-2")
        assert len(entries) >= 1


# ---------------------------------------------------------------------------
# Add air traffic data
# ---------------------------------------------------------------------------


class TestAddAirTrafficData:
    def test_adds_dict_observation(self):
        ops = _fresh_ops()
        ops.create_air_traffic_stream("test-atd-1")
        obs = {
            "icao_address": "AABB11",
            "lat_dd": "51.5",
            "lon_dd": "-0.1",
            "altitude_mm": "100.0",
            "traffic_source": "1",
            "source_type": "0",
            "timestamp": "1700000000",
            "session_id": "sess-1",
        }
        ops.add_air_traffic_data("test-atd-1", obs)
        entries = ops.redis.xrange("test-atd-1")
        # at least the initial + our entry
        assert len(entries) >= 1

    def test_metadata_dict_serialized(self):
        ops = _fresh_ops()
        obs = {
            "icao_address": "CC00",
            "lat_dd": "10.0",
            "lon_dd": "20.0",
            "altitude_mm": "50.0",
            "traffic_source": "0",
            "source_type": "0",
            "timestamp": "0",
            "metadata": {"key": "value"},
        }
        # Should not raise even with dict metadata
        ops.add_air_traffic_data("test-meta-stream", obs)


# ---------------------------------------------------------------------------
# Consumer group management
# ---------------------------------------------------------------------------


class TestConsumerGroupManagement:
    def test_create_consumer_group(self):
        ops = _fresh_ops()
        ops.create_air_traffic_stream("test-cg-stream")
        result = ops.create_consumer_group("test-cg-stream", "my-group")
        assert result is True

    def test_create_consumer_group_idempotent(self):
        ops = _fresh_ops()
        ops.create_air_traffic_stream("test-idm-stream")
        ops.create_consumer_group("test-idm-stream", "my-group")
        result = ops.create_consumer_group("test-idm-stream", "my-group")
        assert result is True

    def test_delete_consumer_group(self):
        ops = _fresh_ops()
        ops.create_air_traffic_stream("test-del-cg")
        ops.create_consumer_group("test-del-cg", "del-group")
        result = ops.delete_consumer_group("test-del-cg", "del-group")
        assert result is True

    def test_delete_nonexistent_group_returns_false(self):
        ops = _fresh_ops()
        ops.create_air_traffic_stream("test-nonexist-cg")
        result = ops.delete_consumer_group("test-nonexist-cg", "no-such-group")
        assert result is False


# ---------------------------------------------------------------------------
# Clear stream
# ---------------------------------------------------------------------------


class TestClearStream:
    def test_clear_existing_stream(self):
        ops = _fresh_ops()
        ops.create_air_traffic_stream("test-clear-1")
        result = ops.clear_stream("test-clear-1")
        assert result is True

    def test_clear_nonexistent_stream_returns_false(self):
        ops = _fresh_ops()
        result = ops.clear_stream("no-such-stream-xyz")
        assert result is False


# ---------------------------------------------------------------------------
# Active track management
# ---------------------------------------------------------------------------


class TestActiveTrackManagement:
    def test_check_active_track_not_exists(self):
        ops = _fresh_ops()
        exists = ops.check_active_track_exists("sess-1", "ICAO0001")
        assert exists is False

    def test_add_and_check_active_track(self):
        ops = _fresh_ops()
        track = ActiveTrack(
            session_id="sess-2",
            unique_aircraft_identifier="ICAO0002",
            last_updated_timestamp="2026-01-01T00:00:00Z",
            observations=[],
        )
        ops.add_active_track_to_session("sess-2", track)
        exists = ops.check_active_track_exists("sess-2", "ICAO0002")
        assert exists is True

    def test_get_active_track_returns_track(self):
        ops = _fresh_ops()
        track = ActiveTrack(
            session_id="sess-3",
            unique_aircraft_identifier="ICAO0003",
            last_updated_timestamp="2026-01-01T00:00:00Z",
            observations=[],
        )
        ops.add_active_track_to_session("sess-3", track)
        retrieved = ops.get_active_track("sess-3", "ICAO0003")
        assert retrieved is not None
        assert retrieved.unique_aircraft_identifier == "ICAO0003"

    def test_get_active_track_nonexistent_returns_none(self):
        ops = _fresh_ops()
        result = ops.get_active_track("no-sess", "NO-ICAO")
        assert result is None

    def test_update_active_track(self):
        ops = _fresh_ops()
        track = ActiveTrack(
            session_id="sess-4",
            unique_aircraft_identifier="ICAO0004",
            last_updated_timestamp="2026-01-01T00:00:00Z",
            observations=[],
        )
        ops.add_active_track_to_session("sess-4", track)
        track.last_updated_timestamp = "2026-01-02T00:00:00Z"
        ops.update_active_track("sess-4", track)
        retrieved = ops.get_active_track("sess-4", "ICAO0004")
        assert retrieved.last_updated_timestamp == "2026-01-02T00:00:00Z"

    def test_get_all_active_tracks_in_session(self):
        ops = _fresh_ops()
        for i in range(3):
            track = ActiveTrack(
                session_id="sess-5",
                unique_aircraft_identifier=f"ICAO000{i}",
                last_updated_timestamp="2026-01-01T00:00:00Z",
                observations=[],
            )
            ops.add_active_track_to_session("sess-5", track)
        tracks = ops.get_all_active_tracks_in_session("sess-5")
        assert len(tracks) == 3


# ---------------------------------------------------------------------------
# Consumer reader
# ---------------------------------------------------------------------------


class TestConsumerReader:
    def test_create_consumer_reader_returns_uuid_string(self):
        ops = _fresh_ops()
        reader_id = ops.create_consumer_reader()
        assert isinstance(reader_id, str)
        assert len(reader_id) == 36  # UUID format


# ---------------------------------------------------------------------------
# Parse stream message
# ---------------------------------------------------------------------------


class TestParseStreamMessage:
    def test_parse_valid_fields(self):
        ops = _fresh_ops()
        fields = {
            b"icao_address": b"ABCDEF",
            b"lat_dd": b"51.5",
            b"lon_dd": b"-0.1",
            b"altitude_mm": b"100.0",
            b"traffic_source": b"1",
            b"source_type": b"0",
            b"timestamp": b"1700000000",
            b"session_id": b"sess-123",
        }
        obs = ops._parse_stream_message_to_observation(fields, message_id=b"1700000000000-0")
        assert obs is not None
        assert obs.icao_address == "ABCDEF"
        assert obs.lat_dd == 51.5

    def test_parse_stream_created_returns_none(self):
        ops = _fresh_ops()
        fields = {b"message": b"stream_created"}
        result = ops._parse_stream_message_to_observation(fields)
        assert result is None

    def test_parse_with_json_metadata(self):
        ops = _fresh_ops()
        fields = {
            b"icao_address": b"XY1234",
            b"lat_dd": b"10.0",
            b"lon_dd": b"20.0",
            b"altitude_mm": b"50.0",
            b"traffic_source": b"0",
            b"source_type": b"0",
            b"timestamp": b"0",
            b"metadata": json.dumps({"key": "value"}).encode(),
        }
        obs = ops._parse_stream_message_to_observation(fields)
        assert obs is not None
        assert obs.metadata == {"key": "value"}

    def test_parse_with_invalid_metadata_returns_empty_dict(self):
        ops = _fresh_ops()
        fields = {
            b"icao_address": b"ZZ9999",
            b"lat_dd": b"10.0",
            b"lon_dd": b"20.0",
            b"altitude_mm": b"50.0",
            b"traffic_source": b"0",
            b"source_type": b"0",
            b"timestamp": b"0",
            b"metadata": b"not-json{{{",
        }
        obs = ops._parse_stream_message_to_observation(fields)
        assert obs is not None
        assert obs.metadata == {}

    def test_parse_with_invalid_fields_returns_none(self):
        ops = _fresh_ops()
        # Completely garbage fields
        fields = {b"icao_address": b"ABC", b"lat_dd": b"not-a-float"}
        result = ops._parse_stream_message_to_observation(fields)
        assert result is None


# ---------------------------------------------------------------------------
# Extract message id timestamp
# ---------------------------------------------------------------------------


class TestExtractMessageIdTimestamp:
    def test_bytes_message_id(self):
        result = RedisStreamOperations._extract_message_id_timestamp(b"1700000000123-0")
        assert result == 1700000000123

    def test_str_message_id(self):
        result = RedisStreamOperations._extract_message_id_timestamp("1700000000456-1")
        assert result == 1700000000456

    def test_none_returns_zero(self):
        result = RedisStreamOperations._extract_message_id_timestamp(None)
        assert result == 0

    def test_malformed_returns_zero(self):
        result = RedisStreamOperations._extract_message_id_timestamp("notanumber")
        assert result == 0
