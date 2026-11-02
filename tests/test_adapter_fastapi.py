from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import create_engine, inspect

from alembic import command
from flight_blender.db.session import async_get_db
from flight_blender.models.flight_declarations_orm import FlightDeclarationORM


def auth(scope="flightblender.read flightblender.write", subject="adapter-test", key="adapter-test-secret-32-characters", algorithm="HS256"):
    token = jwt.encode(
        {
            "sub": subject,
            "iss": "https://passport.example.test",
            "aud": "testflight.flightblender.com",
            "scope": scope,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        key,
        algorithm=algorithm,
        headers={"kid": "test-key"},
    )
    return {"Authorization": "Bearer " + token}


def batch():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "session": {
            "id": str(uuid4()),
            "assignment": {
                "systemID": 1,
                "declarationID": str(uuid4()),
                "vehicleID": "v1",
                "operatorID": "op",
                "pilot": {"id": "", "name": "Test Pilot", "license": "TEST", "contact": "pilot@example.test"},
                "aerobridgeOperationID": "",
            },
            "startedAt": now,
            "endedAt": None,
            "blenderURL": "http://localhost:8000",
            "aerobridgeURL": "",
            "revision": 2,
        },
        "samples": [
            {
                "sequence": 1,
                "vehicle": {
                    "systemID": 1,
                    "lastSeen": now,
                    "heartbeatAt": now,
                    "positionAt": now,
                    "gpsAt": now,
                    "frames": 2,
                    "armed": False,
                    "latitude": 47.5,
                    "longitude": 19.0,
                    "altitudeMSL": 120,
                    "altitudeWGS84": 164,
                    "relativeAltitude": 2,
                    "speed": 2.1,
                    "heading": 90,
                    "fix": 3,
                    "satellites": 12,
                },
            }
        ],
    }


def test_idempotent_batches_and_isolation(mounted_fastapi_client):
    c = mounted_fastapi_client
    b = batch()
    path = "/adapter_ops/sessions/" + b["session"]["id"]
    for _ in range(2):
        assert c.put(path, json=b, headers=auth()).status_code == 200
    result = c.get(path, headers=auth())
    assert result.status_code == 200
    assert len(result.json()["samples"]) == 1
    assert result.json()["session"]["assignment"]["pilot"]["license"] == "TEST"
    assert c.get(path + "?after=1", headers=auth()).json()["samples"] == []
    assert c.get(path, headers=auth(subject="other")).status_code == 404
    assert c.put(path, json=b, headers=auth(subject="other")).status_code == 403
    changed = deepcopy(b)
    changed["session"]["assignment"]["pilot"]["name"] = "Other pilot"
    assert c.put(path, json=changed, headers=auth()).status_code == 409
    changed = deepcopy(b)
    changed["samples"][0]["vehicle"]["speed"] = 3
    assert c.put(path, json=changed, headers=auth()).status_code == 409
    # Conflicts roll back all changes in the request.
    assert c.get(path, headers=auth()).json()["samples"][0]["vehicle"]["speed"] == 2.1


def test_closed_sessions_and_validation(mounted_fastapi_client):
    c = mounted_fastapi_client
    b = batch()
    path = "/adapter_ops/sessions/" + b["session"]["id"]
    assert c.put(path, json=b, headers=auth()).status_code == 200
    closed = deepcopy(b)
    closed["session"]["revision"] = 3
    closed["session"]["endedAt"] = datetime.now(timezone.utc).isoformat()
    assert c.put(path, json=closed, headers=auth()).status_code == 200
    # An old retry must not reopen the record.
    assert c.put(path, json=b, headers=auth()).status_code == 200
    assert c.get(path, headers=auth()).json()["session"]["endedAt"] is not None
    # A stale open-session batch must still respect the stored end time.
    late = deepcopy(b)
    late["samples"][0]["sequence"] = 2
    late["samples"][0]["vehicle"]["gpsAt"] = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    assert c.put(path, json=late, headers=auth()).status_code == 409
    assert len(c.get(path, headers=auth()).json()["samples"]) == 1
    b["session"]["revision"] = 4
    assert c.put(path, json=b, headers=auth()).status_code == 409
    b["samples"][0]["vehicle"]["systemID"] = 2
    assert c.put(path, json=b, headers=auth()).status_code == 422
    b = batch()
    b["session"]["assignment"]["pilot"]["license"] = " "
    assert c.put("/adapter_ops/sessions/" + b["session"]["id"], json=b, headers=auth()).status_code == 422


class TestAdapterAuthEnforcement:
    @pytest.fixture
    def rsa_key(self, monkeypatch):
        monkeypatch.setattr("flight_blender.config.settings.BYPASS_AUTH_TOKEN_VERIFICATION", False)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
        jwk["kid"] = "test-key"

        async def jwks(_url):
            return {"keys": [jwk]}

        monkeypatch.setattr("flight_blender.auth.jwt_validator._fetch_jwks", jwks)
        return key

    def test_missing_token(self, mounted_fastapi_client, rsa_key):
        c = mounted_fastapi_client
        b = batch()
        path = "/adapter_ops/sessions/" + b["session"]["id"]
        assert c.get(path).status_code == 401
        assert c.put(path, json=b).status_code == 401

    def test_wrong_scope(self, mounted_fastapi_client, rsa_key):
        c = mounted_fastapi_client
        b = batch()
        path = "/adapter_ops/sessions/" + b["session"]["id"]
        assert c.get(path, headers=auth("flightblender.write", key=rsa_key, algorithm="RS256")).status_code == 403
        assert c.put(path, json=b, headers=auth("flightblender.read", key=rsa_key, algorithm="RS256")).status_code == 403


def test_adapter_migration_upgrade_and_downgrade(tmp_path, monkeypatch):
    database_url = "sqlite:///" + str(tmp_path / "adapter-migration.sqlite")
    monkeypatch.setattr("flight_blender.config.settings.DATABASE_URL", database_url)
    config = Config("alembic.ini")
    # Exercise the new migration on the preceding deployed revision.
    command.upgrade(config, "8c998de89757")
    command.upgrade(config, "a218_session_records")
    engine = create_engine(database_url)
    tables = inspect(engine).get_table_names()
    assert "adapter_sessions" in tables and "adapter_samples" in tables
    assert inspect(engine).get_pk_constraint("adapter_samples")["constrained_columns"] == ["session_id", "sequence"]
    assert inspect(engine).get_foreign_keys("adapter_samples")[0]["referred_table"] == "adapter_sessions"
    engine.dispose()
    command.downgrade(config, "8c998de89757")
    engine = create_engine(database_url)
    assert "adapter_sessions" not in inspect(engine).get_table_names()
    assert "adapter_samples" not in inspect(engine).get_table_names()
    engine.dispose()


@pytest.fixture
async def archive_batch(mounted_fastapi_client):

    now = datetime(2020, 1, 1, tzinfo=timezone.utc)
    declaration_id = uuid4()
    override = mounted_fastapi_client.app.dependency_overrides[async_get_db]()
    db = await anext(override)
    db.add(
        FlightDeclarationORM(
            id=declaration_id,
            aircraft_id="DJI-SERIAL",
            operational_intent="{}",
            bounds="0,0,1,1",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            state=5,
        )
    )
    await db.commit()
    await override.aclose()
    return {
        "session": {
            "schemaVersion": 2,
            "id": str(uuid4()),
            "source": "dji",
            "sourceData": {},
            "assignment": {
                "declarationID": str(declaration_id),
                "vehicleID": "DJI-SERIAL",
                "operatorID": "operator",
                "pilot": {"id": "p1", "name": "Pilot", "license": "LICENSE", "contact": "pilot@example.test"},
            },
            "startedAt": now.isoformat(),
            "endedAt": None,
            "revision": 1,
            "finalSampleCount": None,
        },
        "samples": [
            {
                "sequence": 0,
                "recordedAt": (now + timedelta(seconds=1)).isoformat(),
                "position": {
                    "latitude": 47.5,
                    "longitude": 19,
                    "relativeAltitude": 5,
                    "altitudeWGS84": 164,
                    "altitudeSource": "DEVICE_GNSS",
                    "altitudeSigma": 5,
                    "velocityNorth": 0,
                    "velocityEast": 2,
                    "velocityDown": 0,
                    "heading": 90,
                    "flying": True,
                    "motorsOn": True,
                },
                "sourceData": {"gps": 5, "mode": "P-GPS"},
            }
        ],
    }


@pytest.mark.parametrize("source", ["dji", "mavlink"])
def test_shared_archive_recovery_after_window_and_idempotent_restart(mounted_fastapi_client, archive_batch, source):
    c, b = mounted_fastapi_client, archive_batch
    b["session"]["source"] = source
    path = "/adapter_ops/sessions/" + b["session"]["id"]
    # Even an ended, expired declaration accepts historical samples. Lost responses
    # are retried identically without duplicate observations.
    for _ in range(2):
        r = c.put(path, json=b, headers=auth())
        assert r.status_code == 200, r.text
        assert r.json()["archive"] == {"complete": False, "receivedSampleCount": 1, "finalSampleCount": None}
    closed = deepcopy(b)
    closed["session"].update(revision=2, endedAt="2020-01-01T00:01:00Z", finalSampleCount=3)
    closed["samples"] = []
    assert c.put(path, json=closed, headers=auth()).json()["archive"]["complete"] is False
    # Out-of-order arrival leaves a gap; count alone cannot claim completion.
    pending = deepcopy(closed)
    pending["samples"] = [deepcopy(b["samples"][0])]
    pending["samples"][0]["sequence"] = 2
    assert c.put(path, json=pending, headers=auth()).json()["archive"]["complete"] is False
    pending["samples"][0]["sequence"] = 1
    for _ in range(2):
        r = c.put(path, json=pending, headers=auth())
        assert r.status_code == 200, r.text
        assert r.json()["archive"] == {"complete": True, "receivedSampleCount": 3, "finalSampleCount": 3}
    assert c.put(path, json=b, headers=auth()).json()["revision"] == 2
    fetched = c.get(path, headers=auth()).json()
    assert [s["sequence"] for s in fetched["samples"]] == [0, 1, 2]
    assert fetched["samples"][0]["sourceData"] == b["samples"][0]["sourceData"]
    assert fetched["archive"]["complete"]
    assert c.get(path + "?after=0&limit=1", headers=auth()).json()["samples"][0]["sequence"] == 1
    assert c.get(path, headers=auth(subject="other")).status_code == 404
    assert c.put(path, json=b, headers=auth(subject="other")).status_code == 403
    changed = deepcopy(pending)
    changed["samples"][0]["position"]["latitude"] = 48
    assert c.put(path, json=changed, headers=auth()).status_code == 409
    assert c.get(path, headers=auth()).json() == fetched


def test_archive_refuses_truncation_protocol_changes_and_invalid_samples(mounted_fastapi_client, archive_batch):
    c, b = mounted_fastapi_client, archive_batch
    path = "/adapter_ops/sessions/" + b["session"]["id"]
    assert c.put(path, json=b, headers=auth()).status_code == 200
    for changes in [dict(finalSampleCount=0, endedAt="2020-01-01T00:01:00Z"), dict(finalSampleCount=1, endedAt="2020-01-01T00:00:00Z")]:
        changed = deepcopy(b)
        changed["session"].update(revision=2, **changes)
        changed["samples"] = []
        assert c.put(path, json=changed, headers=auth()).status_code == 409
    changed = deepcopy(b)
    changed["session"]["assignment"]["pilot"]["name"] = "Changed"
    assert c.put(path, json=changed, headers=auth()).status_code == 409
    changed = batch()
    changed["session"]["id"] = b["session"]["id"]
    assert c.put(path, json=changed, headers=auth()).status_code == 409
    changed = deepcopy(b)
    changed["samples"][0]["sequence"] = -1
    assert c.put(path, json=changed, headers=auth()).status_code == 422
    changed = deepcopy(b)
    changed["samples"] = [dict(deepcopy(b["samples"][0]), sequence=i) for i in range(201)]
    assert c.put(path, json=changed, headers=auth()).status_code == 422
    changed = deepcopy(b)
    changed["session"]["id"] = str(uuid4())
    changed["session"]["assignment"]["declarationID"] = str(uuid4())
    assert c.put("/adapter_ops/sessions/" + changed["session"]["id"], json=changed, headers=auth()).status_code == 409
    assert len(c.get(path, headers=auth()).json()["samples"]) == 1


async def test_archive_never_acknowledges_failed_commit(mounted_fastapi_client, archive_batch):
    app = mounted_fastapi_client.app
    previous = app.dependency_overrides[async_get_db]

    async def failed_commit():
        original = previous()
        db = await anext(original)
        try:
            yield db
            raise RuntimeError("Injected transaction commit failure")
        finally:
            await original.aclose()

    app.dependency_overrides[async_get_db] = failed_commit
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.put("/adapter_ops/sessions/" + archive_batch["session"]["id"], json=archive_batch, headers=auth())
        assert response.status_code == 500
    finally:
        app.dependency_overrides[async_get_db] = previous
