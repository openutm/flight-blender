FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install -U pip && pip install uv

RUN addgroup --gid 10000 django && adduser --shell /bin/bash --disabled-password --gecos "" --uid 10000 --ingroup django django
RUN chown -R django:django /app
USER django:django

# Install dependencies (cached layer — only invalidated when lockfile changes)
COPY --chown=django:django uv.lock pyproject.toml LICENSE README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source, then install the project itself (fast — deps already in .venv)
COPY --chown=django:django . .
RUN uv sync --frozen --no-dev

EXPOSE 8000
