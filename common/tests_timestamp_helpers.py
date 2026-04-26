import json
from datetime import datetime, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from common.database_operations import FlightBlenderDatabaseWriter
from common.redis_stream_operations import RedisStreamOperations


class TimestampNormalizationTests(SimpleTestCase):
    def test_normalize_microsecond_timestamp(self):
        timestamp = 1_775_001_600_123_456

        normalized_timestamp = FlightBlenderDatabaseWriter._normalize_timestamp(timestamp)

        self.assertEqual(normalized_timestamp, datetime.fromtimestamp(1_775_001_600.123456, tz=timezone.utc))

    def test_normalize_millisecond_timestamp(self):
        timestamp = 1_775_001_600_123

        normalized_timestamp = FlightBlenderDatabaseWriter._normalize_timestamp(timestamp)

        self.assertEqual(normalized_timestamp, datetime.fromtimestamp(1_775_001_600.123, tz=timezone.utc))

    def test_normalize_missing_or_invalid_timestamp(self):
        self.assertIsNone(FlightBlenderDatabaseWriter._normalize_timestamp(0))

        with patch("common.database_operations.logger") as mock_logger:
            normalized_timestamp = FlightBlenderDatabaseWriter._normalize_timestamp("not-a-timestamp")

        self.assertIsNone(normalized_timestamp)
        mock_logger.warning.assert_called_once()


class RedisStreamMessageIdTests(SimpleTestCase):
    def test_extract_message_id_timestamp_accepts_str_and_bytes(self):
        self.assertEqual(RedisStreamOperations._extract_message_id_timestamp("1775001600123-0"), 1_775_001_600_123)
        self.assertEqual(RedisStreamOperations._extract_message_id_timestamp(b"1775001600456-1"), 1_775_001_600_456)

    def test_extract_message_id_timestamp_returns_zero_for_missing_or_invalid_values(self):
        self.assertEqual(RedisStreamOperations._extract_message_id_timestamp(None), 0)
        self.assertEqual(RedisStreamOperations._extract_message_id_timestamp("not-a-message-id"), 0)

    def test_parse_stream_message_sets_ingested_at_from_message_id(self):
        redis_stream_operations = RedisStreamOperations.__new__(RedisStreamOperations)
        fields = {
            b"lat_dd": b"46.1",
            b"lon_dd": b"7.2",
            b"altitude_mm": b"3000",
            b"traffic_source": b"11",
            b"source_type": b"0",
            b"icao_address": b"ABC123",
            b"timestamp": b"1775001600123456",
            b"metadata": json.dumps({"source": "test"}).encode("utf-8"),
            b"session_id": b"session-1",
        }

        observation = redis_stream_operations._parse_stream_message_to_observation(fields, message_id=b"1775001600999-0")

        self.assertIsNotNone(observation)
        self.assertEqual(observation.ingested_at_ms, 1_775_001_600_999)
        self.assertEqual(observation.metadata, {"source": "test"})
        self.assertEqual(observation.session_id, "session-1")
