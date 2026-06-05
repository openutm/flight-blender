"""Import layer enforcement: one-way dependency rule.

models → repositories → services → api/routers
clients ─────────────────────────↗
utils ───────────────────────────↗
auth ────────────────────────────↗ (via api/dependencies.py)
"""

import subprocess


def _rg(pattern: str, path: str) -> list[str]:
    result = subprocess.run(
        ["rg", "-n", "--no-heading", pattern, path],
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def test_models_import_nothing_above():
    """models/ must not import from repositories, services, api, tasks, clients."""
    violations = _rg(
        r"^from flight_blender\.(repositories|services|api|tasks|clients)",
        "src/flight_blender/models",
    )
    assert not violations, "models/ imports layer above it:\n" + "\n".join(violations)


def test_repositories_do_not_import_services_or_api():
    violations = _rg(
        r"^from flight_blender\.(services|api|tasks)",
        "src/flight_blender/repositories",
    )
    assert not violations, "repositories/ imports service/api layer:\n" + "\n".join(violations)


def test_services_do_not_import_api():
    violations = _rg(
        r"^from flight_blender\.api",
        "src/flight_blender/services",
    )
    assert not violations, "services/ imports api layer:\n" + "\n".join(violations)


def test_clients_do_not_import_api_or_services():
    violations = _rg(
        r"^from flight_blender\.(api|services)",
        "src/flight_blender/clients",
    )
    assert not violations, "clients/ imports api/service layer:\n" + "\n".join(violations)


def test_utils_do_not_import_api_services_or_tasks():
    violations = _rg(
        r"^from flight_blender\.(api|services|tasks)",
        "src/flight_blender/utils",
    )
    assert not violations, "utils/ imports upper layer:\n" + "\n".join(violations)


def test_schemas_do_not_import_api_or_services():
    violations = _rg(
        r"^from flight_blender\.(api|services|tasks|repositories)",
        "src/flight_blender/schemas",
    )
    assert not violations, "schemas/ imports upper layer:\n" + "\n".join(violations)
