import arrow
from django.conf import settings
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_helper.utils import requires_scopes
from common.data_definitions import FLIGHTBLENDER_WRITE_SCOPE
from services.weather_service import WeatherService

from .serializers import WeatherSerializer


@method_decorator(requires_scopes([FLIGHTBLENDER_WRITE_SCOPE]), name="dispatch")
class WeatherAPIView(APIView):
    def get(self, request, *args, **kwargs):
        longitude = request.query_params.get("longitude")
        latitude = request.query_params.get("latitude")
        time = request.query_params.get("time")
        timezone = request.query_params.get("timezone")

        if not longitude:
            return Response(
                {"error": "Longitude parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not latitude:
            return Response(
                {"error": "Latitude parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        time = time if time else arrow.now().timestamp()

        timezone = timezone if timezone else "UTC"

        weather_service = WeatherService(base_url=settings.WEATHER_API_BASE_URL)
        weather_data = weather_service.get_weather(longitude, latitude, time, timezone)

        serializer = WeatherSerializer(data=weather_data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)
