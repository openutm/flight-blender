"""
Deployment regression guard: the Celery **beat** scheduler must be enabled in
the production compose file. Without a beat process, no scheduled task ever runs
(heartbeat cleanup, and — once wired — periodic conformance/DSS reconciliation),
even though tasks are defined. The beat service had been commented out.
"""

import pathlib

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE = pathlib.Path(__file__).resolve().parents[1] / "docker-compose.yml"
BEAT_SERVICE = "flight-blender-celery-beat"


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text()).get("services", {})


def test_beat_service_enabled_in_prod_compose():
    services = _services()
    assert BEAT_SERVICE in services, f"{BEAT_SERVICE} must be enabled (uncommented) in docker-compose.yml"


def test_beat_runs_the_beat_entrypoint():
    beat = _services()[BEAT_SERVICE]
    assert "entrypoint-beat.sh" in beat["command"], "beat service must run the beat entrypoint"


def test_beat_depends_on_db_and_redis():
    beat = _services()[BEAT_SERVICE]
    deps = beat.get("depends_on", {})
    dep_names = set(deps) if isinstance(deps, (list, dict)) else set()
    assert {"redis-blender", "db-blender"} <= dep_names, "beat must wait for redis and db"
