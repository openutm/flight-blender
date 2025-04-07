#!/bin/bash
FLIGHT_BLENDER_ROOT=.
cp .env.sample .env
chmod +x $FLIGHT_BLENDER_ROOT/entrypoints/with-database/entrypoint.sh
STATUS="$(systemctl is-active postgresql)"
if [ "${STATUS}" = "active" ]; then
    echo "stop local instance of postgresql"
    sudo systemctl stop postgresql
fi
docker-compose down
docker rm -f $(docker ps -a -q)
docker volume rm $(docker volume ls -q)
cd $FLIGHT_BLENDER_ROOT
docker-compose up
