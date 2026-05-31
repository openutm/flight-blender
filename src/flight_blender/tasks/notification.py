"""Operator-RID notification persistence and consumer (FastAPI port).

Mirrors the Django ``notification_operations`` app:

* ``OperatorRIDNotificationCreator`` (``notification_helper.py``) created an
  ``OperatorRIDNotification`` row from a message + session id.
* the ``operator_rid_notifications`` management command consumed messages from
  the durable ``operization_events`` topic exchange and created those rows.

In the FastAPI port the row-creation logic is a dependency-injected async
function (``create_operator_rid_notification``) so it can be unit-tested against
the test DB, while ``consume_operator_rid_notifications`` is an import-safe
consumer entrypoint that becomes a no-op when ``AMQP_URL`` is not configured.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.database import AsyncSessionLocal
from flight_blender.models.notification import OperatorRIDNotification

logger = logging.getLogger(__name__)

# Durable topic exchange used by the Django notification helper/consumer.
NOTIFICATION_EXCHANGE = "operization_events"


def _coerce_message(message: Any) -> str:
    """Return a text representation of a notification message.

    Messages delivered over AMQP arrive as JSON (a ``dict``); the persisted
    model field is text, so dict/list payloads are serialised to JSON.
    """
    if isinstance(message, str):
        return message
    return json.dumps(message)


async def create_operator_rid_notification(
    *,
    message: Any,
    session_id: str | None,
    db: AsyncSession,
) -> OperatorRIDNotification:
    """Persist an :class:`OperatorRIDNotification` row.

    Faithful port of Django's ``OperatorRIDNotificationCreator.create_notification``:
    stores ``message`` and ``session_id`` (``is_active`` defaults to ``True``).
    """
    notification = OperatorRIDNotification(
        message=_coerce_message(message),
        session_id=session_id,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)
    return notification


async def list_operator_rid_notifications(
    *,
    db: AsyncSession,
    session_id: str | None = None,
) -> list[OperatorRIDNotification]:
    """Read persisted notifications (internal helper, optionally by session)."""
    stmt = select(OperatorRIDNotification)
    if session_id is not None:
        stmt = stmt.where(OperatorRIDNotification.session_id == session_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _persist_operator_rid_notification_sync(*, message: Any, session_id: str | None) -> None:
    """Synchronous wrapper used by Celery tasks to persist a notification.

    Runs the async creator against a fresh session. Resilient by design: any
    failure is logged rather than raised so callers (e.g. the publish task) never
    crash on a best-effort local persist.
    """

    async def _runner() -> None:
        async with AsyncSessionLocal() as session:
            await create_operator_rid_notification(message=message, session_id=session_id, db=session)

    try:
        asyncio.run(_runner())
    except Exception:  # pragma: no cover - best-effort local persistence
        logger.exception("Failed to persist operator RID notification locally")


def consume_operator_rid_notifications() -> None:
    """Consume operator-RID notifications from AMQP and persist them.

    Mirrors the Django ``operator_rid_notifications`` management command. This is
    a long-running blocking consumer; it is import-safe and a no-op when
    ``AMQP_URL`` is not configured so it can be imported in any environment.
    """
    amqp_connection_url = os.getenv("AMQP_URL")
    if not amqp_connection_url:
        logger.warning("AMQP_URL is not set; operator RID notification consumer is a no-op")
        return

    import pika  # imported lazily so the module stays import-safe without pika configured

    connection = pika.BlockingConnection(pika.URLParameters(amqp_connection_url))
    channel = connection.channel()
    channel.exchange_declare(exchange=NOTIFICATION_EXCHANGE, exchange_type="topic", durable=True)
    result = channel.queue_declare(queue="", exclusive=True)
    queue_name = result.method.queue
    channel.queue_bind(exchange=NOTIFICATION_EXCHANGE, queue=queue_name, routing_key="#")

    def callback(ch, method, properties, body):  # noqa: ANN001 - pika callback signature
        try:
            message = json.loads(body)
        except (ValueError, TypeError):
            message = body.decode("utf-8") if isinstance(body, bytes) else str(body)
        session_id = method.routing_key
        _persist_operator_rid_notification_sync(message=message, session_id=session_id)

    channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=True)
    channel.start_consuming()
