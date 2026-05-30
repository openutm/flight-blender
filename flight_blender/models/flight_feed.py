"""
SQLAlchemy models for flight feed operations.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from flight_blender.database import Base


class SignedTelemetryPublicKey(Base):
    __tablename__ = "flight_feed_signed_telemetry_public_key"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key_id: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __str__(self) -> str:
        return f"Key: {self.url}"


class FlightObservation(Base):
    __tablename__ = "flight_feed_observation"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    latitude_dd: Mapped[float] = mapped_column(Float, nullable=False)
    longitude_dd: Mapped[float] = mapped_column(Float, nullable=False)
    altitude_mm: Mapped[float] = mapped_column(Float, nullable=False)
    traffic_source: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[int] = mapped_column(Integer, nullable=False)
    icao_address: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[str] = mapped_column("metadata", Text, nullable=False, default="")
    sensor_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
