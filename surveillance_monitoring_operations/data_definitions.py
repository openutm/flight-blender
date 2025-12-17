from dataclasses import dataclass
from enum import Enum


class SurveillanceStatus(str, Enum):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    OUTAGE = "outage"


class SpeedAccuracy(str, Enum):
    SAUnknown = "SAUnknown"
    SA10mpsPlus = "SA10mpsPlus"
    SA10mps = "SA10mps"
    SA3mps = "SA3mps"
    SA1mps = "SA1mps"
    SA03mps = "SA03mps"


@dataclass
class SurveillanceServiceStatus:
    status: SurveillanceStatus


@dataclass
class LatLangAltPoint:
    lat: float
    lng: float
    alt: float


@dataclass
class AircraftPosition:
    lat: float
    lng: float
    alt: float
    accuracy_h: str
    accuracy_v: str
    extrapolated: bool | None
    pressure_altitude: float | None


@dataclass
class AircraftState:
    position: AircraftPosition
    speed_accuracy: SpeedAccuracy
    speed: float | None = 255
    track: float | None = 361
    vertical_speed: float | None = 63


@dataclass
class TrackMessage:
    sdsdp_identifier: str
    unique_aircraft_identifier: str
    state: AircraftState
    timestamp: str
    source: str
    track_state: str


@dataclass
class HeartbeatCode:
    service_degraded: int
    service_outage: int
    upcoming_degradation: int
    sensor_operational: int


@dataclass
class HealthMessage:
    sdsp_identifier: str
    current_status: SurveillanceStatus
    machine_readable_file_of_estimated_coverage: str
    scheduled_degradations: str
    timestamp: str


@dataclass
class HeartbeatMessage:
    surveillance_sdsp_name: str
    meets_sla_surveillance_requirements: bool
    meets_sla_rr_lr_requirements: bool
    average_latency_or_95_percentile_latency_ms: int
    horizontal_or_vertical_95_percentile_accuracy_m: int
    timestamp: str


@dataclass
class SurveillanceSensorDetail:
    id: str
    sensor_type_display: str
    sensor_identifier: str
    created_at: str
    updated_at: str


@dataclass
class FlightPoint:
    """This object holds basic information about a point on the flight track, it has latitude, longitude and altitude in WGS 1984 datum"""

    lat: float  # Degrees of latitude north of the equator, with reference to the WGS84 ellipsoid. For more information see: https://github.com/uastech/standards/blob/master/remoteid/canonical.yaml#L1160
    lng: float  # Degrees of longitude east of the Prime Meridian, with reference to the WGS84 ellipsoid. For more information see: https://github.com/uastech/standards/blob/master/remoteid/canonical.yaml#L1170
    alt: float  # meters in WGS 84, normally calculated as height of ground level in WGS84 and altitude above ground level
    speed: float  # speed in m / s
    bearing: float  # forward azimuth for the this and the next point on the track


@dataclass
class ActiveTrack:
    session_id: str
    unique_aircraft_identifier: str
    last_updated_timestamp: str
    observations: list[dict]
