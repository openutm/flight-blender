"""make operator_rid_notification.session_id nullable

Restores Django parity for ``OperatorRIDNotification.session_id`` which was
declared ``blank=True, null=True`` in the Django model but created as
``nullable=False`` in the initial FastAPI tables. Operator-RID notifications may
be persisted without a session id (e.g. the no-AMQP local-persistence fallback),
so the column must be nullable.

Revision ID: nt001_notification_session_nullable
Revises: ff001_session_id_index
Create Date: 2026-05-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "nt001_notification_session_nullable"
down_revision: Union[str, None] = "ff001_session_id_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("operator_rid_notification") as batch_op:
        batch_op.alter_column(
            "session_id",
            existing_type=sa.String(length=256),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("operator_rid_notification") as batch_op:
        batch_op.alter_column(
            "session_id",
            existing_type=sa.String(length=256),
            nullable=False,
        )
