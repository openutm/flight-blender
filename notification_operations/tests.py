import os
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from pika.exceptions import ChannelClosedByBroker

from notification_operations.notification_helper import InitialNotificationFactory


class InitialNotificationFactoryTests(SimpleTestCase):
    def _factory_with_channel(self, channel):
        factory = InitialNotificationFactory.__new__(InitialNotificationFactory)
        factory.connection = MagicMock()
        factory.channel = channel
        factory.exchange_name = "operational_events"
        return factory

    def test_mismatched_exchange_type_fails_without_deleting_exchange(self):
        channel = MagicMock()
        channel.exchange_declare.side_effect = ChannelClosedByBroker(406, "PRECONDITION_FAILED")
        factory = self._factory_with_channel(channel)

        with patch.dict(os.environ, {"AMQP_RECREATE_MISMATCHED_EXCHANGE": "0"}):
            with self.assertRaises(RuntimeError):
                factory.declare_exchange()

        channel.exchange_delete.assert_not_called()
        factory.connection.channel.assert_not_called()

    def test_mismatched_exchange_type_can_be_recreated_when_enabled(self):
        channel = MagicMock()
        channel.exchange_declare.side_effect = ChannelClosedByBroker(406, "PRECONDITION_FAILED")
        new_channel = MagicMock()
        factory = self._factory_with_channel(channel)
        factory.connection.channel.return_value = new_channel

        with patch.dict(os.environ, {"AMQP_RECREATE_MISMATCHED_EXCHANGE": "true"}):
            factory.declare_exchange()

        new_channel.exchange_delete.assert_called_once_with(exchange="operational_events")
        new_channel.exchange_declare.assert_called_once_with(
            exchange="operational_events",
            exchange_type="topic",
            durable=True,
        )
