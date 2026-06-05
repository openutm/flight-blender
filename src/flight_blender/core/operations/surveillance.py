import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import arrow
from loguru import logger

from flight_blender.core.entities.surveillance import (
    FLIGHT_OBSERVATION_TRAFFIC_SOURCE,
    AggregateHealthMetrics,
    HealthMessage,
    HeartbeatDeliveryProbability,
    HeartbeatRateMetric,
    SensorHealthMetrics,
    SurveillanceMetrics,
    SurveillanceSensorDetail,
    SurveillanceSensorFailureNotificationDetail,
    SurveillanceStatus,
    TrackUpdateProbability,
)
from flight_blender.core.repositories.flight_feed import FlightFeedRepository
from flight_blender.core.repositories.surveillance import SurveillanceRepository, SurveillanceTaskScheduler


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
    ) -> dict:
        now = arrow.now()
        one_week_ago = now.shift(weeks=-1)
        if start_date:
            start_date = start_date.replace(" ", "+")
        if end_date:
            end_date = end_date.replace(" ", "+")
        start_dt = arrow.get(start_date).datetime if start_date else one_week_ago.datetime
        end_dt = arrow.get(end_date).datetime if end_date else now.datetime

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
        return asdict(metric_response)

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
    ) -> list[dict]:
        now = arrow.now()
        one_week_ago = now.shift(weeks=-1)
        if start_date:
            start_date = start_date.replace(" ", "+")
        if end_date:
            end_date = end_date.replace(" ", "+")
        start_dt = arrow.get(start_date).datetime if start_date else one_week_ago.datetime
        end_dt = arrow.get(end_date).datetime if end_date else now.datetime

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
        return result


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
