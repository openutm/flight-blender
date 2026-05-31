"""
Integration tests: constraint, conformance, weather, USS, and UTM adapter operations.

Groups:
- Constraint detail / reference 404 lookups
- Conformance summary, status, records
- Weather endpoint with mocked service
- USS reports, operational intents, constraints, flights
- UTM adapter ping, capabilities, declarations, state management
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

FUTURE_START = (datetime.now(tz=timezone.utc) + timedelta(hours=2)).isoformat()
FUTURE_END = (datetime.now(tz=timezone.utc) + timedelta(hours=4)).isoformat()

DECL_PAYLOAD = {
    "operational_intent": '{"volumes": []}',
    "bounds": "-1.0,51.0,1.0,52.0",
    "aircraft_id": "TEST-001",
    "type_of_operation": 1,
    "start_datetime": FUTURE_START,
    "end_datetime": FUTURE_END,
}


# ══════════════════════════════════════════════════════════════════════════════
# Constraint router
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_get_constraint_detail_not_found(client):
    response = await client.get(f"/constraint_ops/constraint_detail/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_constraint_reference_not_found(client):
    response = await client.get(f"/constraint_ops/constraint_reference/{uuid.uuid4()}")
    assert response.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# Conformance router
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_conformance_summary_empty(client):
    response = await client.get("/conformance_monitoring_ops/conformance_record_summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total_records"] == 0
    assert body["conforming_records"] == 0
    assert body["conformance_rate_percent"] == 100.0


@pytest.mark.anyio
async def test_conformance_status_empty(client):
    response = await client.get("/conformance_monitoring_ops/conformance_status")
    assert response.status_code == 200
    body = response.json()
    assert body["is_conforming"] is True
    assert body["active_nonconforming_count"] == 0


@pytest.mark.anyio
async def test_get_conformance_records_empty(client):
    response = await client.get("/conformance_monitoring_ops/get_conformance_records")
    assert response.status_code == 200
    assert response.json() == []


# ══════════════════════════════════════════════════════════════════════════════
# Weather router
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_get_weather_returns_200(client):
    response = await client.get("/weather_monitoring_ops/weather/", params={"latitude": 51.5, "longitude": -0.1})
    assert response.status_code == 200
    body = response.json()
    # Django parity: latitude/longitude come from the upstream response, the full
    # WeatherSerializer shape is returned, and there is no synthetic current_weather.
    assert body["latitude"] == 51.5
    assert body["longitude"] == -0.1
    assert "hourly" in body
    assert "current_weather" not in body


@pytest.mark.anyio
async def test_get_weather_out_of_range_latitude_is_forwarded(client):
    # Django did presence-only validation (no range bounds); out-of-range
    # coordinates are forwarded upstream rather than rejected with 422.
    response = await client.get("/weather_monitoring_ops/weather/", params={"latitude": 999.0, "longitude": -0.1})
    assert response.status_code == 200


@pytest.mark.anyio
async def test_get_weather_out_of_range_longitude_is_forwarded(client):
    response = await client.get("/weather_monitoring_ops/weather/", params={"latitude": 51.5, "longitude": 999.0})
    assert response.status_code == 200


@pytest.mark.anyio
async def test_get_weather_missing_params(client):
    response = await client.get("/weather_monitoring_ops/weather/")
    assert response.status_code == 400
    assert response.json() == {"error": "Longitude parameter is required"}


# ══════════════════════════════════════════════════════════════════════════════
# USS router
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_submit_uss_report(client):
    response = await client.post(
        "/uss_ops/v1/reports",
        json={"report": {"report_type": "test", "details": "some details"}},
    )
    assert response.status_code == 201
    assert response.json()["message"] == "Report received"


@pytest.mark.anyio
async def test_get_operational_intent_not_found(client):
    response = await client.get(f"/uss_ops/v1/operational_intents/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_operational_intent_existing(client):
    # Create a flight declaration first
    create_resp = await client.post("/flight_declaration_ops/flight_declaration", json=DECL_PAYLOAD)
    assert create_resp.status_code == 201
    decl_id = create_resp.json()["id"]

    response = await client.get(f"/uss_ops/v1/operational_intents/{decl_id}")
    assert response.status_code == 200
    body = response.json()
    assert str(body["operational_intent_id"]) == decl_id


@pytest.mark.anyio
async def test_update_operational_intent_not_found(client):
    response = await client.put(
        f"/uss_ops/v1/operational_intents/{uuid.uuid4()}",
        json={"operational_intent": {"state": 2}, "subscriptions": []},
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_submit_telemetry_no_content(client):
    intent_id = str(uuid.uuid4())
    response = await client.post(
        f"/uss_ops/v1/operational_intents/{intent_id}/telemetry",
        json={"operational_intent_id": str(uuid.uuid4()), "telemetry": {"position": {"lat": 51.5, "lng": -0.1}}},
    )
    assert response.status_code == 204


@pytest.mark.anyio
async def test_notify_operational_intent_change(client):
    response = await client.post(
        "/uss_ops/v1/operational_intents",
        json={"operational_intent": {"state": 1}, "subscriptions": []},
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_get_constraint_uss_not_found(client):
    response = await client.get(f"/uss_ops/v1/constraints/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_update_constraint_uss(client):
    response = await client.put(f"/uss_ops/v1/constraints/{uuid.uuid4()}")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_notify_constraint_change(client):
    response = await client.post("/uss_ops/v1/constraints", json={"constraint_id": str(uuid.uuid4())})
    assert response.status_code == 200


@pytest.mark.anyio
async def test_get_all_flights_uss(client):
    response = await client.get("/uss_ops/flights")
    assert response.status_code == 200
    assert "flights" in response.json()


@pytest.mark.anyio
async def test_get_flight_details_uss(client):
    """Unknown RID flight details return 404 (Django ``get_uss_flight_details``)."""
    flight_id = str(uuid.uuid4())
    response = await client.get(f"/uss_ops/flights/{flight_id}/details")
    assert response.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# UTM Adapter router
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_utm_adapter_ping(client):
    response = await client.get("/utm_adapter/ping")
    assert response.status_code == 200
    assert response.json()["message"] == "pong"


@pytest.mark.anyio
async def test_network_rid_capabilities(client):
    response = await client.get("/utm_adapter/network_remote_id/capabilities")
    assert response.status_code == 200
    assert "capabilities" in response.json()


@pytest.mark.anyio
async def test_utm_adapter_set_telemetry(client):
    response = await client.post(
        "/utm_adapter/network_remote_id/set_telemetry",
        json={"lat_dd": 51.5, "lon_dd": -0.1, "altitude_mm": 100},
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_network_rid_flight_details(client):
    flight_id = "TEST-FLIGHT-001"
    response = await client.get(f"/utm_adapter/network_remote_id/uss/flights/{flight_id}/details")
    assert response.status_code == 200
    assert response.json()["id"] == flight_id


@pytest.mark.anyio
async def test_network_rid_flights_empty(client):
    response = await client.get("/utm_adapter/network_remote_id/uss/flights")
    assert response.status_code == 200
    assert "flights" in response.json()


@pytest.mark.anyio
async def test_utm_list_flight_declarations_empty(client):
    response = await client.get("/utm_adapter/flight_declaration")
    assert response.status_code == 200
    assert response.json()["count"] == 0


@pytest.mark.anyio
async def test_utm_create_flight_declaration(client):
    response = await client.post("/utm_adapter/flight_declaration", json=DECL_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert "id" in body
    assert body["aircraft_id"] == "TEST-001"


@pytest.mark.anyio
async def test_utm_flight_declaration_capabilities(client):
    response = await client.get("/utm_adapter/flight_declaration/capabilities")
    assert response.status_code == 200
    assert "capabilities" in response.json()


@pytest.mark.anyio
async def test_utm_update_declaration_state(client):
    create_resp = await client.post("/utm_adapter/flight_declaration", json=DECL_PAYLOAD)
    decl_id = create_resp.json()["id"]
    response = await client.put(f"/utm_adapter/flight_declaration_state/{decl_id}", json={"state": 1})
    assert response.status_code == 200
    assert response.json()["state"] == 1


@pytest.mark.anyio
async def test_utm_update_declaration_state_not_found(client):
    response = await client.put(f"/utm_adapter/flight_declaration_state/{uuid.uuid4()}", json={"state": 1})
    assert response.status_code == 404


@pytest.mark.anyio
async def test_traffic_information_discovery(client):
    response = await client.get("/utm_adapter/traffic_information")
    assert response.status_code == 200
    body = response.json()
    assert "message" in body
    assert "url" in body
