"""
/api/v1/staff/... : optional JSON version of the Super Admin actions.

The web dashboard (/dashboard/, apps.dashboard) does NOT need this API: it calls the same
functions in apps/staff/services.py directly. Keep this for scripts, integrations or a
future staff mobile app; delete the include in config/urls.py if you don't want it.

Every endpoint needs a staff access token from POST /api/v1/staff/auth/login/.
Roles are enforced per endpoint; see apps/staff/views/_common.py for the matrix.
"""
from django.urls import path
from rest_framework.routers import SimpleRouter

from apps.accounts.views import StaffLoginView

from .views import catalog, finance, overview, providers, rides, support, team, users

router = SimpleRouter()
router.register("promotions", catalog.PromotionViewSet, basename="staff-promotion")
router.register("incentives", catalog.IncentiveViewSet, basename="staff-incentive")
router.register("ride-types", catalog.RideTypeViewSet, basename="staff-ride-type")
router.register("fare-rules", catalog.FareRuleViewSet, basename="staff-fare-rule")
router.register("service-zones", catalog.ServiceZoneViewSet, basename="staff-service-zone")
router.register("team", team.StaffMemberViewSet, basename="staff-team")

urlpatterns = [
    # --- auth + session
    path("auth/login/", StaffLoginView.as_view(), name="staff-login"),
    path("me/", team.StaffMeView.as_view(), name="staff-me"),

    # --- dashboard
    path("overview/", overview.OverviewView.as_view(), name="staff-overview"),
    path("live-map/", overview.LiveMapView.as_view(), name="staff-live-map"),

    # --- users (combined table)
    path("users/", users.UserListView.as_view(), name="staff-users"),
    path("users/<uuid:pk>/", users.UserDetailView.as_view(), name="staff-user"),
    path("users/<uuid:pk>/suspend/", users.UserSuspendView.as_view(), name="staff-user-suspend"),
    path("users/<uuid:pk>/ban/", users.UserBanView.as_view(), name="staff-user-ban"),
    path("users/<uuid:pk>/reinstate/", users.UserReinstateView.as_view(), name="staff-user-reinstate"),

    # --- drivers & riders
    path("providers/", providers.ProviderListView.as_view(), name="staff-providers"),
    path("providers/<uuid:pk>/", providers.ProviderDetailView.as_view(), name="staff-provider"),
    path("providers/<uuid:pk>/approve/", providers.ProviderApproveView.as_view(), name="staff-provider-approve"),
    path("providers/<uuid:pk>/reject/", providers.ProviderRejectView.as_view(), name="staff-provider-reject"),
    path("providers/<uuid:pk>/suspend/", providers.ProviderSuspendView.as_view(), name="staff-provider-suspend"),
    path("providers/<uuid:pk>/reinstate/", providers.ProviderReinstateView.as_view(), name="staff-provider-reinstate"),
    path("vehicles/<uuid:pk>/approve/", providers.VehicleApproveView.as_view(), name="staff-vehicle-approve"),
    path("vehicles/<uuid:pk>/reject/", providers.VehicleRejectView.as_view(), name="staff-vehicle-reject"),
    path("documents/", providers.DocumentListView.as_view(), name="staff-documents"),
    path("documents/<uuid:pk>/approve/", providers.DocumentApproveView.as_view(), name="staff-document-approve"),
    path("documents/<uuid:pk>/reject/", providers.DocumentRejectView.as_view(), name="staff-document-reject"),

    # --- trips + dispatch
    path("rides/", rides.RideListView.as_view(), name="staff-rides"),
    path("rides/<uuid:pk>/", rides.RideDetailView.as_view(), name="staff-ride"),
    path("rides/<uuid:pk>/refund/", rides.RideRefundView.as_view(), name="staff-ride-refund"),
    path("rides/<uuid:pk>/cancel/", rides.RideCancelView.as_view(), name="staff-ride-cancel"),
    path("dispatch/", rides.DispatchQueueView.as_view(), name="staff-dispatch"),
    path("dispatch/<uuid:pk>/candidates/", rides.DispatchCandidatesView.as_view(), name="staff-dispatch-candidates"),
    path("dispatch/<uuid:pk>/assign/", rides.DispatchAssignView.as_view(), name="staff-dispatch-assign"),

    # --- payouts
    path("payouts/", finance.PayoutListView.as_view(), name="staff-payouts"),
    path("payouts/run/", finance.PayoutRunView.as_view(), name="staff-payouts-run"),
    path("payouts/<uuid:pk>/mark-paid/", finance.PayoutMarkPaidView.as_view(), name="staff-payout-paid"),
    path("payouts/<uuid:pk>/mark-failed/", finance.PayoutMarkFailedView.as_view(), name="staff-payout-failed"),

    # --- support + safety
    path("tickets/", support.TicketListView.as_view(), name="staff-tickets"),
    path("tickets/<uuid:pk>/", support.TicketDetailView.as_view(), name="staff-ticket"),
    path("tickets/<uuid:pk>/reply/", support.TicketReplyView.as_view(), name="staff-ticket-reply"),
    path("sos/", support.SOSListView.as_view(), name="staff-sos"),
    path("sos/<uuid:pk>/acknowledge/", support.SOSAcknowledgeView.as_view(), name="staff-sos-ack"),
    path("sos/<uuid:pk>/resolve/", support.SOSResolveView.as_view(), name="staff-sos-resolve"),

    # --- audit
    path("audit-log/", team.AuditLogListView.as_view(), name="staff-audit-log"),
] + router.urls   # promotions, incentives, ride-types, fare-rules, service-zones, team
