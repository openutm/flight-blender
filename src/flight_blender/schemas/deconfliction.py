"""
Pydantic schemas for peer-USS / DSS operational-intent exchange.

These are the minimal, parsing-oriented models used by the peer-USS client
(``services/peer_uss_client.py``) to represent an op-intent *reference*
(uss_base_url + id, as returned by a DSS area query) and the *details*
(volumes) fetched from a peer USS — the inputs to strategic deconfliction.
They intentionally mirror the relevant subset of the Django
``scd_operations/scd_data_definitions.py`` dataclasses.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LatLng(BaseModel):
    """A WGS84 latitude/longitude vertex."""

    lat: float
    lng: float


class Volume4D(BaseModel):
    """A parsed 4D volume from a peer op-intent (outline + altitude band + time)."""

    outline_polygon: list[LatLng] = []
    altitude_lower: float | None = None
    altitude_upper: float | None = None
    time_start: float | None = None
    time_end: float | None = None


class OperationalIntentReference(BaseModel):
    """A DSS op-intent reference: enough to fetch the peer's details."""

    id: str
    uss_base_url: str | None = None
    ovn: str | None = None
    state: str | None = None
    manager: str | None = None
    uss_availability: str | None = None


class PeerOperationalIntentDetails(BaseModel):
    """Op-intent details fetched from a peer USS (the volumes feed deconfliction)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    volumes: list[Volume4D] = []
    reference: OperationalIntentReference | None = None
