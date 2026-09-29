"""/api/v1/rides/ routes (User app · Cars and User app · Bikes)."""
from django.urls import path

from . import views_customer as v

urlpatterns = [
    path("estimate/", v.EstimateView.as_view(), name="ride-estimate"),
    path("", v.RideListCreateView.as_view(), name="rides"),
    path("active/", v.ActiveRideView.as_view(), name="ride-active"),
    path("<uuid:pk>/", v.RideDetailView.as_view(), name="ride-detail"),
    path("<uuid:pk>/cancel/", v.CancelRideView.as_view(), name="ride-cancel"),
    path("<uuid:pk>/rate/", v.RateRideView.as_view(), name="ride-rate"),
    path("<uuid:pk>/tip/", v.TipView.as_view(), name="ride-tip"),
    path("<uuid:pk>/retry-payment/", v.RetryPaymentView.as_view(), name="ride-retry-payment"),
    path("<uuid:pk>/sos/", v.RideSOSView.as_view(), name="ride-sos"),
]
