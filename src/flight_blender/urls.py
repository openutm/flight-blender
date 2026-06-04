"""flight_blender URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
1 Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import include, path

from flight_blender.flight_feed import views as flight_feed_views

urlpatterns = [
    path("", flight_feed_views.HomeView.as_view()),
    path("realtime", flight_feed_views.ASGIHomeView.as_view()),
    path("admin/", admin.site.urls),
    path("ping", flight_feed_views.ping),
    path("signing_public_key", flight_feed_views.public_key_view),
    path("flight_stream/", include("flight_blender.flight_feed.urls")),
    path("rid/", include("flight_blender.rid.urls")),
    path("scd/", include("flight_blender.scd.urls")),
    path("uss/", include("flight_blender.uss.urls")),
    path("flight_declaration_ops/", include("flight_blender.flight_declarations.urls")),
    path("surveillance_monitoring_ops/", include("flight_blender.surveillance.urls")),
    path("conformance_monitoring_ops/", include("flight_blender.conformance.urls")),
    # UTM Adapter endpoints
    # path("utm_adapter/", include("flight_blender.utm_adapter.urls")),
]
