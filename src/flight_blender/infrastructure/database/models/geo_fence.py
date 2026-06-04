import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from flight_blender.infrastructure.database.session import Base


class GeoFenceORM(Base):
    __tablename__ = "geo_fence_operations_geofence"

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
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
