import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from flight_blender.infrastructure.database.session import Base


class ISASubscriptionORM(Base):
    __tablename__ = "rid_operations_isasubscription"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True, default=uuid.uuid4)
    view: Mapped[str | None] = mapped_column(Text, nullable=True)
    flight_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    end_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    view_hash: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    is_simulated: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class RIDFlightDetailORM(Base):
    __tablename__ = "rid_operations_ridflightdetail"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_data: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uas_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    eu_classification: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
