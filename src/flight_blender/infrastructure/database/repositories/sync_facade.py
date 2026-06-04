"""Synchronous SA-backed replacement for FlightBlenderDatabaseReader/Writer.

Used by scd.py and uss.py sync helpers (wrapped in sync_to_async).
Session-per-call: each method opens a session_scope() internally so callers
do not need to manage session lifecycle. Objects returned are detached after
the session closes; callers may freely read `.id` and scalar attrs.
Methods that receive an existing ORM object re-load it by ID in the new session.
"""

import json
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from flight_blender.infrastructure.database.models.constraint import (
    ConstraintDetailORM,
    ConstraintReferenceORM,
)
from flight_blender.infrastructure.database.models.flight_declarations import (
    CompositeOperationalIntentORM,
    FlightDeclarationORM,
    FlightOperationalIntentDetailORM,
    FlightOperationalIntentReferenceORM,
    PeerCompositeOperationalIntentORM,
    PeerOperationalIntentDetailORM,
    PeerOperationalIntentReferenceORM,
    SubscriberORM,
)
from flight_blender.infrastructure.database.models.rid import RIDFlightDetailORM
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
    ) -> None:
        self._composite_id = composite_id
        self.operational_intent_details = details
        self.operational_intent_reference = reference


class _FDProxy:
    """Proxy so `reference.declaration.id` works on an SA reference row."""

    def __init__(self, declaration_id: uuid.UUID) -> None:
        self.id = declaration_id


def _coerce_datetime(value):
    if isinstance(value, datetime):
        return value
    if hasattr(value, "datetime"):
        return value.datetime
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


class SyncDatabaseFacade:
    """Drop-in SA replacement for FlightBlenderDatabaseReader + FlightBlenderDatabaseWriter.

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

    def get_flight_operational_intent_reference_by_flight_declaration_obj(
        self, flight_declaration
    ) -> Optional[FlightOperationalIntentReferenceORM]:
        with session_scope() as db:
            result = db.execute(
                select(FlightOperationalIntentReferenceORM).where(
                    FlightOperationalIntentReferenceORM.declaration_id == flight_declaration.id
                )
            )
            ref = result.scalar_one_or_none()
            if ref is not None:
                ref.declaration = _FDProxy(ref.declaration_id)
                db.expunge(ref)
            return ref

    def get_flight_operational_intent_reference_by_id(self, opint_ref_id) -> Optional[FlightOperationalIntentReferenceORM]:
        with session_scope() as db:
            ref = db.get(FlightOperationalIntentReferenceORM, uuid.UUID(str(opint_ref_id)))
            if ref is not None:
                ref.declaration = _FDProxy(ref.declaration_id)
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
                ref.declaration = _FDProxy(ref.declaration_id)
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
            obj.declaration = _FDProxy(flight_declaration.id)
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

    # ─── FlightOperationalIntentDetail ────────────────────────────────────────

    def get_operational_intent_details_by_flight_declaration_id(
        self, declaration_id: str
    ) -> Optional[FlightOperationalIntentDetailORM]:
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
                reference.declaration = _FDProxy(reference.declaration_id)
                db.expunge(reference)
            return _CompositeBundle(composite.id, details, reference)

    def create_or_update_composite_operational_intent(
        self,
        flight_declaration,
        composite_operational_intent_payload,
    ) -> bool:
        with session_scope() as db:
            payload = composite_operational_intent_payload
            result = db.execute(
                select(CompositeOperationalIntentORM).where(CompositeOperationalIntentORM.declaration_id == flight_declaration.id)
            )
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
                ref_id = uuid.UUID(str(payload.operational_intent_reference_id)) if hasattr(payload, "operational_intent_reference_id") else uuid.uuid4()
                det_id = uuid.UUID(str(payload.operational_intent_details_id)) if hasattr(payload, "operational_intent_details_id") else uuid.uuid4()
                db.add(CompositeOperationalIntentORM(
                    declaration_id=flight_declaration.id,
                    bounds=str(payload.bounds),
                    start_datetime=_coerce_datetime(payload.start_datetime),
                    end_datetime=_coerce_datetime(payload.end_datetime),
                    alt_max=float(payload.alt_max),
                    alt_min=float(payload.alt_min),
                    operational_intent_reference_id=ref_id,
                    operational_intent_details_id=det_id,
                ))
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
                db.add(PeerOperationalIntentDetailORM(
                    id=peer_id,
                    volumes=json.dumps(_payload["volumes"]),
                    off_nominal_volumes=json.dumps(_payload["off_nominal_volumes"]),
                    priority=operational_intent_details.priority,
                ))
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
                db.add(PeerOperationalIntentReferenceORM(
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
                ))
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
                db.add(PeerCompositeOperationalIntentORM(
                    start_datetime=_coerce_datetime(payload.start_datetime),
                    end_datetime=_coerce_datetime(payload.end_datetime),
                    alt_max=float(payload.alt_max),
                    alt_min=float(payload.alt_min),
                    bounds=str(getattr(payload, "bounds", "")),
                    operational_intent_details_id=details.id,
                    operational_intent_reference_id=reference.id,
                ))
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
