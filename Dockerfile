# Build stage: install Python dependencies only
# IMPORTANT: WORKDIR must match the runtime /app so that venv script
# shebangs (#!/app/.venv/bin/python) resolve correctly at runtime.
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install -U pip --no-cache-dir && pip install uv --no-cache-dir

COPY uv.lock pyproject.toml ./
RUN uv sync --frozen --no-install-project --no-dev

# Runtime stage: minimal production image
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN groupadd --gid 10000 app && \
    useradd --uid 10000 --gid 10000 --shell /bin/bash \
            --create-home --no-log-init app && \
    chown -R app:app /app

# Copy the pre-built virtual environment from the builder stage
COPY --from=builder --chown=app:app /app/.venv /app/.venv

USER app:app

COPY --chown=app:app . .

# Activate the virtual environment so all commands use it
ENV PATH="/app/.venv/bin:$PATH"
# src layout: flight_blender package lives under src/ and is not installed
# into the venv (--no-install-project in builder), so make it importable.
ENV PYTHONPATH="/app/src"

EXPOSE 8000

CMD ["uvicorn", "flight_blender.main:app", "--host", "0.0.0.0", "--port", "8000"]
