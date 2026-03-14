#!/usr/bin/env bash
# Run the Flight Blender test suite.
#
# Usage:
#   ./run_tests.sh              # run all tests
#   ./run_tests.sh -v           # verbose output
#   ./run_tests.sh -k <pattern> # run only tests matching pattern
#
# Requires: a Python virtualenv at .venv with all dependencies installed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Activate virtualenv ──────────────────────────────────────────────────
if [[ -d .venv ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
else
    echo "ERROR: .venv not found. Create a virtualenv first." >&2
    exit 1
fi

# ── Defaults ─────────────────────────────────────────────────────────────
VERBOSITY=1
TEST_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--verbose)
            VERBOSITY=2
            shift
            ;;
        -k)
            TEST_ARGS+=("$2")
            shift 2
            ;;
        *)
            TEST_ARGS+=("$1")
            shift
            ;;
    esac
done

# ── Use an in-memory SQLite DB so tests don't need a running Postgres ────
export DATABASE_URL="${DATABASE_URL:-sqlite://:memory:}"
export BYPASS_AUTH_TOKEN_VERIFICATION=1

# ── Test modules with actual tests ───────────────────────────────────────
# Plugin / common tests (no DB needed)
PLUGIN_TESTS="common.tests_plugin_loader"
# Flight declaration endpoint tests (needs DB)
DECLARATION_TESTS="flight_declaration_operations.tests"

if [[ ${#TEST_ARGS[@]} -gt 0 ]]; then
    # Run only the tests the caller asked for
    python manage.py test "${TEST_ARGS[@]}" --verbosity="$VERBOSITY"
else
    echo "==> Running plugin interface tests …"
    python manage.py test "$PLUGIN_TESTS" --verbosity="$VERBOSITY"

    echo ""
    echo "==> Running flight declaration tests …"
    python manage.py test "$DECLARATION_TESTS" --verbosity="$VERBOSITY"

    echo ""
    echo "All tests passed."
fi
