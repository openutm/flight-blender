#!/bin/bash

set -e

source .venv/bin/activate

echo Waiting for DBs...
if ! wait-for-it --parallel --service $REDIS_HOST:$REDIS_PORT --service $POSTGRES_HOST:$POSTGRES_PORT; then
    exit 1
fi

# Apply database migrations
echo "Apply database migrations"
alembic upgrade head
echo "Database migrations applied"

# Start server
echo "Starting server"
uvicorn flight_blender.asgi:application --host 0.0.0.0 --port 8000 --workers 3 --reload
