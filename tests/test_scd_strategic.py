"""
Tests for the SCD strategic conflict computation in the flight-planning router.

These assert the ASTM *planning_result* produced by the deconfliction engine
against declarations stored in the DB — no live DSS / network involved.

Behaviour mirrors the Django ``upsert_close_flight_plan``:
* usage_state not Planned/InUse, or no volumes supplied -> "NotPlanned".
* Planned/InUse with volumes, clear            -> "Planned".
* Planned/InUse with volumes, conflict          -> "ConflictWithFlight".
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from flight_blender.models.flight_declaration import FlightDeclaration

pytestmark = pytest.mark.anyio

BASE = "/scd"

_SQUARE_A = [[-122.4, 37.7], [-122.4, 37.8], [-122.3, 37.8], [-122.3, 37.7], [-122.4, 37.7]]
_SQUARE_OVERLAP = [[-122.35, 37.75], [-122.35, 37.85], [-122.25, 37.85], [-122.25, 37.75], [-122.35, 37.75]]
_SQUARE_FAR = [[-100.0, 30.0], [-100.0, 30.1], [-99.9, 30.1], [-99.9, 30.0], [-100.0, 30.0]]


def _times():
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    end = start + timedelta(hours=2)
    return start, end


def _intended_flight(coords, usage_state="Planned"):
    start, end = _times()
    return {
        "intended_flight": {
            "basic_information": {"usage_state": usage_state, "uas_state": "Nominal"},
            "operational_intent": {
                "volumes": [
                    {
                        "volume": {
                            "outline_polygon": {"vertices": [{"lng": c[0], "lat": c[1]} for c in coords]},
                            "altitude_lower": {"value": 50},
                            "altitude_upper": {"value": 120},
                        },
                        "time_start": {"value": start.isoformat()},
                        "time_end": {"value": end.isoformat()},
                    }
                ],
                "off_nominal_volumes": [],
                "priority": 0,
            },
        },
        "usage_state": usage_state,
        "uas_state": "Nominal",
    }


def _geojson(coords):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"min_altitude": {"meters": 50}, "max_altitude": {"meters": 120}},
                "geometry": {"type": "Polygon", "coordinates": [coords]},
            }
        ],
    }


async def _seed_declaration(db, coords):
    start, end = _times()
    decl = FlightDeclaration(
        operational_intent="{}",
        flight_declaration_raw_geojson=json.dumps(_geojson(coords)),
        bounds=json.dumps(
            {
                "minx": min(c[0] for c in coords),
                "miny": min(c[1] for c in coords),
                "maxx": max(c[0] for c in coords),
                "maxy": max(c[1] for c in coords),
            }
        ),
        aircraft_id="peer",
        type_of_operation=0,
        state=1,  # Accepted / active
        originating_party="peer",
        start_datetime=start,
        end_datetime=end,
    )
    db.add(decl)
    await db.commit()


class TestStrategicResult:
    async def test_not_planned_when_not_planned_usage_state(self, client):
        plan_id = str(uuid.uuid4())
        resp = await client.put(f"{BASE}/flight_planning/flight_plans/{plan_id}", json=_intended_flight(_SQUARE_A, usage_state="Closed"))
        assert resp.status_code == 200
        assert resp.json()["planning_result"] == "NotPlanned"

    async def test_planned_when_no_existing(self, client):
        plan_id = str(uuid.uuid4())
        resp = await client.put(f"{BASE}/flight_planning/flight_plans/{plan_id}", json=_intended_flight(_SQUARE_A))
        assert resp.status_code == 200
        assert resp.json()["planning_result"] == "Planned"

    async def test_planned_when_disjoint(self, client, db):
        await _seed_declaration(db, _SQUARE_FAR)
        plan_id = str(uuid.uuid4())
        resp = await client.put(f"{BASE}/flight_planning/flight_plans/{plan_id}", json=_intended_flight(_SQUARE_A))
        assert resp.status_code == 200
        assert resp.json()["planning_result"] == "Planned"

    async def test_conflict_when_overlapping(self, client, db):
        await _seed_declaration(db, _SQUARE_OVERLAP)
        plan_id = str(uuid.uuid4())
        resp = await client.put(f"{BASE}/flight_planning/flight_plans/{plan_id}", json=_intended_flight(_SQUARE_A))
        assert resp.status_code == 200
        assert resp.json()["planning_result"] == "ConflictWithFlight"

    async def test_uspace_variant_conflict(self, client, db):
        await _seed_declaration(db, _SQUARE_OVERLAP)
        plan_id = str(uuid.uuid4())
        resp = await client.put(f"{BASE}/flight_planning/u_space/flight_plans/{plan_id}", json=_intended_flight(_SQUARE_A))
        assert resp.status_code == 200
        assert resp.json()["planning_result"] == "ConflictWithFlight"
