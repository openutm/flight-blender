import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, List, Optional

import arrow
from loguru import logger
from pyproj import Geod

from flight_blender.domain_types.flight_feed import SingleAirtrafficObservation
from flight_blender.domain_types.protocols_flight_feed import FlightFeedRepository
from flight_blender.domain_types.protocols_surveillance import SurveillanceRepository, SurveillanceTaskScheduler, TrackStore
from flight_blender.domain_types.surveillance import (
    FLIGHT_OBSERVATION_TRAFFIC_SOURCE,
    ActiveTrack,
    AggregateHealthMetrics,
    AircraftPosition,
    AircraftState,
    HealthMessage,
    HeartbeatDeliveryProbability,
    HeartbeatRateMetric,
    LatLangAltPoint,
    SensorHealthMetrics,
    SpeedAccuracy,
    SurveillanceMetrics,
    SurveillanceSensorDetail,
    SurveillanceSensorFailureNotificationDetail,
    SurveillanceStatus,
    TrackMessage,
    TrackUpdateProbability,
)

if TYPE_CHECKING:
    from flight_blender.repositories.conformance_repo import SyncSurveillanceDB


class SurveillanceOperations:
    def __init__(
        self,
        repo: SurveillanceRepository,
        scheduler: SurveillanceTaskScheduler,
        flight_feed_repo: FlightFeedRepository | None = None,
    ):
        self.repo = repo
        self.scheduler = scheduler
        self.flight_feed_repo = flight_feed_repo

    async def get_health(self) -> dict:
        active_sensors = await self.repo.get_active_surveillance_sensors()
        sensor_statuses = []
        for sensor in active_sensors:
            health = await self.repo.get_sensor_health_record(sensor.id)
            if health:
                sensor_statuses.append(health.status)

        if not sensor_statuses or all(s == "outage" for s in sensor_statuses):
            current_status = SurveillanceStatus.OUTAGE
        elif any(s in ("degraded", "outage") for s in sensor_statuses):
            current_status = SurveillanceStatus.DEGRADED
        else:
            current_status = SurveillanceStatus.OPERATIONAL

        health_obj = HealthMessage(
            sdsp_identifier="FLIGHT_BLENDER_SDSP",
            current_status=current_status,
            machine_readable_file_of_estimated_coverage="",
            scheduled_degradations="None",
            timestamp=arrow.utcnow().isoformat(),
        )
        return asdict(health_obj)

    async def start_stop_surveillance_session(self, session_id: uuid.UUID, action: str) -> tuple[dict, int]:
        if action == "start":
            existing = await self.repo.get_session_by_id(session_id)
            if existing is not None:
                return {"error": "Surveillance monitoring heartbeat task already exists"}, 400

            valid_until = datetime.now(timezone.utc) + timedelta(minutes=30)
            created = await self.repo.create_session(session_id=session_id, valid_until=valid_until)
            if not created:
                logger.error(f"Failed to create surveillance session with id {session_id}")
                return {"error": "Failed to create surveillance session"}, 500

            heartbeat_ok = await self._create_heartbeat_task(str(session_id))
            if not heartbeat_ok:
                await self.repo.delete_session(session_id)
                return {"error": "Failed to create surveillance monitoring heartbeat task"}, 500

            track_ok = await self._create_track_task(str(session_id))
            if not track_ok:
                await self.repo.delete_session(session_id)
                return {"error": "Failed to create surveillance monitoring track task"}, 500

            return {"status": "Surveillance monitoring heartbeat started"}, 200
        else:
            session = await self.repo.get_session_by_id(session_id)
            if session is None:
                return {"error": f"Invalid surveillance_session_id provided: {session_id}"}, 400

            self.scheduler.cancel_session_tasks(str(session_id))
            return {"status": "Surveillance monitoring tasks removed successfully"}, 200

    async def _create_heartbeat_task(self, session_id: str) -> bool:
        try:
            return self.scheduler.schedule_surveillance_heartbeat(session_id)
        except Exception:
            logger.exception("Failed to create heartbeat periodic task for session %s", session_id)
            return False

    async def _create_track_task(self, session_id: str) -> bool:
        try:
            return self.scheduler.schedule_surveillance_track(session_id)
        except Exception:
            logger.exception("Failed to create track periodic task for session %s", session_id)
            return False

    async def _get_periodic_tasks_for_session(self, session_id: str):
        return []

    async def list_surveillance_sensors(self) -> list[dict]:
        sensors = await self.repo.get_active_surveillance_sensors()

        source_map = dict(FLIGHT_OBSERVATION_TRAFFIC_SOURCE)
        return [
            asdict(
                SurveillanceSensorDetail(
                    id=str(s.id),
                    sensor_type_display=source_map.get(s.sensor_type, str(s.sensor_type)),
                    sensor_identifier=s.sensor_identifier,
                    created_at=s.created_at.isoformat(),
                    updated_at=s.updated_at.isoformat(),
                )
            )
            for s in sensors
        ]

    async def get_service_metrics(
        self,
        start_date: Optional[str],
        end_date: Optional[str],
        session_id: Optional[str],
    ) -> tuple[dict, int]:
        now = arrow.now()
        one_week_ago = now.shift(weeks=-1)
        if start_date:
            start_date = start_date.replace(" ", "+")
        if end_date:
            end_date = end_date.replace(" ", "+")
        try:
            start_dt = arrow.get(start_date).datetime if start_date else one_week_ago.datetime
            end_dt = arrow.get(end_date).datetime if end_date else now.datetime
        except arrow.parser.ParserError:
            return {"error": "Invalid date format. Use ISO8601 format."}, 400

        active_sessions = await self.repo.get_all_active_sessions()
        active_session_count = len(active_sessions)

        if session_id:
            sessions_to_process = [session_id]
        else:
            sessions_in_window = await self.repo.get_sessions_with_events_in_window(start_time=start_dt, end_time=end_dt)
            sessions_to_process = [str(s.id) for s in sessions_in_window]

        heartbeat_rates = []
        heartbeat_delivery_probabilities = []
        track_update_probabilities = []

        for sid in sessions_to_process:
            sid_uuid = uuid.UUID(sid)
            heartbeat_rates.append(await self._calculate_heartbeat_rate(sid_uuid, start_dt, end_dt))
            heartbeat_delivery_probabilities.append(await self._calculate_heartbeat_delivery_probability(sid_uuid, start_dt, end_dt))
            track_update_probabilities.append(await self._calculate_track_update_probability(sid, start_dt, end_dt))

        active_sensors = await self.repo.get_active_surveillance_sensors()
        per_sensor_health = []
        for sensor in active_sensors:
            per_sensor_health.append(await self._calculate_sensor_health_metrics(sensor.id, sensor.sensor_identifier, start_dt, end_dt))

        aggregate_health = _calculate_aggregate_health_metrics(per_sensor_health, start_dt, end_dt)

        metric_response = SurveillanceMetrics(
            heartbeat_rates=heartbeat_rates,
            heartbeat_delivery_probabilities=heartbeat_delivery_probabilities,
            track_update_probabilities=track_update_probabilities,
            per_sensor_health=per_sensor_health,
            aggregate_health=aggregate_health,
            active_sessions=active_session_count,
            window_start=start_dt.isoformat(),
            window_end=end_dt.isoformat(),
        )
        return asdict(metric_response), 200

    async def _calculate_heartbeat_rate(self, session_id: uuid.UUID, start_time: datetime, end_time: datetime) -> HeartbeatRateMetric:
        events = await self.repo.get_heartbeat_events_for_session(session_id=session_id, start_time=start_time, end_time=end_time)
        total = len(events)
        if total >= 2:
            span_seconds = (events[-1].dispatched_at - events[0].dispatched_at).total_seconds()
            rate_hz = round((total - 1) / span_seconds, 2) if span_seconds > 0 else 0.0
        else:
            rate_hz = 0.0
        return HeartbeatRateMetric(
            measured_rate_hz=rate_hz,
            target_rate_hz=1.0,
            session_id=str(session_id),
            window_start=start_time.isoformat(),
            window_end=end_time.isoformat(),
            total_heartbeats_in_window=total,
        )

    async def _calculate_heartbeat_delivery_probability(
        self, session_id: uuid.UUID, start_time: datetime, end_time: datetime
    ) -> HeartbeatDeliveryProbability:
        events = await self.repo.get_heartbeat_events_for_session(session_id=session_id, start_time=start_time, end_time=end_time)
        total = len(events)
        on_time = sum(1 for e in events if e.delivered_on_time)
        probability = round(on_time / total, 6) if total > 0 else 0.0
        return HeartbeatDeliveryProbability(
            probability=probability,
            delivered_on_time=on_time,
            total_expected=total,
            session_id=str(session_id),
            window_start=start_time.isoformat(),
            window_end=end_time.isoformat(),
        )

    async def _calculate_track_update_probability(self, session_id: str, start_time: datetime, end_time: datetime) -> TrackUpdateProbability:
        total = 0
        if self.flight_feed_repo is not None:
            observations = await self.flight_feed_repo.get_all_flight_observations_in_window(start_time=start_time, end_time=end_time)
            total = len(observations)
        probability = round(total / total, 6) if total > 0 else 0.0
        return TrackUpdateProbability(
            probability=probability,
            ticks_with_active_tracks=total,
            total_ticks=total,
            session_id=session_id,
            window_start=start_time.isoformat(),
            window_end=end_time.isoformat(),
        )

    async def _calculate_sensor_health_metrics(
        self,
        sensor_id: uuid.UUID,
        sensor_identifier: str,
        start_time: datetime,
        end_time: datetime,
    ) -> SensorHealthMetrics:
        records = await self.repo.get_health_tracking_records_for_sensor(sensor_id=sensor_id, start_time=start_time, end_time=end_time)
        pre_window_status = await self.repo.get_sensor_status_before_time(sensor_id=sensor_id, before_time=start_time)

        failure_states = {"degraded", "outage"}
        current_failure_onset: Optional[datetime] = None
        operational_start: Optional[datetime] = None

        if pre_window_status in failure_states:
            current_failure_onset = start_time
        elif pre_window_status == "operational":
            operational_start = start_time

        recovery_durations: list[float] = []
        auto_recovery_durations: list[float] = []
        operational_intervals: list[tuple[float, Optional[str]]] = []

        for record in records:
            status = record.status
            rec_time: datetime = record.recorded_at

            if status in failure_states:
                if current_failure_onset is None:
                    if operational_start is not None:
                        interval_secs = (rec_time - operational_start).total_seconds()
                        operational_intervals.append((interval_secs, None))
                    current_failure_onset = rec_time
                    operational_start = None
            elif status == "operational":
                if current_failure_onset is not None:
                    duration = (rec_time - current_failure_onset).total_seconds()
                    recovery_durations.append(duration)
                    if record.recovery_type == "automatic":
                        auto_recovery_durations.append(duration)
                    operational_start = rec_time
                    current_failure_onset = None
                    if operational_intervals:
                        last = operational_intervals[-1]
                        operational_intervals[-1] = (last[0], record.recovery_type)
                else:
                    if operational_start is None:
                        operational_start = rec_time

        mttr = round(sum(recovery_durations) / len(recovery_durations), 2) if recovery_durations else None
        avg_auto_recovery = round(sum(auto_recovery_durations) / len(auto_recovery_durations), 2) if auto_recovery_durations else None
        auto_intervals = [d for d, rt in operational_intervals if rt == "automatic"]
        mtbf_auto = round(sum(auto_intervals) / len(auto_intervals), 2) if auto_intervals else None
        manual_intervals = [d for d, rt in operational_intervals if rt == "manual"]
        mtbf_manual = round(sum(manual_intervals) / len(manual_intervals), 2) if manual_intervals else None

        return SensorHealthMetrics(
            sensor_id=str(sensor_id),
            sensor_identifier=sensor_identifier,
            mttr_seconds=mttr,
            auto_recovery_time_seconds=avg_auto_recovery,
            mtbf_with_auto_recovery_seconds=mtbf_auto,
            mtbf_without_auto_recovery_seconds=mtbf_manual,
            failure_count=len(recovery_durations),
            auto_recovery_count=len(auto_recovery_durations),
            manual_recovery_count=len(recovery_durations) - len(auto_recovery_durations),
            window_start=start_time.isoformat(),
            window_end=end_time.isoformat(),
        )

    async def update_sensor_health(self, sensor_id: uuid.UUID, new_status: str, recovery_type: Optional[str]) -> tuple[dict, int]:
        valid_statuses = {"operational", "degraded", "outage"}
        if new_status not in valid_statuses:
            return {"error": f"Invalid status. Must be one of: {', '.join(sorted(valid_statuses))}"}, 400

        valid_recovery_types = {"automatic", "manual", None}
        if recovery_type not in valid_recovery_types:
            return {"error": "Invalid recovery_type. Must be 'automatic', 'manual', or omitted."}, 400

        success = await self.repo.update_sensor_health_status(
            sensor_id=sensor_id,
            new_status=new_status,
            recovery_type=recovery_type,
        )
        if not success:
            return {"error": f"Sensor {sensor_id} not found or update failed"}, 404

        return {"status": "Sensor health updated", "sensor_id": str(sensor_id), "new_status": new_status}, 200

    async def list_sensor_health_notifications(
        self,
        sensor_id: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> tuple[dict, int]:
        now = arrow.now()
        one_week_ago = now.shift(weeks=-1)
        if start_date:
            start_date = start_date.replace(" ", "+")
        if end_date:
            end_date = end_date.replace(" ", "+")
        try:
            start_dt = arrow.get(start_date).datetime if start_date else one_week_ago.datetime
            end_dt = arrow.get(end_date).datetime if end_date else now.datetime
        except arrow.parser.ParserError:
            return {"error": "Invalid date format. Use ISO8601 format."}, 400

        if sensor_id:
            notifications = await self.repo.get_failure_notifications_for_sensor(
                sensor_id=uuid.UUID(sensor_id),
                start_time=start_dt,
                end_time=end_dt,
            )
        else:
            notifications = await self.repo.get_all_failure_notifications(start_time=start_dt, end_time=end_dt)

        result = []
        for n in notifications:
            sensor = await self.repo.get_sensor_by_id(n.sensor_id)
            sensor_identifier = sensor.sensor_identifier if sensor else str(n.sensor_id)
            result.append(
                asdict(
                    SurveillanceSensorFailureNotificationDetail(
                        id=str(n.id),
                        sensor_id=str(n.sensor_id),
                        sensor_identifier=sensor_identifier,
                        previous_status=n.previous_status,
                        new_status=n.new_status,
                        recovery_type=n.recovery_type,
                        message=n.message,
                        created_at=n.created_at.isoformat(),
                    )
                )
            )
        return {"notifications": result}, 200


def _calculate_aggregate_health_metrics(
    sensor_metrics_list: list[SensorHealthMetrics],
    start_time: datetime,
    end_time: datetime,
) -> Optional[AggregateHealthMetrics]:
    if not sensor_metrics_list:
        return None

    def _avg(values: list[float]) -> Optional[float]:
        return round(sum(values) / len(values), 2) if values else None

    mttrs = [m.mttr_seconds for m in sensor_metrics_list if m.mttr_seconds is not None]
    auto_recoveries = [m.auto_recovery_time_seconds for m in sensor_metrics_list if m.auto_recovery_time_seconds is not None]
    mtbf_autos = [m.mtbf_with_auto_recovery_seconds for m in sensor_metrics_list if m.mtbf_with_auto_recovery_seconds is not None]
    mtbf_manuals = [m.mtbf_without_auto_recovery_seconds for m in sensor_metrics_list if m.mtbf_without_auto_recovery_seconds is not None]

    return AggregateHealthMetrics(
        avg_mttr_seconds=_avg(mttrs),
        avg_auto_recovery_time_seconds=_avg(auto_recoveries),
        avg_mtbf_with_auto_recovery_seconds=_avg(mtbf_autos),
        avg_mtbf_without_auto_recovery_seconds=_avg(mtbf_manuals),
        total_sensors=len(sensor_metrics_list),
        window_start=start_time.isoformat(),
        window_end=end_time.isoformat(),
    )


class SurveillanceMetricCalculator:
    """Calculates ASTM F3623 SDSP surveillance metrics from database records."""

    def __init__(self, database_reader: "SyncSurveillanceDB"):
        self.db: "SyncSurveillanceDB" = database_reader

    def calculate_heartbeat_rate(self, session_id: str, start_time: datetime, end_time: datetime) -> HeartbeatRateMetric:
        events = self.db.get_heartbeat_events_for_session(session_id=session_id, start_time=start_time, end_time=end_time)
        total = events.count()
        if total >= 2:
            first_event = events.first()
            last_event = events.last()
            if first_event is None or last_event is None:
                rate_hz = 0.0
            else:
                span_seconds = (last_event.dispatched_at - first_event.dispatched_at).total_seconds()
                rate_hz = round((total - 1) / span_seconds, 2) if span_seconds > 0 else 0.0
        else:
            rate_hz = 0.0
        return HeartbeatRateMetric(
            measured_rate_hz=rate_hz,
            target_rate_hz=1.0,
            session_id=session_id,
            window_start=start_time.isoformat(),
            window_end=end_time.isoformat(),
            total_heartbeats_in_window=total,
        )

    def calculate_heartbeat_delivery_probability(self, session_id: str, start_time: datetime, end_time: datetime) -> HeartbeatDeliveryProbability:
        events = self.db.get_heartbeat_events_for_session(session_id=session_id, start_time=start_time, end_time=end_time)
        total = events.count()
        on_time = events.filter(delivered_on_time=True).count()
        probability = round(on_time / total, 6) if total > 0 else 0.0
        return HeartbeatDeliveryProbability(
            probability=probability,
            delivered_on_time=on_time,
            total_expected=total,
            session_id=session_id,
            window_start=start_time.isoformat(),
            window_end=end_time.isoformat(),
        )

    def calculate_track_update_probability(self, session_id: str, start_time: datetime, end_time: datetime) -> TrackUpdateProbability:
        observations = self.db.get_all_flight_observations_in_window(start_time=start_time, end_time=end_time)
        total = observations.count()
        with_tracks = total
        probability = round(with_tracks / total, 6) if total > 0 else 0.0
        return TrackUpdateProbability(
            probability=probability,
            ticks_with_active_tracks=with_tracks,
            total_ticks=total,
            session_id=session_id,
            window_start=start_time.isoformat(),
            window_end=end_time.isoformat(),
        )

    def calculate_sensor_health_metrics(self, sensor_id: str, start_time: datetime, end_time: datetime) -> SensorHealthMetrics:
        records = list(self.db.get_health_tracking_records_for_sensor(sensor_id=sensor_id, start_time=start_time, end_time=end_time))

        sensor = None
        try:
            sensor = self.db.get_surveillance_sensor_by_id(sensor_id=sensor_id)
        except Exception:
            logger.warning(f"calculate_sensor_health_metrics: sensor {sensor_id} not found")

        sensor_identifier = sensor.sensor_identifier if sensor else str(sensor_id)
        pre_window_status = self.db.get_sensor_status_before_time(sensor_id=sensor_id, before_time=start_time)
        failure_states = {"degraded", "outage"}
        current_failure_onset: Optional[datetime] = None
        operational_start: Optional[datetime] = None

        if pre_window_status in failure_states:
            current_failure_onset = start_time
        elif pre_window_status == "operational":
            operational_start = start_time

        recovery_durations: list[float] = []
        auto_recovery_durations: list[float] = []
        operational_intervals: list[tuple[float, Optional[str]]] = []

        for record in records:
            status = record.status
            rec_time: datetime = record.recorded_at

            if status in failure_states:
                if current_failure_onset is None:
                    if operational_start is not None:
                        interval_secs = (rec_time - operational_start).total_seconds()
                        operational_intervals.append((interval_secs, getattr(record, "_preceding_recovery_type", None)))
                    current_failure_onset = rec_time
                    operational_start = None
            elif status == "operational":
                if current_failure_onset is not None:
                    duration = (rec_time - current_failure_onset).total_seconds()
                    recovery_durations.append(duration)
                    if record.recovery_type == "automatic":
                        auto_recovery_durations.append(duration)
                    operational_start = rec_time
                    current_failure_onset = None
                    if operational_intervals:
                        last_interval = operational_intervals[-1]
                        operational_intervals[-1] = (last_interval[0], record.recovery_type)
                else:
                    if operational_start is None:
                        operational_start = rec_time

        mttr: Optional[float] = round(sum(recovery_durations) / len(recovery_durations), 2) if recovery_durations else None
        avg_auto_recovery: Optional[float] = (
            round(sum(auto_recovery_durations) / len(auto_recovery_durations), 2) if auto_recovery_durations else None
        )
        auto_intervals = [d for d, rt in operational_intervals if rt == "automatic"]
        mtbf_auto: Optional[float] = round(sum(auto_intervals) / len(auto_intervals), 2) if auto_intervals else None
        manual_intervals = [d for d, rt in operational_intervals if rt == "manual"]
        mtbf_manual: Optional[float] = round(sum(manual_intervals) / len(manual_intervals), 2) if manual_intervals else None
        auto_recovery_count = len(auto_recovery_durations)
        manual_recovery_count = len(recovery_durations) - auto_recovery_count

        return SensorHealthMetrics(
            sensor_id=str(sensor_id),
            sensor_identifier=sensor_identifier,
            mttr_seconds=mttr,
            auto_recovery_time_seconds=avg_auto_recovery,
            mtbf_with_auto_recovery_seconds=mtbf_auto,
            mtbf_without_auto_recovery_seconds=mtbf_manual,
            failure_count=len(recovery_durations),
            auto_recovery_count=auto_recovery_count,
            manual_recovery_count=manual_recovery_count,
            window_start=start_time.isoformat(),
            window_end=end_time.isoformat(),
        )

    def calculate_aggregate_health_metrics(
        self,
        sensor_metrics_list: list[SensorHealthMetrics],
        start_time: datetime,
        end_time: datetime,
    ) -> AggregateHealthMetrics:
        def _avg(values: list[float]) -> Optional[float]:
            return round(sum(values) / len(values), 2) if values else None

        mttrs = [m.mttr_seconds for m in sensor_metrics_list if m.mttr_seconds is not None]
        auto_recoveries = [m.auto_recovery_time_seconds for m in sensor_metrics_list if m.auto_recovery_time_seconds is not None]
        mtbf_autos = [m.mtbf_with_auto_recovery_seconds for m in sensor_metrics_list if m.mtbf_with_auto_recovery_seconds is not None]
        mtbf_manuals = [m.mtbf_without_auto_recovery_seconds for m in sensor_metrics_list if m.mtbf_without_auto_recovery_seconds is not None]

        return AggregateHealthMetrics(
            avg_mttr_seconds=_avg(mttrs),
            avg_auto_recovery_time_seconds=_avg(auto_recoveries),
            avg_mtbf_with_auto_recovery_seconds=_avg(mtbf_autos),
            avg_mtbf_without_auto_recovery_seconds=_avg(mtbf_manuals),
            total_sensors=len(sensor_metrics_list),
            window_start=start_time.isoformat(),
            window_end=end_time.isoformat(),
        )


class SpecializedTrafficDataFuser:
    """Placeholder data fuser — override generate_track_messages for custom fusion.

    Set ``FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER`` to
    ``flight_blender.core.operations.surveillance.SpecializedTrafficDataFuser``.
    """

    def __init__(self, raw_observations: List[SingleAirtrafficObservation]):
        self.raw_observations = raw_observations

    def fuse_raw_observations(self) -> List[SingleAirtrafficObservation]:
        raise NotImplementedError

    def generate_track_messages(self, fused_observations: List[SingleAirtrafficObservation]) -> List[TrackMessage]:
        raise NotImplementedError


# ── Default TrafficDataFuser (from surveillance/utils.py) ─────────────────────


class BaseTrafficDataFuser:
    """Optional convenience base class for traffic data fusion implementations.

    This class implements a template method pattern for traffic data fusion workflows.
    It provides concrete methods for common operations (speed/bearing calculation,
    track message generation) while expecting subclasses to implement specific fusion
    algorithms and track management logic.

    Implementations can range from simple pass-through fusers to sophisticated
    multi-sensor fusion algorithms using Kalman filters or other estimation techniques.

    .. note::

        The canonical interface is the ``TrafficDataFuser`` Protocol defined in
        ``flight_blender.core.repositories.surveillance``.
        Extending this base class is optional.

    Attributes:
        session_id: Unique identifier for the surveillance session
        raw_observations: List of raw air traffic observations to be processed
        geod: Geodetic calculator for WGS84 ellipsoid (distance/bearing calculations)
        redis_stream_helper: Track store (must be initialized by subclass)
        SDSP_IDENTIFIER: Surveillance Display Service Provider identifier (must be set by subclass)
    """

    def __init__(self, session_id: str, raw_observations: List[SingleAirtrafficObservation]):
        """Initialize the traffic data fuser.

        Args:
            session_id: Unique identifier for the surveillance session
            raw_observations: List of raw air traffic observations from various sources

        Note:
            Subclasses must initialize ``redis_stream_helper`` (a ``TrackStore``) and
            ``SDSP_IDENTIFIER`` attributes.
        """
        self.session_id = session_id
        self.raw_observations = raw_observations
        self.geod = Geod(ellps="WGS84")
        self.redis_stream_helper: TrackStore
        self.SDSP_IDENTIFIER: str = "FLIGHT_BLENDER_SDSP"

    def generate_track_messages(self) -> List[TrackMessage]:
        """Orchestrate the complete data fusion and track generation workflow.

        This template method coordinates the entire fusion process by calling:
        1. _pre_process_raw_data() - Optional pre-processing hook
        2. _fuse_raw_observations() - Abstract method for sensor fusion
        3. _generate_active_tracks() - Abstract method for track management
        4. Retrieve active tracks from the track store
        5. _post_process_fused_data() - Optional post-processing hook
        6. _generate_track_messages_impl() - Convert tracks to messages

        Returns:
            List of track messages ready for distribution to consumers
        """
        self._pre_process_raw_data()
        fused_observations: List[SingleAirtrafficObservation] = self._fuse_raw_observations()
        self._generate_active_tracks(fused_observations=fused_observations)
        active_tracks = self.redis_stream_helper.get_all_active_tracks_in_session(session_id=self.session_id)
        self._post_process_fused_data()
        return self._generate_track_messages_impl(active_tracks=active_tracks)

    def _generate_flight_speed_bearing(self, adjacent_points: List[LatLangAltPoint], delta_time_secs: float = 1.0) -> List[float]:
        """Calculate speed, bearing, and vertical speed between two points.

        Uses geodetic calculations on the WGS84 ellipsoid to compute accurate
        distance, bearing, and rates of change between consecutive observations.

        Args:
            adjacent_points: List containing exactly 2 LatLangAltPoint objects (first and second positions)
            delta_time_secs: Time difference in seconds between the two points (default: 1.0)

        Returns:
            List of [horizontal_speed_m/s, bearing_degrees, vertical_speed_m/s]
        """
        first_point = adjacent_points[0]
        second_point = adjacent_points[1]

        fwd_azimuth, _back_azimuth, adjacent_point_distance_mts = self.geod.inv(first_point.lng, first_point.lat, second_point.lng, second_point.lat)

        if fwd_azimuth < 0:
            fwd_azimuth = 360 + fwd_azimuth

        if delta_time_secs == 0:
            return [0.0, fwd_azimuth, 0.0]

        speed_mts_per_sec = adjacent_point_distance_mts / delta_time_secs
        speed_mts_per_sec = float("{:.2f}".format(speed_mts_per_sec))

        vertical_speed_mps = (second_point.alt - first_point.alt) / delta_time_secs
        vertical_speed_mps = float("{:.2f}".format(vertical_speed_mps))

        return [speed_mts_per_sec, fwd_azimuth, vertical_speed_mps]

    def _generate_track_messages_impl(self, active_tracks: List[ActiveTrack]) -> List[TrackMessage]:
        """Internal implementation for converting active tracks to track messages.

        This concrete method processes each active track to generate standardized
        ASTM F3411 compliant track messages. For each track, it:
        - Extracts the latest and previous observations
        - Calculates speed, bearing, and vertical speed using geodetic methods
        - Creates position and state objects with accuracy estimates
        - Assembles complete track messages with timestamps and identifiers

        Subclasses can override this method to customize track message generation,
        but the default implementation handles standard surveillance scenarios.

        Args:
            active_tracks: List of active tracks for the current session

        Returns:
            List of track messages ready for distribution to consumers
        """
        all_track_data = []
        for track in active_tracks:
            single_unique_aircraft_identifier = track.unique_aircraft_identifier
            # Get the latest observation for this active track
            fused_observations = [SingleAirtrafficObservation(**obs) for obs in track.observations]
            # For simplicity, we take the last observation as the latest
            latest_observation = fused_observations[-1]
            one_before_latest_observation = fused_observations[-2] if len(fused_observations) > 1 else latest_observation
            latest_observation_lat_lng_point = LatLangAltPoint(
                lat=latest_observation.lat_dd, lng=-latest_observation.lon_dd, alt=latest_observation.altitude_mm / 1000.0
            )
            one_before_latest_observation_lat_lng_point = LatLangAltPoint(
                lat=one_before_latest_observation.lat_dd,
                lng=-one_before_latest_observation.lon_dd,
                alt=one_before_latest_observation.altitude_mm / 1000.0,
            )
            # Calculate speed and bearing
            speed_mps, bearing_degrees, vertical_speed_mps = self._generate_flight_speed_bearing(
                adjacent_points=[
                    one_before_latest_observation_lat_lng_point,
                    latest_observation_lat_lng_point,
                ],
                delta_time_secs=(arrow.get(latest_observation.timestamp) - arrow.get(one_before_latest_observation.timestamp)).total_seconds(),
            )
            # Create AircraftPosition
            aircraft_position = AircraftPosition(
                lat=latest_observation.lat_dd,
                lng=-latest_observation.lon_dd,
                alt=latest_observation.altitude_mm,
                accuracy_h="SA1mps",
                accuracy_v="SA3mps",
                extrapolated=True,
                pressure_altitude=latest_observation.altitude_mm,
            )
            speed_accuracy = SpeedAccuracy("SA1mps")
            aircraft_state = AircraftState(
                position=aircraft_position,
                speed=speed_mps,
                track=bearing_degrees,
                vertical_speed=vertical_speed_mps,
                speed_accuracy=speed_accuracy,
            )
            track_data = TrackMessage(
                sdsdp_identifier=self.SDSP_IDENTIFIER,
                unique_aircraft_identifier=single_unique_aircraft_identifier,
                state=aircraft_state,
                timestamp=arrow.utcnow().isoformat(),
                source="FusedSource",
                track_state="Active",
            )
            all_track_data.append(track_data)

        return all_track_data

    def _pre_process_raw_data(self) -> bool:
        """Optional hook for pre-processing raw observations before fusion.

        This method is called before _fuse_raw_observations() and provides an
        extension point for subclasses to implement filtering, validation,
        or transformation of raw observations.

        The default implementation does nothing and returns True.

        Returns:
            True if pre-processing was successful, False otherwise
        """
        return True

    def _post_process_fused_data(self) -> bool:
        """Optional hook for post-processing after track generation.

        This method is called after _generate_active_tracks() but before final
        track message generation. It provides an extension point for subclasses
        to implement cleanup, validation, or additional processing.

        The default implementation does nothing and returns True.

        Returns:
            True if post-processing was successful, False otherwise
        """
        return True

    def _fuse_raw_observations(self) -> List[SingleAirtrafficObservation]:
        """Fuse raw observations from multiple sources.

        Subclasses should override this method to define their fusion algorithm.
        This method applies data fusion algorithms to combine observations from
        multiple sensors or sources. Implementations may use techniques such as:
        - Simple deduplication (return raw observations as-is)
        - Weighted averaging
        - Kalman filtering
        - Interacting Multiple Model (IMM) estimation
        - Multi-hypothesis tracking

        Returns:
            List of fused air traffic observations
        """
        raise NotImplementedError("Subclasses must implement _fuse_raw_observations()")

    def _generate_active_tracks(self, fused_observations: List[SingleAirtrafficObservation]) -> None:
        """Generate and maintain active tracks for the session.

        Subclasses should override this method to define their track management
        strategy. This method creates or updates active track objects for each
        aircraft being monitored in the session. Tracks are typically stored in
        Redis for persistence across task invocations.

        Implementations should:
        - Group fused observations by unique aircraft identifier (e.g., ICAO address)
        - Create new tracks for newly detected aircraft
        - Update existing tracks with new observations
        - Maintain track metadata (timestamps, observation history, etc.)
        - Handle track lifecycle (initiation, maintenance, termination)
        """
        raise NotImplementedError("Subclasses must implement _generate_active_tracks()")


class TrafficDataFuser(BaseTrafficDataFuser):
    """Default data fuser — generates track messages from raw observations."""

    def __init__(
        self,
        session_id: str,
        raw_observations: List[SingleAirtrafficObservation],
        track_store: TrackStore | None = None,
    ):
        self.raw_observations = raw_observations
        self.SDSP_IDENTIFIER = "SDSP123"
        self.session_id = session_id
        self.geod = Geod(ellps="WGS84")
        if track_store is not None:
            self.redis_stream_helper = track_store

    def _fuse_raw_observations(self) -> List[SingleAirtrafficObservation]:
        return self.raw_observations

    def _generate_active_tracks(self, fused_observations: List[SingleAirtrafficObservation]):
        track_store = self.redis_stream_helper
        active_tracks_in_session = {}
        for observation in fused_observations:
            icao_address = observation.icao_address
            if icao_address not in active_tracks_in_session:
                active_tracks_in_session[icao_address] = []
            active_tracks_in_session[icao_address].append(observation)
        for icao_address, observations in active_tracks_in_session.items():
            track_for_icao_address_exists = track_store.check_active_track_exists(session_id=self.session_id, unique_aircraft_identifier=icao_address)
            if track_for_icao_address_exists:
                existing_active_track = track_store.get_active_track(session_id=self.session_id, unique_aircraft_identifier=icao_address)
                existing_active_track.observations.extend([asdict(obs) for obs in observations])
                existing_active_track.last_updated_timestamp = arrow.utcnow().isoformat()
                track_store.update_active_track(session_id=self.session_id, active_track=existing_active_track)
            else:
                active_track = ActiveTrack(
                    session_id=self.session_id,
                    unique_aircraft_identifier=icao_address,
                    last_updated_timestamp=arrow.utcnow().isoformat(),
                    observations=[asdict(obs) for obs in observations],
                )
                track_store.add_active_track_to_session(session_id=self.session_id, active_track=active_track)
