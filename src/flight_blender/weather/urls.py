from django.urls import path

from . import views as weather_monitoring_views

urlpatterns = [
    path("weather/", weather_monitoring_views.WeatherAPIView.as_view(), name="weather"),
]
