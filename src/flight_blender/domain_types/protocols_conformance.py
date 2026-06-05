from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AsyncConformanceRepository(Protocol):
    """Async read-only repository used by ``ConformanceOperations`` (FastAPI).

    Concrete implementation: ``SQLAlchemyConformanceRepository`` in
    ``flight_blender.infrastructure.database.repositories.sa_conformance``.
    """

    async def get_conformance_records_for_duration(self, start_time: datetime, end_time: datetime) -> Any | None: ...


@runtime_checkable
class SyncConformanceDB(Protocol):
    """Sync DB facade used by conformance helpers (Celery tasks, sync DSS dispatch).

    Concrete implementation: ``SyncDatabaseFacade`` in
    ``flight_blender.repositories.sync_facade``.
    """

    def get_flight_declaration_by_id(self, flight_declaration_id: str) -> Any | None: ...
    def get_flight_operational_intent_reference_by_flight_declaration_id(self, flight_declaration_id: str) -> Any | None: ...
    def get_active_geofences(self) -> list[Any]: ...
    def get_conformance_monitoring_task(self, flight_declaration: Any) -> Any | None: ...
    def create_conformance_monitoring_periodic_task(self, flight_declaration: Any) -> bool: ...
    def remove_conformance_monitoring_periodic_task(self, conformance_monitoring_task: Any = None) -> None: ...
    def write_flight_conformance_record(
        self,
        flight_declaration: Any,
        conformance_non_conformance_state: int,
        event_type: str,
        description: str,
        geofence_breach: bool,
        geofence: Any,
        resolved: bool,
    ) -> bool: ...
    def add_flight_declaration_state_history_entry(
        self,
        flight_declaration_id: str,
        original_state: int,
        new_state: int,
        notes: str = "",
    ) -> bool: ...
    def update_flight_operation_state(self, flight_declaration_id: str, state: int) -> bool: ...


@runtime_checkable
class NotificationDispatcher(Protocol):
    """Fire-and-forget notification + DSS submission dispatcher.

    Concrete implementation: Celery task adapters in
    ``flight_blender.infrastructure.celery.tasks.flight_declarations``.
    """

    def send_operational_update_message(self, flight_declaration_id: str, message_text: str, level: str) -> None: ...
    def submit_flight_declaration_to_dss_async(self, flight_declaration_id: str) -> None: ...


@runtime_checkable
class DSSConformanceDispatcher(Protocol):
    """Synchronous dispatch to DSS conformance command handlers.

    Concrete implementation: ``call_command`` in
    ``flight_blender.infrastructure.dss.conformance``.
    """

    def call_command(self, name: str, **kwargs: Any) -> None: ...


@runtime_checkable
class SyncSurveillanceDB(Protocol):
    """Sync DB facade used by ``SurveillanceMetricCalculator`` (Celery-side metrics).

    Concrete implementation: ``SyncDatabaseFacade``.
    """

    def get_heartbeat_events_for_session(self, session_id: str, start_time: Any, end_time: Any) -> Any: ...
    def get_all_flight_observations_in_window(self, start_time: Any, end_time: Any) -> Any: ...
    def get_health_tracking_records_for_sensor(self, sensor_id: str, start_time: Any, end_time: Any) -> Any: ...
    def get_sensor_status_before_time(self, sensor_id: str, before_time: Any) -> Any | None: ...
    def get_surveillance_sensor_by_id(self, sensor_id: Any) -> Any | None: ...
