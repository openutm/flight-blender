import hashlib
import os
from dataclasses import asdict

import arrow
from django.db.models import QuerySet
from loguru import logger
from rtree import index
from rtree.exceptions import RTreeError

from flight_blender.auth.common import get_redis

from .data_definitions import GeoFenceMetadata
from .models import GeoFence


def _open_or_recover_index(base_path: str) -> index.Index:
    """Open an RTree index, auto-recovering from corrupt files."""
    try:
        return index.Index(base_path)
    except RTreeError:
        logger.warning("Corrupt RTree index at {}, recreating", base_path)
        for ext in (".idx", ".dat"):
            path = base_path + ext
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    logger.exception("Failed to remove corrupt RTree index file {} during recovery", path)
        return index.Index(base_path)


class GeoFenceRTreeIndexFactory:
    def __init__(self, index_name: str):
        self.idx = _open_or_recover_index(index_name)
        self.r = get_redis()

    def add_box_to_index(
        self,
        id: int,
        geo_fence_id: str,
        view: list[float],
        start_date: str,
        end_date: str,
    ):
        """
        Add a box to the RTree index with associated metadata.

        Args:
            id (int): The unique identifier for the geo-fence.
            geo_fence_id (str): The string representation of the geo-fence ID.
            view (List[float]): A list of four floats representing the bounding box coordinates.
            start_date (str): The start date for the geo-fence in ISO format.
            end_date (str): The end date for the geo-fence in ISO format.
        """

        metadata = GeoFenceMetadata(
            start_date=start_date,
            end_date=end_date,
            geo_fence_id=geo_fence_id,
        )
        self.idx.insert(id=id, coordinates=(view[0], view[1], view[2], view[3]), obj=asdict(metadata))

    def delete_from_index(self, enumerated_id: int, view: list[float]):
        """
        Delete a box from the RTree index.

        Args:
            enumerated_id (int): The unique identifier for the geo-fence.
            view (List[float]): A list of four floats representing the bounding box coordinates to be deleted.
        """
        self.idx.delete(id=enumerated_id, coordinates=(view[0], view[1], view[2], view[3]))

    def generate_geo_fence_index(self, all_fences: QuerySet | list[GeoFence]) -> None:
        """
        This method generates an RTree index of currently active operational geo-fences.

        Args:
            all_fences (Union[QuerySet, List[GeoFence]]): A list or queryset of GeoFence objects to be indexed.
        """
        present = arrow.now()
        start_date = present.shift(days=-1).isoformat()
        end_date = present.shift(days=1).isoformat()

        for fence in all_fences:
            fence_idx_str = str(fence.id)
            fence_id = int(hashlib.sha256(fence_idx_str.encode("utf-8")).hexdigest(), 16) % 10**8
            view = [float(coord) for coord in fence.bounds.split(",")]
            # Swap the coordinates to store as latitude, longitude format
            view = [view[1], view[0], view[3], view[2]]

            self.add_box_to_index(
                id=fence_id,
                geo_fence_id=fence_idx_str,
                view=view,
                start_date=start_date,
                end_date=end_date,
            )

    def clear_rtree_index(self):
        """
        Method to delete all boxes from the RTree index.
        This method retrieves all GeoFence objects, calculates their unique IDs,
        and deletes each corresponding box from the RTree index.
        """
        all_fences = GeoFence.objects.all()
        for fence in all_fences:
            fence_idx_str = str(fence.id)
            fence_id = int(hashlib.sha256(fence_idx_str.encode("utf-8")).hexdigest(), 16) % 10**8
            view = [float(coord) for coord in fence.bounds.split(",")]
            self.delete_from_index(enumerated_id=fence_id, view=view)

    def check_box_intersection(self, view_box: list[float]):
        """
        Check for intersections with the given view box.

        Args:
            view_box (List[float]): A list of four floats representing the bounding box to check for intersections.

        Returns:
            List[dict]: A list of metadata dictionaries for each intersecting box.
        """

        intersections = [n.object for n in self.idx.intersection((view_box[0], view_box[1], view_box[2], view_box[3]), objects=True)]
        return intersections
