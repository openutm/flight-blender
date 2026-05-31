"""Tests for the peer-USS / DSS op-intent client (HTTP layer mocked).

These tests exercise request construction (URL, auth header, body) and response
parsing for the peer-USS op-intent details GET, the peer notification POST, and
the OVN/key/extents builder used for DSS op-intent reference writes.

The raw network round-trip is never performed: the module-level ``requests``
import is patched and ``_auth_header`` is stubbed, so only the testable client
logic runs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from flight_blender.schemas.deconfliction import (
    OperationalIntentReference,
    PeerOperationalIntentDetails,
)
from flight_blender.services import peer_uss_client


@pytest.fixture(autouse=True)
def _stub_auth(monkeypatch):
    """Avoid real DSS token retrieval; return a deterministic auth header."""
    monkeypatch.setattr(
        peer_uss_client,
        "_auth_header",
        lambda *a, **k: {"Authorization": "Bearer test-token", "Content-Type": "application/json"},
    )


# ── P1: peer op-intent details GET ───────────────────────────────────────────
@pytest.mark.anyio
async def test_get_peer_details_builds_request_and_parses_volumes():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "operational_intent": {
            "details": {
                "volumes": [
                    {
                        "volume": {
                            "outline_polygon": {
                                "vertices": [
                                    {"lat": 1.0, "lng": 2.0},
                                    {"lat": 3.0, "lng": 4.0},
                                ]
                            },
                            "altitude_lower": {"value": 10.0},
                            "altitude_upper": {"value": 120.0},
                        },
                        "time_start": {"value": "2026-01-01T00:00:00Z"},
                        "time_end": {"value": "2026-01-01T01:00:00Z"},
                    }
                ]
            }
        }
    }
    captured = {}

    def fake_get(url, headers, timeout):  # noqa: ANN001, ANN202
        captured["url"] = url
        captured["headers"] = headers
        return fake_response

    reference = OperationalIntentReference(id="op-123", uss_base_url="https://peer.example.com")
    with patch.object(peer_uss_client, "requests") as mock_requests:
        mock_requests.get.side_effect = fake_get
        mock_requests.RequestException = Exception
        result = peer_uss_client.get_peer_operational_intent_details(reference)

    assert isinstance(result, PeerOperationalIntentDetails)
    assert captured["url"] == "https://peer.example.com/uss/v1/operational_intents/op-123"
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert len(result.volumes) == 1
    volume = result.volumes[0]
    assert volume.altitude_lower == 10.0
    assert volume.altitude_upper == 120.0
    assert len(volume.outline_polygon) == 2
    assert volume.outline_polygon[0].lat == 1.0
    assert result.reference is reference


@pytest.mark.anyio
async def test_get_peer_details_non_200_returns_empty():
    fake_response = MagicMock()
    fake_response.status_code = 404

    reference = OperationalIntentReference(id="missing", uss_base_url="https://peer.example.com")
    with patch.object(peer_uss_client, "requests") as mock_requests:
        mock_requests.get.return_value = fake_response
        mock_requests.RequestException = Exception
        result = peer_uss_client.get_peer_operational_intent_details(reference)

    assert isinstance(result, PeerOperationalIntentDetails)
    assert result.volumes == []


@pytest.mark.anyio
async def test_get_peer_details_network_error_returns_empty():
    reference = OperationalIntentReference(id="op-err", uss_base_url="https://peer.example.com")
    with patch.object(peer_uss_client, "requests") as mock_requests:
        mock_requests.RequestException = Exception
        mock_requests.get.side_effect = Exception("boom")
        result = peer_uss_client.get_peer_operational_intent_details(reference)

    assert isinstance(result, PeerOperationalIntentDetails)
    assert result.volumes == []


@pytest.mark.anyio
async def test_get_peer_details_missing_base_url_returns_empty():
    reference = OperationalIntentReference(id="op-1", uss_base_url=None)
    result = peer_uss_client.get_peer_operational_intent_details(reference)
    assert isinstance(result, PeerOperationalIntentDetails)
    assert result.volumes == []


@pytest.mark.anyio
async def test_get_peer_details_malformed_body_returns_empty():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"unexpected": "shape"}

    reference = OperationalIntentReference(id="op-2", uss_base_url="https://peer.example.com")
    with patch.object(peer_uss_client, "requests") as mock_requests:
        mock_requests.get.return_value = fake_response
        mock_requests.RequestException = Exception
        result = peer_uss_client.get_peer_operational_intent_details(reference)

    assert result.volumes == []
    assert result.reference is reference


# ── P2: OVN / key / extents builder ──────────────────────────────────────────
def test_collect_ovns_from_references():
    references = [
        {"id": "a", "ovn": "ovn-a"},
        {"id": "b", "ovn": "ovn-b"},
        {"id": "c"},  # no OVN — skipped
        {"id": "d", "ovn": None},  # null OVN — skipped
    ]
    assert peer_uss_client.collect_ovns(references) == ["ovn-a", "ovn-b"]


def test_build_op_intent_reference_payload_has_key_and_extents():
    volumes = [{"volume": {"foo": "bar"}}]
    references = [{"id": "a", "ovn": "ovn-a"}, {"id": "b", "ovn": "ovn-b"}]
    payload = peer_uss_client.build_operational_intent_reference_payload(
        volumes=volumes,
        state="Accepted",
        existing_references=references,
        uss_base_url="https://blender.example.com",
    )
    assert payload["extents"] == volumes
    assert payload["key"] == ["ovn-a", "ovn-b"]
    assert payload["state"] == "Accepted"
    assert payload["uss_base_url"] == "https://blender.example.com"
    assert payload["new_subscription"]["uss_base_url"] == "https://blender.example.com"
    assert payload["new_subscription"]["notify_for_constraints"] is False


def test_build_op_intent_reference_payload_empty_references_gives_empty_key():
    volumes = [{"volume": {"foo": "bar"}}]
    payload = peer_uss_client.build_operational_intent_reference_payload(
        volumes=volumes,
        state="Accepted",
        existing_references=[],
        uss_base_url="https://blender.example.com",
    )
    assert payload["key"] == []
    assert payload["extents"] == volumes


def test_build_op_intent_reference_payload_extents_never_empty_when_volumes_present():
    volumes = [{"volume": {"a": 1}}, {"volume": {"b": 2}}]
    payload = peer_uss_client.build_operational_intent_reference_payload(
        volumes=volumes,
        state="Activated",
        existing_references=[{"id": "x", "ovn": "ovn-x"}],
        uss_base_url="https://blender.example.com",
    )
    assert payload["extents"]  # non-empty
    assert payload["extents"] == volumes


# ── P3: peer notification POST builder + gate ────────────────────────────────
def test_build_peer_notification_payload_shape():
    payload = peer_uss_client.build_peer_notification_payload(
        operational_intent_id="op-9",
        operational_intent_details={"volumes": [{"v": 1}]},
        ovn="ovn-9",
        blender_base_url="https://blender.example.com",
        subscriptions=[{"subscription_id": "s1"}],
    )
    assert payload["operational_intent_id"] == "op-9"
    reference = payload["operational_intent"]["reference"]
    assert reference["id"] == "op-9"
    assert reference["ovn"] == "ovn-9"
    assert reference["uss_base_url"] == "https://blender.example.com"
    assert payload["operational_intent"]["details"] == {"volumes": [{"v": 1}]}
    assert payload["subscriptions"] == [{"subscription_id": "s1"}]


@pytest.mark.anyio
async def test_notify_peer_uss_builds_request_and_returns_true_on_204(monkeypatch):
    monkeypatch.setattr(peer_uss_client.settings, "ussp_network_enabled", True)
    fake_response = MagicMock()
    fake_response.status_code = 204
    captured = {}

    def fake_post(url, json, headers, timeout):  # noqa: ANN001, ANN202
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return fake_response

    payload = {"operational_intent_id": "op-1", "subscriptions": []}
    with patch.object(peer_uss_client, "requests") as mock_requests:
        mock_requests.post.side_effect = fake_post
        mock_requests.RequestException = Exception
        result = peer_uss_client.notify_peer_uss("https://peer.example.com", payload)

    assert result is True
    assert captured["url"] == "https://peer.example.com/uss/v1/operational_intents"
    assert captured["json"] == payload
    assert captured["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.anyio
async def test_notify_peer_uss_non_204_returns_false(monkeypatch):
    monkeypatch.setattr(peer_uss_client.settings, "ussp_network_enabled", True)
    fake_response = MagicMock()
    fake_response.status_code = 400

    with patch.object(peer_uss_client, "requests") as mock_requests:
        mock_requests.post.return_value = fake_response
        mock_requests.RequestException = Exception
        result = peer_uss_client.notify_peer_uss("https://peer.example.com", {"subscriptions": []})

    assert result is False


@pytest.mark.anyio
async def test_notify_peer_uss_gated_when_network_disabled(monkeypatch):
    monkeypatch.setattr(peer_uss_client.settings, "ussp_network_enabled", False)
    with patch.object(peer_uss_client, "requests") as mock_requests:
        mock_requests.RequestException = Exception
        result = peer_uss_client.notify_peer_uss("https://peer.example.com", {"subscriptions": []})

    assert result is False
    mock_requests.post.assert_not_called()


@pytest.mark.anyio
async def test_notify_peer_uss_network_error_returns_false(monkeypatch):
    monkeypatch.setattr(peer_uss_client.settings, "ussp_network_enabled", True)
    with patch.object(peer_uss_client, "requests") as mock_requests:
        mock_requests.RequestException = Exception
        mock_requests.post.side_effect = Exception("boom")
        result = peer_uss_client.notify_peer_uss("https://peer.example.com", {"subscriptions": []})

    assert result is False
