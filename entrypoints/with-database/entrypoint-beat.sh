#!/bin/bash

set -e

source .venv/bin/activate

echo Waiting for DBs...
if ! wait-for-it --parallel --service $REDIS_HOST:$REDIS_PORT --service $POSTGRES_HOST:$POSTGRES_PORT; then
    exit 1
fi

celery --app=flight_blender beat --loglevel=info --schedule=/tmp/celerybeat-schedule
