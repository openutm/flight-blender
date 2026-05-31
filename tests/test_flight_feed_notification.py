"""Tests for notification/AMQP dispatch on flight_feed air-traffic ingestion.

The FastAPI migration ships a reusable, ``AMQP_URL``-gated
``send_operational_update_message`` Celery task (mirroring the Django AMQP
notification machinery in ``notification_operations``).  The air-traffic
ingestion endpoints should enqueue that task so downstream consumers learn that
new observations were ingested.  These tests assert the ingestion endpoints
enqueue the notification and that the task is a safe no-op when ``AMQP_URL`` is
unset (no broker connection attempted).
"""

import uuid
from unittest import mock

import pytest

OBS_PAYLOAD = {
    "observations": [
        {
            "lat_dd": 1.0,
            "lon_dd": 2.0,
            "altitude_mm": 100.0,
            "traffic_source": 1,
            "source_type": 0,
            "icao_address": "abc123",
        }
    ]
}


@pytest.mark.anyio
async def test_set_air_traffic_enqueues_notification(client):
    session_id = str(uuid.uuid4())
    with mock.patch("flight_blender.routers.flight_feed.send_operational_update_message") as mock_notify:
        resp = await client.post(f"/flight_stream/set_air_traffic/{session_id}", json=OBS_PAYLOAD)

    assert resp.status_code == 200
    mock_notify.delay.assert_called_once()
    # The session id must be threaded through so the message is operation-scoped.
    passed = list(mock_notify.delay.call_args.args) + list(mock_notify.delay.call_args.kwargs.values())
    assert session_id in passed


@pytest.mark.anyio
async def test_bulk_set_air_traffic_enqueues_notification(client):
    session_id = str(uuid.uuid4())
    with mock.patch("flight_blender.routers.flight_feed.send_operational_update_message") as mock_notify:
        resp = await client.post(f"/flight_stream/bulk_set_air_traffic/{session_id}", json=OBS_PAYLOAD)

    assert resp.status_code == 200
    mock_notify.delay.assert_called_once()


def test_notification_task_is_noop_without_amqp_url(monkeypatch):
    """The notification task must short-circuit (return None, open no connection)
    when ``AMQP_URL`` is not configured."""
    import flight_blender.tasks.flight_declaration as fd

    monkeypatch.delenv("AMQP_URL", raising=False)

    # If a broker connection were attempted, this stand-in pika would record it.
    fake_pika = mock.MagicMock()
    with mock.patch.dict("sys.modules", {"pika": fake_pika}):
        # ``.apply`` runs the task eagerly with a properly bound ``self``.
        outcome = fd.send_operational_update_message.apply(
            args=("op-1", "1 air traffic observation(s) ingested", "info"),
        )

    assert outcome.successful()
    assert outcome.result is None
    fake_pika.BlockingConnection.assert_not_called()
