# Copilot Instructions for flight-blender

## Project Overview

This is a Django 5.2 / Python 3.12 project using Django REST Framework. Package management is handled by **uv**. The project uses SQLite in tests and PostgreSQL in production.

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

Test configuration is in `pyproject.toml` under `[tool.pytest.ini_options]`:
- `DJANGO_SETTINGS_MODULE = "flight_blender.settings"`
- Test file patterns: `test_*.py`, `tests.py`, `tests_*.py`

Environment variables needed for tests:
- `DATABASE_URL=sqlite://:memory:`
- `BYPASS_AUTH_TOKEN_VERIFICATION=1`

### Test requirements

1. **Every code change must have tests.** If you add or modify a function, class, or view, add or update tests in the same app's `tests.py` or `test_*.py` file.
2. **All tests must pass.** Run `uv run pytest` after making changes and fix any failures before considering work complete.
3. **Code coverage must be above 80%** for any new or modified code. Run `uv run pytest --cov --cov-report=term-missing --tb=short` and verify coverage on touched files. If a modified file drops below 80%, add more tests before finishing.
4. Use `unittest.mock.patch` and Django's `TestCase` / `APITestCase` for mocking external services (Redis, DSS, Celery tasks).
5. Follow existing test patterns in the codebase.

## Code Style Conventions

- Use type hints where practical.
- Use `dataclass` or `dacite` for data structures (see `data_definitions.py` files throughout the project).
- Django apps follow the standard layout: `models.py`, `views.py`, `urls.py`, `tests.py`, `data_definitions.py`.
- Imports should be organized: stdlib → third-party → local (ruff handles this).

## Dependencies

- Add runtime deps: `uv add <package>`
- Add dev deps: `uv add --dev <package>`
- Never edit `uv.lock` manually.

## Security

Do **not** introduce security vulnerabilities. Follow OWASP best practices:

- **No hardcoded secrets** — use environment variables or Django settings for credentials, tokens, and keys.
- **No SQL injection** — always use Django ORM or parameterized queries. Never interpolate user input into raw SQL.
- **No command injection** — never pass unsanitized input to `subprocess` or `os.system`.
- **Validate and sanitize all external input** — request data, query parameters, headers.
- **No unsafe deserialization** — do not use `pickle.loads`, `yaml.unsafe_load`, or `eval`/`exec` on untrusted data.
- **Use Django's CSRF and authentication** — do not bypass middleware protections.
- **Run `uv run bandit -r . -x ./.venv,./migrations -c pyproject.toml`** to check for security issues and fix any findings before finishing.

## Pre-commit Checklist

Before completing any task, verify:

1. `uv run ruff check .` — zero errors
2. `uv run ruff format --check .` — zero reformats needed
3. `uv run pytest --tb=short` — all tests pass
4. `uv run pytest --cov --cov-report=term-missing --tb=short` — coverage above 80% on modified files
5. `uv run bandit -r . -x ./.venv,./migrations -c pyproject.toml` — no new security issues
