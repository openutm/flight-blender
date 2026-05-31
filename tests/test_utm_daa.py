"""
Integration tests: DAA (Detect and Avoid) operations.

Covers:
- Active alerts listing
- Incident log listing with and without filters
"""

import pytest

BASE = "/detect_and_avoid_ops"


# ── Active alerts ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_active_daa_alerts_empty(client):
    response = await client.get(f"{BASE}/alerts/active/")
    assert response.status_code == 200
    assert response.json() == []


# ── Incident logs ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_daa_incident_logs_empty(client):
    response = await client.get(f"{BASE}/logs/incident/")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
async def test_get_daa_incident_logs_with_event_type_filter(client):
    response = await client.get(f"{BASE}/logs/incident/", params={"event_type": "TCAS_RA"})
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.anyio
async def test_get_daa_incident_logs_with_alert_level_filter(client):
    response = await client.get(f"{BASE}/logs/incident/", params={"alert_level": 2})
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.anyio
async def test_get_daa_incident_logs_with_date_filters(client):
    response = await client.get(
        f"{BASE}/logs/incident/",
        params={"start_date": "2024-01-01T00:00:00", "end_date": "2024-12-31T23:59:59"},
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.anyio
async def test_get_daa_incident_logs_invalid_date_rejected(client):
    # A malformed date filter must be rejected with 422, not silently ignored
    # (the original port swallowed the ValueError and returned unfiltered results).
    response = await client.get(
        f"{BASE}/logs/incident/",
        params={"start_date": "not-a-date"},
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_get_daa_incident_logs_all_filters(client):
    response = await client.get(
        f"{BASE}/logs/incident/",
        params={
            "event_type": "TCAS_RA",
            "alert_level": 1,
            "start_date": "2024-01-01T00:00:00",
            "end_date": "2024-12-31T23:59:59",
        },
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.anyio
async def test_incident_logs_malformed_start_date_returns_422(client):
    """A malformed start_date must be rejected, not silently ignored."""
    response = await client.get(f"{BASE}/logs/incident/", params={"start_date": "not-a-date"})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_incident_logs_malformed_end_date_returns_422(client):
    response = await client.get(f"{BASE}/logs/incident/", params={"end_date": "13/13/2025"})
    assert response.status_code == 422
