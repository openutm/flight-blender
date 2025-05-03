import json
import logging
import time
from dataclasses import asdict
from os import environ as env

import arrow
import pandas as pd
import requests
from dacite import from_dict
from dacite.exceptions import DaciteError, WrongTypeError
from dotenv import find_dotenv, load_dotenv
from pyproj import Transformer

from common.database_operations import FlightBlenderDatabaseWriter
from flight_blender.celery import app

from .data_definitions import SingleAirtrafficObservation

load_dotenv(find_dotenv())

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)


logger = logging.getLogger("django")

#### Airtraffic Endpoint


@app.task(name="write_incoming_air_traffic_data")
def write_incoming_air_traffic_data(observation: str):
    """
    Processes and writes incoming air traffic data.
    This function takes an observation in JSON format, parses it, and writes it to a stream.
    It also trims the stream to keep only the most recent 1000 observations.
    Args:
        observation (str): A JSON string representing the air traffic observation.
    Returns:
        str: The message ID of the added observation.
    """
    my_database_writer = FlightBlenderDatabaseWriter()
    obs = json.loads(observation)
    obs["metadata"] = json.loads(obs["metadata"])

    try:
        single_air_traffic_observation = from_dict(data=obs, data_class=SingleAirtrafficObservation)
    except (
        DaciteError,
        WrongTypeError,
    ) as e:
        logger.error(f"Error parsing observation: {e}")
        return
    logger.info("Parsed observation: %s", single_air_traffic_observation)

    logger.info("Writing observation..")
    # TODO: Write this observation to the Database
    my_database_writer.write_flight_observation(single_air_traffic_observation)


lonlat_to_webmercator = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def mercator_transform(lon, lat):
    x, y = lonlat_to_webmercator.transform(lon, lat)
    return x, y


@app.task(name="start_opensky_network_stream")
def start_opensky_network_stream(view_port: str, session_id: str):
    """
    Starts streaming data from the OpenSky Network within the specified viewport.
    Args:
        view_port (str): A JSON string representing the viewport coordinates in the format
                         [lng_min, lat_min, lng_max, lat_max].
    The function performs the following steps:
    1. Parses the viewport JSON string to extract the coordinates.
    2. Calculates the minimum and maximum longitude and latitude values.
    3. Sets a heartbeat interval for querying the OpenSky Network.
    4. Queries the OpenSky Network API for flight data within the specified viewport for one minute.
    5. Logs the API request URL and response data.
    6. If the response is successful and contains flight data, loads the data into a pandas DataFrame.
    7. Iterates over the DataFrame rows and creates SingleAirtrafficObservation objects.
    8. Submits tasks to write the incoming air traffic data.
    Note:
        - The function uses environment variables for the OpenSky Network username and password.
        - The function logs information and debug messages.
        - The function sleeps for the specified heartbeat interval between API requests.
    """
    view_port = json.loads(view_port)

    lng_min, lat_min, lng_max, lat_max = (
        min(view_port[0], view_port[2]),
        min(view_port[1], view_port[3]),
        max(view_port[0], view_port[2]),
        max(view_port[1], view_port[3]),
    )

    heartbeat = int(env.get("HEARTBEAT_RATE_SECS", 2))
    end_time = arrow.now().shift(seconds=60)

    logger.info("Querying OpenSkies Network for one minute.. ")

    while arrow.now() < end_time:
        url_data = f"https://opensky-network.org/api/states/all?lamin={lat_min}&lomin={lng_min}&lamax={lat_max}&lomax={lng_max}"
        response = requests.get(
            url_data,
            auth=(env.get("OPENSKY_NETWORK_USERNAME", "opensky"), env.get("OPENSKY_NETWORK_PASSWORD", "opensky")),
        )
        logger.info(url_data)
        if response.status_code == 200:
            response_data = response.json()
            logger.debug(response_data)

            if response_data.get("states"):
                col_names = [
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
                flight_df = pd.DataFrame(response_data["states"], columns=col_names).fillna("No Data")
                flight_df["lat"] = flight_df["lat"].astype(float)
                flight_df["long"] = flight_df["long"].astype(float)
                flight_df["baro_altitude"] = flight_df["baro_altitude"].astype(float)

                for _, row in flight_df.iterrows():
                    so = SingleAirtrafficObservation(
                        session_id=session_id,
                        lat_dd=row["lat"],
                        lon_dd=row["long"],
                        altitude_mm=row["baro_altitude"],
                        traffic_source=2,
                        source_type=1,
                        icao_address=str(row["icao24"]),
                        metadata={"velocity": row["velocity"]},
                    )
                    write_incoming_air_traffic_data.delay(json.dumps(asdict(so)))

        time.sleep(heartbeat)
