from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


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


@dataclass
class HeartbeatRateMetric:
    """Metric 1: Measured heartbeat delivery rate."""

    measured_rate_hz: float
    target_rate_hz: float
    session_id: str
    window_start: str
    window_end: str
    total_heartbeats_in_window: int


@dataclass
class HeartbeatDeliveryProbability:
    """Metric 2a: Probability that heartbeats are delivered on time."""

    probability: float
    delivered_on_time: int
    total_expected: int
    session_id: str
    window_start: str
    window_end: str


@dataclass
class TrackUpdateProbability:
    """Metric 2b: Probability that track task ticks produce active track data."""

    probability: float
    ticks_with_active_tracks: int
    total_ticks: int
    session_id: str
    window_start: str
    window_end: str


@dataclass
class SensorHealthMetrics:
    """Metrics 3-6: Per-sensor MTTR, automatic recovery time, and MTBF values."""

    sensor_id: str
    sensor_identifier: str
    mttr_seconds: Optional[float]
    auto_recovery_time_seconds: Optional[float]
    mtbf_with_auto_recovery_seconds: Optional[float]
    mtbf_without_auto_recovery_seconds: Optional[float]
    failure_count: int
    auto_recovery_count: int
    manual_recovery_count: int
    window_start: str
    window_end: str


@dataclass
class AggregateHealthMetrics:
    """Service-level aggregate of health metrics across all sensors."""

    avg_mttr_seconds: Optional[float]
    avg_auto_recovery_time_seconds: Optional[float]
    avg_mtbf_with_auto_recovery_seconds: Optional[float]
    avg_mtbf_without_auto_recovery_seconds: Optional[float]
    total_sensors: int
    window_start: str
    window_end: str


@dataclass
class SurveillanceSensorFailureNotificationDetail:
    """Metric 7: Serialized failure notification for API responses."""

    id: str
    sensor_id: str
    sensor_identifier: str
    previous_status: str
    new_status: str
    recovery_type: Optional[str]
    message: str
    created_at: str


@dataclass
class SurveillanceMetrics:
    """Combined response for the service_metrics endpoint covering all seven SDSP metrics."""

    heartbeat_rates: list[HeartbeatRateMetric]
    heartbeat_delivery_probabilities: list[HeartbeatDeliveryProbability]
    track_update_probabilities: list[TrackUpdateProbability]
    per_sensor_health: list[SensorHealthMetrics]
    aggregate_health: Optional[AggregateHealthMetrics]
    active_sessions: int
    window_start: str
    window_end: str
