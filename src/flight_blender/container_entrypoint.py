import argparse
import os
import socket
import sys
import time

from alembic.config import Config

from alembic import command
from flight_blender.config import settings


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
    os.execvp(args[0], args)


def _serve(reload: bool) -> None:
    _wait_for_dependencies(include_postgres=True)
    _run_migrations()

    args = [
        "uvicorn",
        "flight_blender.asgi:application",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
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
    _exec(["celery", "--app=flight_blender", "beat", "--loglevel=info", "--schedule=/tmp/celerybeat-schedule"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["serve", "serve-reload", "worker", "beat"])
    args = parser.parse_args()

    if args.mode == "serve":
        _serve(reload=False)
    elif args.mode == "serve-reload":
        _serve(reload=True)
    elif args.mode == "worker":
        _worker()
    else:
        _beat()


if __name__ == "__main__":
    main()
