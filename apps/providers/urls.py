"""/api/v1/provider/ onboarding + availability routes (trip routes live in apps.rides.urls_provider)."""
from django.urls import path

from . import views

urlpatterns = [
    path("apply/", views.ApplyView.as_view(), name="provider-apply"),
    path("onboarding/", views.OnboardingView.as_view(), name="provider-onboarding"),
    path("me/", views.ProviderMeView.as_view(), name="provider-me"),
    path("vehicles/", views.VehicleView.as_view(), name="provider-vehicles"),
    path("documents/", views.DocumentView.as_view(), name="provider-documents"),
    path("status/", views.StatusView.as_view(), name="provider-status"),
    path("location/", views.LocationView.as_view(), name="provider-location"),
]
