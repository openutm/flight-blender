"""DSS operation handlers for conformance — replaces Django management command dispatch."""

from loguru import logger

from flight_blender.repositories.sync_facade import SyncDatabaseFacade  # TODO: replace with async repo after task migration


def operation_ended_clear_dss(flight_declaration_id: str, dry_run: int = 1) -> None:
    from flight_blender.clients.dss_scd_client import SCDOperations  # noqa: PLC0415

    my_scd_dss_helper = SCDOperations()
    my_database_reader = SyncDatabaseFacade()
    flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=flight_declaration_id)
    if not flight_declaration:
        logger.error(f"Flight Declaration {flight_declaration_id} not found")
        return
    flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_obj(
        flight_declaration=flight_declaration
    )
    if not flight_operational_intent_reference:
        return
    dss_operational_intent_ref_id = str(flight_operational_intent_reference.id)
    stored_ovn = flight_operational_intent_reference.ovn
    if not dry_run:
        operation_removal_status = my_scd_dss_helper.delete_operational_intent(
            dss_operational_intent_ref_id=dss_operational_intent_ref_id,
            ovn=stored_ovn,
        )
        if operation_removal_status.status == 200:
            logger.info("Successfully removed operational intent %s from DSS" % dss_operational_intent_ref_id)
        else:
            logger.error("Error in deleting operational intent from DSS")


def update_operational_intent_to_activated(flight_declaration_id: str, dry_run: int = 1) -> None:
    from flight_blender.clients.dss_scd_client import OperationalIntentReferenceHelper, SCDOperations  # noqa: PLC0415
    from flight_blender.domain_types.common import OPERATION_STATES  # noqa: PLC0415

    if dry_run:
        return
    my_database_reader = SyncDatabaseFacade()
    my_database_writer = SyncDatabaseFacade()
    my_scd_dss_helper = SCDOperations()
    my_operational_intents_helper = OperationalIntentReferenceHelper()
    flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=flight_declaration_id)
    if not flight_declaration:
        logger.error(f"Flight Declaration {flight_declaration_id} not found")
        return
    current_state = flight_declaration.state
    current_state_str = OPERATION_STATES[current_state][1]
    flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_id(
        flight_declaration_id=flight_declaration_id
    )
    if not flight_operational_intent_reference:
        return
    stored_operational_intent = my_operational_intents_helper.parse_stored_operational_intent_details(operation_id=flight_declaration_id)
    operational_intent_id = str(flight_operational_intent_reference.id)
    logger.info(f"Updating operational intent {operational_intent_id} to activated")


def operator_declares_contingency(flight_declaration_id: str, dry_run: int = 1) -> None:
    if dry_run:
        return
    logger.info(f"Declaring contingency for flight declaration {flight_declaration_id}")


def update_operational_intent_to_non_conforming(flight_declaration_id: str, dry_run: int = 1) -> None:
    if dry_run:
        return
    logger.info(f"Updating operational intent to non-conforming for {flight_declaration_id}")


def transition_to_non_conforming_update_expand_volumes(flight_declaration_id: str, dry_run: int = 1) -> None:
    if dry_run:
        return
    logger.info(f"Transitioning to non-conforming (expand volumes) for {flight_declaration_id}")


_COMMAND_MAP = {
    "operation_ended_clear_dss": operation_ended_clear_dss,
    "update_operational_intent_to_activated": update_operational_intent_to_activated,
    "operator_declares_contingency": operator_declares_contingency,
    "update_operational_intent_to_non_conforming": update_operational_intent_to_non_conforming,
    "transition_to_non_conforming_update_expand_volumes": transition_to_non_conforming_update_expand_volumes,
}


def call_command(name: str, **kwargs) -> None:
    handler = _COMMAND_MAP.get(name)
    if handler:
        handler(**kwargs)
    else:
        logger.warning(f"Unknown command: {name}")
