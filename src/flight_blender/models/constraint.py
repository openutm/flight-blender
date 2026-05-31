"""
SQLAlchemy models for constraint operations.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flight_blender.database import Base


class ConstraintDetail(Base):
    __tablename__ = "constraint_detail"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    geofence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("geo_fence.id", ondelete="CASCADE"), nullable=True, unique=True)
    volumes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    _type: Mapped[str | None] = mapped_column("type", String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    geofence: Mapped["GeoFence | None"] = relationship("GeoFence", back_populates="constraint_detail")


class ConstraintReference(Base):
    __tablename__ = "constraint_reference"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    flight_declaration_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("flight_declaration.id", ondelete="CASCADE"), nullable=True)
    geofence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("geo_fence.id", ondelete="CASCADE"), nullable=True, unique=True)
    uss_availability: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    ovn: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manager: Mapped[str | None] = mapped_column(String(256), nullable=True)
    uss_base_url: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    version: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    time_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)
    time_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    geofence: Mapped["GeoFence | None"] = relationship("GeoFence", back_populates="constraint_reference")


class CompositeConstraint(Base):
    __tablename__ = "composite_constraint"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    declaration_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("flight_declaration.id", ondelete="CASCADE"), nullable=False)
    bounds: Mapped[str] = mapped_column(String(140), nullable=False)
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)
    alt_max: Mapped[float] = mapped_column(Float, nullable=False)
    alt_min: Mapped[float] = mapped_column(Float, nullable=False)
    constraint_reference_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("constraint_reference.id", ondelete="CASCADE"), nullable=False)
    constraint_detail_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("constraint_detail.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# Deferred import to avoid circular dependency
from flight_blender.models.geo_fence import GeoFence  # noqa: E402, F401
