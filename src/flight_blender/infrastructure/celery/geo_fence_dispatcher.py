import json

from flight_blender.geo_fence.tasks import download_geozone_source, write_geo_zone


class CeleryGeoFenceTaskDispatcher:
    def download_geozone_source(self, geo_zone_url: str, geozone_source_id: str) -> None:
        download_geozone_source.delay(geo_zone_url=geo_zone_url, geozone_source_id=geozone_source_id)

    def write_geo_zone(self, geo_zone: dict) -> None:
        write_geo_zone.delay(geo_zone=json.dumps(geo_zone))
