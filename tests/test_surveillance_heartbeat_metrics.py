"""
Unit tests for the pure SDSP heartbeat metrics computation and the
``GET /surveillance_monitoring_ops/get_air_traffic`` endpoint.

These cover the two behavioural regressions restored from the Django original:

* ``compute_sdsp_heartbeat`` derives latency / accuracy / SLA booleans from the
  live observation stream instead of returning hard-coded "healthy" constants.
* ``get_air_traffic`` re-exposes the latest observations (read from Redis) for
  display, matching the Django ``get_air_traffic`` GET view.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from flight_blender.tasks.surveillance import compute_sdsp_heartbeat

BASE = "/surveillance_monitoring_ops"


def _obs(seconds_ago: float, hacc: float = 3.0, vacc: float = 3.0) -> dict:
    """Build one observation dict whose ``timestamp`` is ``seconds_ago`` in the past."""
    now = datetime.now(tz=timezone.utc)
    ts = now - timedelta(seconds=seconds_ago)
    return {
        "timestamp": ts.isoformat(),
        "horizontal_accuracy_m": str(hacc),
        "vertical_accuracy_m": str(vacc),
    }


# ── compute_sdsp_heartbeat (pure) ───────────────────────────────────────────────


def test_compute_sdsp_heartbeat_empty_stream_is_degraded():
    """No observations => SLA booleans False and sentinel latency/accuracy."""
    result = compute_sdsp_heartbeat([])
    assert result["meets_sla_surveillance_requirements"] is False
    assert result["meets_sla_rr_lr_requirements"] is False
    # No data: latency/accuracy must not falsely report a healthy value.
    assert result["average_latency_or_95_percentile_latency_ms"] is None
    assert result["horizontal_or_vertical_95_percentile_accuracy_m"] is None
    assert "surveillance_sdsp_name" in result
    assert "timestamp" in result


def test_compute_sdsp_heartbeat_healthy_stream_within_sla():
    """Fresh, accurate observations => both SLA booleans True, low latency."""
    now = datetime.now(tz=timezone.utc)
    observations = [_obs(0.05, hacc=2.0, vacc=2.0), _obs(0.10, hacc=3.0, vacc=3.0)]
    result = compute_sdsp_heartbeat(observations, now=now)
    assert result["meets_sla_surveillance_requirements"] is True
    assert result["meets_sla_rr_lr_requirements"] is True
    assert result["average_latency_or_95_percentile_latency_ms"] is not None
    assert result["average_latency_or_95_percentile_latency_ms"] < 1000
    assert result["horizontal_or_vertical_95_percentile_accuracy_m"] is not None
    assert result["horizontal_or_vertical_95_percentile_accuracy_m"] <= 10.0


def test_compute_sdsp_heartbeat_stale_stream_breaks_sla():
    """Old observations (high latency) => surveillance SLA False."""
    now = datetime.now(tz=timezone.utc)
    # 60 s old observations: far beyond the latency threshold.
    observations = [_obs(60.0), _obs(65.0)]
    result = compute_sdsp_heartbeat(observations, now=now)
    assert result["meets_sla_surveillance_requirements"] is False
    assert result["average_latency_or_95_percentile_latency_ms"] >= 1000


def test_compute_sdsp_heartbeat_poor_accuracy_breaks_sla():
    """Fresh but inaccurate observations => surveillance SLA False."""
    now = datetime.now(tz=timezone.utc)
    observations = [_obs(0.1, hacc=50.0, vacc=50.0)]
    result = compute_sdsp_heartbeat(observations, now=now)
    assert result["meets_sla_surveillance_requirements"] is False
    assert result["horizontal_or_vertical_95_percentile_accuracy_m"] >= 10.0


# ── GET get_air_traffic ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_air_traffic_empty(client):
    """Patched Redis read returns [] => empty observations list, 200."""
    with patch("flight_blender.routers.surveillance.read_all_observations", return_value=[]):
        response = await client.get(f"{BASE}/get_air_traffic")
    assert response.status_code == 200
    body = response.json()
    assert body["observations"] == []


@pytest.mark.anyio
async def test_get_air_traffic_with_observations(client):
    """Seeded observations are mapped into the response payload."""
    sample = [
        {"icao_address": "ABC123", "lat_dd": "51.5", "lon_dd": "-0.1", "timestamp": "2026-05-31T00:00:00+00:00"},
        {"icao_address": "DEF456", "lat_dd": "52.0", "lon_dd": "-1.0", "timestamp": "2026-05-31T00:00:01+00:00"},
    ]
    with patch("flight_blender.routers.surveillance.read_all_observations", return_value=sample):
        response = await client.get(f"{BASE}/get_air_traffic")
    assert response.status_code == 200
    body = response.json()
    assert len(body["observations"]) == 2
    assert body["observations"][0]["icao_address"] == "ABC123"
