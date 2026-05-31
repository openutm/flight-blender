"""
Wiring + fail-closed tests for the strategic deconfliction path in the
flight-declaration router.

These exercise the router's ``_run_deconfliction`` glue and the create/ingest
endpoints, with the deconfliction engine patched so we assert the *decision*
(approval + resulting state) without any real DB-of-peers or network.
"""

import unittest.mock as mock
from datetime import datetime, timedelta, timezone

import pytest

from flight_blender.routers import flight_declaration as fd_router

pytestmark = pytest.mark.anyio

BASE = "/flight_declaration_ops"


def _future_times():
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    end = start + timedelta(hours=2)
    return start.isoformat(), end.isoformat()


def _geo_json():
    return {
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
    }


def _payload():
    start, end = _future_times()
    return {
        "aircraft_id": "TEST-AIRCRAFT",
        "originating_party": "Test Operator",
        "start_datetime": start,
        "end_datetime": end,
        "flight_declaration_geo_json": _geo_json(),
        "type_of_operation": 0,
    }


class TestFailClosed:
    async def test_set_flight_declaration_fails_closed_on_engine_error(self, client):
        """If the deconfliction engine raises, the declaration must NOT be accepted.

        The fail-closed guard lives inside ``_run_deconfliction``; we trigger it
        by making plugin loading raise, then assert the persisted operation is
        Rejected rather than silently accepted.
        """
        with mock.patch.object(fd_router, "load_plugin", side_effect=RuntimeError("import boom")):
            resp = await client.post(f"{BASE}/set_flight_declaration", json=_payload())
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["is_approved"] is False
        assert data["state"] == 8  # Rejected — fail closed, never accepted

    async def test_run_deconfliction_engine_error_fails_closed(self, db):
        """_run_deconfliction itself must fail closed when plugin loading raises."""
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        end = start + timedelta(hours=3)
        with mock.patch.object(fd_router, "load_plugin", side_effect=RuntimeError("import boom")):
            is_approved, state = await fd_router._run_deconfliction(
                geo_json=_geo_json(),
                start_datetime=start,
                end_datetime=end,
                db=db,
                bounds='{"minx": -122.4, "miny": 37.7, "maxx": -122.3, "maxy": 37.8}',
            )
        assert is_approved is False
        assert state == 8


class TestAcceptOnClear:
    async def test_clear_is_accepted(self, client):
        with mock.patch.object(fd_router, "_run_deconfliction", return_value=(True, 1)):
            resp = await client.post(f"{BASE}/set_flight_declaration", json=_payload())
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["is_approved"] is True
        assert data["state"] == 1

    async def test_conflict_is_rejected(self, client):
        with mock.patch.object(fd_router, "_run_deconfliction", return_value=(False, 8)):
            resp = await client.post(f"{BASE}/set_flight_declaration", json=_payload())
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["is_approved"] is False
        assert data["state"] == 8


class TestEndToEndConflict:
    """A real second declaration overlapping the first (no engine patch) must be
    rejected; a disjoint one accepted."""

    async def _create(self, client, coords):
        start, end = _future_times()
        payload = {
            "aircraft_id": "TEST-AIRCRAFT",
            "originating_party": "Operator",
            "start_datetime": start,
            "end_datetime": end,
            "type_of_operation": 0,
            "flight_declaration_geo_json": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"min_altitude": {"meters": 50}, "max_altitude": {"meters": 120}},
                        "geometry": {"type": "Polygon", "coordinates": [coords]},
                    }
                ],
            },
        }
        return await client.post(f"{BASE}/set_flight_declaration", json=payload)

    async def test_overlapping_declaration_rejected(self, client):
        square_a = [[-122.4, 37.7], [-122.4, 37.8], [-122.3, 37.8], [-122.3, 37.7], [-122.4, 37.7]]
        overlap = [[-122.35, 37.75], [-122.35, 37.85], [-122.25, 37.85], [-122.25, 37.75], [-122.35, 37.75]]
        first = await self._create(client, square_a)
        assert first.json()["state"] == 1  # accepted, becomes active peer
        second = await self._create(client, overlap)
        assert second.json()["is_approved"] is False
        assert second.json()["state"] == 8

    async def test_disjoint_declaration_accepted(self, client):
        square_a = [[-122.4, 37.7], [-122.4, 37.8], [-122.3, 37.8], [-122.3, 37.7], [-122.4, 37.7]]
        far = [[-100.0, 30.0], [-100.0, 30.1], [-99.9, 30.1], [-99.9, 30.0], [-100.0, 30.0]]
        await self._create(client, square_a)
        second = await self._create(client, far)
        assert second.json()["is_approved"] is True
        assert second.json()["state"] == 1
