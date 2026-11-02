"""Durable adapter session compliance records.

Revision ID: a218_session_records
Revises: 8c998de89757
"""

import sqlalchemy as sa

from alembic import op

revision = "a218_session_records"
down_revision = "8c998de89757"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adapter_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner", sa.String(512), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
    )
    op.create_table(
        "adapter_samples",
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("adapter_sessions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("record", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("adapter_samples")
    op.drop_table("adapter_sessions")
