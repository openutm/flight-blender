from datetime import datetime, timezone

from loguru import logger  # noqa: F401 — kept for test monkeypatching compatibility

from flight_blender.infrastructure.database.repositories.django_conformance import DjangoConformanceRepository
from flight_blender.infrastructure.database.repositories.django_constraint import DjangoConstraintRepository
from flight_blender.infrastructure.database.repositories.django_flight_declarations import DjangoFlightDeclarationRepository
from flight_blender.infrastructure.database.repositories.django_flight_feed import DjangoFlightFeedRepository
from flight_blender.infrastructure.database.repositories.django_geo_fence import DjangoGeoFenceRepository
from flight_blender.infrastructure.database.repositories.django_notifications import DjangoNotificationsRepository
from flight_blender.infrastructure.database.repositories.django_rid import DjangoRIDRepository
from flight_blender.infrastructure.database.repositories.django_surveillance import DjangoSurveillanceRepository


class FlightBlenderDatabaseReader:
    def __init__(self):
        self._geo_fence = DjangoGeoFenceRepository()
        self._flight_feed = DjangoFlightFeedRepository()
        self._flight_declarations = DjangoFlightDeclarationRepository()
        self._conformance = DjangoConformanceRepository()
        self._constraint = DjangoConstraintRepository()
        self._rid = DjangoRIDRepository()
        self._surveillance = DjangoSurveillanceRepository()
        self._notifications = DjangoNotificationsRepository()

    # --- peer ops ---
    def get_peer_operational_intent_details_by_id(self, operational_intent_id):
        return self._flight_declarations.get_peer_operational_intent_details_by_id(operational_intent_id)

    def get_peer_operational_intent_reference_by_id(self, operational_intent_reference_id):
        return self._flight_declarations.get_peer_operational_intent_reference_by_id(operational_intent_reference_id)

    # --- constraint ---
    def check_constraint_id_exists(self, constraint_id):
        return self._constraint.check_constraint_id_exists(constraint_id)

    def get_constraint_by_geofence(self, geofence):
        return self._constraint.get_constraint_by_geofence(geofence)

    def check_constraint_reference_id_exists(self, constraint_reference_id):
        return self._constraint.check_constraint_reference_id_exists(constraint_reference_id)

    def get_constraint_reference_by_id(self, constraint_reference_id):
        return self._constraint.get_constraint_reference_by_id(constraint_reference_id)

    def get_constraint_details(self, constraint_id):
        return self._constraint.get_constraint_details(constraint_id)

    def get_geofence_by_constraint_reference_id(self, constraint_reference_id):
        return self._constraint.get_geofence_by_constraint_reference_id(constraint_reference_id)

    # --- flight feed ---
    def get_flight_observations(self, after_datetime):
        return self._flight_feed.get_flight_observations(after_datetime)

    def get_closest_flight_observation_for_now(self, now):
        return self._flight_feed.get_closest_flight_observation_for_now(now)

    def get_flight_observation_objects(self):
        return self._flight_feed.get_flight_observation_objects()

    def get_temporal_flight_observations_by_session(self, session_id, after_datetime):
        return self._flight_feed.get_temporal_flight_observations_by_session(session_id, after_datetime)

    def get_flight_observations_by_session(self, session_id, after_datetime):
        return self._flight_feed.get_flight_observations_by_session(session_id, after_datetime)

    def get_all_flight_observations_in_window(self, start_time, end_time):
        return self._flight_feed.get_all_flight_observations_in_window(start_time, end_time)

    def get_latest_flight_observation_by_session(self, session_id):
        return self._flight_feed.get_latest_flight_observation_by_session(session_id)

    # --- flight declarations ---
    def get_all_flight_declarations(self):
        return self._flight_declarations.get_all_flight_declarations()

    def check_flight_declaration_exists(self, flight_declaration_id):
        return self._flight_declarations.check_flight_declaration_exists(flight_declaration_id)

    def get_flight_declaration_by_id(self, flight_declaration_id):
        return self._flight_declarations.get_flight_declaration_by_id(flight_declaration_id)

    def check_composite_operational_intent_exists(self, flight_declaration_id):
        return self._flight_declarations.check_composite_operational_intent_exists(flight_declaration_id)

    def get_composite_operational_intent_by_declaration_id(self, flight_declaration_id):
        return self._flight_declarations.get_composite_operational_intent_by_declaration_id(flight_declaration_id)

    def get_flight_operational_intent_reference_by_flight_declaration_id(self, flight_declaration_id):
        return self._flight_declarations.get_flight_operational_intent_reference_by_flight_declaration_id(flight_declaration_id)

    def get_active_geofences(self):
        return self._geo_fence.get_active_geofences()

    def get_flight_operational_intent_reference_by_flight_declaration_obj(self, flight_declaration):
        return self._flight_declarations.get_flight_operational_intent_reference_by_flight_declaration_obj(flight_declaration)

    def check_flight_operational_intent_reference_by_id_exists(self, operational_intent_ref_id):
        return self._flight_declarations.check_flight_operational_intent_reference_by_id_exists(operational_intent_ref_id)

    def get_operational_intent_reference_by_id(self, operational_intent_ref_id):
        return self._flight_declarations.get_operational_intent_reference_by_id(operational_intent_ref_id)

    def get_flight_operational_intent_reference_by_id(self, operational_intent_ref_id):
        return self._flight_declarations.get_flight_operational_intent_reference_by_id(operational_intent_ref_id)

    def get_operational_intent_details_by_flight_declaration(self, flight_declaration):
        return self._flight_declarations.get_operational_intent_details_by_flight_declaration(flight_declaration)

    def update_flight_operational_intent_reference_ovn(self, flight_operational_intent_referecne, ovn):
        return self._flight_declarations.update_flight_operational_intent_reference_ovn(flight_operational_intent_referecne, ovn)

    def get_subscribers_of_operational_intent_reference(self, flight_operational_intent_reference):
        return self._flight_declarations.get_subscribers_of_operational_intent_reference(flight_operational_intent_reference)

    def check_flight_operational_intent_details_by_id_exists(self, operational_intent_ref_id):
        return self._flight_declarations.check_flight_operational_intent_details_by_id_exists(operational_intent_ref_id)

    def get_operational_intent_details_by_flight_declaration_id(self, declaration_id):
        return self._flight_declarations.get_operational_intent_details_by_flight_declaration_id(declaration_id)

    def get_conformance_records_for_duration(self, start_time, end_time):
        return self._conformance.get_conformance_records_for_duration(start_time, end_time)

    def get_conformance_record_by_flight_declaration(self, flight_declaration):
        return self._conformance.get_conformance_record_by_flight_declaration(flight_declaration)

    def check_flight_declaration_active(self, flight_declaration_id, now):
        return self._flight_declarations.check_flight_declaration_active(flight_declaration_id, now)

    def check_active_activated_flights_exist(self):
        return self._flight_declarations.check_active_activated_flights_exist()

    def get_active_activated_flight_declarations(self):
        return self._flight_declarations.get_active_activated_flight_declarations()

    def get_current_flight_accepted_activated_declaration_ids(self, now):
        return self._flight_declarations.get_current_flight_accepted_activated_declaration_ids(now)

    # --- rid ---
    def check_flight_details_exist(self, flight_detail_id):
        return self._rid.check_flight_details_exist(flight_detail_id)

    def get_flight_details_by_id(self, flight_detail_id):
        return self._rid.get_flight_details_by_id(flight_detail_id)

    def get_conformance_monitoring_task(self, flight_declaration):
        return self._conformance.get_conformance_monitoring_task(flight_declaration)

    def get_rid_monitoring_task(self, session_id):
        return self._rid.get_rid_monitoring_task(session_id)

    def get_active_rid_observations_for_view(self, start_time, end_time):
        return self._flight_feed.get_active_rid_observations_for_view(start_time, end_time)

    def get_active_rid_observations_for_session(self, session_id):
        return self._flight_feed.get_active_rid_observations_for_session(session_id)

    def get_active_rid_observations_for_session_between_interval(self, start_time, end_time, session_id):
        return self._flight_feed.get_active_rid_observations_for_session_between_interval(start_time, end_time, session_id)

    # --- surveillance ---
    def get_active_surveillance_sensors(self):
        return self._surveillance.get_active_surveillance_sensors()

    def get_surveillance_sensor_by_id(self, sensor_id):
        return self._surveillance.get_surveillance_sensor_by_id(sensor_id)

    def get_surveillance_session_by_id(self, surveillance_session_id):
        return self._surveillance.get_surveillance_session_by_id(surveillance_session_id)

    def get_surveillance_periodic_tasks_by_session_id(self, surveillance_session_id):
        return self._surveillance.get_surveillance_periodic_tasks_by_session_id(surveillance_session_id)

    def get_all_active_surveillance_sessions(self):
        return self._surveillance.get_all_active_surveillance_sessions()

    def get_surveillance_sessions_with_events_in_window(self, start_time, end_time):
        return self._surveillance.get_surveillance_sessions_with_events_in_window(start_time, end_time)

    def get_sensor_health_record(self, sensor_id):
        return self._surveillance.get_sensor_health_record(sensor_id)

    def get_health_tracking_records_for_sensor(self, sensor_id, start_time, end_time):
        return self._surveillance.get_health_tracking_records_for_sensor(sensor_id, start_time, end_time)

    def get_sensor_status_before_time(self, sensor_id, before_time):
        return self._surveillance.get_sensor_status_before_time(sensor_id, before_time)

    def get_heartbeat_events_in_window(self, start_time, end_time):
        return self._surveillance.get_heartbeat_events_in_window(start_time, end_time)

    def get_heartbeat_events_for_session(self, surveillance_session_id, start_time, end_time):
        return self._surveillance.get_heartbeat_events_for_session(surveillance_session_id, start_time, end_time)

    def get_track_events_for_session(self, surveillance_session_id, start_time, end_time):
        return self._surveillance.get_track_events_for_session(surveillance_session_id, start_time, end_time)

    def get_failure_notifications_for_sensor(self, sensor_id, start_time, end_time):
        return self._surveillance.get_failure_notifications_for_sensor(sensor_id, start_time, end_time)

    # --- notifications ---
    def get_active_user_notifications_between_interval(self, start_time, end_time):
        return self._notifications.get_active_user_notifications_between_interval(start_time, end_time)

    # --- rid subscriptions ---
    def check_rid_subscription_record_by_view_hash_exists(self, view_hash):
        return self._rid.check_rid_subscription_record_by_view_hash_exists(view_hash)

    def check_rid_subscription_record_by_subscription_id_exists(self, subscription_id):
        return self._rid.check_rid_subscription_record_by_subscription_id_exists(subscription_id)

    def get_rid_subscription_record_by_subscription_id(self, subscription_id):
        return self._rid.get_rid_subscription_record_by_subscription_id(subscription_id)

    def get_all_rid_simulated_subscription_records(self):
        return self._rid.get_all_rid_simulated_subscription_records()

    def get_rid_subscription_record_by_id(self, id):
        return self._rid.get_rid_subscription_record_by_id(id)


class FlightBlenderDatabaseWriter:
    @staticmethod
    def _normalize_timestamp(ts):
        if not ts:
            return None
        try:
            timestamp = float(ts)
        except (TypeError, ValueError):
            logger.warning("Invalid sensor timestamp {!r}; storing observation without sensor_timestamp", ts)
            return None
        if timestamp > 1e15:
            timestamp = timestamp / 1_000_000
        elif timestamp > 1e12:
            timestamp = timestamp / 1_000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            logger.warning("Out-of-range sensor timestamp {!r}; storing observation without sensor_timestamp", ts)
            return None

    def __init__(self):
        self._geo_fence = DjangoGeoFenceRepository()
        self._flight_feed = DjangoFlightFeedRepository()
        self._flight_declarations = DjangoFlightDeclarationRepository()
        self._conformance = DjangoConformanceRepository()
        self._constraint = DjangoConstraintRepository()
        self._rid = DjangoRIDRepository()
        self._surveillance = DjangoSurveillanceRepository()
        self._notifications = DjangoNotificationsRepository()

    # --- peer ops ---
    def create_or_update_peer_operational_intent_details(self, peer_operational_intent_id, operational_intent_details):
        return self._flight_declarations.create_or_update_peer_operational_intent_details(peer_operational_intent_id, operational_intent_details)

    def create_or_update_peer_operational_intent_reference(self, peer_operational_intent_reference_id, peer_operational_intent_reference):
        return self._flight_declarations.create_or_update_peer_operational_intent_reference(
            peer_operational_intent_reference_id, peer_operational_intent_reference
        )

    def get_peer_operational_intent_reference_by_id(self, operational_intent_reference_id):
        return self._flight_declarations.get_peer_operational_intent_reference_by_id(operational_intent_reference_id)

    # --- flight feed ---
    def bulk_write_flight_observations(self, observations):
        return self._flight_feed.bulk_write_flight_observations(observations)

    def write_flight_observation(self, single_observation):
        return self._flight_feed.write_flight_observation(single_observation)

    def delete_all_flight_observations(self):
        return self._flight_feed.delete_all_flight_observations()

    # --- notifications ---
    def create_operator_rid_notification(self, operator_rid_notification):
        return self._notifications.create_operator_rid_notification(operator_rid_notification)

    # --- flight declarations ---
    def create_flight_declaration(self, flight_declaration_creation):
        return self._flight_declarations.create_flight_declaration(flight_declaration_creation)

    def delete_flight_declaration(self, flight_declaration_id):
        return self._flight_declarations.delete_flight_declaration(flight_declaration_id)

    def set_flight_declaration_non_conforming(self, flight_declaration):
        return self._flight_declarations.set_flight_declaration_non_conforming(flight_declaration)

    def create_flight_operational_intent_reference_with_submitted_operational_intent(self, flight_declaration, operational_intent_reference_payload):
        return self._flight_declarations.create_flight_operational_intent_reference_with_submitted_operational_intent(
            flight_declaration, operational_intent_reference_payload
        )

    def create_flight_operational_intent_reference_subscribers(self, flight_declaration, subscribers):
        return self._flight_declarations.create_flight_operational_intent_reference_subscribers(flight_declaration, subscribers)

    def create_flight_operational_intent_details_with_submitted_operational_intent(self, flight_declaration, operational_intent_details_payload):
        return self._flight_declarations.create_flight_operational_intent_details_with_submitted_operational_intent(
            flight_declaration, operational_intent_details_payload
        )

    def create_or_update_peer_composite_operational_intent(self, operation_id, composite_operational_intent):
        return self._flight_declarations.create_or_update_peer_composite_operational_intent(operation_id, composite_operational_intent)

    def create_or_update_composite_operational_intent(self, flight_declaration, composite_operational_intent_payload):
        return self._flight_declarations.create_or_update_composite_operational_intent(flight_declaration, composite_operational_intent_payload)

    def update_flight_operational_intent_reference_with_dss_response(
        self, flight_declaration, dss_operational_intent_reference_id, ovn, dss_response
    ):
        return self._flight_declarations.update_flight_operational_intent_reference_with_dss_response(
            flight_declaration, dss_operational_intent_reference_id, ovn, dss_response
        )

    def create_flight_operational_intent_reference_from_flight_declaration_obj(self, flight_declaration):
        return self._flight_declarations.create_flight_operational_intent_reference_from_flight_declaration_obj(flight_declaration)

    def create_flight_operational_intent_reference(self, flight_declaration, created_operational_intent_reference):
        return self._flight_declarations.create_flight_operational_intent_reference(flight_declaration, created_operational_intent_reference)

    def update_telemetry_timestamp(self, flight_declaration_id):
        return self._flight_declarations.update_telemetry_timestamp(flight_declaration_id)

    def update_flight_operational_intent_reference_op_int(self, flight_operational_intent_reference, dss_operational_intent_reference_id):
        return self._flight_declarations.update_flight_operational_intent_reference_op_int(
            flight_operational_intent_reference, dss_operational_intent_reference_id
        )

    def update_flight_operational_intent_reference_ovn(self, flight_operational_intent_reference, ovn):
        return self._flight_declarations.update_flight_operational_intent_reference_ovn(flight_operational_intent_reference, ovn)

    def update_flight_operational_intent_reference(self, flight_operational_intent_reference, update_operational_intent_reference):
        return self._flight_declarations.update_flight_operational_intent_reference(
            flight_operational_intent_reference, update_operational_intent_reference
        )

    def update_flight_operational_intent_details(self, flight_operational_intent_detail, operational_intent_details):
        return self._flight_declarations.update_flight_operational_intent_details(flight_operational_intent_detail, operational_intent_details)

    def update_flight_operational_intent_reference_op_int_ovn(self, flight_operational_intent_reference, dss_operational_intent_reference_id, ovn):
        return self._flight_declarations.update_flight_operational_intent_reference_op_int_ovn(
            flight_operational_intent_reference, dss_operational_intent_reference_id, ovn
        )

    def update_flight_operation_operational_intent(self, flight_declaration_id, operational_intent):
        return self._flight_declarations.update_flight_operation_operational_intent(flight_declaration_id, operational_intent)

    def update_flight_operation_state(self, flight_declaration_id, state):
        return self._flight_declarations.update_flight_operation_state(flight_declaration_id, state)

    def clear_stored_operational_intents(self):
        return self._flight_declarations.clear_stored_operational_intents()

    # --- conformance ---
    def write_flight_conformance_record(
        self, flight_declaration, conformance_non_conformance_state, description, event_type, geofence_breach, resolved, geofence
    ):
        return self._conformance.write_flight_conformance_record(
            flight_declaration, conformance_non_conformance_state, description, event_type, geofence_breach, resolved, geofence
        )

    def create_conformance_monitoring_periodic_task(self, flight_declaration):
        return self._conformance.create_conformance_monitoring_periodic_task(flight_declaration)

    def remove_conformance_monitoring_periodic_task(self, conformance_monitoring_task):
        return self._conformance.remove_conformance_monitoring_periodic_task(conformance_monitoring_task)

    # --- surveillance ---
    def create_surveillance_session(self, surveillance_session_id, valid_until):
        return self._surveillance.create_surveillance_session(surveillance_session_id, valid_until)

    def create_surveillance_monitoring_heartbeat_periodic_task(self, surveillance_session_id):
        return self._surveillance.create_surveillance_monitoring_heartbeat_periodic_task(surveillance_session_id)

    def create_surveillance_monitoring_track_periodic_task(self, surveillance_session_id):
        return self._surveillance.create_surveillance_monitoring_track_periodic_task(surveillance_session_id)

    def remove_track_monitoring_heartbeat_periodic_task(self, track_monitoring_heartbeat_task):
        return self._surveillance.remove_track_monitoring_heartbeat_periodic_task(track_monitoring_heartbeat_task)

    def remove_surveillance_monitoring_heartbeat_periodic_task(self, surveillance_monitoring_heartbeat_task):
        return self._surveillance.remove_surveillance_monitoring_heartbeat_periodic_task(surveillance_monitoring_heartbeat_task)

    def delete_surveillance_session(self, surveillance_session_id):
        return self._surveillance.delete_surveillance_session(surveillance_session_id)

    def update_sensor_health_status(self, sensor_id, new_status, recovery_type=None):
        return self._surveillance.update_sensor_health_status(sensor_id, new_status, recovery_type)

    def record_heartbeat_event(self, surveillance_session_id, expected_at, delivered_on_time):
        return self._surveillance.record_heartbeat_event(surveillance_session_id, expected_at, delivered_on_time)

    def record_track_event(self, surveillance_session_id, expected_at, had_active_tracks):
        return self._surveillance.record_track_event(surveillance_session_id, expected_at, had_active_tracks)

    # --- rid ---
    def create_rid_subscription_record(self, subscription_id, record_id, view, view_hash, end_datetime, flights_dict, is_simulated):
        return self._rid.create_rid_subscription_record(subscription_id, record_id, view, view_hash, end_datetime, flights_dict, is_simulated)

    def update_flight_details_in_rid_subscription_record(self, existing_subscription_record, flights_dict):
        return self._rid.update_flight_details_in_rid_subscription_record(existing_subscription_record, flights_dict)

    def delete_all_simulated_rid_subscription_records(self):
        return self._rid.delete_all_simulated_rid_subscription_records()

    def create_or_update_rid_flight_details(self, rid_flight_details_payload):
        return self._rid.create_or_update_rid_flight_details(rid_flight_details_payload)

    def delete_all_flight_details(self):
        return self._rid.delete_all_flight_details()

    def create_rid_stream_monitoring_periodic_task(self, session_id, end_datetime):
        return self._rid.create_rid_stream_monitoring_periodic_task(session_id, end_datetime)

    def remove_rid_stream_monitoring_periodic_task(self, rid_stream_monitoring_task):
        return self._rid.remove_rid_stream_monitoring_periodic_task(rid_stream_monitoring_task)

    # --- geo fence ---
    def create_or_update_geofence(self, geofence_payload):
        return self._geo_fence.create_or_update_geofence(geofence_payload)

    # --- constraint ---
    def write_constraint_details(self, constraint_id, constraint):
        return self._constraint.write_constraint_details(constraint_id, constraint)

    def write_constraint_reference_details(self, constraint):
        return self._constraint.write_constraint_reference_details(constraint)

    def create_or_update_composite_constraint(self, composite_constraint_payload):
        return self._constraint.create_or_update_composite_constraint(composite_constraint_payload)

    def update_constraint_reference_ovn(self, constraint_reference, ovn):
        return self._constraint.update_constraint_reference_ovn(constraint_reference, ovn)

    def create_or_update_constraint_detail(self, constraint, geofence):
        return self._constraint.create_or_update_constraint_detail(constraint, geofence)

    def create_or_update_constraint_reference(self, constraint_reference, geofence, flight_declaration):
        return self._constraint.create_or_update_constraint_reference(constraint_reference, geofence, flight_declaration)
