"""
Root URL map.

Everything the apps use lives under /api/v1/. Versioning in the path means we can ship
/api/v1 and /api/v2 side by side when a breaking change is needed, so old app builds
in users' pockets keep working.

    /api/v1/auth/...        login (OTP), tokens, current user, devices      -> apps.accounts
    /api/v1/customer/...    customer profile, places, emergency contacts     -> apps.customers
    /api/v1/ride-types/     ride types per service (car | bike)              -> apps.pricing
    /api/v1/rides/...       estimates, booking, tracking, cancel, rate       -> apps.rides
    /api/v1/wallet/...      wallet, payment methods, promo validation        -> apps.payments
    /api/v1/provider/...    drivers (cars) + riders (bikes)                  -> providers, rides, payments
    /api/v1/support/...     tickets, lost items, SOS, notifications, share   -> apps.support
    /api/v1/staff/...       staff JSON API (optional; same actions as the dashboard) -> apps.staff

Super Admin web dashboard (HTML pages, Django templates, session login):
    /dashboard/...                                                           -> apps.dashboard

Docs:
    /api/schema/   raw OpenAPI 3 (YAML; add ?format=json for JSON). Feed this to code generators.
    /api/docs/     Swagger UI (try requests in the browser; click "Authorize" and paste a token)
    /api/redoc/    ReDoc (clean reference to share with the team)
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.core.views import HealthView

api_v1 = [
    path("health/", HealthView.as_view(), name="health"),
    path("auth/", include("apps.accounts.urls")),
    path("customer/", include("apps.customers.urls")),
    path("", include("apps.pricing.urls")),        # /ride-types/
    path("rides/", include("apps.rides.urls_customer")),
    path("wallet/", include("apps.payments.urls_customer")),
    path("provider/", include("apps.providers.urls")),
    path("provider/", include("apps.rides.urls_provider")),
    path("provider/", include("apps.payments.urls_provider")),
    path("support/", include("apps.support.urls")),
    path("staff/", include("apps.staff.urls")),
]

urlpatterns = [
    path("", RedirectView.as_view(url="/dashboard/", permanent=False)),
    path("dashboard/", include("apps.dashboard.urls")),     # Super Admin web dashboard (staff only)
    path("api/v1/", include(api_v1)),
    # OpenAPI 3 schema + docs UIs (developer tooling)
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # Engineering-only escape hatch. Ops staff use the Super Admin web app instead.
    path("django-admin/", admin.site.urls),
]

if settings.DEBUG:
    # Serve uploaded documents locally. In production, serve MEDIA from object storage/CDN.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
