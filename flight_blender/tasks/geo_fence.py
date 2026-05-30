"""
Celery tasks for geo fence / geozone operations.
"""

import json

import requests
from loguru import logger

from flight_blender.tasks.celery_app import celery_app


@celery_app.task(name="download_geozone_source", bind=True, max_retries=3)
def download_geozone_source(self, url: str):
    """Download a GeoZone source from a URL and queue it for parsing."""
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        write_geo_zone.delay(data, source_url=url)
    except Exception as exc:
        logger.error("GeoZone download error from %s: %s", url, exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="write_geo_zone", bind=True, max_retries=2)
def write_geo_zone(self, geozone_data: dict, source_url: str = ""):
    """
    Parse an ED-269 GeoZone feature collection and persist GeoFence records.
    """
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from flight_blender.config import get_settings
        from flight_blender.models.geo_fence import GeoFence
        from datetime import datetime

        settings = get_settings()
        sync_url = settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)

        features = geozone_data.get("features", geozone_data.get("GeoZones", []))
        if not features:
            logger.warning("No GeoZone features found in payload from %s", source_url)
            return

        with Session(engine) as session:
            created_count = 0
            for feature in features:
                name = feature.get("name", feature.get("identifier", "Unknown"))
                fence = GeoFence(
                    geozone=json.dumps(feature),
                    upper_limit=float(feature.get("upper_limit_m", 120)),
                    lower_limit=float(feature.get("lower_limit_m", 0)),
                    altitude_ref=0,
                    name=str(name)[:50],
                    bounds="",
                    status=1,
                    start_datetime=datetime.now(),
                    end_datetime=datetime.now(),
                )
                session.add(fence)
                created_count += 1
            session.commit()
            logger.info("Created %d GeoFence records from GeoZone source", created_count)

    except Exception as exc:
        logger.error("GeoZone write error: %s", exc)
        raise self.retry(exc=exc, countdown=10)
