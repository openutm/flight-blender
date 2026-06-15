from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import arrow
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.clients.dss_scd_client import OperationalIntentReferenceHelper, SCDOperations
from flight_blender.config import settings
from flight_blender.db.session import async_task_session
from flight_blender.domain_types.conformance import ConformanceRecord, ConformanceSummary
from flight_blender.domain_types.scd import Altitude, Circle, LatLngPoint, Polygon, Radius, Time, Volume3D, Volume4D
from flight_blender.repositories.conformance_repo import SQLAlchemyConformanceRepository
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository

if TYPE_CHECKING:
    from flight_blender.tasks.flight_declarations_task import CelerySCDNotifier

class StatusCode:
    @classmethod
    def list(cls):
        return list(cls.dict().values())

    @classmethod
    def text(cls, key):
        return cls.options.get(key, None)

    @classmethod
    def items(cls):
        return cls.options.items()

    @classmethod
    def keys(cls):
        return cls.options.keys()

    @classmethod
    def labels(cls):
        return cls.options.values()

    @classmethod
    def names(cls):
        keys = cls.keys()
        status_names = {}
        for d in dir(cls):
            if d.startswith("_"):
                continue
            value = getattr(cls, d, None)
            if value is None or callable(value) or not isinstance(value, int):
                continue
            if value not in keys:
                continue
            status_names[d] = value
        return status_names

    @classmethod
    def dict(cls):
        values = {}
        for name, value in cls.names().items():
            entry = {"key": value, "name": name, "label": cls.label(value)}
            if hasattr(cls, "colors"):
                if color := cls.colors.get(value, None):
                    entry["color"] = color
            values[name] = entry
        return values

    @classmethod
    def label(cls, value):
        return cls.options.get(value, value)

    @classmethod
    def value(cls, label):
        label = label if isinstance(label, int) else label.lower()
        for k in cls.options.keys():
            if cls.options[k].lower() == label:
                return k
        raise ValueError("Label not found")

    @classmethod
    def state_code(cls, key):
        names = cls.names()
        for k, v in names.items():
            if v == key:
                return k
        raise ValueError("Key not found")


class ConformanceChecksList(StatusCode):
    C2 = 2
    C3 = 3
    C4 = 4
    C5 = 5
    C6 = 6
    C7a = 7
    C7b = 8
    C8 = 9
    C9a = 10
    C9b = 11
    C10 = 12
    C11 = 13

    options = {
        C2: "Flight Auth not granted",
        C3: "Telemetry Auth mismatch",
        C4: "Operation state invalid",
        C5: "Operation not activated",
        C6: "Telemetry time incorrect",
        C7a: "Flight out of bounds",
        C7b: "Flight altitude out of bounds",
        C8: "Geofence breached",
        C9a: "Telemetry not received",
        C9b: "Telemetry not received within last 15 secs",
        C10: "State not in accepted, non-conforming, activated",
        C11: "No Flight Authorization",
    }


class ConformanceOperations:
    def __init__(self, repo: SQLAlchemyConformanceRepository) -> None:
        self._repo = repo

    @staticmethod
    def parse_date_range(start_date: str | None, end_date: str | None) -> tuple[tuple[datetime, datetime] | None, str | None]:
        if not start_date or not end_date:
            return None, "start_date and end_date are required"
        try:
            start = arrow.get(start_date).datetime
            end = arrow.get(end_date).datetime
        except arrow.parser.ParserError:
            return None, "Invalid date format. Use ISO 8601 format."
        if start >= end:
            return None, "start_date must be before end_date"
        return (start, end), None

    async def get_records(self, start_time: datetime, end_time: datetime) -> list[ConformanceRecord]:
        orm_records = await self._repo.get_conformance_records_for_duration(start_time=start_time, end_time=end_time)
        return [
            ConformanceRecord(
                id=r.id,
                flight_declaration_id=r.flight_declaration_id,
                conformance_state=r.conformance_state,
                timestamp=r.timestamp,
                description=r.description,
                event_type=r.event_type,
                geofence_breach=r.geofence_breach,
                geofence_id=r.geofence_id,
                resolved=r.resolved,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in orm_records
        ]

    async def get_summary(self, start_time: datetime, end_time: datetime, start_date: str, end_date: str) -> ConformanceSummary:
        records = await self._repo.get_conformance_records_for_duration(start_time=start_time, end_time=end_time)
        total = len(records)
        conforming = sum(1 for r in records if r.conformance_state == 1)
        return ConformanceSummary(
            total_records=total,
            conforming_records=conforming,
            non_conforming_records=total - conforming,
            conformance_rate_percentage=(conforming / total * 100) if total else 0,
            start_date=start_date,
            end_date=end_date,
        )


def cast_to_volume4d(volume) -> Volume4D:
    outline_polygon = None
    outline_circle = None
    if "outline_polygon" in volume["volume"].keys():
        all_vertices = volume["volume"]["outline_polygon"]["vertices"]
        polygon_verticies = []
        for vertex in all_vertices:
            v = LatLngPoint(lat=vertex["lat"], lng=vertex["lng"])
            polygon_verticies.append(v)
        polygon_verticies.pop()
        outline_polygon = Polygon(vertices=polygon_verticies)

    if "outline_circle" in volume["volume"].keys():
        if volume["volume"]["outline_circle"]:
            circle_center = LatLngPoint(
                lat=volume["volume"]["outline_circle"]["center"]["lat"],
                lng=volume["volume"]["outline_circle"]["center"]["lng"],
            )
            circle_radius = Radius(
                value=volume["volume"]["outline_circle"]["radius"]["value"],
                units=volume["volume"]["outline_circle"]["radius"]["units"],
            )
            outline_circle = Circle(center=circle_center, radius=circle_radius)
        else:
            outline_circle = None

    altitude_lower = Altitude(
        value=volume["volume"]["altitude_lower"]["value"],
        reference=volume["volume"]["altitude_lower"]["reference"],
        units=volume["volume"]["altitude_lower"]["units"],
    )
    altitude_upper = Altitude(
        value=volume["volume"]["altitude_upper"]["value"],
        reference=volume["volume"]["altitude_upper"]["reference"],
        units=volume["volume"]["altitude_upper"]["units"],
    )
    volume_3d = Volume3D(
        outline_circle=outline_circle,
        outline_polygon=outline_polygon,
        altitude_lower=altitude_lower,
        altitude_upper=altitude_upper,
    )
    time_start = Time(format=volume["time_start"]["format"], value=volume["time_start"]["value"])
    time_end = Time(format=volume["time_end"]["format"], value=volume["time_end"]["value"])
    return Volume4D(volume=volume_3d, time_start=time_start, time_end=time_end)


# ── State machine (from conformance/operation_state_helper.py) ────────────────


class State:
    """State transitions per ASTM F3548-21."""

    def __init__(self):
        logger.info("Processing current state:%s" % str(self))

    def get_value(self):
        return self._value

    def on_event(self, event):
        pass

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        return self.__class__.__name__


class ProcessingNotSubmittedToDss(State):
    def on_event(self, event):
        if event == "dss_accepts":
            return AcceptedState()
        elif event == "operator_withdraws":
            return WithdrawnState()
        elif event == "operator_cancels":
            return CancelledState()
        return self


class AcceptedState(State):
    def on_event(self, event):
        if event == "operator_activates":
            return ActivatedState()
        elif event == "operator_confirms_ended":
            return EndedState()
        elif event == "ua_departs_early_late_outside_op_intent":
            return NonconformingState()
        return self


class ActivatedState(State):
    def on_event(self, event):
        if event == "operator_confirms_ended":
            return EndedState()
        elif event == "ua_exits_coordinated_op_intent":
            return NonconformingState()
        elif event == "operator_initiates_contingent":
            return ContingentState()
        return self


class EndedState(State):
    def on_event(self, event):
        return self


class NonconformingState(State):
    def on_event(self, event):
        if event == "operator_return_to_coordinated_op_intent":
            return ActivatedState()
        elif event == "operator_confirms_ended":
            return EndedState()
        elif event in ["timeout", "operator_confirms_contingent"]:
            return ContingentState()
        return self


class ContingentState(State):
    def on_event(self, event):
        if event == "operator_confirms_ended":
            return EndedState()
        return self


class WithdrawnState(State):
    def on_event(self, event):
        return self


class CancelledState(State):
    def on_event(self, event):
        return self


class RejectedState(State):
    def on_event(self, event):
        return self


state_mapping = {
    0: ProcessingNotSubmittedToDss,
    1: AcceptedState,
    2: ActivatedState,
    3: NonconformingState,
    4: ContingentState,
    5: EndedState,
    6: WithdrawnState,
    7: CancelledState,
    8: RejectedState,
}


def match_state(status: int):
    return state_mapping.get(status, lambda: False)()


def get_status(state: State):
    reverse_mapping = {v: k for k, v in state_mapping.items()}
    return reverse_mapping.get(type(state), False)


class FlightOperationStateMachine:
    def __init__(self, state: int = 1):
        s = match_state(state)
        self.state = s

    def on_event(self, event):
        self.state = self.state.on_event(event)


# ── Conformance helper (from conformance/conformance_checks_handler.py) ───────


class FlightOperationConformanceHelper:
    def __init__(self, flight_declaration_id: uuid.UUID, db: AsyncSession):
        self.flight_declaration_id = flight_declaration_id
        self.db = db
        self.fd_repo = SQLAlchemyFlightDeclarationRepository(db)
        self.flight_declaration = None
        self.ENABLE_CONFORMANCE_MONITORING = settings.ENABLE_CONFORMANCE_MONITORING
        self.USSP_NETWORK_ENABLED = settings.USSP_NETWORK_ENABLED

    # ── Orchestration functions

    async def operation_ended_clear_dss(self, dry_run: int = 1) -> None:
        my_scd_dss_helper = SCDOperations()
        flight_declaration = await self.fd_repo.get_by_id(self.flight_declaration_id)
        if not flight_declaration:
            logger.error(f"Flight Declaration {self.flight_declaration_id} not found")
            return
        flight_operational_intent_reference = await self.fd_repo.get_opint_reference_by_declaration_id(flight_declaration.id)

        if not flight_operational_intent_reference:
            return
        dss_operational_intent_ref_id = str(flight_operational_intent_reference.id)
        stored_ovn = flight_operational_intent_reference.ovn
        if not dry_run:
            operation_removal_status = await my_scd_dss_helper.delete_operational_intent(
                dss_operational_intent_ref_id=dss_operational_intent_ref_id,
                ovn=stored_ovn,
            )
            if operation_removal_status.status == 200:
                logger.info("Successfully removed operational intent %s from DSS" % dss_operational_intent_ref_id)
            else:
                logger.error("Error in deleting operational intent from DSS")

    async def update_operational_intent_to_activated(self, dry_run: int = 1) -> None:
        if dry_run:
            return
        my_operational_intents_helper = OperationalIntentReferenceHelper()
        flight_declaration = await self.fd_repo.get_by_id(self.flight_declaration_id)
        if not flight_declaration:
            logger.error(f"Flight Declaration {self.flight_declaration_id} not found")
            return
        flight_operational_intent_reference = await self.fd_repo.get_opint_reference_by_declaration_id(self.flight_declaration_id)

        if not flight_operational_intent_reference:
            return
        await my_operational_intents_helper.parse_stored_operational_intent_details(operation_id=str(self.flight_declaration_id))
        operational_intent_id = str(flight_operational_intent_reference.id)
        logger.info(f"Updating operational intent {operational_intent_id} to activated")

    def operator_declares_contingency(self, dry_run: int = 1) -> None:
        if dry_run:
            return
        logger.info(f"Declaring contingency for flight declaration {self.flight_declaration_id}")

    def update_operational_intent_to_non_conforming(self, dry_run: int = 1) -> None:
        if dry_run:
            return
        logger.info(f"Updating operational intent to non-conforming for {self.flight_declaration_id}")

    def transition_to_non_conforming_update_expand_volumes(self, dry_run: int = 1) -> None:
        if dry_run:
            return
        logger.info(f"Transitioning to non-conforming (expand volumes) for {self.flight_declaration_id}")

    # ── State transition handlers ──────────────────────────────────────────

    @staticmethod
    def verify_operation_state_transition(original_state: int, new_state: int, event: str) -> bool:
        my_operation_state_machine = FlightOperationStateMachine(state=original_state)
        logger.info("Current Operation State %s" % my_operation_state_machine.state)
        my_operation_state_machine.on_event(event)
        changed_state = get_status(my_operation_state_machine.state)
        if changed_state == new_state:
            return True
        logger.info("State change verification failed")
        return False

    async def manage_operation_state_transition(self, original_state: int, new_state: int, event: str):
        self.flight_declaration = await self.fd_repo.get_by_id(self.flight_declaration_id)
        state_transition_handlers = {
            5: self._handle_operation_ended,
            4: self._handle_contingent_state,
            3: self._handle_non_conforming_state,
            2: self._handle_activated_state,
            6: self._handle_withdrawn_state,
            7: self._handle_cancelled_state,
        }
        handler = state_transition_handlers.get(new_state)
        if handler:
            await handler(original_state, event)
        else:
            logger.info(f"No handler defined for new state: {new_state}")

    async def _handle_operation_ended(self, original_state: int, event: str):
        if event != "operator_confirms_ended":
            logger.info("Operation has ended, but no confirmation received")
            return
        if self.USSP_NETWORK_ENABLED:
            await self.operation_ended_clear_dss(dry_run=0)
        if self.ENABLE_CONFORMANCE_MONITORING:
            logger.info("Removing conformance monitoring task as operation has ended")
            await self._remove_conformance_monitoring_task()

    async def _remove_conformance_monitoring_task(self):
        conformance_monitoring_job = None
        logger.info(f"Removing conformance monitoring job for {self.flight_declaration_id}")
        if conformance_monitoring_job:
            logger.info(f"Removed conformance monitoring job for {self.flight_declaration_id}")

    async def _handle_contingent_state(self, original_state: int, event: str):
        valid_events_for_state_2 = ["operator_initiates_contingent", "flight_blender_confirms_contingent"]
        valid_events_for_state_3 = ["timeout", "operator_confirms_contingent"]
        if self.USSP_NETWORK_ENABLED:
            if original_state in [2, 3] and event in (valid_events_for_state_2 if original_state == 2 else valid_events_for_state_3):
                self.operator_declares_contingency(dry_run=0)
        else:
            logger.info("USSP Network is not enabled, skipping contingency state handling with DSS")

    async def _handle_non_conforming_state(self, original_state: int, event: str):
        non_conforming_handlers = {
            "ua_exits_coordinated_op_intent": self.transition_to_non_conforming_update_expand_volumes,
            "ua_departs_early_late": self.update_operational_intent_to_non_conforming,
        }
        handler = non_conforming_handlers.get(event)
        if handler and original_state in [1, 2]:
            if self.USSP_NETWORK_ENABLED:
                handler(dry_run=0)
            if self.ENABLE_CONFORMANCE_MONITORING:
                logger.info("Removing conformance monitoring task due to non-conformance")
            else:
                logger.info("USSP Network is not enabled, skipping non-conforming state handling with DSS")

    async def _handle_withdrawn_state(self, original_state: int, event: str):
        if event != "operator_withdraws":
            logger.info("Withdrawal event mismatch")
            return
        if self.USSP_NETWORK_ENABLED:
            await self.operation_ended_clear_dss(dry_run=0)
        if self.ENABLE_CONFORMANCE_MONITORING:
            await self._remove_conformance_monitoring_task()

    async def _handle_cancelled_state(self, original_state: int, event: str):
        if event != "operator_cancels":
            logger.info("Cancellation event mismatch")
            return
        if self.USSP_NETWORK_ENABLED:
            await self.operation_ended_clear_dss(dry_run=0)
        if self.ENABLE_CONFORMANCE_MONITORING:
            await self._remove_conformance_monitoring_task()

    async def _handle_activated_state(self, original_state: int, event: str):
        if original_state != 1 or event != "operator_activates":
            logger.info("Invalid state or event for activation")
            return
        if self.USSP_NETWORK_ENABLED:
            await self.update_operational_intent_to_activated(dry_run=0)
        if self.ENABLE_CONFORMANCE_MONITORING:
            await self._create_conformance_monitoring_task()

    async def _create_conformance_monitoring_task(self):
        logger.info(f"Conformance monitoring scheduling is handled by TaskSchedulerService for {self.flight_declaration_id}")


# ── Notifications (from conformance/operator_conformance_notifications.py) ────


class OperationConformanceNotification:
    def __init__(self, flight_declaration_id: str, notifier: CelerySCDNotifier):
        self.amqp_connection_url = settings.AMQP_URL
        self.flight_declaration_id = flight_declaration_id
        self.notifier: CelerySCDNotifier = notifier

    def send_conformance_status_notification(self, message: str, level: str):
        if self.amqp_connection_url:
            self.notifier.send_operational_update_message(
                flight_declaration_id=self.flight_declaration_id,
                message_text=message,
                level=level,
            )
        else:
            logger.error(f"Conformance Notification for {self.flight_declaration_id}")
            logger.error(message)


# ── Signal definitions + receivers (from conformance/custom_signals.py) ───────

from flight_blender.services.signals import Signal, receiver  # noqa: E402

telemetry_non_conformance_signal = Signal()
flight_operational_intent_reference_non_conformance_signal = Signal()


class ConformanceDependencies:
    """Bundle of infrastructure dependencies that signal receivers need at runtime.

    Signal receivers are global and called with a fixed signature; they cannot
    take constructor-injected dependencies. The receivers therefore resolve
    dependencies from a module-level provider that the application wires once
    at startup. Tests can replace the provider with a stub.
    """

    def __init__(self, db: AsyncSession | None, notifier: CelerySCDNotifier) -> None:
        self.db: AsyncSession | None = db
        self.notifier: CelerySCDNotifier = notifier


_DEFAULT_DEPS_HOLDER: list[Optional[ConformanceDependencies]] = [None]


def set_conformance_deps(deps: ConformanceDependencies) -> None:
    """Register the conformance deps provider used by signal receivers."""
    _DEFAULT_DEPS_HOLDER[0] = deps


def _get_conformance_deps() -> ConformanceDependencies:
    deps = _DEFAULT_DEPS_HOLDER[0]
    if deps is None:
        raise RuntimeError("Conformance dependencies not configured — call set_conformance_deps() at app startup")
    return deps


def _run_signal_db_work(coro) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
    else:
        loop.create_task(coro)


async def _process_telemetry_conformance_db_work(
    deps: ConformanceDependencies,
    flight_declaration_id: str,
    non_conformance_state: int,
    detailed_non_conformance_message: str,
    non_conformance_state_code: str,
    event,
    new_state: int | None,
) -> None:
    async with async_task_session() as db:
        fd_repo = SQLAlchemyFlightDeclarationRepository(db)
        conformance_repo = SQLAlchemyConformanceRepository(db)
        fd = await fd_repo.get_by_id(uuid.UUID(flight_declaration_id))
        if fd is None:
            logger.error(f"Flight declaration {flight_declaration_id} not found for telemetry conformance handling")
            return
        await conformance_repo.create_conformance_record(
            declaration_id=fd.id,
            state=non_conformance_state,
            event_type="deviation",
            description=detailed_non_conformance_message,
            geofence_breach=False,
            resolved=False,
        )
        if event and new_state is not None:
            original_state = fd.state
            await fd_repo.add_state_history_entry(
                flight_declaration_id=fd.id,
                original_state=original_state,
                new_state=new_state,
                notes="State changed by telemetry conformance checks because of telemetry non-conformance: %s" % non_conformance_state_code,
            )
            await fd_repo.update(fd.id, state=new_state)
            helper = FlightOperationConformanceHelper(flight_declaration_id=fd.id, db=db)
            await helper.manage_operation_state_transition(original_state=original_state, new_state=new_state, event=event)


async def _process_opint_reference_conformance_db_work(
    deps: ConformanceDependencies,
    flight_declaration_id: str,
    non_conformance_state: int,
    non_conformance_state_code: str,
    event,
    new_state: int | None,
) -> None:
    async with async_task_session() as db:
        fd_repo = SQLAlchemyFlightDeclarationRepository(db)
        conformance_repo = SQLAlchemyConformanceRepository(db)
        fd = await fd_repo.get_by_id(uuid.UUID(flight_declaration_id))
        if fd is None:
            logger.error(f"Flight declaration {flight_declaration_id} not found for operational intent conformance handling")
            return
        await conformance_repo.create_conformance_record(
            declaration_id=fd.id,
            state=non_conformance_state,
            event_type="deviation",
            description="Flight Operational Intent Reference non-conformance detected: %s" % non_conformance_state_code,
            geofence_breach=False,
            resolved=False,
        )
        if event and new_state is not None:
            original_state = fd.state
            await fd_repo.add_state_history_entry(
                flight_declaration_id=fd.id,
                original_state=original_state,
                new_state=new_state,
                notes="State changed by flight authorization checks: %s" % non_conformance_state_code,
            )
            await fd_repo.update(fd.id, state=new_state)
            helper = FlightOperationConformanceHelper(flight_declaration_id=fd.id, db=db)
            await helper.manage_operation_state_transition(original_state=original_state, new_state=new_state, event=event)


@receiver(telemetry_non_conformance_signal)
def process_telemetry_conformance_message(sender, **kwargs):
    deps = _get_conformance_deps()
    non_conformance_state = int(kwargs["non_conformance_state"])
    flight_declaration_id = kwargs["flight_declaration_id"]
    my_operation_notification = OperationConformanceNotification(flight_declaration_id=flight_declaration_id, notifier=deps.notifier)

    logger.debug(f"{sender} -- {kwargs['non_conformance_state']}")
    event = False

    non_conformance_state_code = ConformanceChecksList.state_code(non_conformance_state)
    detailed_non_conformance_message = ""

    if non_conformance_state_code == "C3":
        invalid_aircraft_id_msg = "The aircraft ID provided in telemetry for operation {flight_declaration_id}, does not match the declared / authorized aircraft, you must stop operation. C3 Check failed.".format(
            flight_declaration_id=flight_declaration_id
        )
        detailed_non_conformance_message = invalid_aircraft_id_msg
        logger.error(f"{invalid_aircraft_id_msg}")
        my_operation_notification.send_conformance_status_notification(message=invalid_aircraft_id_msg, level="error")
        new_state = 4
        event = "flight_blender_confirms_contingent"

    elif non_conformance_state_code in ["C4", "C5"]:
        flight_state_not_correct_msg = "The state for operation {flight_declaration_id}, is not one of 'Accepted' or 'Activated', your authorization is invalid. C4+C5 Check failed.".format(
            flight_declaration_id=flight_declaration_id
        )
        detailed_non_conformance_message = flight_state_not_correct_msg
        logger.error(flight_state_not_correct_msg)
        my_operation_notification.send_conformance_status_notification(message=flight_state_not_correct_msg, level="error")
        event = "flight_blender_confirms_contingent"
        new_state = 3

    elif non_conformance_state_code == "C6":
        telemetry_timestamp_not_within_op_start_end_msg = "The telemetry timestamp provided for operation {flight_declaration_id}, is not within the start / end time for an operation. C6 Check failed.".format(
            flight_declaration_id=flight_declaration_id
        )
        detailed_non_conformance_message = telemetry_timestamp_not_within_op_start_end_msg
        logger.error(telemetry_timestamp_not_within_op_start_end_msg)
        my_operation_notification.send_conformance_status_notification(message=telemetry_timestamp_not_within_op_start_end_msg, level="error")
        new_state = 3
        event = "ua_departs_early_late"

    elif non_conformance_state_code == "C7a":
        aircraft_altitude_nonconformant_msg = (
            "The telemetry timestamp provided for operation {flight_declaration_id}, is not within the altitude bounds C7a check failed.".format(
                flight_declaration_id=flight_declaration_id
            )
        )
        detailed_non_conformance_message = aircraft_altitude_nonconformant_msg
        logger.error(aircraft_altitude_nonconformant_msg)
        my_operation_notification.send_conformance_status_notification(message=aircraft_altitude_nonconformant_msg, level="error")
        new_state = 3
        event = "ua_exits_coordinated_op_intent"

    elif non_conformance_state_code == "C7b":
        aircraft_bounds_nonconformant_msg = "The telemetry location provided for operation {flight_declaration_id}, is not within the declared bounds for an operation. C7b check failed.".format(
            flight_declaration_id=flight_declaration_id
        )
        detailed_non_conformance_message = aircraft_bounds_nonconformant_msg
        logger.error(aircraft_bounds_nonconformant_msg)
        my_operation_notification.send_conformance_status_notification(message=aircraft_bounds_nonconformant_msg, level="error")
        new_state = 3
        event = "ua_exits_coordinated_op_intent"

    _run_signal_db_work(
        _process_telemetry_conformance_db_work(
            deps=deps,
            flight_declaration_id=flight_declaration_id,
            non_conformance_state=non_conformance_state,
            detailed_non_conformance_message=detailed_non_conformance_message,
            non_conformance_state_code=non_conformance_state_code,
            event=event,
            new_state=new_state if event else None,
        )
    )


@receiver(flight_operational_intent_reference_non_conformance_signal)
def process_flight_operational_intent_reference_non_conformance_message(sender, **kwargs):
    deps = _get_conformance_deps()
    non_conformance_state = kwargs["non_conformance_state"]
    flight_declaration_id = kwargs["flight_declaration_id"]
    my_operation_notification = OperationConformanceNotification(flight_declaration_id=flight_declaration_id, notifier=deps.notifier)

    non_conformance_state_code = ConformanceChecksList.state_code(non_conformance_state)
    event = None
    if non_conformance_state_code == "C9a":
        telemetry_not_being_received_error_msg = (
            "The telemetry for operation {flight_declaration_id}, has not been received in the past 15 seconds. Check C9a Failed".format(
                flight_declaration_id=flight_declaration_id
            )
        )
        logger.error(telemetry_not_being_received_error_msg)
        my_operation_notification.send_conformance_status_notification(message=telemetry_not_being_received_error_msg, level="error")
        event = "timeout"
        new_state = 4
    elif non_conformance_state_code == "C9b":
        telemetry_never_received_error_msg = "The telemetry for operation {flight_declaration_id}, has never been received. Check C9b Failed".format(
            flight_declaration_id=flight_declaration_id
        )
        logger.error(telemetry_never_received_error_msg)
        my_operation_notification.send_conformance_status_notification(message=telemetry_never_received_error_msg, level="error")
        event = "flight_blender_confirms_contingent"
        new_state = 4
    elif non_conformance_state_code == "C10":
        flight_operational_intent_reference_expired = (
            "The authorization for operation {flight_declaration_id}, has been expired. You must stop operation ".format(
                flight_declaration_id=flight_declaration_id
            )
        )
        logger.error(flight_operational_intent_reference_expired)
        my_operation_notification.send_conformance_status_notification(message=flight_operational_intent_reference_expired, level="error")
        event = "flight_blender_confirms_contingent"
        new_state = 4
    elif non_conformance_state_code == "C11":
        authorization_not_granted_message = "There is no flight authorization for operation with ID {flight_declaration_id}. Check C11 Failed".format(
            flight_declaration_id=flight_declaration_id
        )
        logger.error(authorization_not_granted_message)
        new_state = 4
        my_operation_notification.send_conformance_status_notification(message=authorization_not_granted_message, level="error")
        event = "flight_blender_confirms_contingent"

    _run_signal_db_work(
        _process_opint_reference_conformance_db_work(
            deps=deps,
            flight_declaration_id=flight_declaration_id,
            non_conformance_state=non_conformance_state,
            non_conformance_state_code=non_conformance_state_code,
            event=event,
            new_state=new_state if event else None,
        )
    )


# ── Conformance engine (from conformance/utils.py) ────────────────────────────

import json as _json  # noqa: E402

from shapely.geometry import Point as _Point  # noqa: E402
from shapely.geometry import Polygon as _Plgn  # noqa: E402

from flight_blender.domain_types.conformance import PolygonAltitude  # noqa: E402


def is_time_between(begin_time, end_time, check_time=None):
    check_time = check_time or arrow.now()
    if begin_time < end_time:
        return check_time >= begin_time and check_time <= end_time
    else:
        return check_time >= begin_time or check_time <= end_time


class FlightBlenderConformanceEngine:
    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    async def _get_flight_declaration(self, flight_declaration_id: uuid.UUID):
        return await SQLAlchemyFlightDeclarationRepository(self.db).get_by_id(flight_declaration_id)

    async def _get_opint_reference(self, flight_declaration_id: uuid.UUID):
        return await SQLAlchemyFlightDeclarationRepository(self.db).get_opint_reference_by_declaration_id(flight_declaration_id)

    async def _get_active_geofences(self):
        return await SQLAlchemyConformanceRepository(self.db).get_active_geofences()

    async def is_operation_conformant_via_telemetry(
        self,
        flight_declaration_id: uuid.UUID,
        aircraft_id: str,
        telemetry_location: LatLngPoint,
        altitude_m_wgs_84: float,
    ) -> int:
        now = arrow.now()
        USSP_NETWORK_ENABLED = settings.USSP_NETWORK_ENABLED

        flight_declaration = await self._get_flight_declaration(flight_declaration_id=flight_declaration_id)

        if USSP_NETWORK_ENABLED:
            flight_operational_intent_reference = await self._get_opint_reference(flight_declaration_id=flight_declaration_id)
        else:
            flight_operational_intent_reference = True

        if not flight_operational_intent_reference or not flight_declaration:
            logger.error(
                f"Error in getting flight authorization and declaration for {flight_declaration_id}, cannot continue with conformance checks, C2 Check failed."
            )
            logger.error("Conformance check failed, flight authorization not found, raising code {ConformanceChecksList.C2}")
            return ConformanceChecksList.C2

        operational_intent_details_raw = flight_declaration.operational_intent
        operational_intent_details = _json.loads(operational_intent_details_raw)

        operation_start_time = arrow.get(flight_declaration.start_datetime)
        operation_end_time = arrow.get(flight_declaration.end_datetime)

        if flight_declaration.aircraft_id != aircraft_id:
            logger.error(
                f"Aircraft ID mismatch for {flight_declaration_id}, C3 Check failed: Flight Declaration {flight_declaration.aircraft_id} != Telemetry {aircraft_id}"
            )
            logger.error(f"Raising error code {ConformanceChecksList.C3}")
            return ConformanceChecksList.C3

        if flight_declaration.state in [0, 5, 6, 7, 8]:
            logger.error(f"Flight state is invalid for {flight_declaration_id}, C4 Check failed.")
            logger.error(f"Raising error code {ConformanceChecksList.C4}")
            return ConformanceChecksList.C4

        if flight_declaration.state not in [2, 3, 4]:
            logger.error(f"Flight state is not activated for {flight_declaration_id}, C5 Check failed.")
            logger.error(f"Raising Error code {ConformanceChecksList.C5}")
            return ConformanceChecksList.C5

        if not is_time_between(
            begin_time=operation_start_time,
            end_time=operation_end_time,
            check_time=now,
        ):
            logger.error(f"Telemetry is not within operation time for {flight_declaration_id}, C6 Check failed.")
            logger.error(f"Raising Error code {ConformanceChecksList.C6}")
            return ConformanceChecksList.C6

        all_volumes = operational_intent_details["volumes"]

        lng = float(telemetry_location.lng)
        lat = float(telemetry_location.lat)
        rid_location = _Point(lng, lat)
        logger.info(f"Checking C7 Conformance for location {rid_location.wkt}...")
        all_polygon_altitudes: list[PolygonAltitude] = []

        for v in all_volumes:
            v4d = cast_to_volume4d(v)
            altitude_lower = v4d.volume.altitude_lower.value
            altitude_upper = v4d.volume.altitude_upper.value
            outline_polygon = v4d.volume.outline_polygon
            point_list = [_Point(vertex.lng, vertex.lat) for vertex in outline_polygon.vertices]
            outline_polygon = _Plgn([[p.x, p.y] for p in point_list])

            pa = PolygonAltitude(
                polygon=outline_polygon,
                altitude_upper=altitude_upper,
                altitude_lower=altitude_lower,
            )
            all_polygon_altitudes.append(pa)

        rid_obs_within_all_volumes = []
        rid_obs_within_altitudes = []

        for p in all_polygon_altitudes:
            is_within = rid_location.within(p.polygon)
            logger.debug(f"Altitude Check: {altitude_m_wgs_84} between {p.altitude_lower} and {p.altitude_upper}")
            altitude_conformant = p.altitude_lower <= altitude_m_wgs_84 <= p.altitude_upper
            rid_obs_within_all_volumes.append(is_within)
            rid_obs_within_altitudes.append(altitude_conformant)

        logger.debug(f"Polygon conformity results: {rid_obs_within_all_volumes}")
        logger.debug(f"Altitude conformity results: {rid_obs_within_altitudes}")
        aircraft_bounds_conformant = any(rid_obs_within_all_volumes)
        aircraft_altitude_conformant = any(rid_obs_within_altitudes)

        if not aircraft_altitude_conformant:
            logger.error(f"Aircraft altitude is not conformant for {flight_declaration_id}, C7b Check failed.")
            logger.error(f"Raising Error code {ConformanceChecksList.C7b}")
            return ConformanceChecksList.C7b

        if not aircraft_bounds_conformant:
            logger.error(f"Aircraft bounds are not conformant for {flight_declaration_id}, C7a Check failed.")
            logger.error(f"Raising Error code {ConformanceChecksList.C7a}")
            return ConformanceChecksList.C7a

        geofences = await self._get_active_geofences()
        for geofence in geofences:
            geofence_geojson = _json.loads(geofence.raw_geo_fence)
            features = geofence_geojson.get("features", [])
            for feature in features:
                geometry = feature.get("geometry", {})
                geofence_type = geometry.get("type")
                coordinates = geometry.get("coordinates", [])

                if geofence_type == "Polygon":
                    if self._is_within_geofence(rid_location, coordinates[0]):
                        logger.error(f"Aircraft is breaching an active GeoFence for {flight_declaration_id}, C8 Check failed.")
                        logger.error(f"Raising Error code {ConformanceChecksList.C8}")
                        return ConformanceChecksList.C8

                elif geofence_type == "MultiPolygon":
                    for polygon_coords in coordinates:
                        if self._is_within_geofence(rid_location, polygon_coords[0]):
                            logger.error(f"Aircraft is breaching an active GeoFence for {flight_declaration_id}, C8 Check failed.")
                            logger.error(f"Raising Error code {ConformanceChecksList.C8}")
                            return ConformanceChecksList.C8

        return 100

    def _is_within_geofence(self, rid_location: _Point, coordinates: list) -> bool:
        geofence_polygon = _Plgn(coordinates)
        return rid_location.within(geofence_polygon)

    async def check_flight_operational_intent_reference_conformance(self, flight_declaration_id: uuid.UUID) -> int:
        now = arrow.now()
        flight_declaration = await self._get_flight_declaration(flight_declaration_id=flight_declaration_id)
        flight_operational_intent_reference_exists = await self._get_opint_reference(flight_declaration_id=flight_declaration_id)
        ussp_network_enabled = settings.USSP_NETWORK_ENABLED
        if ussp_network_enabled and not flight_operational_intent_reference_exists:
            logger.info(f"Flight authorization / operational intent reference does not exist for {flight_declaration_id}, C11 Check failed.")
            logger.info(f"Raising Error code {ConformanceChecksList.C11}")
            return ConformanceChecksList.C11
        if not flight_declaration:
            logger.info(f"Flight declaration does not exist for {flight_declaration_id}, C10 Check failed.")
            return ConformanceChecksList.C10
        latest_telemetry_datetime = flight_declaration.latest_telemetry_datetime
        fifteen_seconds_before_now = now.shift(seconds=-15)
        fifteen_seconds_after_now = now.shift(seconds=15)
        allowed_states = [2, 3, 4]
        if flight_declaration.state not in allowed_states:
            logger.info(f"Flight operation state is ended for {flight_declaration_id}, C10 Check failed.")
            logger.info(f"Raising Error code {ConformanceChecksList.C10}")
            return ConformanceChecksList.C10

        if latest_telemetry_datetime:
            if not fifteen_seconds_before_now <= latest_telemetry_datetime <= fifteen_seconds_after_now:
                logger.info(f"No telemetry data being sent for {flight_declaration_id}, C9 Check failed.")
                logger.info(f"Raising Error code {ConformanceChecksList.C9b}")
                return ConformanceChecksList.C9b
        else:
            logger.info(f"Flight operation state is contingent for {flight_declaration_id}, C9 Check failed.")
            logger.info(f"Raising Error code {ConformanceChecksList.C9a}")
            return ConformanceChecksList.C9a

        return 1
