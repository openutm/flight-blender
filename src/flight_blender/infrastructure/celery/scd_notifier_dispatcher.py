from flight_blender.infrastructure.celery.tasks.flight_declarations import send_operational_update_message, submit_flight_declaration_to_dss_async


class CelerySCDNotifier:
    def send_operational_update_message(self, flight_declaration_id: str, message_text: str, level: str) -> None:
        send_operational_update_message.delay(
            flight_declaration_id=flight_declaration_id,
            message_text=message_text,
            level=level,
        )

    def submit_flight_declaration_to_dss_async(self, flight_declaration_id: str) -> None:
        submit_flight_declaration_to_dss_async.delay(flight_declaration_id=flight_declaration_id)
