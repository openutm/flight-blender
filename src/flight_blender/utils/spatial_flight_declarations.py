from __future__ import annotations

import hashlib
import os
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import arrow
from loguru import logger
from rtree import index
from rtree.exceptions import RTreeError

from flight_blender.auth.token_cache import get_redis
from flight_blender.domain_types.flight_declarations import FlightDeclarationMetadata

if TYPE_CHECKING:
    from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository


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
                except OSError as exc:
                    logger.warning("Failed to remove corrupt RTree file {} during recovery: {}", path, exc)
        return index.Index(base_path)


class FlightDeclarationRTreeIndexFactory:
    def __init__(self, index_name: str, fd_repo: SQLAlchemyFlightDeclarationRepository | None = None):
        self.r = get_redis()
        self.idx = _open_or_recover_index(index_name)
        self.fd_repo = fd_repo

    def add_box_to_index(self, id: int, flight_declaration_id: str, view: list[float], start_date: str, end_date: str) -> None:
        metadata = FlightDeclarationMetadata(start_date=start_date, end_date=end_date, flight_declaration_id=flight_declaration_id)
        self.idx.insert(id=id, coordinates=(view[0], view[1], view[2], view[3]), obj=asdict(metadata))

    def delete_from_index(self, enumerated_id: int, view: list[float]) -> None:
        self.idx.delete(id=enumerated_id, coordinates=(view[0], view[1], view[2], view[3]))

    def generate_flight_declaration_index(self, all_flight_declarations: list[Any]) -> None:
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

    async def clear_rtree_index(self, all_declarations: list[Any] | None = None) -> None:
        if all_declarations is None:
            if self.fd_repo is None:
                raise ValueError("fd_repo is required when all_declarations is not provided")
            all_declarations = await self.fd_repo.list(states=[1, 2])
        for declaration in all_declarations:
            declaration_idx_str = str(declaration.id)
            declaration_id = int(hashlib.sha256(declaration_idx_str.encode("utf-8")).hexdigest(), 16) % 10**8
            view = [float(i) for i in declaration.bounds.split(",")]
            self.delete_from_index(enumerated_id=declaration_id, view=view)

    def check_flight_declaration_box_intersection(self, view_box: list[float]) -> list[FlightDeclarationMetadata]:
        return [
            FlightDeclarationMetadata(**n.object) for n in self.idx.intersection((view_box[0], view_box[1], view_box[2], view_box[3]), objects=True)
        ]
