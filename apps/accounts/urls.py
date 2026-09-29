"""/api/v1/auth/ routes."""
from django.urls import path

from . import views

urlpatterns = [
    path("otp/request/", views.OtpRequestView.as_view(), name="otp-request"),
    path("otp/verify/", views.OtpVerifyView.as_view(), name="otp-verify"),
    path("token/refresh/", views.TokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("me/", views.MeView.as_view(), name="me"),
    path("devices/", views.DeviceListCreateView.as_view(), name="devices"),
    path("devices/<uuid:pk>/", views.DeviceDeleteView.as_view(), name="device-delete"),
]
