"""
Tests that periodic conformance monitoring is scheduled via Celery beat and
that the periodic dispatcher fans out to active operations.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from flight_blender.tasks.celery_app import celery_app


def test_beat_schedule_has_conformance_entry():
    schedule = celery_app.conf.beat_schedule
    assert "periodic-conformance-check" in schedule


def test_conformance_schedule_runs_the_conformance_task():
    entry = celery_app.conf.beat_schedule["periodic-conformance-check"]
    assert entry["task"] == "check_all_flight_conformance"
    # runs on a finite, positive period
    assert isinstance(entry["schedule"], (int, float))
    assert entry["schedule"] > 0


def test_check_all_flight_conformance_dispatches_active_declarations():
    """The periodic dispatcher should schedule a check for each active op."""
    from flight_blender.tasks.conformance import check_all_flight_conformance

    decl = MagicMock()
    decl.id = uuid.uuid4()
    decl.state = 2
    decl.start_datetime = datetime.now(timezone.utc) - timedelta(hours=1)
    decl.end_datetime = datetime.now(timezone.utc) + timedelta(hours=1)

    with (
        patch("flight_blender.tasks.conformance.create_engine"),
        patch("flight_blender.tasks.conformance.Session") as mock_session_cls,
        patch("flight_blender.tasks.conformance.check_flight_conformance") as mock_check,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.execute.return_value.scalars.return_value.all.return_value = [decl]

        check_all_flight_conformance()

        mock_check.delay.assert_called_once_with(str(decl.id))
