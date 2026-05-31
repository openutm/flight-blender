#!/bin/bash

source .venv/bin/activate

echo Waiting for DBs...
if ! wait-for-it --parallel --service $REDIS_HOST:$REDIS_PORT; then
    exit
fi

celery -A flight_blender.tasks.celery_app beat --loglevel=info
