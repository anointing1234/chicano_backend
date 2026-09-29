"""
Staff API: Overview numbers and the live map.

    GET /api/v1/staff/overview/?service=all|car|bike
    GET /api/v1/staff/live-map/?service=all|car|bike

The numbers come from apps.staff.services (the web dashboard shows the same ones).
"""
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.schema import errors

from .. import services
from ..serializers import LiveProviderSerializer, OverviewSerializer
from ._common import SERVICE_PARAM, TAG, AnyStaff, service_filter


class OverviewView(APIView):
    permission_classes = [AnyStaff]

    @extend_schema(
        tags=[TAG], summary="Dashboard numbers",
        description=("**GET /api/v1/staff/overview/?service=all|car|bike** · Bearer (any staff).\n\n"
                     "Today's numbers and a 7-day series. Today = since midnight Africa/Lagos."),
        parameters=[SERVICE_PARAM], responses={200: OverviewSerializer, **errors(400, 401, 403)},
    )
    def get(self, request):
        return Response(OverviewSerializer(services.overview(service_filter(request))).data)


class LiveMapView(APIView):
    permission_classes = [AnyStaff]

    @extend_schema(
        tags=[TAG], summary="Online drivers & riders (live map)",
        description=("**GET /api/v1/staff/live-map/?service=all|car|bike** · Bearer (any staff). Every online "
                     "driver/rider with a GPS fix from the last 10 minutes; `on_trip` colours the pin."),
        parameters=[SERVICE_PARAM], responses={200: LiveProviderSerializer(many=True), **errors(400, 401, 403)},
    )
    def get(self, request):
        return Response(LiveProviderSerializer(services.live_providers(service_filter(request)), many=True).data)
