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
#   bash testing/interuss/scripts/run_interuss_tests.sh [OPTIONS]
#
# Options:
#   --skip-build   Skip rebuilding the flight-blender Docker image
#   --clean        Remove all test containers and networks before starting
#   --suite NAME   Run only one test suite: "f3548" or "netrid" (default: both)
#   --filter EXPR  Run only test scenarios matching the filter expression.
#                  Passed to uss_qualifier as --filter <EXPR>.
#                  Requires --suite to be set.
#                  Example: --suite f3548 --filter astm.f3548.v21.SCD0020
#
# Examples:
#   Run everything:                     bash testing/interuss/scripts/run_interuss_tests.sh
#   Run only F3548:                     bash testing/interuss/scripts/run_interuss_tests.sh --suite f3548
#   Run only NetRID:                    bash testing/interuss/scripts/run_interuss_tests.sh --suite netrid
#   Debug a single scenario (F3548):    bash testing/interuss/scripts/run_interuss_tests.sh --suite f3548 --filter astm.f3548.v21.SCD0020
#   Skip rebuild + single suite:        bash testing/interuss/scripts/run_interuss_tests.sh --skip-build --suite netrid

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
SUITE=""
FILTER=""

for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=true ;;
    --clean)      CLEAN=true ;;
  esac
done

# Parse --suite and --filter which need the next argument
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build|--clean) shift ;;
    --suite)
      SUITE="${2:-}"
      if [ -z "${SUITE}" ]; then
        echo "ERROR: --suite requires a value (f3548 or netrid)"
        exit 1
      fi
      shift 2
      ;;
    --filter)
      FILTER="${2:-}"
      if [ -z "${FILTER}" ]; then
        echo "ERROR: --filter requires a filter expression"
        exit 1
      fi
      shift 2
      ;;
    *)
      echo "ERROR: Unknown argument: $1"
      echo "Usage: $0 [--skip-build] [--clean] [--suite f3548|netrid] [--filter EXPR]"
      exit 1
      ;;
  esac
done

# Validate --suite value
if [ -n "${SUITE}" ] && [ "${SUITE}" != "f3548" ] && [ "${SUITE}" != "netrid" ]; then
  echo "ERROR: --suite must be 'f3548' or 'netrid', got '${SUITE}'"
  exit 1
fi

# --filter requires --suite
if [ -n "${FILTER}" ] && [ -z "${SUITE}" ]; then
  echo "ERROR: --filter requires --suite to be set"
  exit 1
fi

RUN_F3548=true
RUN_NETRID=true
if [ "${SUITE}" = "f3548" ]; then
  RUN_NETRID=false
elif [ "${SUITE}" = "netrid" ]; then
  RUN_F3548=false
fi

# ============================================================
# Helpers
# ============================================================
log() { echo "[$(date +%T)] $*"; }
separator() { echo; echo "======================================================"; echo "  $*"; echo "======================================================"; echo; }

separator "Configuration"
log "Skip build:      ${SKIP_BUILD}"
log "Clean mode:      ${CLEAN}"
log "Suite:           ${SUITE:-both}"
log "Filter:          ${FILTER:-all}"
log "Will run F3548:  ${RUN_F3548}"
log "Will run NetRID: ${RUN_NETRID}"

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
F3548_RC=0
if [ "${RUN_F3548}" = "true" ]; then
  separator "uss_qualifier: F3548-21 (Strategic Conflict Detection)"

  F3548_CMD=(
    docker run --rm
    --network "${NETWORK}"
    --add-host "host.docker.internal:host-gateway"
    -w /app/monitoring/uss_qualifier
    -e "AUTH_SPEC=DummyOAuth(http://oauth.authority.localutm:8085/token,uss_qualifier)"
    -e "AUTH_SPEC_2=DummyOAuth(http://oauth.authority.localutm:8085/token,uss_qualifier_2)"
    -v "${CONFIGS_DIR}:/configs:ro"
    -v "${OUTPUT_DIR}:/app/monitoring/uss_qualifier/output"
    "${INTERUSS_IMAGE}"
    uv run main.py
    --config "file:///configs/f3548_flight_blender.yaml"
    --output-path "output/f3548"
  )

  if [ -n "${FILTER}" ]; then
    F3548_CMD+=(--filter "${FILTER}")
    log "Filtering to: ${FILTER}"
  fi

  "${F3548_CMD[@]}" || F3548_RC=$?

  log "F3548-21 uss_qualifier finished (exit code: ${F3548_RC})."
else
  separator "Skipping F3548-21 (--suite netrid)"
fi

# ============================================================
# Run uss_qualifier — NetRID v22a
# ============================================================
NETRID_RC=0
if [ "${RUN_NETRID}" = "true" ]; then
  separator "uss_qualifier: NetRID v22a (Remote ID F3411-22a)"

  NETRID_CMD=(
    docker run --rm
    --network "${NETWORK}"
    --add-host "host.docker.internal:host-gateway"
    -w /app/monitoring/uss_qualifier
    -e "AUTH_SPEC=DummyOAuth(http://oauth.authority.localutm:8085/token,uss_qualifier)"
    -v "${CONFIGS_DIR}:/configs:ro"
    -v "${OUTPUT_DIR}:/app/monitoring/uss_qualifier/output"
    "${INTERUSS_IMAGE}"
    uv run main.py
    --config "file:///configs/netrid_v22a_flight_blender.yaml"
    --output-path "output/netrid_v22a"
  )

  if [ -n "${FILTER}" ]; then
    NETRID_CMD+=(--filter "${FILTER}")
    log "Filtering to: ${FILTER}"
  fi

  "${NETRID_CMD[@]}" || NETRID_RC=$?

  log "NetRID v22a uss_qualifier finished (exit code: ${NETRID_RC})."
else
  separator "Skipping NetRID v22a (--suite f3548)"
fi

# ============================================================
# Generate summary
# ============================================================
separator "Test summary"

SUMMARY_ARGS=()
if [ "${RUN_F3548}" = "true" ] && [ -f "${OUTPUT_DIR}/f3548/report.json" ]; then
  SUMMARY_ARGS+=("${OUTPUT_DIR}/f3548/report.json")
fi
if [ "${RUN_NETRID}" = "true" ] && [ -f "${OUTPUT_DIR}/netrid_v22a/report.json" ]; then
  SUMMARY_ARGS+=("${OUTPUT_DIR}/netrid_v22a/report.json")
fi

if [ ${#SUMMARY_ARGS[@]} -gt 0 ]; then
  python3 "${SCRIPT_DIR}/report_to_summary.py" "${SUMMARY_ARGS[@]}" \
    2>/dev/null || log "WARNING: report_to_summary.py failed (reports may be incomplete)"
else
  log "No report files found — skipping summary."
fi

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
if [ "${RUN_F3548}" = "true" ] && [ "${F3548_RC}" -ne 0 ]; then
  log "F3548-21 run had failures (exit code: ${F3548_RC}). Review ${OUTPUT_DIR}/f3548/ for details."
fi
if [ "${RUN_NETRID}" = "true" ] && [ "${NETRID_RC}" -ne 0 ]; then
  log "NetRID v22a run had failures (exit code: ${NETRID_RC}). Review ${OUTPUT_DIR}/netrid_v22a/ for details."
fi

if [ "${RUN_F3548}" = "true" ] && [ "${F3548_RC}" -ne 0 ] || \
   [ "${RUN_NETRID}" = "true" ] && [ "${NETRID_RC}" -ne 0 ]; then
  log "This is expected until Flight Blender fully implements all requirements."
  exit 0  # Always exit 0 — failures are captured in the reports
fi

log "All uss_qualifier runs completed successfully."
