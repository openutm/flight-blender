import requests
from rest_framework import status

WEATHER_TOPICS = [
    "temperature_2m",
    "showers",
    "windspeed_10m",
    "winddirection_10m",
    "windgusts_10m",
]


class WeatherService:
    def __init__(self, base_url):
        self.base_url = base_url
        # Add more API related properties as needed

    def get_weather(self, longitude, latitude, time, timezone):
        url = f"{self.base_url}"
        params = {
            "longitude": longitude,
            "latitude": latitude,
            "time": time,
            "timezone": timezone,
            "forecast_days": "1",
            "hourly": ",".join(WEATHER_TOPICS),
        }

        response = requests.get(url, params=params, timeout=30)

        if response.status_code == status.HTTP_200_OK:
            return response.json()
        else:
            error_message = f"Error fetching weather data: {response.text}"
            raise Exception(error_message)
