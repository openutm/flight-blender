import os
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

# A pre-signed HS256 JWT with scope/iss/aud accepted by the bypass verifier.
# Signature is not checked when BYPASS_AUTH_TOKEN_VERIFICATION=1.
_DUMMY_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ0ZXN0dXNlciIsInNjb3BlIjoiZmxpZ2h0YmxlbmRlci53cml0ZSBmbGlnaHRibGVuZGVyLnJlYWQiLCJpc3MiOiJkdW1teSIsImF1ZCI6InRlc3RmbGlnaHQuZmxpZ2h0YmxlbmRlci5jb20ifQ"
    ".zW7dJaQyj0MpARupQDW5xA5KT8zNYF0DqIZXOowHlgI"
)
_AUTH_HEADER = f"Bearer {_DUMMY_JWT}"
_BYPASS_ENV = {"BYPASS_AUTH_TOKEN_VERIFICATION": "1"}


class StartStopSurveillanceHeartbeatTrackTests(TestCase):
    """Tests for start_stop_surveillance_heartbeat_track view – session/task creation error paths."""

    def _url(self, session_id):
        return reverse("start_stop_surveillance_heartbeat_track", kwargs={"surveillance_session_id": str(session_id)})

    def _put(self, session_id, data):
        with patch.dict(os.environ, _BYPASS_ENV):
            return self.client.put(
                self._url(session_id),
                data=data,
                content_type="application/json",
                HTTP_AUTHORIZATION=_AUTH_HEADER,
            )

    # ------------------------------------------------------------------
    # Invalid action
    # ------------------------------------------------------------------
    def test_invalid_action_returns_400(self):
        session_id = uuid.uuid4()
        response = self._put(session_id, {"action": "invalid"})
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # "start" action – success path
    # ------------------------------------------------------------------
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseReader")
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseWriter")
    def test_start_success(self, MockWriter, MockReader):
        session_id = uuid.uuid4()
        reader_instance = MockReader.return_value
        writer_instance = MockWriter.return_value

        reader_instance.get_surveillance_session_by_id.return_value = None
        writer_instance.create_surveillance_session.return_value = True
        writer_instance.create_surveillance_monitoring_heartbeat_periodic_task.return_value = True
        writer_instance.create_surveillance_monitoring_track_periodic_task.return_value = True

        response = self._put(session_id, {"action": "start"})

        self.assertEqual(response.status_code, 200)
        writer_instance.create_surveillance_session.assert_called_once()
        writer_instance.create_surveillance_monitoring_heartbeat_periodic_task.assert_called_once()
        writer_instance.create_surveillance_monitoring_track_periodic_task.assert_called_once()

    # ------------------------------------------------------------------
    # "start" action – session already exists
    # ------------------------------------------------------------------
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseReader")
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseWriter")
    def test_start_session_already_exists_returns_400(self, MockWriter, MockReader):
        session_id = uuid.uuid4()
        MockReader.return_value.get_surveillance_session_by_id.return_value = MagicMock()

        response = self._put(session_id, {"action": "start"})

        self.assertEqual(response.status_code, 400)
        MockWriter.return_value.create_surveillance_session.assert_not_called()

    # ------------------------------------------------------------------
    # "start" action – session creation fails
    # ------------------------------------------------------------------
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseReader")
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseWriter")
    def test_start_session_creation_failure_returns_500(self, MockWriter, MockReader):
        session_id = uuid.uuid4()
        reader_instance = MockReader.return_value
        writer_instance = MockWriter.return_value

        reader_instance.get_surveillance_session_by_id.return_value = None
        writer_instance.create_surveillance_session.return_value = False

        response = self._put(session_id, {"action": "start"})

        self.assertEqual(response.status_code, 500)
        self.assertIn("error", response.json())
        writer_instance.create_surveillance_monitoring_heartbeat_periodic_task.assert_not_called()
        writer_instance.create_surveillance_monitoring_track_periodic_task.assert_not_called()
        writer_instance.delete_surveillance_session.assert_not_called()

    # ------------------------------------------------------------------
    # "start" action – heartbeat task creation fails → session rolled back
    # ------------------------------------------------------------------
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseReader")
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseWriter")
    def test_start_heartbeat_task_failure_rolls_back_session(self, MockWriter, MockReader):
        session_id = uuid.uuid4()
        reader_instance = MockReader.return_value
        writer_instance = MockWriter.return_value

        reader_instance.get_surveillance_session_by_id.return_value = None
        writer_instance.create_surveillance_session.return_value = True
        writer_instance.create_surveillance_monitoring_heartbeat_periodic_task.return_value = False

        response = self._put(session_id, {"action": "start"})

        self.assertEqual(response.status_code, 500)
        self.assertIn("error", response.json())
        writer_instance.delete_surveillance_session.assert_called_once_with(surveillance_session_id=session_id)
        writer_instance.create_surveillance_monitoring_track_periodic_task.assert_not_called()

    # ------------------------------------------------------------------
    # "start" action – track task creation fails → session rolled back
    # ------------------------------------------------------------------
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseReader")
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseWriter")
    def test_start_track_task_failure_rolls_back_session(self, MockWriter, MockReader):
        session_id = uuid.uuid4()
        reader_instance = MockReader.return_value
        writer_instance = MockWriter.return_value

        reader_instance.get_surveillance_session_by_id.return_value = None
        writer_instance.create_surveillance_session.return_value = True
        writer_instance.create_surveillance_monitoring_heartbeat_periodic_task.return_value = True
        writer_instance.create_surveillance_monitoring_track_periodic_task.return_value = False

        response = self._put(session_id, {"action": "start"})

        self.assertEqual(response.status_code, 500)
        self.assertIn("error", response.json())
        writer_instance.delete_surveillance_session.assert_called_once_with(surveillance_session_id=session_id)

    # ------------------------------------------------------------------
    # "stop" action – session not found
    # ------------------------------------------------------------------
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseReader")
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseWriter")
    def test_stop_session_not_found_returns_400(self, MockWriter, MockReader):
        session_id = uuid.uuid4()
        MockReader.return_value.get_surveillance_session_by_id.return_value = None

        response = self._put(session_id, {"action": "stop"})

        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # "stop" action – no active tasks found
    # ------------------------------------------------------------------
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseReader")
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseWriter")
    def test_stop_no_tasks_returns_400(self, MockWriter, MockReader):
        session_id = uuid.uuid4()
        reader_instance = MockReader.return_value
        reader_instance.get_surveillance_session_by_id.return_value = MagicMock()
        reader_instance.get_surveillance_periodic_tasks_by_session_id.return_value = []

        response = self._put(session_id, {"action": "stop"})

        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # "stop" action – success path
    # ------------------------------------------------------------------
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseReader")
    @patch("surveillance_monitoring_operations.views.FlightBlenderDatabaseWriter")
    def test_stop_success(self, MockWriter, MockReader):
        session_id = uuid.uuid4()
        reader_instance = MockReader.return_value
        writer_instance = MockWriter.return_value

        task = MagicMock()
        reader_instance.get_surveillance_session_by_id.return_value = MagicMock()
        reader_instance.get_surveillance_periodic_tasks_by_session_id.return_value = [task]

        response = self._put(session_id, {"action": "stop"})

        self.assertEqual(response.status_code, 200)
        writer_instance.remove_surveillance_monitoring_heartbeat_periodic_task.assert_called_once_with(surveillance_monitoring_heartbeat_task=task)
