from django.urls import path

from . import views as flight_feed_views

urlpatterns = [
    path("set_air_traffic/<uuid:session_id>", flight_feed_views.set_air_traffic),
    path("get_air_traffic/<uuid:session_id>", flight_feed_views.get_air_traffic),
    path("start_opensky_feed", flight_feed_views.start_opensky_feed),
    path("set_telemetry", flight_feed_views.set_telemetry),
    path("set_signed_telemetry", flight_feed_views.set_signed_telemetry),
    path("public_keys/", flight_feed_views.SignedTelmetryPublicKeyList.as_view()),
    path(
        "public_keys/<uuid:pk>/",
        flight_feed_views.SignedTelmetryPublicKeyDetail.as_view(),
    ),
]
