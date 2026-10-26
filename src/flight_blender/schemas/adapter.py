from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1, max_length=512, pattern=r"\S")]


class Pilot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(max_length=128)
    name: Text
    license: Text
    contact: Text


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    systemID: int = Field(ge=1, le=255)
    declarationID: UUID
    vehicleID: Text
    operatorID: Text
    pilot: Pilot
    aerobridgeOperationID: UUID | Literal[""]


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    assignment: Assignment
    startedAt: AwareDatetime
    endedAt: AwareDatetime | None
    blenderURL: Text
    aerobridgeURL: str = Field(max_length=2048)
    revision: int = Field(ge=1)

    @model_validator(mode="after")
    def end_after_start(self):
        if self.endedAt and self.endedAt < self.startedAt:
            raise ValueError("session end precedes start")
        return self


class Vehicle(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    systemID: int = Field(ge=1, le=255)
    lastSeen: datetime
    heartbeatAt: datetime
    positionAt: datetime
    gpsAt: AwareDatetime
    frames: int = Field(ge=0)
    armed: bool
    latitude: float | None = Field(ge=-90, le=90)
    longitude: float | None = Field(ge=-180, le=180)
    altitudeMSL: float | None
    altitudeWGS84: float | None
    relativeAltitude: float | None
    speed: float | None = Field(ge=0)
    heading: float | None = Field(ge=0, lt=360)
    fix: int = Field(ge=0, le=255)
    satellites: int = Field(ge=0, le=255)


class Sample(BaseModel):
    sequence: int = Field(ge=1)
    vehicle: Vehicle


class SessionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: Session
    samples: list[Sample] = Field(max_length=200)

    @model_validator(mode="after")
    def consistent_samples(self):
        sequences = [sample.sequence for sample in self.samples]
        if sequences != sorted(set(sequences)):
            raise ValueError("sample sequences must be unique and ordered")
        for sample in self.samples:
            if sample.vehicle.systemID != self.session.assignment.systemID:
                raise ValueError("sample system does not match session")
            if sample.vehicle.gpsAt < self.session.startedAt:
                raise ValueError("sample precedes session")
            if self.session.endedAt and sample.vehicle.gpsAt > self.session.endedAt:
                raise ValueError("sample follows session")
        return self


class ArchivePilot(BaseModel):
    """Provisioned metadata; absent licence/contact are explicit, never fabricated."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(max_length=128)
    name: Text
    license: Text | None = None
    contact: Text | None = None


class ArchiveAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    declarationID: UUID
    vehicleID: Text
    operatorID: Text
    pilot: ArchivePilot


class ArchiveSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schemaVersion: Literal[2] = 2
    id: UUID
    source: Text
    sourceData: dict = Field(default_factory=dict)
    assignment: ArchiveAssignment
    startedAt: AwareDatetime
    endedAt: AwareDatetime | None
    revision: int = Field(ge=1)
    # Zero-based, contiguous sequences [0, finalSampleCount). Null while recording.
    finalSampleCount: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_end(self):
        if self.endedAt is not None and self.endedAt < self.startedAt:
            raise ValueError("session end precedes start")
        if (self.endedAt is None) != (self.finalSampleCount is None):
            raise ValueError("end time and final sample count must be supplied together")
        return self


class ArchivePosition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    relativeAltitude: float | None = None
    # Immutable per sample: the reference and uncertainty used for this conversion.
    altitudeWGS84: float | None = None
    altitudeSource: Text | None = None
    altitudeSigma: float | None = Field(default=None, ge=0)
    velocityNorth: float | None = None
    velocityEast: float | None = None
    velocityDown: float | None = None
    heading: float | None = Field(default=None, ge=0, lt=360)
    flying: bool | None = None
    motorsOn: bool | None = None
    satellites: int | None = Field(default=None, ge=0, le=255)
    speed: float | None = Field(default=None, ge=0)


class ArchiveSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(ge=0)
    recordedAt: AwareDatetime
    position: ArchivePosition
    # Source-specific evidence, not MAVLink GPS fix codes.
    sourceData: dict = Field(default_factory=dict)


class ArchiveBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: ArchiveSession
    samples: list[ArchiveSample] = Field(max_length=200)

    @model_validator(mode="after")
    def consistent_samples(self):
        sequences = [sample.sequence for sample in self.samples]
        if sequences != sorted(set(sequences)):
            raise ValueError("sample sequences must be unique and ordered")
        for sample in self.samples:
            if sample.recordedAt < self.session.startedAt:
                raise ValueError("sample precedes session")
            if self.session.endedAt is not None and sample.recordedAt > self.session.endedAt:
                raise ValueError("sample follows session")
            if self.session.finalSampleCount is not None and sample.sequence >= self.session.finalSampleCount:
                raise ValueError("sample exceeds final sample count")
        return self


class ArchiveStatus(BaseModel):
    receivedSampleCount: int = Field(ge=0)
    finalSampleCount: int | None
    complete: bool


class SessionReceipt(BaseModel):
    id: UUID
    revision: int = Field(ge=1)


class ArchiveReceipt(SessionReceipt):
    archive: ArchiveStatus


class ArchivePage(ArchiveBatch):
    archive: ArchiveStatus
