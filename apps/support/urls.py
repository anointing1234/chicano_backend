"""/api/v1/support/ routes."""
from django.urls import path

from . import views

urlpatterns = [
    path("notifications/", views.NotificationListView.as_view(), name="notifications"),
    path("notifications/read-all/", views.NotificationReadAllView.as_view(), name="notifications-read-all"),
    path("tickets/", views.TicketListCreateView.as_view(), name="tickets"),
    path("tickets/<uuid:pk>/", views.TicketDetailView.as_view(), name="ticket-detail"),
    path("tickets/<uuid:pk>/reply/", views.TicketReplyView.as_view(), name="ticket-reply"),
    path("lost-items/", views.LostItemView.as_view(), name="lost-items"),
    path("sos/", views.SOSView.as_view(), name="sos"),
    path("share/<str:token>/", views.PublicTripView.as_view(), name="public-trip"),
]
