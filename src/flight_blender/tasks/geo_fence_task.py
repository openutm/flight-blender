import json
from dataclasses import asdict

import arrow
import requests
from loguru import logger
from requests.exceptions import ConnectionError
from shapely.geometry import shape
from shapely.ops import unary_union

from flight_blender.celery import app
from flight_blender.domain_types.geo_fence import GeoAwarenessTestStatus, GeoZone
from flight_blender.services.geo_fence_svc import GeoZoneParser
from flight_blender.auth.token_cache import get_redis
from flight_blender.models.geo_fence_orm import GeoFenceORM
from flight_blender.db.session import session_scope


@app.task(name="download_geozone_source")
def download_geozone_source(geo_zone_url: str, geozone_source_id: str):
    r = get_redis()
    geoawareness_test_data_store = "geoawarenes_test." + str(geozone_source_id)
    try:
        geo_zone_request = requests.get(geo_zone_url, timeout=30)
    except ConnectionError as ce:
        logger.error("Error in downloading data from Geofence url")
        logger.error(ce)
        test_status_storage = GeoAwarenessTestStatus(result="Error", message="Error in downloading data")
    else:
        if geo_zone_request.status_code == 200:
            try:
                geo_zone_data = geo_zone_request.json()
                geo_zone_str = json.dumps(geo_zone_data)
                write_geo_zone.delay(geo_zone=geo_zone_str, test_harness_datasource="1")
                test_status_storage = GeoAwarenessTestStatus(result="Ready", message="")
            except Exception:
                test_status_storage = GeoAwarenessTestStatus(result="Error", message="The URL could be ")
        else:
            test_status_storage = GeoAwarenessTestStatus(result="Unsupported", message="")

    if r.exists(geoawareness_test_data_store):
        r.set(geoawareness_test_data_store, json.dumps(asdict(test_status_storage)))


@app.task(name="write_geo_zone")
def write_geo_zone(geo_zone: str, test_harness_datasource: str = "0"):
    geo_zone = json.loads(geo_zone)
    test_harness_datasource = int(test_harness_datasource)
    my_geo_zone_parser = GeoZoneParser(geo_zone=geo_zone)

    parse_response = my_geo_zone_parser.parse_validate_geozone()

    # all_zones_valid = parse_response.all_zones
    processed_geo_zone_features = parse_response.feature_list

    logger.info("Processing %s geozone features.." % len(processed_geo_zone_features))
    for geo_zone_feature in processed_geo_zone_features:
        all_feat_geoms = geo_zone_feature.geometry

        fc = {"type": "FeatureCollection", "features": []}
        all_shapes = []
        for g in all_feat_geoms:
            f = {"type": "Feature", "properties": {}, "geometry": {}}
            s = shape(g["horizontalProjection"])
            f["geometry"] = g["horizontalProjection"]
            fc["features"].append(f)
            all_shapes.append(s)
        u = unary_union(all_shapes)
        bounds = u.bounds
        bounds_str = ",".join([str(x) for x in bounds])

        logger.debug(f"Bounding box for shape.. {bounds}")

        geo_zone = GeoZone(
            title=geo_zone["title"],
            description=geo_zone["description"],
            features=geo_zone_feature,
        )
        name = geo_zone_feature.name
        start_time = arrow.now()
        end_time = start_time.shift(years=1)
        upper_limit = geo_zone_feature["upperLimit"] if "upperLimit" in geo_zone_feature else 300
        lower_limit = geo_zone_feature["lowerLimit"] if "lowerLimit" in geo_zone_feature else 10
        with session_scope() as db:
            db.add(
                GeoFenceORM(
                    geozone=json.dumps(geo_zone_feature),
                    raw_geo_fence=json.dumps(fc),
                    start_datetime=start_time.datetime,
                    end_datetime=end_time.datetime,
                    upper_limit=upper_limit,
                    lower_limit=lower_limit,
                    bounds=bounds_str,
                    name=name,
                    is_test_dataset=bool(test_harness_datasource),
                )
            )

        logger.info("Saved Geofence to database ..")


import json


class CeleryGeoFenceTaskDispatcher:
    def download_geozone_source(self, geo_zone_url: str, geozone_source_id: str) -> None:
        download_geozone_source.delay(geo_zone_url=geo_zone_url, geozone_source_id=geozone_source_id)

    def write_geo_zone(self, geo_zone: dict) -> None:
        write_geo_zone.delay(geo_zone=json.dumps(geo_zone))
