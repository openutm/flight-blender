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
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.urls import path

from . import views as geo_fence_views

urlpatterns = [
    path("set_geo_fence", geo_fence_views.set_geo_fence, name="set_geo_fence"),
    path("set_geozone", geo_fence_views.set_geozone),
    path("geo_fence", geo_fence_views.GeoFenceList.as_view()),
    path("geo_fence/<uuid:pk>", geo_fence_views.GeoFenceDetail.as_view()),
    path(
        "geo_fence/<uuid:pk>/delete",
        geo_fence_views.GeoFenceDelete.as_view(),
        name="geo-fence-delete",
    ),
    # End points for automated testing interface
    path("geo_awareness/status", geo_fence_views.GeoZoneTestHarnessStatus.as_view()),
    # End points for automated testing interface
    path(
        "geo_awareness/geospatial_data_sources",
        geo_fence_views.GeospatialMapList.as_view(),
    ),
    path(
        "geo_awareness/geospatial_data_sources/<uuid:geozone_source_id>",
        geo_fence_views.GeoZoneSourcesOperations.as_view(),
    ),
    path("geo_awareness/map/queries", geo_fence_views.GeoZoneCheck.as_view()),
]
