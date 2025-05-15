import enum
from dataclasses import dataclass
from typing import Literal

from implicitdict import ImplicitDict
from marshmallow import Schema, fields


class GeoJSONFeatureProperties(Schema):
    name = fields.Str(required=True)
    upper_limit = fields.Int(required=True)
    lower_limit = fields.Int(required=True)
    start_time = fields.Str(required=False)
    end_time = fields.Str(required=False)


class GeoJSONFeatureGeometry(Schema):
    type = fields.Str(required=True)
    coordinates = fields.List(fields.List(fields.List(fields.Float()), required=True))


class GeoJSONFeature(Schema):
    type = fields.Str(required=True)
    properties = fields.Nested(GeoJSONFeatureProperties)
    geometry = fields.Nested(GeoJSONFeatureGeometry)


class GeoFencePutSchema(Schema):
    type = fields.Str(required=True)
    features = fields.List(fields.Nested(lambda: GeoJSONFeature()), required=True)


class GeoAwarenessStatusResponseEnum(str, enum.Enum):
    """A enum to specify if the USS is ready (or not)"""

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


class HTTPSSource(ImplicitDict):
    url: str
    format: str


class GeoZoneHttpsSource(ImplicitDict):
    https_source: HTTPSSource


class GeoAwarenessRestrictions(str, enum.Enum):
    """A enum to specify the result of processing of a GeoZone"""

    PROHIBITED = "PROHIBITED"
    REQ_AUTHORISATION = "REQ_AUTHORISATION"
    CONDITIONAL = "CONDITIONAL"
    NO_RESTRICTION = "NO_RESTRICTION"


class GeozoneCheckResultEnum(str, enum.Enum):
    """A enum to specify the result of processing of a GeoZone"""

    Present = "Present"
    Absent = "Absent"
    UnsupportedFilter = "UnsupportedFilter"
    Error = "Error"


class GeoAwarenessImportResponseEnum(str, enum.Enum):
    """A enum to specify the result of processing of a GeoZone"""

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


class ZoneAuthority(ImplicitDict):
    name: str
    service: str
    email: str
    contactName: str
    siteURL: str
    phone: str
    purpose: str
    intervalBefore: str


class HorizontalProjection(ImplicitDict):
    type: str
    coordinates: list[list]


class ED269Geometry(ImplicitDict):
    uomDimensions: str
    lowerLimit: int
    lowerVerticalReference: str
    upperLimit: float
    upperVerticalReference: str
    horizontalProjection: HorizontalProjection


class GeoZoneFeature(ImplicitDict):
    identifier: str
    country: str
    name: str
    type: str
    restriction: str
    restrictionConditions: str
    region: int
    reason: list[str]
    otherReasonInfo: str
    regulationExemption: str
    uSpaceClass: str
    message: str
    applicability: list[dict[str, str]]
    zoneAuthority: list[ZoneAuthority]
    geometry: list[ED269Geometry]


@dataclass
class ParseValidateResponse:
    all_zones: list[bool]
    feature_list: None | list[GeoZoneFeature]


class GeoZone(ImplicitDict):
    title: str
    description: str
    features: list[GeoZoneFeature]

@dataclass
class GeofencePayload:
    id: str
    upper_limit: str
    lower_limit: str
    altitude_ref: str
    name: str
    bounds: str
    status: str
    message: str
    is_test_dataset: bool
    start_datetime: str
    end_datetime: str
    raw_geo_fence: dict
    geozone : dict


class GeoZoneFilterPosition(ImplicitDict):
    uomDimensions: str
    verticalReferenceType: str
    height: int
    longitude: float
    latitude: float


class ED269Filter(ImplicitDict):
    uSpaceClass: str
    acceptableRestrictions: Literal[
        GeoAwarenessRestrictions.PROHIBITED,
        GeoAwarenessRestrictions.REQ_AUTHORISATION,
        GeoAwarenessRestrictions.CONDITIONAL,
        GeoAwarenessRestrictions.NO_RESTRICTION,
    ]


class ResultingOperationalImpactEnum(str, enum.Enum):
    """A enum to specify the result of processing of a GeoZone"""

    Block = "Block"
    Advise = "Advise"
    BlockOrAdvise = "BlockOrAdvise"


class GeoZoneFilterSet(ImplicitDict):
    resulting_operational_impact: str
    position: GeoZoneFilterPosition | None
    after: str | None
    before: str | None
    operation_rule_set: str | None
    restriction_source: str | None
    ed269: ED269Filter | None


class GeozonesCheck(ImplicitDict):
    filter_sets: list[GeoZoneFilterSet]


class GeoZoneCheckRequestBody(ImplicitDict):
    checks: list[GeozonesCheck]


@dataclass
class GeoZoneCheckResult:
    geozone: Literal[
        GeozoneCheckResultEnum.Present,
        GeozoneCheckResultEnum.Present,
        GeozoneCheckResultEnum.UnsupportedFilter,
        GeozoneCheckResultEnum.Error,
    ]


@dataclass
class GeoZoneChecksResponse:
    applicableGeozone: list[GeoZoneCheckResult]
    message: str | None


@dataclass
class GeoFenceMetadata:
    start_date: str
    end_date: str
    geo_fence_id: str
