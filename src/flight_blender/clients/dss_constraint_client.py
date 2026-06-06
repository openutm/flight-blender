import json
import uuid
from dataclasses import asdict
from enum import Enum
from urllib.parse import urlparse

import requests
import urllib3
from dacite import Config, from_dict
from loguru import logger

from flight_blender.auth.dss_auth import get_dss_auth_token
from flight_blender.auth.token_audience import generate_audience_from_base_url
from flight_blender.config import settings
from flight_blender.db.session import async_task_session
from flight_blender.domain_types.constraint import Constraint, ConstraintDetails, ConstraintReference, QueryConstraintsPayload
from flight_blender.domain_types.scd import Time, Volume4D
from flight_blender.repositories.constraint_repo import SQLAlchemyConstraintRepository


class ConstraintOperations:
    def __init__(self, constraint_repo: SQLAlchemyConstraintRepository | None = None):
        self.dss_base_url = settings.DSS_BASE_URL
        self.constraint_repo = constraint_repo

    async def get_nearby_constraints(self, volumes: list[Volume4D]) -> list[Constraint]:
        # This method checks the USS network for any other volume in the airspace and queries the individual USS for data

        # Get auth token with DSS audience (not self audience)
        dss_audience = generate_audience_from_base_url(self.dss_base_url)
        auth_token = self.get_auth_token(audience=dss_audience)
        if not auth_token or "error" in auth_token:
            logger.error("Failed to get auth token for DSS constraint query")
            return []
        # Query the DSS for constraints
        query_constraints_url = self.dss_base_url + "dss/v1/constraint_references/query"
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }

        flight_blender_base_url = settings.FLIGHTBLENDER_FQDN

        all_constraints_in_aoi: list[Constraint] = []

        for volume in volumes:
            constraints_retrieved = False
            constraint_references = []
            area_of_interest = QueryConstraintsPayload(area_of_interest=volume)
            logger.info("Querying DSS for constraints in the area..")
            logger.debug(f"Area of interest {area_of_interest}")
            try:
                query_constraints_request = requests.post(
                    query_constraints_url,
                    json=json.loads(json.dumps(asdict(area_of_interest))),
                    headers=headers,
                    timeout=30,
                )
            except Exception as re:
                logger.error("Error in getting constraint for the volume %s " % re)
            else:
                # The DSS returned constraint references as a list
                _dss_constraint_references = query_constraints_request.json()
                logger.debug(f"DSS Response {_dss_constraint_references}")
                if "constraint_references" in _dss_constraint_references:
                    constraint_references = _dss_constraint_references["constraint_references"]
                else:
                    logger.error("DSS constraint query did not return constraint_references: %s" % _dss_constraint_references)
                    constraint_references = []
            _constraint_references = []

            # Query the operational intent reference details
            for constraint_reference in constraint_references:
                try:
                    _constraint_reference_tmp = from_dict(data_class=ConstraintReference, data=constraint_reference, config=Config(cast=[Enum]))

                except Exception as e:
                    logger.error("Error in processing constraint reference %s " % e)
                else:
                    _constraint_references.append(_constraint_reference_tmp)

            for _constraint_reference in _constraint_references:
                logger.info("All Constraints in the area..")

                current_uss_base_url = _constraint_reference.uss_base_url
                _constraint_details_to_process = {}
                _constraint_reference_to_process = {}

                if current_uss_base_url == flight_blender_base_url:
                    # This constraint is managed in Blender, so we can get the details from the database
                    if self.constraint_repo is not None:
                        constraint_repo = self.constraint_repo
                    else:
                        from flight_blender.repositories.constraint_repo import SQLAlchemyConstraintRepository  # noqa: PLC0415

                        _db_ctx = async_task_session()
                        db = await _db_ctx.__aenter__()
                        constraint_repo = SQLAlchemyConstraintRepository(db)
                    db_constraint_reference = await constraint_repo.get_constraint_reference_by_id(uuid.UUID(str(_constraint_reference.id)))
                    constraints_reference_exists = db_constraint_reference is not None

                    if constraints_reference_exists:
                        constraint_detail = await constraint_repo.get_constraint_by_geofence_id(db_constraint_reference.geofence_id)
                        await constraint_repo.update_constraint_reference_ovn(
                            ref_id=db_constraint_reference.id,
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
                            "geozone": constraint_detail.geozone,
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

                    parsed_url = urlparse(current_uss_base_url)
                    if parsed_url.path:
                        current_uss_base_url = parsed_url.scheme + "://" + parsed_url.netloc

                    constraints_detail_url = current_uss_base_url + "/uss/v1/constraints/" + str(_constraint_reference.id)

                    logger.info(f"Querying USS for constraints: {constraints_detail_url}")
                    try:
                        uss_constraint_request = requests.get(constraints_detail_url, headers=uss_headers, timeout=30)
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
                        data_class=ConstraintReference, data=_constraint_reference_to_process, config=Config(cast=[Enum])
                    )
                    _constraint_details_processed = from_dict(
                        data_class=ConstraintDetails, data=_constraint_details_to_process, config=Config(cast=[Enum])
                    )
                    _constraint = Constraint(
                        reference=_constraint_reference_processed,
                        details=_constraint_details_processed,
                    )

                    all_constraints_in_aoi.append(_constraint)

        return all_constraints_in_aoi

    def get_auth_token(self, audience: str = ""):
        return get_dss_auth_token(audience=audience, token_type="constraints")  # nosec B106
