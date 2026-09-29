"""/api/v1/provider/ trip routes (Driver app · Cars and Rider app · Bikes)."""
from django.urls import path

from . import views_provider as v

urlpatterns = [
    path("offers/current/", v.CurrentOfferView.as_view(), name="offer-current"),
    path("offers/<uuid:pk>/accept/", v.OfferActionView.as_view(offer_action="accept"), name="offer-accept"),
    path("offers/<uuid:pk>/decline/", v.OfferActionView.as_view(offer_action="decline"), name="offer-decline"),
    path("trips/", v.TripHistoryView.as_view(), name="trip-history"),
    path("trips/current/", v.CurrentTripView.as_view(), name="trip-current"),
    path("trips/<uuid:pk>/arrive/", v.ArriveView.as_view(), name="trip-arrive"),
    path("trips/<uuid:pk>/helmet-check/", v.HelmetCheckView.as_view(), name="trip-helmet-check"),
    path("trips/<uuid:pk>/start/", v.StartView.as_view(), name="trip-start"),
    path("trips/<uuid:pk>/complete/", v.CompleteView.as_view(), name="trip-complete"),
    path("trips/<uuid:pk>/collect-cash/", v.CollectCashView.as_view(), name="trip-collect-cash"),
    path("trips/<uuid:pk>/helmet-returned/", v.HelmetReturnedView.as_view(), name="trip-helmet-returned"),
    path("trips/<uuid:pk>/cancel/", v.CancelTripView.as_view(), name="trip-cancel"),
    path("trips/<uuid:pk>/no-show/", v.NoShowView.as_view(), name="trip-no-show"),
    path("trips/<uuid:pk>/rate/", v.RateCustomerView.as_view(), name="trip-rate"),
    path("sos/", v.ProviderSOSView.as_view(), name="provider-sos"),
]
