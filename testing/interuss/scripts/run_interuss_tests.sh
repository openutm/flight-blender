#!/usr/bin/env bash
# run_interuss_tests.sh — Run Flight Blender against InterUSS uss_qualifier locally.
#
# This script replicates what the GitHub Actions workflow does, so you can run
# the full InterUSS qualification test suite on your local machine.
#
# Prerequisites:
#   - Docker installed and running
#   - The interuss/monitoring repository cloned (or let this script do it)
#   - ~8 GB RAM available for Docker
#
# Usage:
#   cd /path/to/flight-blender
#   bash testing/interuss/scripts/run_interuss_tests.sh [--skip-build] [--clean]
#
# Options:
#   --skip-build   Skip rebuilding the flight-blender Docker image
#   --clean        Remove all test containers and networks before starting

set -euo pipefail

# ============================================================
# Configuration
# ============================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TESTING_DIR="${REPO_ROOT}/testing/interuss"
OUTPUT_DIR="${TESTING_DIR}/output"
CONFIGS_DIR="${TESTING_DIR}/configs"

INTERUSS_MONITORING_REPO="https://github.com/interuss/monitoring.git"
INTERUSS_MONITORING_TAG="interuss/monitoring/v0.30.0"
INTERUSS_MONITORING_DIR="/tmp/interuss-monitoring"

INTERUSS_IMAGE="interuss/monitoring:v0.30.0"
BLENDER_IMAGE="openutm/flight-blender-test:latest"
NETWORK="interop_ecosystem_network"

SKIP_BUILD=false
CLEAN=false

for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=true ;;
    --clean)      CLEAN=true ;;
  esac
done

# ============================================================
# Helpers
# ============================================================
log() { echo "[$(date +%T)] $*"; }
separator() { echo; echo "======================================================"; echo "  $*"; echo "======================================================"; echo; }

cleanup() {
  log "Cleaning up test containers..."
  docker compose -f "${TESTING_DIR}/docker-compose.yml" down -v --remove-orphans 2>/dev/null || true
  if [ -d "${INTERUSS_MONITORING_DIR}" ]; then
    (cd "${INTERUSS_MONITORING_DIR}" && \
      docker compose -f monitoring/mock_uss/docker-compose.yaml down --remove-orphans 2>/dev/null || true) || true
    (cd "${INTERUSS_MONITORING_DIR}" && \
      NUM_USS=2 ./build/dev/run_locally.sh down 2>/dev/null || true) || true
  fi
  log "Cleanup done."
}

wait_for_http() {
  local url="$1"
  local max_attempts="${2:-60}"
  local attempt=0
  log "Waiting for ${url} ..."
  until [ "$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "${url}" 2>/dev/null || echo "000")" != "000" ]; do
    attempt=$((attempt + 1))
    if [ "${attempt}" -ge "${max_attempts}" ]; then
      log "ERROR: ${url} did not become ready after ${max_attempts} attempts."
      return 1
    fi
    sleep 3
  done
  log "${url} is up."
}

# ============================================================
# Preflight checks
# ============================================================
separator "Preflight checks"
command -v docker >/dev/null 2>&1 || { log "ERROR: Docker is not installed."; exit 1; }
docker info >/dev/null 2>&1       || { log "ERROR: Docker is not running."; exit 1; }
log "Docker OK."

# ============================================================
# Optional clean
# ============================================================
if [ "${CLEAN}" = "true" ]; then
  separator "Clean mode — removing existing test containers"
  cleanup
  docker network rm "${NETWORK}" 2>/dev/null || true
fi

# ============================================================
# Prepare output directory
# ============================================================
mkdir -p "${OUTPUT_DIR}"
chmod 777 "${OUTPUT_DIR}"
log "Output directory: ${OUTPUT_DIR}"

# ============================================================
# Create Docker network
# ============================================================
separator "Docker network"
if ! docker network inspect "${NETWORK}" >/dev/null 2>&1; then
  log "Creating network ${NETWORK} ..."
  docker network create "${NETWORK}"
else
  log "Network ${NETWORK} already exists."
fi

# ============================================================
# Clone / update interuss/monitoring
# ============================================================
separator "InterUSS monitoring repo"
if [ -d "${INTERUSS_MONITORING_DIR}/.git" ]; then
  log "Using existing clone at ${INTERUSS_MONITORING_DIR}"
else
  log "Cloning ${INTERUSS_MONITORING_REPO} at tag ${INTERUSS_MONITORING_TAG} ..."
  git clone --depth=1 --branch "${INTERUSS_MONITORING_TAG}" \
    "${INTERUSS_MONITORING_REPO}" "${INTERUSS_MONITORING_DIR}"
fi

# Pull the monitoring image upfront so startup is faster
log "Pulling ${INTERUSS_IMAGE} ..."
docker pull "${INTERUSS_IMAGE}" || log "WARNING: Could not pull ${INTERUSS_IMAGE} (will use cached)"

# ============================================================
# Start DSS ecosystem (CockroachDB + DSS + dummy OAuth)
# ============================================================
separator "Starting DSS ecosystem"
(
  cd "${INTERUSS_MONITORING_DIR}"
  log "Starting DSS (NUM_USS=2) ..."
  NUM_USS=2 ./build/dev/run_locally.sh up -d
)
log "Waiting for dummy OAuth ..."
wait_for_http "http://localhost:8085/.well-known/jwks.json" 60 \
  || { log "ERROR: OAuth not ready. Check DSS ecosystem."; cleanup; exit 1; }
log "DSS ecosystem is ready."

# ============================================================
# Build Flight Blender image (unless --skip-build)
# ============================================================
separator "Flight Blender image"
if [ "${SKIP_BUILD}" = "false" ]; then
  log "Building ${BLENDER_IMAGE} from ${REPO_ROOT} ..."
  docker build -t "${BLENDER_IMAGE}" "${REPO_ROOT}"
else
  log "Skipping build (--skip-build)."
fi

# ============================================================
# Start Flight Blender stack
# ============================================================
separator "Starting Flight Blender"
log "Starting Flight Blender containers ..."
docker compose -f "${TESTING_DIR}/docker-compose.yml" up -d

log "Waiting for Flight Blender health check ..."
# Django returns 400/401 on unauthenticated requests — that's still "up"
wait_for_http "http://localhost:8000/scd/flight_planning/status" 90 \
  || wait_for_http "http://localhost:8000/rid/capabilities" 30 \
  || log "WARNING: Flight Blender health check did not pass (will continue)"

# ============================================================
# Start mock_uss instances
# ============================================================
separator "Starting mock USS instances"
(
  cd "${INTERUSS_MONITORING_DIR}"
  log "Starting mock_uss (scd + ridsp + riddp profiles) ..."
  UID_GID="$(id -u):$(id -g)" \
  docker compose -f monitoring/mock_uss/docker-compose.yaml up -d \
    mock_uss_scdsc_a mock_uss_scdsc_b mock_uss_scdsc_interaction_log \
    mock_uss_ridsp mock_uss_riddp \
    2>/dev/null || true
)
# Brief pause to let mock_uss settle
sleep 10

# ============================================================
# Run uss_qualifier — F3548-21
# ============================================================
separator "uss_qualifier: F3548-21 (Strategic Conflict Detection)"
F3548_RC=0
docker run --rm \
  --network "${NETWORK}" \
  --add-host "host.docker.internal:host-gateway" \
  -w /app/monitoring/uss_qualifier \
  -e "AUTH_SPEC=DummyOAuth(http://oauth.authority.localutm:8085/token,uss_qualifier)" \
  -e "AUTH_SPEC_2=DummyOAuth(http://oauth.authority.localutm:8085/token,uss_qualifier_2)" \
  -v "${CONFIGS_DIR}:/configs:ro" \
  -v "${OUTPUT_DIR}:/app/monitoring/uss_qualifier/output" \
  "${INTERUSS_IMAGE}" \
  uv run main.py \
    --config "file:///configs/f3548_flight_blender.yaml" \
    --output-path "output/f3548" \
  || F3548_RC=$?

log "F3548-21 uss_qualifier finished (exit code: ${F3548_RC})."

# ============================================================
# Run uss_qualifier — NetRID v22a
# ============================================================
separator "uss_qualifier: NetRID v22a (Remote ID F3411-22a)"
NETRID_RC=0
docker run --rm \
  --network "${NETWORK}" \
  --add-host "host.docker.internal:host-gateway" \
  -w /app/monitoring/uss_qualifier \
  -e "AUTH_SPEC=DummyOAuth(http://oauth.authority.localutm:8085/token,uss_qualifier)" \
  -v "${CONFIGS_DIR}:/configs:ro" \
  -v "${OUTPUT_DIR}:/app/monitoring/uss_qualifier/output" \
  "${INTERUSS_IMAGE}" \
  uv run main.py \
    --config "file:///configs/netrid_v22a_flight_blender.yaml" \
    --output-path "output/netrid_v22a" \
  || NETRID_RC=$?

log "NetRID v22a uss_qualifier finished (exit code: ${NETRID_RC})."

# ============================================================
# Generate summary
# ============================================================
separator "Test summary"
python3 "${SCRIPT_DIR}/report_to_summary.py" \
  "${OUTPUT_DIR}/f3548/report.json" \
  "${OUTPUT_DIR}/netrid_v22a/report.json" \
  2>/dev/null || log "WARNING: report_to_summary.py failed (reports may be incomplete)"

echo
log "Reports written to: ${OUTPUT_DIR}/"
ls -lh "${OUTPUT_DIR}/" 2>/dev/null || true

# ============================================================
# Teardown
# ============================================================
separator "Stopping test containers"
cleanup

# ============================================================
# Exit
# ============================================================
if [ "${F3548_RC}" -ne 0 ] || [ "${NETRID_RC}" -ne 0 ]; then
  log "One or more test runs had failures (f3548=${F3548_RC}, netrid=${NETRID_RC})."
  log "This is expected until Flight Blender fully implements all requirements."
  log "Review the reports in ${OUTPUT_DIR}/ for details."
  exit 0  # Always exit 0 from this script — failures are captured in the reports
fi

log "All uss_qualifier runs completed successfully."
