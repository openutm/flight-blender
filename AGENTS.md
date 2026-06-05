# Flight Blender — Agent & Contributor Guide

Flight Blender is a UTM (Unmanned Traffic Management) API implementing ASTM F3548-21.
Stack: **FastAPI + SQLAlchemy 2.0 (async) + Celery + Redis**. Django has been fully removed.

---

## Target Directory Layout

Every file belongs in exactly one of these locations. No other locations are valid.

```
src/flight_blender/
├── config.py                              # pydantic-settings — ALL env vars
├── asgi.py                                # one-liner: application = create_fastapi_app()
├── celery.py                              # Celery app; explicit include= list
│
├── core/                                  # ZERO framework imports in this entire subtree
│   ├── entities/
│   │   └── <domain>.py                   # dataclasses / Pydantic models for domain types
│   ├── repositories/
│   │   └── <domain>.py                   # typing.Protocol interfaces — no ORM, no SA, no HTTP
│   └── operations/
│       └── <domain>.py                   # business logic — async, uses entities + repo protocols
│
├── infrastructure/
│   ├── database/
│   │   ├── session.py                    # async_get_db (FastAPI), session_scope() (Celery)
│   │   ├── models/
│   │   │   └── <domain>.py              # SQLAlchemy ORM models
│   │   ├── repositories/
│   │   │   ├── sa_<domain>.py           # AsyncSession repo + sync repo per domain
│   │   │   └── sync_facade.py           # SA-backed sync replacement for the old DB god-object
│   │   └── alembic/                     # env.py + versions/
│   ├── auth/
│   │   └── jwt_validator.py             # async JWT + scope validation
│   ├── dss/
│   │   └── <domain>.py                  # DSS HTTP clients (dss_rid_helper, dss_scd_helper, etc.)
│   ├── spatial/
│   │   └── <domain>.py                  # rtree helpers, buffer helpers, geo utils
│   └── celery/
│       ├── *_dispatcher.py              # Celery-backed dispatch adapters injected into core ops
│       ├── task_scheduler.py            # TaskSchedulerService (conformance beat scheduling)
│       └── tasks/
│           └── <domain>.py             # Celery task definitions (one file per domain)
│
└── api/
    ├── main.py                          # create_fastapi_app() factory
    ├── dependencies.py                  # require_scopes, async_get_db Depends() helpers
    ├── schemas/
    │   └── <domain>.py                  # Pydantic request/response models (HTTP layer only)
    └── routers/
        └── <domain>.py                  # HTTP boundary: parse → call ops → return
```

### What is NOT a valid location

- `<domain>/data_definitions.py` — move contents to `core/entities/<domain>.py`
- `<domain>/*_helper.py` — move to `core/operations/<domain>.py` or `infrastructure/dss/<domain>.py`
- `<domain>/utils.py` — move to `core/operations/<domain>.py` (pure logic) or `infrastructure/` (I/O)
- `<domain>/tasks.py` — move to `infrastructure/celery/tasks/<domain>.py`
- `<domain>/rtree_*`, `<domain>/buffer_*` — move to `infrastructure/spatial/<domain>.py`
- `<domain>/pki_helper.py` — move to `infrastructure/auth/`
- `common/database_operations.py` — delete after deletion gate passes (see Remaining Tasks)
- Any file in a bare `<domain>/` package that does not fit the target layout above

---

## Import Hierarchy (strictly one-way)

```
config.py
    ↓
core/entities/          imports: stdlib, pydantic, config
    ↓
core/repositories/      imports: core/entities, config
    ↓
core/operations/        imports: core/entities, core/repositories, config
    ↓
infrastructure/         imports: core/*, config      ← never imports from api/
    ↓
api/schemas/            imports: core/entities, config
    ↓
api/dependencies/       imports: core/*, infrastructure/*, config
    ↓
api/routers/            imports: api/schemas, core/operations, infrastructure/*, config
```

**Violations that cause circular imports — never do these:**

| Illegal import | Why forbidden |
|----------------|--------------|
| `core/operations/` → `api/schemas/` | ops is below api; creates cycle if router imports ops |
| `core/repositories/` → `<domain>/data_definitions.py` | entities must live in `core/entities/`, not domain dirs |
| `infrastructure/` → `api/` | infra is below api |
| `api/routers/` → `<domain>/` helpers directly | bypasses ops layer; creates dep on deleted domain dirs |
| `core/` → `infrastructure/` | core must be framework-free; infra implements core protocols |

**The only upward reference allowed:** lazy (inline) imports in `infrastructure/` or `api/routers/` to break circular app-load-order issues. These must be documented with a comment explaining why. `core/` never has lazy imports.

---

## Architecture Layers

### `core/entities/<domain>.py`

Dataclasses or `pydantic.BaseModel` subclasses representing domain concepts. No SQLAlchemy, no FastAPI, no HTTP status codes. Shared by operations, repository protocols, and (read-only) infrastructure.

### `core/repositories/<domain>.py`

`typing.Protocol` interfaces only. Each method signature uses `core/entities` types, stdlib, and nothing else. Concrete implementations live in `infrastructure/database/repositories/`.

```python
from typing import Protocol, runtime_checkable
from flight_blender.core.entities.geo_fence import GeofencePayload

@runtime_checkable
class GeoFenceRepository(Protocol):
    def get_active_geofences(self) -> list[GeofencePayload]: ...
```

### `core/operations/<domain>.py`

Business logic. Async. Receives a repo protocol via `__init__`. Never imports SQLAlchemy, FastAPI, or HTTP types. Returns plain dicts, entity objects, or `(result, status_int)` tuples — never `JSONResponse`.
Operations that need side effects such as Celery task dispatch, spatial indexes, validators, DSS calls, or external HTTP clients receive those capabilities as protocol-typed constructor dependencies. The concrete adapter lives in `infrastructure/` and is wired in `api/routers/<domain>.py`.

### `infrastructure/database/repositories/sa_<domain>.py`

Implements the repo protocol with `AsyncSession` (for FastAPI) and a matching sync class (for Celery). Calls `session.flush()` — never `session.commit()`. Does not catch exceptions; lets them propagate for transaction control.

### `api/schemas/<domain>.py`

All `BaseModel` request/response classes. Never imported by `core/`. Configuration:
- `ConfigDict(extra="ignore")` on input schemas
- `Literal` for fixed-choice fields (removes runtime guard in ops)
- Optional patch schemas: `field: type | None = None` + `model_dump(exclude_none=True)`

### `api/routers/<domain>.py`

HTTP boundary. Three lines per handler: parse, delegate, return. No business logic.
- Typed body parameter, not `Request` + `await request.json()` (exception: `set_signed_telemetry` needs raw bytes)
- `HTTPException` for errors, never `JSONResponse({"message": ...})`
- Validation errors → 422 automatically via Pydantic; no manual guards duplicating schema constraints

---

## Session / Transaction Contract

| Context | Session source | Pattern |
|---------|---------------|---------|
| FastAPI endpoint | `async_get_db` via `Depends()` → `AsyncSession` | auto-commit on success, rollback on exception |
| Celery task | `session_scope()` context manager | `with session_scope() as db: repo = SyncRepo(db); repo.write(...)` |

Never use `db = SessionLocal(); try/except/finally` in tasks. Use `session_scope()`.

---

## ORM Model Rules

- `__tablename__` must **exactly** match Django's generated name: `<app_label>_<modelname_lower>`.
- `app_label` comes from `apps.py` `label =` field — not the Python module name. Always read `apps.py`.
  - Example: `label = "surveillance_monitoring_operations"` → tables are `surveillance_monitoring_operations_*`.
- PostgreSQL max identifier = 63 chars. Django truncates: first 59 chars + 4-char hex hash of full name. Query the live DB or run `python manage.py sqlmigrate` to get the exact truncated name. Hardcode it.
- `ForeignKey("...")` strings must use the target's `__tablename__` value, not the class name.
- `auto_now_add=True` Django field → SA model needs `default=lambda: datetime.now(timezone.utc)`.

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

Each domain's test file must include one `TestXxxAuthEnforcement` class that sets `BYPASS_AUTH_TOKEN_VERIFICATION = False` and asserts 401 on missing token, 403 on wrong scope.

---

## Celery Pattern

Tasks live in `infrastructure/celery/tasks/<domain>.py`. All imports at module top.

```python
from flight_blender.infrastructure.database.session import session_scope
from flight_blender.infrastructure.database.repositories.sa_flight_feed import SQLAlchemyFlightFeedSyncRepository

@app.task(name="write_observation")
def write_observation(payload: dict) -> None:
    with session_scope() as db:
        repo = SQLAlchemyFlightFeedSyncRepository(db)
        repo.write_flight_observation(payload)
```

When an import is hoisted from inline to module-level, update test `patch()` targets from the source module path to the tasks module path (`"flight_blender.infrastructure.celery.tasks.<domain>.SyncRepoClass"`).

---

## Alembic

- `alembic.ini` at project root. `version_table = "alembic_version"`.
- All schema changes go through Alembic. No Django migrations.
- Fresh install: `alembic upgrade head`.
- Existing DB: `alembic stamp head` once, then `alembic upgrade head` per migration.

---

## Testing

```python
@pytest.fixture
def fastapi_client():
    from fastapi.testclient import TestClient
    from flight_blender.api.main import create_fastapi_app
    with TestClient(create_fastapi_app(), raise_server_exceptions=True) as c:
        yield c
```

- No `pytest-django`. No `@pytest.mark.django_db`.
- DB isolation: SQLAlchemy session-based (pytest-alembic).
- Patch targets use the module where the name is imported, not where it's defined.
- `BYPASS_AUTH_TOKEN_VERIFICATION=True` in test `.env`. One class per domain verifies real auth.

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

- `_calculate_track_update_probability` (`core/operations/surveillance.py`): `probability = total/total` always 1.0 when observations exist. Matches original behaviour.
- `SurveillanceSensortHealthTracking` table name has typo ("Sensort") — preserved to match live DB.
- `set_signed_telemetry` (`api/routers/flight_feed.py`): `MessageVerifier` needs raw bytes + headers dict. Fix requires refactoring `MessageVerifier`; do not wrap in Django request objects.

---

## Remaining Tasks

### 1. Keep `core/` import-clean

**Status: PASSING.** Both gates return clean. Only allowed import is `flight_blender.config`.

Run these gates after any core-layer change:

```bash
rg '^from flight_blender\.(api|infrastructure)|^import flight_blender\.(api|infrastructure)' \
   src/flight_blender/core -n
# Must return empty

rg '^from flight_blender\.[a-z_]+\.(data_definitions|rid_utils|tasks|.*helper)|^from flight_blender\.[a-z_]+ import' \
   src/flight_blender/core -n
# Must return only allowed config imports
```

Do not add new `core` imports from legacy domain packages. If core needs a capability, define a `typing.Protocol` in `core/repositories/` and inject an adapter from `infrastructure/` via `api/routers/`.

### 2. Delete `common/database_operations.py` (deletion gate)

**Status: DONE.** `common/database_operations.py` deleted. `FlightBlenderDatabaseReader` / `FlightBlenderDatabaseWriter` have zero remaining callers. Gate passes.

```bash
grep -r "FlightBlenderDatabaseReader\|FlightBlenderDatabaseWriter\|from.*database_operations" \
     src/ tests/ --include="*.py" | grep -v database_operations.py
# Returns empty — gate passes
```

### 3. Relocate domain helper files

Files already moved (targets exist, old files deleted):

| Done | Target |
|------|--------|
| `rid/rtree_helper.py` | `infrastructure/spatial/rid.py` ✓ |
| `flight_declarations/flight_declarations_rtree_helper.py` | `infrastructure/spatial/flight_declarations.py` ✓ |
| `rid/dss_rid_helper.py` | `infrastructure/dss/rid.py` ✓ |
| `scd/dss_scd_helper.py` | `infrastructure/dss/scd.py` ✓ |
| `constraint/dss_constraints_helper.py` | `infrastructure/dss/constraint.py` ✓ |
| `flight_feed/pki_helper.py` | `infrastructure/auth/pki_helper.py` ✓ |
| `notifications/notification_helper.py` | `infrastructure/messaging/notification_helper.py` ✓ |
| `rid/tasks.py`, `geo_fence/tasks.py`, `flight_feed/tasks.py`, `surveillance/tasks.py`, `conformance/tasks.py`, `flight_declarations/tasks.py` | `infrastructure/celery/tasks/<domain>.py` ✓ |

**Still to move** — files in bare domain packages that must reach the target layout:

#### SCD (tier 4)

| Current | Target |
|---------|--------|
| `scd/opint_helper.py` | pure logic → `core/operations/scd.py`; I/O → `infrastructure/dss/scd.py` |
| `scd/scd_test_harness_helper.py` | same split |
| `scd/utils.py` | same split |
| `scd/data_definitions.py`, `scd/scd_data_definitions.py`, `scd/flight_planning_data_definitions.py` | consolidate into `core/entities/scd.py` (create if missing) |

#### Conformance (tier 3)

| Current | Target |
|---------|--------|
| `conformance/conformance_checks_handler.py` | `core/operations/conformance.py` |
| `conformance/conformance_state_helper.py` | `core/operations/conformance.py` |
| `conformance/operation_state_helper.py` | `core/operations/conformance.py` |
| `conformance/data_helper.py` | `core/operations/conformance.py` |
| `conformance/dss_handlers.py` | `infrastructure/dss/conformance.py` (does I/O) |
| `conformance/operator_conformance_notifications.py` | `core/operations/conformance.py` or `infrastructure/messaging/` if sends HTTP |
| `conformance/custom_signals.py` | `core/operations/conformance.py` (pure) |
| `conformance/utils.py` | `core/operations/conformance.py` (pure) |
| `conformance/data_definitions.py` | merge into `core/entities/conformance.py` |

#### Flight Declarations (tier 4)

| Current | Target |
|---------|--------|
| `flight_declarations/deconfliction_protocol.py` | `core/operations/flight_declarations.py` (Protocol definition) |
| `flight_declarations/deconfliction_engine.py` | `core/operations/flight_declarations.py` (pure) or `infrastructure/` (if I/O) |
| `flight_declarations/example_deconfliction_engine.py` | `plugins/examples/` |
| `flight_declarations/custom_volume_generation.py` | `core/operations/flight_declarations.py` (pure) or `infrastructure/spatial/flight_declarations.py` (geo) |
| `flight_declarations/custom_utils.py`, `flight_declarations/utils.py` | `core/operations/flight_declarations.py` (pure) |
| `flight_declarations/data_definitions.py` | merge into `core/entities/flight_declarations.py` |

#### RID (tier 3)

| Current | Target |
|---------|--------|
| `rid/rid_telemetry_monitoring.py` | `core/operations/rid.py` (pure) or `infrastructure/` (if I/O) |
| `rid/rid_utils.py` | `core/operations/rid.py` |
| `rid/view_port_ops.py` | `core/operations/rid.py` |
| `rid/data_definitions.py` | merge into `core/entities/rid.py` |

#### Flight Feed (tier 2)

| Current | Target |
|---------|--------|
| `flight_feed/flight_stream_helper.py` | `core/operations/flight_feed.py` (pure) or `infrastructure/` (if I/O) |
| `flight_feed/rid_telemetry_helper.py` | `core/operations/flight_feed.py` or `infrastructure/dss/rid.py` |
| `flight_feed/data_definitions.py` | merge into `core/entities/flight_feed.py` |

#### Surveillance (tier 1)

| Current | Target |
|---------|--------|
| `surveillance/metric_calculator.py` | `core/operations/surveillance.py` |
| `surveillance/traffic_data_fuser_protocol.py` | `core/repositories/surveillance.py` (Protocol) |
| `surveillance/custom_utils.py`, `surveillance/utils.py` | `core/operations/surveillance.py` |
| `surveillance/custom_signals.py` | `core/operations/surveillance.py` (pure) |
| `surveillance/data_definitions.py` | merge into `core/entities/surveillance.py` |

#### Geo Fence (tier 1)

| Current | Target |
|---------|--------|
| `geo_fence/rtree_geo_fence_helper.py` | absorb into `infrastructure/spatial/geo_fence.py` |
| `geo_fence/buffer_helper.py` | absorb into `infrastructure/spatial/geo_fence.py` |
| `geo_fence/common.py` | `core/operations/geo_fence.py` (pure) |
| `geo_fence/data_definitions.py` | merge into `core/entities/geo_fence.py` |

#### Constraint (tier 2)

| Current | Target |
|---------|--------|
| `constraint/constraints_helper.py` | absorb into `infrastructure/dss/constraint.py` |
| `constraint/data_definitions.py` | merge into `core/entities/constraint.py` (create if missing) |

#### Notifications (tier 1)

| Current | Target |
|---------|--------|
| `notifications/data_definitions.py` | merge into `core/entities/notifications.py` |

#### USS (tier 4)

| Current | Target |
|---------|--------|
| `uss/rid_data_definitions.py`, `uss/uss_data_definitions.py` | merge into `core/entities/uss.py` (create if missing) |

#### Auth / Common (cross-cutting)

| Current | Target |
|---------|--------|
| `auth/dss_auth_helper.py` | `infrastructure/auth/` |
| `auth/common.py` | `infrastructure/auth/` |
| `common/altitude_helper.py` | `core/operations/` (pure) or `infrastructure/spatial/` (geo) |
| `common/auth_token_audience_helper.py` | `infrastructure/auth/` |
| `common/base_traffic_data_fuser.py` | `core/repositories/` (Protocol) or `infrastructure/` |
| `common/data_definitions.py` | split: entities → `core/entities/`, shared types stay until callers migrated |
| `common/dispatch.py` | `infrastructure/celery/` |
| `common/redis_stream_operations.py` | `infrastructure/` (I/O) |
| `common/utils.py` | `core/operations/` (pure) or split |

After each move: fix all imports, run `uv run pytest -x`, delete old file.

### 4. Delete dead legacy files

**Status: DONE.** All files confirmed deleted:
`uss/views.py`, `surveillance/views.py`, `flight_feed/views.py`, `flight_feed/serializers.py`,
`flight_declarations/serializers.py`, `flight_declarations/pagination.py`, `scd/views.py`, `conformance/views.py`.

### 5. Django admin decision

No Django in the stack — admin is gone. Choose: `fastapi-admin`, minimal custom UI, or drop.

---

## End-to-End Verification

```bash
uv run pytest -x --tb=short
uv run ruff format
uv run ruff check src/ --fix
uv run pyright src/
uvicorn flight_blender.asgi:application --port 8000
uv run celery -A flight_blender.celery worker --loglevel=info

# Core import gates (Task 1)
rg '^from flight_blender\.(api|infrastructure)|^import flight_blender\.(api|infrastructure)' \
   src/flight_blender/core -n
rg '^from flight_blender\.[a-z_]+\.(data_definitions|rid_utils|tasks|.*helper)|^from flight_blender\.[a-z_]+ import' \
   src/flight_blender/core -n
```
