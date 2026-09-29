"""/api/v1/ride-types/"""
from django.urls import path

from . import views

urlpatterns = [path("ride-types/", views.RideTypeListView.as_view(), name="ride-types")]
