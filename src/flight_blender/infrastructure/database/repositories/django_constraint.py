import json
from dataclasses import asdict
from typing import Optional

from django.db.models import QuerySet
from django.db.utils import IntegrityError

from flight_blender.constraint.data_definitions import CompositeConstraintPayload, ConstraintDetails
from flight_blender.constraint.data_definitions import Constraint as ConstraintData
from flight_blender.constraint.data_definitions import ConstraintReference as ConstraintReferencePayload
from flight_blender.constraint.models import CompositeConstraint, ConstraintDetail, ConstraintReference
from flight_blender.geo_fence.models import GeoFence


class DjangoConstraintRepository:
    def check_constraint_id_exists(self, constraint_id: str) -> bool:
        return ConstraintDetail.objects.filter(id=constraint_id).exists()

    def get_constraint_by_geofence(self, geofence: GeoFence) -> QuerySet:
        return ConstraintDetail.objects.filter(geofence=geofence)

    def check_constraint_reference_id_exists(self, constraint_reference_id: str) -> bool:
        return ConstraintReference.objects.filter(id=constraint_reference_id).exists()

    def get_constraint_reference_by_id(self, constraint_reference_id: str) -> ConstraintReference:
        return ConstraintReference.objects.get(id=constraint_reference_id)

    def get_constraint_details(self, constraint_id: str) -> ConstraintDetail:
        return ConstraintDetail.objects.get(id=constraint_id)

    def get_geofence_by_constraint_reference_id(self, constraint_reference_id: str) -> Optional[GeoFence]:
        try:
            constraint_reference = ConstraintReference.objects.get(id=constraint_reference_id)
            return GeoFence.objects.get(id=constraint_reference.geofence.id)
        except ConstraintReference.DoesNotExist:
            return None
        except GeoFence.DoesNotExist:
            return None

    def write_constraint_details(self, constraint_id: str, constraint: ConstraintData) -> bool:
        try:
            constraint_obj = ConstraintDetail(
                id=constraint_id,
                details=json.dumps(asdict(constraint.details)),
            )
            constraint_obj.save()
            return True
        except IntegrityError:
            return False

    def write_constraint_reference_details(self, constraint: ConstraintData) -> bool:
        try:
            constraint_reference_obj = ConstraintReference(
                id=constraint.reference.id,
                ovn=constraint.reference.ovn,
                details=json.dumps(asdict(constraint.reference)),
            )
            constraint_reference_obj.save()
            return True
        except IntegrityError:
            return False

    def create_or_update_composite_constraint(self, composite_constraint_payload: CompositeConstraintPayload):
        try:
            composite_constraint_obj = CompositeConstraint(
                constraint_reference_id=composite_constraint_payload.constraint_reference_id,
                constraint_detail_id=composite_constraint_payload.constraint_detail_id,
                declaration_id=composite_constraint_payload.flight_declaration_id,
                bounds=composite_constraint_payload.bounds,
                start_datetime=composite_constraint_payload.start_datetime,
                end_datetime=composite_constraint_payload.start_datetime,
                alt_max=composite_constraint_payload.alt_max,
                alt_min=composite_constraint_payload.alt_min,
            )
            composite_constraint_obj.save()
            return True
        except IntegrityError:
            return False

    def update_constraint_reference_ovn(self, constraint_reference: ConstraintReference, ovn: str) -> bool:
        try:
            constraint_reference.ovn = ovn
            constraint_reference.save()
            return True
        except IntegrityError:
            return False

    def create_or_update_constraint_detail(self, constraint: ConstraintDetails, geofence: GeoFence) -> Optional[ConstraintDetail]:
        try:
            _constraint_volumes = [asdict(_volume) for _volume in constraint.volumes]
            constraint_obj = ConstraintDetail(
                volumes=json.dumps(_constraint_volumes),
                _type=constraint.type,
                geofence=geofence,
            )
            constraint_obj.save()
            return constraint_obj
        except IntegrityError:
            return None

    def create_or_update_constraint_reference(
        self,
        constraint_reference: ConstraintReferencePayload,
        geofence: GeoFence,
        flight_declaration,
    ) -> Optional[ConstraintReference]:
        try:
            constraint_obj = ConstraintReference(
                id=constraint_reference.id,
                ovn=constraint_reference.ovn,
                uss_base_url=constraint_reference.uss_base_url,
                uss_availability=constraint_reference.uss_availability,
                version=constraint_reference.version,
                time_start=constraint_reference.time_start.value,
                time_end=constraint_reference.time_end.value,
                geofence=geofence,
                flight_declaration=flight_declaration,
            )
            constraint_obj.save()
            return constraint_obj
        except IntegrityError:
            return None
