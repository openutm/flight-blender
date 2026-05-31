"""Tests for the operator-RID notification persistence path (FastAPI port).

Mirrors the Django ``OperatorRIDNotificationCreator`` / ``operator_rid_notifications``
consumer behaviour: published operator-RID notifications are persisted as
``OperatorRIDNotification`` rows.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from sqlalchemy import select

from flight_blender.models.notification import OperatorRIDNotification
from flight_blender.tasks import notification as nt


@pytest.mark.anyio
async def test_create_operator_rid_notification_persists_row(db):
    """The creator writes an OperatorRIDNotification row with the Django field mapping."""
    created = await nt.create_operator_rid_notification(
        message="hello operator",
        session_id="sess-abc",
        db=db,
    )

    assert created.id is not None
    assert created.message == "hello operator"
    assert created.session_id == "sess-abc"
    # Django default: is_active=True.
    assert created.is_active is True

    rows = (await db.execute(select(OperatorRIDNotification))).scalars().all()
    assert len(rows) == 1
    assert rows[0].message == "hello operator"
    assert rows[0].session_id == "sess-abc"


@pytest.mark.anyio
async def test_create_operator_rid_notification_serialises_dict_message(db):
    """A dict message (as delivered over AMQP) is serialised to JSON text."""
    payload = {"event": "rid", "value": 42}
    created = await nt.create_operator_rid_notification(
        message=payload,
        session_id="sess-dict",
        db=db,
    )

    assert json.loads(created.message) == payload
    assert created.session_id == "sess-dict"


@pytest.mark.anyio
async def test_create_operator_rid_notification_allows_null_session(db):
    """session_id is nullable (Django blank/null=True)."""
    created = await nt.create_operator_rid_notification(
        message="no session",
        session_id=None,
        db=db,
    )
    assert created.session_id is None
    assert created.message == "no session"


def test_consume_operator_rid_notifications_is_noop_without_amqp(monkeypatch):
    """The consumer entrypoint is import-safe and a no-op without AMQP_URL."""
    monkeypatch.delenv("AMQP_URL", raising=False)

    fake_pika = mock.MagicMock()
    with mock.patch.dict("sys.modules", {"pika": fake_pika}):
        # Must not raise and must not attempt a broker connection.
        nt.consume_operator_rid_notifications()

    fake_pika.BlockingConnection.assert_not_called()


def test_send_operational_update_persists_when_no_amqp(monkeypatch):
    """P2: without AMQP_URL the notification is persisted locally, not silently dropped."""
    monkeypatch.delenv("AMQP_URL", raising=False)

    captured: dict = {}

    # Capture the local-persistence call the task makes when AMQP is unset.
    monkeypatch.setattr(
        nt,
        "_persist_operator_rid_notification_sync",
        lambda **kw: captured.update(kw),
    )

    import flight_blender.tasks.flight_declaration as fd

    outcome = fd.send_operational_update_message.apply(
        args=("op-77", "feed update", "info"),
    )

    assert outcome.successful()
    # The session id (operation id) and message must be threaded through to the
    # local persistence path instead of being dropped.
    assert captured.get("session_id") == "op-77"
    assert captured.get("message") == "feed update"
