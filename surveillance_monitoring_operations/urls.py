from django.urls import path

from . import views as surveillance_monitoring_views

urlpatterns = [
        path('health/', surveillance_monitoring_views.surveillance_health, name='health'),
        
]
