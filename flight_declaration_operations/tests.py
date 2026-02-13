"""Tests for the single flight declaration endpoints.

These tests verify that the refactored ``set_flight_declaration`` and
``set_operational_intent`` endpoints produce exactly the same observable
behaviour as the original monolithic implementations:

* A valid payload creates a FlightDeclaration row, state history entries,
  fires the correct Celery tasks, and returns a 200 JSON response.
* An invalid payload returns a 400 JSON error and creates nothing.
* When an intersection conflict is detected the declaration is saved with
  ``state=8``, ``is_approved=False``, and the rejection history entry is
  recorded.
* Content-Type enforcement is preserved.
"""

import json
import os
from unittest.mock import patch

import arrow
import jwt
from django.test import Client, TestCase, override_settings

from common.data_definitions import RESPONSE_CONTENT_TYPE

from .data_definitions import IntersectionCheckResult
from .models import FlightDeclaration, FlightOperationTracking


# ---------------------------------------------------------------------------
# Test-payload helpers
# ---------------------------------------------------------------------------

def _make_geojson_feature_collection(
    lng: float = 8.55,
    lat: float = 47.36,
    min_alt_m: int = 50,
    max_alt_m: int = 120,
) -> dict:
    """Build a minimal, valid GeoJSON FeatureCollection with one polygon feature."""
    offset = 0.001
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [lng - offset, lat - offset],
                            [lng + offset, lat - offset],
                            [lng + offset, lat + offset],
                            [lng - offset, lat + offset],
                            [lng - offset, lat - offset],
                        ]
                    ],
                },
                "properties": {
                    "min_altitude": {"meters": min_alt_m, "datum": "W84"},
                    "max_altitude": {"meters": max_alt_m, "datum": "W84"},
                },
            }
        ],
    }


def _make_flight_declaration_payload(**overrides) -> dict:
    """Return a valid ``set_flight_declaration`` request payload."""
    now = arrow.now()
    payload = {
        "originating_party": "Test Party",
        "start_datetime": now.shift(minutes=10).isoformat(),
        "end_datetime": now.shift(hours=1).isoformat(),
        "flight_declaration_geo_json": _make_geojson_feature_collection(),
        "type_of_operation": 1,
        "aircraft_id": "test-aircraft-001",
    }
    payload.update(overrides)
    return payload


def _make_operational_intent_payload(**overrides) -> dict:
    """Return a valid ``set_operational_intent`` request payload."""
    now = arrow.now()
    payload = {
        "originating_party": "Test Party",
        "start_datetime": now.shift(minutes=10).isoformat(),
        "end_datetime": now.shift(hours=1).isoformat(),
        "type_of_operation": 1,
        "aircraft_id": "test-aircraft-001",
        "operational_intent_volume4ds": [
            {
                "volume": {
                    "outline_polygon": {
                        "vertices": [
                            {"lat": 47.36, "lng": 8.55},
                            {"lat": 47.361, "lng": 8.55},
                            {"lat": 47.361, "lng": 8.551},
                            {"lat": 47.36, "lng": 8.551},
                        ]
                    },
                    "altitude_lower": {
                        "value": 50,
                        "reference": "W84",
                        "units": "M",
                    },
                    "altitude_upper": {
                        "value": 120,
                        "reference": "W84",
                        "units": "M",
                    },
                },
                "time_start": {"value": now.shift(minutes=10).isoformat(), "format": "RFC3339"},
                "time_end": {"value": now.shift(hours=1).isoformat(), "format": "RFC3339"},
            }
        ],
    }
    payload.update(overrides)
    return payload


def _make_dummy_bearer_token() -> str:
    """Create a minimal JWT accepted by the bypass-auth path.

    With ``BYPASS_AUTH_TOKEN_VERIFICATION=1`` the decorator decodes the token
    **without** verifying the signature, so HS256 with an arbitrary key is fine.
    It only checks for ``scope``, ``iss``, and ``aud`` claims.
    """
    token = jwt.encode(
        {
            "scope": "flightblender.read flightblender.write",
            "iss": "dummy",
            "aud": "testflight.flightblender.com",
            "sub": "test-user",
        },
        key="test-secret",
        algorithm="HS256",
    )
    return f"Bearer {token}"


# ---------------------------------------------------------------------------
# Reusable intersection-check fixtures
# ---------------------------------------------------------------------------
_NO_INTERSECTION = IntersectionCheckResult(
    all_relevant_fences=[],
    all_relevant_declarations=[],
    is_approved=True,
    declaration_state=1,
)

_CONFLICTING_INTERSECTION = IntersectionCheckResult(
    all_relevant_fences=[{"id": "fence-1"}],
    all_relevant_declarations=[{"id": "decl-1"}],
    is_approved=False,
    declaration_state=8,
)


def _mock_check_intersections(result: IntersectionCheckResult):
    """Create a side_effect for check_intersections that returns *result* for every declaration."""
    def side_effect(flight_declarations, ussp_network_enabled):
        return {str(fd.id): result for fd in flight_declarations}
    return side_effect


# ---------------------------------------------------------------------------
# set_flight_declaration tests
# ---------------------------------------------------------------------------

@override_settings(
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    CELERY_TASK_ALWAYS_EAGER=True,
)
class SetFlightDeclarationTests(TestCase):
    """Tests for the ``set_flight_declaration`` endpoint."""

    URL = "/flight_declaration_ops/set_flight_declaration"

    def setUp(self):
        self.client = Client()
        self.auth = _make_dummy_bearer_token()

    def _post(self, payload, content_type=RESPONSE_CONTENT_TYPE):
        return self.client.post(
            self.URL,
            data=json.dumps(payload),
            content_type=content_type,
            HTTP_AUTHORIZATION=self.auth,
        )

    # -- Happy path: valid payload, no conflicts --------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views.FlightDeclarationRequestValidator.check_intersections",
        side_effect=_mock_check_intersections(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_valid_payload_creates_declaration_and_returns_200(
        self,
        mock_check_intersections,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_flight_declaration_payload()

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("id", body)
        self.assertEqual(body["message"], "Submitted Flight Declaration")
        self.assertTrue(body["is_approved"])
        self.assertEqual(body["state"], 1)  # USSP disabled -> state=1

        # A FlightDeclaration row was created
        fd = FlightDeclaration.objects.get(pk=body["id"])
        self.assertEqual(fd.aircraft_id, "test-aircraft-001")
        self.assertEqual(fd.originating_party, "Test Party")
        self.assertTrue(fd.is_approved)
        self.assertEqual(fd.state, 1)

        # State history: at least the "Created Declaration" entry
        history = FlightOperationTracking.objects.filter(flight_declaration=fd)
        self.assertTrue(history.exists())
        created_entry = history.filter(notes="Created Declaration")
        self.assertTrue(created_entry.exists())

    # -- Happy path with USSP enabled -> state=0 & DSS submission ----------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views.FlightDeclarationRequestValidator.check_intersections",
        side_effect=_mock_check_intersections(IntersectionCheckResult(
            all_relevant_fences=[],
            all_relevant_declarations=[],
            is_approved=True,
            declaration_state=0,
        )),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_valid_payload_ussp_enabled_submits_to_dss(
        self,
        mock_check_intersections,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_flight_declaration_payload()

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["state"], 0)  # USSP enabled -> state=0

        # DSS async submission should have been called
        mock_submit_dss.delay.assert_called_once()

    # -- Intersection conflict -> state=8 & is_approved=False ---------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views.FlightDeclarationRequestValidator.check_intersections",
        side_effect=_mock_check_intersections(_CONFLICTING_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_intersection_conflict_rejects_declaration(
        self,
        mock_check_intersections,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_flight_declaration_payload()

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["is_approved"])
        self.assertEqual(body["state"], 8)

        fd = FlightDeclaration.objects.get(pk=body["id"])
        self.assertFalse(fd.is_approved)
        self.assertEqual(fd.state, 8)

        # Rejection history entry should exist
        rejection_entries = FlightOperationTracking.objects.filter(
            flight_declaration=fd,
            notes__icontains="Rejected by Flight Blender",
        )
        self.assertTrue(rejection_entries.exists())

        # Deconfliction failure message should be sent
        self.assertTrue(mock_send_msg.delay.call_count >= 2)

        # DSS submission should NOT be called
        mock_submit_dss.delay.assert_not_called()

    # -- Notification task fired on creation --------------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views.FlightDeclarationRequestValidator.check_intersections",
        side_effect=_mock_check_intersections(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_notification_task_called_on_creation(
        self,
        mock_check_intersections,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_flight_declaration_payload()

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()

        mock_send_msg.delay.assert_called_once_with(
            flight_declaration_id=body["id"],
            message_text="Flight Declaration created..",
            level="info",
        )

    # -- Validation failure: missing required field -------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_missing_aircraft_id_returns_400(
        self,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_flight_declaration_payload()
        del payload["aircraft_id"]

        response = self._post(payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(FlightDeclaration.objects.count(), 0)
        mock_send_msg.delay.assert_not_called()

    # -- Validation failure: missing GeoJSON --------------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_missing_geojson_returns_400(
        self,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_flight_declaration_payload()
        del payload["flight_declaration_geo_json"]

        response = self._post(payload)

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("message", body)
        self.assertEqual(FlightDeclaration.objects.count(), 0)

    # -- Validation failure: dates in the past ------------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_past_dates_returns_400(
        self,
        mock_send_msg,
        mock_submit_dss,
    ):
        past = arrow.now().shift(hours=-2)
        payload = _make_flight_declaration_payload(
            start_datetime=past.isoformat(),
            end_datetime=past.shift(hours=1).isoformat(),
        )

        response = self._post(payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(FlightDeclaration.objects.count(), 0)

    # -- Wrong Content-Type returns 415 ------------------------------------
    # DRF's @api_view rejects unsupported content types during request
    # parsing (before the view body runs), returning a proper 415.

    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_wrong_content_type_returns_415(self):
        payload = _make_flight_declaration_payload()

        response = self._post(payload, content_type="text/plain")

        self.assertEqual(response.status_code, 415)
        self.assertEqual(FlightDeclaration.objects.count(), 0)

    # -- Response shape matches FlightDeclarationCreateResponse ------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views.FlightDeclarationRequestValidator.check_intersections",
        side_effect=_mock_check_intersections(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_response_shape_matches_dataclass(
        self,
        mock_check_intersections,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_flight_declaration_payload()

        response = self._post(payload)

        body = response.json()
        self.assertCountEqual(body.keys(), {"id", "message", "is_approved", "state"})


# ---------------------------------------------------------------------------
# set_operational_intent tests
# ---------------------------------------------------------------------------

@override_settings(
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    CELERY_TASK_ALWAYS_EAGER=True,
)
class SetOperationalIntentTests(TestCase):
    """Tests for the ``set_operational_intent`` endpoint."""

    URL = "/flight_declaration_ops/set_operational_intent"

    def setUp(self):
        self.client = Client()
        self.auth = _make_dummy_bearer_token()

    def _post(self, payload, content_type=RESPONSE_CONTENT_TYPE):
        return self.client.post(
            self.URL,
            data=json.dumps(payload),
            content_type=content_type,
            HTTP_AUTHORIZATION=self.auth,
        )

    # -- Happy path: valid payload, no conflicts --------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views.FlightDeclarationRequestValidator.check_intersections",
        side_effect=_mock_check_intersections(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_valid_payload_creates_declaration_and_returns_200(
        self,
        mock_check_intersections,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_operational_intent_payload()

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("id", body)
        self.assertEqual(body["message"], "Submitted Flight Declaration")
        self.assertTrue(body["is_approved"])
        self.assertEqual(body["state"], 1)  # USSP disabled -> state=1

        fd = FlightDeclaration.objects.get(pk=body["id"])
        self.assertEqual(fd.aircraft_id, "test-aircraft-001")
        self.assertTrue(fd.is_approved)

    # -- Happy path with USSP enabled -> state=0 & DSS submission ----------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views.FlightDeclarationRequestValidator.check_intersections",
        side_effect=_mock_check_intersections(IntersectionCheckResult(
            all_relevant_fences=[],
            all_relevant_declarations=[],
            is_approved=True,
            declaration_state=0,
        )),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_valid_payload_ussp_enabled_submits_to_dss(
        self,
        mock_check_intersections,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_operational_intent_payload()

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["state"], 0)
        mock_submit_dss.delay.assert_called_once()

    # -- Intersection conflict -> state=8 & is_approved=False ---------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views.FlightDeclarationRequestValidator.check_intersections",
        side_effect=_mock_check_intersections(_CONFLICTING_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_intersection_conflict_rejects_declaration(
        self,
        mock_check_intersections,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_operational_intent_payload()

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["is_approved"])
        self.assertEqual(body["state"], 8)

        fd = FlightDeclaration.objects.get(pk=body["id"])
        self.assertFalse(fd.is_approved)
        self.assertEqual(fd.state, 8)

        rejection_entries = FlightOperationTracking.objects.filter(
            flight_declaration=fd,
            notes__icontains="Rejected by Flight Blender",
        )
        self.assertTrue(rejection_entries.exists())
        mock_submit_dss.delay.assert_not_called()

    # -- Validation failure: missing required field -------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_missing_aircraft_id_returns_400(
        self,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_operational_intent_payload()
        del payload["aircraft_id"]

        response = self._post(payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(FlightDeclaration.objects.count(), 0)
        mock_send_msg.delay.assert_not_called()

    # -- Validation failure: dates in the past ------------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_past_dates_returns_400(
        self,
        mock_send_msg,
        mock_submit_dss,
    ):
        past = arrow.now().shift(hours=-2)
        payload = _make_operational_intent_payload(
            start_datetime=past.isoformat(),
            end_datetime=past.shift(hours=1).isoformat(),
        )
        payload["operational_intent_volume4ds"][0]["time_start"]["value"] = past.isoformat()
        payload["operational_intent_volume4ds"][0]["time_end"]["value"] = past.shift(hours=1).isoformat()

        response = self._post(payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(FlightDeclaration.objects.count(), 0)

    # -- Wrong Content-Type returns 415 ------------------------------------
    # DRF's @api_view rejects unsupported content types during request
    # parsing (before the view body runs), returning a proper 415.

    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_wrong_content_type_returns_415(self):
        payload = _make_operational_intent_payload()

        response = self._post(payload, content_type="text/plain")

        self.assertEqual(response.status_code, 415)
        self.assertEqual(FlightDeclaration.objects.count(), 0)

    # -- Notification task is called with correct id -----------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views.FlightDeclarationRequestValidator.check_intersections",
        side_effect=_mock_check_intersections(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_notification_task_called_on_creation(
        self,
        mock_check_intersections,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_operational_intent_payload()

        response = self._post(payload)

        body = response.json()
        mock_send_msg.delay.assert_called_once_with(
            flight_declaration_id=body["id"],
            message_text="Flight Declaration created..",
            level="info",
        )

    # -- Response shape matches FlightDeclarationCreateResponse ------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views.FlightDeclarationRequestValidator.check_intersections",
        side_effect=_mock_check_intersections(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_response_shape_matches_dataclass(
        self,
        mock_check_intersections,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_operational_intent_payload()

        response = self._post(payload)

        body = response.json()
        self.assertCountEqual(body.keys(), {"id", "message", "is_approved", "state"})
