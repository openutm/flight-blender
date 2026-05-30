"""
SQLAlchemy models for geo fence operations.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flight_blender.database import Base


class GeoFence(Base):
    __tablename__ = "geo_fence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    raw_geo_fence: Mapped[str | None] = mapped_column(Text, nullable=True)
    geozone: Mapped[str | None] = mapped_column(Text, nullable=True)
    upper_limit: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    lower_limit: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    altitude_ref: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    bounds: Mapped[str] = mapped_column(String(140), nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str | None] = mapped_column(String(140), nullable=True)
    is_test_dataset: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships (back-populated from constraint models)
    constraint_detail: Mapped["ConstraintDetail | None"] = relationship("ConstraintDetail", back_populates="geofence", uselist=False)
    constraint_reference: Mapped["ConstraintReference | None"] = relationship("ConstraintReference", back_populates="geofence", uselist=False)

    def __str__(self) -> str:
        return self.name


# Import here to avoid circular – models define the FK
from flight_blender.models.constraint import ConstraintDetail, ConstraintReference  # noqa: E402, F401
