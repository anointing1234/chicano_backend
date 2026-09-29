"""
/dashboard/... URL map (namespace "dashboard"). Pages are GET; every action is a POST form with a
CSRF token that redirects back (POST → redirect → GET). Every view checks the staff role on the server.
"""
from django.urls import path

from .views import admin, auth, dashboards, growth, payments, payouts, providers, support, trips, users

app_name = "dashboard"

urlpatterns = [
    # sign in / out
    path("login/", auth.login_view, name="login"),
    path("logout/", auth.logout_view, name="logout"),

    # Platform
    path("", dashboards.overview, name="overview"),
    path("dispatch/", trips.dispatch, name="dispatch"),
    path("dispatch/<uuid:pk>/assign/", trips.dispatch_assign, name="dispatch_assign"),
    path("reports/", dashboards.reports, name="reports"),
    path("search/", admin.search, name="search"),
    path("live/data/", dashboards.live_data, name="live_data"),                  # JSON for the live map
    path("live/sos-banner/", dashboards.sos_banner, name="sos_banner"),          # fragment

    # Customers & Rides
    path("customer-panel/", dashboards.customer_dashboard, name="customer_dashboard"),
    path("customers/", users.customer_list, name="customers"),
    path("trips/", trips.trip_list, name="trips"),
    path("trips/<uuid:pk>/", trips.trip_detail, name="trip_detail"),
    path("trips/<uuid:pk>/refund/", trips.trip_refund, name="trip_refund"),
    path("trips/<uuid:pk>/cancel/", trips.trip_cancel, name="trip_cancel"),
    path("payments/", payments.payments, name="payments"),
    path("promotions/", growth.promotions, name="promotions"),
    path("promotions/new/", growth.promo_edit, name="promo_new"),
    path("promotions/<uuid:pk>/", growth.promo_edit, name="promo_edit"),
    path("support/", support.support_home, name="support"),
    path("support/tickets/bulk/", support.ticket_bulk, name="ticket_bulk"),                         # assign_me | resolve
    path("support/tickets/<uuid:pk>/", support.ticket_detail, name="ticket_detail"),
    path("support/tickets/<uuid:pk>/reply/", support.ticket_reply, name="ticket_reply"),
    path("support/tickets/<uuid:pk>/update/", support.ticket_update, name="ticket_update"),
    path("support/sos/<uuid:pk>/<str:action>/", support.sos_action, name="sos_action"),           # acknowledge|resolve
    path("lost-items/", support.lost_items, name="lost_items"),
    path("lost-items/<uuid:pk>/update/", support.lost_item_update, name="lost_item_update"),

    # Drivers & Fleet
    path("driver-panel/", dashboards.driver_dashboard, name="driver_dashboard"),
    path("providers/", providers.provider_list, name="providers"),
    path("providers/<uuid:pk>/", providers.provider_detail, name="provider_detail"),
    path("providers/<uuid:pk>/approve/", providers.provider_approve, name="provider_approve"),
    path("providers/<uuid:pk>/<str:action>/", providers.provider_status, name="provider_status"),   # reject|suspend|reinstate
    path("vehicles/<uuid:pk>/<str:action>/", providers.vehicle_action, name="vehicle_action"),     # approve|reject
    path("documents/", providers.document_list, name="documents"),
    path("documents/<uuid:pk>/review/", providers.document_review, name="document_review"),
    path("documents/<uuid:pk>/file/", providers.document_file, name="document_file"),             # private file (?side=back)
    path("documents/<uuid:pk>/<str:action>/", providers.document_action, name="document_action"), # approve|reject
    path("payouts/", payouts.payout_list, name="payouts"),
    path("payouts/run/", payouts.payout_run, name="payout_run"),
    path("payouts/<uuid:pk>/<str:action>/", payouts.payout_action, name="payout_action"),          # paid|failed
    path("incentives/", growth.incentives, name="incentives"),
    path("incentives/new/", growth.incentive_edit, name="incentive_new"),
    path("incentives/<uuid:pk>/", growth.incentive_edit, name="incentive_edit"),

    # Administration
    path("broadcasts/", admin.broadcasts, name="broadcasts"),
    path("users/", users.user_list, name="users"),
    path("users/<uuid:pk>/", users.user_detail, name="user_detail"),
    path("users/<uuid:pk>/<str:action>/", users.user_set_status, name="user_status"),              # suspend|ban|reinstate
    path("settings/", admin.settings_home, name="settings"),
    path("settings/fares/new/", admin.fare_edit, name="fare_new"),
    path("settings/fares/<uuid:pk>/", admin.fare_edit, name="fare_edit"),
    path("settings/ride-types/new/", admin.ride_type_edit, name="ride_type_new"),
    path("settings/ride-types/<uuid:pk>/", admin.ride_type_edit, name="ride_type_edit"),
    path("settings/zones/new/", admin.zone_edit, name="zone_new"),
    path("settings/zones/<uuid:pk>/", admin.zone_edit, name="zone_edit"),
    path("staff/", admin.staff, name="staff"),
    path("staff/new/", admin.staff_edit, name="staff_new"),
    path("staff/<uuid:pk>/", admin.staff_edit, name="staff_edit"),
    path("audit/", admin.audit, name="audit"),
]
