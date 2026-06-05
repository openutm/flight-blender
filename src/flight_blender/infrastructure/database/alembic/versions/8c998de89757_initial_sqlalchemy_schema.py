"""initial sqlalchemy schema

Revision ID: 8c998de89757
Revises:
Create Date: 2026-06-05
"""

from collections.abc import Sequence

from alembic import op

from flight_blender.infrastructure.database import models  # noqa: F401
from flight_blender.infrastructure.database.session import Base

revision: str = "8c998de89757"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAMES: tuple[str, ...] = (
    "flight_declaration_operations_flightdeclaration",
    "flight_declaration_operations_peeroperationalintentdetail",
    "flight_declaration_operations_peeroperationalintentreference",
    "flight_feed_operations_flightobservation",
    "flight_feed_operations_signedtelmetrypublickey",
    "geo_fence_operations_geofence",
    "notification_operations_operatorridnotification",
    "rid_operations_isasubscription",
    "rid_operations_ridflightdetail",
    "surveillance_monitoring_operations_surveillancesensor",
    "surveillance_monitoring_operations_surveillancesession",
    "conformance_monitoring_operations_conformancerecord",
    "constraint_operations_constraintdetail",
    "constraint_operations_constraintreference",
    "flight_declaration_operations_flightoperationalintentdetail",
    "flight_declaration_operations_flightoperationalintentreference",
    "flight_declaration_operations_flightoperationtracking",
    "flight_declaration_operations_peercompositeoperationalintent",
    "surveillance_monitoring_operations_surveillanceheartbeatevent",
    "surveillance_monitoring_operations_surveillancesensorfailur2a6d",
    "surveillance_monitoring_operations_surveillancesensorhealth",
    "surveillance_monitoring_operations_surveillancesensormainte43b7",
    "surveillance_monitoring_operations_surveillancesensorthealte007",
    "surveillance_monitoring_operations_surveillancetrackevent",
    "constraint_operations_compositeconstraint",
    "flight_declaration_operations_compositeoperationalintent",
    "flight_declaration_operations_subscriber",
)


def _tables() -> list:
    return [Base.metadata.tables[name] for name in TABLE_NAMES]


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=_tables(), checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, tables=list(reversed(_tables())), checkfirst=True)
