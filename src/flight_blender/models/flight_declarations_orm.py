import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from flight_blender.db.session import Base


class FlightDeclarationORM(Base):
    __tablename__ = "flight_declaration_operations_flightdeclaration"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operational_intent: Mapped[str] = mapped_column(Text, nullable=False)
    flight_declaration_raw_geojson: Mapped[str | None] = mapped_column(Text, nullable=True)
    type_of_operation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    bounds: Mapped[str] = mapped_column(String(140), nullable=False)
    aircraft_id: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    originating_party: Mapped[str] = mapped_column(String(100), nullable=False, default="Flight Blender Default")
    submitted_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    latest_telemetry_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class FlightOperationalIntentDetailORM(Base):
    __tablename__ = "flight_declaration_operations_flightoperationalintentdetail"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    declaration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flight_declaration_operations_flightdeclaration.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    volumes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    off_nominal_volumes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    subscribers: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_live: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class FlightOperationalIntentReferenceORM(Base):
    __tablename__ = "flight_declaration_operations_flightoperationalintentreference"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    declaration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flight_declaration_operations_flightdeclaration.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    uss_availability: Mapped[str] = mapped_column(String(256), nullable=False)
    ovn: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manager: Mapped[str] = mapped_column(String(256), nullable=False)
    uss_base_url: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    time_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    time_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    subscription_id: Mapped[str] = mapped_column(String(256), nullable=False)
    is_live: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class SubscriberORM(Base):
    __tablename__ = "flight_declaration_operations_subscriber"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operational_intent_reference_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flight_declaration_operations_flightoperationalintentreference.id", ondelete="CASCADE"),
        nullable=False,
    )
    subscriptions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    uss_base_url: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class CompositeOperationalIntentORM(Base):
    __tablename__ = "flight_declaration_operations_compositeoperationalintent"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    declaration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flight_declaration_operations_flightdeclaration.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    bounds: Mapped[str] = mapped_column(String(140), nullable=False)
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    alt_max: Mapped[float] = mapped_column(Float, nullable=False)
    alt_min: Mapped[float] = mapped_column(Float, nullable=False)
    operational_intent_details_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flight_declaration_operations_flightoperationalintentdetail.id", ondelete="CASCADE"),
        nullable=False,
    )
    operational_intent_reference_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flight_declaration_operations_flightoperationalintentreference.id", ondelete="CASCADE"),
        nullable=False,
    )


class PeerOperationalIntentDetailORM(Base):
    __tablename__ = "flight_declaration_operations_peeroperationalintentdetail"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    volumes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    off_nominal_volumes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    subscribers: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_live: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class PeerOperationalIntentReferenceORM(Base):
    __tablename__ = "flight_declaration_operations_peeroperationalintentreference"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    uss_availability: Mapped[str] = mapped_column(String(256), nullable=False)
    ovn: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manager: Mapped[str] = mapped_column(String(256), nullable=False)
    uss_base_url: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    time_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    time_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    subscription_id: Mapped[str] = mapped_column(String(256), nullable=False)
    is_live: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class PeerCompositeOperationalIntentORM(Base):
    __tablename__ = "flight_declaration_operations_peercompositeoperationalintent"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bounds: Mapped[str] = mapped_column(String(140), nullable=False)
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    alt_max: Mapped[float] = mapped_column(Float, nullable=False)
    alt_min: Mapped[float] = mapped_column(Float, nullable=False)
    operational_intent_details_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flight_declaration_operations_peeroperationalintentdetail.id", ondelete="CASCADE"),
        nullable=False,
    )
    operational_intent_reference_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flight_declaration_operations_peeroperationalintentreference.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class FlightOperationTrackingORM(Base):
    __tablename__ = "flight_declaration_operations_flightoperationtracking"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    flight_declaration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flight_declaration_operations_flightdeclaration.id", ondelete="CASCADE"),
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)
    deltas: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
