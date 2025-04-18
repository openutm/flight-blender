from typing import Never

import arrow

from common.database_operations import FlightBlenderDatabaseReader
from rid_operations.data_definitions import RIDStreamErrorDetail

all_rid_errors = [
    RIDStreamErrorDetail(
        error_code="NET0040",
        error_description="Error in receiving position updates from the aircraft",
    )
]


class FlightTelemetryRIDEngine:
    def __init__(self, session_id: str):
        self.session_id = session_id

    def check_rid_stream_ok(self) -> tuple[bool, list[Never] | list[RIDStreamErrorDetail]]:
        my_database_reader = FlightBlenderDatabaseReader()
        # This method processes the stored RID stream for any errors
        # Get all telemetry observations for the session that are active since last observation
        now = arrow.now()
        four_seconds_before_now = arrow.now().shift(seconds=-4)
        # Get all the observations since last visit
        relevant_observations = my_database_reader.get_active_rid_observations_for_session_between_interval(
            session_id=self.session_id, start_time=four_seconds_before_now, end_time=now
        )

        if not relevant_observations:
            return (True, [])

        errors = []
        for i in range(1, len(relevant_observations)):
            prev_observation = relevant_observations[i - 1]
            current_observation = relevant_observations[i]
            time_diff = (current_observation.timestamp - prev_observation.timestamp).total_seconds()
            if time_diff != 1:
                errors.append(
                    RIDStreamErrorDetail(
                        error_code="NET0040",
                        error_description=f"NET0040: Timestamp difference error: {time_diff} seconds between observations {i - 1} and {i}",
                    )
                )

        if errors:
            return (False, errors)
