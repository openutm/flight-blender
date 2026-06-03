#!/usr/bin/env bash
# Run the Flight Blender test suite.
#
# Usage:
#   ./run_tests.sh                    # run all tests
#   ./run_tests.sh -v                 # verbose output
#   ./run_tests.sh <test_label> ...   # run only specified test labels
#
# Requires: uv (https://docs.astral.sh/uv/)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Sync deps + editable install ─────────────────────────────────────────
uv sync --group dev

uv run pytest "$@"
