"""rid view_hash nullable + Django db_index columns

Aligns ``rid_isa_subscription`` / ``rid_flight_detail`` with the Django
originals (``rid_operations/models.py``):

* ``rid_isa_subscription.view_hash`` becomes nullable (Django: ``null=True``).
  FastAPI computes a *string* SHA-256 digest, so the column stays
  ``String(64)`` rather than Django's ``IntegerField`` (documented deviation),
  but matches the nullability.
* Add the indexes Django declared with ``db_index=True``:
  ``rid_isa_subscription.subscription_id``, ``rid_isa_subscription.view_hash``,
  ``rid_isa_subscription.created_at`` and ``rid_flight_detail.created_at``.

Revision ID: rid001_view_hash_indexes
Revises: daa001_add_tables
Create Date: 2026-05-31 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "rid001_view_hash_indexes"
down_revision: Union[str, None] = "daa001_add_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("rid_isa_subscription") as batch_op:
        batch_op.alter_column("view_hash", existing_type=sa.String(length=64), nullable=True)
        batch_op.create_index("ix_rid_isa_subscription_subscription_id", ["subscription_id"], unique=False)
        batch_op.create_index("ix_rid_isa_subscription_view_hash", ["view_hash"], unique=False)
        batch_op.create_index("ix_rid_isa_subscription_created_at", ["created_at"], unique=False)

    with op.batch_alter_table("rid_flight_detail") as batch_op:
        batch_op.create_index("ix_rid_flight_detail_created_at", ["created_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("rid_flight_detail") as batch_op:
        batch_op.drop_index("ix_rid_flight_detail_created_at")

    with op.batch_alter_table("rid_isa_subscription") as batch_op:
        batch_op.drop_index("ix_rid_isa_subscription_created_at")
        batch_op.drop_index("ix_rid_isa_subscription_view_hash")
        batch_op.drop_index("ix_rid_isa_subscription_subscription_id")
        batch_op.alter_column("view_hash", existing_type=sa.String(length=64), nullable=False)
