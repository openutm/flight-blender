from django.contrib import admin
from django.urls import path

# Phase 7: Django serves admin only. All domain endpoints are handled by FastAPI.
# Behind Mount("/admin", app=django_asgi_app) in asgi.py, the "/admin" prefix is
# stripped by Starlette before Django sees the path, so admin is registered at "".
urlpatterns = [
    path("", admin.site.urls),
]
