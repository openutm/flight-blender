"""SA-backed database facade.

Thin wrappers around SA sync repos using session_scope() per method.
Kept for callers (dss_rid_helper, rtree_helper, core/ops/flight_feed, rid/tasks)
that have not yet been inlined to SA. SyncDatabaseFacade covers scd/uss.
"""

from loguru import logger  # noqa: F401 — kept for test monkeypatching compatibility


class FlightBlenderDatabaseReader:
    # ─── RID ──────────────────────────────────────────────────────────────────

    def check_flight_details_exist(self, flight_detail_id: str) -> bool:
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.rid import RIDFlightDetailORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            result = db.execute(select(RIDFlightDetailORM).where(RIDFlightDetailORM.id == _to_uuid(flight_detail_id)))
            return result.scalar_one_or_none() is not None

    def get_flight_details_by_id(self, flight_detail_id: str):
        from flight_blender.infrastructure.database.models.rid import RIDFlightDetailORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            obj = db.get(RIDFlightDetailORM, _to_uuid(flight_detail_id))
            if obj is not None:
                db.expunge(obj)
            return obj

    def check_rid_subscription_record_by_view_hash_exists(self, view_hash: int) -> bool:
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.rid import ISASubscriptionORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            result = db.execute(select(ISASubscriptionORM).where(ISASubscriptionORM.view_hash == view_hash))
            return result.scalar_one_or_none() is not None

    def check_rid_subscription_record_by_subscription_id_exists(self, subscription_id: str) -> bool:
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.rid import ISASubscriptionORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            result = db.execute(select(ISASubscriptionORM).where(ISASubscriptionORM.subscription_id == _to_uuid(subscription_id)))
            return result.scalar_one_or_none() is not None

    def get_rid_subscription_record_by_subscription_id(self, subscription_id: str):
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.rid import ISASubscriptionORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            result = db.execute(select(ISASubscriptionORM).where(ISASubscriptionORM.subscription_id == _to_uuid(subscription_id)))
            obj = result.scalar_one_or_none()
            if obj is not None:
                db.expunge(obj)
            return obj

    def get_rid_subscription_record_by_id(self, id: str):
        from flight_blender.infrastructure.database.models.rid import ISASubscriptionORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            obj = db.get(ISASubscriptionORM, _to_uuid(str(id)))
            if obj is not None:
                db.expunge(obj)
            return obj

    def get_all_rid_simulated_subscription_records(self):
        import arrow
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.rid import ISASubscriptionORM
        from flight_blender.infrastructure.database.session import session_scope

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

    # ─── FlightDeclaration ────────────────────────────────────────────────────

    def get_flight_declaration_by_id(self, flight_declaration_id: str):
        from flight_blender.infrastructure.database.models.flight_declarations import FlightDeclarationORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            obj = db.get(FlightDeclarationORM, _to_uuid(flight_declaration_id))
            if obj is not None:
                db.expunge(obj)
            return obj

    def check_flight_declaration_active(self, flight_declaration_id: str, now) -> bool:
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.flight_declarations import FlightDeclarationORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            result = db.execute(
                select(FlightDeclarationORM).where(
                    FlightDeclarationORM.id == _to_uuid(flight_declaration_id),
                    FlightDeclarationORM.start_datetime <= now,
                    FlightDeclarationORM.end_datetime >= now,
                )
            )
            return result.scalar_one_or_none() is not None

    def check_active_activated_flights_exist(self) -> bool:
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.flight_declarations import FlightDeclarationORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            result = db.execute(select(FlightDeclarationORM).where(FlightDeclarationORM.state.in_([1, 2])))
            return result.scalar_one_or_none() is not None

    def get_active_activated_flight_declarations(self):
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.flight_declarations import FlightDeclarationORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            result = db.execute(select(FlightDeclarationORM).where(FlightDeclarationORM.state.in_([1, 2])))
            objs = list(result.scalars().all())
            for o in objs:
                db.expunge(o)
            return objs

    def get_active_rid_observations_for_view(self, start_time, end_time):
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.flight_feed import FlightObservationORM
        from flight_blender.infrastructure.database.session import session_scope

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

    def get_active_rid_observations_for_session_between_interval(self, session_id: str, start_time, end_time):
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.flight_feed import FlightObservationORM
        from flight_blender.infrastructure.database.session import session_scope

        start_dt = start_time.datetime if hasattr(start_time, "datetime") else start_time
        end_dt = end_time.datetime if hasattr(end_time, "datetime") else end_time
        with session_scope() as db:
            result = db.execute(
                select(FlightObservationORM)
                .where(
                    FlightObservationORM.session_id == _to_uuid(session_id),
                    FlightObservationORM.created_at >= start_dt,
                    FlightObservationORM.created_at <= end_dt,
                )
                .order_by(FlightObservationORM.created_at)
            )
            objs = list(result.scalars().all())
            for o in objs:
                db.expunge(o)
            return objs

    def get_closest_flight_observation_for_now(self, now):
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.flight_feed import FlightObservationORM
        from flight_blender.infrastructure.database.session import session_scope

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

    def get_all_flight_observations_in_window(self, start_time, end_time):
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.flight_feed import FlightObservationORM
        from flight_blender.infrastructure.database.session import session_scope

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

    def get_flight_observations(self, after_datetime):
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.flight_feed import FlightObservationORM
        from flight_blender.infrastructure.database.session import session_scope

        cutoff = after_datetime.datetime if hasattr(after_datetime, "datetime") else after_datetime
        with session_scope() as db:
            rows = (
                db.execute(
                    select(FlightObservationORM)
                    .where(FlightObservationORM.created_at >= cutoff)
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

    def get_active_user_notifications_between_interval(self, start_time, end_time):
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.notifications import OperatorRIDNotificationORM
        from flight_blender.infrastructure.database.session import session_scope

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

    def get_surveillance_sensor_by_id(self, sensor_id):
        from flight_blender.infrastructure.database.models.surveillance import SurveillanceSensorORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            obj = db.get(SurveillanceSensorORM, sensor_id)
            if obj is not None:
                db.expunge(obj)
            return obj

    def get_conformance_monitoring_task(self, flight_declaration):
        return None  # Tasks are now Celery-based — no DB row

    def get_flight_operational_intent_reference_by_flight_declaration_obj(self, flight_declaration):
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.flight_declarations import FlightOperationalIntentReferenceORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            result = db.execute(
                select(FlightOperationalIntentReferenceORM).where(
                    FlightOperationalIntentReferenceORM.declaration_id == flight_declaration.id
                )
            )
            obj = result.scalar_one_or_none()
            if obj is not None:
                db.expunge(obj)
            return obj

    def get_flight_operational_intent_reference_by_flight_declaration_id(self, flight_declaration_id: str):
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.flight_declarations import FlightOperationalIntentReferenceORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            result = db.execute(
                select(FlightOperationalIntentReferenceORM).where(
                    FlightOperationalIntentReferenceORM.declaration_id == _to_uuid(flight_declaration_id)
                )
            )
            obj = result.scalar_one_or_none()
            if obj is not None:
                db.expunge(obj)
            return obj


class FlightBlenderDatabaseWriter:
    @staticmethod
    def _normalize_timestamp(ts):
        from flight_blender.infrastructure.database.repositories.sa_flight_feed import _normalize_timestamp

        return _normalize_timestamp(ts)

    # ─── RID ──────────────────────────────────────────────────────────────────

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
        import uuid as _uuid

        from flight_blender.infrastructure.database.models.rid import ISASubscriptionORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            obj = ISASubscriptionORM(
                id=_uuid.UUID(record_id),
                subscription_id=_uuid.UUID(subscription_id),
                view=view,
                view_hash=view_hash,
                end_datetime=end_datetime,
                flight_details=flights_dict,
                is_simulated=is_simulated,
            )
            db.add(obj)
            return True

    def update_flight_details_in_rid_subscription_record(self, existing_subscription_record, flights_dict: str) -> bool:
        from sqlalchemy import select

        from flight_blender.infrastructure.database.models.rid import ISASubscriptionORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            result = db.execute(
                select(ISASubscriptionORM).where(
                    ISASubscriptionORM.subscription_id == existing_subscription_record.subscription_id
                )
            )
            obj = result.scalar_one_or_none()
            if obj is None:
                return False
            obj.flight_details = flights_dict
            return True

    def delete_all_simulated_rid_subscription_records(self) -> bool:
        from sqlalchemy import delete

        from flight_blender.infrastructure.database.models.rid import ISASubscriptionORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            db.execute(delete(ISASubscriptionORM).where(ISASubscriptionORM.is_simulated == True))  # noqa: E712
            return True

    def create_or_update_rid_flight_details(self, rid_flight_details_payload) -> None:
        from flight_blender.infrastructure.database.models.rid import RIDFlightDetailORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            detail_id = _to_uuid(str(rid_flight_details_payload.id))
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
                db.add(RIDFlightDetailORM(
                    id=detail_id,
                    operation_description=rid_flight_details_payload.operation_description,
                    operator_location=ol,
                    operator_id=rid_flight_details_payload.operator_id,
                    auth_data=ad,
                    uas_id=ui,
                    eu_classification=ec,
                ))

    def delete_all_flight_details(self) -> bool:
        from sqlalchemy import delete

        from flight_blender.infrastructure.database.models.rid import RIDFlightDetailORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            db.execute(delete(RIDFlightDetailORM))
            return True

    def delete_all_flight_observations(self) -> bool:
        from sqlalchemy import delete

        from flight_blender.infrastructure.database.models.flight_feed import FlightObservationORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            db.execute(delete(FlightObservationORM))
            return True

    def write_flight_observation(self, single_observation) -> None:
        from flight_blender.infrastructure.database.repositories.sa_flight_feed import SQLAlchemyFlightFeedSyncRepository
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            repo = SQLAlchemyFlightFeedSyncRepository(db)
            repo.write_flight_observation(single_observation)

    def create_operator_rid_notification(self, operator_rid_notification) -> bool:
        import uuid as _uuid

        from flight_blender.infrastructure.database.models.notifications import OperatorRIDNotificationORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            obj = OperatorRIDNotificationORM(
                session_id=_uuid.UUID(str(operator_rid_notification.session_id)) if operator_rid_notification.session_id else None,
                message=operator_rid_notification.message,
            )
            db.add(obj)
            return True

    def update_telemetry_timestamp(self, flight_declaration_id: str) -> bool:
        import arrow

        from flight_blender.infrastructure.database.models.flight_declarations import FlightDeclarationORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            obj = db.get(FlightDeclarationORM, _to_uuid(flight_declaration_id))
            if obj is None:
                return False
            obj.latest_telemetry_datetime = arrow.now().datetime
            return True

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
        from flight_blender.infrastructure.database.models.conformance import ConformanceRecordORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            obj = ConformanceRecordORM(
                flight_declaration_id=_to_uuid(str(flight_declaration.id)),
                conformance_state=conformance_non_conformance_state,
                event_type=event_type,
                description=description,
                geofence_breach=geofence_breach,
                resolved=resolved,
            )
            db.add(obj)
            return True

    def add_flight_declaration_state_history_entry(
        self,
        flight_declaration_id: str,
        original_state: int,
        new_state: int,
        notes: str = "",
    ) -> bool:
        import json

        from flight_blender.infrastructure.database.models.flight_declarations import FlightOperationTrackingORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            obj = FlightOperationTrackingORM(
                flight_declaration_id=_to_uuid(flight_declaration_id),
                notes=notes,
                deltas=json.dumps({"original_state": str(original_state), "new_state": str(new_state)}),
            )
            db.add(obj)
            return True

    def update_flight_operation_state(self, flight_declaration_id: str, state: int) -> bool:
        from flight_blender.infrastructure.database.models.flight_declarations import FlightDeclarationORM
        from flight_blender.infrastructure.database.session import session_scope

        with session_scope() as db:
            obj = db.get(FlightDeclarationORM, _to_uuid(flight_declaration_id))
            if obj is None:
                return False
            obj.state = state
            return True

    def create_flight_operational_intent_reference(
        self,
        flight_declaration,
        created_operational_intent_reference,
    ):
        from flight_blender.infrastructure.database.repositories.sync_facade import SyncDatabaseFacade

        return SyncDatabaseFacade().create_flight_operational_intent_reference_with_submitted_operational_intent(
            flight_declaration=flight_declaration,
            operational_intent_reference_payload=created_operational_intent_reference,
        )

    def create_flight_operational_intent_reference_with_submitted_operational_intent(
        self,
        flight_declaration,
        operational_intent_reference_payload,
    ):
        from flight_blender.infrastructure.database.repositories.sync_facade import SyncDatabaseFacade

        return SyncDatabaseFacade().create_flight_operational_intent_reference_with_submitted_operational_intent(
            flight_declaration=flight_declaration,
            operational_intent_reference_payload=operational_intent_reference_payload,
        )

    def create_flight_operational_intent_details_with_submitted_operational_intent(
        self,
        flight_declaration,
        operational_intent_details_payload,
    ):
        from flight_blender.infrastructure.database.repositories.sync_facade import SyncDatabaseFacade

        return SyncDatabaseFacade().create_flight_operational_intent_details_with_submitted_operational_intent(
            flight_declaration=flight_declaration,
            operational_intent_details_payload=operational_intent_details_payload,
        )

    def create_or_update_composite_operational_intent(self, composite_operational_intent_payload):
        from flight_blender.infrastructure.database.repositories.sync_facade import SyncDatabaseFacade

        return SyncDatabaseFacade().create_or_update_composite_operational_intent(
            composite_operational_intent_payload=composite_operational_intent_payload
        )

    def create_flight_operational_intent_reference_subscribers(
        self,
        flight_declaration,
        subscribers,
    ) -> bool:
        from flight_blender.infrastructure.database.repositories.sync_facade import SyncDatabaseFacade

        return SyncDatabaseFacade().create_flight_operational_intent_reference_subscribers(
            flight_declaration=flight_declaration,
            subscribers=subscribers,
        )

    def create_conformance_monitoring_periodic_task(self, flight_declaration) -> bool:
        import arrow

        from flight_blender.infrastructure.celery.task_scheduler import TaskSchedulerService  # noqa: PLC0415
        expires = arrow.now().shift(hours=6).isoformat()
        return TaskSchedulerService.schedule_conformance_check(
            flight_declaration_id=str(flight_declaration.id),
            session_id=str(flight_declaration.id),
            expires=expires,
        )

    def remove_conformance_monitoring_periodic_task(self, conformance_monitoring_task=None) -> None:
        pass  # no-op: Celery tasks expire naturally


# ─── helpers ──────────────────────────────────────────────────────────────────


def _to_uuid(value: str):
    import uuid

    return uuid.UUID(str(value))


def _serialize_dc(obj) -> str:
    import json
    from dataclasses import asdict

    return json.dumps(asdict(obj)) if obj is not None else json.dumps({})


def _flight_observation_legacy_view(row):
    from types import SimpleNamespace

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
