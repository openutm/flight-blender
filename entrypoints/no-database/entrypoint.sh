#!/bin/bash

source .venv/bin/activate

echo Waiting for DBs...
if ! wait-for-it --parallel --service $REDIS_HOST:$REDIS_PORT; then
    exit
fi

echo "Starting server"
uvicorn flight_blender.main:app --host 0.0.0.0 --port 8000 --workers 3
