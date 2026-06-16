# syntax=docker/dockerfile:1.7

ARG PYTHON_BUILDER_IMAGE=cgr.dev/chainguard/python:latest-dev
ARG PYTHON_RUNTIME_IMAGE=cgr.dev/chainguard/python:latest
ARG SHELL_COMPAT_IMAGE=busybox:1.37.0-musl

FROM ${PYTHON_BUILDER_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependency layers stay cached unless the dependency metadata or lockfile changes.
COPY uv.lock pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY LICENSE README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
RUN find /app/.venv -type d \( -name test -o -name tests \) -prune -exec rm -rf '{}' + \
    && find /app/.venv -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.a' \) -delete \
    && find /app/.venv -type f \( -name '*.so' -o -name '*.so.*' \) -exec strip --strip-unneeded '{}' +

FROM ${SHELL_COMPAT_IMAGE} AS shell-compat

FROM ${PYTHON_RUNTIME_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/venv/bin:${PATH}" \
    VIRTUAL_ENV="/venv"

WORKDIR /app

COPY --from=shell-compat /bin/busybox /bin/sh
COPY --from=builder --chown=65532:65532 /app/.venv /venv
COPY --chown=65532:65532 src ./src
COPY --chown=65532:65532 alembic ./alembic
COPY --chown=65532:65532 alembic.ini LICENSE README.md ./

EXPOSE 8000

ENTRYPOINT ["/venv/bin/python", "-m", "flight_blender.container_entrypoint"]
CMD ["serve"]
