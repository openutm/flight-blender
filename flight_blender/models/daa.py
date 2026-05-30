"""SQLAlchemy models for Detect and Avoid (DAA) operations."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from flight_blender.database import Base


class DAAAlert(Base):
    __tablename__ = "daa_alert"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ownship_declaration_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    intruder_icao_address: Mapped[str] = mapped_column(String(256), nullable=False)
    alert_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    geometry: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    initial_cpa_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    closest_range_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DAAIncidentLog(Base):
    __tablename__ = "daa_incident_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    alert_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    ownship_declaration_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    intruder_icao_address: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, default="alert_update")
    alert_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    geometry: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    range_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    bearing_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpa_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    altitude_diff_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
