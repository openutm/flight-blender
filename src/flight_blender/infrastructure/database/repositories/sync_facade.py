"""Synchronous SA-backed database facade.

Session-per-call: each method opens a session_scope() internally so callers
do not need to manage session lifecycle. Objects returned are detached after
the session closes; callers may freely read `.id` and scalar attrs.
"""

import json
import uuid
from dataclasses import asdict
from datetime import datetime
from types import SimpleNamespace
from typing import Optional

import arrow
from sqlalchemy import delete, select

from flight_blender.infrastructure.database.models.conformance import ConformanceRecordORM
from flight_blender.infrastructure.database.models.constraint import CompositeConstraintORM, ConstraintDetailORM, ConstraintReferenceORM
from flight_blender.infrastructure.database.models.flight_declarations import (
    CompositeOperationalIntentORM,
    FDProxy,
    FlightDeclarationORM,
    FlightOperationalIntentDetailORM,
    FlightOperationalIntentReferenceORM,
    FlightOperationTrackingORM,
    PeerCompositeOperationalIntentORM,
    PeerOperationalIntentDetailORM,
    PeerOperationalIntentReferenceORM,
    SubscriberORM,
)
from flight_blender.infrastructure.database.models.flight_feed import FlightObservationORM
from flight_blender.infrastructure.database.models.geo_fence import GeoFenceORM
from flight_blender.infrastructure.database.models.notifications import OperatorRIDNotificationORM
from flight_blender.infrastructure.database.models.rid import ISASubscriptionORM, RIDFlightDetailORM
from flight_blender.infrastructure.database.models.surveillance import SurveillanceSensorORM
from flight_blender.infrastructure.database.repositories.sa_flight_feed import SQLAlchemyFlightFeedSyncRepository
from flight_blender.infrastructure.database.repositories.sa_flight_feed import _normalize_timestamp as _sa_normalize_timestamp
from flight_blender.infrastructure.database.session import session_scope


class _CompositeBundle:
    """Wraps a CompositeOperationalIntentORM with eagerly-loaded related objects.

    Mirrors the Django ORM attribute names used by uss.py helpers.
    """

    def __init__(
        self,
        composite_id: uuid.UUID,
        details: Optional[FlightOperationalIntentDetailORM],
        reference: Optional[FlightOperationalIntentReferenceORM],
        bounds: str = "",
        start_datetime: Optional[datetime] = None,
        end_datetime: Optional[datetime] = None,
        alt_max: float = 0.0,
        alt_min: float = 0.0,
    ) -> None:
        self.id = composite_id
        self._composite_id = composite_id
        self.operational_intent_details = details
        self.operational_intent_reference = reference
        self.bounds = bounds
        self.start_datetime = start_datetime
        self.end_datetime = end_datetime
        self.alt_max = alt_max
        self.alt_min = alt_min


def _serialize_dc(obj) -> str:
    return json.dumps(asdict(obj)) if obj is not None else json.dumps({})


def _flight_observation_legacy_view(row) -> SimpleNamespace:
    return SimpleNamespace(
        id=row.id,
        session_id=row.session_id,
        latitude_dd=row.latitude_dd,
        longitude_dd=row.longitude_dd,
        altitude_mm=row.altitude_mm,
        traffic_source=row.traffic_source,
        source_type=row.source_type,
        icao_address=row.icao_address,
        created_at=row.created_at,
        updated_at=row.updated_at,
        metadata=row.raw_metadata,
    )


def _coerce_datetime(value):
    if isinstance(value, datetime):
        return value
    if hasattr(value, "datetime"):
        return value.datetime
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


class SyncDatabaseFacade:
    """SA-backed database facade: unified reader + writer per method call.

    Each method opens its own session_scope(). Scalar attributes on returned
    objects remain readable after the session closes (SA detach does not clear them).
    """

    # ─── FlightDeclaration ─────────────────────────────────────────────────────

    def get_flight_declaration_by_id(self, flight_declaration_id: str) -> Optional[FlightDeclarationORM]:
        with session_scope() as db:
            obj = db.get(FlightDeclarationORM, uuid.UUID(flight_declaration_id))
            if obj is not None:
                db.expunge(obj)
            return obj

    def create_flight_declaration(self, flight_declaration_creation) -> Optional[FlightDeclarationORM]:
        with session_scope() as db:
            opint = flight_declaration_creation.operational_intent
            raw_geojson = flight_declaration_creation.flight_declaration_raw_geojson
            obj = FlightDeclarationORM(
                id=uuid.UUID(str(flight_declaration_creation.id)),
                operational_intent=json.dumps(opint) if not isinstance(opint, str) else opint,
                flight_declaration_raw_geojson=json.dumps(raw_geojson) if raw_geojson and not isinstance(raw_geojson, str) else raw_geojson,
                bounds=flight_declaration_creation.bounds,
                aircraft_id=flight_declaration_creation.aircraft_id or "unknown",
                state=flight_declaration_creation.state,
            )
            db.add(obj)
            db.flush()
            db.expunge(obj)
            return obj

    def delete_flight_declaration(self, flight_declaration_id: str) -> bool:
        with session_scope() as db:
            obj = db.get(FlightDeclarationORM, uuid.UUID(flight_declaration_id))
            if obj is None:
                return False
            db.delete(obj)
            return True

    def update_flight_operation_state(self, flight_declaration_id: str, state: int) -> bool:
        with session_scope() as db:
            obj = db.get(FlightDeclarationORM, uuid.UUID(flight_declaration_id))
            if obj is None:
                return False
            obj.state = state
            return True

    # ─── FlightOperationalIntentReference ─────────────────────────────────────

    def get_flight_operational_intent_reference_by_flight_declaration_obj(self, flight_declaration) -> Optional[FlightOperationalIntentReferenceORM]:
        with session_scope() as db:
            result = db.execute(
                select(FlightOperationalIntentReferenceORM).where(FlightOperationalIntentReferenceORM.declaration_id == flight_declaration.id)
            )
            ref = result.scalar_one_or_none()
            if ref is not None:
                ref.declaration = FDProxy(ref.declaration_id)
                db.expunge(ref)
            return ref

    def get_flight_operational_intent_reference_by_id(self, opint_ref_id) -> Optional[FlightOperationalIntentReferenceORM]:
        with session_scope() as db:
            ref = db.get(FlightOperationalIntentReferenceORM, uuid.UUID(str(opint_ref_id)))
            if ref is not None:
                ref.declaration = FDProxy(ref.declaration_id)
                db.expunge(ref)
            return ref

    def get_flight_operational_intent_reference_by_flight_declaration_id(
        self, flight_declaration_id: str
    ) -> Optional[FlightOperationalIntentReferenceORM]:
        with session_scope() as db:
            result = db.execute(
                select(FlightOperationalIntentReferenceORM).where(
                    FlightOperationalIntentReferenceORM.declaration_id == uuid.UUID(flight_declaration_id)
                )
            )
            ref = result.scalar_one_or_none()
            if ref is not None:
                ref.declaration = FDProxy(ref.declaration_id)
                db.expunge(ref)
            return ref

    def update_flight_operational_intent_reference(
        self,
        flight_operational_intent_reference,
        update_operational_intent_reference,
    ) -> bool:
        with session_scope() as db:
            ref = db.get(FlightOperationalIntentReferenceORM, flight_operational_intent_reference.id)
            if ref is None:
                return False
            ref.ovn = update_operational_intent_reference.ovn
            ref.state = update_operational_intent_reference.state
            ref.uss_availability = update_operational_intent_reference.uss_availability
            ref.uss_base_url = update_operational_intent_reference.uss_base_url
            ref.version = str(update_operational_intent_reference.version)
            ref.time_start = _coerce_datetime(update_operational_intent_reference.time_start.value)
            ref.time_end = _coerce_datetime(update_operational_intent_reference.time_end.value)
            ref.subscription_id = update_operational_intent_reference.subscription_id
            ref.manager = update_operational_intent_reference.manager
            return True

    def create_flight_operational_intent_reference_with_submitted_operational_intent(
        self,
        flight_declaration,
        operational_intent_reference_payload,
    ) -> Optional[FlightOperationalIntentReferenceORM]:
        with session_scope() as db:
            obj = FlightOperationalIntentReferenceORM(
                id=uuid.UUID(str(operational_intent_reference_payload.id)),
                declaration_id=flight_declaration.id,
                ovn=operational_intent_reference_payload.ovn,
                state=operational_intent_reference_payload.state,
                uss_availability=operational_intent_reference_payload.uss_availability,
                uss_base_url=operational_intent_reference_payload.uss_base_url,
                version=str(operational_intent_reference_payload.version),
                manager=operational_intent_reference_payload.manager,
                time_start=_coerce_datetime(operational_intent_reference_payload.time_start.value),
                time_end=_coerce_datetime(operational_intent_reference_payload.time_end.value),
                subscription_id=operational_intent_reference_payload.subscription_id,
            )
            db.add(obj)
            db.flush()
            obj.declaration = FDProxy(flight_declaration.id)
            db.expunge(obj)
            return obj

    def create_flight_operational_intent_reference_subscribers(
        self,
        flight_declaration,
        subscribers,
    ) -> bool:
        with session_scope() as db:
            result = db.execute(
                select(FlightOperationalIntentReferenceORM).where(FlightOperationalIntentReferenceORM.declaration_id == flight_declaration.id)
            )
            ref = result.scalar_one_or_none()
            if ref is None:
                return False
            for subscriber in subscribers:
                all_subscriptions = [asdict(s) for s in subscriber.subscriptions]
                obj = SubscriberORM(
                    operational_intent_reference_id=ref.id,
                    uss_base_url=subscriber.uss_base_url,
                    subscriptions=json.dumps(all_subscriptions),
                )
                db.add(obj)
            return True

    def get_subscribers_of_operational_intent_reference(self, flight_operational_intent_reference) -> list:
        ref_id = (
            flight_operational_intent_reference.id
            if hasattr(flight_operational_intent_reference, "id")
            else uuid.UUID(str(flight_operational_intent_reference))
        )
        with session_scope() as db:
            result = db.execute(select(SubscriberORM).where(SubscriberORM.operational_intent_reference_id == ref_id))
            objs = list(result.scalars().all())
            for o in objs:
                db.expunge(o)
            return objs

    def check_flight_operational_intent_reference_by_id_exists(self, operational_intent_ref_id: str) -> bool:
        with session_scope() as db:
            result = db.execute(
                select(FlightOperationalIntentReferenceORM).where(FlightOperationalIntentReferenceORM.id == uuid.UUID(operational_intent_ref_id))
            )
            return result.scalar_one_or_none() is not None

    def get_operational_intent_reference_by_id(self, operational_intent_ref_id: str) -> Optional[FlightOperationalIntentReferenceORM]:
        with session_scope() as db:
            ref = db.get(FlightOperationalIntentReferenceORM, uuid.UUID(operational_intent_ref_id))
            if ref is not None:
                ref.declaration = FDProxy(ref.declaration_id)
                db.expunge(ref)
            return ref

    def update_flight_operational_intent_reference_ovn(self, flight_operational_intent_reference, ovn: str) -> bool:
        ref_id = (
            flight_operational_intent_reference.id
            if hasattr(flight_operational_intent_reference, "id")
            else uuid.UUID(str(flight_operational_intent_reference))
        )
        with session_scope() as db:
            obj = db.get(FlightOperationalIntentReferenceORM, ref_id)
            if obj is None:
                return False
            obj.ovn = ovn
            return True

    # ─── FlightOperationalIntentDetail ────────────────────────────────────────

    def get_operational_intent_details_by_flight_declaration_id(self, declaration_id: str) -> Optional[FlightOperationalIntentDetailORM]:
        with session_scope() as db:
            result = db.execute(
                select(FlightOperationalIntentDetailORM).where(FlightOperationalIntentDetailORM.declaration_id == uuid.UUID(declaration_id))
            )
            obj = result.scalar_one_or_none()
            if obj is not None:
                db.expunge(obj)
            return obj

    def create_flight_operational_intent_details_with_submitted_operational_intent(
        self,
        flight_declaration,
        operational_intent_details_payload,
    ) -> Optional[FlightOperationalIntentDetailORM]:
        with session_scope() as db:
            _payload = asdict(operational_intent_details_payload)
            obj = FlightOperationalIntentDetailORM(
                declaration_id=flight_declaration.id,
                volumes=json.dumps(_payload["volumes"]),
                off_nominal_volumes=json.dumps(_payload["off_nominal_volumes"]),
                priority=operational_intent_details_payload.priority,
            )
            db.add(obj)
            db.flush()
            db.expunge(obj)
            return obj

    def update_flight_operational_intent_details(
        self,
        flight_operational_intent_detail,
        operational_intent_details,
    ) -> bool:
        with session_scope() as db:
            obj = db.get(FlightOperationalIntentDetailORM, flight_operational_intent_detail.id)
            if obj is None:
                return False
            _volumes = [asdict(v) for v in (operational_intent_details.volumes or [])]
            _off_nominal = [asdict(v) for v in (operational_intent_details.off_nominal_volumes or [])]
            obj.volumes = json.dumps(_volumes)
            obj.off_nominal_volumes = json.dumps(_off_nominal)
            obj.priority = operational_intent_details.priority
            return True

    # ─── CompositeOperationalIntent ───────────────────────────────────────────

    def get_composite_operational_intent_by_declaration_id(self, flight_declaration_id: str) -> Optional[_CompositeBundle]:
        with session_scope() as db:
            result = db.execute(
                select(CompositeOperationalIntentORM).where(CompositeOperationalIntentORM.declaration_id == uuid.UUID(flight_declaration_id))
            )
            composite = result.scalar_one_or_none()
            if composite is None:
                return None
            details = db.get(FlightOperationalIntentDetailORM, composite.operational_intent_details_id)
            reference = db.get(FlightOperationalIntentReferenceORM, composite.operational_intent_reference_id)
            if details is not None:
                db.expunge(details)
            if reference is not None:
                reference.declaration = FDProxy(reference.declaration_id)
                db.expunge(reference)
            return _CompositeBundle(
                composite.id,
                details,
                reference,
                bounds=composite.bounds,
                start_datetime=composite.start_datetime,
                end_datetime=composite.end_datetime,
                alt_max=composite.alt_max,
                alt_min=composite.alt_min,
            )

    def create_or_update_composite_operational_intent(
        self,
        flight_declaration,
        composite_operational_intent_payload,
    ) -> bool:
        with session_scope() as db:
            payload = composite_operational_intent_payload
            result = db.execute(select(CompositeOperationalIntentORM).where(CompositeOperationalIntentORM.declaration_id == flight_declaration.id))
            existing = result.scalar_one_or_none()
            if existing:
                existing.bounds = str(payload.bounds)
                existing.start_datetime = _coerce_datetime(payload.start_datetime)
                existing.end_datetime = _coerce_datetime(payload.end_datetime)
                existing.alt_max = float(payload.alt_max)
                existing.alt_min = float(payload.alt_min)
                if hasattr(payload, "operational_intent_reference_id") and payload.operational_intent_reference_id:
                    existing.operational_intent_reference_id = uuid.UUID(str(payload.operational_intent_reference_id))
                if hasattr(payload, "operational_intent_details_id") and payload.operational_intent_details_id:
                    existing.operational_intent_details_id = uuid.UUID(str(payload.operational_intent_details_id))
            else:
                ref_id = (
                    uuid.UUID(str(payload.operational_intent_reference_id)) if hasattr(payload, "operational_intent_reference_id") else uuid.uuid4()
                )
                det_id = uuid.UUID(str(payload.operational_intent_details_id)) if hasattr(payload, "operational_intent_details_id") else uuid.uuid4()
                db.add(
                    CompositeOperationalIntentORM(
                        declaration_id=flight_declaration.id,
                        bounds=str(payload.bounds),
                        start_datetime=_coerce_datetime(payload.start_datetime),
                        end_datetime=_coerce_datetime(payload.end_datetime),
                        alt_max=float(payload.alt_max),
                        alt_min=float(payload.alt_min),
                        operational_intent_reference_id=ref_id,
                        operational_intent_details_id=det_id,
                    )
                )
            return True

    # ─── Peer operational intent ───────────────────────────────────────────────

    def create_or_update_peer_operational_intent_details(
        self,
        peer_operational_intent_id: str,
        operational_intent_details,
    ):
        with session_scope() as db:
            _payload = asdict(operational_intent_details)
            peer_id = uuid.UUID(peer_operational_intent_id)
            existing = db.get(PeerOperationalIntentDetailORM, peer_id)
            if existing:
                existing.volumes = json.dumps(_payload["volumes"])
                existing.off_nominal_volumes = json.dumps(_payload["off_nominal_volumes"])
                existing.priority = operational_intent_details.priority
            else:
                db.add(
                    PeerOperationalIntentDetailORM(
                        id=peer_id,
                        volumes=json.dumps(_payload["volumes"]),
                        off_nominal_volumes=json.dumps(_payload["off_nominal_volumes"]),
                        priority=operational_intent_details.priority,
                    )
                )
            return None

    def create_or_update_peer_operational_intent_reference(
        self,
        peer_operational_intent_reference_id: str,
        peer_operational_intent_reference,
    ):
        with session_scope() as db:
            peer_id = uuid.UUID(peer_operational_intent_reference_id)
            existing = db.get(PeerOperationalIntentReferenceORM, peer_id)
            if existing:
                existing.uss_base_url = peer_operational_intent_reference.uss_base_url
                existing.ovn = peer_operational_intent_reference.ovn
                existing.state = peer_operational_intent_reference.state
                existing.uss_availability = peer_operational_intent_reference.uss_availability
                existing.version = str(peer_operational_intent_reference.version)
                existing.time_start = _coerce_datetime(peer_operational_intent_reference.time_start.value)
                existing.time_end = _coerce_datetime(peer_operational_intent_reference.time_end.value)
                existing.subscription_id = peer_operational_intent_reference.subscription_id
            else:
                db.add(
                    PeerOperationalIntentReferenceORM(
                        id=peer_id,
                        uss_base_url=peer_operational_intent_reference.uss_base_url,
                        ovn=peer_operational_intent_reference.ovn,
                        state=peer_operational_intent_reference.state,
                        uss_availability=peer_operational_intent_reference.uss_availability,
                        version=str(peer_operational_intent_reference.version),
                        manager=getattr(peer_operational_intent_reference, "manager", ""),
                        time_start=_coerce_datetime(peer_operational_intent_reference.time_start.value),
                        time_end=_coerce_datetime(peer_operational_intent_reference.time_end.value),
                        subscription_id=peer_operational_intent_reference.subscription_id,
                    )
                )
            return None

    def create_or_update_peer_composite_operational_intent(
        self,
        operation_id: str,
        composite_operational_intent,
    ) -> bool:
        with session_scope() as db:
            peer_id = uuid.UUID(operation_id)
            details = db.get(PeerOperationalIntentDetailORM, peer_id)
            reference = db.get(PeerOperationalIntentReferenceORM, peer_id)
            if details is None or reference is None:
                return False
            result = db.execute(
                select(PeerCompositeOperationalIntentORM).where(PeerCompositeOperationalIntentORM.operational_intent_details_id == peer_id)
            )
            existing = result.scalar_one_or_none()
            payload = composite_operational_intent
            if existing:
                existing.start_datetime = _coerce_datetime(payload.start_datetime)
                existing.end_datetime = _coerce_datetime(payload.end_datetime)
                existing.alt_max = float(payload.alt_max)
                existing.alt_min = float(payload.alt_min)
            else:
                db.add(
                    PeerCompositeOperationalIntentORM(
                        start_datetime=_coerce_datetime(payload.start_datetime),
                        end_datetime=_coerce_datetime(payload.end_datetime),
                        alt_max=float(payload.alt_max),
                        alt_min=float(payload.alt_min),
                        bounds=str(getattr(payload, "bounds", "")),
                        operational_intent_details_id=details.id,
                        operational_intent_reference_id=reference.id,
                    )
                )
            return True

    # ─── Constraint ───────────────────────────────────────────────────────────

    def check_constraint_id_exists(self, constraint_id: str) -> bool:
        with session_scope() as db:
            result = db.execute(select(ConstraintDetailORM).where(ConstraintDetailORM.id == uuid.UUID(constraint_id)))
            return result.scalar_one_or_none() is not None

    def get_constraint_details(self, constraint_id: str) -> Optional[ConstraintDetailORM]:
        with session_scope() as db:
            obj = db.get(ConstraintDetailORM, uuid.UUID(constraint_id))
            if obj is not None:
                db.expunge(obj)
            return obj

    def check_constraint_reference_id_exists(self, constraint_reference_id: str) -> bool:
        with session_scope() as db:
            result = db.execute(select(ConstraintReferenceORM).where(ConstraintReferenceORM.id == uuid.UUID(constraint_reference_id)))
            return result.scalar_one_or_none() is not None

    def write_constraint_details(self, constraint_id: str, constraint) -> None:
        with session_scope() as db:
            existing = db.get(ConstraintDetailORM, uuid.UUID(constraint_id))
            details_json = json.dumps(asdict(constraint)) if hasattr(constraint, "__dataclass_fields__") else json.dumps(constraint)
            if existing:
                existing.details = details_json
            else:
                db.add(ConstraintDetailORM(id=uuid.UUID(constraint_id), details=details_json))

    def write_constraint_reference_details(self, constraint) -> None:
        with session_scope() as db:
            details_json = json.dumps(asdict(constraint)) if hasattr(constraint, "__dataclass_fields__") else json.dumps(constraint)
            db.add(ConstraintReferenceORM(details=details_json))

    def get_constraint_reference_by_id(self, constraint_reference_id: str) -> Optional[ConstraintReferenceORM]:
        with session_scope() as db:
            obj = db.get(ConstraintReferenceORM, uuid.UUID(constraint_reference_id))
            if obj is not None:
                db.expunge(obj)
            return obj

    def get_constraint_by_geofence(self, geofence) -> Optional[ConstraintDetailORM]:
        with session_scope() as db:
            geofence_uuid = geofence.id if hasattr(geofence, "id") else uuid.UUID(str(geofence))
            result = db.execute(select(ConstraintDetailORM).where(ConstraintDetailORM.geofence_id == geofence_uuid))
            obj = result.scalar_one_or_none()
            if obj is not None:
                db.expunge(obj)
            return obj

    def get_geofence_by_constraint_reference_id(self, constraint_reference_id: str) -> Optional[GeoFenceORM]:
        with session_scope() as db:
            ref = db.get(ConstraintReferenceORM, uuid.UUID(constraint_reference_id))
            if ref is None or ref.geofence_id is None:
                return None
            obj = db.get(GeoFenceORM, ref.geofence_id)
            if obj is not None:
                db.expunge(obj)
            return obj

    def update_constraint_reference_ovn(self, constraint_reference, ovn: str) -> bool:
        ref_id = constraint_reference.id if hasattr(constraint_reference, "id") else constraint_reference
        with session_scope() as db:
            obj = db.get(ConstraintReferenceORM, uuid.UUID(str(ref_id)))
            if obj is None:
                return False
            obj.ovn = ovn
            return True

    def create_or_update_constraint_detail(self, constraint, geofence) -> Optional[ConstraintDetailORM]:
        geofence_uuid = geofence.id if hasattr(geofence, "id") else uuid.UUID(str(geofence))
        with session_scope() as db:
            existing = db.execute(select(ConstraintDetailORM).where(ConstraintDetailORM.geofence_id == geofence_uuid)).scalar_one_or_none()
            details_json = json.dumps(asdict(constraint)) if hasattr(constraint, "__dataclass_fields__") else json.dumps(constraint)
            if existing is not None:
                existing.volumes = details_json
                obj = existing
            else:
                obj = ConstraintDetailORM(geofence_id=geofence_uuid, volumes=details_json)
                db.add(obj)
            db.flush()
            db.expunge(obj)
            return obj

    def create_or_update_constraint_reference(self, constraint_reference, geofence, flight_declaration) -> Optional[ConstraintReferenceORM]:
        geofence_uuid = geofence.id if hasattr(geofence, "id") else uuid.UUID(str(geofence))
        ref_id = constraint_reference.id if hasattr(constraint_reference, "id") else uuid.UUID(str(constraint_reference))
        fd_id = flight_declaration.id if hasattr(flight_declaration, "id") else uuid.UUID(str(flight_declaration))
        with session_scope() as db:
            existing = db.get(ConstraintReferenceORM, ref_id)
            if existing is not None:
                existing.ovn = constraint_reference.ovn
                existing.geofence_id = geofence_uuid
                existing.flight_declaration_id = fd_id
                existing.uss_availability = getattr(constraint_reference, "uss_availability", existing.uss_availability)
                existing.uss_base_url = getattr(constraint_reference, "uss_base_url", existing.uss_base_url)
                existing.version = str(getattr(constraint_reference, "version", existing.version))
                existing.manager = getattr(constraint_reference, "manager", existing.manager)
                if hasattr(constraint_reference, "time_start"):
                    existing.time_start = _coerce_datetime(
                        constraint_reference.time_start.value
                        if hasattr(constraint_reference.time_start, "value")
                        else constraint_reference.time_start
                    )
                if hasattr(constraint_reference, "time_end"):
                    existing.time_end = _coerce_datetime(
                        constraint_reference.time_end.value if hasattr(constraint_reference.time_end, "value") else constraint_reference.time_end
                    )
                obj = existing
            else:
                obj = ConstraintReferenceORM(
                    id=ref_id,
                    geofence_id=geofence_uuid,
                    flight_declaration_id=fd_id,
                    ovn=constraint_reference.ovn,
                    uss_availability=getattr(constraint_reference, "uss_availability", ""),
                    uss_base_url=getattr(constraint_reference, "uss_base_url", ""),
                    version=str(getattr(constraint_reference, "version", "")),
                    manager=getattr(constraint_reference, "manager", None),
                    time_start=_coerce_datetime(
                        constraint_reference.time_start.value
                        if hasattr(constraint_reference.time_start, "value")
                        else constraint_reference.time_start
                    ),
                    time_end=_coerce_datetime(
                        constraint_reference.time_end.value if hasattr(constraint_reference.time_end, "value") else constraint_reference.time_end
                    ),
                )
                db.add(obj)
            db.flush()
            db.expunge(obj)
            return obj

    def create_or_update_geofence(self, geofence_payload) -> GeoFenceORM:
        payload_id = getattr(geofence_payload, "id", None)
        with session_scope() as db:
            existing = db.get(GeoFenceORM, uuid.UUID(str(payload_id))) if payload_id is not None else None
            if existing is not None:
                existing.raw_geo_fence = getattr(geofence_payload, "raw_geo_fence", existing.raw_geo_fence)
                existing.geozone = getattr(geofence_payload, "geozone", existing.geozone)
                existing.upper_limit = getattr(geofence_payload, "upper_limit", existing.upper_limit)
                existing.lower_limit = getattr(geofence_payload, "lower_limit", existing.lower_limit)
                existing.altitude_ref = getattr(geofence_payload, "altitude_ref", existing.altitude_ref)
                existing.name = getattr(geofence_payload, "name", existing.name)
                existing.bounds = getattr(geofence_payload, "bounds", existing.bounds)
                existing.status = getattr(geofence_payload, "status", existing.status)
                existing.message = getattr(geofence_payload, "message", existing.message)
                existing.is_test_dataset = getattr(geofence_payload, "is_test_dataset", existing.is_test_dataset)
                if hasattr(geofence_payload, "start_datetime"):
                    existing.start_datetime = _coerce_datetime(geofence_payload.start_datetime)
                if hasattr(geofence_payload, "end_datetime"):
                    existing.end_datetime = _coerce_datetime(geofence_payload.end_datetime)
                obj = existing
            else:
                obj = GeoFenceORM(
                    id=uuid.UUID(str(payload_id)) if payload_id is not None else uuid.uuid4(),
                    raw_geo_fence=getattr(geofence_payload, "raw_geo_fence", None),
                    geozone=getattr(geofence_payload, "geozone", None),
                    upper_limit=getattr(geofence_payload, "upper_limit", 0),
                    lower_limit=getattr(geofence_payload, "lower_limit", 0),
                    altitude_ref=getattr(geofence_payload, "altitude_ref", 0),
                    name=getattr(geofence_payload, "name", "constraint"),
                    bounds=getattr(geofence_payload, "bounds", ""),
                    status=getattr(geofence_payload, "status", 1),
                    message=getattr(geofence_payload, "message", ""),
                    is_test_dataset=getattr(geofence_payload, "is_test_dataset", False),
                    start_datetime=_coerce_datetime(geofence_payload.start_datetime),
                    end_datetime=_coerce_datetime(geofence_payload.end_datetime),
                )
                db.add(obj)
            db.flush()
            db.expunge(obj)
            return obj

    def create_or_update_composite_constraint(self, composite_constraint_payload) -> bool:
        with session_scope() as db:
            ref_id = uuid.UUID(str(composite_constraint_payload.constraint_reference_id))
            det_id = uuid.UUID(str(composite_constraint_payload.constraint_detail_id))
            fd_id = uuid.UUID(str(composite_constraint_payload.flight_declaration_id))
            existing = db.execute(select(CompositeConstraintORM).where(CompositeConstraintORM.constraint_reference_id == ref_id)).scalar_one_or_none()
            if existing is not None:
                existing.declaration_id = fd_id
                existing.bounds = str(composite_constraint_payload.bounds)
                existing.start_datetime = _coerce_datetime(composite_constraint_payload.start_datetime)
                existing.end_datetime = _coerce_datetime(composite_constraint_payload.end_datetime)
                existing.alt_max = float(composite_constraint_payload.alt_max)
                existing.alt_min = float(composite_constraint_payload.alt_min)
                existing.constraint_detail_id = det_id
            else:
                db.add(
                    CompositeConstraintORM(
                        declaration_id=fd_id,
                        bounds=str(composite_constraint_payload.bounds),
                        start_datetime=_coerce_datetime(composite_constraint_payload.start_datetime),
                        end_datetime=_coerce_datetime(composite_constraint_payload.end_datetime),
                        alt_max=float(composite_constraint_payload.alt_max),
                        alt_min=float(composite_constraint_payload.alt_min),
                        constraint_reference_id=ref_id,
                        constraint_detail_id=det_id,
                    )
                )
            return True

    # ─── RID FlightDetail ─────────────────────────────────────────────────────

    def check_flight_details_exist(self, flight_detail_id: str) -> bool:
        with session_scope() as db:
            result = db.execute(select(RIDFlightDetailORM).where(RIDFlightDetailORM.id == uuid.UUID(flight_detail_id)))
            return result.scalar_one_or_none() is not None

    def get_flight_details_by_id(self, flight_detail_id: str) -> Optional[RIDFlightDetailORM]:
        with session_scope() as db:
            obj = db.get(RIDFlightDetailORM, uuid.UUID(flight_detail_id))
            if obj is not None:
                db.expunge(obj)
            return obj

    # ─── ISA subscriptions ────────────────────────────────────────────────────

    def check_rid_subscription_record_by_view_hash_exists(self, view_hash: int) -> bool:
        with session_scope() as db:
            result = db.execute(select(ISASubscriptionORM).where(ISASubscriptionORM.view_hash == view_hash))
            return result.scalar_one_or_none() is not None

    def check_rid_subscription_record_by_subscription_id_exists(self, subscription_id: str) -> bool:
        with session_scope() as db:
            result = db.execute(select(ISASubscriptionORM).where(ISASubscriptionORM.subscription_id == uuid.UUID(subscription_id)))
            return result.scalar_one_or_none() is not None

    def get_rid_subscription_record_by_subscription_id(self, subscription_id: str) -> Optional[ISASubscriptionORM]:
        with session_scope() as db:
            result = db.execute(select(ISASubscriptionORM).where(ISASubscriptionORM.subscription_id == uuid.UUID(subscription_id)))
            obj = result.scalar_one_or_none()
            if obj is not None:
                db.expunge(obj)
            return obj

    def get_rid_subscription_record_by_id(self, id: str) -> Optional[ISASubscriptionORM]:
        with session_scope() as db:
            obj = db.get(ISASubscriptionORM, uuid.UUID(str(id)))
            if obj is not None:
                db.expunge(obj)
            return obj

    def get_all_rid_simulated_subscription_records(self) -> list:
        now = arrow.now().datetime
        with session_scope() as db:
            result = db.execute(
                select(ISASubscriptionORM).where(
                    ISASubscriptionORM.is_simulated == True,  # noqa: E712
                    ISASubscriptionORM.end_datetime >= now,
                    ISASubscriptionORM.created_at <= now,
                )
            )
            objs = list(result.scalars().all())
            for o in objs:
                db.expunge(o)
            return objs

    def create_rid_subscription_record(
        self,
        subscription_id: str,
        record_id: str,
        view: str,
        view_hash: int,
        end_datetime: str,
        flights_dict: str,
        is_simulated: bool,
    ) -> bool:
        with session_scope() as db:
            obj = ISASubscriptionORM(
                id=uuid.UUID(record_id),
                subscription_id=uuid.UUID(subscription_id),
                view=view,
                view_hash=view_hash,
                end_datetime=end_datetime,
                flight_details=flights_dict,
                is_simulated=is_simulated,
            )
            db.add(obj)
            return True

    def update_flight_details_in_rid_subscription_record(self, existing_subscription_record, flights_dict: str) -> bool:
        with session_scope() as db:
            result = db.execute(select(ISASubscriptionORM).where(ISASubscriptionORM.subscription_id == existing_subscription_record.subscription_id))
            obj = result.scalar_one_or_none()
            if obj is None:
                return False
            obj.flight_details = flights_dict
            return True

    def delete_all_simulated_rid_subscription_records(self) -> bool:
        with session_scope() as db:
            db.execute(delete(ISASubscriptionORM).where(ISASubscriptionORM.is_simulated == True))  # noqa: E712
            return True

    # ─── RID flight details ───────────────────────────────────────────────────

    def create_or_update_rid_flight_details(self, rid_flight_details_payload) -> None:
        with session_scope() as db:
            detail_id = uuid.UUID(str(rid_flight_details_payload.id))
            existing = db.get(RIDFlightDetailORM, detail_id)
            ol = _serialize_dc(rid_flight_details_payload.operator_location)
            ad = _serialize_dc(rid_flight_details_payload.auth_data)
            ec = _serialize_dc(rid_flight_details_payload.eu_classification)
            ui = _serialize_dc(rid_flight_details_payload.uas_id)
            if existing:
                existing.operation_description = rid_flight_details_payload.operation_description
                existing.operator_location = ol
                existing.operator_id = rid_flight_details_payload.operator_id
                existing.auth_data = ad
                existing.uas_id = ui
                existing.eu_classification = ec
            else:
                db.add(
                    RIDFlightDetailORM(
                        id=detail_id,
                        operation_description=rid_flight_details_payload.operation_description,
                        operator_location=ol,
                        operator_id=rid_flight_details_payload.operator_id,
                        auth_data=ad,
                        uas_id=ui,
                        eu_classification=ec,
                    )
                )

    def delete_all_flight_details(self) -> bool:
        with session_scope() as db:
            db.execute(delete(RIDFlightDetailORM))
            return True

    # ─── Flight observations ──────────────────────────────────────────────────

    def get_active_rid_observations_for_view(self, start_time, end_time) -> list:
        with session_scope() as db:
            result = db.execute(
                select(FlightObservationORM).where(
                    FlightObservationORM.created_at >= start_time,
                    FlightObservationORM.created_at <= end_time,
                )
            )
            objs = list(result.scalars().all())
            for o in objs:
                db.expunge(o)
            return objs

    def get_active_rid_observations_for_session_between_interval(self, session_id: str, start_time, end_time) -> list:
        start_dt = start_time.datetime if hasattr(start_time, "datetime") else start_time
        end_dt = end_time.datetime if hasattr(end_time, "datetime") else end_time
        with session_scope() as db:
            result = db.execute(
                select(FlightObservationORM)
                .where(
                    FlightObservationORM.session_id == uuid.UUID(session_id),
                    FlightObservationORM.created_at >= start_dt,
                    FlightObservationORM.created_at <= end_dt,
                )
                .order_by(FlightObservationORM.created_at)
            )
            objs = list(result.scalars().all())
            for o in objs:
                db.expunge(o)
            return objs

    def get_closest_flight_observation_for_now(self, now) -> list:
        now_dt = now.datetime if hasattr(now, "datetime") else now
        one_second_before = now.shift(seconds=-1).datetime if hasattr(now, "shift") else now_dt
        with session_scope() as db:
            rows = (
                db.execute(
                    select(FlightObservationORM).where(
                        FlightObservationORM.created_at >= one_second_before,
                        FlightObservationORM.created_at <= now_dt,
                    )
                )
                .scalars()
                .all()
            )
            return [_flight_observation_legacy_view(row) for row in rows]

    def get_all_flight_observations_in_window(self, start_time, end_time) -> list:
        with session_scope() as db:
            rows = (
                db.execute(
                    select(FlightObservationORM).where(
                        FlightObservationORM.created_at >= start_time,
                        FlightObservationORM.created_at <= end_time,
                    )
                )
                .scalars()
                .all()
            )
            return [_flight_observation_legacy_view(row) for row in rows]

    def get_flight_observations(self, after_datetime) -> list:
        cutoff = after_datetime.datetime if hasattr(after_datetime, "datetime") else after_datetime
        with session_scope() as db:
            rows = (
                db.execute(select(FlightObservationORM).where(FlightObservationORM.created_at >= cutoff).order_by(FlightObservationORM.created_at))
                .scalars()
                .all()
            )
            return [
                {
                    "id": str(row.id),
                    "session_id": str(row.session_id) if row.session_id else "",
                    "latitude_dd": row.latitude_dd,
                    "longitude_dd": row.longitude_dd,
                    "altitude_mm": row.altitude_mm,
                    "traffic_source": row.traffic_source,
                    "source_type": row.source_type,
                    "icao_address": row.icao_address,
                    "created_at": row.created_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                    "metadata": row.raw_metadata,
                }
                for row in rows
            ]

    def get_flight_observation_objects(self) -> list:
        with session_scope() as db:
            rows = db.execute(select(FlightObservationORM).order_by(FlightObservationORM.created_at)).scalars().all()
            return [
                {
                    "id": str(row.id),
                    "session_id": str(row.session_id) if row.session_id else "",
                    "latitude_dd": row.latitude_dd,
                    "longitude_dd": row.longitude_dd,
                    "altitude_mm": row.altitude_mm,
                    "traffic_source": row.traffic_source,
                    "source_type": row.source_type,
                    "icao_address": row.icao_address,
                    "created_at": row.created_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                    "metadata": row.raw_metadata,
                }
                for row in rows
            ]

    def get_latest_flight_observation_by_session(self, session_id: str):
        with session_scope() as db:
            row = (
                db.execute(
                    select(FlightObservationORM)
                    .where(FlightObservationORM.session_id == uuid.UUID(session_id))
                    .order_by(FlightObservationORM.created_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            db.expunge(row)
            return row

    def get_temporal_flight_observations_by_session(self, session_id: str, after_datetime) -> list:
        cutoff = after_datetime.datetime if hasattr(after_datetime, "datetime") else after_datetime
        with session_scope() as db:
            rows = (
                db.execute(
                    select(FlightObservationORM)
                    .where(
                        FlightObservationORM.session_id == uuid.UUID(session_id),
                        FlightObservationORM.created_at >= cutoff,
                    )
                    .order_by(FlightObservationORM.created_at)
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": str(row.id),
                    "session_id": str(row.session_id) if row.session_id else "",
                    "latitude_dd": row.latitude_dd,
                    "longitude_dd": row.longitude_dd,
                    "altitude_mm": row.altitude_mm,
                    "traffic_source": row.traffic_source,
                    "source_type": row.source_type,
                    "icao_address": row.icao_address,
                    "created_at": row.created_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                    "metadata": row.raw_metadata,
                }
                for row in rows
            ]

    def delete_all_flight_observations(self) -> bool:
        with session_scope() as db:
            db.execute(delete(FlightObservationORM))
            return True

    def write_flight_observation(self, single_observation) -> None:
        with session_scope() as db:
            repo = SQLAlchemyFlightFeedSyncRepository(db)
            repo.write_flight_observation(single_observation)

    # ─── Notifications ────────────────────────────────────────────────────────

    def get_active_user_notifications_between_interval(self, start_time, end_time) -> list:
        with session_scope() as db:
            result = db.execute(
                select(OperatorRIDNotificationORM).where(
                    OperatorRIDNotificationORM.is_active == True,  # noqa: E712
                    OperatorRIDNotificationORM.created_at >= start_time,
                    OperatorRIDNotificationORM.created_at <= end_time,
                )
            )
            objs = list(result.scalars().all())
            for o in objs:
                db.expunge(o)
            return objs

    def create_operator_rid_notification(self, operator_rid_notification) -> bool:
        with session_scope() as db:
            obj = OperatorRIDNotificationORM(
                session_id=uuid.UUID(str(operator_rid_notification.session_id)) if operator_rid_notification.session_id else None,
                message=operator_rid_notification.message,
            )
            db.add(obj)
            return True

    # ─── Surveillance ─────────────────────────────────────────────────────────

    def get_surveillance_sensor_by_id(self, sensor_id) -> Optional[SurveillanceSensorORM]:
        with session_scope() as db:
            obj = db.get(SurveillanceSensorORM, sensor_id)
            if obj is not None:
                db.expunge(obj)
            return obj

    # ─── Conformance ──────────────────────────────────────────────────────────

    def get_conformance_monitoring_task(self, flight_declaration):
        return None  # Tasks are Celery-based — no DB row

    def write_flight_conformance_record(
        self,
        flight_declaration,
        conformance_non_conformance_state: int,
        event_type: str,
        description: str,
        geofence_breach: bool,
        geofence,
        resolved: bool,
    ) -> bool:
        with session_scope() as db:
            obj = ConformanceRecordORM(
                flight_declaration_id=uuid.UUID(str(flight_declaration.id)),
                conformance_state=conformance_non_conformance_state,
                event_type=event_type,
                description=description,
                geofence_breach=geofence_breach,
                resolved=resolved,
            )
            db.add(obj)
            return True

    def create_conformance_monitoring_periodic_task(self, flight_declaration) -> bool:
        from flight_blender.infrastructure.celery.task_scheduler import TaskSchedulerService

        expires = arrow.now().shift(hours=6).isoformat()
        return TaskSchedulerService.schedule_conformance_check(
            flight_declaration_id=str(flight_declaration.id),
            session_id=str(flight_declaration.id),
            expires=expires,
        )

    def remove_conformance_monitoring_periodic_task(self, conformance_monitoring_task=None) -> None:
        pass  # no-op: Celery tasks expire naturally

    # ─── Geofence ─────────────────────────────────────────────────────────────

    def get_active_geofences(self) -> list:
        with session_scope() as db:
            result = db.execute(select(GeoFenceORM).where(GeoFenceORM.status == 0))
            objs = list(result.scalars().all())
            for o in objs:
                db.expunge(o)
            return objs

    # ─── FlightDeclaration extras ─────────────────────────────────────────────

    def check_flight_declaration_active(self, flight_declaration_id: str, now) -> bool:
        with session_scope() as db:
            result = db.execute(
                select(FlightDeclarationORM).where(
                    FlightDeclarationORM.id == uuid.UUID(flight_declaration_id),
                    FlightDeclarationORM.start_datetime <= now,
                    FlightDeclarationORM.end_datetime >= now,
                )
            )
            return result.scalar_one_or_none() is not None

    def check_active_activated_flights_exist(self) -> bool:
        with session_scope() as db:
            result = db.execute(select(FlightDeclarationORM).where(FlightDeclarationORM.state.in_([1, 2])))
            return result.scalar_one_or_none() is not None

    def get_active_activated_flight_declarations(self) -> list:
        with session_scope() as db:
            result = db.execute(select(FlightDeclarationORM).where(FlightDeclarationORM.state.in_([1, 2])))
            objs = list(result.scalars().all())
            for o in objs:
                db.expunge(o)
            return objs

    def update_telemetry_timestamp(self, flight_declaration_id: str) -> bool:
        with session_scope() as db:
            obj = db.get(FlightDeclarationORM, uuid.UUID(flight_declaration_id))
            if obj is None:
                return False
            obj.latest_telemetry_datetime = arrow.now().datetime
            return True

    def add_flight_declaration_state_history_entry(
        self,
        flight_declaration_id: str,
        original_state: int,
        new_state: int,
        notes: str = "",
    ) -> bool:
        with session_scope() as db:
            obj = FlightOperationTrackingORM(
                flight_declaration_id=uuid.UUID(flight_declaration_id),
                notes=notes,
                deltas=json.dumps({"original_state": str(original_state), "new_state": str(new_state)}),
            )
            db.add(obj)
            return True

    def create_flight_operational_intent_reference(
        self,
        flight_declaration,
        created_operational_intent_reference,
    ):
        return self.create_flight_operational_intent_reference_with_submitted_operational_intent(
            flight_declaration=flight_declaration,
            operational_intent_reference_payload=created_operational_intent_reference,
        )

    # ─── Normalize timestamp (compat static method) ───────────────────────────

    @staticmethod
    def _normalize_timestamp(ts):
        return _sa_normalize_timestamp(ts)
