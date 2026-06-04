from dataclasses import asdict
from datetime import datetime

from asgiref.sync import sync_to_async

from flight_blender.conformance.data_definitions import ConformanceRecord, ConformanceSummary
from flight_blender.infrastructure.database.repositories.django_conformance import DjangoConformanceRepository


class ConformanceOperations:
    def __init__(self) -> None:
        self._repo = DjangoConformanceRepository()

    async def get_records(self, start_time: datetime, end_time: datetime) -> list[dict]:
        def _fetch():
            raw = self._repo.get_conformance_records_for_duration(start_time=start_time, end_time=end_time)
            return list(raw) if raw else []

        orm_records = await sync_to_async(_fetch)()
        return [
            asdict(
                ConformanceRecord(
                    id=str(r.id),
                    flight_declaration_id=str(r.flight_declaration_id),
                    conformance_state=r.conformance_state,
                    timestamp=r.timestamp,
                    description=r.description,
                    event_type=r.event_type,
                    geofence_breach=r.geofence_breach,
                    geofence_id=str(r.geofence_id) if r.geofence_id else None,
                    resolved=r.resolved,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
            )
            for r in orm_records
        ]

    async def get_summary(self, start_time: datetime, end_time: datetime, start_date: str, end_date: str) -> ConformanceSummary:
        def _fetch():
            raw = self._repo.get_conformance_records_for_duration(start_time=start_time, end_time=end_time)
            return list(raw) if raw else []

        records = await sync_to_async(_fetch)()
        total = len(records)
        conforming = sum(1 for r in records if r.conformance_state == 1)
        return ConformanceSummary(
            total_records=total,
            conforming_records=conforming,
            non_conforming_records=total - conforming,
            conformance_rate_percentage=(conforming / total * 100) if total else 0,
            start_date=start_date,
            end_date=end_date,
        )
