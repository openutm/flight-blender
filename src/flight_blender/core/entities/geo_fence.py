import enum
from dataclasses import dataclass
from typing import Literal

from implicitdict import ImplicitDict

GEOFENCE_INDEX_BASEPATH = "/tmp/blender_geofence_idx"  # nosec B108


class GeoAwarenessStatusResponseEnum(str, enum.Enum):
    Starting = "Starting"
    Ready = "Ready"


@dataclass
class GeoSpatialMapTestHarnessStatus:
    status: Literal[
        GeoAwarenessStatusResponseEnum.Starting,
        GeoAwarenessStatusResponseEnum.Ready,
    ]
    api_version: str | None
    api_name: str | None = "Geospatial Map Provider Automated Testing Interface"


class GeoAwarenessImportResponseEnum(str, enum.Enum):
    Activating = "Activating"
    Ready = "Ready"
    Deactivating = "Deactivating"
    Unsupported = "Unsupported"
    Rejected = "Rejected"
    Error = "Error"


@dataclass
class GeoAwarenessTestStatus:
    result: Literal[
        GeoAwarenessImportResponseEnum.Activating,
        GeoAwarenessImportResponseEnum.Ready,
        GeoAwarenessImportResponseEnum.Deactivating,
        GeoAwarenessImportResponseEnum.Unsupported,
        GeoAwarenessImportResponseEnum.Rejected,
        GeoAwarenessImportResponseEnum.Error,
    ]
    message: str | None


class GeozoneCheckResultEnum(str, enum.Enum):
    Present = "Present"
    Absent = "Absent"
    UnsupportedFilter = "UnsupportedFilter"
    Error = "Error"


class GeoZoneFilterPosition(ImplicitDict):
    uomDimensions: str
    verticalReferenceType: str
    height: int
    longitude: float
    latitude: float


class GeoZoneFilterSet(ImplicitDict):
    resulting_operational_impact: str
    position: GeoZoneFilterPosition | None
    after: str | None
    before: str | None
    operation_rule_set: str | None
    restriction_source: str | None
    ed269: dict | None


class GeozonesCheck(ImplicitDict):
    filter_sets: list[GeoZoneFilterSet]


class GeoZoneCheckRequestBody(ImplicitDict):
    checks: list[GeozonesCheck]


@dataclass
class GeoZoneCheckResult:
    geozone: Literal[
        GeozoneCheckResultEnum.Present,
        GeozoneCheckResultEnum.Absent,
        GeozoneCheckResultEnum.UnsupportedFilter,
        GeozoneCheckResultEnum.Error,
    ]


@dataclass
class GeoZoneChecksResponse:
    applicableGeozone: list[GeoZoneCheckResult]
    message: str | None
