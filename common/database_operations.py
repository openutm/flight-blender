import json
import logging
import os
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Never, Union
from uuid import UUID

import arrow
from django.db.models import QuerySet
from django.db.utils import IntegrityError
from dotenv import find_dotenv, load_dotenv

from conformance_monitoring_operations.models import TaskScheduler
from constraint_operations.data_definitions import Constraint as ConstraintData
from constraint_operations.data_definitions import ConstraintDetails
from constraint_operations.data_definitions import ConstraintReference as ConstraintReferencePayload
from constraint_operations.models import ConstraintDetail, ConstraintReference
from flight_declaration_operations.models import (
    CompositeOperationalIntent,
    FlightDeclaration,
    FlightOperationalIntentDetail,
    FlightOperationalIntentReference,
    PeerCompositeOperationalIntent,
    PeerOperationalIntentDetail,
    PeerOperationalIntentReference,
    Subscriber,
)
from flight_feed_operations.data_definitions import SingleAirtrafficObservation
from flight_feed_operations.models import FlightObservation
from geo_fence_operations.data_definitions import GeofencePayload
from geo_fence_operations.models import GeoFence
from notification_operations.models import OperatorRIDNotification
from rid_operations.data_definitions import OperatorRIDNotificationCreationPayload
from rid_operations.models import ISASubscription
from scd_operations.data_definitions import FlightDeclarationCreationPayload
from scd_operations.scd_data_definitions import (
    CompositeOperationalIntentPayload,
    OperationalIntentReferenceDSSResponse,
    OperationalIntentStorage,
    OperationalIntentUSSDetails,
    PartialCreateOperationalIntentReference,
    SubscriberToNotify,
)

logger = logging.getLogger("django")

load_dotenv(find_dotenv())

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)


class FlightBlenderDatabaseReader:
    """
    A file to unify read and write operations to the database. Eventually caching etc. can be added via this file
    """

    def get_peer_operational_intent_details_by_id(self, operational_intent_id: str) -> None | PeerOperationalIntentDetail:
        try:
            peer_operational_intent_detail = PeerOperationalIntentDetail.objects.get(id=operational_intent_id)
            return peer_operational_intent_detail
        except PeerOperationalIntentDetail.DoesNotExist:
            return None

    def get_peer_operational_intent_reference_by_id(self, operational_intent_reference_id: str) -> None | PeerOperationalIntentReference:
        try:
            peer_operational_intent_reference = PeerOperationalIntentReference.objects.get(id=operational_intent_reference_id)
            return peer_operational_intent_reference
        except PeerOperationalIntentReference.DoesNotExist:
            return None

    def check_constraint_id_exists(self, constraint_id: str) -> bool:
        return ConstraintDetail.objects.filter(id=constraint_id).exists()

    def get_constraint_by_geofence(self, geofence: GeoFence) -> ConstraintDetail:
        return ConstraintDetail.objects.filter(geofence=geofence)

    def check_constraint_reference_id_exists(self, constraint_reference_id: str) -> bool:
        return ConstraintReference.objects.filter(id=constraint_reference_id).exists()

    def get_constraint_reference_by_id(self, constraint_reference_id: str) -> ConstraintReference:
        return ConstraintReference.objects.get(id=constraint_reference_id)

    def get_constraint_details(self, constraint_id: str) -> ConstraintDetail:
        return ConstraintDetail.objects.get(id=constraint_id)

    def get_flight_observations(self, after_datetime: arrow.arrow.Arrow):
        observations = FlightObservation.objects.filter(created_at__gte=after_datetime.isoformat()).order_by("created_at").values()
        return observations

    def get_flight_observations_by_session(self, session_id: str, after_datetime: arrow.arrow.Arrow):
        observations = (
            FlightObservation.objects.filter(session_id=session_id, created_at__gte=after_datetime.isoformat())
            .exclude(traffic_source=11)
            .order_by("created_at")
            .values()
        )
        return observations

    def get_latest_flight_observation_by_session(self, session_id: str):
        observation = FlightObservation.objects.filter(session_id=session_id).latest("created_at")
        return observation

    def get_all_flight_declarations(self) -> list[FlightDeclaration]:
        flight_declarations = FlightDeclaration.objects.all()
        return flight_declarations

    def check_flight_declaration_exists(self, flight_declaration_id: str) -> bool:
        return FlightDeclaration.objects.filter(id=flight_declaration_id).exists()

    def get_flight_declaration_by_id(self, flight_declaration_id: str) -> None | FlightDeclaration:
        try:
            flight_declaration = FlightDeclaration.objects.get(id=flight_declaration_id)
            return flight_declaration
        except FlightDeclaration.DoesNotExist:
            return None

    def check_composite_operational_intent_exists(self, flight_declaration_id: str) -> bool:
        composite_operational_intent_exists = CompositeOperationalIntent.objects.filter(declaration=flight_declaration_id).exists()
        return composite_operational_intent_exists

    def get_composite_operational_intent_by_declaration_id(self, flight_declaration_id: str) -> None | CompositeOperationalIntent:
        try:
            return CompositeOperationalIntent.objects.get(declaration__id=flight_declaration_id)

        except CompositeOperationalIntent.DoesNotExist:
            return None

    def get_flight_operational_intent_reference_by_flight_declaration_id(self, flight_declaration_id: str) -> None | FlightOperationalIntentReference:
        """
        Retrieves a FlightAuthorization object based on the given flight declaration ID.
        Args:
            flight_declaration_id (str): The ID of the flight declaration.
        Returns:
            Union[None, FlightAuthorization]: The FlightAuthorization object if found, otherwise None.
        Raises:
            FlightDeclaration.DoesNotExist: If the flight declaration with the given ID does not exist.
            FlightAuthorization.DoesNotExist: If the flight authorization for the given flight declaration does not exist.
        """

        try:
            flight_declaration = FlightDeclaration.objects.get(id=flight_declaration_id)
            flight_operational_intent_reference = FlightOperationalIntentReference.objects.get(declaration=flight_declaration)
            return flight_operational_intent_reference
        except FlightDeclaration.DoesNotExist:
            return None
        except FlightOperationalIntentReference.DoesNotExist:
            return None

    def get_active_geofences(self) -> None | list[GeoFence]:
        now = arrow.now()
        return GeoFence.objects.filter(start_datetime__lte=now, end_datetime__gte=now)

    def get_flight_operational_intent_reference_by_flight_declaration_obj(
        self, flight_declaration: FlightDeclaration
    ) -> None | FlightOperationalIntentReference:
        try:
            flight_operational_intent_reference = FlightOperationalIntentReference.objects.get(declaration=flight_declaration)
            return flight_operational_intent_reference
        except FlightDeclaration.DoesNotExist:
            return None
        except FlightOperationalIntentReference.DoesNotExist:
            return None

    def check_flight_operational_intent_reference_by_id_exists(self, operational_intent_ref_id: str) -> bool:
        return FlightOperationalIntentReference.objects.filter(id=operational_intent_ref_id).exists()

    def get_operational_intent_reference_by_id(self, operational_intent_ref_id: str) -> FlightOperationalIntentReference:
        return FlightOperationalIntentReference.objects.get(id=operational_intent_ref_id)

    def get_flight_operational_intent_reference_by_id(self, operational_intent_ref_id: str) -> None | FlightOperationalIntentReference:
        """
        Retrieves a FlightOperationalIntentReference object based on the given flight declaration ID.
        Args:
            flight_declaration_id (str): The ID of the flight declaration.
        Returns:
            Union[None, FlightOperationalIntentReference]: The FlightOperationalIntentReference object if found, otherwise None.
        Raises:
            FlightDeclaration.DoesNotExist: If the flight declaration with the given ID does not exist.
            FlightOperationalIntentReference.DoesNotExist: If the flight authorization for the given flight declaration does not exist.
        """

        try:
            flight_operational_intent_reference = FlightOperationalIntentReference.objects.get(id=operational_intent_ref_id)
            return flight_operational_intent_reference
        except FlightOperationalIntentReference.DoesNotExist:
            return None

    def get_operational_intent_details_by_flight_declaration(self, flight_declaration: FlightDeclaration) -> None | FlightOperationalIntentDetail:
        try:
            flight_operational_intent_detail = FlightOperationalIntentDetail.objects.get(declaration=flight_declaration)
            return flight_operational_intent_detail
        except FlightOperationalIntentDetail.DoesNotExist:
            return None

    def update_flight_operational_intent_reference_ovn(
        self,
        flight_operational_intent_referecne: FlightOperationalIntentReference,
        ovn: str,
    ) -> bool:
        try:
            flight_operational_intent_referecne.ovn = ovn
            flight_operational_intent_referecne.save()
            return True

        except IntegrityError:
            return False

    def get_subscribers_of_operational_intent_reference(
        self, flight_operational_intent_reference: FlightOperationalIntentReference
    ) -> Never | list[Subscriber]:
        try:
            subscribers = Subscriber.objects.filter(operational_intent_reference=flight_operational_intent_reference)
            return subscribers
        except Subscriber.DoesNotExist:
            return None

    def check_flight_operational_intent_details_by_id_exists(self, operational_intent_ref_id: str) -> bool:
        return FlightOperationalIntentDetail.objects.filter(id=operational_intent_ref_id).exists()

    def get_operational_intent_details_by_flight_declaration_id(self, declaration_id: str) -> None | FlightOperationalIntentDetail:
        """
        Retrieves a FlightOperationalIntentReference object based on the given flight declaration ID.
        Args:
            flight_declaration_id (str): The ID of the flight declaration.
        Returns:
            Union[None, FlightOperationalIntentReference]: The FlightOperationalIntentReference object if found, otherwise None.
        Raises:
            FlightDeclaration.DoesNotExist: If the flight declaration with the given ID does not exist.
            FlightOperationalIntentReference.DoesNotExist: If the flight authorization for the given flight declaration does not exist.
        """

        try:
            flight_operational_intent_detail = FlightOperationalIntentDetail.objects.get(declaration__id=declaration_id)
            return flight_operational_intent_detail
        except FlightOperationalIntentDetail.DoesNotExist:
            return None

    def get_geofence_by_constraint_reference_id(self, constraint_reference_id: str) -> None | GeoFence:
        try:
            constraint_reference = ConstraintReference.objects.get(id=constraint_reference_id)
            geofence = GeoFence.objects.get(id=constraint_reference.geofence.id)
            return geofence
        except ConstraintReference.DoesNotExist:
            return None
        except GeoFence.DoesNotExist:
            return None

    def check_flight_declaration_active(self, flight_declaration_id: str, now: datetime) -> bool:
        return FlightDeclaration.objects.filter(
            id=flight_declaration_id,
            start_datetime__lte=now,
            end_datetime__gte=now,
        ).exists()

    def check_active_activated_flights_exist(self) -> bool:
        return FlightDeclaration.objects.filter().filter(state__in=[1, 2]).exists()

    def get_active_activated_flight_declarations(
        self,
    ) -> QuerySet | list[FlightDeclaration]:
        return FlightDeclaration.objects.filter().filter(state__in=[1, 2])

    def get_current_flight_accepted_activated_declaration_ids(self, now: str) -> None | UUID:
        """This method gets flight operation ids that are active in the system"""
        n = arrow.get(now)

        two_minutes_before_now = n.shift(seconds=-120).isoformat()
        five_hours_from_now = n.shift(minutes=300).isoformat()
        relevant_ids = (
            FlightDeclaration.objects.filter(
                start_datetime__gte=two_minutes_before_now,
                end_datetime__lte=five_hours_from_now,
            )
            .filter(state__in=[1, 2])
            .values_list("id", flat=True)
        )
        return relevant_ids

    def get_conformance_monitoring_task(self, flight_declaration: FlightDeclaration) -> None | TaskScheduler:
        try:
            return TaskScheduler.objects.get(flight_declaration=flight_declaration)
        except TaskScheduler.DoesNotExist:
            return None

    def get_rid_monitoring_task(self, session_id: UUID) -> None | TaskScheduler:
        try:
            return TaskScheduler.objects.get(session_id=session_id)
        except TaskScheduler.DoesNotExist:
            return None

    def get_active_rid_observations_for_view(self, start_time: datetime, end_time: datetime) -> None | QuerySet | list[FlightObservation]:
        try:
            observations = FlightObservation.objects.filter(created_at__gte=start_time, created_at__lte=end_time, traffic_source=11).order_by(
                "-created_at"
            )
            return observations
        except FlightObservation.DoesNotExist:
            return None

    def get_active_rid_observations_for_session(self, session_id: str) -> None | QuerySet | list[FlightObservation]:
        try:
            observations = FlightObservation.objects.filter(session_id=session_id, traffic_source=11).order_by("-created_at")
            return observations
        except FlightObservation.DoesNotExist:
            return None

    def get_active_rid_observations_for_session_between_interval(
        self, start_time: datetime, end_time: datetime, session_id: str
    ) -> None | QuerySet | list[FlightObservation]:
        try:
            observations = FlightObservation.objects.filter(
                session_id=session_id,
                created_at__gte=start_time,
                created_at__lte=end_time,
                traffic_source=11,
            )
            return observations
        except FlightObservation.DoesNotExist:
            return None

    def get_active_user_notifications_between_interval(
        self, start_time: datetime, end_time: datetime
    ) -> None | QuerySet | list[OperatorRIDNotification]:
        try:
            notifications = OperatorRIDNotification.objects.filter(created_at__gte=start_time, created_at__lte=end_time, is_active=True)
            return notifications
        except OperatorRIDNotification.DoesNotExist:
            return None

    def check_rid_subscription_record_by_view_hash_exists(self, view_hash: int) -> bool:
        rid_subscription_exists = ISASubscription.objects.filter(view_hash=view_hash).exists()
        return rid_subscription_exists

    def check_rid_subscription_record_by_subscription_id_exists(self, subscription_id: str) -> bool:
        rid_subscription_record_exists = ISASubscription.objects.filter(subscription_id=subscription_id).exists()
        return rid_subscription_record_exists

    def get_rid_subscription_record_by_subscription_id(self, subscription_id: str) -> ISASubscription:
        rid_subscription_record = ISASubscription.objects.get(subscription_id=subscription_id)
        return rid_subscription_record

    def get_all_rid_simulated_subscription_records(self) -> QuerySet[ISASubscription]:
        now = arrow.now().datetime
        return ISASubscription.objects.filter(is_simulated=True, end_datetime__gte=now, created_at__lte=now)

    def get_rid_subscription_record_by_id(self, id: str) -> ISASubscription:
        return ISASubscription.objects.get(id=id)


class FlightBlenderDatabaseWriter:
    def create_or_update_peer_operational_intent_details(
        self,
        peer_operational_intent_id: str,
        operational_intent_details: OperationalIntentUSSDetails,
    ) -> None | PeerOperationalIntentDetail:
        try:
            _operational_intent_details = asdict(operational_intent_details)

            peer_operational_intent_detail_obj = PeerOperationalIntentDetail(
                id=peer_operational_intent_id,
                volumes=_operational_intent_details["volumes"],
                off_nominal_volumes=_operational_intent_details["off_nominal_volumes"],
                priority=operational_intent_details.priority,
            )
            peer_operational_intent_detail_obj.save()
            return peer_operational_intent_detail_obj
        except IntegrityError:
            return None

    def create_or_update_peer_operational_intent_reference(
        self,
        peer_operational_intent_reference_id: str,
        peer_operational_intent_reference: OperationalIntentReferenceDSSResponse,
    ) -> None | PeerOperationalIntentReference:
        try:
            peer_operational_intent_reference_obj = PeerOperationalIntentReference(
                id=peer_operational_intent_reference_id,
                uss_base_url=peer_operational_intent_reference.uss_base_url,
                ovn=peer_operational_intent_reference.ovn,
                state=peer_operational_intent_reference.state,
                uss_availability=peer_operational_intent_reference.uss_availability,
                version=peer_operational_intent_reference.version,
                time_start=peer_operational_intent_reference.time_start.value,
                time_end=peer_operational_intent_reference.time_end.value,
                subscription_id=peer_operational_intent_reference.subscription_id,
            )
            peer_operational_intent_reference_obj.save()
            return peer_operational_intent_reference_obj
        except IntegrityError:
            return None

    def get_peer_operational_intent_reference_by_id(self, operational_intent_reference_id: str) -> None | PeerOperationalIntentReference:
        try:
            peer_operational_intent_reference = PeerOperationalIntentReference.objects.get(id=operational_intent_reference_id)
            return peer_operational_intent_reference
        except PeerOperationalIntentReference.DoesNotExist:
            return None

    def write_flight_observation(self, single_observation: SingleAirtrafficObservation) -> bool:
        session_id = single_observation.session_id if single_observation.session_id else "00000000-0000-0000-0000-000000000000"
        try:
            flight_observation = FlightObservation(
                session_id=session_id,
                traffic_source=single_observation.traffic_source,
                latitude_dd=single_observation.lat_dd,
                longitude_dd=single_observation.lon_dd,
                altitude_mm=single_observation.altitude_mm,
                source_type=single_observation.source_type,
                icao_address=single_observation.icao_address,
                metadata=json.dumps(single_observation.metadata),
            )
            flight_observation.save()
            return True
        except IntegrityError:
            return False

    def delete_flight_declaration(self, flight_declaration_id: str) -> bool:
        try:
            flight_declaration = FlightDeclaration.objects.get(id=flight_declaration_id)
            flight_declaration.delete()
            return True
        except FlightDeclaration.DoesNotExist:
            return False
        except IntegrityError:
            return False

    def create_operator_rid_notification(self, operator_rid_notification: OperatorRIDNotificationCreationPayload) -> bool:
        try:
            operator_rid_notification_obj = OperatorRIDNotification(
                message=operator_rid_notification.message,
                session_id=operator_rid_notification.session_id,
            )
            operator_rid_notification_obj.save()
            return True
        except IntegrityError:
            return False

    def create_flight_declaration(self, flight_declaration_creation: FlightDeclarationCreationPayload) -> None | FlightDeclaration:
        try:
            flight_declaration = FlightDeclaration(
                id=flight_declaration_creation.id,
                operational_intent=flight_declaration_creation.operational_intent,
                flight_declaration_raw_geojson=flight_declaration_creation.flight_declaration_raw_geojson,
                bounds=flight_declaration_creation.bounds,
                aircraft_id=flight_declaration_creation.aircraft_id,
                state=flight_declaration_creation.state,
            )
            flight_declaration.save()
            return flight_declaration

        except IntegrityError:
            return None

    def set_flight_declaration_non_conforming(self, flight_declaration: FlightDeclaration):
        flight_declaration.state = 3
        flight_declaration.save()

    def create_flight_operational_intent_reference_with_submitted_operational_intent(
        self,
        flight_declaration: FlightDeclaration,
        operational_intent_reference_payload: (OperationalIntentReferenceDSSResponse | PartialCreateOperationalIntentReference),
    ) -> None | FlightOperationalIntentReference:
        try:
            flight_operational_intent_reference = FlightOperationalIntentReference(
                id=operational_intent_reference_payload.id,  # assigned by the DSS
                declaration=flight_declaration,
                ovn=operational_intent_reference_payload.ovn,
                state=operational_intent_reference_payload.state,
                uss_availability=operational_intent_reference_payload.uss_availability,
                uss_base_url=operational_intent_reference_payload.uss_base_url,
                version=operational_intent_reference_payload.version,
                time_start=operational_intent_reference_payload.time_start.value,
                manager=operational_intent_reference_payload.manager,
                time_end=operational_intent_reference_payload.time_end.value,
                subscription_id=operational_intent_reference_payload.subscription_id,
            )
            flight_operational_intent_reference.save()

            return flight_operational_intent_reference

        except IntegrityError:
            return None

    def create_flight_operational_intent_reference_subscribers(
        self,
        flight_declaration: FlightDeclaration,
        subscribers: list[SubscriberToNotify],
    ) -> bool:
        try:
            flight_operational_intent_reference = FlightOperationalIntentReference.objects.get(declaration=flight_declaration)
            if flight_operational_intent_reference is None:
                return False
            else:
                for subscriber in subscribers:
                    all_subscriptions = []
                    for subscrition in subscriber.subscriptions:
                        all_subscriptions.append(asdict(subscrition))

                    subscriber = Subscriber(
                        operational_intent_reference=flight_operational_intent_reference,
                        uss_base_url=subscriber.uss_base_url,
                        subscriptions=json.dumps(all_subscriptions),
                    )
                    subscriber.save()

            return True
        except IntegrityError:
            return False

    def create_flight_operational_intent_details_with_submitted_operational_intent(
        self,
        flight_declaration: FlightDeclaration,
        operational_intent_details_payload: OperationalIntentUSSDetails,
    ) -> None | FlightOperationalIntentDetail:
        try:
            _operational_intent_details_payload = asdict(operational_intent_details_payload)
            flight_operational_intent_detail_obj = FlightOperationalIntentDetail(
                declaration=flight_declaration,
                volumes=json.dumps(_operational_intent_details_payload["volumes"]),
                off_nominal_volumes=json.dumps(_operational_intent_details_payload["off_nominal_volumes"]),
                priority=operational_intent_details_payload.priority,
            )
            flight_operational_intent_detail_obj.save()

            return flight_operational_intent_detail_obj

        except IntegrityError:
            return None

    def create_or_update_peer_composite_operational_intent(
        self,
        operation_id: str,
        composite_operational_intent: CompositeOperationalIntentPayload,
    ) -> bool:
        try:
            peer_operational_intent_details = PeerOperationalIntentDetail.objects.get(id=operation_id)
            peer_operational_intent_reference = PeerOperationalIntentReference.objects.get(id=operation_id)
            if peer_operational_intent_details is None or peer_operational_intent_reference is None:
                return False

            composite_operational_intent_obj = PeerCompositeOperationalIntent(
                start_datetime=composite_operational_intent.start_datetime,
                end_datetime=composite_operational_intent.end_datetime,
                alt_min=composite_operational_intent.alt_min,
                alt_max=composite_operational_intent.alt_max,
                operational_intent_details=peer_operational_intent_details,
                operational_intent_reference=peer_operational_intent_reference,
            )
            composite_operational_intent_obj.save()
            return True
        except IntegrityError:
            return False

    def create_or_update_composite_operational_intent(
        self,
        flight_declaration: FlightDeclaration,
        composite_operational_intent_payload: CompositeOperationalIntentPayload | OperationalIntentStorage,
    ) -> bool:
        try:
            operational_intent_details = FlightOperationalIntentDetail.objects.get(declaration=flight_declaration)
            operational_intent_reference = FlightOperationalIntentReference.objects.get(declaration=flight_declaration)

            if operational_intent_reference is None or operational_intent_details is None:
                return False

            composite_operational_intent_obj = CompositeOperationalIntent(
                declaration=flight_declaration,
                bounds=composite_operational_intent_payload.bounds,
                start_datetime=composite_operational_intent_payload.start_datetime,
                end_datetime=composite_operational_intent_payload.end_datetime,
                alt_min=composite_operational_intent_payload.alt_min,
                alt_max=composite_operational_intent_payload.alt_max,
                operational_intent_details=operational_intent_details,
                operational_intent_reference=operational_intent_reference,
            )
            composite_operational_intent_obj.save()

            return True
        except IntegrityError:
            return False

    def update_flight_operational_intent_reference_with_dss_response(
        self,
        flight_declaration: FlightDeclaration,
        dss_operational_intent_reference_id: str,
        ovn: str,
        dss_response: OperationalIntentReferenceDSSResponse,
    ) -> bool:
        try:
            flight_operational_intent_reference = FlightOperationalIntentReference(
                declaration=flight_declaration,
                id=dss_operational_intent_reference_id,
                ovn=ovn,
                dss_response=json.dumps(asdict(dss_response)),
            )
            flight_operational_intent_reference.save()
            return True

        except IntegrityError:
            return False

    def create_flight_operational_intent_reference_from_flight_declaration_obj(self, flight_declaration: FlightDeclaration) -> bool:
        try:
            flight_operational_intent_reference = FlightOperationalIntentReference(declaration=flight_declaration)
            flight_operational_intent_reference.save()
            return True
        except FlightDeclaration.DoesNotExist:
            return False
        except IntegrityError:
            return False

    def create_flight_operational_intent_reference(
        self,
        flight_declaration: FlightDeclaration,
        created_operational_intent_reference: OperationalIntentReferenceDSSResponse,
    ) -> bool | FlightOperationalIntentReference:
        try:
            flight_operational_intent_reference = FlightOperationalIntentReference(
                id=created_operational_intent_reference.id,
                declaration=flight_declaration,
                uss_availability=created_operational_intent_reference.uss_availability,
                ovn=created_operational_intent_reference.ovn,
                manager=created_operational_intent_reference.manager,
                state=created_operational_intent_reference.state,
                uss_base_url=created_operational_intent_reference.uss_base_url,
                version=created_operational_intent_reference.version,
                time_start=created_operational_intent_reference.time_start.value,
                time_end=created_operational_intent_reference.time_end.value,
                subscription_id=created_operational_intent_reference.subscription_id,
            )
            flight_operational_intent_reference.save()
            return flight_operational_intent_reference
        except FlightDeclaration.DoesNotExist:
            return False
        except IntegrityError as ie:
            logger.error("IntegrityError while creating operational intent reference: %s" % ie)
            return False
        except Exception as e:
            logger.error("Error while creating operational intent reference: %s" % e)
            return False

    def update_telemetry_timestamp(self, flight_declaration_id: str) -> bool:
        now = arrow.now().isoformat()
        try:
            flight_declaration = FlightDeclaration.objects.get(id=flight_declaration_id)
            flight_declaration.latest_telemetry_datetime = now
            flight_declaration.save()
            return True
        except FlightDeclaration.DoesNotExist:
            return False

    def update_flight_operational_intent_reference_op_int(
        self,
        flight_operational_intent_reference: FlightOperationalIntentReference,
        dss_operational_intent_reference_id,
    ) -> bool:
        try:
            flight_operational_intent_reference.id = dss_operational_intent_reference_id
            flight_operational_intent_reference.save()
            return True
        except Exception:
            return False

    def update_flight_operational_intent_reference_ovn(
        self,
        flight_operational_intent_reference: FlightOperationalIntentReference,
        ovn: str,
    ) -> bool:
        try:
            flight_operational_intent_reference.ovn = ovn
            flight_operational_intent_reference.save()
            return True
        except Exception:
            return False

    def update_flight_operational_intent_reference(
        self,
        flight_operational_intent_reference: FlightOperationalIntentReference,
        update_operational_intent_reference: OperationalIntentReferenceDSSResponse,
    ) -> bool:
        try:
            flight_operational_intent_reference.ovn = update_operational_intent_reference.ovn
            flight_operational_intent_reference.state = update_operational_intent_reference.state
            flight_operational_intent_reference.uss_availability = update_operational_intent_reference.uss_availability
            flight_operational_intent_reference.uss_base_url = update_operational_intent_reference.uss_base_url
            flight_operational_intent_reference.version = update_operational_intent_reference.version
            flight_operational_intent_reference.time_start = update_operational_intent_reference.time_start.value
            flight_operational_intent_reference.time_end = update_operational_intent_reference.time_end.value
            flight_operational_intent_reference.subscription_id = update_operational_intent_reference.subscription_id
            flight_operational_intent_reference.manager = update_operational_intent_reference.manager

            flight_operational_intent_reference.save()
            return True
        except Exception:
            return False

    def update_flight_operational_intent_details(
        self,
        flight_operational_intent_detail: FlightOperationalIntentDetail,
        operational_intent_details: OperationalIntentUSSDetails,
    ) -> bool:
        _volumes = []
        _off_nominal_volumes = operational_intent_details.off_nominal_volumes
        for volume in operational_intent_details.volumes:
            _volumes.append(asdict(volume))
        _off_nominal_volumes = []
        for volume in operational_intent_details.off_nominal_volumes:
            _off_nominal_volumes.append(asdict(volume))

        try:
            flight_operational_intent_detail.volumes = json.dumps(_volumes)
            flight_operational_intent_detail.off_nominal_volumes = json.dumps(_off_nominal_volumes)
            flight_operational_intent_detail.priority = operational_intent_details.priority
            flight_operational_intent_detail.save()
            return True
        except Exception:
            return False

    def update_flight_operational_intent_reference_op_int_ovn(
        self,
        flight_operational_intent_reference: FlightOperationalIntentReference,
        dss_operational_intent_reference_id: str,
        ovn: str,
    ) -> bool:
        try:
            flight_operational_intent_reference.id = dss_operational_intent_reference_id
            flight_operational_intent_reference.ovn = ovn
            flight_operational_intent_reference.save()
            return True
        except Exception:
            return False

    def update_flight_operation_operational_intent(
        self,
        flight_declaration_id: str,
        operational_intent: PartialCreateOperationalIntentReference,
    ) -> bool:
        try:
            flight_declaration = FlightDeclaration.objects.get(id=flight_declaration_id)
            flight_declaration.operational_intent = json.dumps(asdict(operational_intent))
            # TODO: Convert the updated operational intent to GeoJSON
            flight_declaration.save()
            return True
        except Exception:
            return False

    def update_flight_operation_state(self, flight_declaration_id: str, state: int) -> bool:
        try:
            flight_declaration = FlightDeclaration.objects.get(id=flight_declaration_id)
            flight_declaration.state = state
            flight_declaration.save()
            return True
        except Exception:
            return False

    def create_conformance_monitoring_periodic_task(self, flight_declaration: FlightDeclaration) -> bool:
        conformance_monitoring_job = TaskScheduler()
        every = int(os.getenv("HEARTBEAT_RATE_SECS", default=5))
        now = arrow.now()
        session_id = str(uuid.uuid4())
        fd_end = arrow.get(flight_declaration.end_datetime)
        delta = fd_end - now
        delta_seconds = delta.total_seconds()
        expires = now.shift(seconds=delta_seconds)
        task_name = "check_flight_conformance"
        logger.info("Creating periodic task for conformance monitoring expires at %s" % expires)
        try:
            p_task = conformance_monitoring_job.schedule_every(
                task_name=task_name,
                period="seconds",
                every=every,
                expires=expires.isoformat(),
                flight_declaration=flight_declaration,
                session_id=session_id,
            )

            p_task.start()
            return True
        except Exception as e:
            logger.debug(e)
            logger.error("Could not create periodic task")
            return False

    def remove_conformance_monitoring_periodic_task(self, conformance_monitoring_task: TaskScheduler):
        conformance_monitoring_task.terminate()

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
                expires=expires,
                session_id=session_id,
                flight_declaration=None,
            )

            logger.error("Created and starting RID stream observation task")
            p_task.start()
            return True
        except Exception as e:
            logger.error("Could not create RID stream observation periodic task %s" % e)
            return False

    def remove_rid_stream_monitoring_periodic_task(self, rid_stream_monitoring_task: TaskScheduler):
        rid_stream_monitoring_task.terminate()

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

    def clear_stored_operational_intents(self):
        PeerOperationalIntentReference.objects.filter(is_live=False).delete()
        PeerOperationalIntentDetail.objects.filter(is_live=False).delete()

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

    def update_constraint_reference_ovn(
        self,
        constraint_reference: ConstraintReference,
        ovn: str,
    ) -> bool:
        try:
            constraint_reference.ovn = ovn
            constraint_reference.save()
            return True

        except IntegrityError:
            return False

    def create_or_update_geofence(self, geofence_payload: GeofencePayload) -> None | GeoFence:
        try:
            geofence = GeoFence(
                raw_geo_fence=json.dumps(asdict(geofence_payload)),
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
                geozone=json.dumps(geofence_payload.geozone),
            )
            geofence.save()
            return geofence
        except IntegrityError:
            return None

    def create_or_update_constraint_detail(self, constraint: ConstraintDetails, geofence: GeoFence) -> bool:
        try:
            _constraint_volumes = []
            for _volume in constraint.volumes:
                _constraint_volumes.append(asdict(_volume))
            constraint_obj = ConstraintDetail(volumes=json.dumps(_constraint_volumes), _type=constraint.type, geofence=geofence)
            constraint_obj.save()
            return True
        except IntegrityError:
            return False

    def create_or_update_constraint_reference(self, constraint_reference: ConstraintReferencePayload, geofence: GeoFence) -> bool:
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
            )
            constraint_obj.save()
            return True
        except IntegrityError:
            return False
