---
description: Run a verification scenario locally against the current branch using Docker, or debug a failing scenario by hitting the live endpoint directly.
---

# Run / debug a verification scenario locally

Verification scenarios live in the sibling repo at `../verification/` relative to this repo (`flight-blender`).  The CI workflow (`verification.yml`) documents the canonical steps; this skill mirrors them for local use.

## Assumptions

- Both repos checked out at the same level: `openutm/flight-blender/` and `openutm/verification/`
- Docker running; the compose stack creates its own `tests_default` network automatically
- Working directory for all commands is the **flight-blender repo root** unless noted
- **macOS Apple Silicon**: the compose file forces `platform: linux/amd64` on all services, so you must build and pull amd64 images (they run via Rosetta). See step 1.
- No pre-existing containers named `db-blender`, `redis-blender`, `flight-blender`, or `worker`. Stop and remove any before starting the stack (step 2 teardown command covers this).

---

## 1. Build the image from the current branch

Always rebuild before running scenarios so the container has the latest code.

**Linux / CI (amd64):**
```bash
docker build -t openutm/flight-blender .
```

**macOS Apple Silicon (arm64 host):** the compose file forces `linux/amd64`, so build for that platform and pull infrastructure images to match:
```bash
docker build --platform linux/amd64 -t openutm/flight-blender .
docker pull --platform linux/amd64 valkey/valkey:latest
docker pull --platform linux/amd64 postgres:latest
```

---

## 2. Start the stack

The compose file is in `verification/tests/`. It starts Postgres, Redis (Valkey), flight-blender (HTTP), and a Celery worker.

```bash
docker compose \
  --env-file ../verification/tests/.env.tests \
  -f ../verification/tests/docker-compose.fb.yml \
  up -d --wait --pull never
```

`--wait` blocks until the flight-blender healthcheck passes (`GET /ping` returns `{"message":"pong"}`).  If it times out, check logs with step 4.

---

## 3. Run the scenario suite

```bash
cd ../verification
uv run openutm-verify --debug --config config/fb_pr.yaml
```

To run a single suite (e.g. only `astm_f3623`):

```bash
uv run openutm-verify --debug --config config/fb_pr.yaml -s astm_f3623
```

Available suites in `config/fb_pr.yaml`: `basic_conformance`, `astm_f3623`, `air_traffic_simulations`, `extra`.

Reports land in `verification/reports/`.

---

## 4. Debug a 500 — get the server traceback

```bash
docker logs flight-blender 2>&1 | grep -A 30 "Error\|Exception" | tail -60
```

The full Python traceback appears there.  The most common causes on this branch:

- **Wrong SQLAlchemy `__tablename__`** — Django app label (`apps.py` `label =`) differs from the Python module name. Actual table = `<label>_<modelname_lower>`. Long names (> 63 chars) get truncated by Django with a deterministic hex suffix; query the DB to get the exact name:
  ```bash
  docker exec db-blender psql -U mydatabaseuser -d mydatabase \
    -c "SELECT tablename FROM pg_tables WHERE tablename LIKE 'surveillance%' ORDER BY tablename;"
  ```
- **Missing migration** — table doesn't exist at all. The compose entrypoint runs `manage.py migrate` automatically; if a table is still missing, a new Django migration needs to be generated and committed.
- **ForeignKey string mismatch** — `ForeignKey("old_table.id")` must match the target's `__tablename__`; update both when renaming.

---

## 5. Hit an endpoint manually (for isolated 500 reproduction)

Generate a bypass JWT (works when `BYPASS_AUTH_TOKEN_VERIFICATION=1`):

```bash
python3 -c "
import base64, json
def b64(d): return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b'=').decode()
header  = b64({'alg':'HS256','typ':'JWT'})
payload = b64({'iss':'http://localhost:9000','aud':'testflight.flightblender.com',
               'scope':'flightblender.read flightblender.write'})
sig = base64.urlsafe_b64encode(b'fake').rstrip(b'=').decode()
print(f'{header}.{payload}.{sig}')
"
```

Then curl the endpoint:

```bash
TOKEN=<paste token>
curl -s -X PUT http://localhost:8000/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/<uuid> \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"action": "start"}'
```

---

## 6. Tear down the stack

```bash
docker compose \
  --env-file ../verification/tests/.env.tests \
  -f ../verification/tests/docker-compose.fb.yml \
  down -v
```

`-v` removes the Postgres volume so the next run starts with a clean DB.

---

## Quick reference

| What | Command (from `flight-blender/`) |
|---|---|
| Build image | `docker build -t openutm/flight-blender .` |
| Start stack | `docker compose --env-file ../verification/tests/.env.tests -f ../verification/tests/docker-compose.fb.yml up -d --wait --pull never` |
| Full scenario run | `cd ../verification && uv run openutm-verify --debug --config config/fb_pr.yaml` |
| Single suite | `uv run openutm-verify --debug --config config/fb_pr.yaml -s astm_f3623` |
| Server logs | `docker logs flight-blender 2>&1 \| tail -80` |
| DB table names | `docker exec db-blender psql -U mydatabaseuser -d mydatabase -c "SELECT tablename FROM pg_tables WHERE tablename LIKE '<prefix>%';"` |
| Tear down | `docker compose --env-file ../verification/tests/.env.tests -f ../verification/tests/docker-compose.fb.yml down -v` |
