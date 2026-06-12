"""Tests for flight_blender.notifications/notification_helper.py.

All pika network calls are mocked – no RabbitMQ required.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import arrow
import pytest
from pika.exceptions import ChannelClosedByBroker

from flight_blender.domain_types.notifications import FlightDeclarationUpdateMessage, NotificationLevel
from flight_blender.clients.notification_client import (
    InitialNotificationFactory,
    NotificationFactory,
    _should_recreate_mismatched_exchange,
)


# ---------------------------------------------------------------------------
# _should_recreate_mismatched_exchange helper
# ---------------------------------------------------------------------------


class TestShouldRecreateFlag:
    def test_returns_false_when_unset(self, monkeypatch):
        monkeypatch.setattr("flight_blender.config.settings.AMQP_RECREATE_MISMATCHED_EXCHANGE", False)
        assert _should_recreate_mismatched_exchange() is False

    def test_returns_true_for_1(self, monkeypatch):
        monkeypatch.setattr("flight_blender.config.settings.AMQP_RECREATE_MISMATCHED_EXCHANGE", True)
        assert _should_recreate_mismatched_exchange() is True

    def test_returns_true_for_true(self, monkeypatch):
        monkeypatch.setattr("flight_blender.config.settings.AMQP_RECREATE_MISMATCHED_EXCHANGE", True)
        assert _should_recreate_mismatched_exchange() is True

    def test_returns_false_for_0(self, monkeypatch):
        monkeypatch.setattr("flight_blender.config.settings.AMQP_RECREATE_MISMATCHED_EXCHANGE", False)
        assert _should_recreate_mismatched_exchange() is False


# ---------------------------------------------------------------------------
# NotificationFactory (requires RabbitMQ) – fully mocked
# ---------------------------------------------------------------------------


FAKE_AMQP_URL = "amqp://guest:guest@localhost:5672/"


@pytest.fixture()
def mock_pika():
    """Patch pika.BlockingConnection so no network call happens."""
    mock_conn = MagicMock()
    mock_channel = MagicMock()
    mock_conn.channel.return_value = mock_channel
    with patch("flight_blender.clients.notification_client.pika.BlockingConnection", return_value=mock_conn) as p:
        yield p, mock_conn, mock_channel


class TestNotificationFactory:
    def test_init_creates_channel(self, mock_pika):
        _, mock_conn, mock_channel = mock_pika
        factory = NotificationFactory(flight_declaration_id="flight-123", amqp_connection_url=FAKE_AMQP_URL)
        mock_conn.channel.assert_called_once()
        assert factory.exchange == "operational_events"
        assert factory.flight_declaration_id == "flight-123"

    def test_send_message(self, mock_pika):
        _, mock_conn, mock_channel = mock_pika
        factory = NotificationFactory(flight_declaration_id="flight-123", amqp_connection_url=FAKE_AMQP_URL)
        msg = FlightDeclarationUpdateMessage(
            body="Flight state changed",
            level=NotificationLevel.INFO.value,
            timestamp="2026-01-01T00:00:00Z",
        )
        factory.send_message(msg)
        mock_channel.basic_publish.assert_called_once()
        call_kwargs = mock_channel.basic_publish.call_args
        assert call_kwargs.kwargs["exchange"] == "operational_events"
        assert call_kwargs.kwargs["routing_key"] == "flight-123"

    def test_declare_queue(self, mock_pika):
        _, mock_conn, mock_channel = mock_pika
        factory = NotificationFactory(flight_declaration_id="flight-123", amqp_connection_url=FAKE_AMQP_URL)
        factory.declare_queue("my-queue")
        mock_channel.queue_declare.assert_called_once_with(queue="my-queue")
        mock_channel.queue_bind.assert_called_once()

    def test_declare_exchange(self, mock_pika):
        _, mock_conn, mock_channel = mock_pika
        factory = NotificationFactory(flight_declaration_id="flight-123", amqp_connection_url=FAKE_AMQP_URL)
        factory.declare_exchange("my-exchange")
        mock_channel.exchange_declare.assert_called_once_with(exchange="my-exchange")

    def test_close(self, mock_pika):
        _, mock_conn, mock_channel = mock_pika
        factory = NotificationFactory(flight_declaration_id="flight-123", amqp_connection_url=FAKE_AMQP_URL)
        factory.close()
        mock_channel.close.assert_called_once()
        mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# InitialNotificationFactory
# ---------------------------------------------------------------------------


class TestInitialNotificationFactory:
    def test_declare_exchange_success(self, mock_pika):
        _, mock_conn, mock_channel = mock_pika
        factory = InitialNotificationFactory(amqp_connection_url=FAKE_AMQP_URL)
        factory.declare_exchange()
        mock_channel.exchange_declare.assert_called_once_with(
            exchange="operational_events",
            exchange_type="topic",
            durable=True,
        )

    def test_declare_exchange_mismatched_type_recreate_disabled_raises(self, mock_pika, monkeypatch):
        monkeypatch.setattr("flight_blender.config.settings.AMQP_RECREATE_MISMATCHED_EXCHANGE", False)
        _, mock_conn, mock_channel = mock_pika
        mock_channel.exchange_declare.side_effect = ChannelClosedByBroker(406, "PRECONDITION_FAILED")

        factory = InitialNotificationFactory(amqp_connection_url=FAKE_AMQP_URL)
        with pytest.raises(RuntimeError, match="already exists with a different type"):
            factory.declare_exchange()

    def test_declare_exchange_mismatched_type_recreate_enabled(self, mock_pika, monkeypatch):
        monkeypatch.setattr("flight_blender.config.settings.AMQP_RECREATE_MISMATCHED_EXCHANGE", True)
        _, mock_conn, new_channel = mock_pika

        # First call raises, second call succeeds
        new_channel.exchange_declare.side_effect = [
            ChannelClosedByBroker(406, "PRECONDITION_FAILED"),
            None,
        ]
        # After channel close, connection.channel() returns the same mock channel
        mock_conn.channel.return_value = new_channel

        factory = InitialNotificationFactory(amqp_connection_url=FAKE_AMQP_URL)
        factory.declare_exchange()  # Should not raise
        new_channel.exchange_delete.assert_called_once_with(exchange="operational_events")

    def test_declare_exchange_non_406_error_reraises(self, mock_pika):
        _, mock_conn, mock_channel = mock_pika
        mock_channel.exchange_declare.side_effect = ChannelClosedByBroker(404, "NOT_FOUND")

        factory = InitialNotificationFactory(amqp_connection_url=FAKE_AMQP_URL)
        with pytest.raises(ChannelClosedByBroker):
            factory.declare_exchange()

    def test_close(self, mock_pika):
        _, mock_conn, mock_channel = mock_pika
        factory = InitialNotificationFactory(amqp_connection_url=FAKE_AMQP_URL)
        factory.close()
        mock_channel.close.assert_called_once()
        mock_conn.close.assert_called_once()

    def test_custom_exchange_name(self, mock_pika):
        _, mock_conn, mock_channel = mock_pika
        factory = InitialNotificationFactory(amqp_connection_url=FAKE_AMQP_URL, exchange_name="custom-exchange")
        factory.declare_exchange()
        call_kwargs = mock_channel.exchange_declare.call_args
        assert call_kwargs.kwargs["exchange"] == "custom-exchange"


# ---------------------------------------------------------------------------
# Notifications service additional coverage
# ---------------------------------------------------------------------------


class TestNotificationsServiceCoverage:
    """Additional tests for NotificationsOperations."""

    @pytest.mark.asyncio
    async def test_get_active_notifications(self):
        """Test get_active_notifications."""
        from flight_blender.services.notifications_svc import NotificationsOperations

        mock_repo = AsyncMock()

        mock_notification = MagicMock()
        mock_notification.id = uuid.uuid4()
        mock_notification.session_id = uuid.uuid4()
        mock_notification.message = "Test message"
        mock_notification.is_active = True
        mock_notification.created_at = arrow.utcnow().datetime

        mock_repo.get_active_notifications_between = AsyncMock(return_value=[mock_notification])

        service = NotificationsOperations(repo=mock_repo)

        result = await service.get_active_notifications(
            start_time=arrow.utcnow().shift(hours=-1).datetime,
            end_time=arrow.utcnow().datetime,
        )

        assert len(result) == 1
        assert "id" in result[0]

    @pytest.mark.asyncio
    async def test_create_notification(self):
        """Test create_notification."""
        from flight_blender.services.notifications_svc import NotificationsOperations

        mock_repo = AsyncMock()

        mock_notification = MagicMock()
        mock_notification.id = uuid.uuid4()
        mock_notification.session_id = uuid.uuid4()
        mock_notification.message = "Test message"
        mock_notification.is_active = True

        mock_repo.create_notification = AsyncMock(return_value=mock_notification)

        service = NotificationsOperations(repo=mock_repo)

        result = await service.create_notification(message="Test message")

        assert "id" in result
        assert "message" in result

    def test_parse_date_range_with_lookback(self):
        """Test parse_date_range_with_lookback static method."""
        from flight_blender.services.notifications_svc import NotificationsOperations

        result, error = NotificationsOperations.parse_date_range_with_lookback(
            start_date=arrow.utcnow().shift(hours=-1).isoformat(),
            end_date=arrow.utcnow().isoformat(),
        )

        assert result is not None
        assert error is None

    def test_parse_date_range_with_lookback_invalid(self):
        """Test parse_date_range_with_lookback with invalid date."""
        from flight_blender.services.notifications_svc import NotificationsOperations

        result, error = NotificationsOperations.parse_date_range_with_lookback(
            start_date="invalid-date",
            end_date="also-invalid",
        )

        assert result is None
        assert error is not None
