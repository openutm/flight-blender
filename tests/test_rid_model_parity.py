"""Schema-parity checks for the RID models vs the Django originals (P4).

Django (``rid_operations/models.py``):
  * ``ISASubscription.view_hash``       -> ``IntegerField(null=True, db_index=True)``
  * ``ISASubscription.subscription_id`` -> ``db_index=True``
  * ``ISASubscription.created_at``      -> ``db_index=True``
  * ``RIDFlightDetail.created_at``      -> ``db_index=True``

The FastAPI port computes a *string* view hash, so we keep ``view_hash`` a
``String`` (documented deviation) but make it nullable to match Django's
``null=True``. The ``db_index=True`` columns must carry indexes.
"""

from flight_blender.models.rid import ISASubscription, RIDFlightDetail


def _col(model, name):
    return model.__table__.columns[name]


def test_view_hash_is_nullable():
    # Django: IntegerField(null=True). We keep String (FastAPI computes a string
    # hash) but it must be nullable.
    assert _col(ISASubscription, "view_hash").nullable is True


def test_indexed_columns_present():
    cols = ISASubscription.__table__.columns
    detail_cols = RIDFlightDetail.__table__.columns
    assert cols["subscription_id"].index is True
    assert cols["view_hash"].index is True
    assert cols["created_at"].index is True
    assert detail_cols["created_at"].index is True
