"""
Tests for honoring the USSP_NETWORK_ENABLED gate on the flight-declaration
acceptance / DSS-submission path.

* USSP disabled: a clear declaration is Accepted (state 1) directly, no DSS
  submission.
* USSP enabled: a clear declaration is left Processing (state 0) and submitted
  to the DSS asynchronously.

Mirrors Django views.py: ``if is_approved and declaration_state == 0 and
ussp_network_enabled and auto_submit_to_dss: submit_..._async.delay(...)``.
"""

import unittest.mock as mock
from datetime import datetime, timedelta, timezone

import pytest

from flight_blender.routers import flight_declaration as fd_router
from flight_blender.services.deconfliction import resolve_decision

pytestmark = pytest.mark.anyio

BASE = "/flight_declaration_ops"


def _payload():
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    end = start + timedelta(hours=2)
    return {
        "aircraft_id": "TEST-AIRCRAFT",
        "originating_party": "Test Operator",
        "start_datetime": start.isoformat(),
        "end_datetime": end.isoformat(),
        "flight_declaration_geo_json": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"min_altitude": {"meters": 50}, "max_altitude": {"meters": 120}},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-122.4, 37.7], [-122.4, 37.8], [-122.3, 37.8], [-122.3, 37.7], [-122.4, 37.7]]],
                    },
                }
            ],
        },
        "type_of_operation": 0,
    }


class TestUSSPDecisionLogic:
    def test_clear_without_network_is_accepted(self):
        d = resolve_decision(engine_error=False, is_clear=True, ussp_network_enabled=False)
        assert (d.is_approved, d.declaration_state) == (True, 1)

    def test_clear_with_network_is_pending(self):
        d = resolve_decision(engine_error=False, is_clear=True, ussp_network_enabled=True)
        assert (d.is_approved, d.declaration_state) == (True, 0)

    def test_conflict_is_rejected_regardless_of_network(self):
        for net in (False, True):
            d = resolve_decision(engine_error=False, is_clear=False, ussp_network_enabled=net)
            assert (d.is_approved, d.declaration_state) == (False, 8)


def _enable_ussp():
    """Patch settings so the USSP network gate is on (and auto-submit enabled)."""
    settings = fd_router.get_settings()
    return (
        mock.patch.object(settings, "ussp_network_enabled", 1),
        mock.patch.object(settings, "auto_submit_to_dss", 1),
    )


class TestUSSPSubmissionGate:
    async def test_no_dss_submission_when_network_disabled(self, client):
        # Clear deconfliction, state Accepted (1) -> no DSS submission.
        with (
            mock.patch.object(fd_router, "_run_deconfliction", return_value=(True, 1)),
            mock.patch.object(fd_router.submit_flight_declaration_to_dss_async, "delay") as delay_mock,
        ):
            resp = await client.post(f"{BASE}/set_flight_declaration", json=_payload())
        assert resp.status_code in (200, 201)
        assert resp.json()["state"] == 1
        delay_mock.assert_not_called()

    async def test_dss_submission_when_network_enabled(self, client):
        # Clear deconfliction with USSP -> state Processing (0) and DSS submission.
        p_ussp, p_auto = _enable_ussp()
        with (
            p_ussp,
            p_auto,
            mock.patch.object(fd_router, "_run_deconfliction", return_value=(True, 0)),
            mock.patch.object(fd_router.submit_flight_declaration_to_dss_async, "delay") as delay_mock,
        ):
            resp = await client.post(f"{BASE}/set_flight_declaration", json=_payload())
        assert resp.status_code in (200, 201)
        assert resp.json()["state"] == 0
        delay_mock.assert_called_once()

    async def test_no_dss_submission_on_conflict(self, client):
        p_ussp, p_auto = _enable_ussp()
        with (
            p_ussp,
            p_auto,
            mock.patch.object(fd_router, "_run_deconfliction", return_value=(False, 8)),
            mock.patch.object(fd_router.submit_flight_declaration_to_dss_async, "delay") as delay_mock,
        ):
            resp = await client.post(f"{BASE}/set_flight_declaration", json=_payload())
        assert resp.status_code in (200, 201)
        assert resp.json()["state"] == 8
        delay_mock.assert_not_called()

    async def test_op_intent_submission_when_network_enabled(self, client):
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        end = start + timedelta(hours=2)
        op_payload = {
            "operational_intent_volume4ds": [
                {
                    "volume": {
                        "outline_polygon": {
                            "vertices": [
                                {"lng": -122.4, "lat": 37.7},
                                {"lng": -122.4, "lat": 37.8},
                                {"lng": -122.3, "lat": 37.8},
                                {"lng": -122.3, "lat": 37.7},
                            ]
                        },
                        "altitude_lower": {"value": 50},
                        "altitude_upper": {"value": 120},
                    }
                }
            ],
            "start_datetime": start.isoformat(),
            "end_datetime": end.isoformat(),
            "aircraft_id": "test-aircraft",
        }
        p_ussp, p_auto = _enable_ussp()
        with (
            p_ussp,
            p_auto,
            mock.patch.object(fd_router, "_run_deconfliction", return_value=(True, 0)),
            mock.patch.object(fd_router.submit_flight_declaration_to_dss_async, "delay") as delay_mock,
        ):
            resp = await client.post(f"{BASE}/set_operational_intent", json=op_payload)
        assert resp.status_code in (200, 201)
        assert resp.json()["state"] == 0
        delay_mock.assert_called_once()
