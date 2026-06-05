from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel

FLIGHT_OBSERVATION_TRAFFIC_SOURCE = (
    (0, "1090ES"),
    (1, "UAT"),
    (2, "Multi-radar (MRT)"),
    (3, "MLAT"),
    (4, "SSR"),
    (5, "PSR"),
    (6, "Mode-S"),
    (7, "MRT"),
    (8, "SSR + PSR Fused"),
    (9, "ADS-B"),
    (10, "FLARM"),
    (11, "Network Remote-ID"),
    (12, "Other"),
    (13, "Broadcast Remote-ID"),
    (14, "ADS-L"),
    (15, "Drone sensed by another means"),
)

SURVEILLANCE_SENSOR_HEALTH_CHOICES = [
    ("operational", "Operational"),
    ("degraded", "Degraded"),
    ("outage", "Outage"),
]

SURVEILLANCE_SENSOR_MAINTENANCE_CHOICES = [
    ("planned", "Planned"),
    ("unplanned", "Unplanned"),
]


class SurveillanceStatus(str, Enum):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    OUTAGE = "outage"


@dataclass
class HealthMessage:
    sdsp_identifier: str
    current_status: SurveillanceStatus
    machine_readable_file_of_estimated_coverage: str
    scheduled_degradations: str
    timestamp: str


@dataclass
class SurveillanceSensorDetail:
    id: str
    sensor_type_display: str
    sensor_identifier: str
    created_at: str
    updated_at: str


@dataclass
class HeartbeatRateMetric:
    measured_rate_hz: float
    target_rate_hz: float
    session_id: str
    window_start: str
    window_end: str
    total_heartbeats_in_window: int


@dataclass
class HeartbeatDeliveryProbability:
    probability: float
    delivered_on_time: int
    total_expected: int
    session_id: str
    window_start: str
    window_end: str


@dataclass
class TrackUpdateProbability:
    probability: float
    ticks_with_active_tracks: int
    total_ticks: int
    session_id: str
    window_start: str
    window_end: str


@dataclass
class SensorHealthMetrics:
    sensor_id: str
    sensor_identifier: str
    mttr_seconds: float | None
    auto_recovery_time_seconds: float | None
    mtbf_with_auto_recovery_seconds: float | None
    mtbf_without_auto_recovery_seconds: float | None
    failure_count: int
    auto_recovery_count: int
    manual_recovery_count: int
    window_start: str
    window_end: str


@dataclass
class AggregateHealthMetrics:
    avg_mttr_seconds: float | None
    avg_auto_recovery_time_seconds: float | None
    avg_mtbf_with_auto_recovery_seconds: float | None
    avg_mtbf_without_auto_recovery_seconds: float | None
    total_sensors: int
    window_start: str
    window_end: str


@dataclass
class SurveillanceSensorFailureNotificationDetail:
    id: str
    sensor_id: str
    sensor_identifier: str
    previous_status: str
    new_status: str
    recovery_type: str | None
    message: str
    created_at: str


@dataclass
class SurveillanceMetrics:
    heartbeat_rates: list[HeartbeatRateMetric]
    heartbeat_delivery_probabilities: list[HeartbeatDeliveryProbability]
    track_update_probabilities: list[TrackUpdateProbability]
    per_sensor_health: list[SensorHealthMetrics]
    aggregate_health: AggregateHealthMetrics | None
    active_sessions: int
    window_start: str
    window_end: str


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
class HeartbeatMessage:
    surveillance_sdsp_name: str
    meets_sla_surveillance_requirements: bool
    meets_sla_rr_lr_requirements: bool
    average_latency_or_95_percentile_latency_ms: int
    horizontal_or_vertical_95_percentile_accuracy_m: int
    timestamp: str


@dataclass
class FlightPoint:
    lat: float
    lng: float
    alt: float
    speed: float
    bearing: float


@dataclass
class ActiveTrack:
    session_id: str
    unique_aircraft_identifier: str
    last_updated_timestamp: str
    observations: list[dict]


class SurveillanceSessionAction(BaseModel):
    action: Literal["start", "stop"]


class SensorHealthUpdate(BaseModel):
    status: str
    recovery_type: str | None = None
