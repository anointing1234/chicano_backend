"""/api/v1/provider/ earnings routes (Driver/Rider apps)."""
from django.urls import path

from . import views

urlpatterns = [
    path("earnings/summary/", views.EarningsSummaryView.as_view(), name="earnings-summary"),
    path("earnings/ledger/", views.LedgerView.as_view(), name="earnings-ledger"),
    path("payouts/", views.PayoutListView.as_view(), name="payouts"),
    path("payouts/instant/", views.InstantPayoutView.as_view(), name="payout-instant"),
    path("incentives/", views.IncentivesView.as_view(), name="incentives"),
]
