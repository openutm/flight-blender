#!/bin/bash

echo Waiting for DBs...
if ! wait-for-it --parallel --service redis-flight-blender:6379; then
    exit
fi

celery --app=flight_blender beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
