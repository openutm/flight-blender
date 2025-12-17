# Create your views here.
# Create your views here.

from dataclasses import asdict

import arrow
from dacite import from_dict
from django.http import JsonResponse
from rest_framework.decorators import api_view

from auth_helper.utils import requires_scopes
from common.data_definitions import FLIGHTBLENDER_READ_SCOPE
from common.database_operations import (
    FlightBlenderDatabaseReader,
)

from .data_definitions import ConformanceRecord, ConformanceSummary


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def conformance_status(request):
    # Implement logic to retrieve and return conformance status
    return JsonResponse({"status": "OK"})


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def get_conformance_records(request):
    # Implement logic to retrieve and return conformance record summary
    my_database_reader = FlightBlenderDatabaseReader()
    start_date = request.parameters.get("start_date")
    end_date = request.parameters.get("end_date")
    if not start_date or not end_date:
        return JsonResponse({"error": "start_date and end_date are required"}, status=400)

    try:
        start_datetime = arrow.get(start_date)
        end_datetime = arrow.get(end_date)
    except arrow.ParserError:
        return JsonResponse({"error": "Invalid date format. Use ISO 8601 format."}, status=400)

    if start_datetime >= end_datetime:
        return JsonResponse({"error": "start_date must be before end_date"}, status=400)

    all_conformance_records = my_database_reader.get_conformance_records_for_duration(start_time=start_datetime, end_time=end_datetime)

    conformance_records = [from_dict(data_class=ConformanceRecord, data=record) for record in all_conformance_records]
    return JsonResponse({"conformance_records": [asdict(record) for record in conformance_records]})


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def get_conformance_record_summary(request):
    # Implement logic to retrieve and return conformance record summary
    my_database_reader = FlightBlenderDatabaseReader()
    start_date = request.parameters.get("start_date")
    end_date = request.parameters.get("end_date")
    if not start_date or not end_date:
        return JsonResponse({"error": "start_date and end_date are required"}, status=400)

    try:
        start_datetime = arrow.get(start_date)
        end_datetime = arrow.get(end_date)
    except arrow.ParserError:
        return JsonResponse({"error": "Invalid date format. Use ISO 8601 format."}, status=400)

    if start_datetime >= end_datetime:
        return JsonResponse({"error": "start_date must be before end_date"}, status=400)

    all_conformance_records = my_database_reader.get_conformance_records_for_duration(start_time=start_datetime, end_time=end_datetime)
    # Calculate summary statistics
    total_records = len(all_conformance_records)
    conforming_records = sum(1 for record in all_conformance_records if record.get("conformance_state", False))
    non_conforming_records = total_records - conforming_records
    conformance_rate = (conforming_records / total_records * 100) if total_records > 0 else 0

    summary = asdict(
        ConformanceSummary(
            total_records=total_records,
            conforming_records=conforming_records,
            non_conforming_records=non_conforming_records,
            conformance_rate_percentage=conformance_rate,
            start_date=start_date,
            end_date=end_date,
        )
    )

    return JsonResponse({"summary": summary})
