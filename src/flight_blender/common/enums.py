"""
Shared enums and constants (migrated from common/data_definitions.py).
"""

from enum import IntEnum


class AltitudeRef(IntEnum):
    WGS84 = 0
    AGL = 1
    MSL = 2
    W84 = 4


class ConformanceState(IntEnum):
    NONCONFORMING = 0
    CONFORMING = 1


class OperationState(IntEnum):
    NOT_SUBMITTED = 0
    ACCEPTED = 1
    ACTIVATED = 2
    NONCONFORMING = 3
    CONTINGENT = 4
    ENDED = 5
    WITHDRAWN = 6
    CANCELLED = 7
    REJECTED = 8


class OperationType(IntEnum):
    VLOS = 1
    BVLOS = 2
    CREWED = 3


class FlightObservationTrafficSource(IntEnum):
    ADSB_UNVALIDATED = 0
    SECONDARY_SURVEILLANCE_RADAR = 1
    PRIMARY_SURVEILLANCE_RADAR = 3
    TRANSPONDER_ADS_B = 5
    MLATURATION = 6
    RADAR_TRACK = 7
    ADS_B_VALIDATED = 8
    FLIGHT_PLAN = 9
    DRONE_SENSOR_GPS = 10
    NETWORK_REMOTE_ID = 11
    OTHER = 12
    BROADCAST_REMOTE_ID = 13
    DRONE_SENSED = 15


class SurveillanceSensorHealth(str):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    OUTAGE = "outage"


class SurveillanceSensorMaintenance(str):
    PLANNED = "planned"
    UNPLANNED = "unplanned"


# Scope constants
FLIGHTBLENDER_READ_SCOPE = "blender.read"
FLIGHTBLENDER_WRITE_SCOPE = "blender.write"
RESPONSE_CONTENT_TYPE = "application/json"
