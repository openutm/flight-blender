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

from . import views as flight_declaration_views

urlpatterns = [
    path("set_flight_declaration", flight_declaration_views.set_flight_declaration),
    path("flight_declaration", flight_declaration_views.FlightDeclarationCreateList.as_view()),
    path(
        "flight_declaration/<uuid:pk>",
        flight_declaration_views.FlightDeclarationDetail.as_view(),
    ),
    path(
        "flight_declaration/<uuid:flight_declaration_id>/network_flight_declarations",
        flight_declaration_views.network_flight_declaration_details,
    ),
    path(
        "network_flight_declarations_by_view",
        flight_declaration_views.network_flight_declaration_details_by_view,
    ),
    path(
        "flight_declaration_review/<uuid:pk>",
        flight_declaration_views.FlightDeclarationApproval.as_view(),
        name="flight-declaration-review",
    ),
    path(
        "flight_declaration_state/<uuid:pk>",
        flight_declaration_views.FlightDeclarationStateUpdate.as_view(),
    ),
    path(
        "flight_declaration/<uuid:declaration_id>/delete",
        flight_declaration_views.FlightDeclarationDelete.as_view(),
        name="flight-declaration-delete",
    ),
]
