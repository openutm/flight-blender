---
description: Analyze interUSS qualification failures, run the qualifier locally with Docker, and debug Flight Blender issues against ASTM F3548-21 and NetRID F3411-22a.
---

# Analyze interUSS Qualification Problems

Flight Blender is tested against the [interUSS uss_qualifier](https://github.com/interuss/monitoring) which validates ASTM F3548-21 (SCD) and F3411-22a (NetRID) compliance. This skill covers running the qualifier locally, interpreting results, and debugging failures.

## Assumptions

- Docker running with ~8 GB RAM available
- Working directory is the **flight-blender repo root** unless noted
- The `interuss/monitoring` repo is cloned to `/tmp/interuss-monitoring` (the run script handles this)
- macOS Apple Silicon: the interuss containers are linux/amd64 and run via Rosetta

---

## 1. Run the full qualifier locally

The all-in-one script handles everything: builds the image, starts the DSS ecosystem, starts Flight Blender, starts mock USS instances, runs both test suites, and tears down.

```bash
bash testing/interuss/scripts/run_interuss_tests.sh
```

Options:
- `--skip-build` — reuse the existing Docker image (faster iteration)
- `--clean` — remove all test containers and networks before starting

Reports land in `testing/interuss/output/`:
```
testing/interuss/output/
  f3548/
    report.json                     # raw JSON — all participants, all checks
    sequence/                       # HTML sequence view (index.html + s1.html .. s64.html)
    f3548_requirements/             # HTML per-requirement breakdown
  netrid_v22a/
    report.json
    sequence/
    netrid_v22a_requirements/
```

Open the sequence view in a browser:
```bash
open testing/interuss/output/f3548/sequence/index.html
```

---

## 2. Run steps manually (for iteration)

If you want to iterate on code changes without running the full script:

### 2a. Start the DSS ecosystem

```bash
cd /tmp/interuss-monitoring
NUM_USS=2 ./build/dev/run_locally.sh up -d
```

Wait for OAuth:
```bash
for i in $(seq 1 60); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:8085/ 2>/dev/null || echo "000")
  [ "$STATUS" != "000" ] && echo "OAuth ready" && break
  sleep 3
done
```

### 2b. Build and start Flight Blender

```bash
docker build -t openutm/flight-blender-test:latest .
docker compose -f testing/interuss/docker-compose.yml up -d
```

Wait for Flight Blender:
```bash
for i in $(seq 1 90); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:8000/scd/flight_planning/status 2>/dev/null || echo "000")
  [ "$STATUS" != "000" ] && echo "Flight Blender ready (HTTP $STATUS)" && break
  sleep 3
done
```

### 2c. Start mock USS instances

```bash
cd /tmp/interuss-monitoring
docker compose -f monitoring/mock_uss/docker-compose.yaml up -d \
  mock_uss_scdsc_a mock_uss_scdsc_b mock_uss_scdsc_interaction_log \
  mock_uss_ridsp mock_uss_riddp
sleep 10
```

### 2d. Run only F3548 (skip NetRID for faster iteration)

```bash
docker run --rm \
  --network interop_ecosystem_network \
  --add-host "host.docker.internal:host-gateway" \
  -w /app/monitoring/uss_qualifier \
  -e "AUTH_SPEC=DummyOAuth(http://oauth.authority.localutm:8085/token,uss_qualifier)" \
  -e "AUTH_SPEC_2=DummyOAuth(http://oauth.authority.localutm:8085/token,uss_qualifier_2)" \
  -v "$(pwd)/testing/interuss/configs:/configs:ro" \
  -v "$(pwd)/testing/interuss/output:/app/monitoring/uss_qualifier/output" \
  interuss/monitoring:v0.30.0 \
  uv run main.py \
    --config "file:///configs/f3548_flight_blender.yaml" \
    --output-path "output/f3548"
```

### 2e. Tear down

```bash
docker compose -f testing/interuss/docker-compose.yml down --remove-orphans
cd /tmp/interuss-monitoring
docker compose -f monitoring/mock_uss/docker-compose.yaml down --remove-orphans
NUM_USS=2 ./build/dev/run_locally.sh down
```

---

## 3. Understand the results table

Open `testing/interuss/output/f3548/sequence/index.html` in a browser.

### Participant columns

Each column is a system instance being tested simultaneously:

| Column | What it is | Why it's tested |
|--------|-----------|-----------------|
| `flight_blender` | Your Flight Blender instance | System under test |
| `mock_uss` | interuss mock_uss (subscription callbacks, notifications) | Validates notification delivery works |
| `uss1_dss` | First DSS (`dss1.uss1.localutm`) | Validates DSS that Flight Blender depends on |
| `uss2_core` | Peer USS (`scdsc.uss2.localutm`) | Reference implementation — baseline comparison |
| `uss2_dss` | Second DSS (`dss1.uss2.us2.localutm`) | Validates cross-DSS interoperability |
| `<None>` | Global/aggregate tests | Tests that don't belong to a specific participant |

### How to read a row

For each test case (row), each cell shows:
- ✅ `pass_result` — participant passed
- ❌ `fail_result` — participant failed
- (empty) — not applicable to that participant

### What matters for Flight Blender debugging

**Focus on the `flight_blender` column.** When:
- `flight_blender` = ❌ and other columns = ✅ → the bug is in Flight Blender specifically
- `flight_blender` = ❌ and DSS columns = ❌ → likely an infrastructure/DSS issue
- `flight_blender` = ✅ and others = ❌ → Flight Blender is fine, peer implementation is broken

---

## 4. Read the HTML sequence reports

Each `s{N}.html` file shows the detailed request/response for one test scenario.

Key elements to look for:
- **Request URL and method** — which endpoint was called
- **Request body** — what was sent (flight intent, area, time range)
- **Response code** — 200, 400, 404, 500, etc.
- **Response body** — the actual response JSON
- **Failed check** — the `failed_check_summary` and `failed_check_details` text

Example failure pattern:
```
failed_check_summary: Flight planning activity PlanningActivityResult.Failed
failed_check_details: flight_blender indicated PlanningActivityResult.Failed
  leaving flight plan FlightPlanStatus.NotPlanned rather than the expected
  (Activity Completed, flight plan Planned)
```

This tells you: Flight Blender returned `Failed` when it should have returned `Completed` with the flight planned.

---

## 5. Common failure categories

### A. Missing endpoint (404)

**Symptom:** `GET /versioning/versions/astm.f3548.v21` returns 404.
**Fix:** Implement the endpoint returning `{"system_identity": "<id>", "system_version": "<ver>"}`.

### B. Flight planning returns `Failed` instead of `Rejected`/`Completed`

**Symptom:** `PUT /scd/flight_planning/flight_plans/{id}` returns:
```json
{"flight_plan_status": "NotPlanned", "planning_result": "Failed",
 "notes": "Flight Blender failed to process this flight"}
```
**Root cause:** The flight planning handler crashes before evaluating the conflict.
**Debug:**
1. Check Flight Blender logs: `docker logs flight-blender-test 2>&1 | tail -100`
2. Look for Python tracebacks around the time of the request
3. Common causes: missing DSS query, malformed flight intent conversion, missing deconfliction wiring

### C. DSS not publicly addressable

**Symptom:** `DSS host dss1.uss1.localutm is not publicly addressable` (resolved IP 172.18.0.x).
**Root cause:** Docker-internal hostname not routable from the qualifier container.
**Fix:** Infrastructure/DNS configuration, not a code issue.

### D. Rejection reasons not structured

**Symptom:** Flight Blender rejects a flight but doesn't include the structured `rejection` field the qualifier expects.
**Fix:** Return proper rejection details in the response body with `result` enum values like `ConflictFlightTested`, `ConflictOther`.

---

## 6. Debug with container logs

Flight Blender logs (last 5000 lines per container):
```bash
docker logs flight-blender-test 2>&1 | tail -100
docker logs celery-blender-test 2>&1 | tail -50
```

Search for errors:
```bash
docker logs flight-blender-test 2>&1 | grep -i "error\|exception\|traceback" | tail -20
```

DSS logs (for DSS-related failures):
```bash
# Find DSS container name
docker ps --format '{{.Names}}' | grep dss
# Then check its logs
docker logs <dss-container> 2>&1 | tail -50
```

Query the DB directly:
```bash
docker exec db-blender-test psql -U testuser -d testdb \
  -c "SELECT tablename FROM pg_tables WHERE tablename LIKE 'operational%' ORDER BY tablename;"
```

---

## 7. CI workflow logs

The GitHub Actions workflow saves structured logs as artifacts:

1. Go to the Actions run → Artifacts section
2. Download `interuss-container-logs-{run_number}`
3. Each container has `.log` (raw) and `.jsonl` (structured) files

The JSONL format — each line is:
```json
{"ts": "2026-06-05T23:41:18.219502Z", "service": "flight-blender-test", "msg": "INFO ..."}
```

Filter by service and timestamp to trace a specific request:
```bash
cat flight-blender-test.jsonl | python3 -c "
import json, sys
for line in sys.stdin:
    e = json.loads(line)
    if '2026-06-05T23:41:4' in e['ts']:
        print(e['msg'])
"
```

---

## 8. Config overview

The qualifier config is at `testing/interuss/configs/f3548_flight_blender.yaml`.

Key sections:
- `flight_planners` — defines Flight Blender and mock_uss as flight planners
- `dss` / `dss_instances` — DSS endpoints
- `conflicting_flights` / `non_conflicting_flights` — test flight intents with geographic translation
- `invalid_flight_intents` — flights that should be rejected (too far away, recently ended, tiny overlap)
- `artifacts.sequence_view` — generates the HTML sequence report

---

## Quick reference

| What | Command |
|------|---------|
| Run full qualifier locally | `bash testing/interuss/scripts/run_interuss_tests.sh` |
| Run with existing image | `bash testing/interuss/scripts/run_interuss_tests.sh --skip-build` |
| Clean everything first | `bash testing/interuss/scripts/run_interuss_tests.sh --clean` |
| Open sequence report | `open testing/interuss/output/f3548/sequence/index.html` |
| Flight Blender logs | `docker logs flight-blender-test 2>&1 \| tail -100` |
| Search for errors | `docker logs flight-blender-test 2>&1 \| grep -i error \| tail -20` |
| DSS container name | `docker ps --format '{{.Names}}' \| grep dss` |
| DB table names | `docker exec db-blender-test psql -U testuser -d testdb -c "SELECT tablename FROM pg_tables;"` |
| Tear down all | `docker compose -f testing/interuss/docker-compose.yml down --remove-orphans && (cd /tmp/interuss-monitoring && NUM_USS=2 ./build/dev/run_locally.sh down)` |
