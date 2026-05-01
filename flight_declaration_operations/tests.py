"""Tests for the flight declaration endpoints (single and bulk).

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

The bulk endpoint tests additionally cover:

* Batch submission with all-valid, all-invalid, and mixed payloads.
* Correct HTTP status codes (200 vs 207 for partial failures).
* Per-item result ordering and index labelling.
* In-batch intersection conflict handling (some approved, some rejected).
* Edge cases: empty list, non-list body, wrong content type.
"""

import json
import os
from unittest.mock import patch

import arrow
import jwt
from django.test import Client, TestCase, override_settings

from common.data_definitions import RESPONSE_CONTENT_TYPE

from .data_definitions import IntersectionCheckResult
from .models import FlightDeclaration, FlightOperationTracking, FlightOperationalIntentReference

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

    With ``BYPASS_AUTH_TOKEN_VERIFICATION=1`` the decorator calls
    ``handle_bypass_verification``, which decodes with
    ``options={"verify_signature": False}``.  In PyJWT ≥ 2.0 this flag
    disables the algorithm-mismatch check as well, so an HS256 token is
    accepted even though the call lists ``algorithms=["RS256"]``.  The
    decoder only validates ``scope``, ``iss``, and ``aud`` claims.
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


def _mock_run_deconfliction(result: IntersectionCheckResult):
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
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_valid_payload_creates_declaration_and_returns_200(
        self,
        mock_run_deconfliction,
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
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(
            IntersectionCheckResult(
                all_relevant_fences=[],
                all_relevant_declarations=[],
                is_approved=True,
                declaration_state=0,
            )
        ),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_valid_payload_ussp_enabled_submits_to_dss(
        self,
        mock_run_deconfliction,
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
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_CONFLICTING_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_intersection_conflict_rejects_declaration(
        self,
        mock_run_deconfliction,
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
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_notification_task_called_on_creation(
        self,
        mock_run_deconfliction,
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
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_response_shape_matches_dataclass(
        self,
        mock_run_deconfliction,
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
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_valid_payload_creates_declaration_and_returns_200(
        self,
        mock_run_deconfliction,
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
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(
            IntersectionCheckResult(
                all_relevant_fences=[],
                all_relevant_declarations=[],
                is_approved=True,
                declaration_state=0,
            )
        ),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_valid_payload_ussp_enabled_submits_to_dss(
        self,
        mock_run_deconfliction,
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
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_CONFLICTING_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_intersection_conflict_rejects_declaration(
        self,
        mock_run_deconfliction,
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
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_notification_task_called_on_creation(
        self,
        mock_run_deconfliction,
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
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_response_shape_matches_dataclass(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        payload = _make_operational_intent_payload()

        response = self._post(payload)

        body = response.json()
        self.assertCountEqual(body.keys(), {"id", "message", "is_approved", "state"})


# ---------------------------------------------------------------------------
# Helpers for bulk intersection-check mocking
# ---------------------------------------------------------------------------


def _mock_run_deconfliction_alternating(approved_result, rejected_result):
    """Return a side_effect that approves even-indexed declarations and rejects odd-indexed ones.

    The declarations list order is used to determine the index.
    """

    def side_effect(flight_declarations, ussp_network_enabled):
        results = {}
        for i, fd in enumerate(flight_declarations):
            results[str(fd.id)] = approved_result if i % 2 == 0 else rejected_result
        return results

    return side_effect


# ---------------------------------------------------------------------------
# set_flight_declarations_bulk tests
# ---------------------------------------------------------------------------


@override_settings(
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    CELERY_TASK_ALWAYS_EAGER=True,
)
class SetFlightDeclarationsBulkTests(TestCase):
    """Tests for the ``set_flight_declarations_bulk`` endpoint."""

    URL = "/flight_declaration_ops/set_flight_declarations_bulk"

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

    # -- Happy path: all valid, no conflicts → 200 -------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_all_valid_returns_200_with_all_approved(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        payloads = [_make_flight_declaration_payload(aircraft_id=f"ac-{i}") for i in range(3)]

        response = self._post(payloads)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["submitted"], 3)
        self.assertEqual(body["failed"], 0)
        self.assertEqual(len(body["results"]), 3)

        for i, result in enumerate(body["results"]):
            self.assertEqual(result["index"], i)
            self.assertTrue(result["success"])
            self.assertTrue(result["is_approved"])
            self.assertEqual(result["state"], 1)
            self.assertIn("id", result)

        # All three rows created in the database
        self.assertEqual(FlightDeclaration.objects.count(), 3)

    # -- All valid with USSP enabled → state=0 & DSS submission ------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(
            IntersectionCheckResult(
                all_relevant_fences=[],
                all_relevant_declarations=[],
                is_approved=True,
                declaration_state=0,
            )
        ),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_all_valid_ussp_enabled_submits_each_to_dss(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        payloads = [_make_flight_declaration_payload(aircraft_id=f"ac-{i}") for i in range(2)]

        response = self._post(payloads)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        for result in body["results"]:
            self.assertEqual(result["state"], 0)

        # DSS submission called once per declaration
        self.assertEqual(mock_submit_dss.delay.call_count, 2)

    # -- Non-list body → 400 -----------------------------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_non_list_body_returns_400(
        self,
        mock_send_msg,
        mock_submit_dss,
    ):
        response = self._post({"not": "a list"})

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("message", body)
        self.assertEqual(FlightDeclaration.objects.count(), 0)

    # -- Empty list → 200 with submitted=0, failed=0 -----------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_empty_list_returns_200_with_zero_counts(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        response = self._post([])

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["submitted"], 0)
        self.assertEqual(body["failed"], 0)
        self.assertEqual(body["results"], [])

    # -- Mixed valid/invalid → 207, partial failures -----------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_mixed_valid_invalid_returns_207(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        valid = _make_flight_declaration_payload()
        invalid_missing_aircraft = _make_flight_declaration_payload()
        del invalid_missing_aircraft["aircraft_id"]
        valid2 = _make_flight_declaration_payload(aircraft_id="ac-2")

        response = self._post([valid, invalid_missing_aircraft, valid2])

        self.assertEqual(response.status_code, 207)
        body = response.json()
        self.assertEqual(body["submitted"], 2)
        self.assertEqual(body["failed"], 1)
        self.assertEqual(len(body["results"]), 3)

        # Results sorted by index
        self.assertEqual(body["results"][0]["index"], 0)
        self.assertTrue(body["results"][0]["success"])
        self.assertEqual(body["results"][1]["index"], 1)
        self.assertFalse(body["results"][1]["success"])
        self.assertEqual(body["results"][2]["index"], 2)
        self.assertTrue(body["results"][2]["success"])

        # Only 2 rows created
        self.assertEqual(FlightDeclaration.objects.count(), 2)

    # -- All invalid → 207 -------------------------------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_all_invalid_returns_207_with_all_failed(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        bad1 = _make_flight_declaration_payload()
        del bad1["aircraft_id"]
        bad2 = _make_flight_declaration_payload()
        del bad2["aircraft_id"]

        response = self._post([bad1, bad2])

        self.assertEqual(response.status_code, 207)
        body = response.json()
        self.assertEqual(body["submitted"], 0)
        self.assertEqual(body["failed"], 2)
        for result in body["results"]:
            self.assertFalse(result["success"])

        self.assertEqual(FlightDeclaration.objects.count(), 0)
        # No intersection check needed when nothing was saved
        mock_run_deconfliction.assert_called_once()

    # -- Intersection conflict for some items in batch ---------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction_alternating(_NO_INTERSECTION, _CONFLICTING_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_partial_intersection_conflict_in_batch(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        payloads = [_make_flight_declaration_payload(aircraft_id=f"ac-{i}") for i in range(4)]

        response = self._post(payloads)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["submitted"], 4)
        self.assertEqual(body["failed"], 0)

        # Even-indexed approved, odd-indexed rejected
        for result in body["results"]:
            if result["index"] % 2 == 0:
                self.assertTrue(result["is_approved"])
                self.assertEqual(result["state"], 1)
            else:
                self.assertFalse(result["is_approved"])
                self.assertEqual(result["state"], 8)

        # Rejected declarations in DB have state=8
        for result in body["results"]:
            fd = FlightDeclaration.objects.get(pk=result["id"])
            if result["index"] % 2 == 0:
                self.assertTrue(fd.is_approved)
            else:
                self.assertFalse(fd.is_approved)
                self.assertEqual(fd.state, 8)

    # -- check_intersections called once with all saved declarations -------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_check_intersections_called_once_with_all_saved(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        payloads = [_make_flight_declaration_payload(aircraft_id=f"ac-{i}") for i in range(3)]

        self._post(payloads)

        # check_intersections is called exactly once (batch call)
        mock_run_deconfliction.assert_called_once()
        args = mock_run_deconfliction.call_args
        # First positional arg is the list of saved declarations
        fds_arg = args[0][0]
        self.assertEqual(len(fds_arg), 3)

    # -- Notification tasks fired for each saved declaration ----------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_notification_task_called_per_saved_declaration(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        payloads = [_make_flight_declaration_payload(aircraft_id=f"ac-{i}") for i in range(3)]

        self._post(payloads)

        # One notification per saved declaration
        self.assertEqual(mock_send_msg.delay.call_count, 3)

    # -- Wrong content type → 415 ------------------------------------------

    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_wrong_content_type_returns_415(self):
        payloads = [_make_flight_declaration_payload()]

        response = self._post(payloads, content_type="text/plain")

        self.assertEqual(response.status_code, 415)
        self.assertEqual(FlightDeclaration.objects.count(), 0)

    # -- Response shape matches BulkFlightDeclarationCreateResponse --------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_response_shape_matches_bulk_dataclass(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        payloads = [_make_flight_declaration_payload()]

        response = self._post(payloads)

        body = response.json()
        self.assertCountEqual(body.keys(), {"submitted", "failed", "results"})
        result = body["results"][0]
        self.assertIn("index", result)
        self.assertIn("success", result)
        self.assertIn("id", result)
        self.assertIn("is_approved", result)
        self.assertIn("state", result)

    # -- Results ordered by original index despite mixed processing --------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_results_ordered_by_index(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        valid = _make_flight_declaration_payload()
        invalid = _make_flight_declaration_payload()
        del invalid["aircraft_id"]
        valid2 = _make_flight_declaration_payload(aircraft_id="ac-x")

        response = self._post([valid, invalid, valid2])

        body = response.json()
        indices = [r["index"] for r in body["results"]]
        self.assertEqual(indices, [0, 1, 2])


# ---------------------------------------------------------------------------
# set_operational_intents_bulk tests
# ---------------------------------------------------------------------------


@override_settings(
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    CELERY_TASK_ALWAYS_EAGER=True,
)
class SetOperationalIntentsBulkTests(TestCase):
    """Tests for the ``set_operational_intents_bulk`` endpoint."""

    URL = "/flight_declaration_ops/set_operational_intents_bulk"

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

    # -- Happy path: all valid, no conflicts → 200 -------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_all_valid_returns_200_with_all_approved(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        payloads = [_make_operational_intent_payload(aircraft_id=f"ac-{i}") for i in range(3)]

        response = self._post(payloads)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["submitted"], 3)
        self.assertEqual(body["failed"], 0)
        self.assertEqual(len(body["results"]), 3)

        for i, result in enumerate(body["results"]):
            self.assertEqual(result["index"], i)
            self.assertTrue(result["success"])
            self.assertTrue(result["is_approved"])
            self.assertEqual(result["state"], 1)

        self.assertEqual(FlightDeclaration.objects.count(), 3)

    # -- All valid with USSP enabled → state=0 & DSS submission ------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(
            IntersectionCheckResult(
                all_relevant_fences=[],
                all_relevant_declarations=[],
                is_approved=True,
                declaration_state=0,
            )
        ),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_all_valid_ussp_enabled_submits_each_to_dss(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        payloads = [_make_operational_intent_payload(aircraft_id=f"ac-{i}") for i in range(2)]

        response = self._post(payloads)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        for result in body["results"]:
            self.assertEqual(result["state"], 0)

        self.assertEqual(mock_submit_dss.delay.call_count, 2)

    # -- Non-list body → 400 -----------------------------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_non_list_body_returns_400(
        self,
        mock_send_msg,
        mock_submit_dss,
    ):
        response = self._post({"not": "a list"})

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("message", body)
        self.assertEqual(FlightDeclaration.objects.count(), 0)

    # -- Empty list → 200 with submitted=0, failed=0 -----------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_empty_list_returns_200_with_zero_counts(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        response = self._post([])

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["submitted"], 0)
        self.assertEqual(body["failed"], 0)
        self.assertEqual(body["results"], [])

    # -- Mixed valid/invalid → 207, partial failures -----------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_mixed_valid_invalid_returns_207(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        valid = _make_operational_intent_payload()
        invalid_missing_aircraft = _make_operational_intent_payload()
        del invalid_missing_aircraft["aircraft_id"]
        valid2 = _make_operational_intent_payload(aircraft_id="ac-2")

        response = self._post([valid, invalid_missing_aircraft, valid2])

        self.assertEqual(response.status_code, 207)
        body = response.json()
        self.assertEqual(body["submitted"], 2)
        self.assertEqual(body["failed"], 1)
        self.assertEqual(len(body["results"]), 3)

        self.assertTrue(body["results"][0]["success"])
        self.assertFalse(body["results"][1]["success"])
        self.assertTrue(body["results"][2]["success"])

        self.assertEqual(FlightDeclaration.objects.count(), 2)

    # -- All invalid → 207 -------------------------------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_all_invalid_returns_207_with_all_failed(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        bad1 = _make_operational_intent_payload()
        del bad1["aircraft_id"]
        bad2 = _make_operational_intent_payload()
        del bad2["aircraft_id"]

        response = self._post([bad1, bad2])

        self.assertEqual(response.status_code, 207)
        body = response.json()
        self.assertEqual(body["submitted"], 0)
        self.assertEqual(body["failed"], 2)
        self.assertEqual(FlightDeclaration.objects.count(), 0)

    # -- Intersection conflict for some items in batch ---------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction_alternating(_NO_INTERSECTION, _CONFLICTING_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_partial_intersection_conflict_in_batch(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        payloads = [_make_operational_intent_payload(aircraft_id=f"ac-{i}") for i in range(4)]

        response = self._post(payloads)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["submitted"], 4)
        self.assertEqual(body["failed"], 0)

        for result in body["results"]:
            if result["index"] % 2 == 0:
                self.assertTrue(result["is_approved"])
                self.assertEqual(result["state"], 1)
            else:
                self.assertFalse(result["is_approved"])
                self.assertEqual(result["state"], 8)

    # -- check_intersections called once with all saved --------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_check_intersections_called_once_with_all_saved(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        payloads = [_make_operational_intent_payload(aircraft_id=f"ac-{i}") for i in range(3)]

        self._post(payloads)

        mock_run_deconfliction.assert_called_once()
        fds_arg = mock_run_deconfliction.call_args[0][0]
        self.assertEqual(len(fds_arg), 3)

    # -- Wrong content type → 415 ------------------------------------------

    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_wrong_content_type_returns_415(self):
        payloads = [_make_operational_intent_payload()]

        response = self._post(payloads, content_type="text/plain")

        self.assertEqual(response.status_code, 415)
        self.assertEqual(FlightDeclaration.objects.count(), 0)

    # -- Response shape matches BulkFlightDeclarationCreateResponse --------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_response_shape_matches_bulk_dataclass(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        payloads = [_make_operational_intent_payload()]

        response = self._post(payloads)

        body = response.json()
        self.assertCountEqual(body.keys(), {"submitted", "failed", "results"})
        result = body["results"][0]
        self.assertIn("index", result)
        self.assertIn("success", result)
        self.assertIn("id", result)
        self.assertIn("is_approved", result)
        self.assertIn("state", result)

    # -- Results ordered by original index ---------------------------------

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(_NO_INTERSECTION),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_results_ordered_by_index(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        valid = _make_operational_intent_payload()
        invalid = _make_operational_intent_payload()
        del invalid["aircraft_id"]
        valid2 = _make_operational_intent_payload(aircraft_id="ac-x")

        response = self._post([valid, invalid, valid2])

        body = response.json()
        indices = [r["index"] for r in body["results"]]
        self.assertEqual(indices, [0, 1, 2])


# ---------------------------------------------------------------------------
# AUTO_SUBMIT_TO_DSS and submit_to_dss endpoint tests
# ---------------------------------------------------------------------------

@override_settings(
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    CELERY_TASK_ALWAYS_EAGER=True,
)
class AutoSubmitToDssTests(TestCase):
    """Tests for the AUTO_SUBMIT_TO_DSS gate in _process_intersection_result."""

    URL = "/flight_declaration_ops/set_flight_declaration"

    
    def _post(self, payload, content_type=RESPONSE_CONTENT_TYPE):
        return self.client.post(
            self.URL,
            data=json.dumps(payload),
            content_type=content_type,
            HTTP_AUTHORIZATION=self.auth,
        )

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(
            IntersectionCheckResult(
                all_relevant_fences=[],
                all_relevant_declarations=[],
                is_approved=True,
                declaration_state=0,
            )
        ),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "AUTO_SUBMIT_TO_DSS": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_auto_submit_disabled_does_not_trigger_dss(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        """When AUTO_SUBMIT_TO_DSS=0, declarations remain in state=0 and are NOT submitted."""
        payload = _make_flight_declaration_payload()

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["state"], 0)
        mock_submit_dss.delay.assert_not_called()

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch(
        "flight_declaration_operations.views._run_deconfliction",
        side_effect=_mock_run_deconfliction(
            IntersectionCheckResult(
                all_relevant_fences=[],
                all_relevant_declarations=[],
                is_approved=True,
                declaration_state=0,
            )
        ),
    )
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "AUTO_SUBMIT_TO_DSS": "1", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_auto_submit_enabled_triggers_dss(
        self,
        mock_run_deconfliction,
        mock_send_msg,
        mock_submit_dss,
    ):
        """When AUTO_SUBMIT_TO_DSS=1 (default), DSS submission is triggered immediately."""
        payload = _make_flight_declaration_payload()

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        mock_submit_dss.delay.assert_called_once()


@override_settings(
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    CELERY_TASK_ALWAYS_EAGER=True,
)
class SubmitToDssEndpointTests(TestCase):
    """Tests for the POST /flight_declaration/<pk>/submit_to_dss endpoint."""

    def setUp(self):
        self.client = Client()
        self.auth = _make_dummy_bearer_token()

    def _make_declaration(self, state: int = 0) -> FlightDeclaration:
        now = arrow.now()
        fd = FlightDeclaration.objects.create(
            operational_intent="{}",
            bounds="-1,-1,1,1",
            type_of_operation=1,
            aircraft_id="test-ac",
            state=state,
            originating_party="Test",
            start_datetime=now.shift(minutes=10).datetime,
            end_datetime=now.shift(hours=1).datetime,
            is_approved=True,
        )
        return fd

    def _post(self, pk):
        return self.client.post(
            f"/flight_declaration_ops/flight_declaration/{pk}/submit_to_dss",
            content_type=RESPONSE_CONTENT_TYPE,
            HTTP_AUTHORIZATION=self.auth,
        )

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_valid_state0_declaration_triggers_dss_submission(self, mock_send_msg, mock_submit_dss):
        """A declaration in state=0 with USSP enabled should be submitted to DSS."""
        fd = self._make_declaration(state=0)

        response = self._post(fd.id)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("id", body)
        self.assertEqual(body["id"], str(fd.id))
        mock_submit_dss.delay.assert_called_once_with(flight_declaration_id=str(fd.id))

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "0", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_ussp_disabled_returns_400(self, mock_send_msg, mock_submit_dss):
        """Returns 400 when USSP_NETWORK_ENABLED=0."""
        fd = self._make_declaration(state=0)

        response = self._post(fd.id)

        self.assertEqual(response.status_code, 400)
        mock_submit_dss.delay.assert_not_called()

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_declaration_not_found_returns_404(self, mock_send_msg, mock_submit_dss):
        """Returns 404 when the declaration ID does not exist."""
        import uuid

        fake_id = uuid.uuid4()
        response = self._post(fake_id)

        self.assertEqual(response.status_code, 404)
        mock_submit_dss.delay.assert_not_called()

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_declaration_in_wrong_state_returns_409(self, mock_send_msg, mock_submit_dss):
        """Returns 409 when declaration is already in state != 0 (e.g. Accepted=1)."""
        fd = self._make_declaration(state=1)

        response = self._post(fd.id)

        self.assertEqual(response.status_code, 409)
        mock_submit_dss.delay.assert_not_called()

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_existing_opint_reference_returns_409(self, mock_send_msg, mock_submit_dss):
        """Returns 409 when a FlightOperationalIntentReference already exists (already submitted)."""
        import arrow as _arrow

        fd = self._make_declaration(state=0)
        # Simulate a previously completed DSS submission by creating the reference directly.
        FlightOperationalIntentReference.objects.create(
            declaration=fd,
            uss_availability="Normal",
            manager="test-manager",
            uss_base_url="http://localhost:8000",
            version="1",
            state="Accepted",
            subscription_id="sub-1",
            time_start=_arrow.now().shift(minutes=10).datetime,
            time_end=_arrow.now().shift(hours=1).datetime,
        )

        response = self._post(fd.id)

        self.assertEqual(response.status_code, 409)
        mock_submit_dss.delay.assert_not_called()

    @patch("flight_declaration_operations.views.submit_flight_declaration_to_dss_async")
    @patch("flight_declaration_operations.views.send_operational_update_message")
    @patch.dict(os.environ, {"USSP_NETWORK_ENABLED": "1", "BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_valid_submission_records_history_entry(self, mock_send_msg, mock_submit_dss):
        """A successful submission records a 'DSS submission initiated' history entry."""
        fd = self._make_declaration(state=0)

        response = self._post(fd.id)

        self.assertEqual(response.status_code, 200)
        history = FlightOperationTracking.objects.filter(
            flight_declaration=fd,
            notes="DSS submission initiated via manual endpoint",
        )
        self.assertTrue(history.exists())


# ---------------------------------------------------------------------------
# FlightDeclarationCreateList state-filter tests
# ---------------------------------------------------------------------------

class FlightDeclarationListStateFilterTests(TestCase):
    """Tests for the ``?state=`` query parameter on the flight_declaration list endpoint."""

    URL = "/flight_declaration_ops/flight_declaration"

    def setUp(self):
        self.client = Client()
        self.auth = _make_dummy_bearer_token()
        now = arrow.now()
        _empty_intent = json.dumps({"volumes": []})
        _raw_geojson = json.dumps({"type": "FeatureCollection", "features": []})
        _bounds = "0.0,0.0,1.0,1.0"
        # Create declarations with different states so we can filter them.
        self.fd_accepted = FlightDeclaration.objects.create(
            originating_party="Test",
            start_datetime=now.shift(minutes=10).isoformat(),
            end_datetime=now.shift(hours=1).isoformat(),
            type_of_operation=1,
            aircraft_id="ac-accepted",
            state=1,  # Accepted
            operational_intent=_empty_intent,
            flight_declaration_raw_geojson=_raw_geojson,
            bounds=_bounds,
        )
        self.fd_activated = FlightDeclaration.objects.create(
            originating_party="Test",
            start_datetime=now.shift(minutes=10).isoformat(),
            end_datetime=now.shift(hours=1).isoformat(),
            type_of_operation=1,
            aircraft_id="ac-activated",
            state=2,  # Activated
            operational_intent=_empty_intent,
            flight_declaration_raw_geojson=_raw_geojson,
            bounds=_bounds,
        )
        self.fd_rejected = FlightDeclaration.objects.create(
            originating_party="Test",
            start_datetime=now.shift(minutes=10).isoformat(),
            end_datetime=now.shift(hours=1).isoformat(),
            type_of_operation=1,
            aircraft_id="ac-rejected",
            state=8,  # Rejected
            operational_intent=_empty_intent,
            flight_declaration_raw_geojson=_raw_geojson,
            bounds=_bounds,
        )

    def _get(self, params=""):
        return self.client.get(
            f"{self.URL}{params}",
            HTTP_AUTHORIZATION=self.auth,
        )

    @patch.dict(os.environ, {"BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_no_state_filter_returns_all(self):
        """Without ?state= all declarations within the default date window are returned."""
        response = self._get()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        returned_ids = {r["id"] for r in body["results"]}
        self.assertIn(str(self.fd_accepted.id), returned_ids)
        self.assertIn(str(self.fd_activated.id), returned_ids)
        self.assertIn(str(self.fd_rejected.id), returned_ids)

    @patch.dict(os.environ, {"BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_single_state_filter(self):
        """?state=1 returns only Accepted declarations."""
        response = self._get("?state=1")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        returned_ids = {r["id"] for r in body["results"]}
        self.assertIn(str(self.fd_accepted.id), returned_ids)
        self.assertNotIn(str(self.fd_activated.id), returned_ids)
        self.assertNotIn(str(self.fd_rejected.id), returned_ids)

    @patch.dict(os.environ, {"BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_multiple_states_filter(self):
        """?state=1,2 returns Accepted and Activated declarations."""
        response = self._get("?state=1,2")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        returned_ids = {r["id"] for r in body["results"]}
        self.assertIn(str(self.fd_accepted.id), returned_ids)
        self.assertIn(str(self.fd_activated.id), returned_ids)
        self.assertNotIn(str(self.fd_rejected.id), returned_ids)

    @patch.dict(os.environ, {"BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_state_filter_no_matches_returns_empty(self):
        """?state=5 (Ended) returns an empty list when no matching declarations exist."""
        response = self._get("?state=5")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["results"], [])

    @patch.dict(os.environ, {"BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_invalid_state_value_returns_400(self):
        """?state=notanumber returns 400 Bad Request."""
        response = self._get("?state=notanumber")
        self.assertEqual(response.status_code, 400)

    @patch.dict(os.environ, {"BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_trailing_comma_parses_valid_states(self):
        """?state=1, (trailing comma) is treated as state=1 — empty token ignored."""
        response = self._get("?state=1,")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        returned_ids = {r["id"] for r in body["results"]}
        self.assertIn(str(self.fd_accepted.id), returned_ids)
        self.assertNotIn(str(self.fd_activated.id), returned_ids)
        self.assertNotIn(str(self.fd_rejected.id), returned_ids)

    @patch.dict(os.environ, {"BYPASS_AUTH_TOKEN_VERIFICATION": "1"})
    def test_double_comma_parses_valid_states(self):
        """?state=1,,2 (double comma) is treated as state=1,2 — empty token ignored."""
        response = self._get("?state=1,,2")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        returned_ids = {r["id"] for r in body["results"]}
        self.assertIn(str(self.fd_accepted.id), returned_ids)
        self.assertIn(str(self.fd_activated.id), returned_ids)
        self.assertNotIn(str(self.fd_rejected.id), returned_ids)

