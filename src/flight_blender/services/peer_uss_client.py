"""
Peer-USS / DSS operational-intent client (migrated from Django
scd_operations/dss_scd_helper.py).

This module implements the *testable* request-construction and response-parsing
logic for three peer-USS / DSS interactions used by strategic deconfliction and
op-intent submission:

* :func:`get_peer_operational_intent_details` -- authenticated GET to a peer
  USS's ``/uss/v1/operational_intents/{id}`` endpoint, parsing the returned
  operational-intent details into :class:`PeerOperationalIntentDetails`
  (volumes) that feed strategic deconfliction.
* :func:`collect_ovns` / :func:`build_operational_intent_reference_payload` --
  pure logic that collects the OVNs of overlapping op-intent references returned
  by the DSS area query to build the ``key`` list and constructs non-empty
  ``extents`` from the operation's volumes for DSS op-intent reference writes.
* :func:`build_peer_notification_payload` / :func:`notify_peer_uss` -- the
  ``POST /uss/v1/operational_intents`` notification request to inform a
  subscriber USS of a created/updated op-intent.

The HTTP layer is the module-level ``requests`` import (mirroring
``constraints_client``), which tests patch. The raw network send is gated behind
``settings.ussp_network_enabled`` for the notification path; the GET details
call is only exercised behind the patched/gated submission flow. No live DSS I/O
is performed in the test-suite.
"""

from __future__ import annotations

from typing import Any

import requests
from loguru import logger

from flight_blender.auth.dss import get_dss_auth_header
from flight_blender.config import get_settings
from flight_blender.schemas.deconfliction import (
    LatLng,
    OperationalIntentReference,
    PeerOperationalIntentDetails,
    Volume4D,
)

settings = get_settings()

_REQUEST_TIMEOUT = 30


def _auth_header(token_type: str = "scd") -> dict[str, str]:
    """Build the Authorization header using the DSS token helper."""
    return get_dss_auth_header(audience=settings.dss_auth_audience, token_type=token_type)


# ── P1: peer op-intent details GET ───────────────────────────────────────────
def _coerce_float(value: Any) -> float | None:
    """Return *value* as a float, or ``None`` when it is not numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _parse_volume(raw_volume: dict) -> Volume4D:
    """Parse one ASTM op-intent ``volume`` entry into a :class:`Volume4D`."""
    volume = raw_volume.get("volume", {}) if isinstance(raw_volume, dict) else {}

    vertices = []
    outline = volume.get("outline_polygon")
    if isinstance(outline, dict):
        for vertex in outline.get("vertices", []) or []:
            if isinstance(vertex, dict) and "lat" in vertex and "lng" in vertex:
                vertices.append(LatLng(lat=vertex["lat"], lng=vertex["lng"]))

    def _altitude(key: str) -> float | None:
        node = volume.get(key)
        if isinstance(node, dict):
            return _coerce_float(node.get("value"))
        return _coerce_float(node)

    def _time(key: str) -> float | None:
        node = raw_volume.get(key)
        if isinstance(node, dict):
            return _coerce_float(node.get("value"))
        return _coerce_float(node)

    return Volume4D(
        outline_polygon=vertices,
        altitude_lower=_altitude("altitude_lower"),
        altitude_upper=_altitude("altitude_upper"),
        time_start=_time("time_start"),
        time_end=_time("time_end"),
    )


def _parse_operational_intent_details(body: dict, reference: OperationalIntentReference) -> PeerOperationalIntentDetails:
    """Parse a peer op-intent details response body into volumes."""
    if not isinstance(body, dict):
        return PeerOperationalIntentDetails(volumes=[], reference=reference)
    operational_intent = body.get("operational_intent", {})
    details = operational_intent.get("details", {}) if isinstance(operational_intent, dict) else {}
    raw_volumes = details.get("volumes", []) if isinstance(details, dict) else []
    volumes = [_parse_volume(raw) for raw in raw_volumes if isinstance(raw, dict)]
    return PeerOperationalIntentDetails(volumes=volumes, reference=reference)


def get_peer_operational_intent_details(reference: OperationalIntentReference) -> PeerOperationalIntentDetails:
    """Fetch op-intent details from a peer USS and parse them into volumes.

    Builds the authenticated GET to
    ``{uss_base_url}/uss/v1/operational_intents/{id}`` and parses the response.
    Returns an empty :class:`PeerOperationalIntentDetails` (no volumes) on a
    missing base URL, a non-200 response, a network error, or a malformed body
    -- mirroring the Django ``get_and_set_dss_operational_intent_details``
    behaviour of returning ``{}`` rather than raising.
    """
    if not reference.uss_base_url:
        logger.error("Peer op-intent details requested without a uss_base_url")
        return PeerOperationalIntentDetails(volumes=[], reference=reference)

    url = f"{reference.uss_base_url}/uss/v1/operational_intents/{reference.id}"
    headers = _auth_header()
    try:
        response = requests.get(url, headers=headers, timeout=_REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.error("Peer op-intent details request failed: %s", exc)
        return PeerOperationalIntentDetails(volumes=[], reference=reference)

    if response.status_code != 200:
        logger.error("Peer op-intent details returned %s for %s", response.status_code, url)
        return PeerOperationalIntentDetails(volumes=[], reference=reference)

    try:
        body = response.json()
    except ValueError as exc:
        logger.error("Peer op-intent details body was not JSON: %s", exc)
        return PeerOperationalIntentDetails(volumes=[], reference=reference)

    return _parse_operational_intent_details(body, reference)


# ── P2: OVN / key / extents builder for DSS writes ───────────────────────────
def collect_ovns(existing_references: list[dict]) -> list[str]:
    """Collect the OVNs of overlapping op-intent references.

    Mirrors the Django ``[ref["ovn"] for ref in operational_intent_references]``
    used to populate the ``key`` of a DSS op-intent reference write. References
    without a (non-null) ``ovn`` are skipped.
    """
    ovns: list[str] = []
    for reference in existing_references or []:
        if not isinstance(reference, dict):
            continue
        ovn = reference.get("ovn")
        if ovn:
            ovns.append(ovn)
    return ovns


def build_operational_intent_reference_payload(
    volumes: list[dict],
    state: str,
    existing_references: list[dict],
    uss_base_url: str,
) -> dict:
    """Build the DSS op-intent reference create/update payload.

    Populates a non-empty ``extents`` from the operation's *volumes* and the
    ``key`` from the OVNs of overlapping *existing_references* (the area-query
    result), addressing the empty ``key``/``extents`` regression.
    """
    return {
        "extents": volumes,
        "key": collect_ovns(existing_references),
        "state": state,
        "uss_base_url": uss_base_url,
        "new_subscription": {
            "uss_base_url": uss_base_url,
            "notify_for_constraints": False,
        },
    }


# ── P3: peer notification POST builder + gated send ──────────────────────────
def build_peer_notification_payload(
    operational_intent_id: str,
    operational_intent_details: dict,
    ovn: str | None,
    blender_base_url: str,
    subscriptions: list[dict],
) -> dict:
    """Build the body for a ``POST /uss/v1/operational_intents`` notification.

    Mirrors Django ``notify_peer_uss_of_created_or_updated_opint``: the
    reference advertises Blender's own ``uss_base_url`` and the new OVN.
    """
    return {
        "operational_intent_id": operational_intent_id,
        "operational_intent": {
            "reference": {
                "id": operational_intent_id,
                "ovn": ovn,
                "uss_base_url": blender_base_url,
            },
            "details": operational_intent_details,
        },
        "subscriptions": subscriptions,
    }


def notify_peer_uss(uss_base_url: str, notification_payload: dict) -> bool:
    """POST a notification to a subscriber USS about a created/updated op-intent.

    Builds the authenticated POST to ``{uss_base_url}/uss/v1/operational_intents``.
    The raw send is gated behind ``settings.ussp_network_enabled``; returns
    ``True`` only on a 204 response (per the ASTM contract), ``False``
    otherwise (gate off, non-204, or network error).
    """
    if not settings.ussp_network_enabled:
        logger.info("USSP network disabled; skipping peer USS notification to %s", uss_base_url)
        return False

    url = f"{uss_base_url}/uss/v1/operational_intents"
    headers = _auth_header()
    try:
        response = requests.post(url, json=notification_payload, headers=headers, timeout=_REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.error("Error notifying peer USS %s: %s", uss_base_url, exc)
        return False

    if response.status_code == 204:
        logger.info("Successfully notified peer USS %s", uss_base_url)
        return True
    logger.error("Peer USS notification to %s returned %s", uss_base_url, response.status_code)
    return False
