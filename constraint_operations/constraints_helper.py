import json
import logging
from dataclasses import asdict
from os import environ as env
from typing import Never

import requests
import urllib3
from dacite import from_dict
from dotenv import find_dotenv, load_dotenv

from auth_helper import dss_auth_helper
from auth_helper.common import get_redis
from common.auth_token_audience_helper import generate_audience_from_base_url
from common.database_operations import FlightBlenderDatabaseReader, FlightBlenderDatabaseWriter
from scd_operations.dss_scd_helper import Volume4D

from .data_definitions import Constraint, ConstraintReference, QueryConstraintsPayload

load_dotenv(find_dotenv())

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)

logger = logging.getLogger("django")


class USSConstraintsOperations:
    def __init__(self):
        self.dss_base_url = env.get("DSS_BASE_URL", "0")
        self.r = get_redis()
        self.database_reader = FlightBlenderDatabaseReader()
        self.database_writer = FlightBlenderDatabaseWriter()

    def get_auth_token(self, audience: str = ""):
        my_authorization_helper = dss_auth_helper.AuthorityCredentialsGetter()
        if not audience:
            audience = env.get("DSS_SELF_AUDIENCE", "")
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

    def query_constraint_references(self, volume: Volume4D) -> list[ConstraintReference] | list[Never]:
        auth_token = self.get_auth_token()

        query_constraint_references_url = self.dss_base_url + "dss/v1/constraint_references/query"
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }

        constraint_references: list[ConstraintReference] = []
        area_of_interest = QueryConstraintsPayload(area_of_interest=volume)
        logger.info("Querying DSS for constraints in the area..")
        logger.debug(f"Area of interest {area_of_interest}")
        try:
            constraints_query_response = requests.post(
                query_constraint_references_url,
                json=json.loads(json.dumps(asdict(area_of_interest))),
                headers=headers,
            )
        except Exception as re:
            logger.error("Error in getting operational intent for the volume %s " % re)
        else:
            # The DSS returned operational intent references as a list
            dss_constraint_references = constraints_query_response.json()
            logger.debug(f"DSS Response {dss_constraint_references}")
            _dss_constraint_references = dss_constraint_references["constraint_references"]
            if _dss_constraint_references:
                for _constraint_reference in _dss_constraint_references:
                    constraint_reference = from_dict(
                        data_class=ConstraintReference,
                        data=_constraint_reference,
                    )
                    constraint_references.append(constraint_reference)
            else:
                logger.info("No constraints found in the area of interest")
        return constraint_references

    def get_constraint_details_from_uss(self, constraint_reference: ConstraintReference) -> Constraint | None:
        current_uss_base_url = constraint_reference.uss_base_url

        uss_audience = generate_audience_from_base_url(base_url=current_uss_base_url)

        uss_auth_token = self.get_auth_token(audience=uss_audience)
        logger.debug(f"Auth Token {uss_auth_token}")
        uss_headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + uss_auth_token["access_token"],
        }
        uss_constraint_details_url = current_uss_base_url + "/uss/v1/constraints/" + constraint_reference.id

        logger.debug(f"Querying USS: {current_uss_base_url}")

        constraint_details_retrived = False
        try:
            uss_constraint_details_request = requests.get(uss_constraint_details_url, headers=uss_headers)
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
                    constraint_id=constraint_reference.id,
                    uss_base_url=current_uss_base_url,
                )
            )
            logger.info("Raising connection Error 1")
            raise ConnectionError("Could not reach peer USS..")

        # Verify status of the response from the USS
        if uss_constraint_details_request.status_code == 200:
            # Request was successful
            uss_constraint_details_json = uss_constraint_details_request.json()
            constraint_details_retrived = True
        # The attempt to get data from the USS in the network failed
        elif uss_constraint_details_request.status_code in [
            401,
            400,
            404,
            500,
        ]:
            logger.debug(uss_constraint_details_request.json())
            logger.error(
                "Error in querying peer USS about constraint (ID: {constraint_id}) details from uss with base url {uss_base_url}".format(
                    constraint_id=constraint_reference.id,
                    uss_base_url=current_uss_base_url,
                )
            )

        constraint = None
        if constraint_details_retrived:
            constraint = from_dict(data_class=Constraint, data=uss_constraint_details_json)
            logger.debug(f"Constraint details {constraint}")
        return constraint
