"""add index on flight_feed_observation.session_id

Restores an index on ``flight_feed_observation.session_id`` for the
latest-observation-by-session lookup hot path (the Django
``FlightObservation`` was queried by ``session_id`` and ordered by
``-created_at``).

Revision ID: ff001_session_id_index
Revises: rid001_view_hash_indexes
Create Date: 2026-05-31
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff001_session_id_index"
down_revision: Union[str, None] = "rid001_view_hash_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("flight_feed_observation") as batch_op:
        batch_op.create_index(
            "ix_flight_feed_observation_session_id",
            ["session_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("flight_feed_observation") as batch_op:
        batch_op.drop_index("ix_flight_feed_observation_session_id")
