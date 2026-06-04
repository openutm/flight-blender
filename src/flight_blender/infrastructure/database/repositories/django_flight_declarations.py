import json
from dataclasses import asdict
from typing import Optional

from django.db.models import QuerySet
from django.db.utils import IntegrityError
from loguru import logger

from flight_blender.flight_declarations.models import (
    CompositeOperationalIntent,
    FlightDeclaration,
    FlightOperationalIntentDetail,
    FlightOperationalIntentReference,
    PeerCompositeOperationalIntent,
    PeerOperationalIntentDetail,
    PeerOperationalIntentReference,
    Subscriber,
)
from flight_blender.scd.data_definitions import FlightDeclarationCreationPayload
from flight_blender.scd.scd_data_definitions import (
    CompositeOperationalIntentPayload,
    OperationalIntentReferenceDSSResponse,
    OperationalIntentUSSDetails,
    PartialCreateOperationalIntentReference,
    SubscriberToNotify,
)


class DjangoFlightDeclarationRepository:
    # --- peer ops ---

    def get_peer_operational_intent_details_by_id(self, operational_intent_id: str) -> Optional[PeerOperationalIntentDetail]:
        try:
            return PeerOperationalIntentDetail.objects.get(id=operational_intent_id)
        except PeerOperationalIntentDetail.DoesNotExist:
            return None

    def get_peer_operational_intent_reference_by_id(self, operational_intent_reference_id: str) -> Optional[PeerOperationalIntentReference]:
        try:
            return PeerOperationalIntentReference.objects.get(id=operational_intent_reference_id)
        except PeerOperationalIntentReference.DoesNotExist:
            return None

    def create_or_update_peer_operational_intent_details(
        self,
        peer_operational_intent_id: str,
        operational_intent_details: OperationalIntentUSSDetails,
    ) -> Optional[PeerOperationalIntentDetail]:
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
    ) -> Optional[PeerOperationalIntentReference]:
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

    def clear_stored_operational_intents(self) -> None:
        PeerOperationalIntentReference.objects.filter(is_live=False).delete()
        PeerOperationalIntentDetail.objects.filter(is_live=False).delete()

    # --- flight declarations ---

    def get_all_flight_declarations(self) -> QuerySet[FlightDeclaration]:
        return FlightDeclaration.objects.all()

    def check_flight_declaration_exists(self, flight_declaration_id: str) -> bool:
        return FlightDeclaration.objects.filter(id=flight_declaration_id).exists()

    def get_flight_declaration_by_id(self, flight_declaration_id: str) -> Optional[FlightDeclaration]:
        try:
            return FlightDeclaration.objects.get(id=flight_declaration_id)
        except FlightDeclaration.DoesNotExist:
            return None

    def create_flight_declaration(self, flight_declaration_creation: FlightDeclarationCreationPayload) -> Optional[FlightDeclaration]:
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

    def delete_flight_declaration(self, flight_declaration_id: str) -> bool:
        try:
            flight_declaration = FlightDeclaration.objects.get(id=flight_declaration_id)
            flight_declaration.delete()
            return True
        except FlightDeclaration.DoesNotExist:
            return False
        except IntegrityError:
            return False

    def set_flight_declaration_non_conforming(self, flight_declaration: FlightDeclaration) -> None:
        flight_declaration.state = 3
        flight_declaration.save()

    def check_flight_declaration_active(self, flight_declaration_id: str, now) -> bool:
        return FlightDeclaration.objects.filter(
            id=flight_declaration_id,
            start_datetime__lte=now,
            end_datetime__gte=now,
        ).exists()

    def check_active_activated_flights_exist(self) -> bool:
        return FlightDeclaration.objects.filter().filter(state__in=[1, 2]).exists()

    def get_active_activated_flight_declarations(self):
        return FlightDeclaration.objects.filter().filter(state__in=[1, 2])

    def get_current_flight_accepted_activated_declaration_ids(self, now: str):
        import arrow

        n = arrow.get(now)
        two_minutes_before_now = n.shift(seconds=-120).isoformat()
        five_hours_from_now = n.shift(minutes=300).isoformat()
        return (
            FlightDeclaration.objects.filter(
                start_datetime__gte=two_minutes_before_now,
                end_datetime__lte=five_hours_from_now,
            )
            .filter(state__in=[1, 2])
            .values_list("id", flat=True)
        )

    def update_telemetry_timestamp(self, flight_declaration_id: str) -> bool:
        import arrow

        now = arrow.now().isoformat()
        try:
            flight_declaration = FlightDeclaration.objects.get(id=flight_declaration_id)
            flight_declaration.latest_telemetry_datetime = now
            flight_declaration.save()
            return True
        except FlightDeclaration.DoesNotExist:
            return False

    def update_flight_operation_state(self, flight_declaration_id: str, state: int) -> bool:
        try:
            flight_declaration = FlightDeclaration.objects.get(id=flight_declaration_id)
            flight_declaration.state = state
            flight_declaration.save()
            return True
        except Exception:
            return False

    def update_flight_operation_operational_intent(
        self, flight_declaration_id: str, operational_intent: PartialCreateOperationalIntentReference
    ) -> bool:
        try:
            flight_declaration = FlightDeclaration.objects.get(id=flight_declaration_id)
            flight_declaration.operational_intent = json.dumps(asdict(operational_intent))
            flight_declaration.save()
            return True
        except Exception:
            return False

    # --- composite operational intents ---

    def check_composite_operational_intent_exists(self, flight_declaration_id: str) -> bool:
        return CompositeOperationalIntent.objects.filter(declaration=flight_declaration_id).exists()

    def get_composite_operational_intent_by_declaration_id(self, flight_declaration_id: str) -> Optional[CompositeOperationalIntent]:
        try:
            return CompositeOperationalIntent.objects.get(declaration__id=flight_declaration_id)
        except CompositeOperationalIntent.DoesNotExist:
            return None

    def create_or_update_composite_operational_intent(
        self,
        flight_declaration: FlightDeclaration,
        composite_operational_intent_payload,
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

    # --- operational intent references ---

    def get_flight_operational_intent_reference_by_flight_declaration_id(
        self, flight_declaration_id: str
    ) -> Optional[FlightOperationalIntentReference]:
        try:
            flight_declaration = FlightDeclaration.objects.get(id=flight_declaration_id)
            return FlightOperationalIntentReference.objects.get(declaration=flight_declaration)
        except FlightDeclaration.DoesNotExist:
            return None
        except FlightOperationalIntentReference.DoesNotExist:
            return None

    def get_flight_operational_intent_reference_by_flight_declaration_obj(
        self, flight_declaration: FlightDeclaration
    ) -> Optional[FlightOperationalIntentReference]:
        try:
            return FlightOperationalIntentReference.objects.get(declaration=flight_declaration)
        except FlightDeclaration.DoesNotExist:
            return None
        except FlightOperationalIntentReference.DoesNotExist:
            return None

    def check_flight_operational_intent_reference_by_id_exists(self, operational_intent_ref_id: str) -> bool:
        return FlightOperationalIntentReference.objects.filter(id=operational_intent_ref_id).exists()

    def get_operational_intent_reference_by_id(self, operational_intent_ref_id: str) -> FlightOperationalIntentReference:
        return FlightOperationalIntentReference.objects.get(id=operational_intent_ref_id)

    def get_flight_operational_intent_reference_by_id(self, operational_intent_ref_id: str) -> Optional[FlightOperationalIntentReference]:
        try:
            return FlightOperationalIntentReference.objects.get(id=operational_intent_ref_id)
        except FlightOperationalIntentReference.DoesNotExist:
            return None

    def update_flight_operational_intent_reference_ovn(self, flight_operational_intent_reference: FlightOperationalIntentReference, ovn: str) -> bool:
        try:
            flight_operational_intent_reference.ovn = ovn
            flight_operational_intent_reference.save()
            return True
        except IntegrityError:
            return False

    def update_flight_operational_intent_reference_op_int(
        self, flight_operational_intent_reference: FlightOperationalIntentReference, dss_operational_intent_reference_id: str
    ) -> bool:
        try:
            flight_operational_intent_reference.id = dss_operational_intent_reference_id
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

    def update_flight_operational_intent_reference_op_int_ovn(
        self, flight_operational_intent_reference: FlightOperationalIntentReference, dss_operational_intent_reference_id: str, ovn: str
    ) -> bool:
        try:
            flight_operational_intent_reference.id = dss_operational_intent_reference_id
            flight_operational_intent_reference.ovn = ovn
            flight_operational_intent_reference.save()
            return True
        except Exception:
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

    def create_flight_operational_intent_reference_with_submitted_operational_intent(
        self,
        flight_declaration: FlightDeclaration,
        operational_intent_reference_payload,
    ) -> Optional[FlightOperationalIntentReference]:
        try:
            flight_operational_intent_reference = FlightOperationalIntentReference(
                id=operational_intent_reference_payload.id,
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
    ):
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

    # --- subscribers ---

    def get_subscribers_of_operational_intent_reference(self, flight_operational_intent_reference: FlightOperationalIntentReference):
        try:
            return Subscriber.objects.filter(operational_intent_reference=flight_operational_intent_reference)
        except Subscriber.DoesNotExist:
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
            for subscriber in subscribers:
                all_subscriptions = [asdict(subscrition) for subscrition in subscriber.subscriptions]
                subscriber_obj = Subscriber(
                    operational_intent_reference=flight_operational_intent_reference,
                    uss_base_url=subscriber.uss_base_url,
                    subscriptions=json.dumps(all_subscriptions),
                )
                subscriber_obj.save()
            return True
        except IntegrityError:
            return False

    # --- operational intent details ---

    def check_flight_operational_intent_details_by_id_exists(self, operational_intent_ref_id: str) -> bool:
        return FlightOperationalIntentDetail.objects.filter(id=operational_intent_ref_id).exists()

    def get_operational_intent_details_by_flight_declaration(self, flight_declaration: FlightDeclaration) -> Optional[FlightOperationalIntentDetail]:
        try:
            return FlightOperationalIntentDetail.objects.get(declaration=flight_declaration)
        except FlightOperationalIntentDetail.DoesNotExist:
            return None

    def get_operational_intent_details_by_flight_declaration_id(self, declaration_id: str) -> Optional[FlightOperationalIntentDetail]:
        try:
            return FlightOperationalIntentDetail.objects.get(declaration__id=declaration_id)
        except FlightOperationalIntentDetail.DoesNotExist:
            return None

    def create_flight_operational_intent_details_with_submitted_operational_intent(
        self,
        flight_declaration: FlightDeclaration,
        operational_intent_details_payload: OperationalIntentUSSDetails,
    ) -> Optional[FlightOperationalIntentDetail]:
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

    def update_flight_operational_intent_details(
        self,
        flight_operational_intent_detail: FlightOperationalIntentDetail,
        operational_intent_details: OperationalIntentUSSDetails,
    ) -> bool:
        _volumes = []
        for volume in operational_intent_details.volumes:
            _volumes.append(asdict(volume))
        _off_nominal_volumes = []
        for volume in operational_intent_details.off_nominal_volumes or []:
            _off_nominal_volumes.append(asdict(volume))
        try:
            flight_operational_intent_detail.volumes = json.dumps(_volumes)
            flight_operational_intent_detail.off_nominal_volumes = json.dumps(_off_nominal_volumes)
            flight_operational_intent_detail.priority = operational_intent_details.priority
            flight_operational_intent_detail.save()
            return True
        except Exception:
            return False
