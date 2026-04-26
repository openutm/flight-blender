from unittest.mock import patch

import arrow
from django.test import SimpleTestCase

from rid_operations.tasks import _parse_rid_timestamp_us


class RIDTimestampParsingTests(SimpleTestCase):
    def test_parse_rid_timestamp_returns_epoch_microseconds(self):
        timestamp_value = "2026-04-01T00:00:00Z"

        parsed_timestamp = _parse_rid_timestamp_us(timestamp_value, "test operation")

        self.assertEqual(parsed_timestamp, int(arrow.get(timestamp_value).float_timestamp * 1_000_000))

    def test_invalid_rid_timestamp_returns_zero_and_logs_warning(self):
        with patch("rid_operations.tasks.logger") as mock_logger:
            parsed_timestamp = _parse_rid_timestamp_us("not-a-timestamp", "test operation")

        self.assertEqual(parsed_timestamp, 0)
        mock_logger.warning.assert_called_once()

    def test_missing_rid_timestamp_returns_zero_and_logs_warning(self):
        with patch("rid_operations.tasks.logger") as mock_logger:
            parsed_timestamp = _parse_rid_timestamp_us(None, "test operation")

        self.assertEqual(parsed_timestamp, 0)
        mock_logger.warning.assert_called_once()
