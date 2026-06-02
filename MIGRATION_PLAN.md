# Staggered Django → FastAPI Migration Plan

**Principle:** Test first, structure second, framework last. Each phase is a working checkpoint.

---

## Phase 0: Pre-flight (on `master`)

**Goal:** Snapshot current Django behavior with real HTTP tests.

- [ ] **0.1** Add a `conftest.py` with a Django test client that hits real endpoints (no function-level mocks)
- [ ] **0.2** Write integration tests for each Django app's endpoints:
  - `rid_operations` — `display_data`, `display_data/{id}`, `create_dss_subscription`, `tests/{id}`, etc.
  - `scd_operations` — flight planning, clear area, status/capabilities
  - `flight_declaration_operations` — CRUD, state transitions, bulk create, DSS submission
  - `geo_fence_operations` — CRUD, geo-awareness harness, map queries
  - `surveillance_monitoring_operations` — sensors, health, metrics, heartbeat sessions
  - `conformance_monitoring_operations` — records, status, summary
  - `uss_operations` — operational intents, constraints, reports, telemetry
  - `detect_and_avoid_operations` — alerts, incident logs
  - `weather_monitoring_operations` — weather endpoint
  - `constraint_operations` — constraint CRUD
  - `notification_operations` — notification persistence
- [ ] **0.3** Run tests against Django — all green = behavior baseline captured
- [ ] **0.4** Commit tests to `master`

**Key constraint:** Tests must NOT mock Python functions. They use `httpx` against a running Django test server. This ensures the same tests can run against FastAPI later.
Make sure we need only minor changes to these tests during the migration.
Produce high coverage, through integration level, so no mocking.
Use the `tests/` directory with pytest approach.
---

## Phase 1: Shared utilities extraction (still on `master`)

**Goal:** Extract reusable logic into `common/` without changing any endpoints.

- [ ] **1.1** Extract `common/geometry.py` — `point_in_polygon`, `bounds_contains_point`, `compute_bounds`
- [ ] **1.2** Extract `common/datetime_utils.py` — `parse_iso_utc`, `ensure_utc`
- [ ] **1.3** Extract `common/auth.py` → move `get_dss_auth_header` into `auth/dss.py`
- [ ] **1.4** Extract `common/sync_engine.py` — `get_sync_engine` with `@lru_cache`
- [ ] **1.5** Extract `common/redis_client.py` — `get_redis`, `get_async_redis`
- [ ] **1.6** Extract `common/enums.py` — `OperationState`, `ACTIVE_OPERATIONAL_STATES`, etc.
- [ ] **1.7** Update Django views to import from `common/` instead of duplicating
- [ ] **1.8** Run Phase 0 tests — all still green

---

## Phase 2: Model & DB alignment (still on `master`)

**Goal:** Unify database models so they work with both Django ORM and SQLAlchemy.

- [ ] **2.1** Create `models/` directory with SQLAlchemy model definitions (can coexist with Django ORM initially)
- [ ] **2.2** Add `database.py` with async engine setup, `get_db` dependency
- [ ] **2.3** Verify Phase 0 tests still pass (Django ORM still active)

---

## Phase 3: FastAPI app skeleton (new branch from `master`)

**Goal:** Add FastAPI alongside Django — both run, tests pass against both.

- [ ] **3.1** Add `main.py` with `create_app()`, mount routers
- [ ] **3.2** Port **one simple router** (e.g., `health`, `weather`) as proof of concept
- [ ] **3.3** Run Phase 0 tests against FastAPI — verify parity for the ported endpoints
- [ ] **3.4** Add FastAPI-specific `conftest.py` with `httpx.AsyncClient` fixture
- [ ] **3.5** Run all tests against both Django and FastAPI in CI

---

## Phase 4: Incremental endpoint migration (one router at a time)

**Goal:** Port each Django app to a FastAPI router, verifying with Phase 0 tests after each.

Migration order (simplest → most complex, dependencies respected):

1. [ ] **4.1** `weather` — simple, no DB
2. [ ] **4.2** `flight_feed` — observation CRUD, Redis stream
3. [ ] **4.3** `rid` — RID display, subscriptions, tests
4. [ ] **4.4** `surveillance` — sensors, health, heartbeat
5. [ ] **4.5** `geo_fence` — CRUD, geo-awareness harness, parsing
6. [ ] **4.6** `constraint` — constraint CRUD
7. [ ] **4.7** `conformance` — records, status, state machine
8. [ ] **4.8** `daa` — alerts, incident logs
9. [ ] **4.9** `notification` — notification persistence
10. [ ] **4.10** `flight_declaration` — CRUD, state transitions, bulk create, deconfliction
11. [ ] **4.11** `scd` — flight planning, strategic deconfliction, DSS integration
12. [ ] **4.12** `uss` — operational intents, constraints, telemetry, notifications
13. [ ] **4.13** `utm_adapter` — network RID, flight declarations

For each router:
- Port the Django view logic to FastAPI endpoint
- Run Phase 0 tests — must all pass
- Fix any regressions before moving to next router
- Mark the Django app as "ported" (comment out in `urls.py`)

---

## Phase 5: Cut over & cleanup

**Goal:** Remove Django, FastAPI is the only framework.

- [ ] **5.1** Remove Django apps from `urls.py` (one at a time, tests pass after each)
- [ ] **5.2** Remove Django-specific code (DRF serializers, Django views, `manage.py`)
- [ ] **5.3** Remove Django dependencies from `pyproject.toml`
- [ ] **5.4** Update Dockerfile, docker-compose for FastAPI/uvicorn
- [ ] **5.5** Run full test suite — all green
- [ ] **5.6** Run lint/typecheck — clean

---

## Phase 6: Production hardening

**Goal:** Make it production-ready.

- [ ] **6.1** Add pagination to all list endpoints
- [ ] **6.2** Add transactional safety to multi-step operations
- [ ] **6.3** Fix N+1 queries
- [ ] **6.4** Add proper error handling and logging
- [ ] **6.5** Security audit (CORS, auth, input validation)
- [ ] **6.6** Performance testing

---

## Test design principles

```python
# tests/conftest.py — NO function-level mocks
# Only infrastructure mocks (DB, Redis, external HTTP)

@pytest.fixture
async def client():
    """HTTP client against the running app (Django or FastAPI)."""
    # Uses httpx against the test server
    # DB is overridden to use test SQLite
    # Redis is overridden to use fakeredis
    # External HTTP calls are mocked at the network level (responses/httpretty)
    # BUT: no Python function mocking — the actual view/route code runs end-to-end
```

This ensures that when you port `rid_operations/views.py` → `routers/rid.py`, the same test catches any behavioral difference.
