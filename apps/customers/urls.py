"""/api/v1/customer/ routes."""
from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("places", views.SavedPlaceViewSet, basename="places")
router.register("emergency-contacts", views.EmergencyContactViewSet, basename="emergency-contacts")

urlpatterns = [path("profile/", views.CustomerProfileView.as_view(), name="customer-profile"), *router.urls]
