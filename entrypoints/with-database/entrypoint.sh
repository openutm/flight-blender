#!/bin/bash

echo Waiting for DBs...
if ! wait-for-it --parallel --service redis-flight-blender:6379 --service db-flight-blender:5432; then
    exit
fi

# Collect static files
#echo "Collect static files"
#python manage.py collectstatic --noinput

# Apply database migrations
echo "Apply database migrations"
python manage.py migrate

# Start server
echo "Starting server"
python manage.py runserver 0.0.0.0:8000
