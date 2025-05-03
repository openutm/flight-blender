import json
import logging
import uuid
from dataclasses import asdict
from os import environ as env

import requests
import urllib3
from dacite import from_dict

from auth_helper import dss_auth_helper
from common.auth_token_audience_helper import generate_audience_from_base_url
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from geo_fence_operations.data_definitions import GeofencePayload
from scd_operations.dss_scd_helper import VolumesConverter
from scd_operations.scd_data_definitions import Time, Volume4D

from .data_definitions import Constraint, ConstraintDetails, ConstraintReference, QueryConstraintsPayload

load_dotenv(find_dotenv())

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)

logger = logging.getLogger("django")


class ConstraintsHelper:
    def __init__(self) -> None:
        self.my_database_reader = FlightBlenderDatabaseReader()
        self.my_database_writer = FlightBlenderDatabaseWriter()

    # def parse_stored_constraint_details(self, geozone_id: str) -> ConstraintDetails | None:
    #     pass

    # def parse_and_load_stored_constraint_reference(self, geozone_id: str) -> ConstraintReference | None:
    #     pass

    def write_nearby_constraints(self, constraints: list[Constraint]):
        # This method writes the constraint reference and constraint details to the database
        my_volumes_converter = VolumesConverter()
        for constraint in constraints:
            constraint_reference = constraint.reference
            constraint_details = constraint.details
            # Check if the constraint reference already exists in the database
            constraint_reference_exists = self.my_database_reader.check_constraint_reference_id_exists(
                constraint_reference_id=str(constraint_reference.id)
            )
            geofence_id = str(uuid.uuid4())

            if constraint_reference_exists:
                geo_fence = self.my_database_reader.get_geofence_by_constraint_reference_id(constraint_reference_id=str(constraint_reference.id))
                geofence_id = geo_fence.id

            my_volumes_converter.convert_volumes_to_geojson(volumes=constraint_details.volumes)

            geofence_payload = GeofencePayload(
                id=geofence_id,
                raw_geo_fence=my_volumes_converter.geojson,
                upper_limit=my_volumes_converter.upper_altitude,
                lower_limit=my_volumes_converter.upper_altitude,
                altitude_ref=my_volumes_converter.altitude_ref,
                name=constraint_details.volumes[0].name,
                bounds=my_volumes_converter.get_bounds(),
                status=1,
                message="Constraint from peer USS",
                is_test_dataset=False,
                start_datetime=constraint_reference.time_start,
                end_datetime=constraint_reference.time_end,
            )

            geo_fence = self.my_database_writer.create_or_update_geofence(geofence_payload=geofence_payload)
            # Create a new ConstraintReference object

            self.my_database_writer.create_or_update_constraint_reference(
                constraint_reference=constraint_reference,
                geofence=geo_fence,
            )

            # Write the constraint details to the database
            self.my_database_writer.create_or_update_constraint_detail(
                constraint=constraint_details,
                geofence=geo_fence,
            )


class ConstraintOperations:
    def __init__(self):
        self.dss_base_url = env.get("DSS_BASE_URL", "0")

        self.database_reader = FlightBlenderDatabaseReader()
        self.database_writer = FlightBlenderDatabaseWriter()

    def get_nearby_constraints(self, volumes: list[Volume4D]) -> list[Constraint]:
        # This method checks the USS network for any other volume in the airspace and queries the individual USS for data

        all_uss_op_int_details = []
        auth_token = self.get_auth_token()
        # Query the DSS for operational intentns
        query_constraints_url = self.dss_base_url + "dss/v1/constraint_references/query"
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }

        flight_blender_base_url = env.get("FLIGHTBLENDER_FQDN", "http://flight-blender:8000")
        constraints_helper = ConstraintsHelper()
        all_constraint_details: list[Constraint] = []

        for volume in volumes:
            constraints_retrieved = False
            constraint_references = []
            area_of_interest = QueryConstraintsPayload(area_of_interest=volume)
            logger.info("Querying DSS for operational intents in the area..")
            logger.debug(f"Area of interest {area_of_interest}")
            try:
                query_constraints_request = requests.post(
                    query_constraints_url,
                    json=json.loads(json.dumps(asdict(area_of_interest))),
                    headers=headers,
                )
            except Exception as re:
                logger.error("Error in getting operational intent for the volume %s " % re)
            else:
                # The DSS returned operational intent references as a list
                _dss_constraint_references = query_constraints_request.json()
                logger.debug(f"DSS Response {_dss_constraint_references}")
                constraint_references = _dss_constraint_references["constraint_references"]

            # Query the operational intent reference details
            for constraint_reference in constraint_references:
                _constraint_reference = from_dict(data_class=ConstraintReference, data=constraint_reference)
                constraint_references.append(_constraint_reference)

            for _constraint_reference in constraint_references:
                logging.info("All Constraints in the area..")

                current_uss_base_url = _constraint_reference.uss_base_url
                _constraint_details_to_process = {}
                _constraint_reference_to_process = {}

                if current_uss_base_url == flight_blender_base_url:
                    # This constraint is managed in Blender, so we can get the details from the database

                    constraints_reference_exists = self.database_reader.check_constraint_reference_id_exists(
                        constraint_reference_id=str(_constraint_reference.id)
                    )

                    if constraints_reference_exists:
                        # Get the declaration
                        db_constraint_reference = self.database_reader.get_constraint_reference_by_id(
                            constraint_reference_id=str(_constraint_reference.id)
                        )
                        geofence = db_constraint_reference.geofence

                        constraint_detail = self.database_reader.get_constraint_by_geofence(geofence=geofence)

                        self.database_writer.update_constraint_reference_ovn(
                            constraint_reference=db_constraint_reference,
                            ovn=_constraint_reference.ovn,
                        )

                        _constraint_reference_temp = ConstraintReference(
                            id=str(db_constraint_reference.id),
                            manager=db_constraint_reference.manager,
                            uss_availability=db_constraint_reference.uss_availability,
                            version=db_constraint_reference.version,
                            time_start=Time(
                                format="RFC3339",
                                value=db_constraint_reference.time_start,
                            ),
                            time_end=Time(
                                format="RFC3339",
                                value=db_constraint_reference.time_end,
                            ),
                            uss_base_url=db_constraint_reference.uss_base_url,
                            ovn=db_constraint_reference.ovn,
                            subscription_id=db_constraint_reference.subscription_id,
                            state=db_constraint_reference.state,
                        )
                        _constraint_reference_to_process = asdict(_constraint_reference_temp)
                        _constraint_details_to_process = {
                            "volumes": json.loads(constraint_detail.volumes),
                            "type": json.loads(constraint_detail._type),
                            "geozone": constraint_detail.priority,
                        }
                    else:
                        logger.warning(f"Constraint reference not found in the database, : {_constraint_reference.id}")
                    constraints_retrieved = True

                else:  # This operational intent details is from a peer uss, need to query peer USS
                    uss_audience = generate_audience_from_base_url(base_url=current_uss_base_url)
                    uss_auth_token = self.get_auth_token(audience=uss_audience)
                    logger.debug(f"Auth Token {uss_auth_token}")
                    uss_headers = {
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + uss_auth_token["access_token"],
                    }
                    constraints_detail_url = current_uss_base_url + "/uss/v1/constraints/" + _constraint_reference.id

                    logger.debug(f"Querying USS for constraints: {current_uss_base_url}")
                    try:
                        uss_constraint_request = requests.get(constraints_detail_url, headers=uss_headers)
                    except urllib3.exceptions.NameResolutionError:
                        logger.info("URLLIB error")
                        raise ConnectionError("Could not reach peer USS.. ")

                    except (
                        requests.exceptions.ConnectTimeout,
                        requests.exceptions.HTTPError,
                        requests.exceptions.ReadTimeout,
                        requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError,
                    ) as e:
                        logger.error("Connection error details..")
                        logger.error(e)
                        logger.error(
                            "Error in getting constraint id {constraint_id} details from uss with base url {uss_base_url}".format(
                                constraint_id=_constraint_reference.id,
                                uss_base_url=current_uss_base_url,
                            )
                        )
                        constraints_retrieved = False
                        logger.info("Raising connection Error 1")
                        raise ConnectionError("Could not reach peer USS..")

                    else:
                        # Verify status of the response from the USS
                        if uss_constraint_request.status_code == 200:
                            retrived_constraints_json = uss_constraint_request.json()
                            constraints_retrieved = True
                            _constraint_details_to_process = retrived_constraints_json["constraint"]["details"]
                            _constraint_reference_to_process = retrived_constraints_json["constraint"]["reference"]
                        # The attempt to get data from the USS in the network failed
                        elif uss_constraint_request.status_code in [
                            401,
                            400,
                            404,
                            500,
                        ]:
                            logger.debug(uss_constraint_request.json())
                            logger.error(
                                "Error in querying peer USS about operational intent (ID: {constraint_id}) details from uss with base url {uss_base_url}".format(
                                    constraint_id=_constraint_reference.id,
                                    uss_base_url=current_uss_base_url,
                                )
                            )

                if constraints_retrieved:
                    _constraint_reference_processed = from_dict(
                        data_class=ConstraintReference,
                        data=_constraint_reference_to_process,
                    )
                    _constraint_details_processed = from_dict(
                        data_class=ConstraintDetails,
                        data=_constraint_details_to_process,
                    )
                    _constraint = Constraint(reference=_constraint_reference_processed, details=_constraint_details_processed)
                    all_constraint_details.append(_constraint)

        return all_constraint_details

    def get_auth_token(self, audience: str = ""):
        my_authorization_helper = dss_auth_helper.AuthorityCredentialsGetter()
        if audience is None:
            audience = env.get("DSS_SELF_AUDIENCE", 0)
        try:
            assert audience
        except AssertionError:
            logger.error("Error in getting Authority Access Token DSS_SELF_AUDIENCE is not set in the environment")
        auth_token = {}
        try:
            auth_token = my_authorization_helper.get_cached_credentials(audience=audience, token_type="constraints")
        except Exception as e:
            logger.error("Error in getting Authority Access Token %s " % e)
            logger.error(f"Auth server error {e}")
            auth_token["error"] = "Error in getting access token"
        else:
            error = auth_token.get("error", None)
            if error:
                logger.error("Authority server provided the following error during token request %s " % error)

        return auth_token
