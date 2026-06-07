#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash testing/interuss/scripts/run_interuss_tests.sh [OPTIONS]

Options:
  --clean        Remove existing local test containers before starting
  --skip-build   Reuse openutm/flight-blender-test:latest
  --suite NAME   Run one suite: f3548 or netrid (default: both)
  --filter EXPR  Pass a uss_qualifier scenario filter; requires --suite

Examples:
  bash testing/interuss/scripts/run_interuss_tests.sh --clean
  bash testing/interuss/scripts/run_interuss_tests.sh --suite netrid --clean
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TESTING_DIR="${REPO_ROOT}/testing/interuss"
OUTPUT_DIR="${TESTING_DIR}/output"
CONFIGS_DIR="${TESTING_DIR}/configs"

INTERUSS_MONITORING_REPO="${INTERUSS_MONITORING_REPO:-https://github.com/interuss/monitoring.git}"
INTERUSS_MONITORING_TAG="${INTERUSS_MONITORING_TAG:-interuss/monitoring/v0.30.0}"
INTERUSS_MONITORING_DIR="${INTERUSS_MONITORING_DIR:-/tmp/interuss-monitoring}"
INTERUSS_IMAGE="${INTERUSS_IMAGE:-interuss/monitoring:v0.30.0}"
BLENDER_IMAGE="${BLENDER_IMAGE:-openutm/flight-blender-test:latest}"
NETWORK="${NETWORK:-interop_ecosystem_network}"

SKIP_BUILD=false
CLEAN=false
SUITE=""
FILTER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build)
      SKIP_BUILD=true
      shift
      ;;
    --clean)
      CLEAN=true
      shift
      ;;
    --suite)
      SUITE="${2:-}"
      shift 2
      ;;
    --filter)
      FILTER="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -n "${SUITE}" && "${SUITE}" != "f3548" && "${SUITE}" != "netrid" ]]; then
  echo "ERROR: --suite must be f3548 or netrid, got ${SUITE}" >&2
  exit 1
fi
if [[ -n "${FILTER}" && -z "${SUITE}" ]]; then
  echo "ERROR: --filter requires --suite" >&2
  exit 1
fi

RUN_F3548=true
RUN_NETRID=true
[[ "${SUITE}" == "f3548" ]] && RUN_NETRID=false
[[ "${SUITE}" == "netrid" ]] && RUN_F3548=false

log() { echo "[$(date +%T)] $*"; }
warn() {
  log "WARNING: $*"
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    echo "::warning::$*"
  fi
}
section() { printf '\n======================================================\n  %s\n======================================================\n\n' "$*"; }

CLEANED_UP=false
cleanup() {
  [[ "${CLEANED_UP}" == "true" ]] && return
  CLEANED_UP=true
  log "Cleaning up test containers..."
  docker compose -f "${TESTING_DIR}/docker-compose.yml" down -v --remove-orphans 2>/dev/null || true
  if [[ -d "${INTERUSS_MONITORING_DIR}" ]]; then
    (cd "${INTERUSS_MONITORING_DIR}" && docker compose -f monitoring/mock_uss/docker-compose.yaml down --remove-orphans 2>/dev/null) || true
    (cd "${INTERUSS_MONITORING_DIR}" && NUM_USS=2 ./build/dev/run_locally.sh down 2>/dev/null) || true
  fi
  docker network rm "${NETWORK}" 2>/dev/null || true
  log "Cleanup done."
}
trap cleanup EXIT

wait_for_http() {
  local url="$1"
  local attempts="${2:-60}"
  local status

  log "Waiting for ${url} ..."
  for i in $(seq 1 "${attempts}"); do
    status="$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "${url}" 2>/dev/null || true)"
    status="${status:-000}"
    if [[ "${status}" != "000" ]]; then
      log "${url} is up (HTTP ${status})."
      return 0
    fi
    sleep 3
  done

  log "ERROR: ${url} did not become ready after ${attempts} attempts."
  return 1
}

run_qualifier() {
  local name="$1"
  local config="$2"
  local output_path="$3"
  local auth_spec_2="${4:-}"
  local -a cmd=(
    docker run --rm
    --network "${NETWORK}"
    --add-host "host.docker.internal:host-gateway"
    -w /app/monitoring/uss_qualifier
    -e "AUTH_SPEC=DummyOAuth(http://oauth.authority.localutm:8085/token,uss_qualifier)"
  )

  if [[ -n "${auth_spec_2}" ]]; then
    cmd+=(-e "AUTH_SPEC_2=${auth_spec_2}")
  fi

  cmd+=(
    -v "${CONFIGS_DIR}:/configs:ro"
    -v "${OUTPUT_DIR}:/app/monitoring/uss_qualifier/output"
    "${INTERUSS_IMAGE}"
    uv run main.py
    --config "file:///configs/${config}"
    --output-path "output/${output_path}"
  )

  if [[ -n "${FILTER}" ]]; then
    cmd+=(--filter "${FILTER}")
  fi

  section "uss_qualifier: ${name}"
  "${cmd[@]}"
}

section "Configuration"
log "Skip build:      ${SKIP_BUILD}"
log "Clean mode:      ${CLEAN}"
log "Suite:           ${SUITE:-both}"
log "Filter:          ${FILTER:-all}"
log "Will run F3548:  ${RUN_F3548}"
log "Will run NetRID: ${RUN_NETRID}"

section "Preflight checks"
command -v docker >/dev/null 2>&1 || { log "ERROR: Docker is not installed."; exit 1; }
docker info >/dev/null 2>&1 || { log "ERROR: Docker is not running."; exit 1; }
log "Docker OK."

if [[ "${CLEAN}" == "true" ]]; then
  section "Clean"
  cleanup
  CLEANED_UP=false
fi

mkdir -p "${OUTPUT_DIR}"
chmod 777 "${OUTPUT_DIR}"
log "Output directory: ${OUTPUT_DIR}"

section "Docker network"
docker network inspect "${NETWORK}" >/dev/null 2>&1 || docker network create "${NETWORK}"

section "InterUSS monitoring"
if [[ ! -d "${INTERUSS_MONITORING_DIR}/.git" ]]; then
  git clone --depth=1 --branch "${INTERUSS_MONITORING_TAG}" "${INTERUSS_MONITORING_REPO}" "${INTERUSS_MONITORING_DIR}"
else
  log "Using existing clone at ${INTERUSS_MONITORING_DIR}"
fi
docker pull "${INTERUSS_IMAGE}" || warn "Could not pull ${INTERUSS_IMAGE}; using cached image if available."

section "DSS ecosystem"
(cd "${INTERUSS_MONITORING_DIR}" && NUM_USS=2 ./build/dev/run_locally.sh up -d)
wait_for_http "http://localhost:8085/.well-known/jwks.json" 60 || wait_for_http "http://localhost:8085/" 10

section "Flight Blender image"
if [[ "${SKIP_BUILD}" == "false" ]]; then
  docker build -t "${BLENDER_IMAGE}" "${REPO_ROOT}"
else
  log "Skipping build (--skip-build)."
fi

section "Flight Blender stack"
docker compose -f "${TESTING_DIR}/docker-compose.yml" up -d
wait_for_http "http://localhost:8000/scd/flight_planning/status" 90 || wait_for_http "http://localhost:8000/rid/capabilities" 30 || true

section "mock_uss"
(
  cd "${INTERUSS_MONITORING_DIR}"
  UID_GID="$(id -u):$(id -g)" docker compose -f monitoring/mock_uss/docker-compose.yaml up -d \
    mock_uss_scdsc_a mock_uss_scdsc_b mock_uss_scdsc_interaction_log \
    mock_uss_ridsp mock_uss_riddp
) || warn "mock_uss startup had issues; continuing."
sleep 10

F3548_RC=0
NETRID_RC=0

if [[ "${RUN_F3548}" == "true" ]]; then
  run_qualifier \
    "F3548-21 (Strategic Conflict Detection)" \
    "f3548_flight_blender.yaml" \
    "f3548" \
    "DummyOAuth(http://oauth.authority.localutm:8085/token,uss_qualifier_2)" || F3548_RC=$?
else
  section "Skipping F3548-21"
fi

if [[ "${RUN_NETRID}" == "true" ]]; then
  run_qualifier \
    "NetRID v22a (Remote ID F3411-22a)" \
    "netrid_v22a_flight_blender.yaml" \
    "netrid_v22a" || NETRID_RC=$?
else
  section "Skipping NetRID v22a"
fi

section "Test summary"
SUMMARY_ARGS=()
[[ "${RUN_F3548}" == "true" && -f "${OUTPUT_DIR}/f3548/report.json" ]] && SUMMARY_ARGS+=("${OUTPUT_DIR}/f3548/report.json")
[[ "${RUN_NETRID}" == "true" && -f "${OUTPUT_DIR}/netrid_v22a/report.json" ]] && SUMMARY_ARGS+=("${OUTPUT_DIR}/netrid_v22a/report.json")

if [[ ${#SUMMARY_ARGS[@]} -gt 0 ]]; then
  python3 "${SCRIPT_DIR}/report_to_summary.py" "${SUMMARY_ARGS[@]}" || warn "report_to_summary.py failed."
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
      echo "# InterUSS Qualification Report"
      echo
      python3 "${SCRIPT_DIR}/report_to_summary.py" "${SUMMARY_ARGS[@]}" || true
    } >> "${GITHUB_STEP_SUMMARY}"
  fi
else
  log "No report files found; skipping summary."
fi

echo
log "Reports written to: ${OUTPUT_DIR}/"
ls -lh "${OUTPUT_DIR}/" 2>/dev/null || true

if [[ "${F3548_RC}" -ne 0 ]]; then
  warn "F3548-21 uss_qualifier exited with ${F3548_RC}; reports were still generated when available."
fi
if [[ "${NETRID_RC}" -ne 0 ]]; then
  warn "NetRID v22a uss_qualifier exited with ${NETRID_RC}; reports were still generated when available."
fi

if [[ "${F3548_RC}" -ne 0 || "${NETRID_RC}" -ne 0 ]]; then
  warn "uss_qualifier command failures are treated as warnings; inspect the uploaded reports for details."
  exit 0
fi

log "All uss_qualifier runs completed successfully."
