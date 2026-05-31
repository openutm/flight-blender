"""Parity guardrails for the ``flight_feed_observation.session_id`` index.

Django ``flight_feed_operations/models.py`` orders ``FlightObservation`` by
``-created_at`` and looks observations up by ``session_id`` on the hot
latest-observation-by-session path.  The FastAPI model/migration must keep an
index on ``session_id``: the model column must be indexed and a matching Alembic
migration must create ``ix_flight_feed_observation_session_id``.
"""

from pathlib import Path

VERSIONS_DIR = Path(__file__).parent.parent / "src" / "flight_blender" / "alembic_migrations" / "versions"


def _read_all_migration_sql() -> str:
    """Concatenate all migration file source into one string for substring checks."""
    return "\n".join(p.read_text() for p in VERSIONS_DIR.glob("*.py"))


def test_session_id_index_migration_exists():
    """The session_id index must be defined in a migration."""
    sql = _read_all_migration_sql()
    assert "ix_flight_feed_observation_session_id" in sql


def test_flight_observation_session_id_is_indexed():
    """The FlightObservation model's session_id column must be indexed."""
    from flight_blender.models.flight_feed import FlightObservation

    session_col = FlightObservation.__table__.columns["session_id"]
    assert session_col.index is True
