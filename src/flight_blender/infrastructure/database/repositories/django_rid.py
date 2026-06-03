import json
import os
from typing import Optional
from uuid import UUID

import arrow
from django.db.utils import IntegrityError
from loguru import logger

from flight_blender.conformance.models import TaskScheduler
from flight_blender.rid.models import ISASubscription, RIDFlightDetail
from flight_blender.rid.rid_utils import RIDFlightDetails


class DjangoRIDRepository:
    def check_flight_details_exist(self, flight_detail_id: str) -> bool:
        return RIDFlightDetail.objects.filter(id=flight_detail_id).exists()

    def get_flight_details_by_id(self, flight_detail_id: str) -> RIDFlightDetail:
        return RIDFlightDetail.objects.get(id=flight_detail_id)

    def get_rid_monitoring_task(self, session_id: UUID) -> Optional[TaskScheduler]:
        try:
            return TaskScheduler.objects.get(session_id=session_id)
        except TaskScheduler.DoesNotExist:
            return None

    def check_rid_subscription_record_by_view_hash_exists(self, view_hash: int) -> bool:
        return ISASubscription.objects.filter(view_hash=view_hash).exists()

    def check_rid_subscription_record_by_subscription_id_exists(self, subscription_id: str) -> bool:
        return ISASubscription.objects.filter(subscription_id=subscription_id).exists()

    def get_rid_subscription_record_by_subscription_id(self, subscription_id: str) -> ISASubscription:
        return ISASubscription.objects.get(subscription_id=subscription_id)

    def get_all_rid_simulated_subscription_records(self):
        now = arrow.now().datetime
        return ISASubscription.objects.filter(is_simulated=True, end_datetime__gte=now, created_at__lte=now)

    def get_rid_subscription_record_by_id(self, id: str) -> ISASubscription:
        return ISASubscription.objects.get(id=id)

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
        try:
            rid_subscription = ISASubscription(
                id=record_id,
                subscription_id=subscription_id,
                view=view,
                view_hash=view_hash,
                end_datetime=end_datetime,
                flight_details=flights_dict,
                is_simulated=is_simulated,
            )
            rid_subscription.save()
            return True
        except IntegrityError:
            return False

    def update_flight_details_in_rid_subscription_record(self, existing_subscription_record: ISASubscription, flights_dict: str) -> bool:
        try:
            existing_subscription_record.flight_details = flights_dict
            existing_subscription_record.save()
            return True
        except Exception:
            return False

    def delete_all_simulated_rid_subscription_records(self) -> bool:
        try:
            ISASubscription.objects.filter(is_simulated=True).delete()
            return True
        except Exception:
            return False

    def _serialize_operator_location(self, operator_location):
        from dataclasses import asdict
        return json.dumps(asdict(operator_location)) if operator_location else json.dumps({})

    def _serialize_auth_data(self, auth_data):
        from dataclasses import asdict
        return json.dumps(asdict(auth_data)) if auth_data else json.dumps({})

    def _serialize_eu_classification(self, eu_classification):
        from dataclasses import asdict
        return json.dumps(asdict(eu_classification)) if eu_classification else json.dumps({})

    def _serialize_uas_id(self, uas_id):
        from dataclasses import asdict
        return json.dumps(asdict(uas_id)) if uas_id else json.dumps({})

    def _create_rid_flight_details(self, rid_flight_details_payload: RIDFlightDetails) -> Optional[RIDFlightDetail]:
        operator_location = self._serialize_operator_location(rid_flight_details_payload.operator_location)
        auth_data = self._serialize_auth_data(rid_flight_details_payload.auth_data)
        eu_classification = self._serialize_eu_classification(rid_flight_details_payload.eu_classification)
        uas_id = self._serialize_uas_id(rid_flight_details_payload.uas_id)
        try:
            rid_flight_details = RIDFlightDetail(
                id=rid_flight_details_payload.id,
                operation_description=rid_flight_details_payload.operation_description,
                operator_location=operator_location,
                operator_id=rid_flight_details_payload.operator_id,
                auth_data=auth_data,
                uas_id=uas_id,
                eu_classification=eu_classification,
            )
            rid_flight_details.save()
            return rid_flight_details
        except IntegrityError:
            return None

    def create_or_update_rid_flight_details(self, rid_flight_details_payload: RIDFlightDetails):
        rid_flight_details_exist = RIDFlightDetail.objects.filter(id=rid_flight_details_payload.id).exists()
        operator_location = self._serialize_operator_location(rid_flight_details_payload.operator_location)
        auth_data = self._serialize_auth_data(rid_flight_details_payload.auth_data)
        eu_classification = self._serialize_eu_classification(rid_flight_details_payload.eu_classification)
        uas_id = self._serialize_uas_id(rid_flight_details_payload.uas_id)
        if rid_flight_details_exist:
            rid_flight_details = RIDFlightDetail.objects.get(id=rid_flight_details_payload.id)
            rid_flight_details.operation_description = rid_flight_details_payload.operation_description
            rid_flight_details.operator_location = operator_location
            rid_flight_details.auth_data = auth_data
            rid_flight_details.uas_id = uas_id
            rid_flight_details.eu_classification = eu_classification
            try:
                rid_flight_details.save()
                return rid_flight_details
            except IntegrityError:
                return None
        else:
            return self._create_rid_flight_details(rid_flight_details_payload)

    def delete_all_flight_details(self) -> bool:
        try:
            RIDFlightDetail.objects.all().delete()
            return True
        except Exception as e:
            logger.error(f"Error deleting all flight observations: {e}")
            return False

    def create_rid_stream_monitoring_periodic_task(self, session_id: str, end_datetime: str) -> bool:
        rid_stream_monitoring_job = TaskScheduler()
        every = int(os.getenv("HEARTBEAT_RATE_SECS", default=5))
        now = arrow.now()
        stream_end = arrow.get(end_datetime)
        delta = stream_end - now
        delta_seconds = delta.total_seconds()
        expires = now.shift(seconds=delta_seconds)
        task_name = "check_rid_stream_conformance"
        try:
            p_task = rid_stream_monitoring_job.schedule_every(
                task_name=task_name,
                period="seconds",
                every=every,
                expires=expires.isoformat(),
                session_id=session_id,
                flight_declaration=None,
            )
            logger.error("Created and starting RID stream observation task")
            p_task.start()
            return True
        except Exception as e:
            logger.error("Could not create RID stream observation periodic task %s" % e)
            return False

    def remove_rid_stream_monitoring_periodic_task(self, rid_stream_monitoring_task: TaskScheduler) -> None:
        rid_stream_monitoring_task.terminate()
