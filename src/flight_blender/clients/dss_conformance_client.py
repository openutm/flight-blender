"""DSS operation handlers for conformance — replaces Django management command dispatch."""

import asyncio
import uuid

from loguru import logger

from flight_blender.clients.dss_scd_client import OperationalIntentReferenceHelper, SCDOperations
from flight_blender.db.session import async_task_session
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository


async def operation_ended_clear_dss(flight_declaration_id: str, dry_run: int = 1) -> None:
    my_scd_dss_helper = SCDOperations()
    async with async_task_session() as db:
        fd_repo = SQLAlchemyFlightDeclarationRepository(db)
        flight_declaration = await fd_repo.get_by_id(uuid.UUID(flight_declaration_id))
        if not flight_declaration:
            logger.error(f"Flight Declaration {flight_declaration_id} not found")
            return
        flight_operational_intent_reference = await fd_repo.get_opint_reference_by_declaration_id(flight_declaration.id)

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


async def update_operational_intent_to_activated(flight_declaration_id: str, dry_run: int = 1) -> None:
    if dry_run:
        return
    my_operational_intents_helper = OperationalIntentReferenceHelper()
    async with async_task_session() as db:
        fd_repo = SQLAlchemyFlightDeclarationRepository(db)
        flight_declaration = await fd_repo.get_by_id(uuid.UUID(flight_declaration_id))
        if not flight_declaration:
            logger.error(f"Flight Declaration {flight_declaration_id} not found")
            return
        flight_operational_intent_reference = await fd_repo.get_opint_reference_by_declaration_id(uuid.UUID(flight_declaration_id))

    if not flight_operational_intent_reference:
        return
    await my_operational_intents_helper.parse_stored_operational_intent_details(operation_id=flight_declaration_id)
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
        if asyncio.iscoroutinefunction(handler):
            asyncio.run(handler(**kwargs))
        else:
            handler(**kwargs)
    else:
        logger.warning(f"Unknown command: {name}")
