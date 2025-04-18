import json
import logging
from functools import partial

import pyproj
from shapely.geometry import Point, mapping
from shapely.ops import transform

from .data_definitions import (
    ED269Geometry,
    GeoZoneFeature,
    HorizontalProjection,
    ImplicitDict,
    ParseValidateResponse,
    ZoneAuthority,
)

logger = logging.getLogger("django")
proj_wgs84 = pyproj.Proj("+proj=longlat +datum=WGS84")


class GeoZoneParser:
    def __init__(self, geo_zone):
        self.geo_zone = geo_zone

    def parse_validate_geozone(
        self,
    ) -> ParseValidateResponse:
        processed_geo_zone_features: list[GeoZoneFeature] = []
        all_zones_valid: list[bool] = []
        for _geo_zone_feature in self.geo_zone["features"]:
            zone_authorities = _geo_zone_feature["zoneAuthority"]
            all_zone_authorities = []
            for z_a in zone_authorities:
                zone_authority = ImplicitDict.parse(z_a, ZoneAuthority)
                all_zone_authorities.append(zone_authority)
            ed_269_geometries = []

            all_ed_269_geometries = _geo_zone_feature["geometry"]

            for ed_269_geometry in all_ed_269_geometries:
                parse_error = False
                if ed_269_geometry["horizontalProjection"]["type"] == "Polygon":
                    pass
                elif ed_269_geometry["horizontalProjection"]["type"] == "Circle":
                    try:
                        lat = ed_269_geometry["horizontalProjection"]["center"][1]
                        lng = ed_269_geometry["horizontalProjection"]["center"][0]
                        radius = ed_269_geometry["horizontalProjection"]["radius"]
                    except KeyError as ke:
                        logger.info("Error in parsing points provided in the ED 269 file %s" % ke)

                        parse_error = True
                    else:
                        r = radius / 1000  # Radius in km
                        buf = geodesic_point_buffer(lat, lng, r)
                        b = mapping(buf)
                        fc = {
                            "type": "FeatureCollection",
                            "features": [{"type": "Feature", "properties": {}, "geometry": b}],
                        }
                        logger.info("Converting point to circle")
                        logger.debug(json.dumps(fc))
                        ed_269_geometry["horizontalProjection"] = b
                if not parse_error:
                    horizontal_projection = ImplicitDict.parse(ed_269_geometry["horizontalProjection"], HorizontalProjection)
                    parse_error = False
                    ed_269_geometry = ED269Geometry(
                        uomDimensions=ed_269_geometry["uomDimensions"],
                        lowerLimit=ed_269_geometry["lowerLimit"],
                        lowerVerticalReference=ed_269_geometry["lowerVerticalReference"],
                        upperLimit=ed_269_geometry["upperLimit"],
                        upperVerticalReference=ed_269_geometry["upperVerticalReference"],
                        horizontalProjection=horizontal_projection,
                    )
                    ed_269_geometries.append(ed_269_geometry)

            geo_zone_feature = GeoZoneFeature(
                identifier=_geo_zone_feature["identifier"],
                country=_geo_zone_feature["country"],
                name=_geo_zone_feature["name"],
                type=_geo_zone_feature["type"],
                restriction=_geo_zone_feature["restriction"],
                restrictionConditions=_geo_zone_feature["restrictionConditions"],
                region=_geo_zone_feature["region"],
                reason=_geo_zone_feature["reason"],
                otherReasonInfo=_geo_zone_feature["otherReasonInfo"],
                regulationExemption=_geo_zone_feature["regulationExemption"],
                uSpaceClass=_geo_zone_feature["uSpaceClass"],
                message=_geo_zone_feature["message"],
                applicability=_geo_zone_feature["applicability"],
                zoneAuthority=all_zone_authorities,
                geometry=ed_269_geometries,
            )
            processed_geo_zone_features.append(geo_zone_feature)
            all_zones_valid.append(True)

        return ParseValidateResponse(all_zones=all_zones_valid, feature_list=processed_geo_zone_features)


def geodesic_point_buffer(lat, lon, km):
    # Azimuthal equidistant projection
    aeqd_proj = "+proj=aeqd +lat_0={lat} +lon_0={lon} +x_0=0 +y_0=0"
    project = partial(pyproj.transform, pyproj.Proj(aeqd_proj.format(lat=lat, lon=lon)), proj_wgs84)
    buf = Point(0, 0).buffer(km * 1000)  # distance in metres
    return transform(project, buf)


def validate_geo_zone(geo_zone) -> bool:
    """A class to validate GeoZones"""

    if all(k in geo_zone for k in ("title", "description", "features")):
        pass
    else:
        return False

    my_geo_zone_parser = GeoZoneParser(geo_zone=geo_zone)
    parse_response = my_geo_zone_parser.parse_validate_geozone()

    all_zones = parse_response.all_zones
    # processed_geo_zone_features = parse_response.feature_list

    all_zones_valid = all(all_zones)
    if all_zones_valid:
        return True
    else:
        return False
