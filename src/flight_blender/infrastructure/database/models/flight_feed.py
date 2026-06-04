import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from flight_blender.infrastructure.database.session import Base


class FlightObservationORM(Base):
    __tablename__ = "flight_feed_operations_flightobservation"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    latitude_dd: Mapped[float] = mapped_column(Float, nullable=False)
    longitude_dd: Mapped[float] = mapped_column(Float, nullable=False)
    altitude_mm: Mapped[float] = mapped_column(Float, nullable=False)
    traffic_source: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[int] = mapped_column(Integer, nullable=False)
    icao_address: Mapped[str] = mapped_column(Text, nullable=False)
    raw_metadata: Mapped[str] = mapped_column("metadata", Text, nullable=False)
    sensor_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class SignedTelmetryPublicKeyORM(Base):
    # Note: table name uses Django's typo "Telmetry" not "Telemetry"
    __tablename__ = "flight_feed_operations_signedtelmetrypublickey"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key_id: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
