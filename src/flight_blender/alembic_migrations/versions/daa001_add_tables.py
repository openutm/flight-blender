"""add daa tables

Revision ID: daa001_add_tables
Revises: 214fa5d62bd1
Create Date: 2026-05-30 20:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "daa001_add_tables"
down_revision = "214fa5d62bd1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daa_alert",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ownship_declaration_id", sa.Uuid(), nullable=True),
        sa.Column("intruder_icao_address", sa.String(length=256), nullable=False),
        sa.Column("alert_level", sa.Integer(), nullable=False),
        sa.Column("alert_type", sa.String(length=64), nullable=False),
        sa.Column("geometry", sa.String(length=64), nullable=False),
        sa.Column("initial_cpa_seconds", sa.Float(), nullable=True),
        sa.Column("closest_range_m", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "daa_incident_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=True),
        sa.Column("ownship_declaration_id", sa.Uuid(), nullable=True),
        sa.Column("intruder_icao_address", sa.String(length=256), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("alert_level", sa.Integer(), nullable=False),
        sa.Column("geometry", sa.String(length=64), nullable=False),
        sa.Column("range_m", sa.Float(), nullable=True),
        sa.Column("bearing_deg", sa.Float(), nullable=True),
        sa.Column("cpa_seconds", sa.Float(), nullable=True),
        sa.Column("altitude_diff_m", sa.Float(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("daa_incident_log")
    op.drop_table("daa_alert")
