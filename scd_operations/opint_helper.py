import json
import logging
import uuid

import arrow
import tldextract
from dacite import from_dict

from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from flight_declaration_operations.utils import OperationalIntentsConverter
from scd_operations.dss_scd_helper import ConstraintsWriter, SCDOperations

from .data_definitions import FlightDeclarationOperationalIntentStorageDetails
from .scd_data_definitions import (
    CompositeOperationalIntentPayload,
    NotifyPeerUSSPostPayload,
    OperationalIntentSubmissionStatus,
    OperationalIntentUSSDetails,
    OtherError,
)

logger = logging.getLogger("django")

INDEX_NAME = "opint_idx"


class DSSOperationalIntentsCreator:
    """This class provides helper function to submit a operational intent in the DSS based on a operation ID"""

    def __init__(self, flight_declaration_id: str):
        self.flight_declaration_id = flight_declaration_id

        self.my_scd_dss_helper = SCDOperations()
        self.my_operational_intent_reference_helper = OperationalIntentsConverter()
        self.my_database_reader = FlightBlenderDatabaseReader()
        self.my_database_writer = FlightBlenderDatabaseWriter()

    def validate_flight_declaration_start_end_time(self) -> bool:
        flight_declaration = self.my_database_reader.get_flight_declaration_by_id(flight_declaration_id=self.flight_declaration_id)
        # check that flight declaration start and end time is in the next two hours
        now = arrow.now()
        two_hours_from_now = now.shift(hours=2)

        op_start_time = arrow.get(flight_declaration.start_datetime)
        op_end_time = arrow.get(flight_declaration.end_datetime)

        start_time_ok = op_start_time <= two_hours_from_now and op_start_time >= now
        end_time_ok = op_end_time <= two_hours_from_now and op_end_time >= now

        start_end_time_oks = [start_time_ok, end_time_ok]
        if False in start_end_time_oks:
            return False
        else:
            return True

    def submit_flight_declaration_to_dss(self) -> OperationalIntentSubmissionStatus:
        """This method submits a flight declaration as a operational intent to the DSS"""
        new_entity_id = str(uuid.uuid4())

        flight_declaration = self.my_database_reader.get_flight_declaration_by_id(flight_declaration_id=self.flight_declaration_id)
        if not flight_declaration:
            logger.error("Flight Declaration with ID %s not found in the database" % self.flight_declaration_id)
            return OperationalIntentSubmissionStatus(
                status="declaration_not_found",
                status_code=404,
                message="Flight Declaration with ID %s not found in the database" % self.flight_declaration_id,
                dss_response=OtherError(notes="Flight Declaration with ID %s not found in the database" % self.flight_declaration_id),
                operational_intent_id=new_entity_id,
            )

        current_state = flight_declaration.state
        operational_intent = json.loads(flight_declaration.operational_intent)
        operational_intent_data = from_dict(
            data_class=FlightDeclarationOperationalIntentStorageDetails,
            data=operational_intent,
        )

        auth_token = self.my_scd_dss_helper.get_auth_token()

        if "error" in auth_token:
            logger.error("Error in retrieving auth_token, check if the auth server is running properly, error details displayed above")
            notes = "Error in getting a token from the Auth server, flight not submitted to the DSS, check if the auth server is running properly"
            logger.error(auth_token["error"])
            op_int_submission_result = OperationalIntentSubmissionStatus(
                status="auth_server_error",
                status_code=500,
                message="Error in getting a token from the Auth server",
                dss_response=OtherError(notes=notes),
                operational_intent_id=new_entity_id,
            )

            self.my_database_writer.update_flight_operation_state(flight_declaration_id=self.flight_declaration_id, state=8)
            flight_declaration.add_state_history_entry(
                new_state=8,
                original_state=current_state,
                notes=notes,
            )
        else:
            op_int_submission_result = self.my_scd_dss_helper.create_and_submit_operational_intent_reference(
                state=operational_intent_data.state,
                volumes=operational_intent_data.volumes,
                off_nominal_volumes=operational_intent_data.off_nominal_volumes,
                priority=operational_intent_data.priority,
            )

            if op_int_submission_result.status_code == 201:
                # Operational intent successfully created in the DSS
                # Write the flight_operatinal_intent_reference to the database
                logger.info("Successfully created operational intent in the DSS, updating database..")

                created_flight_operational_intent_reference = self.my_database_writer.create_flight_operational_intent_reference(
                    flight_declaration=flight_declaration,
                    created_operational_intent_reference=op_int_submission_result.dss_response.operational_intent_reference,
                )
                operational_intent_details_payload = OperationalIntentUSSDetails(
                    volumes=operational_intent_data.volumes,
                    off_nominal_volumes=operational_intent_data.off_nominal_volumes,
                    priority=operational_intent_data.priority,
                )
                created_flight_operational_intent_detail = (
                    self.my_database_writer.create_flight_operational_intent_details_with_submitted_operational_intent(
                        flight_declaration=flight_declaration, operational_intent_details_payload=operational_intent_details_payload
                    )
                )
                # Create a composite operational intent reference
                if created_flight_operational_intent_detail and created_flight_operational_intent_reference:
                    generated_composite_operational_intent_data = (
                        self.my_operational_intent_reference_helper.generate_bounds_altitude_time_for_volumes(
                            operational_intent_details_payload=operational_intent_details_payload,
                            flight_declaration_id=self.flight_declaration_id,
                        )
                    )
                    composite_operational_intent_data = CompositeOperationalIntentPayload(
                        bounds=generated_composite_operational_intent_data.bounds,
                        start_datetime=generated_composite_operational_intent_data.start_datetime,
                        end_datetime=generated_composite_operational_intent_data.end_datetime,
                        alt_max=generated_composite_operational_intent_data.alt_max,
                        alt_min=generated_composite_operational_intent_data.alt_min,
                        operational_intent_reference_id=str(created_flight_operational_intent_reference.id),
                        operational_intent_details_id=str(created_flight_operational_intent_detail.id),
                    )

                    self.my_database_writer.create_or_update_composite_operational_intent(
                        flight_declaration=flight_declaration, composite_operational_intent_payload=composite_operational_intent_data
                    )
                # Write the constraints
                if op_int_submission_result.constraints:
                    my_constraints_writer = ConstraintsWriter()
                    my_constraints_writer.write_nearby_constraints(
                        flight_declaration=flight_declaration,
                        constraints=op_int_submission_result.constraints,
                    )

                # Update operation state
                logger.info("Updating state from Processing to Accepted...")
                self.my_database_writer.update_flight_operation_state(flight_declaration_id=self.flight_declaration_id, state=1)
                flight_declaration.add_state_history_entry(
                    new_state=1,
                    original_state=current_state,
                    notes="Operational Intent successfully submitted to DSS and is Accepted",
                )
            elif op_int_submission_result.status_code in [400, 409, 401, 403, 412, 413, 429]:
                if op_int_submission_result.status_code == 400:
                    notes = "Error during submission of operational intent, the DSS rejected because one or more parameters was missing"
                elif op_int_submission_result.status_code == 409:
                    notes = "Error during submission of operational intent, the DSS rejected it with because the latest airspace keys was not present"
                elif op_int_submission_result.status_code == 401:
                    notes = "There was a error in submitting the operational intent to the DSS, the token was invalid"
                elif op_int_submission_result.status_code == 403:
                    notes = "There was a error in submitting the operational intent to the DSS, the appropriate scope was not present"
                elif op_int_submission_result.status_code == 413:
                    notes = "There was a error in submitting the operational intent to the DSS, the operational intent was too large"
                elif op_int_submission_result.status_code == 429:
                    notes = "There was a error in submitting the operational intent to the DSS, too many requests were submitted to the DSS"
                # Update operation state, the DSS rejected our data
                logger.info(
                    "There was a error in submitting the operational intent to the DSS, the DSS rejected our submission with a {status_code} response code".format(
                        status_code=op_int_submission_result.status_code
                    )
                )
                self.my_database_writer.update_flight_operation_state(flight_declaration_id=self.flight_declaration_id, state=8)
                flight_declaration.add_state_history_entry(
                    new_state=8,
                    original_state=current_state,
                    notes=notes,
                )
            elif op_int_submission_result.status_code == 500 and op_int_submission_result.message == "conflict_with_flight":
                # Update operation state, DSS responded with a error
                logger.info("Flight is not deconflicted, updating state from Processing to Rejected ..")
                self.my_database_writer.update_flight_operation_state(flight_declaration_id=self.flight_declaration_id, state=8)
                flight_declaration.add_state_history_entry(
                    new_state=8,
                    original_state=current_state,
                    notes="Flight was not deconflicted correctly",
                )

        return op_int_submission_result

    def notify_peer_uss(self, uss_base_url: str, notification_payload: NotifyPeerUSSPostPayload):
        """This method submits a flight declaration as a operational intent to the DSS"""
        # Get the Flight Declaration object

        my_scd_dss_helper = SCDOperations()

        try:
            ext = tldextract.extract(uss_base_url)
        except Exception:
            uss_audience = "localhost"
        else:
            if ext.domain in [
                "localhost",
                "internal",
            ]:  # for host.docker.internal type calls
                uss_audience = "localhost"
            else:
                uss_audience = ".".join(ext[:3])  # get the subdomain, domain and suffix and create a audience and get credentials

        if ext.subdomain != "dummy" and ext.domain != "uss":
            # Do not notify dummy.uss
            my_scd_dss_helper.notify_peer_uss_of_created_updated_operational_intent(
                uss_base_url=uss_base_url,
                notification_payload=notification_payload,
                audience=uss_audience,
            )
