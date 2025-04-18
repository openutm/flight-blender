import json
import logging
from itertools import cycle

import arrow

from auth_helper.common import get_redis
from common.data_definitions import FLIGHT_OPINT_KEY, OPINT_INDEX_BASEPATH
from common.database_operations import FlightBlenderDatabaseWriter
from rid_operations import rtree_helper

from . import dss_scd_helper
from .scd_data_definitions import ClearAreaResponse, ClearAreaResponseOutcome

logger = logging.getLogger("django")


class UAVSerialNumberValidator:
    """A class to validate the Serial number of a UAV per the ANSI/CTA-2063-A standard"""

    def code_contains_O_or_I(self, manufacturer_code):
        m_code = [c for c in manufacturer_code]
        if "O" in m_code or "I" in m_code:
            return True
        else:
            return False

    def __init__(self, serial_number):
        self.serial_number = serial_number
        self.serial_number_length_code_points = {
            "1": 1,
            "2": 2,
            "3": 3,
            "4": 4,
            "5": 5,
            "6": 6,
            "7": 7,
            "8": 8,
            "9": 9,
            "A": 10,
            "B": 11,
            "C": 12,
            "D": 13,
            "E": 14,
            "F": 15,
        }
        self.serial_number_code_points = [
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "A",
            "B",
            "C",
            "D",
            "E",
            "F",
            "G",
            "H",
            "J",
            "K",
            "L",
            "M",
            "N",
            "P",
            "Q",
            "R",
            "S",
            "T",
            "U",
            "V",
            "W",
            "X",
            "Y",
            "Z",
        ]

    def is_valid(self):
        manufacturer_code = self.serial_number[:4]

        # Check if the string is four characters
        if not len(manufacturer_code):
            return False
        if self.code_contains_O_or_I(manufacturer_code=manufacturer_code):
            return False

        character_length_code = self.serial_number[4:5]
        # Length code can only be 1-9, A-F
        if character_length_code not in self.serial_number_length_code_points.keys():
            return False
        # Get the rest of the string
        manufacturers_code = self.serial_number[5:]
        if len(manufacturers_code) != self.serial_number_length_code_points[character_length_code]:
            return False

        return True


class OperatorRegistrationNumberValidator:
    """A class to validate a Operator Registration provided number per the EN4709-02 standard"""

    def __init__(self, operator_registration_number):
        self.operator_registration_number = operator_registration_number
        self.registration_number_code_points = [
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "a",
            "b",
            "c",
            "d",
            "e",
            "f",
            "g",
            "h",
            "i",
            "j",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "q",
            "r",
            "s",
            "t",
            "u",
            "v",
            "w",
            "x",
            "y",
            "z",
        ]

    def gen_checksum(self, raw_id):
        assert raw_id.isalnum()
        assert len(raw_id) == 15
        d = {v: k for k, v in enumerate(self.registration_number_code_points)}
        numeric_base_id = list(map(d.__getitem__, list(raw_id)))
        # Multiplication factors for each digit depending on its position
        mult_factors = cycle([2, 1])

        def partial_sum(number, mult_factor):
            """Calculate partial sum ofr a single digit."""
            quotient, remainder = divmod(number * mult_factor, 36)
            return quotient + remainder

        final_sum = sum(partial_sum(int(character), mult_factor) for character, mult_factor in zip(numeric_base_id, mult_factors))

        # Calculate control number based on partial sums
        control_number = -final_sum % 36
        return self.registration_number_code_points[control_number]

    def is_valid(self):
        # Get the prefix
        oprn, secure_characters = self.operator_registration_number.split("-")

        if len(oprn) != 16:
            return False
        if len(secure_characters) != 3:
            return False
        base_id = oprn[3:-1]
        if not base_id.isalnum():
            return False
        # country_code = self.operator_registration_number[:3]
        checksum = self.operator_registration_number[-5]  # checksum
        # op_registration_suffix = self.operator_registration_number[3:]
        random_three_alnum_string = self.operator_registration_number[-3:]

        computed_checksum = self.gen_checksum(base_id + random_three_alnum_string)

        if computed_checksum != checksum:
            return False

        return True


class DSSAreaClearHandler:
    def __init__(self, request_id):
        self.request_id = request_id

    def clear_area_request(self, extent_raw) -> ClearAreaResponse:
        r = get_redis()
        my_database_writer = FlightBlenderDatabaseWriter()
        # Create a list of Volume4D objects
        my_operational_intent_parser = dss_scd_helper.OperationalIntentReferenceHelper()
        volume4D = my_operational_intent_parser.parse_volume_to_volume4D(volume=extent_raw)
        my_geo_json_converter = dss_scd_helper.VolumesConverter()

        my_geo_json_converter.convert_volumes_to_geojson(volumes=[volume4D])
        view_rect_bounds = my_geo_json_converter.get_bounds()
        my_rtree_helper = rtree_helper.OperationalIntentsIndexFactory(index_name=OPINT_INDEX_BASEPATH)
        my_rtree_helper.generate_active_flights_operational_intents_index()
        op_ints_exist = my_rtree_helper.check_op_ints_exist()
        all_existing_op_ints_in_area = []
        if op_ints_exist:
            all_existing_op_ints_in_area = my_rtree_helper.check_box_intersection(view_box=view_rect_bounds)

        all_deletion_requests_status = []
        if all_existing_op_ints_in_area:
            for flight_details in all_existing_op_ints_in_area:
                if flight_details:
                    deletion_success = False
                    operation_id = flight_details["flight_id"]
                    op_int_details_key = FLIGHT_OPINT_KEY + operation_id
                    if r.exists(op_int_details_key):
                        op_int_detail_raw = r.get(op_int_details_key)
                        my_scd_dss_helper = dss_scd_helper.SCDOperations()
                        # op_int_detail_raw = op_int_details.decode()

                        op_int_detail = json.loads(op_int_detail_raw)
                        ovn = op_int_detail["success_response"]["operational_intent_reference"]["ovn"]
                        opint_id = op_int_detail["success_response"]["operational_intent_reference"]["id"]
                        ovn_opint = {"ovn_id": ovn, "opint_id": opint_id}
                        logger.info("Deleting operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))

                        deletion_request = my_scd_dss_helper.delete_operational_intent(dss_operational_intent_ref_id=opint_id, ovn=ovn)
                        if deletion_request.status == 200:
                            logger.info("Success in deleting operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))
                            deletion_success = True

                            r.delete(op_int_details_key)
                            my_database_writer.delete_flight_declaration(flight_declaration_id=operation_id)

                        else:
                            logger.info("Failed to delete operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))
                            logger.error(deletion_request.dss_response)
                            deletion_success = False

                        all_deletion_requests_status.append(deletion_success)

            message = (
                "Some operational intents in the area failed to clear"
                if not all(all_deletion_requests_status)
                else "All operational intents in the area cleared successfully"
            )

            clear_area_status = ClearAreaResponseOutcome(
                success=all(all_deletion_requests_status),
                message=message,
                timestamp=arrow.now().isoformat(),
            )

        else:
            clear_area_status = ClearAreaResponseOutcome(
                success=True,
                message="All operational intents in the area cleared successfully",
                timestamp=arrow.now().isoformat(),
            )
        my_rtree_helper.clear_rtree_index()
        clear_area_response = ClearAreaResponse(outcome=clear_area_status)

        return clear_area_response
