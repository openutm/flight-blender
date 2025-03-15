#!/bin/bash

echo Waiting for DBs...
if ! wait-for-it --parallel --service redis-blender:6379; then
    exit
fi

celery --app=flight_blender worker --loglevel=info
