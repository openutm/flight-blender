"""
SQLAlchemy models for Remote ID operations.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from flight_blender.database import Base


class ISASubscription(Base):
    __tablename__ = "rid_isa_subscription"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[str] = mapped_column(String(256), nullable=False)
    view: Mapped[str] = mapped_column(String(256), nullable=False)
    flight_details: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    view_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RIDFlightDetail(Base):
    __tablename__ = "rid_flight_detail"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    operation_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    auth_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    uas_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    eu_classification: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
