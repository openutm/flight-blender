#!/bin/bash

source .venv/bin/activate

echo Waiting for DBs...
if ! wait-for-it --parallel --service $REDIS_HOST:$REDIS_PORT; then
    exit
fi

celery -A flight_blender.tasks.celery_app worker \
  --loglevel=info \
  --concurrency=${CELERY_WORKER_CONCURRENCY:-4} \
  --max-tasks-per-child=${CELERY_MAX_TASKS_PER_CHILD:-200}
