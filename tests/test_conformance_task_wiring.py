"""
Tests that the conformance Celery tasks are wired to the real conformance
engine (rather than the old ``state == 2`` stub).

These use mocked SQLAlchemy sessions / Redis (no real DB, broker or Redis),
mirroring the patching style in ``tests/test_tasks.py``.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

VALID_ID = str(uuid.uuid4())


def _decl(state=2, latest_offset_seconds=0, operational_intent="{}"):
    obj = MagicMock()
    obj.id = uuid.uuid4()
    obj.state = state
    obj.aircraft_id = "abc-123"
    obj.start_datetime = datetime.now(timezone.utc) - timedelta(hours=1)
    obj.end_datetime = datetime.now(timezone.utc) + timedelta(hours=1)
    obj.operational_intent = operational_intent
    obj.latest_telemetry_datetime = datetime.now(timezone.utc) - timedelta(seconds=latest_offset_seconds)
    return obj


def test_flight_conformance_conforming_record():
    """Activated op with fresh telemetry -> conforming record."""
    from flight_blender.tasks.conformance import check_flight_conformance

    with (
        patch("flight_blender.tasks.conformance.create_engine"),
        patch("flight_blender.tasks.conformance.Session") as mock_session_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = _decl(state=2, latest_offset_seconds=0)

        check_flight_conformance(VALID_ID)

        added = mock_session.add.call_args[0][0]
        assert added.conformance_state == 1
        assert added.resolved is True
        assert added.geofence_breach is False
        mock_session.commit.assert_called_once()


def test_flight_conformance_stale_telemetry_marks_non_conforming():
    """Activated op with stale telemetry should fail liveness (C9b) -> non-conforming."""
    from flight_blender.tasks.conformance import check_flight_conformance

    with (
        patch("flight_blender.tasks.conformance.create_engine"),
        patch("flight_blender.tasks.conformance.Session") as mock_session_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = _decl(state=2, latest_offset_seconds=600)

        check_flight_conformance(VALID_ID)

        added = mock_session.add.call_args[0][0]
        assert added.conformance_state == 0
        assert added.geofence_breach is False
        assert "C9b" in added.description


def test_flight_conformance_records_check_code_in_description():
    """A non-active state (Accepted) fails C10 and is named in the description."""
    from flight_blender.tasks.conformance import check_flight_conformance

    with (
        patch("flight_blender.tasks.conformance.create_engine"),
        patch("flight_blender.tasks.conformance.Session") as mock_session_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = _decl(state=1)  # Accepted -> C10

        check_flight_conformance(VALID_ID)

        added = mock_session.add.call_args[0][0]
        assert added.conformance_state == 0
        assert "C10" in added.description


def _operational_intent_square():
    return json.dumps(
        {
            "volumes": [
                {
                    "volume": {
                        "outline_polygon": {
                            "vertices": [
                                {"lng": 0.0, "lat": 0.0},
                                {"lng": 1.0, "lat": 0.0},
                                {"lng": 1.0, "lat": 1.0},
                                {"lng": 0.0, "lat": 1.0},
                            ]
                        },
                        "altitude_lower": {"value": 0.0, "reference": "W84", "units": "M"},
                        "altitude_upper": {"value": 120.0, "reference": "W84", "units": "M"},
                    },
                    "time_start": {"value": "2026-01-01T00:00:00Z", "format": "RFC3339"},
                    "time_end": {"value": "2026-12-31T00:00:00Z", "format": "RFC3339"},
                }
            ]
        }
    )


def test_telemetry_conformance_conforming_inside_volume():
    """Telemetry inside the volume, no geofence -> conforming telemetry record."""
    from flight_blender.tasks.conformance import check_operation_telemetry_conformance

    telemetry = {"icao_address": "abc-123", "lat_dd": "0.5", "lon_dd": "0.5", "altitude_mm": "50000"}

    with (
        patch("flight_blender.tasks.conformance.read_latest_observation", return_value=telemetry),
        patch("flight_blender.tasks.conformance.create_engine"),
        patch("flight_blender.tasks.conformance.Session") as mock_session_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = _decl(state=2, operational_intent=_operational_intent_square())
        mock_session.execute.return_value.scalars.return_value.all.return_value = []

        check_operation_telemetry_conformance(VALID_ID)

        added = mock_session.add.call_args[0][0]
        assert added.conformance_state == 1
        assert added.geofence_breach is False
        assert added.event_type == "telemetry_check"


def test_telemetry_conformance_geofence_breach_sets_flag():
    """When telemetry breaches a geofence the record records a geofence breach (C8)."""
    from flight_blender.tasks.conformance import check_operation_telemetry_conformance

    telemetry = {"icao_address": "abc-123", "lat_dd": "0.5", "lon_dd": "0.5", "altitude_mm": "50000"}
    geofence = MagicMock()
    geofence.raw_geo_fence = json.dumps(
        {
            "features": [
                {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
                    }
                }
            ]
        }
    )

    with (
        patch("flight_blender.tasks.conformance.read_latest_observation", return_value=telemetry),
        patch("flight_blender.tasks.conformance.create_engine"),
        patch("flight_blender.tasks.conformance.Session") as mock_session_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = _decl(state=2, operational_intent=_operational_intent_square())
        mock_session.execute.return_value.scalars.return_value.all.return_value = [geofence]

        check_operation_telemetry_conformance(VALID_ID)

        added = mock_session.add.call_args[0][0]
        assert added.geofence_breach is True
        assert added.conformance_state == 0
        assert "C8" in added.description


def test_telemetry_conformance_no_observation_is_noop():
    """No telemetry -> nothing written."""
    from flight_blender.tasks.conformance import check_operation_telemetry_conformance

    with (
        patch("flight_blender.tasks.conformance.read_latest_observation", return_value=None),
        patch("flight_blender.tasks.conformance.create_engine"),
        patch("flight_blender.tasks.conformance.Session") as mock_session_cls,
    ):
        check_operation_telemetry_conformance(VALID_ID)
        mock_session_cls.assert_not_called()
