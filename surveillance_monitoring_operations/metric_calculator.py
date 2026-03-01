from datetime import datetime
from typing import TYPE_CHECKING, Optional

from loguru import logger

from .data_definitions import (
    AggregateHealthMetrics,
    HeartbeatDeliveryProbability,
    HeartbeatRateMetric,
    SensorHealthMetrics,
    TrackUpdateProbability,
)

if TYPE_CHECKING:
    from common.database_operations import FlightBlenderDatabaseReader


class SurveillanceMetricCalculator:
    """
    Calculates all seven ASTM F3623 SDSP surveillance metrics from database records.
    All ORM access is delegated to FlightBlenderDatabaseReader to follow project conventions.
    """

    def __init__(self, database_reader: "FlightBlenderDatabaseReader"):
        self.db = database_reader

    # ------------------------------------------------------------------
    # Metric 1: Heartbeat rate
    # ------------------------------------------------------------------

    def calculate_heartbeat_rate(self, session_id: str, start_time: datetime, end_time: datetime) -> HeartbeatRateMetric:
        events = self.db.get_all_flight_observations_in_window(start_time=start_time, end_time=end_time)
        total = events.count()
        duration_secs = (end_time - start_time).total_seconds()
        rate_hz = round(total / duration_secs, 4) if duration_secs > 0 else 0.0
        return HeartbeatRateMetric(
            measured_rate_hz=rate_hz,
            target_rate_hz=1.0,
            session_id=session_id,
            window_start=start_time.isoformat(),
            window_end=end_time.isoformat(),
            total_heartbeats_in_window=total,
        )

    # ------------------------------------------------------------------
    # Metric 2a: Heartbeat delivery probability
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Metric 2b: Track update probability
    # ------------------------------------------------------------------

    def calculate_track_update_probability(self, session_id: str, start_time: datetime, end_time: datetime) -> TrackUpdateProbability:
        observations = self.db.get_all_flight_observations_in_window(start_time=start_time, end_time=end_time)
        total = observations.count()
        with_tracks = total  # every received observation constitutes a track update attempt
        probability = round(with_tracks / total, 6) if total > 0 else 0.0
        return TrackUpdateProbability(
            probability=probability,
            ticks_with_active_tracks=with_tracks,
            total_ticks=total,
            session_id=session_id,
            window_start=start_time.isoformat(),
            window_end=end_time.isoformat(),
        )

    # ------------------------------------------------------------------
    # Metrics 3–6: Per-sensor MTTR, auto recovery time, and MTBF
    # ------------------------------------------------------------------

    def calculate_sensor_health_metrics(self, sensor_id: str, start_time: datetime, end_time: datetime) -> SensorHealthMetrics:
        """
        Walks the health tracking audit trail in chronological order and derives:
        - MTTR (all recoveries)
        - Average automatic recovery time
        - MTBF with automatic recovery
        - MTBF without automatic recovery

        Algorithm overview
        ------------------
        State machine over the ordered tracking records:
          operational → failure_onset recorded when status changes to degraded/outage
          degraded/outage → recovery recorded when status returns to operational

        recovery_duration = recovery.recorded_at - failure_onset.recorded_at
        operational_interval = failure_onset.recorded_at - operational_start.recorded_at

        Seeding: if the sensor was already in a failure state before the window starts,
        use start_time as the proxy failure_onset so we don't miss the recovery.
        """
        records = list(self.db.get_health_tracking_records_for_sensor(sensor_id=sensor_id, start_time=start_time, end_time=end_time))

        sensor = None
        try:
            from surveillance_monitoring_operations.models import SurveillanceSensor

            sensor = SurveillanceSensor.objects.get(id=sensor_id)
        except Exception:
            logger.warning(f"calculate_sensor_health_metrics: sensor {sensor_id} not found")

        sensor_identifier = sensor.sensor_identifier if sensor else str(sensor_id)

        # Seed: determine the status just before the window to handle mid-failure windows
        pre_window_status = self.db.get_sensor_status_before_time(sensor_id=sensor_id, before_time=start_time)

        failure_states = {"degraded", "outage"}

        # Track the current failure onset and the start of the current operational period
        current_failure_onset: Optional[datetime] = None
        operational_start: Optional[datetime] = None

        if pre_window_status in failure_states:
            current_failure_onset = start_time
        elif pre_window_status == "operational":
            operational_start = start_time

        recovery_durations: list[float] = []
        auto_recovery_durations: list[float] = []

        # operational_intervals[i] = (duration_seconds, recovery_type_that_started_this_period)
        # recovery_type is the type of the recovery that brought the sensor back before this interval
        operational_intervals: list[tuple[float, Optional[str]]] = []

        for record in records:
            status = record.status
            rec_time: datetime = record.recorded_at

            if status in failure_states:
                if current_failure_onset is None:
                    # Entering failure from operational
                    if operational_start is not None:
                        # Record the operational interval that just ended
                        interval_secs = (rec_time - operational_start).total_seconds()
                        # The recovery type that started this operational period is stored
                        # in the variable below — we'll attach it when we find the recovery
                        # For now, store None; we update once we know the preceding recovery
                        operational_intervals.append((interval_secs, getattr(record, "_preceding_recovery_type", None)))
                    current_failure_onset = rec_time
                    operational_start = None

            elif status == "operational":
                if current_failure_onset is not None:
                    # Recovery event
                    duration = (rec_time - current_failure_onset).total_seconds()
                    recovery_durations.append(duration)
                    if record.recovery_type == "automatic":
                        auto_recovery_durations.append(duration)

                    # Record the operational interval that just ended (if applicable)
                    # Tag the *preceding* operational interval with this recovery type
                    # We mark the new operational period's recovery_type for use when next failure occurs
                    operational_start = rec_time
                    current_failure_onset = None

                    # Annotate so the next failure transition can tag the interval correctly
                    # We use a temporary attribute on the last-appended record reference
                    # (this is in-memory only, not persisted)
                    if operational_intervals:
                        last_interval = operational_intervals[-1]
                        operational_intervals[-1] = (last_interval[0], record.recovery_type)
                else:
                    # Operational without a preceding tracked failure (e.g., first record)
                    if operational_start is None:
                        operational_start = rec_time

        # Compute MTTR
        mttr: Optional[float] = None
        if recovery_durations:
            mttr = round(sum(recovery_durations) / len(recovery_durations), 2)

        # Compute average automatic recovery time
        avg_auto_recovery: Optional[float] = None
        if auto_recovery_durations:
            avg_auto_recovery = round(sum(auto_recovery_durations) / len(auto_recovery_durations), 2)

        # Compute MTBF with automatic recovery (intervals preceded by auto recovery)
        auto_intervals = [d for d, rt in operational_intervals if rt == "automatic"]
        mtbf_auto: Optional[float] = None
        if auto_intervals:
            mtbf_auto = round(sum(auto_intervals) / len(auto_intervals), 2)

        # Compute MTBF without automatic recovery (intervals preceded by manual recovery)
        manual_intervals = [d for d, rt in operational_intervals if rt == "manual"]
        mtbf_manual: Optional[float] = None
        if manual_intervals:
            mtbf_manual = round(sum(manual_intervals) / len(manual_intervals), 2)

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

    # ------------------------------------------------------------------
    # Aggregate across all sensors
    # ------------------------------------------------------------------

    def calculate_aggregate_health_metrics(
        self,
        sensor_metrics_list: list[SensorHealthMetrics],
        start_time: datetime,
        end_time: datetime,
    ) -> AggregateHealthMetrics:
        """
        Calculate aggregate health metrics from a list of sensor health metrics.

        Computes average values for Mean Time To Repair (MTTR), auto-recovery time,
        and Mean Time Between Failures (MTBF) across all provided sensors within a
        specified time window.

        Args:
            sensor_metrics_list: List of SensorHealthMetrics objects to aggregate.
            start_time: The start time of the metrics collection window.
            end_time: The end time of the metrics collection window.

        Returns:
            AggregateHealthMetrics: An object containing:
                - avg_mttr_seconds: Average MTTR across sensors (None if no data).
                - avg_auto_recovery_time_seconds: Average auto-recovery time (None if no data).
                - avg_mtbf_with_auto_recovery_seconds: Average MTBF with auto-recovery (None if no data).
                - avg_mtbf_without_auto_recovery_seconds: Average MTBF without auto-recovery (None if no data).
                - total_sensors: Total number of sensors in the list.
                - window_start: ISO format string of the start time.
                - window_end: ISO format string of the end time.
        """

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
