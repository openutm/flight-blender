import uuid

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from flight_blender.db.session import Base


class AdapterSessionORM(Base):
    __tablename__ = "adapter_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(512), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    record: Mapped[dict] = mapped_column(JSON, nullable=False)


class AdapterSampleORM(Base):
    __tablename__ = "adapter_samples"

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adapter_sessions.id", ondelete="CASCADE"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    record: Mapped[dict] = mapped_column(JSON, nullable=False)
