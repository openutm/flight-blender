import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flight_blender.infrastructure.database.session import Base


class SurveillanceSessionORM(Base):
    __tablename__ = "surveillance_monitoring_operations_surveillancesession"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    heartbeat_events: Mapped[list["SurveillanceHeartbeatEventORM"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    track_events: Mapped[list["SurveillanceTrackEventORM"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class SurveillanceSensorORM(Base):
    __tablename__ = "surveillance_monitoring_operations_surveillancesensor"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sensor_type: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    sensor_identifier: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    refresh_rate_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    horizontal_accuracy_m: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    vertical_accuracy_m: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    expected_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=150)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    health_record: Mapped["SurveillanceSensorHealthORM | None"] = relationship(back_populates="sensor", uselist=False, cascade="all, delete-orphan")
    health_tracking_records: Mapped[list["SurveillanceSensorHealthTrackingORM"]] = relationship(back_populates="sensor", cascade="all, delete-orphan")
    failure_notifications: Mapped[list["SurveillanceSensorFailureNotificationORM"]] = relationship(
        back_populates="sensor", cascade="all, delete-orphan"
    )


class SurveillanceSensorHealthORM(Base):
    __tablename__ = "surveillance_monitoring_operations_surveillancesensorhealth"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sensor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surveillance_monitoring_operations_surveillancesensor.id"), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    sensor: Mapped["SurveillanceSensorORM"] = relationship(back_populates="health_record")


class SurveillanceSensorHealthTrackingORM(Base):
    # Django model name has typo: SurveillanceSensortHealthTracking (Sensort)
    __tablename__ = "surveillance_monitoring_operations_surveillancesensorthealte007"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sensor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surveillance_monitoring_operations_surveillancesensor.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    recovery_type: Mapped[str | None] = mapped_column(String(12), nullable=True)

    sensor: Mapped["SurveillanceSensorORM"] = relationship(back_populates="health_tracking_records")


class SurveillanceSensorMaintenanceORM(Base):
    __tablename__ = "surveillance_monitoring_operations_surveillancesensormainte43b7"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sensor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surveillance_monitoring_operations_surveillancesensor.id"), nullable=False, unique=True)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_or_unplanned: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class SurveillanceHeartbeatEventORM(Base):
    __tablename__ = "surveillance_monitoring_operations_surveillanceheartbeatevent"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surveillance_monitoring_operations_surveillancesession.id"), nullable=False)
    dispatched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    expected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_on_time: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    session: Mapped["SurveillanceSessionORM"] = relationship(back_populates="heartbeat_events")


class SurveillanceTrackEventORM(Base):
    __tablename__ = "surveillance_monitoring_operations_surveillancetrackevent"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surveillance_monitoring_operations_surveillancesession.id"), nullable=False)
    dispatched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    expected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    had_active_tracks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    session: Mapped["SurveillanceSessionORM"] = relationship(back_populates="track_events")


class SurveillanceSensorFailureNotificationORM(Base):
    __tablename__ = "surveillance_monitoring_operations_surveillancesensorfailur2a6d"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sensor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surveillance_monitoring_operations_surveillancesensor.id"), nullable=False)
    previous_status: Mapped[str] = mapped_column(String(12), nullable=False)
    new_status: Mapped[str] = mapped_column(String(12), nullable=False)
    recovery_type: Mapped[str | None] = mapped_column(String(12), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    sensor: Mapped["SurveillanceSensorORM"] = relationship(back_populates="failure_notifications")
