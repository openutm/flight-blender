# Copilot Instructions for flight-blender

## Project Overview

This is a FastAPI + SQLAlchemy 2.0 (async) + Celery + Redis project. Python 3.12. Package management is handled by **uv**. The project uses SQLite (aiosqlite) in tests and PostgreSQL (asyncpg) in production.

## Linting & Formatting

All code **must** pass `ruff` linting and formatting. Configuration lives in `pyproject.toml` under `[tool.ruff]`.

- **Run linter:** `uv run ruff check .`
- **Auto-fix lint issues:** `uv run ruff check --fix .`
- **Run formatter:** `uv run ruff format .`
- **Check formatting:** `uv run ruff format --check .`

Key rules:
- Line length: 150
- Indent: 4 spaces
- Quote style: double quotes
- Target: Python 3.12
- Lint rules: E4, E7, E9, F

**Before finishing any code change, always run `ruff check` and `ruff format` on modified files and fix all issues.**

## Testing

All code changes **must** include or update relevant pytest test cases. Every modified module should have corresponding tests.

- **Run tests:** `uv run pytest --tb=short`
- **Run specific file:** `uv run pytest path/to/tests.py --tb=short`
- **Run with coverage:** `uv run pytest --cov --cov-report=term-missing --tb=short`

Test configuration is in `pyproject.toml` under `[tool.pytest.ini_options]`.
No `pytest-django`. No `@pytest.mark.django_db`.

Environment variables needed for tests:
- `DATABASE_URL=sqlite+aiosqlite:///:memory:` (or `sqlite:///:memory:` for sync)
- `BYPASS_AUTH_TOKEN_VERIFICATION=1`

### Test requirements

1. **Every code change must have tests.** If you add or modify a function, class, or endpoint, add or update tests.
2. **All tests must pass.** Run `uv run pytest` after making changes and fix any failures before considering work complete.
3. **Code coverage must be above 80%** for any new or modified code. Run `uv run pytest --cov --cov-report=term-missing --tb=short` and verify coverage on touched files. If a modified file drops below 80%, add more tests before finishing.
4. Use `unittest.mock.patch` and `pytest` with `AsyncMock` for mocking external services (Redis, DSS, Celery tasks).
5. Follow existing test patterns in the codebase.

## Code Style Conventions

- Use type hints where practical.
- Use `dataclass` for data structures (see `domain_types/` directory).
- Follow the directory layout from AGENTS.md: `models/*_orm.py`, `services/*_svc.py`, `api/routers/*_api.py`, etc.
- Imports should be organized: stdlib → third-party → local (ruff handles this).

## Dependencies

- Add runtime deps: `uv add <package>`
- Add dev deps: `uv add --dev <package>`
- Never edit `uv.lock` manually.

## Security

Do **not** introduce security vulnerabilities. Follow OWASP best practices:

- **No hardcoded secrets** — use environment variables or pydantic-settings (`config.py`) for credentials, tokens, and keys.
- **No SQL injection** — always use SQLAlchemy ORM or parameterized queries. Never interpolate user input into raw SQL.
- **No command injection** — never pass unsanitized input to `subprocess` or `os.system`.
- **Validate and sanitize all external input** — request data, query parameters, headers.
- **No unsafe deserialization** — do not use `pickle.loads`, `yaml.unsafe_load`, or `eval`/`exec` on untrusted data.
- **Use the FastAPI/JWT auth pattern** — all endpoints require a JWT bearer token via `require_scopes()`. Do not bypass authentication.
- **Run `uv run bandit -r . -x ./.venv,./migrations -c pyproject.toml`** to check for security issues and fix any findings before finishing.

## Pre-commit Checklist

Before completing any task, verify:

1. `uv run ruff check .` — zero errors
2. `uv run ruff format --check .` — zero reformats needed
3. `uv run pytest --tb=short` — all tests pass
4. `uv run pytest --cov --cov-report=term-missing --tb=short` — coverage above 80% on modified files
5. `uv run bandit -r . -x ./.venv,./migrations -c pyproject.toml` — no new security issues
