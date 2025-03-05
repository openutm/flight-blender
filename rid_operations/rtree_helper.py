import hashlib
import json
import logging
from typing import List

import arrow
from rtree import index
from shapely.geometry import Polygon

from auth_helper.common import get_redis
from common.database_operations import FlightBlenderDatabaseReader
from scd_operations.scd_data_definitions import Altitude, OpInttoCheckDetails, Time

logger = logging.getLogger("django")


class OperationalIntentComparisonFactory:
    """A method to check if two operational intents are same in geometry / time and altitude."""

    def check_volume_geometry_same(self, polygon_a: Polygon, polygon_b: Polygon) -> bool:
        return polygon_a.equals(polygon_b)  # Also has exact_equals and almost_equals method

    def check_volume_start_end_time_same(self, time_a: Time, time_b: Time) -> bool:
        # TODO: Implement checking of two times
        return True

    def check_volume_(self, altitude_a: Altitude, altitude_b: Altitude) -> bool:
        # TODO: Implement checking of two altitudes
        return True


class OperationalIntentsIndexFactory:
    def __init__(self, index_name: str):
        self.idx = index.Index(index_name)
        self.r = get_redis()

    def add_box_to_index(
        self,
        enumerated_id: int,
        flight_id: str,
        view: List[float],
        start_time: str,
        end_time: str,
    ):
        metadata = {
            "start_time": start_time,
            "end_time": end_time,
            "flight_id": flight_id,
        }
        self.idx.insert(
            id=enumerated_id,
            coordinates=(view[0], view[1], view[2], view[3]),
            obj=metadata,
        )

    def delete_from_index(self, enumerated_id: int, view: List[float]):
        self.idx.delete(id=enumerated_id, coordinates=(view[0], view[1], view[2], view[3]))

    def check_op_ints_exist(self) -> bool:
        """This method generates a rTree index of currently active operational indexes"""
        my_database_reader = FlightBlenderDatabaseReader()
        return my_database_reader.check_active_activated_flights_exist()

    def generate_active_flights_operational_intents_index(self) -> None:
        """This method generates a rTree index of currently active operational intents"""

        my_database_reader = FlightBlenderDatabaseReader()
        flight_declarations = my_database_reader.get_active_activated_flight_declarations()

        for flight_declaration in flight_declarations:
            flight_id_str = str(flight_declaration.id)

            enumerated_flight_id = int(hashlib.sha256(flight_id_str.encode("utf-8")).hexdigest(), 16) % 10**8

            split_view = flight_declaration.bounds.split(",")
            start_time = flight_declaration.start_datetime
            end_time = flight_declaration.end_datetime
            view = [float(i) for i in split_view]

            self.add_box_to_index(
                enumerated_id=enumerated_flight_id,
                flight_id=flight_id_str,
                view=view,
                start_time=start_time,
                end_time=end_time,
            )

    def clear_rtree_index(self):
        """Method to delete all boxes from the index"""

        my_database_reader = FlightBlenderDatabaseReader()
        flight_declarations = my_database_reader.get_active_activated_flight_declarations()

        for flight_declaration in flight_declarations:
            flight_id_str = str(flight_declaration.id)

            enumerated_flight_id = int(hashlib.sha256(flight_id_str.encode("utf-8")).hexdigest(), 16) % 10**8

            split_view = flight_declaration.bounds.split(",")
            view = [float(i) for i in split_view]
            self.delete_from_index(enumerated_id=enumerated_flight_id, view=view)

    def close_index(self):
        """Method to delete / close index"""
        self.idx.close()

    def check_box_intersection(self, view_box: List[float]):
        intersections = [n.object for n in self.idx.intersection((view_box[0], view_box[1], view_box[2], view_box[3]), objects=True)]
        return intersections


def check_polygon_intersection(op_int_details: List[OpInttoCheckDetails], polygon_to_check: Polygon) -> bool:
    idx = index.Index()
    for pos, op_int_detail in enumerate(op_int_details):
        idx.insert(pos, op_int_detail.shape.bounds)

    op_ints_of_interest_ids = list(idx.intersection(polygon_to_check.bounds))
    does_intersect = []
    if op_ints_of_interest_ids:
        for op_ints_of_interest_id in op_ints_of_interest_ids:
            existing_op_int = op_int_details[op_ints_of_interest_id]
            intersects = polygon_to_check.intersects(existing_op_int.shape)
            if intersects:
                does_intersect.append(True)
            else:
                does_intersect.append(False)

        return all(does_intersect)
    else:
        return False
