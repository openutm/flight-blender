"""
SQLAlchemy models for surveillance monitoring operations.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flight_blender.database import Base


def _thirty_minutes_from_now() -> datetime:
    return datetime.now(tz=timezone.utc) + timedelta(minutes=30)


class SurveillanceSession(Base):
    __tablename__ = "surveillance_session"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_thirty_minutes_from_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __str__(self) -> str:
        return str(self.id)


class SurveillanceSensor(Base):
    __tablename__ = "surveillance_sensor"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sensor_type: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    sensor_identifier: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    refresh_rate_seconds: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    horizontal_accuracy_m: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    vertical_accuracy_m: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    expected_latency_ms: Mapped[int] = mapped_column(Integer, default=150, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    health: Mapped["SurveillanceSensorHealth | None"] = relationship("SurveillanceSensorHealth", back_populates="sensor", uselist=False)
    health_tracking: Mapped[list["SurveillanceSensorHealthTracking"]] = relationship("SurveillanceSensorHealthTracking", back_populates="sensor")
    maintenance: Mapped["SurveillanceSensorMaintenance | None"] = relationship(
        "SurveillanceSensorMaintenance", back_populates="sensor", uselist=False
    )

    def __str__(self) -> str:
        return f"{self.sensor_type} - {self.sensor_identifier}"


class SurveillanceSensorHealth(Base):
    __tablename__ = "surveillance_sensor_health"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sensor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surveillance_sensor.id", ondelete="CASCADE"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    sensor: Mapped["SurveillanceSensor"] = relationship("SurveillanceSensor", back_populates="health")


class SurveillanceSensorHealthTracking(Base):
    __tablename__ = "surveillance_sensor_health_tracking"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sensor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surveillance_sensor.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    recovery_type: Mapped[str | None] = mapped_column(String(12), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sensor: Mapped["SurveillanceSensor"] = relationship("SurveillanceSensor", back_populates="health_tracking")


class SurveillanceSensorMaintenance(Base):
    __tablename__ = "surveillance_sensor_maintenance"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sensor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surveillance_sensor.id", ondelete="CASCADE"), unique=True, nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_or_unplanned: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    sensor: Mapped["SurveillanceSensor"] = relationship("SurveillanceSensor", back_populates="maintenance")


class SurveillanceHeartbeatEvent(Base):
    __tablename__ = "surveillance_heartbeat_event"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dispatched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_on_time: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SurveillanceTrackEvent(Base):
    __tablename__ = "surveillance_track_event"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dispatched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    had_active_tracks: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SurveillanceSensorFailureNotification(Base):
    __tablename__ = "surveillance_sensor_failure_notification"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sensor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surveillance_sensor.id", ondelete="CASCADE"), nullable=False)
    previous_status: Mapped[str] = mapped_column(String(12), nullable=False)
    new_status: Mapped[str] = mapped_column(String(12), nullable=False)
    recovery_type: Mapped[str | None] = mapped_column(String(12), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
