# Flight Blender — Agent & Contributor Guide

Flight Blender is a UTM (Unmanned Traffic Management) API implementing ASTM F3548-21.
Stack: **FastAPI + SQLAlchemy 2.0 (async) + Celery + Redis**. Django has been fully removed.

---

## Directory Layout

Every file belongs in exactly one of these locations. No other locations are valid.

```
src/flight_blender/
├── config.py                              # pydantic-settings — ALL env vars
├── asgi.py                                # one-liner: application = create_fastapi_app()
├── celery.py                              # Celery app; explicit include= list
│
├── auth/                                  # JWT validation + credential helpers
│   ├── jwt_validator.py                   # async JWT + scope validation
│   ├── dss_auth.py                        # DSS authority credential fetcher
│   ├── pki.py                             # PKI / certificate helpers
│   ├── token_cache.py                     # Redis-backed token storage
│   └── token_audience.py                  # audience URL derivation
│
├── db/
│   └── session.py                         # async_get_db, AsyncSessionLocal, Base
│
├── models/                                # SQLAlchemy ORM models (suffix: _orm)
│   └── <domain>_orm.py
│
├── repositories/                          # Concrete SA repos, no Protocol (suffix: _repo)
│   └── <domain>_repo.py                   # async + sync classes; flush not commit
│
├── domain_types/                          # Pure domain dataclasses for ASTM domains
│   ├── common.py                          # shared scopes, constants
│   ├── rid.py                             # ASTM RID wire types
│   ├── rid_operations.py                  # operational RID types (Cluster, ISA, etc.)
│   ├── scd.py                             # ASTM SCD/USS types
│   ├── surveillance.py                    # surveillance domain types
│   └── <domain>.py                        # other domain types
│
├── schemas/                               # Pydantic HTTP request/response models
│   └── <domain>.py                        # never imported by repositories or services
│
├── services/                              # Business logic (suffix: _svc)
│   └── <domain>_svc.py                    # async; receives concrete repo via constructor
│
├── tasks/                                 # Celery task definitions (suffix: _task)
│   ├── <domain>_task.py
│   └── scheduler.py                       # TaskSchedulerService
│
├── clients/                               # External HTTP + Redis clients (suffix: _client)
│   ├── dss_rid_client.py
│   ├── dss_scd_client.py
│   ├── dss_conformance_client.py
│   ├── dss_constraint_client.py
│   ├── weather_client.py
│   ├── redis_client.py
│   └── notification_client.py
│
├── utils/                                 # Pure computation helpers, no I/O
│   ├── spatial_flight_declarations.py
│   ├── spatial_geo_fence.py
│   ├── spatial_rid.py
│   └── json_codecs.py
│
├── api/
│   ├── main.py                            # create_fastapi_app() factory
│   ├── dependencies.py                    # require_scopes, async_get_db Depends() helpers
│   └── routers/                           # HTTP boundary (suffix: _api)
│       └── <domain>_api.py
│
├── plugins/                               # Plugin loader + examples (unchanged)
└── alembic/                               # DB migrations
    ├── env.py
    └── versions/
```

### File suffix conventions

| Location | Suffix | Example |
|----------|--------|---------|
| `api/routers/` | `_api` | `geo_fence_api.py` |
| `models/` | `_orm` | `geo_fence_orm.py` |
| `repositories/` | `_repo` | `geo_fence_repo.py` |
| `services/` | `_svc` | `geo_fence_svc.py` |
| `tasks/` | `_task` | `geo_fence_task.py` |
| `clients/` | `_client` | `dss_rid_client.py` |

---

## Import Hierarchy (strictly one-way)

```
models → repositories → services → api/routers
clients ─────────────────────────↗
utils ───────────────────────────↗
auth ────────────────────────────↗ (via api/dependencies.py)
domain_types ────────────────────↗ (used by all layers)
```

No Protocol layer. Services receive concrete repo instances via constructor. Router wires it:

```python
# api/routers/geo_fence_api.py
@router.get("/")
async def list_geofences(
    db: AsyncSession = Depends(async_get_db),
    _auth = Depends(require_scopes(["flightblender.read"])),
):
    svc = GeoFenceOperations(GeoFenceRepo(db))
    return await svc.list_active()
```

### Import rule enforcement

The layer rule is enforced by `tests/test_import_architecture.py`. This test runs as part of the normal pytest suite. It checks:
- `models/` imports nothing from repositories, services, api, tasks, clients
- `repositories/` imports nothing from services or api
- `services/` imports nothing from api
- `clients/` imports nothing from api or services
- `utils/` imports nothing from api, services, or tasks
- `schemas/` imports nothing from api, services, tasks, or repositories

**Violations caught by the test must be fixed before merging.**

Illegal imports:
| Illegal | Why forbidden |
|---------|--------------|
| `repositories/` → `services/` | repositories are below services |
| `clients/` → `services/` | clients are a peer layer; domain types belong in `domain_types/` |
| `services/` → `api/` | services are below api |
| any layer → `api/routers/` | routers are the top |

---

## Three Object Types

Every value in the system is exactly one of these:

| Type | Where | Rule |
|------|-------|------|
| **ORM** (SQLAlchemy model) | `models/*_orm.py` | Never leaves the repository. Repo converts it to domain type before returning. |
| **Domain type** (dataclass/NamedTuple) | `domain_types/*.py` | Used within services and passed to clients. Not exposed directly via HTTP. |
| **Schema** (Pydantic BaseModel) | `schemas/*.py` | HTTP boundary only. Router parses request → schema → service call → schema → response. |

Simple domains (notifications, flight_feed, surveillance, weather) may use schemas directly without a domain type intermediary.

---

## Session / Transaction Contract

| Context | Session source | Pattern |
|---------|---------------|---------|
| FastAPI endpoint | `async_get_db` via `Depends()` → `AsyncSession` | auto-commit on success, rollback on exception |
| Celery task | `session_scope()` from `infrastructure.database.session` | sync context manager; TODO replace with `asyncio.run()` |

Tasks currently use the legacy sync session via `SyncDatabaseFacade`. This is preserved intentionally — migration to async repos + `asyncio.run()` is a separate workstream.

Target pattern (not yet implemented for most tasks):

```python
# tasks/geo_fence_task.py
@app.task(name="process_geofence")
def process_geofence(payload: dict) -> None:
    asyncio.run(_run(payload))

async def _run(payload: dict) -> None:
    async with AsyncSessionLocal() as db:
        await GeoFenceOperations(GeoFenceRepo(db)).process(payload)
```

---

## Auth Pattern

All endpoints require a JWT bearer token. `BYPASS_AUTH_TOKEN_VERIFICATION=True` in `.env` disables validation (tests only).

```python
@router.get("/resource")
async def list_resource(
    ops: MyOps = Depends(_ops),
    _auth = Depends(require_scopes(["flightblender.read"])),
):
    return await ops.list()
```

JWT validation lives in `auth/jwt_validator.py`. DSS credential fetching is in `auth/dss_auth.py`. Each domain's test file must include one `TestXxxAuthEnforcement` class that sets `BYPASS_AUTH_TOKEN_VERIFICATION = False` and asserts 401 on missing token, 403 on wrong scope.

---

## ORM Model Rules

- Each `_orm.py` file imports `Base` from `flight_blender.db.session`.
- `__tablename__` must match the historical Django table name to avoid data migrations.
- `ForeignKey("...")` strings use the target's `__tablename__` value.
- Repos call `session.flush()` — never `session.commit()`. Commit is owned by `async_get_db`.

---

## Alembic

- `alembic.ini` at project root. `version_table = "alembic_version"`.
- All schema changes go through Alembic. No Django migrations.
- Fresh install: `alembic upgrade head`.
- Existing DB: `alembic stamp head` once, then `alembic upgrade head` per migration.

---

## Testing

```python
# conftest.py pattern — in-memory SQLite, both old and new Base tables
@pytest.fixture
async def mounted_fastapi_client():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", ...)
    async with test_engine.begin() as conn:
        await conn.run_sync(OldBase.metadata.create_all)
        await conn.run_sync(Base.metadata.create_all)
    app = create_fastapi_app()
    app.dependency_overrides[async_get_db] = override_get_db
    app.dependency_overrides[old_async_get_db] = override_get_db
    with TestClient(app) as c:
        yield c
```

- No `pytest-django`. No `@pytest.mark.django_db`.
- DB isolation: each test fixture creates an in-memory SQLite engine.
- Patch targets use the module where the name is **used**, not where it's defined.
  - New routers patch `flight_blender.auth.jwt_validator._fetch_jwks` (not old infrastructure path)
  - New SCD fixtures patch `flight_blender.clients.dss_scd_client.SCDOperations`
- `BYPASS_AUTH_TOKEN_VERIFICATION=1` in `pyproject.toml` `[tool.pytest.ini_options] env`. One class per domain verifies real auth.

---

## Config

All env vars in `config.py` (`FlightBlenderSettings(BaseSettings)`). Never read `os.environ` directly.

```
DATABASE_URL                              REDIS_BROKER_URL
SECRET_KEY                                BYPASS_AUTH_TOKEN_VERIFICATION
PASSPORT_AUDIENCE                         PASSPORT_URL / PASSPORT_JWKS_URL
USSP_NETWORK_ENABLED                      IS_DEBUG
FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE
FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER
FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR
```

---

## Domain Map

| Domain | Router prefix | Tier |
|--------|--------------|------|
| geo_fence | `/geo_fence_ops` | 1 |
| surveillance | `/surveillance_monitoring_ops` | 1 |
| weather | `/weather_monitoring_ops` | 1 |
| notifications | `/notifications_ops` | 1 |
| flight_feed | `/flight_stream` | 2 |
| constraint | `/constraint_ops` | 2 |
| rid | `/rid` | 3 |
| conformance | `/conformance_monitoring_ops` | 3 |
| flight_declarations | `/flight_declaration_ops` | 4 |
| scd | `/scd` | 4 |
| uss | `/uss` | 4 |
| realtime (WebSocket) | `/realtime` | — |

---

## Known Preserved Bugs

Do not fix without a product decision.

- `_calculate_track_update_probability` (`services/surveillance_svc.py`): `probability = total/total` always 1.0 when observations exist. Matches original behaviour.
- `SurveillanceSensortHealthTracking` table name has typo ("Sensort") — preserved to match live DB.
- `set_signed_telemetry` (`api/routers/flight_feed_api.py`): `MessageVerifier` needs raw bytes + headers dict. Fix requires refactoring `MessageVerifier`.

---

## End-to-End Verification

```bash
uv run pytest -x --tb=short

# Import layer enforcement (also runs as part of pytest)
rg '^from flight_blender\.(api|services)' src/flight_blender/clients -n
rg '^from flight_blender\.api' src/flight_blender/services -n

uv run ruff format
uv run ruff check src/ --fix
uv run pyright src/
uvicorn flight_blender.asgi:application --port 8000
uv run celery -A flight_blender.celery worker --loglevel=info
```
