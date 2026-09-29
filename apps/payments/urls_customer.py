"""/api/v1/wallet/ routes (User apps)."""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.WalletView.as_view(), name="wallet"),
    path("transactions/", views.WalletTransactionsView.as_view(), name="wallet-transactions"),
    path("top-up/", views.TopUpView.as_view(), name="wallet-top-up"),
    path("payment-methods/", views.PaymentMethodListView.as_view(), name="payment-methods"),
    path("payment-methods/<uuid:pk>/", views.PaymentMethodDeleteView.as_view(), name="payment-method-delete"),
    path("promos/validate/", views.PromoValidateView.as_view(), name="promo-validate"),
]
