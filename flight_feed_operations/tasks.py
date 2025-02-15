import json
import logging
import time
from dataclasses import asdict
from os import environ as env

import arrow
import pandas as pd
import requests
from dotenv import find_dotenv, load_dotenv
from pyproj import Transformer

from flight_blender.celery import app

from . import flight_stream_helper
from .data_definitions import SingleAirtrafficObservation

load_dotenv(find_dotenv())

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)

logger = logging.getLogger("django")

#### Airtraffic Endpoint


@app.task(name="write_incoming_air_traffic_data")
def write_incoming_air_traffic_data(observation):
    obs = json.loads(observation)
    logger.debug("Writing observation..")

    my_stream_ops = flight_stream_helper.StreamHelperOps()
    cg = my_stream_ops.get_pull_cg()
    msg_id = cg.all_observations.add(obs)
    cg.all_observations.trim(1000)
    return msg_id


lonlat_to_webmercator = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def mercator_transform(lon, lat):
    x, y = lonlat_to_webmercator.transform(lon, lat)
    return x, y


@app.task(name="start_opensky_network_stream")
def start_opensky_network_stream(view_port: str):
    view_port = json.loads(view_port)

    # submit task to write to the flight stream
    lng_min = min(view_port[0], view_port[2])
    lng_max = max(view_port[0], view_port[2])
    lat_min = min(view_port[1], view_port[3])
    lat_max = max(view_port[1], view_port[3])

    heartbeat = env.get("HEARTBEAT_RATE_SECS", 2)
    heartbeat = int(heartbeat)
    now = arrow.now()
    two_minutes_from_now = now.shift(seconds=60)

    logger.info("Querying OpenSkies Network for one minute.. ")

    while arrow.now() < two_minutes_from_now:
        url_data = (
            "https://opensky-network.org/api/states/all?"
            + "lamin="
            + str(lat_min)
            + "&lomin="
            + str(lng_min)
            + "&lamax="
            + str(lat_max)
            + "&lomax="
            + str(lng_max)
        )
        openskies_username = env.get("OPENSKY_NETWORK_USERNAME")
        openskies_password = env.get("OPENSKY_NETWORK_PASSWORD")
        response = requests.get(url_data, auth=(openskies_username, openskies_password))
        logger.info(url_data)
        # LOAD TO PANDAS DATAFRAME
        col_name = [
            "icao24",
            "callsign",
            "origin_country",
            "time_position",
            "last_contact",
            "long",
            "lat",
            "baro_altitude",
            "on_ground",
            "velocity",
            "true_track",
            "vertical_rate",
            "sensors",
            "geo_altitude",
            "squawk",
            "spi",
            "position_source",
        ]

        response_data = response.json()
        logger.debug(response_data)

        if response.status_code == 200:
            if response_data["states"] is not None:
                flight_df = pd.DataFrame(response_data["states"], columns=col_name)
                flight_df = flight_df.fillna("No Data")
                for index, row in flight_df.iterrows():
                    metadata = {"velocity": row["velocity"]}
                    lat_dd = row["lat"]
                    lon_dd = row["long"]
                    altitude_mm = row["baro_altitude"]
                    traffic_source = 2
                    source_type = 1
                    icao_address = row["icao24"]

                    so = SingleAirtrafficObservation(
                        lat_dd=lat_dd,
                        lon_dd=lon_dd,
                        altitude_mm=altitude_mm,
                        traffic_source=traffic_source,
                        source_type=source_type,
                        icao_address=icao_address,
                        metadata=json.dumps(metadata),
                    )

                    write_incoming_air_traffic_data.delay(json.dumps(asdict(so)))

        time.sleep(heartbeat)
