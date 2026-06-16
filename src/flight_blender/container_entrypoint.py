import argparse
import os
import socket
import sys
import time

from alembic.config import Config

from alembic import command
from flight_blender.config import settings

LEGACY_MODES = {
    "./entrypoints/with-database/entrypoint.sh": "serve",
    "entrypoints/with-database/entrypoint.sh": "serve",
    "./entrypoints/with-database/entrypoint-prod.sh": "serve",
    "entrypoints/with-database/entrypoint-prod.sh": "serve",
    "./entrypoints/no-database/entrypoint.sh": "serve",
    "entrypoints/no-database/entrypoint.sh": "serve",
    "./entrypoints/with-database/entrypoint-celery.sh": "worker",
    "entrypoints/with-database/entrypoint-celery.sh": "worker",
    "./entrypoints/no-database/entrypoint-celery.sh": "worker",
    "entrypoints/no-database/entrypoint-celery.sh": "worker",
    "./entrypoints/with-database/entrypoint-beat.sh": "beat",
    "entrypoints/with-database/entrypoint-beat.sh": "beat",
    "./entrypoints/no-database/entrypoint-beat.sh": "beat",
    "entrypoints/no-database/entrypoint-beat.sh": "beat",
}


def _wait_for_service(host: str, port: int) -> None:
    deadline = time.monotonic() + settings.CONTAINER_STARTUP_TIMEOUT_SECS
    last_error: OSError | None = None

    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(1)

    detail = f": {last_error}" if last_error else ""
    raise TimeoutError(f"Timed out waiting for {host}:{port}{detail}")


def _wait_for_dependencies(include_postgres: bool) -> None:
    print("Waiting for Redis...", flush=True)
    _wait_for_service(settings.REDIS_HOST, settings.REDIS_PORT)

    if include_postgres:
        print("Waiting for Postgres...", flush=True)
        _wait_for_service(settings.POSTGRES_HOST, settings.POSTGRES_PORT)


def _run_migrations() -> None:
    print("Applying database migrations", flush=True)
    command.upgrade(Config("alembic.ini"), "head")
    print("Database migrations applied", flush=True)


def _exec(args: list[str]) -> None:
    print(f"Starting {' '.join(args)}", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    # Fixed argv is assembled internally for container process replacement.
    os.execvp(args[0], args)  # nosec B606


def _serve(reload: bool) -> None:
    _wait_for_dependencies(include_postgres=True)
    _run_migrations()

    args = [
        sys.executable,
        "-m",
        "uvicorn",
        "flight_blender.asgi:application",
        "--host",
        settings.UVICORN_HOST,
        "--port",
        str(settings.UVICORN_PORT),
    ]
    if reload:
        args.append("--reload")
    else:
        args.extend(["--workers", str(settings.UVICORN_WORKERS)])
    _exec(args)


def _worker() -> None:
    _wait_for_dependencies(include_postgres=False)
    _exec(
        [
            sys.executable,
            "-m",
            "celery",
            "--app=flight_blender",
            "worker",
            "--loglevel=info",
            f"--concurrency={settings.CELERY_WORKER_CONCURRENCY}",
            f"--max-tasks-per-child={settings.CELERY_MAX_TASKS_PER_CHILD}",
        ]
    )


def _beat() -> None:
    _wait_for_dependencies(include_postgres=True)
    _exec([sys.executable, "-m", "celery", "--app=flight_blender", "beat", "--loglevel=info", "--schedule=/tmp/celerybeat-schedule"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["serve", "serve-reload", "worker", "beat", *LEGACY_MODES])
    args = parser.parse_args()
    mode = LEGACY_MODES.get(args.mode, args.mode)

    if mode == "serve":
        _serve(reload=False)
    elif mode == "serve-reload":
        _serve(reload=True)
    elif mode == "worker":
        _worker()
    else:
        _beat()


if __name__ == "__main__":
    main()
