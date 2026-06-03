import json
from typing import Optional

import arrow
from django.db.utils import IntegrityError

from flight_blender.common.utils import EnhancedJSONEncoder
from flight_blender.geo_fence.data_definitions import GeofencePayload
from flight_blender.geo_fence.models import GeoFence


class DjangoGeoFenceRepository:
    def get_active_geofences(self) -> list:
        now = arrow.now()
        return GeoFence.objects.filter(start_datetime__lte=now.isoformat(), end_datetime__gte=now.isoformat())

    def create_or_update_geofence(self, geofence_payload: GeofencePayload) -> Optional[GeoFence]:
        try:
            geofence = GeoFence(
                raw_geo_fence=json.dumps(geofence_payload.raw_geo_fence),
                id=geofence_payload.id,
                upper_limit=geofence_payload.upper_limit,
                lower_limit=geofence_payload.lower_limit,
                altitude_ref=geofence_payload.altitude_ref,
                bounds=geofence_payload.bounds,
                status=geofence_payload.status,
                message=geofence_payload.message,
                is_test_dataset=geofence_payload.is_test_dataset,
                start_datetime=geofence_payload.start_datetime.value,
                end_datetime=geofence_payload.end_datetime.value,
                geozone=json.dumps(geofence_payload.geozone, cls=EnhancedJSONEncoder),
            )
            geofence.save()
            return geofence
        except IntegrityError:
            return None
