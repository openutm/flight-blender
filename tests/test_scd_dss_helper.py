"""Unit tests for flight_blender.scd/dss_scd_helper.py.

Covers the pure-logic classes that do not require a live DSS:
  - FlightPlanningDataValidator
  - OperationalIntentValidator
  - PeerOperationalIntentValidator
  - VolumesValidator
  - VolumesConverter (polygon and circle volumes)
  - OperationalIntentReferenceHelper.parse_volume_to_volume4D
  - OperationalIntentReferenceHelper.parse_operational_intent_details
  - OperationalIntentReferenceHelper.parse_operational_intent_reference_from_dss
"""

import arrow
import httpx
import pytest
from fastapi import HTTPException

import flight_blender.clients.dss_scd_client as dss_helper
from flight_blender.clients.dss_scd_client import OperationalIntentReferenceHelper
from flight_blender.config import settings
from flight_blender.domain_types.scd import (
    Altitude,
    Circle,
    FlightPlanCurrentStatus,
    FlightPlanningInjectionData,
    LatLngPoint,
    NotifyPeerUSSPostPayload,
    OperationalIntentDetailsUSSResponse,
    OperationalIntentReferenceDSSResponse,
    OperationalIntentState,
    OperationalIntentTestInjection,
    OperationalIntentUSSDetails,
    OpIntUpdateCheckResultCodes,
    Radius,
    SubscriptionState,
    Time,
    Volume3D,
    Volume4D,
)
from flight_blender.domain_types.scd import Polygon as Plgn
from flight_blender.utils.scd_helpers import (
    FlightPlanningDataValidator,
    OperationalIntentValidator,
    PeerOperationalIntentValidator,
    VolumesConverter,
    VolumesValidator,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_volume4d(
    lat_lng_pairs: list[tuple[float, float]] | None = None,
    minutes_ahead: int = 5,
) -> Volume4D:
    """Build a minimal polygon-based Volume4D starting ``minutes_ahead`` from now."""
    now = arrow.now()
    pairs = lat_lng_pairs or [(52.500, 13.399), (52.501, 13.399), (52.501, 13.400), (52.500, 13.400)]
    vertices = [LatLngPoint(lat=lat, lng=lng) for lat, lng in pairs]
    volume3d = Volume3D(
        outline_polygon=Plgn(vertices=vertices),
        altitude_lower=Altitude(value=0, reference="W84", units="M"),
        altitude_upper=Altitude(value=100, reference="W84", units="M"),
    )
    return Volume4D(
        volume=volume3d,
        time_start=Time(format="RFC3339", value=now.shift(minutes=minutes_ahead).isoformat()),
        time_end=Time(format="RFC3339", value=now.shift(hours=1).isoformat()),
    )


def _make_circle_volume4d() -> Volume4D:
    """Build a minimal circle-based Volume4D."""
    now = arrow.now()
    center = LatLngPoint(lat=47.3769, lng=8.5417)
    circle = Circle(center=center, radius=Radius(value=300, units="M"))
    volume3d = Volume3D(
        outline_polygon=None,
        outline_circle=circle,
        altitude_lower=Altitude(value=0, reference="W84", units="M"),
        altitude_upper=Altitude(value=100, reference="W84", units="M"),
    )
    return Volume4D(
        volume=volume3d,
        time_start=Time(format="RFC3339", value=now.shift(minutes=5).isoformat()),
        time_end=Time(format="RFC3339", value=now.shift(hours=1).isoformat()),
    )


def _make_notification_payload() -> NotifyPeerUSSPostPayload:
    now = arrow.now()
    reference = OperationalIntentReferenceDSSResponse(
        id="test-id",
        manager="test-manager",
        uss_availability="Unknown",
        version=1,
        state="Accepted",
        ovn="test-ovn",
        time_start=Time(format="RFC3339", value=now.isoformat()),
        time_end=Time(format="RFC3339", value=now.shift(hours=1).isoformat()),
        uss_base_url="http://peer.example.com",
        subscription_id="subscription-id",
    )
    details = OperationalIntentUSSDetails(volumes=[_make_volume4d()], priority=0, off_nominal_volumes=[])
    return NotifyPeerUSSPostPayload(
        operational_intent_id="test-id",
        operational_intent=OperationalIntentDetailsUSSResponse(reference=reference, details=details),
        subscriptions=[SubscriptionState(subscription_id="subscription-id", notification_index=1)],
    )


# ---------------------------------------------------------------------------
# FlightPlanningDataValidator
# ---------------------------------------------------------------------------


class TestFlightPlanningDataValidator:
    def _make_data(self, uas_state="Nominal", usage_state="Planned", off_nominal=None):
        return FlightPlanningInjectionData(
            volumes=[],
            priority=0,
            off_nominal_volumes=off_nominal or [],
            uas_state=uas_state,
            usage_state=usage_state,
            state="Accepted",
        )

    def test_valid_nominal_planned(self):
        data = self._make_data(uas_state="Nominal", usage_state="Planned")
        v = FlightPlanningDataValidator(incoming_flight_planning_data=data)
        assert v.validate_flight_planning_test_data() is True

    def test_invalid_uas_state(self):
        data = self._make_data(uas_state="InvalidState")
        v = FlightPlanningDataValidator(incoming_flight_planning_data=data)
        assert v.validate_flight_planning_state() is False
        assert v.validate_flight_planning_test_data() is False

    def test_off_nominals_when_planned_are_invalid(self):
        """Providing off-nominal volumes while usage_state is Planned is invalid."""
        vol = _make_volume4d()
        data = self._make_data(uas_state="Nominal", usage_state="Planned", off_nominal=[vol])
        v = FlightPlanningDataValidator(incoming_flight_planning_data=data)
        assert v.validate_flight_planning_off_nominals() is False
        assert v.validate_flight_planning_test_data() is False

    def test_off_nominals_rejected_when_usage_state_is_in_use(self):
        """Off-nominal volumes are rejected when usage_state is InUse (even for Contingent UAS).

        The validator checks usage_state, not uas_state: any off-nominal volumes
        attached to a declaration with usage_state='Planned' or 'InUse' are invalid.
        """
        vol = _make_volume4d()
        data = self._make_data(uas_state="Contingent", usage_state="InUse", off_nominal=[vol])
        v = FlightPlanningDataValidator(incoming_flight_planning_data=data)
        assert v.validate_flight_planning_off_nominals() is False

    def test_off_nominal_state(self):
        data = self._make_data(uas_state="OffNominal", usage_state="InUse")
        v = FlightPlanningDataValidator(incoming_flight_planning_data=data)
        assert v.validate_flight_planning_state() is True

    def test_not_specified_state(self):
        data = self._make_data(uas_state="NotSpecified", usage_state="Planned")
        v = FlightPlanningDataValidator(incoming_flight_planning_data=data)
        assert v.validate_flight_planning_state() is True


# ---------------------------------------------------------------------------
# OperationalIntentValidator
# ---------------------------------------------------------------------------


class TestOperationalIntentValidator:
    def _make_opint(self, state="Accepted", off_nominal=None):
        return OperationalIntentTestInjection(
            volumes=[_make_volume4d()],
            priority=0,
            off_nominal_volumes=off_nominal or [],
            state=state,
        )

    def test_accepted_state_is_valid(self):
        opint = self._make_opint(state="Accepted")
        v = OperationalIntentValidator(operational_intent_data=opint)
        assert v.validate_operational_intent_state() is True
        assert v.validate_operational_intent_test_data() is True

    def test_activated_state_is_valid(self):
        opint = self._make_opint(state="Activated")
        v = OperationalIntentValidator(operational_intent_data=opint)
        assert v.validate_operational_intent_state() is True

    def test_nonconforming_state_is_valid(self):
        opint = self._make_opint(state="Nonconforming")
        v = OperationalIntentValidator(operational_intent_data=opint)
        assert v.validate_operational_intent_state() is True

    def test_invalid_state(self):
        opint = self._make_opint(state="Garbage")
        v = OperationalIntentValidator(operational_intent_data=opint)
        assert v.validate_operational_intent_state() is False
        assert v.validate_operational_intent_test_data() is False

    def test_off_nominals_with_accepted_state_invalid(self):
        """Accepted state with off-nominal volumes is invalid."""
        off_vol = _make_volume4d()
        opint = self._make_opint(state="Accepted", off_nominal=[off_vol])
        v = OperationalIntentValidator(operational_intent_data=opint)
        assert v.validate_operational_intent_state_off_nominals() is False

    def test_off_nominals_with_nonconforming_state_valid(self):
        """Nonconforming state with off-nominal volumes is valid."""
        off_vol = _make_volume4d()
        opint = self._make_opint(state="Nonconforming", off_nominal=[off_vol])
        v = OperationalIntentValidator(operational_intent_data=opint)
        assert v.validate_operational_intent_state_off_nominals() is True


# ---------------------------------------------------------------------------
# PeerOperationalIntentValidator
# ---------------------------------------------------------------------------


class TestPeerOperationalIntentValidator:
    def _make_ref(self, state="Accepted"):
        now = arrow.now()
        return OperationalIntentReferenceDSSResponse(
            id="test-id",
            manager="test",
            uss_availability="Unknown",
            version=1,
            state=state,
            ovn="test-ovn",
            time_start=Time(format="RFC3339", value=now.isoformat()),
            time_end=Time(format="RFC3339", value=now.shift(hours=1).isoformat()),
            uss_base_url="http://test",
            subscription_id="sub-1",
        )

    def _make_opint_response(self, state="Accepted", priority=0):
        ref = self._make_ref(state=state)
        details = OperationalIntentUSSDetails(
            volumes=[_make_volume4d()],
            priority=priority,
            off_nominal_volumes=[],
        )
        return OperationalIntentDetailsUSSResponse(reference=ref, details=details)

    def test_valid_opint(self):
        v = PeerOperationalIntentValidator()
        opint = self._make_opint_response(state="Accepted", priority=0)
        assert v.validate_individual_operational_intent(opint) is True

    def test_invalid_state(self):
        v = PeerOperationalIntentValidator()
        opint = self._make_opint_response(state="INVALID", priority=0)
        assert v.validate_individual_operational_intent(opint) is False

    def test_non_integer_priority(self):
        v = PeerOperationalIntentValidator()
        opint = self._make_opint_response(state="Accepted", priority="high")  # type: ignore[arg-type]
        assert v.validate_individual_operational_intent(opint) is False

    def test_validate_nearby_empty_list(self):
        v = PeerOperationalIntentValidator()
        assert v.validate_nearby_operational_intents([]) is True

    def test_validate_nearby_multiple_valid(self):
        v = PeerOperationalIntentValidator()
        opints = [self._make_opint_response(state="Accepted", priority=i) for i in range(3)]
        assert v.validate_nearby_operational_intents(opints) is True

    def test_validate_nearby_one_invalid(self):
        v = PeerOperationalIntentValidator()
        opints = [
            self._make_opint_response(state="Accepted", priority=0),
            self._make_opint_response(state="BOGUS", priority=0),
        ]
        assert v.validate_nearby_operational_intents(opints) is False


# ---------------------------------------------------------------------------
# VolumesValidator
# ---------------------------------------------------------------------------


class TestVolumesValidator:
    def test_valid_volume_future(self):
        vol = _make_volume4d(minutes_ahead=10)
        v = VolumesValidator()
        assert v.validate_volume_start_end_date(vol) is True

    def test_volume_too_far_future(self):
        """Volume starting more than 30 days from now is invalid."""
        now = arrow.now()
        vertices = [LatLngPoint(lat=52.5, lng=13.4), LatLngPoint(lat=52.51, lng=13.4), LatLngPoint(lat=52.51, lng=13.41)]
        volume3d = Volume3D(
            outline_polygon=Plgn(vertices=vertices),
            altitude_lower=Altitude(value=0, reference="W84", units="M"),
            altitude_upper=Altitude(value=100, reference="W84", units="M"),
        )
        vol = Volume4D(
            volume=volume3d,
            time_start=Time(format="RFC3339", value=now.shift(days=31).isoformat()),
            time_end=Time(format="RFC3339", value=now.shift(days=32).isoformat()),
        )
        v = VolumesValidator()
        assert v.validate_volume_start_end_date(vol) is False

    def test_validate_polygon_vertices_enough(self):
        vol = _make_volume4d()
        v = VolumesValidator()
        assert v.validate_polygon_vertices(vol) is True

    def test_validate_polygon_vertices_too_few(self):
        now = arrow.now()
        # Only 2 vertices — should fail
        vertices = [LatLngPoint(lat=52.5, lng=13.4), LatLngPoint(lat=52.51, lng=13.4)]
        volume3d = Volume3D(
            outline_polygon=Plgn(vertices=vertices),
            altitude_lower=Altitude(value=0, reference="W84", units="M"),
            altitude_upper=Altitude(value=100, reference="W84", units="M"),
        )
        vol = Volume4D(
            volume=volume3d,
            time_start=Time(format="RFC3339", value=now.shift(minutes=5).isoformat()),
            time_end=Time(format="RFC3339", value=now.shift(hours=1).isoformat()),
        )
        v = VolumesValidator()
        assert v.validate_polygon_vertices(vol) is False

    def test_validate_volumes_valid_list(self):
        vols = [_make_volume4d(minutes_ahead=5), _make_volume4d(minutes_ahead=10)]
        v = VolumesValidator()
        assert v.validate_volumes(vols) is True

    def test_pre_creation_checks_future_start(self):
        vol = _make_volume4d(minutes_ahead=5)
        v = VolumesValidator()
        assert v.pre_operational_intent_creation_checks([vol]) is True

    def test_pre_creation_checks_past_start(self):
        """Volume whose start time is in the past should fail pre-creation check."""
        now = arrow.now()
        vertices = [LatLngPoint(lat=52.5, lng=13.4), LatLngPoint(lat=52.51, lng=13.4), LatLngPoint(lat=52.51, lng=13.41)]
        volume3d = Volume3D(
            outline_polygon=Plgn(vertices=vertices),
            altitude_lower=Altitude(value=0, reference="W84", units="M"),
            altitude_upper=Altitude(value=100, reference="W84", units="M"),
        )
        vol = Volume4D(
            volume=volume3d,
            time_start=Time(format="RFC3339", value=now.shift(minutes=-30).isoformat()),
            time_end=Time(format="RFC3339", value=now.shift(hours=1).isoformat()),
        )
        v = VolumesValidator()
        assert v.pre_operational_intent_creation_checks([vol]) is False


# ---------------------------------------------------------------------------
# VolumesConverter
# ---------------------------------------------------------------------------


class TestVolumesConverter:
    def test_convert_polygon_volume(self):
        vol = _make_volume4d()
        converter = VolumesConverter()
        converter.convert_volumes_to_geojson(volumes=[vol])
        features = converter.geo_json["features"]
        assert len(features) >= 1
        assert features[0]["type"] == "Feature"

    def test_convert_circle_volume(self, monkeypatch):
        """Circle volumes require UTM conversion.  Use zone '32' (Central Europe) to avoid pyproj issues."""
        monkeypatch.setattr(settings, "UTM_ZONE", "32")
        vol = _make_circle_volume4d()
        converter = VolumesConverter()
        converter.convert_volumes_to_geojson(volumes=[vol])
        features = converter.geo_json["features"]
        assert len(features) >= 1

    def test_get_bounds_returns_four_values(self):
        vol = _make_volume4d()
        converter = VolumesConverter()
        converter.convert_volumes_to_geojson(volumes=[vol])
        bounds = converter.get_bounds()
        assert len(bounds) == 4

    def test_get_volume_bounds_returns_coordinates(self):
        vol = _make_volume4d()
        converter = VolumesConverter()
        converter.convert_volumes_to_geojson(volumes=[vol])
        vol_bounds = converter.get_volume_bounds()
        assert isinstance(vol_bounds, list)
        assert len(vol_bounds) > 0

    def test_time_start_end_captured(self):
        vol = _make_volume4d(minutes_ahead=5)
        converter = VolumesConverter()
        converter.convert_volumes_to_geojson(volumes=[vol])
        assert converter.time_start is not None
        assert converter.time_end is not None

    def test_altitude_captured(self):
        vol = _make_volume4d()
        converter = VolumesConverter()
        converter.convert_volumes_to_geojson(volumes=[vol])
        assert converter.upper_altitude == 100
        assert converter.lower_altitude == 0

    def test_get_earliest_and_latest_times(self):
        vol = _make_volume4d()
        converter = VolumesConverter()
        converter.convert_volumes_to_geojson(volumes=[vol])
        assert converter.get_earliest_time_from_volumes() is not None
        assert converter.get_latest_time_from_volumes() is not None

    def test_get_minimum_rotated_rectangle(self):
        vol = _make_volume4d()
        converter = VolumesConverter()
        converter.convert_volumes_to_geojson(volumes=[vol])
        rect = converter.get_minimum_rotated_rectangle()
        assert rect is not None


# ---------------------------------------------------------------------------
# OperationalIntentReferenceHelper – pure parsing methods
# ---------------------------------------------------------------------------


class TestOperationalIntentReferenceHelperParsing:
    def _volume_dict(self, minutes_ahead=5):
        return {
            "volume": {
                "outline_polygon": {
                    "vertices": [
                        {"lat": 52.500, "lng": 13.399},
                        {"lat": 52.501, "lng": 13.399},
                        {"lat": 52.501, "lng": 13.400},
                    ]
                },
                "altitude_lower": {"value": 0, "reference": "W84", "units": "M"},
                "altitude_upper": {"value": 100, "reference": "W84", "units": "M"},
            },
            "time_start": {"format": "RFC3339", "value": arrow.now().shift(minutes=minutes_ahead).isoformat()},
            "time_end": {"format": "RFC3339", "value": arrow.now().shift(hours=1).isoformat()},
        }

    def _circle_volume_dict(self):
        now = arrow.now()
        return {
            "volume": {
                "outline_circle": {
                    "center": {"lat": 47.3769, "lng": 8.5417},
                    "radius": {"value": 300, "units": "M"},
                },
                "altitude_lower": {"value": 0, "reference": "W84", "units": "M"},
                "altitude_upper": {"value": 100, "reference": "W84", "units": "M"},
            },
            "time_start": {"format": "RFC3339", "value": now.shift(minutes=5).isoformat()},
            "time_end": {"format": "RFC3339", "value": now.shift(hours=1).isoformat()},
        }

    def test_parse_polygon_volume_to_volume4d(self):
        helper = OperationalIntentReferenceHelper()
        vol = helper.parse_volume_to_volume4D(self._volume_dict())
        assert isinstance(vol, Volume4D)
        assert vol.volume.outline_polygon is not None
        assert len(vol.volume.outline_polygon.vertices) == 3

    def test_parse_circle_volume_to_volume4d(self):
        helper = OperationalIntentReferenceHelper()
        vol = helper.parse_volume_to_volume4D(self._circle_volume_dict())
        assert isinstance(vol, Volume4D)
        assert vol.volume.outline_circle is not None

    def test_parse_operational_intent_details(self):
        helper = OperationalIntentReferenceHelper()
        volumes = [self._volume_dict()]
        details = helper.parse_operational_intent_details(volumes=volumes, priority=5)
        assert isinstance(details, OperationalIntentUSSDetails)
        assert len(details.volumes) == 1
        assert details.priority == 5

    def test_parse_operational_intent_details_with_off_nominal(self):
        helper = OperationalIntentReferenceHelper()
        volumes = [self._volume_dict()]
        off_nom = [self._volume_dict(minutes_ahead=10)]
        details = helper.parse_operational_intent_details(volumes=volumes, priority=0, off_nominal_volumes=off_nom)
        assert len(details.off_nominal_volumes) == 1

    def test_parse_operational_intent_reference_from_dss(self):
        now = arrow.now()
        raw = {
            "id": "ref-001",
            "manager": "test-mgr",
            "uss_availability": "Unknown",
            "version": 3,
            "state": "Accepted",
            "ovn": "ovn-xyz",
            "time_start": {"format": "RFC3339", "value": now.isoformat()},
            "time_end": {"format": "RFC3339", "value": now.shift(hours=1).isoformat()},
            "uss_base_url": "http://example.com",
            "subscription_id": "sub-abc",
        }
        helper = OperationalIntentReferenceHelper()
        ref = helper.parse_operational_intent_reference_from_dss(raw)
        assert isinstance(ref, OperationalIntentReferenceDSSResponse)
        assert ref.id == "ref-001"
        assert ref.version == 3
        assert ref.state == "Accepted"


class TestOperationalIntentUpdatePreSubmissionChecks:
    def test_activated_update_with_conflict_is_rejected_before_dss_submission(self):
        ops = dss_helper.SCDOperations()

        response = ops.check_if_update_payload_should_be_submitted_to_dss(
            current_state=OperationalIntentState.Activated.value,
            new_state=OperationalIntentState.Activated.value,
            extents_conflict_with_dss_volumes=True,
            priority=0,
        )

        assert response.should_submit_update_payload_to_dss == 0
        assert response.check_id == OpIntUpdateCheckResultCodes.B
        assert response.tentative_flight_plan_processing_response == FlightPlanCurrentStatus.OkToFly


class TestPeerUSSNotification:
    async def test_notify_auth_failure_raises_http_exception(self, monkeypatch):
        ops = dss_helper.SCDOperations()

        async def auth_error(audience):
            return {"error": "auth_failed"}

        monkeypatch.setattr(ops, "async_get_auth_token", auth_error)

        with pytest.raises(HTTPException) as exc_info:
            await ops.notify_peer_uss_of_created_updated_operational_intent(
                uss_base_url="http://peer.example.com",
                notification_payload=_make_notification_payload(),
                audience="peer.example.com",
            )

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == {"message": "Failed to get auth token for peer USS notification"}

    async def test_notify_transport_failure_raises_http_exception(self, monkeypatch):
        ops = dss_helper.SCDOperations()

        async def auth_success(audience):
            return {"access_token": "token"}

        monkeypatch.setattr(ops, "async_get_auth_token", auth_success)

        async def raise_transport_error(method, endpoint, **kwargs):
            raise HTTPException(status_code=504, detail={"message": f"Request to {endpoint} timed out"})

        monkeypatch.setattr(dss_helper, "_async_request", raise_transport_error)

        with pytest.raises(HTTPException) as exc_info:
            await ops.notify_peer_uss_of_created_updated_operational_intent(
                uss_base_url="http://peer.example.com",
                notification_payload=_make_notification_payload(),
                audience="peer.example.com",
            )

        assert exc_info.value.status_code == 504
        assert exc_info.value.detail == {"message": "Request to http://peer.example.com/uss/v1/operational_intents timed out"}

    async def test_notify_peer_non_204_raises_http_exception(self, monkeypatch):
        ops = dss_helper.SCDOperations()

        async def auth_success(audience):
            return {"access_token": "token"}

        monkeypatch.setattr(ops, "async_get_auth_token", auth_success)
        request = httpx.Request("POST", "http://peer.example.com/uss/v1/operational_intents")

        async def peer_error_response(method, endpoint, **kwargs):
            return httpx.Response(503, request=request)

        monkeypatch.setattr(dss_helper, "_async_request", peer_error_response)

        with pytest.raises(HTTPException) as exc_info:
            await ops.notify_peer_uss_of_created_updated_operational_intent(
                uss_base_url="http://peer.example.com",
                notification_payload=_make_notification_payload(),
                audience="peer.example.com",
            )

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == {"message": "Error in notification"}

    async def test_notify_success_returns_response(self, monkeypatch):
        ops = dss_helper.SCDOperations()

        async def auth_success(audience):
            return {"access_token": "token"}

        monkeypatch.setattr(ops, "async_get_auth_token", auth_success)
        request = httpx.Request("POST", "http://peer.example.com/uss/v1/operational_intents")

        async def peer_success_response(method, endpoint, **kwargs):
            return httpx.Response(204, request=request)

        monkeypatch.setattr(dss_helper, "_async_request", peer_success_response)

        response = await ops.notify_peer_uss_of_created_updated_operational_intent(
            uss_base_url="http://peer.example.com",
            notification_payload=_make_notification_payload(),
            audience="peer.example.com",
        )

        assert response.status == 204
