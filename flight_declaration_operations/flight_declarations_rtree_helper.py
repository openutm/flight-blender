import hashlib
from dataclasses import asdict

import arrow
from django.db.models import QuerySet
from rtree import index

from auth_helper.common import get_redis

from .data_definitions import FlightDeclarationMetadata
from .models import FlightDeclaration


class FlightDeclarationRTreeIndexFactory:
    """
    A factory class for managing an RTree index of flight declarations.
    Methods:
        __init__(index_name: str):
            Initializes the RTree index with the given name.
        add_box_to_index(id: int, flight_declaration_id: str, view: List[float], start_date: str, end_date: str) -> None:
        delete_from_index(enumerated_id: int, view: List[float]) -> None:
        generate_flight_declaration_index(all_flight_declarations: Union[QuerySet, List[FlightDeclaration]]) -> None:
        clear_rtree_index() -> None:
        check_flight_declaration_box_intersection(view_box: List[float]) -> List[FlightDeclarationMetadata]:
    """

    def __init__(self, index_name: str):
        self.r = get_redis()
        self.idx = index.Index(index_name)

    def add_box_to_index(
        self,
        id: int,
        flight_declaration_id: str,
        view: list[float],
        start_date: str,
        end_date: str,
    ) -> None:
        """
        Adds a bounding box to the RTree index.

        Args:
            id (int): The unique identifier for the box.
            flight_declaration_id (str): The flight declaration ID.
            view (List[float]): The bounding box coordinates [minx, miny, maxx, maxy].
            start_date (str): The start date of the flight declaration.
            end_date (str): The end date of the flight declaration.
        """
        metadata = FlightDeclarationMetadata(start_date=start_date, end_date=end_date, flight_declaration_id=flight_declaration_id)
        self.idx.insert(id=id, coordinates=(view[0], view[1], view[2], view[3]), obj=asdict(metadata))

    def delete_from_index(self, enumerated_id: int, view: list[float]) -> None:
        """
        Deletes a bounding box from the RTree index.

        Args:
            enumerated_id (int): The unique identifier for the box.
            view (List[float]): The bounding box coordinates [minx, miny, maxx, maxy].
        """
        self.idx.delete(id=enumerated_id, coordinates=(view[0], view[1], view[2], view[3]))

    def generate_flight_declaration_index(self, all_flight_declarations: QuerySet | list[FlightDeclaration]) -> None:
        """
        Generates an RTree index of currently active operational indexes.

        Args:
            all_flight_declarations (Union[QuerySet, List[FlightDeclaration]]): A list or queryset of flight declarations.
        """
        present = arrow.now()
        start_date = present.shift(days=-1).isoformat()
        end_date = present.shift(days=1).isoformat()
        for flight_declaration in all_flight_declarations:
            declaration_idx_str = str(flight_declaration.id)
            flight_declaration_id = int(hashlib.sha256(declaration_idx_str.encode("utf-8")).hexdigest(), 16) % 10**8
            view = [float(i) for i in flight_declaration.bounds.split(",")]
            self.add_box_to_index(
                id=flight_declaration_id,
                flight_declaration_id=declaration_idx_str,
                view=view,
                start_date=start_date,
                end_date=end_date,
            )

    def clear_rtree_index(self) -> None:
        """
        Deletes all boxes from the RTree index.
        """
        all_declarations = FlightDeclaration.objects.all()
        for declaration in all_declarations:
            declaration_idx_str = str(declaration.id)
            declaration_id = int(hashlib.sha256(declaration_idx_str.encode("utf-8")).hexdigest(), 16) % 10**8
            view = [float(i) for i in declaration.bounds.split(",")]
            self.delete_from_index(enumerated_id=declaration_id, view=view)

    def check_flight_declaration_box_intersection(self, view_box: list[float]) -> list[FlightDeclarationMetadata]:
        """
        Checks for intersections with a given bounding box.

        Args:
            view_box (List[float]): The bounding box coordinates [minx, miny, maxx, maxy].

        Returns:
            List[FlightDeclarationMetadata]: A list of metadata for intersecting boxes.
        """
        intersections = [
            FlightDeclarationMetadata(**n.object) for n in self.idx.intersection((view_box[0], view_box[1], view_box[2], view_box[3]), objects=True)
        ]

        return intersections
