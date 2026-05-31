"""
Unit tests for Celery task logic using mocked SQLAlchemy and Redis.

These tests verify the helper functions and task logic without requiring
a real Celery broker, database, or Redis instance.
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# flight_feed task helpers
# ---------------------------------------------------------------------------
class TestFlightFeedHelpers:
    def test_parse_viewport_valid(self):
        from flight_blender.tasks.flight_feed import _parse_viewport

        result = _parse_viewport("10.0,20.0,30.0,40.0")
        assert result == {"lamin": 10.0, "lomin": 20.0, "lamax": 30.0, "lomax": 40.0}

    def test_parse_viewport_invalid_returns_empty(self):
        from flight_blender.tasks.flight_feed import _parse_viewport

        assert _parse_viewport("not,valid") == {}
        assert _parse_viewport("only,two") == {}

    def test_state_to_observation_valid(self):
        from flight_blender.tasks.flight_feed import _state_to_observation

        state = ["abc123", "CALLSIGN", None, None, None, 10.0, 20.0, 100.0]
        result = _state_to_observation(state, "sess1")
        assert result is not None
        assert result["lon_dd"] == 10.0
        assert result["lat_dd"] == 20.0
        assert result["altitude_mm"] == 100_000.0
        assert result["session_id"] == "sess1"

    def test_state_to_observation_none_coords_returns_none(self):
        from flight_blender.tasks.flight_feed import _state_to_observation

        state = ["abc123", "CALLSIGN", None, None, None, None, None, 100.0]
        assert _state_to_observation(state, None) is None

    def test_state_to_observation_short_state_returns_none(self):
        from flight_blender.tasks.flight_feed import _state_to_observation

        assert _state_to_observation([], None) is None
        assert _state_to_observation([1, 2], None) is None

    def test_write_incoming_air_traffic_data_success(self):
        mock_session = MagicMock()
        mock_engine = MagicMock()
        mock_engine.__enter__ = MagicMock(return_value=mock_engine)
        mock_engine.__exit__ = MagicMock(return_value=False)
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("flight_blender.tasks.flight_feed.add_air_traffic_data") as mock_stream,
            patch("sqlalchemy.create_engine", return_value=mock_engine),
            patch("sqlalchemy.orm.Session", return_value=mock_session),
        ):
            from flight_blender.tasks.flight_feed import write_incoming_air_traffic_data

            write_incoming_air_traffic_data(
                {"lat_dd": 10.0, "lon_dd": 20.0, "altitude_mm": 100, "icao_address": "ABC123"},
            )
            mock_stream.assert_called_once()

    def test_write_incoming_air_traffic_data_dict_metadata(self):
        """Metadata dict should be JSON-serialized."""
        mock_session = MagicMock()
        mock_engine = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        captured_obs = {}

        def fake_add(obj):
            captured_obs["metadata_"] = obj.metadata_

        mock_session.add = fake_add

        with (
            patch("flight_blender.tasks.flight_feed.add_air_traffic_data"),
            patch("sqlalchemy.create_engine", return_value=mock_engine),
            patch("sqlalchemy.orm.Session", return_value=mock_session),
        ):
            from flight_blender.tasks.flight_feed import write_incoming_air_traffic_data

            write_incoming_air_traffic_data({"lat_dd": 1.0, "lon_dd": 2.0, "metadata": {"key": "val"}})
            parsed = json.loads(captured_obs["metadata_"])
            assert parsed["key"] == "val"

    def test_start_opensky_stream_handles_api_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("requests.get", return_value=mock_resp):
            from flight_blender.tasks.flight_feed import start_opensky_network_stream

            # Should return early without raising
            start_opensky_network_stream("10,20,30,40", "session1")

    def test_start_opensky_stream_processes_states(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "states": [["abc", "CALLSIGN", None, None, None, 10.0, 20.0, 100.0]],
        }
        with (
            patch("requests.get", return_value=mock_resp),
            patch("flight_blender.tasks.flight_feed.write_incoming_air_traffic_data") as mock_task,
        ):
            mock_task.delay = MagicMock()
            from flight_blender.tasks.flight_feed import start_opensky_network_stream

            start_opensky_network_stream("10,20,30,40", "session1")
            mock_task.delay.assert_called_once()

    def test_bulk_write_incoming_air_traffic_data_success(self):
        mock_session = MagicMock()
        mock_engine = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("flight_blender.tasks.flight_feed.add_air_traffic_data") as mock_stream,
            patch("sqlalchemy.create_engine", return_value=mock_engine),
            patch("sqlalchemy.orm.Session", return_value=mock_session),
        ):
            from flight_blender.tasks.flight_feed import bulk_write_incoming_air_traffic_data

            bulk_write_incoming_air_traffic_data(
                [
                    {"lat_dd": 10.0, "lon_dd": 20.0, "altitude_mm": 100},
                    {"lat_dd": 11.0, "lon_dd": 21.0, "altitude_mm": 200},
                ]
            )
            assert mock_stream.call_count == 2


# ---------------------------------------------------------------------------
# geo_fence task helpers
# ---------------------------------------------------------------------------
class TestGeoFenceTasks:
    def test_download_geozone_source_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"features": [{"name": "Zone1", "upper_limit_m": 100, "lower_limit_m": 0}]}
        with (
            patch("requests.get", return_value=mock_resp),
            patch("flight_blender.tasks.geo_fence.write_geo_zone") as mock_write,
        ):
            mock_write.delay = MagicMock()
            from flight_blender.tasks.geo_fence import download_geozone_source

            download_geozone_source("https://example.com/zones")
            mock_write.delay.assert_called_once()

    def test_write_geo_zone_no_features_returns_early(self):
        mock_engine = MagicMock()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("sqlalchemy.create_engine", return_value=mock_engine),
            patch("sqlalchemy.orm.Session", return_value=mock_session),
        ):
            from flight_blender.tasks.geo_fence import write_geo_zone

            write_geo_zone({"features": []})
            # No session operations should happen
            mock_session.add.assert_not_called()

    def test_write_geo_zone_creates_geofence_records(self):
        mock_engine = MagicMock()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("sqlalchemy.create_engine", return_value=mock_engine),
            patch("sqlalchemy.orm.Session", return_value=mock_session),
        ):
            from flight_blender.tasks.geo_fence import write_geo_zone

            write_geo_zone(
                {
                    "features": [
                        {"name": "Zone A", "upper_limit_m": 120, "lower_limit_m": 0},
                        {"name": "Zone B", "upper_limit_m": 80, "lower_limit_m": 10},
                    ]
                }
            )
            assert mock_session.add.call_count == 2
            mock_session.commit.assert_called_once()

    def test_write_geo_zone_supports_geozones_key(self):
        """Accept both 'features' and 'GeoZones' keys."""
        mock_engine = MagicMock()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("sqlalchemy.create_engine", return_value=mock_engine),
            patch("sqlalchemy.orm.Session", return_value=mock_session),
        ):
            from flight_blender.tasks.geo_fence import write_geo_zone

            write_geo_zone({"GeoZones": [{"identifier": "Z1", "upper_limit_m": 50, "lower_limit_m": 0}]})
            mock_session.add.assert_called_once()


# ---------------------------------------------------------------------------
# surveillance task helpers (helper function coverage)
# ---------------------------------------------------------------------------
class TestSurveillanceTasks:
    def test_get_sync_engine_builds_url(self):
        """_get_sync_engine should convert async URL to sync."""
        import os

        with patch.dict(os.environ, {"DATABASE_URL": "sqlite+aiosqlite://:memory:"}):
            with patch("sqlalchemy.create_engine") as mock_create:
                mock_create.return_value = MagicMock()
                from flight_blender.tasks.surveillance import _get_sync_engine

                _get_sync_engine()
                call_url = mock_create.call_args[0][0]
                assert "+aiosqlite" not in call_url

    def test_send_heartbeat_session_not_found(self):
        """If the surveillance session doesn't exist, task should return without error."""
        mock_session_obj = MagicMock()
        mock_session_obj.__enter__ = MagicMock(return_value=mock_session_obj)
        mock_session_obj.__exit__ = MagicMock(return_value=False)
        mock_session_obj.get.return_value = None  # session not found

        with (
            patch("flight_blender.tasks.surveillance._get_sync_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_session_obj),
        ):
            from flight_blender.tasks.surveillance import send_heartbeat_to_consumer

            # Should complete without raising
            send_heartbeat_to_consumer("00000000-0000-0000-0000-000000000001")

    def test_cleanup_old_heartbeat_events_executes(self):
        """cleanup_old_heartbeat_events should execute delete and commit."""
        mock_engine = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.__enter__ = MagicMock(return_value=mock_session_obj)
        mock_session_obj.__exit__ = MagicMock(return_value=False)
        mock_result = MagicMock()
        mock_result.rowcount = 5
        mock_session_obj.execute.return_value = mock_result

        with (
            patch("sqlalchemy.create_engine", return_value=mock_engine),
            patch("sqlalchemy.orm.Session", return_value=mock_session_obj),
            patch("sqlalchemy.delete") as mock_delete,
        ):
            mock_delete.return_value.where.return_value = MagicMock()
            from flight_blender.tasks.surveillance import cleanup_old_heartbeat_events

            cleanup_old_heartbeat_events()
            mock_session_obj.commit.assert_called_once()


# ---------------------------------------------------------------------------
# conformance task helpers
# ---------------------------------------------------------------------------
class TestConformanceTasks:
    # NOTE: these tests were updated when the conformance tasks moved from the
    # `state == 2 => conforming` stub to the real C2-C11 engine. The tasks now
    # import `create_engine` / `Session` / `read_latest_observation` at module
    # level, so they are patched at `flight_blender.tasks.conformance.*`. The
    # "conforming" expectation now also requires fresh telemetry within the
    # liveness window, and the telemetry task writes its own ConformanceRecord
    # (it no longer just dispatches `check_flight_conformance`).
    _VALID_ID = "00000000-0000-0000-0000-000000000001"

    def test_check_flight_conformance_declaration_not_found(self):
        """When declaration not found, task should log and return without error."""
        mock_session_obj = MagicMock()
        mock_session_obj.__enter__ = MagicMock(return_value=mock_session_obj)
        mock_session_obj.__exit__ = MagicMock(return_value=False)
        mock_session_obj.get.return_value = None

        with (
            patch("flight_blender.tasks.conformance.create_engine"),
            patch("flight_blender.tasks.conformance.Session", return_value=mock_session_obj),
        ):
            from flight_blender.tasks.conformance import check_flight_conformance

            check_flight_conformance(self._VALID_ID)
            mock_session_obj.add.assert_not_called()

    def test_check_flight_conformance_creates_record(self):
        """An Activated declaration with fresh telemetry yields a conforming record."""
        mock_session_obj = MagicMock()
        mock_session_obj.__enter__ = MagicMock(return_value=mock_session_obj)
        mock_session_obj.__exit__ = MagicMock(return_value=False)

        fake_decl = MagicMock()
        fake_decl.id = uuid.uuid4()
        fake_decl.state = 2  # Activated
        fake_decl.latest_telemetry_datetime = datetime.now(timezone.utc)
        mock_session_obj.get.return_value = fake_decl

        with (
            patch("flight_blender.tasks.conformance.create_engine"),
            patch("flight_blender.tasks.conformance.Session", return_value=mock_session_obj),
        ):
            from flight_blender.tasks.conformance import check_flight_conformance

            check_flight_conformance(self._VALID_ID)
            mock_session_obj.add.assert_called_once()
            mock_session_obj.commit.assert_called_once()
            record = mock_session_obj.add.call_args[0][0]
            assert record.conformance_state == 1

    def test_check_operation_telemetry_conformance_no_telemetry(self):
        """When no telemetry exists, task should return without error."""
        with patch("flight_blender.tasks.conformance.read_latest_observation", return_value=None):
            from flight_blender.tasks.conformance import check_operation_telemetry_conformance

            check_operation_telemetry_conformance(self._VALID_ID)

    def test_check_operation_telemetry_conformance_with_telemetry(self):
        """When telemetry exists, the task writes a telemetry ConformanceRecord."""
        mock_session_obj = MagicMock()
        mock_session_obj.__enter__ = MagicMock(return_value=mock_session_obj)
        mock_session_obj.__exit__ = MagicMock(return_value=False)
        fake_decl = MagicMock()
        fake_decl.id = uuid.uuid4()
        fake_decl.state = 2
        fake_decl.aircraft_id = "ABC"
        fake_decl.start_datetime = datetime.now(timezone.utc) - timedelta(hours=1)
        fake_decl.end_datetime = datetime.now(timezone.utc) + timedelta(hours=1)
        fake_decl.operational_intent = "{}"
        mock_session_obj.get.return_value = fake_decl
        mock_session_obj.execute.return_value.scalars.return_value.all.return_value = []

        with (
            patch(
                "flight_blender.tasks.conformance.read_latest_observation",
                return_value={"lat_dd": "10.0", "lon_dd": "20.0", "altitude_mm": 0, "icao_address": "ABC"},
            ),
            patch("flight_blender.tasks.conformance.create_engine"),
            patch("flight_blender.tasks.conformance.Session", return_value=mock_session_obj),
        ):
            from flight_blender.tasks.conformance import check_operation_telemetry_conformance

            check_operation_telemetry_conformance(self._VALID_ID)
            mock_session_obj.add.assert_called_once()
            record = mock_session_obj.add.call_args[0][0]
            assert record.event_type == "telemetry_check"


# ---------------------------------------------------------------------------
# surveillance task: session found paths
# ---------------------------------------------------------------------------
class TestSurveillanceTasksWithSession:
    def _make_session_mock(self, surveillance_session=None):
        mock_session_obj = MagicMock()
        mock_session_obj.__enter__ = MagicMock(return_value=mock_session_obj)
        mock_session_obj.__exit__ = MagicMock(return_value=False)
        mock_session_obj.get.return_value = surveillance_session
        return mock_session_obj

    def test_send_heartbeat_session_found_creates_event(self):
        """When session exists, heartbeat event should be created."""
        from flight_blender.tasks.surveillance import send_heartbeat_to_consumer

        fake_sess = MagicMock()
        mock_db = self._make_session_mock(surveillance_session=fake_sess)

        with (
            patch("flight_blender.tasks.surveillance._get_sync_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
        ):
            # Only mock apply_async to prevent real Celery scheduling
            send_heartbeat_to_consumer.apply_async = MagicMock()
            send_heartbeat_to_consumer("00000000-0000-0000-0000-000000000001")
            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()

    def test_send_track_session_not_found(self):
        """When track session not found, should return without error."""
        from flight_blender.tasks.surveillance import send_and_generate_track_to_consumer

        mock_db = self._make_session_mock(surveillance_session=None)

        with (
            patch("flight_blender.tasks.surveillance._get_sync_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
        ):
            send_and_generate_track_to_consumer("00000000-0000-0000-0000-000000000001")
            mock_db.add.assert_not_called()

    def test_send_track_session_found_with_observations(self):
        """When session exists and observations present, creates TrackEvent."""
        from flight_blender.tasks.surveillance import send_and_generate_track_to_consumer

        fake_sess = MagicMock()
        mock_db = self._make_session_mock(surveillance_session=fake_sess)

        with (
            patch("flight_blender.tasks.surveillance._get_sync_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
            patch(
                "flight_blender.common.redis_stream_operations.read_all_observations",
                return_value=[{"lat_dd": "10.0"}],
            ),
        ):
            send_and_generate_track_to_consumer.apply_async = MagicMock()
            send_and_generate_track_to_consumer("00000000-0000-0000-0000-000000000001")
            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()

    def test_send_track_session_found_no_observations(self):
        """When session exists but no observations, still creates TrackEvent with had_tracks=False."""
        from flight_blender.tasks.surveillance import send_and_generate_track_to_consumer

        fake_sess = MagicMock()
        mock_db = self._make_session_mock(surveillance_session=fake_sess)

        with (
            patch("flight_blender.tasks.surveillance._get_sync_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
            patch(
                "flight_blender.common.redis_stream_operations.read_all_observations",
                return_value=[],
            ),
        ):
            send_and_generate_track_to_consumer.apply_async = MagicMock()
            send_and_generate_track_to_consumer("00000000-0000-0000-0000-000000000001")
            # Still creates event with had_tracks=False
            mock_db.add.assert_called_once()
            event_obj = mock_db.add.call_args[0][0]
            assert event_obj.had_active_tracks is False


# ---------------------------------------------------------------------------
# flight_declaration task tests
# ---------------------------------------------------------------------------
class TestFlightDeclarationTasks:
    def _make_session_mock(self, declaration=None):
        mock_session_obj = MagicMock()
        mock_session_obj.__enter__ = MagicMock(return_value=mock_session_obj)
        mock_session_obj.__exit__ = MagicMock(return_value=False)
        mock_session_obj.get.return_value = declaration
        return mock_session_obj

    def test_submit_declaration_not_found(self):
        """When declaration not found, should return without raising."""
        mock_db = self._make_session_mock(declaration=None)

        with (
            patch("sqlalchemy.create_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
        ):
            from flight_blender.tasks.flight_declaration import submit_flight_declaration_to_dss_async

            submit_flight_declaration_to_dss_async("00000000-0000-0000-0000-000000000001")
            mock_db.add.assert_not_called()

    def test_submit_declaration_end_time_in_past(self):
        """When declaration end time is in the past, should log and return."""
        fake_decl = MagicMock()
        fake_decl.state = 1
        fake_decl.end_datetime = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        mock_db = self._make_session_mock(declaration=fake_decl)

        with (
            patch("sqlalchemy.create_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
        ):
            from flight_blender.tasks.flight_declaration import submit_flight_declaration_to_dss_async

            submit_flight_declaration_to_dss_async("00000000-0000-0000-0000-000000000001")
            # Tracking record should be added for time validation failure
            mock_db.add.assert_called()

    def test_submit_declaration_dss_success(self):
        """When DSS returns 201, declaration state should be Accepted."""
        from flight_blender.tasks.flight_declaration import submit_flight_declaration_to_dss_async

        future_time = datetime.now(tz=timezone.utc) + timedelta(hours=2)
        fake_decl = MagicMock()
        fake_decl.state = 0
        fake_decl.id = "decl-id"
        fake_decl.end_datetime = future_time
        mock_db = self._make_session_mock(declaration=fake_decl)

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "operational_intent_reference": {
                "ovn": "test-ovn",
                "manager": "test-manager",
                "uss_base_url": "http://test",
                "version": 1,
                "state": "Accepted",
                "subscription_id": "sub-1",
            }
        }
        mock_creds_instance = MagicMock()
        mock_creds_instance.get_cached_credentials.return_value = {"access_token": "token123"}

        with (
            patch("sqlalchemy.create_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
            patch("flight_blender.auth.dss_auth_helper.AuthorityCredentialsGetter", return_value=mock_creds_instance),
            patch("requests.put", return_value=mock_resp),
        ):
            submit_flight_declaration_to_dss_async("00000000-0000-0000-0000-000000000001")
            # State should be set to 1 (Accepted)
            assert fake_decl.state == 1
            mock_db.commit.assert_called_once()

    def test_submit_declaration_sends_real_extents_from_volumes(self):
        """The DSS op-intent PUT carries the operation's stored volumes as ``extents``
        (was hard-coded ``[]``), built via the utm_adapter reference-payload builder."""
        from flight_blender.tasks.flight_declaration import submit_flight_declaration_to_dss_async

        volumes = [
            {
                "volume": {
                    "outline_polygon": {"vertices": [{"lat": 1.0, "lng": 2.0}, {"lat": 1.0, "lng": 3.0}, {"lat": 2.0, "lng": 3.0}]},
                    "altitude_lower": {"value": 0.0},
                    "altitude_upper": {"value": 120.0},
                },
                "time_start": {"value": "2030-01-01T00:00:00Z"},
                "time_end": {"value": "2030-01-01T01:00:00Z"},
            }
        ]
        fake_decl = MagicMock()
        fake_decl.state = 0
        fake_decl.id = "decl-id"
        fake_decl.end_datetime = datetime.now(tz=timezone.utc) + timedelta(hours=2)
        fake_decl.operational_intent = json.dumps(volumes)
        mock_db = self._make_session_mock(declaration=fake_decl)

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"operational_intent_reference": {}}
        mock_creds_instance = MagicMock()
        mock_creds_instance.get_cached_credentials.return_value = {"access_token": "token123"}

        with (
            patch("sqlalchemy.create_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
            patch("flight_blender.auth.dss_auth_helper.AuthorityCredentialsGetter", return_value=mock_creds_instance),
            patch("requests.put", return_value=mock_resp) as mock_put,
        ):
            submit_flight_declaration_to_dss_async("00000000-0000-0000-0000-000000000001")

        mock_put.assert_called_once()
        body = mock_put.call_args.kwargs["json"]
        assert body["extents"] == volumes
        # No live DSS area query yet, so the airspace key stays empty (documented follow-up).
        assert body["key"] == []

    def test_submit_declaration_empty_op_intent_sends_empty_extents(self):
        """A declaration whose operational_intent is ``{}`` yields ``extents=[]`` (no crash)."""
        from flight_blender.tasks.flight_declaration import submit_flight_declaration_to_dss_async

        fake_decl = MagicMock()
        fake_decl.state = 0
        fake_decl.id = "decl-id"
        fake_decl.end_datetime = datetime.now(tz=timezone.utc) + timedelta(hours=2)
        fake_decl.operational_intent = "{}"
        mock_db = self._make_session_mock(declaration=fake_decl)

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"operational_intent_reference": {}}
        mock_creds_instance = MagicMock()
        mock_creds_instance.get_cached_credentials.return_value = {"access_token": "token123"}

        with (
            patch("sqlalchemy.create_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
            patch("flight_blender.auth.dss_auth_helper.AuthorityCredentialsGetter", return_value=mock_creds_instance),
            patch("requests.put", return_value=mock_resp) as mock_put,
        ):
            submit_flight_declaration_to_dss_async("00000000-0000-0000-0000-000000000001")

        body = mock_put.call_args.kwargs["json"]
        assert body["extents"] == []

    def test_submit_declaration_dss_failure(self):
        """When DSS returns non-201, declaration state should be Rejected."""
        from flight_blender.tasks.flight_declaration import submit_flight_declaration_to_dss_async

        future_time = datetime.now(tz=timezone.utc) + timedelta(hours=2)
        fake_decl = MagicMock()
        fake_decl.state = 0
        fake_decl.id = "decl-id"
        fake_decl.end_datetime = future_time
        mock_db = self._make_session_mock(declaration=fake_decl)

        mock_resp = MagicMock()
        mock_resp.status_code = 409
        mock_resp.text = "Conflict"
        mock_creds_instance = MagicMock()
        mock_creds_instance.get_cached_credentials.return_value = {"access_token": "token123"}

        with (
            patch("sqlalchemy.create_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
            patch("flight_blender.auth.dss_auth_helper.AuthorityCredentialsGetter", return_value=mock_creds_instance),
            patch("requests.put", return_value=mock_resp),
        ):
            submit_flight_declaration_to_dss_async("00000000-0000-0000-0000-000000000001")
            # State should be set to 8 (Rejected)
            assert fake_decl.state == 8

    def test_send_operational_update_no_amqp_url(self):
        """When no AMQP_URL configured, should return without error."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AMQP_URL", None)
            from flight_blender.tasks.flight_declaration import send_operational_update_message

            # Should return without error
            send_operational_update_message("decl-id", "Test message", "info")

    def test_send_operational_update_with_amqp_url(self):
        """When AMQP_URL is configured, should connect and publish."""
        mock_connection = MagicMock()
        mock_channel = MagicMock()
        mock_connection.channel.return_value = mock_channel

        with (
            patch.dict(os.environ, {"AMQP_URL": "amqp://localhost"}),
            patch("pika.URLParameters", return_value=MagicMock()),
            patch("pika.BlockingConnection", return_value=mock_connection),
            patch("pika.BasicProperties", return_value=MagicMock()),
        ):
            from flight_blender.tasks.flight_declaration import send_operational_update_message

            send_operational_update_message("decl-id", "Test message", "info")
            mock_channel.basic_publish.assert_called_once()
            mock_connection.close.assert_called_once()


# ---------------------------------------------------------------------------
# RID task tests
# ---------------------------------------------------------------------------
class TestRidTasks:
    def _make_session_mock(self, db_obj=None):
        mock_session_obj = MagicMock()
        mock_session_obj.__enter__ = MagicMock(return_value=mock_session_obj)
        mock_session_obj.__exit__ = MagicMock(return_value=False)
        mock_session_obj.get.return_value = db_obj
        return mock_session_obj

    def test_submit_dss_subscription_success(self):
        """When DSS returns 200 and subscription exists, updates subscription_id."""
        from flight_blender.tasks.rid import submit_dss_subscription

        fake_sub = MagicMock()
        mock_db = self._make_session_mock(db_obj=fake_sub)

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_creds_instance = MagicMock()
        mock_creds_instance.get_cached_credentials.return_value = {"access_token": "tok"}

        with (
            patch("sqlalchemy.create_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
            patch("flight_blender.auth.dss_auth_helper.AuthorityCredentialsGetter", return_value=mock_creds_instance),
            patch("requests.put", return_value=mock_resp),
        ):
            submit_dss_subscription("00000000-0000-0000-0000-000000000001", "10,20,30,40", "2030-01-01T00:00:00Z")
            # subscription_id should be set
            assert fake_sub.subscription_id is not None
            mock_db.commit.assert_called_once()

    def test_submit_dss_subscription_dss_error(self):
        """When DSS returns non-200, should log error without commit."""
        from flight_blender.tasks.rid import submit_dss_subscription

        fake_sub = MagicMock()
        mock_db = self._make_session_mock(db_obj=fake_sub)

        mock_resp = MagicMock()
        mock_resp.status_code = 409
        mock_resp.text = "Conflict"

        mock_creds_instance = MagicMock()
        mock_creds_instance.get_cached_credentials.return_value = {"access_token": "tok"}

        with (
            patch("sqlalchemy.create_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
            patch("flight_blender.auth.dss_auth_helper.AuthorityCredentialsGetter", return_value=mock_creds_instance),
            patch("requests.put", return_value=mock_resp),
        ):
            submit_dss_subscription("00000000-0000-0000-0000-000000000001", "10,20,30,40", "2030-01-01T00:00:00Z")
            mock_db.commit.assert_not_called()

    def test_write_operator_rid_notification_success(self):
        """Should create notification record."""
        from flight_blender.tasks.rid import write_operator_rid_notification

        mock_db = self._make_session_mock()

        with (
            patch("sqlalchemy.create_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
        ):
            write_operator_rid_notification("session-1", "Test message", None)
            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()

    def test_write_operator_rid_notification_with_declaration(self):
        """Should handle flight_declaration_id as UUID."""
        from flight_blender.tasks.rid import write_operator_rid_notification

        mock_db = self._make_session_mock()

        with (
            patch("sqlalchemy.create_engine"),
            patch("sqlalchemy.orm.Session", return_value=mock_db),
        ):
            write_operator_rid_notification("session-1", "Test message", "00000000-0000-0000-0000-000000000001")
            mock_db.add.assert_called_once()

    def test_stream_rid_telemetry_data_success(self):
        """Should add telemetry data to Redis stream."""
        from flight_blender.tasks.rid import stream_rid_telemetry_data

        with patch("flight_blender.common.redis_stream_operations.add_air_traffic_data") as mock_add:
            stream_rid_telemetry_data({"lat": 10.0, "lon": 20.0})
            mock_add.assert_called_once()
            call_data = mock_add.call_args[0][0]
            assert call_data["type"] == "rid_telemetry"
            assert call_data["lat"] == 10.0
