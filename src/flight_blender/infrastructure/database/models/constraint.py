import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flight_blender.infrastructure.database.session import Base


class ConstraintDetailORM(Base):
    __tablename__ = "constraint_operations_constraintdetail"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    geofence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("geo_fence_operations_geofence.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    volumes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    _type: Mapped[str | None] = mapped_column("_type", String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    composite_constraint_detail: Mapped[list["CompositeConstraintORM"]] = relationship(
        back_populates="constraint_detail", cascade="all, delete-orphan"
    )


class ConstraintReferenceORM(Base):
    __tablename__ = "constraint_operations_constraintreference"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Stub — real FK to flight_declarations_flightdeclaration filled in Phase 6
    flight_declaration_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    geofence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("geo_fence_operations_geofence.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    uss_availability: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    ovn: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manager: Mapped[str | None] = mapped_column(String(256), nullable=True)
    uss_base_url: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    time_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    time_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    is_live: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    composite_constraint_reference: Mapped[list["CompositeConstraintORM"]] = relationship(
        back_populates="constraint_reference", cascade="all, delete-orphan"
    )


class CompositeConstraintORM(Base):
    __tablename__ = "constraint_operations_compositeconstraint"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Stub — real FK to flight_declarations_flightdeclaration filled in Phase 6
    declaration_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    bounds: Mapped[str] = mapped_column(String(140), nullable=False)
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    alt_max: Mapped[float] = mapped_column(Float, nullable=False)
    alt_min: Mapped[float] = mapped_column(Float, nullable=False)
    constraint_reference_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("constraint_operations_constraintreference.id", ondelete="CASCADE"),
        nullable=False,
    )
    constraint_detail_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("constraint_operations_constraintdetail.id", ondelete="CASCADE"),
        nullable=False,
    )

    constraint_reference: Mapped["ConstraintReferenceORM"] = relationship(back_populates="composite_constraint_reference")
    constraint_detail: Mapped["ConstraintDetailORM"] = relationship(back_populates="composite_constraint_detail")
