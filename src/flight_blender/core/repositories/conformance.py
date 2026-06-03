from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ConformanceRepository(Protocol):
    def get_conformance_records_for_duration(self, start_time: datetime, end_time: datetime) -> Any | None: ...
    def get_conformance_record_by_flight_declaration(self, flight_declaration: Any) -> Any | None: ...
    def get_conformance_monitoring_task(self, flight_declaration: Any) -> Any | None: ...
    def write_flight_conformance_record(
        self,
        flight_declaration: Any,
        conformance_non_conformance_state: int,
        description: str,
        event_type: str,
        geofence_breach: bool,
        resolved: bool,
        geofence: Any | None,
    ) -> Any | None: ...
    def create_conformance_monitoring_periodic_task(self, flight_declaration: Any) -> bool: ...
    def remove_conformance_monitoring_periodic_task(self, conformance_monitoring_task: Any) -> None: ...
