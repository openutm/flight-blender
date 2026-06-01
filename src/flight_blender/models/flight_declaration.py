"""
SQLAlchemy models for flight declaration operations.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flight_blender.database import Base


class FlightDeclaration(Base):
    __tablename__ = "flight_declaration"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    operational_intent: Mapped[str] = mapped_column(Text, nullable=False)
    flight_declaration_raw_geojson: Mapped[str | None] = mapped_column(Text, nullable=True)
    type_of_operation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    bounds: Mapped[str] = mapped_column(String(140), nullable=False)
    aircraft_id: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    originating_party: Mapped[str] = mapped_column(String(100), default="Flight Blender Default", nullable=False)
    submitted_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    latest_telemetry_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    operational_intent_detail: Mapped["FlightOperationalIntentDetail | None"] = relationship(
        "FlightOperationalIntentDetail", back_populates="declaration", uselist=False, cascade="all, delete-orphan"
    )
    operational_intent_reference: Mapped["FlightOperationalIntentReference | None"] = relationship(
        "FlightOperationalIntentReference", back_populates="declaration", uselist=False, cascade="all, delete-orphan"
    )
    state_history: Mapped[list["FlightOperationTracking"]] = relationship(
        "FlightOperationTracking", back_populates="flight_declaration", cascade="all, delete-orphan", order_by="FlightOperationTracking.created_at"
    )

    def __str__(self) -> str:
        return f"{self.originating_party} {self.id}"


class FlightOperationalIntentDetail(Base):
    __tablename__ = "flight_operational_intent_detail"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    declaration_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("flight_declaration.id", ondelete="CASCADE"), unique=True, nullable=False)
    volumes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    off_nominal_volumes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    subscribers: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    declaration: Mapped["FlightDeclaration"] = relationship("FlightDeclaration", back_populates="operational_intent_detail")


class FlightOperationalIntentReference(Base):
    __tablename__ = "flight_operational_intent_reference"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    declaration_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("flight_declaration.id", ondelete="CASCADE"), unique=True, nullable=False)
    uss_availability: Mapped[str] = mapped_column(String(256), nullable=False)
    ovn: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manager: Mapped[str] = mapped_column(String(256), nullable=False)
    uss_base_url: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    time_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    time_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    subscription_id: Mapped[str] = mapped_column(String(256), nullable=False)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    declaration: Mapped["FlightDeclaration"] = relationship("FlightDeclaration", back_populates="operational_intent_reference")


class FlightOperationTracking(Base):
    __tablename__ = "flight_operation_tracking"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    flight_declaration_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("flight_declaration.id", ondelete="CASCADE"), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    deltas: Mapped[str] = mapped_column(Text, default="{}", nullable=False)  # JSON string
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    flight_declaration: Mapped["FlightDeclaration"] = relationship("FlightDeclaration", back_populates="state_history")


class Subscriber(Base):
    __tablename__ = "flight_subscriber"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    operational_intent_reference_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("flight_operational_intent_reference.id", ondelete="CASCADE"), nullable=False
    )
    subscriptions: Mapped[str] = mapped_column(Text, default="[]", nullable=False)  # JSON
    uss_base_url: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
