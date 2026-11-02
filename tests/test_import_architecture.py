"""Import layer enforcement: one-way dependency rule.

models → repositories → services → api/routers
clients ─────────────────────────↗
utils ───────────────────────────↗
auth ────────────────────────────↗ (via api/dependencies.py)
"""

import re
from pathlib import Path

from flight_blender.utils.paths import SRC_FLIGHT_BLENDER_PATH


def _find_violations(pattern: str, path: Path) -> list[str]:
    regex = re.compile(pattern)
    matches: list[str] = []
    for py_file in sorted(path.rglob("*.py")):
        for line_no, line in enumerate(py_file.read_text().splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{py_file}:{line_no}:{line.rstrip()}")
    return matches


def test_models_import_nothing_above():
    """models/ must not import from repositories, services, api, tasks, clients."""
    violations = _find_violations(
        r"^from flight_blender\.(repositories|services|api|tasks|clients)",
        SRC_FLIGHT_BLENDER_PATH / "models",
    )
    assert not violations, "models/ imports layer above it:\n" + "\n".join(violations)


def test_repositories_do_not_import_services_or_api():
    violations = _find_violations(
        r"^from flight_blender\.(services|api|tasks)",
        SRC_FLIGHT_BLENDER_PATH / "repositories",
    )
    assert not violations, "repositories/ imports service/api layer:\n" + "\n".join(violations)


def test_services_do_not_import_api():
    violations = _find_violations(
        r"^from flight_blender\.api",
        SRC_FLIGHT_BLENDER_PATH / "services",
    )
    assert not violations, "services/ imports api layer:\n" + "\n".join(violations)


def test_clients_do_not_import_api_or_services():
    violations = _find_violations(
        r"^from flight_blender\.(api|services)",
        SRC_FLIGHT_BLENDER_PATH / "clients",
    )
    assert not violations, "clients/ imports api/service layer:\n" + "\n".join(violations)


def test_utils_do_not_import_api_services_or_tasks():
    violations = _find_violations(
        r"^from flight_blender\.(api|services|tasks)",
        SRC_FLIGHT_BLENDER_PATH / "utils",
    )
    assert not violations, "utils/ imports upper layer:\n" + "\n".join(violations)


def test_schemas_do_not_import_api_or_services():
    violations = _find_violations(
        r"^from flight_blender\.(api|services|tasks|repositories)",
        SRC_FLIGHT_BLENDER_PATH / "schemas",
    )
    assert not violations, "schemas/ imports upper layer:\n" + "\n".join(violations)
